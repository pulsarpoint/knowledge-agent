"""The session explorer: one agent session's run records as an in-memory object with query methods, for a person (the
run page's Debug tab, POST /api/agent-runs/{run_key}/explore) and for a model (tools(): the same methods as SDK
function tools bound to one record, so a model cannot reach another session).

A session's turns are its run records: turn n is run key `cc:<session_id>:<n>` (session_worker.run_key): the person's
input, everything the agent did, its final answer and the end status. A run outside a session (`run:<id>`) is a
session of one turn. Inside a turn, a step is one model response with what led to it: its inputs (the task, a comment
sent while the agent worked, a retry, an expert's or subagent's task), reasoning and messages, its tool calls paired
with their results by call_id, the errors and the end it reached, and its usage. A new step starts when an input or a
model event follows a step that already has its usage or its end. Events with a parent_call_id are a nested run (an
expert call, or a subagent run_subagent started), built as a child run under the tool call with that call_id, as the
run page reads an expert call (runs_api: ?call=).

build() is a pure function from the rows; SessionRecord.load reads them once. Every method is deterministic and returns
JSON values, capped at MAX_CHARS of serialised JSON (truncated and total_chars tell what was cut); every string has
gone through URL masking (http_errors.safe_url: credentials and queries hidden) and redaction (activity.redact with the
session's secrets). METHODS is the one table of method names, argument models and functions that the tools and the API
both use."""

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from agents import FunctionTool
from agents.strict_schema import ensure_strict_json_schema
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.http_errors import safe_url
from knowledge_admin.sessions.activity import redact, tool_failed

MAX_CHARS = 24_000  # the most characters (serialised JSON) one method returns, digest and tool_output aside
OUTPUT_PAGE = 8_000  # tool_output's default page
MAX_OUTPUT_PAGE = 20_000
DEFAULT_DIGEST_CHARS = 16_000
MAX_DIGEST_CHARS = 100_000
PREVIEW = 200  # an input preview in overview
SNIPPET = 80  # characters of context on each side of a search hit
ERROR_TEXT = 300  # a failed call's error text in turn()
NOT_FOUND = 'Run record not found.'

SESSION_KEY = re.compile(r'cc:([^:]+):(\d+)')
MODEL_KINDS = ('reasoning', 'message', 'tool_call', 'usage')
TOKEN_KEYS = ('input_tokens', 'output_tokens', 'cached_tokens', 'reasoning_tokens', 'total_tokens')
URL = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://[^\s"\'<>`]+')
EventKind = Literal[
    'start', 'instructions', 'input', 'reasoning', 'message', 'tool_call', 'tool_result', 'usage', 'error', 'end'
]
SearchKind = Literal['input', 'reasoning', 'message', 'tool_call', 'tool_result', 'error']


class RunNotFound(LookupError):
    """No record for this run key or session in this workspace."""


@dataclass
class Call:
    call_id: str
    name: str
    arguments: Any
    turn: int
    step: int
    seq: int
    status: str = 'pending'  # ok | error | pending (no result recorded)
    output: str = ''
    child: 'Run | None' = None  # the expert call or subagent run it started

    @property
    def size(self) -> int:
        return len(self.output)


@dataclass
class Step:
    n: int
    inputs: list[dict[str, str]] = field(default_factory=list)  # {kind: task|comment|retry|expert|subagent, text}
    reasoning: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)  # {type, message}
    usage: dict[str, int] = field(default_factory=dict)
    end: str | None = None
    responded: bool = False  # its usage was recorded: the model answered

    @property
    def failed(self) -> bool:
        return bool(self.errors) or any(call.status == 'error' for call in self.calls)


@dataclass
class Run:
    """One agent's events: a session turn (one run key), or a nested run (an expert call, a subagent) in it."""

    turn: int  # the session turn it belongs to
    kind: str = 'turn'  # turn | expert | subagent
    run_key: str = ''
    parent_call_id: str | None = None
    run_kind: str | None = None  # the start event's kind (chat_session, event_session, ...)
    title: str | None = None
    agent: str | None = None
    agent_id: str | None = None
    status: str = 'unfinished'
    started_at: str | None = None
    ended_at: str | None = None
    instructions: str = ''
    answer: str = ''
    steps: list[Step] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)  # timeline entries, tool outputs left out
    calls: dict[str, Call] = field(default_factory=dict)
    unlinked: dict[str, 'Run'] = field(default_factory=dict)  # nested runs whose tool call is not recorded

    @property
    def failed(self) -> bool:
        return self.status == 'failed' or any(step.failed for step in self.steps)

    @property
    def input(self) -> str:
        return next((item['text'] for step in self.steps for item in step.inputs), '')

    def own_calls(self) -> list[Call]:
        return [call for step in self.steps for call in step.calls]

    def nested(self) -> list['Run']:
        return [call.child for call in self.own_calls() if call.child is not None] + list(self.unlinked.values())

    def all_calls(self) -> list[Call]:
        """Its tool calls and those of its nested runs, depth first."""
        found = []
        for call in self.own_calls():
            found.append(call)
            if call.child is not None:
                found += call.child.all_calls()
        for child in self.unlinked.values():
            found += child.all_calls()
        return found

    def tokens(self) -> int:
        return sum(step.usage.get('total_tokens', 0) for step in self.steps)

    def nested_tokens(self) -> int:
        return sum(child.tokens() + child.nested_tokens() for child in self.nested())


# --- building -------------------------------------------------------------------------------------------------------


def _clean(value: Any, secrets: tuple[str, ...]) -> Any:
    """Every string with its URLs masked (safe_url), then redacted with the session's secrets."""
    if isinstance(value, str):
        return redact(URL.sub(lambda match: safe_url(match.group()), value), secrets)
    if isinstance(value, dict):
        return {key: _clean(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item, secrets) for item in value]
    return value


def _time(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _input_kind(payload: dict) -> str:
    for kind in ('comment', 'retry', 'expert', 'subagent'):
        if payload.get(kind):
            return kind
    return 'task'


def _text(value: Any) -> str:
    """Arguments as one line of text: a string as it is, anything else as compact JSON."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _build_run(rows: list[dict], groups: dict[str | None, list[dict]], turn: int, parent: str | None = None) -> Run:
    """One run (a turn's own events, or a nested run under call `parent`) out of its rows in seq order; nested runs
    recursively."""
    run = Run(turn=turn, parent_call_id=parent)
    steps: list[Step] = []
    answer = ''

    def current() -> Step:
        if not steps:
            steps.append(Step(1))
        return steps[-1]

    for row in rows:
        kind, payload = row['kind'], row['payload']
        step = current()
        if kind in ('input', *MODEL_KINDS) and (step.responded or step.end is not None):
            step = Step(len(steps) + 1)
            steps.append(step)
        entry: dict[str, Any] = {'seq': row['seq'], 'turn': turn, 'step': step.n, 'kind': kind}
        if kind == 'start':
            run.title, run.agent, run.run_kind = payload.get('title'), payload.get('agent'), payload.get('kind')
            entry.update(title=payload.get('title'), run_kind=payload.get('kind'))
        elif kind == 'instructions':
            if not run.instructions:
                run.instructions = payload.get('text', '')
            entry.update(agent=payload.get('agent'), text=payload.get('text', ''))
        elif kind == 'input':
            item = {'kind': _input_kind(payload), 'text': payload.get('task', '')}
            if item['kind'] in ('expert', 'subagent'):
                run.kind = item['kind']
                run.agent = (payload.get('expert') or {}).get('name') or run.agent
            step.inputs.append(item)
            entry.update(input=item['kind'], text=item['text'])
        elif kind == 'reasoning':
            text = payload.get('text', '') if payload.get('available', True) else '(not returned by the provider)'
            step.reasoning.append(text)
            entry['text'] = text
        elif kind == 'message':
            answer = payload.get('text', '')
            step.messages.append(answer)
            entry['text'] = answer
        elif kind == 'tool_call':
            call_id = payload.get('call_id') or f'#{turn}.{row["seq"]}'  # an old event without one
            found = Call(call_id, payload.get('name', ''), payload.get('arguments'), turn, step.n, row['seq'])
            if call_id in groups:
                found.child = _build_run(groups[call_id], groups, turn, call_id)
            step.calls.append(found)
            run.calls[call_id] = found
            answer = ''  # a message before a tool call was not the answer
            entry.update(name=found.name, call_id=call_id, arguments=found.arguments)
            if found.child is not None:
                entry['nested_run'] = found.child.kind
        elif kind == 'tool_result':
            call_id = payload.get('call_id') or ''
            output = payload['output'] if 'output' in payload else payload.get('preview', '')
            output = output if isinstance(output, str) else _text(output)
            error = payload['is_error'] if isinstance(payload.get('is_error'), bool) else tool_failed(output)
            found = run.calls.get(call_id)
            if found is None:  # a result without its call in this record
                found = Call(
                    call_id or f'#{turn}.{row["seq"]}', payload.get('name', ''), None, turn, step.n, row['seq']
                )
                step.calls.append(found)
                run.calls[found.call_id] = found
            found.output, found.status = output, 'error' if error else 'ok'
            entry.update(name=found.name, call_id=found.call_id, status=found.status, size=found.size)
        elif kind == 'usage':
            for key in TOKEN_KEYS:
                if isinstance(payload.get(key), int):
                    step.usage[key] = step.usage.get(key, 0) + payload[key]
            step.responded = True
            entry['tokens'] = {key: payload[key] for key in TOKEN_KEYS if isinstance(payload.get(key), int)}
        elif kind == 'error':
            error = {'type': payload.get('type', ''), 'message': payload.get('message', '')}
            step.errors.append(error)
            entry.update(error)
        elif kind == 'end':
            step.end = payload.get('status')
            run.ended_at = _time(row['created_at'])
            entry['status'] = step.end
        run.events.append(entry)
    run.steps, run.answer = steps, answer
    if rows:
        run.agent_id, run.started_at = rows[0]['agent_id'], _time(rows[0]['created_at'])
    if steps and steps[-1].end is not None:
        run.status = steps[-1].end
    else:  # no end yet, or a retry continued after one
        run.ended_at = None
    for call in run.calls.values():
        if call.child is not None:  # a nested run has no end of its own: its call's result tells
            call.child.status = {'ok': 'completed', 'error': 'failed'}.get(call.status, 'unfinished')
    return run


def _turn_number(run_key: str) -> int | None:
    match = SESSION_KEY.fullmatch(run_key)
    return int(match.group(2)) if match else None


def _build_turn(run_key: str, n: int, rows: list[dict]) -> Run:
    groups: dict[str | None, list[dict]] = {}
    for row in rows:
        groups.setdefault(row['payload'].get('parent_call_id'), []).append(row)
    run = _build_run(groups.get(None, []), groups, n)
    linked = {call.call_id for call in run.all_calls() if call.child is not None}
    for parent, child_rows in groups.items():
        if parent is not None and parent not in linked:
            run.unlinked[parent] = _build_run(child_rows, groups, n, parent)
    run.run_key = run_key
    for child in [call.child for call in run.all_calls() if call.child is not None] + list(run.unlinked.values()):
        child.run_key = run_key
    return run


def build(
    rows: Iterable[dict], *, run_key: str, meta: dict | None = None, secrets: Iterable[str] = ()
) -> 'SessionRecord':
    """The session record out of its event rows ({run_key, seq, kind, agent_id, payload, created_at[, user]}, a row
    without run_key belongs to `run_key`); run_key: the run being looked at (its turn is the focus turn); meta: what the
    events do not hold ({session_id, model}). RunNotFound without rows."""
    secret_values = tuple(value for value in secrets if value)
    rows = [{**row, 'payload': _clean(row['payload'] or {}, secret_values)} for row in rows]
    if not rows:
        raise RunNotFound(NOT_FOUND)
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        by_key.setdefault(row.get('run_key') or run_key, []).append(row)
    keys = sorted(by_key, key=lambda key: (_turn_number(key) is None, _turn_number(key) or 0, key))
    turns = []
    for position, key in enumerate(keys, start=1):
        n = _turn_number(key) or position
        turns.append(_build_turn(key, n, sorted(by_key[key], key=lambda row: row['seq'])))
    focus = next((turn.turn for turn in turns if turn.run_key == run_key), turns[-1].turn)
    meta = meta or {}
    first = turns[0]
    return SessionRecord(
        session_id=meta.get('session_id'),
        run_key=run_key,
        focus_turn=focus,
        model=meta.get('model'),
        user=next((row.get('user') for row in rows if row.get('user')), None),
        turns=turns,
        full_chars=sum(len(json.dumps(row['payload'], ensure_ascii=False, default=str)) for row in rows),
        agent=first.agent,
        agent_id=first.agent_id,
        title=first.title,
    )


# --- caps -----------------------------------------------------------------------------------------------------------


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def _shorten(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f'… [{len(value) - limit} more chars]'
    if isinstance(value, dict):
        return {key: _shorten(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_shorten(item, limit) for item in value]
    return value


def _fit(result: dict, items: str | None = None) -> dict:
    """The result within MAX_CHARS: total_chars is its full size; when it is over, items are dropped from the end of
    result[items] (total_items: how many there were), then long strings are shortened, and truncated is true."""
    result = {**result, 'truncated': False, 'total_chars': 0}
    full = _size(result)
    result['total_chars'] = full
    if full <= MAX_CHARS:
        return result
    result['truncated'] = True
    if items is not None:
        result['total_items'] = len(result[items])
        while len(result[items]) > 1 and _size(result) > MAX_CHARS:
            result[items] = result[items][: max(1, len(result[items]) * 3 // 4)]
        while len(result[items]) > 1 and _size(result) > MAX_CHARS:
            result[items] = result[items][:-1]
    limit = 4_000
    while _size(result) > MAX_CHARS and limit > 20:
        result = {**_shorten(result, limit), 'truncated': True, 'total_chars': full}
        limit //= 2
    return result


def _nested_run(call: Call) -> dict | None:
    return None if call.child is None else {'kind': call.child.kind, 'agent': call.child.agent}


def _step_line(step: Step) -> dict:
    return {
        'step': step.n,
        'input': _shorten(step.inputs[0]['text'], PREVIEW) if step.inputs else None,
        'tools': [call.name for call in step.calls],
        'errors': len(step.errors) + sum(call.status == 'error' for call in step.calls),
        'end': step.end,
        'tokens': step.usage.get('total_tokens', 0),
    }


def _step_in_full(step: Step) -> dict:
    calls = []
    for call in step.calls:
        output: dict[str, Any] = {'size': call.size}
        if call.status == 'error':
            output['error'] = _shorten(call.output, ERROR_TEXT)
        calls.append(
            {
                'call_id': call.call_id,
                'name': call.name,
                'arguments': call.arguments,
                'status': call.status,
                'output': output,
                'nested_run': _nested_run(call),
            }
        )
    return {
        'step': step.n,
        'inputs': step.inputs,
        'reasoning': step.reasoning,
        'messages': step.messages,
        'tool_calls': calls,
        'errors': step.errors,
        'usage': step.usage,
        'end': step.end,
    }


def _totals(runs: list[Run]) -> dict:
    calls = [call for run in runs for call in run.own_calls()]
    return {
        'steps': sum(len(run.steps) for run in runs),
        'tool_calls': dict(Counter(call.name for call in calls)),
        'tool_errors': sum(call.status == 'error' for call in calls),
        'errors': sum(len(step.errors) for run in runs for step in run.steps),
        'tokens': sum(run.tokens() for run in runs),
        'expert_calls': sum(len(run.nested()) for run in runs),
        'expert_tokens': sum(run.nested_tokens() for run in runs),
    }


def _run_overview(run: Run) -> dict:
    """A nested run at a glance: what it is, one line per step and its totals."""
    return {
        'run': {
            'kind': run.kind,
            'agent': run.agent,
            'agent_id': run.agent_id,
            'turn': run.turn,
            'call_id': run.parent_call_id,
            'status': run.status,
            'started_at': run.started_at,
        },
        'steps': [_step_line(step) for step in run.steps],
        'totals': _totals([run]),
    }


# --- the record -----------------------------------------------------------------------------------------------------


@dataclass
class SessionRecord:
    run_key: str  # the run being looked at
    focus_turn: int  # its turn
    turns: list[Run]
    session_id: str | None = None
    model: str | None = None
    user: str | None = None
    agent: str | None = None
    agent_id: str | None = None
    title: str | None = None
    full_chars: int = 0

    @classmethod
    def load(cls, conn, project, key: str, secrets: Iterable[str] = ()) -> 'SessionRecord':
        """The session of a run key (`cc:<session_id>:<n>`) or a session id in this workspace, every turn read once
        (a run outside a session: that run alone); RunNotFound when there is no record."""
        match = SESSION_KEY.fullmatch(key)
        session_id = match.group(1) if match else (key if ':' not in key else None)
        prefix = f'cc:{session_id}:' if session_id is not None else None
        rows = conn.execute(
            """SELECT e.run_key,e.seq,e.kind,e.agent_id,e.payload,e.created_at,p.email AS "user"
            FROM ka.agent_run_events e LEFT JOIN ka.principals p ON p.id=e.actor_id
            WHERE e.project_id=%s AND (e.run_key=%s OR (%s::text IS NOT NULL AND starts_with(e.run_key, %s)))
            ORDER BY e.run_key,e.seq""",
            (project, key, prefix, prefix),
        ).fetchall()
        rows = [row for row in rows if row['run_key'] == key or SESSION_KEY.fullmatch(row['run_key'])]
        if not rows or (session_id != key and not any(row['run_key'] == key for row in rows)):
            raise RunNotFound(NOT_FOUND)  # a run key reads its session only when this workspace has its record
        model = None
        if session_id is not None:
            found = conn.execute(
                """SELECT m.name FROM ka.agent_sessions s JOIN ka.llm_models m ON m.id=s.model_id
                WHERE s.project_id=%s AND s.id::text=%s""",
                (project, session_id),
            ).fetchone()
            model = found['name'] if found else None
        run_key = key if match or session_id is None else max((row['run_key'] for row in rows), key=_turn_number)
        return build(rows, run_key=run_key, meta={'session_id': session_id, 'model': model}, secrets=secrets)

    # helpers

    @property
    def status(self) -> str:
        return self.turns[-1].status

    def all_calls(self) -> list[Call]:
        return [call for turn in self.turns for call in turn.all_calls()]

    def _call(self, call_id: str) -> Call:
        for call in self.all_calls():
            if call.call_id == call_id:
                return call
        raise ValueError(f'call_id: no tool call {call_id} in this session.')

    def _turn(self, n: int) -> Run:
        for turn in self.turns:
            if turn.turn == n:
                return turn
        numbers = ', '.join(str(turn.turn) for turn in self.turns)
        raise ValueError(f'turn: there is no turn {n}; the session has turns {numbers}.')

    def _turns(self, n: int | None) -> list[Run]:
        return self.turns if n is None else [self._turn(n)]

    def _unlinked(self, call_id: str) -> Run | None:
        return next((turn.unlinked[call_id] for turn in self.turns if call_id in turn.unlinked), None)

    # methods

    def overview(self) -> dict:
        """The session's metadata, one line per turn (with its step count) and the totals."""
        result = {
            'session': {
                'session_id': self.session_id,
                'run_key': self.run_key,
                'focus_turn': self.focus_turn,
                'title': self.title,
                'agent': self.agent,
                'agent_id': self.agent_id,
                'model': self.model,
                'user': self.user,
                'status': self.status,
                'started_at': self.turns[0].started_at,
                'ended_at': self.turns[-1].ended_at,
                'instructions_chars': len(self.turns[0].instructions),
            },
            'turns': [
                {
                    'turn': turn.turn,
                    'run_key': turn.run_key,
                    'input': _shorten(turn.input, PREVIEW),
                    'steps': len(turn.steps),
                    'tools': [call.name for call in turn.own_calls()],
                    'errors': sum(len(s.errors) for s in turn.steps)
                    + sum(c.status == 'error' for c in turn.own_calls()),
                    'status': turn.status,
                    'tokens': turn.tokens(),
                }
                for turn in self.turns
            ],
            'totals': {'turns': len(self.turns), **_totals(self.turns)},
        }
        return _fit(result, 'turns')

    def timeline(self, kinds: list[str] | None = None, turn: int | None = None, tool: str | None = None) -> dict:
        """The events in order, each with its turn and step, filtered by kind, turn and tool name; tool outputs as
        their size only."""
        events = [
            event
            for run in self._turns(turn)
            for event in run.events
            if (kinds is None or event['kind'] in kinds) and (tool is None or event.get('name') == tool)
        ]
        return _fit({'events': events}, 'events')

    def tool_calls(self, turn: int | None = None, name: str | None = None, errors_only: bool = False) -> dict:
        found = [
            {
                'call_id': call.call_id,
                'turn': call.turn,
                'step': call.step,
                'name': call.name,
                'arguments': call.arguments,
                'status': call.status,
                'output_size': call.size,
                'expert_call': call.child is not None and call.child.kind == 'expert',
                'nested_run': _nested_run(call),
            }
            for run in self._turns(turn)
            for call in run.own_calls()
            if (name is None or call.name == name) and (not errors_only or call.status == 'error')
        ]
        return _fit({'tool_calls': found}, 'tool_calls')

    def tool_output(self, call_id: str, offset: int = 0, max_chars: int = OUTPUT_PAGE) -> dict:
        call = self._call(call_id)
        text = call.output[offset : offset + max_chars]
        end = offset + len(text)
        return {
            'call_id': call.call_id,
            'turn': call.turn,
            'name': call.name,
            'status': call.status,
            'total_chars': call.size,
            'offset': offset,
            'text': text,
            'next_offset': end if end < call.size else None,
        }

    def turn(self, n: int) -> dict:
        """Session turn n in full: its steps with outputs as their size (and a short error text)."""
        run = self._turn(n)
        result = {
            'turn': run.turn,
            'run_key': run.run_key,
            'status': run.status,
            'started_at': run.started_at,
            'ended_at': run.ended_at,
            'tokens': run.tokens(),
            'answer': run.answer,
            'steps': [_step_in_full(step) for step in run.steps],
        }
        return _fit(result, 'steps')

    def search(self, text: str, kinds: list[str] | None = None, turn: int | None = None, max_hits: int = 20) -> dict:
        """Case-insensitive substring search; one hit per text, with how often it matched and a snippet around the
        first match."""
        needle = text.lower()
        hits = []
        for kind, run, step, call_id, value in (item for r in self._turns(turn) for item in _texts(r)):
            if kinds is not None and kind not in kinds:
                continue
            lowered = value.lower()
            at = lowered.find(needle)
            if at < 0:
                continue
            start, end = max(0, at - SNIPPET), min(len(value), at + len(text) + SNIPPET)
            hit = {
                'kind': kind,
                'turn': run.turn,
                'step': step,
                'count': lowered.count(needle),
                'snippet': ('…' if start else '') + value[start:end] + ('…' if end < len(value) else ''),
            }
            if call_id is not None:
                hit['call_id'] = call_id
            if run.parent_call_id is not None:
                hit['in_call'] = run.parent_call_id
            hits.append(hit)
        return _fit({'total_hits': len(hits), 'hits': hits[:max_hits]}, 'hits')

    def expert_call(self, call_id: str) -> dict:
        unlinked = self._unlinked(call_id)
        if unlinked is not None:
            return _fit(
                {'call_id': call_id, 'overview': _run_overview(unlinked), 'timeline': unlinked.events}, 'timeline'
            )
        call = self._call(call_id)
        if call.child is None:
            raise ValueError(f'call_id: {call_id} is not an expert call or subagent run.')
        return _fit(
            {
                'call_id': call.call_id,
                'turn': call.turn,
                'name': call.name,
                'arguments': call.arguments,
                'status': call.status,
                'overview': _run_overview(call.child),
                'timeline': call.child.events,
            },
            'timeline',
        )

    def digest(self, budget_chars: int = DEFAULT_DIGEST_CHARS, turn: int | None = None) -> dict:
        return digest(self, budget_chars, turn)


def _texts(run: Run):
    """(kind, run, step, call_id, text) of every searchable text of a run, its nested runs after its own."""
    for step in run.steps:
        for value in step.inputs:
            yield 'input', run, step.n, None, value['text']
        for text in step.reasoning:
            yield 'reasoning', run, step.n, None, text
        for text in step.messages:
            yield 'message', run, step.n, None, text
        for call in step.calls:
            yield 'tool_call', run, step.n, call.call_id, _text(call.arguments)
            yield 'tool_result', run, step.n, call.call_id, call.output
        for error in step.errors:
            yield 'error', run, step.n, None, f'{error["type"]}: {error["message"]}'
    for child in run.nested():
        yield from _texts(child)


# --- digest ---------------------------------------------------------------------------------------------------------

INSTRUCTIONS_SHARE = 4  # the instructions take at most a quarter of the budget
INPUT_CHARS, MESSAGE_CHARS, REASONING_CHARS, ARGUMENT_CHARS = 1_500, 1_500, 800, 300


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f' … [{len(text) - limit} more chars]'


def _call_line(call: Call) -> str:
    line = f'- {call.name}({_cut(_text(call.arguments), ARGUMENT_CHARS)}) -> {call.status}, {call.size} chars'
    if call.child is not None:
        child = call.child
        calls = len(child.own_calls())
        line += (
            f' [{child.kind} {child.agent}: {len(child.steps)} steps, {calls} tool calls; expert_call({call.call_id})]'
        )
    return line


def _full_step(step: Step) -> str:
    lines = [f'### Step {step.n}']
    lines += [f'{item["kind"]}: {_cut(item["text"], INPUT_CHARS)}' for item in step.inputs]
    lines += [f'reasoning: {_cut(text, REASONING_CHARS)}' for text in step.reasoning]
    lines += [f'message: {_cut(text, MESSAGE_CHARS)}' for text in step.messages]
    lines += [_call_line(call) for call in step.calls]
    lines += [f'error: {error["type"]}: {_cut(error["message"], MESSAGE_CHARS)}' for error in step.errors]
    lines.append(f'tokens: {step.usage.get("total_tokens", 0)}' + (f' · end: {step.end}' if step.end else ''))
    return '\n'.join(lines)


def _short_step(step: Step) -> str:
    calls = ', '.join(f'{call.name} -> {call.status}' for call in step.calls) or 'no tool calls'
    return f'Step {step.n}: {calls} · {len(step.messages)} messages · {step.usage.get("total_tokens", 0)} tokens'


def _full_turn(run: Run, kept_steps: set[int] | None) -> str:
    """A turn in full; kept_steps: the steps in full (None: all), the others one line each."""
    head = f'## Turn {run.turn} ({run.run_key}) · {len(run.steps)} steps · status {run.status} · {run.tokens()} tokens'
    shortened = 0 if kept_steps is None else len(run.steps) - len(kept_steps)
    parts = [head]
    if shortened:
        parts.append(f'[{shortened} steps shortened; use turn({run.turn}) to read them in full]')
    parts += [
        _full_step(step) if kept_steps is None or step.n in kept_steps else _short_step(step) for step in run.steps
    ]
    parts.append(f'Answer: {_cut(run.answer, MESSAGE_CHARS)}' if run.answer else 'Answer: (none)')
    return '\n'.join(parts)


def _short_turn(run: Run) -> str:
    calls = len(run.own_calls())
    return (
        f'Turn {run.turn}: {_cut(run.input, 120)!r} · {len(run.steps)} steps · {calls} tool calls · '
        f'status {run.status} · {run.tokens()} tokens'
    )


def _essential_steps(run: Run) -> set[int]:
    """The steps a shortened turn keeps in full: the first, the last and those with errors."""
    numbers = [step.n for step in run.steps]
    return {numbers[0], numbers[-1], *(step.n for step in run.steps if step.failed)} if numbers else set()


def digest(record: SessionRecord, budget_chars: int = DEFAULT_DIGEST_CHARS, focus: int | None = None) -> dict:
    """The session compressed to text (a model reads it as is; JSON would spend the budget on keys and quotes): the
    header, the instructions (at most a quarter of the budget), every turn (its steps: inputs, reasoning, messages,
    tool calls as name(arguments) -> ok/error, output size; outputs never; then its answer and status) and the end.

    Over budget: the first and last turns, the turns with errors and the focus turn (the run being looked at) stay in
    full, as many other last turns as fit too; the others become one line each, with a note. Still over: only the first,
    last and focus turns stay in full, then their middle steps are shortened the same way (first, last and failed steps
    in full); as a last resort the text is cut at the budget."""
    focus = record.focus_turn if focus is None else record._turn(focus).turn
    turns = {run.turn: run for run in record.turns}
    numbers = list(turns)
    calls = len([call for run in record.turns for call in run.own_calls()])
    header = (
        f'Session {record.session_id or record.run_key} · agent {record.agent or record.agent_id} · '
        f'model {record.model or "unknown"} · status {record.status} · {len(numbers)} turns · '
        f'{sum(len(run.steps) for run in record.turns)} steps · {calls} tool calls · focus turn {focus}'
    )
    instructions = record.turns[0].instructions
    shown_instructions = _cut(instructions, max(500, budget_chars // INSTRUCTIONS_SHARE))

    def render(kept: set[int], steps: dict[int, set[int]] | None = None) -> str:
        shortened = len(numbers) - len(kept)
        parts = [header, f'Instructions ({len(instructions)} chars):\n{shown_instructions}']
        if shortened:
            parts.append(f'[{shortened} turns shortened; use turn(n) to read one in full]')
        parts += [_full_turn(turns[n], (steps or {}).get(n)) if n in kept else _short_turn(turns[n]) for n in numbers]
        parts.append(f'End: {record.status}')
        return '\n\n'.join(parts)

    kept = set(numbers)
    steps: dict[int, set[int]] = {}
    text = render(kept)
    if len(text) > budget_chars:
        essential = {numbers[0], numbers[-1], focus}
        kept = essential | {n for n in numbers if turns[n].failed}
        if len(render(kept)) > budget_chars:
            kept = set(essential)
        for n in reversed(numbers):  # the last turns, as many as fit
            if n in kept:
                continue
            if len(render(kept | {n})) > budget_chars:
                break
            kept.add(n)
        text = render(kept)
    if len(text) > budget_chars:
        steps = {n: _essential_steps(turns[n]) for n in kept}
        for n in sorted(kept, key=lambda n: (n != focus, -n)):  # the focus turn first, then the last turns
            for step in reversed(turns[n].steps):  # its last steps, as many as fit
                if step.n in steps[n]:
                    continue
                if len(render(kept, {**steps, n: steps[n] | {step.n}})) > budget_chars:
                    break
                steps[n].add(step.n)
        text = render(kept, steps)
    truncated = len(text) > budget_chars
    if truncated:
        marker = '\n[cut at the budget]'
        text = text[: budget_chars - len(marker)] + marker
    return {
        'format': 'text',
        'text': text,
        'size_chars': len(text),
        'full_chars': record.full_chars,
        'focus_turn': focus,
        'shortened_turns': [n for n in numbers if n not in kept],
        'shortened_steps': {
            str(n): [step.n for step in turns[n].steps if step.n not in kept_steps]
            for n, kept_steps in steps.items()
            if len(kept_steps) < len(turns[n].steps)
        },
        'truncated': truncated,
    }


# --- the method table: tools and API --------------------------------------------------------------------------------


class _Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NoArguments(_Arguments):
    pass


TURN = 'Only this session turn (1 is the first; record_overview lists them); null for all turns.'


class TimelineArguments(_Arguments):
    kinds: list[EventKind] | None = Field(None, description='Only these event kinds; null for all.')
    turn: int | None = Field(None, ge=1, description=TURN)
    tool: str | None = Field(None, max_length=200, description='Only tool calls and results of this tool name.')


class ToolCallsArguments(_Arguments):
    turn: int | None = Field(None, ge=1, description=TURN)
    name: str | None = Field(None, max_length=200, description='Only calls of this tool name; null for all.')
    errors_only: bool | None = Field(None, description='true: only the calls whose result was an error.')


class ToolOutputArguments(_Arguments):
    call_id: str = Field(min_length=1, max_length=200, description='The call id (from record_tool_calls or a turn).')
    offset: int | None = Field(None, ge=0, description='Where the page starts in the output; null for 0.')
    max_chars: int | None = Field(
        None, ge=100, le=MAX_OUTPUT_PAGE, description=f'The page size in characters; null for {OUTPUT_PAGE}.'
    )


class TurnArguments(_Arguments):
    turn: int = Field(ge=1, description='The session turn number (1 is the first; record_overview lists them).')


class SearchArguments(_Arguments):
    text: str = Field(min_length=1, max_length=200, description='The text to find (case does not matter).')
    kinds: list[SearchKind] | None = Field(None, description='Only in these kinds of text; null for all.')
    turn: int | None = Field(None, ge=1, description=TURN)
    max_hits: int | None = Field(None, ge=1, le=50, description='The most hits returned; null for 20.')


class ExpertCallArguments(_Arguments):
    call_id: str = Field(min_length=1, max_length=200, description='The id of the call that started the nested run.')


class DigestArguments(_Arguments):
    budget_chars: int | None = Field(
        None,
        ge=2_000,
        le=MAX_DIGEST_CHARS,
        description=f'The most characters the digest may have; null for {DEFAULT_DIGEST_CHARS}.',
    )
    turn: int | None = Field(
        None, ge=1, description='The turn kept in full whatever the budget; null for the turn of the run looked at.'
    )


@dataclass(frozen=True)
class Method:
    name: str  # the API's method name
    tool: str  # the tool name a model calls
    description: str  # what a model reads: what it returns and when to use it
    arguments: type[BaseModel]
    run: Callable[[SessionRecord, Any], dict]

    def schema(self) -> dict:
        return ensure_strict_json_schema(self.arguments.model_json_schema())


SESSION = (
    " A session is made of turns (turn n: the person's input, everything the agent did, its answer and status); a "
    'turn is made of steps (one model response each: reasoning, messages, tool calls).'
)
CAPPED = f' Results are capped at {MAX_CHARS} characters: truncated is true and total_chars gives the full size.'
METHODS: dict[str, Method] = {
    method.name: method
    for method in (
        Method(
            'overview',
            'record_overview',
            'The session at a glance: its agent, model, status and times, and the turn being looked at (focus_turn); '
            'one line per turn (input preview, step count, tools called, errors, status, tokens); and totals (tool '
            'calls by name, errors, tokens, expert calls). Start here.' + SESSION + CAPPED,
            NoArguments,
            lambda record, value: record.overview(),
        ),
        Method(
            'digest',
            'record_digest',
            'The whole session as compact text within a character budget: the instructions, then per turn its steps '
            '(input, reasoning, messages, tool calls as name(arguments) -> ok/error with the output size; outputs '
            'are never included), its answer and status. Over budget, the first and last turns, turns with errors '
            'and the focus turn stay in full and the others are shortened to one line (then middle steps the same '
            'way); read those with record_turn. Use it to read the session once from start to end.',
            DigestArguments,
            lambda record, value: record.digest(value.budget_chars or DEFAULT_DIGEST_CHARS, value.turn),
        ),
        Method(
            'timeline',
            'record_timeline',
            'The session events in order (input, instructions, reasoning, message, tool_call, tool_result, usage, '
            'error, end), each with its turn and step; tool results give their status and size, not the output. '
            'Filter by kinds, turn or tool name to follow one part of the session.' + CAPPED,
            TimelineArguments,
            lambda record, value: record.timeline(value.kinds, value.turn, value.tool),
        ),
        Method(
            'tool_calls',
            'record_tool_calls',
            'The tool calls of the session: call id, turn, step, tool name, arguments, status (ok, error, or pending '
            'without a result), output size and whether it was an expert call (open it with record_expert_call). '
            'Filter by turn, tool name or errors only.' + CAPPED,
            ToolCallsArguments,
            lambda record, value: record.tool_calls(value.turn, value.name, bool(value.errors_only)),
        ),
        Method(
            'tool_output',
            'record_tool_output',
            "One page of one tool call's output, in any turn and also inside an expert call: the text from offset, "
            'the total size and next_offset (null at the end). Use it when the size or an error says the output '
            'matters; read further pages with next_offset.',
            ToolOutputArguments,
            lambda record, value: record.tool_output(value.call_id, value.offset or 0, value.max_chars or OUTPUT_PAGE),
        ),
        Method(
            'turn',
            'record_turn',
            'One session turn in full: its status, answer and steps, each step with its inputs, reasoning, '
            'messages, tool calls with arguments and status (outputs as their size, with a short error text for a '
            'failed call), errors, usage and end. Use it for a turn the digest shortened or one with errors.' + CAPPED,
            TurnArguments,
            lambda record, value: record.turn(value.turn),
        ),
        Method(
            'search',
            'record_search',
            'Find text in the session (messages, inputs, reasoning, tool arguments, tool outputs and errors, expert '
            'calls included), case-insensitive: per hit its kind, turn, step, call id (in_call: the expert call it '
            'is inside) and a snippet around it. Use it to find where something was said or returned.' + CAPPED,
            SearchArguments,
            lambda record, value: record.search(value.text, value.kinds, value.turn, value.max_hits or 20),
        ),
        Method(
            'expert_call',
            'record_expert_call',
            "One expert call (or subagent run) started by a tool call: the call, the nested run's overview (one line "
            'per step) and its timeline. Read its tool outputs with record_tool_output.' + CAPPED,
            ExpertCallArguments,
            lambda record, value: record.expert_call(value.call_id),
        ),
    )
}


def listing() -> list[dict]:
    """The methods as the model sees them: name, tool name, description and strict JSON schema."""
    return [
        {'method': m.name, 'tool': m.tool, 'description': m.description, 'parameters': m.schema()}
        for m in METHODS.values()
    ]


def call(record: SessionRecord, name: str, args: dict | None) -> dict:
    """Run one method by name; ValueError for an unknown method or a bad value (prefixed with the argument),
    pydantic.ValidationError for invalid arguments."""
    method = METHODS.get(name)
    if method is None:
        raise ValueError(f'method: unknown method {name}; one of {", ".join(METHODS)}.')
    return method.run(record, method.arguments.model_validate(args or {}))


def tools(record: SessionRecord) -> list[FunctionTool]:
    """The methods as SDK function tools bound to `record` (no run_key argument: a model reads only this session).
    Not in the tool catalog: the session reviewer gives them to its own model."""
    from knowledge_admin.tools.sdk import _function_tool

    def bound(method: Method):
        async def run(context: dict, value: BaseModel) -> str:
            return json.dumps(method.run(record, value), ensure_ascii=False)

        return _function_tool(method.tool, method.description, method.arguments, run)

    return [bound(method) for method in METHODS.values()]
