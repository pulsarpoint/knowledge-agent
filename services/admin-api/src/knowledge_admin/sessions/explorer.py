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

import asyncio
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
# A scheme starts where the previous character cannot be part of one and is at most 32 characters: one pass, linear
# (an unbounded scheme run made a long run of letters quadratic).
URL = re.compile(r'(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s"\'<>`]+')
HEAD_CHARS = 2_000  # a long field repeated on every page (an answer, an expert's task) is shortened to this
STEP_TEXT = 2_000  # a step's input, reasoning or message text in turn()
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
        return {_clean(key, secrets): _clean(item, secrets) for key, item in value.items()}
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


def _build_run(
    rows: list[dict],
    groups: dict[str | None, list[dict]],
    turn: int,
    parent: str | None = None,
    seen: frozenset[str] = frozenset(),
) -> Run:
    """One run (a turn's own events, or a nested run under call `parent`) out of its rows in seq order; nested runs
    recursively (seen: the calls above it, so a call id repeated in its own nested run cannot loop)."""
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
            if item['kind'] == 'retry':
                answer = ''  # the retried attempt answers afresh (SessionEvents.draft of a new turn)
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
            if call_id in groups and call_id not in seen and call_id != parent:
                found.child = _build_run(groups[call_id], groups, turn, call_id, seen | {call_id})
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


def _record_size(payload: dict) -> int:
    """A payload's serialised size without what repeats other fields (a result's preview, a call's summary)."""
    repeated = {'summary', 'preview'} if 'output' in payload else {'summary'}
    kept = {key: value for key, value in payload.items() if key not in repeated}
    return len(json.dumps(kept, ensure_ascii=False, default=str))


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
            run.unlinked[parent] = _build_run(child_rows, groups, n, parent, frozenset({parent}))
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
        full_chars=sum(_record_size(row['payload']) for row in rows),
        agent=first.agent,
        agent_id=first.agent_id,
        title=first.title,
    )


# --- caps and paging ------------------------------------------------------------------------------------------------


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def _shorten(value: Any, chars: int, items: int | None = None) -> Any:
    """Strings cut to `chars` (with how much was cut) and lists to their first `items`, at every depth."""
    if isinstance(value, str):
        return value if len(value) <= chars else value[:chars] + f'… [{len(value) - chars} more chars]'
    if isinstance(value, dict):
        return {key: _shorten(item, chars, items) for key, item in value.items()}
    if isinstance(value, list):
        return [_shorten(item, chars, items) for item in (value if items is None else value[:items])]
    return value


PAGING = ('offset', 'limit', 'next_offset', 'total_items', 'total_chars')
TOO_LARGE = 'This result is too large even shortened; ask for a smaller part (limit, a turn, a call).'


def _squeeze(result: dict) -> dict:
    """The last resort for a result over MAX_CHARS: every string and nested list shortened, shorter and shorter, until
    it fits (its paging fields kept as they are)."""
    kept = {key: result[key] for key in PAGING if key in result}
    chars, items = 2_000, 100
    while True:
        squeezed = {**_shorten(result, chars, items), **kept, 'truncated': True}
        if _size(squeezed) <= MAX_CHARS or (chars <= 20 and items <= 1):
            break
        chars, items = max(20, chars // 2), max(1, items // 2)
    return squeezed if _size(squeezed) <= MAX_CHARS else {**kept, 'truncated': True, 'error': TOO_LARGE}


def _page(head: dict, key: str, items: list, offset: int = 0, limit: int | None = None) -> dict:
    """result[key] = items[offset:offset+limit], in order, as many as fit in MAX_CHARS with the rest of the result:
    next_offset is where the next page starts (null: nothing after it), total_items how many there are, total_chars
    the size of the whole unpaged result; truncated is true when the cap cut anything (fewer items than asked, or
    shortened content when one item alone does not fit)."""
    total = len(items)
    window = items[offset : offset + limit] if limit is not None else items[offset:]
    full = _size({**head, key: items})

    def result(n: int, cut: bool) -> dict:
        end = offset + n
        return {
            **head,
            key: window[:n],
            'offset': offset,
            'next_offset': end if end < total else None,
            'total_items': total,
            'total_chars': full,
            'truncated': cut,
        }

    whole = result(len(window), False)
    if _size(whole) <= MAX_CHARS:
        return whole
    if len(window) <= 1 or _size(result(1, True)) > MAX_CHARS:
        return _squeeze(result(min(1, len(window)), True))
    low, high = 1, len(window) - 1  # the most items that fit
    while low < high:
        middle = (low + high + 1) // 2
        if _size(result(middle, True)) <= MAX_CHARS:
            low = middle
        else:
            high = middle - 1
    return result(low, True)


def _nested_run(call: Call) -> dict | None:
    return None if call.child is None else {'kind': call.child.kind, 'agent': call.child.agent}


def _step_line(step: Step) -> dict:
    return {
        'step': step.n,
        'input': _shorten(step.inputs[0]['text'], PREVIEW) if step.inputs else None,
        'tools': dict(Counter(call.name for call in step.calls)),
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
                'arguments': _shorten(call.arguments, STEP_TEXT, 50),
                'status': call.status,
                'output': output,
                'nested_run': _nested_run(call),
            }
        )
    return {
        'step': step.n,
        'inputs': _shorten(step.inputs, STEP_TEXT),
        'reasoning': _shorten(step.reasoning, STEP_TEXT),
        'messages': _shorten(step.messages, STEP_TEXT),
        'tool_calls': calls,
        'errors': step.errors,
        'usage': step.usage,
        'end': step.end,
    }


def _totals(runs: list[Run]) -> dict:
    calls = [call for run in runs for call in run.own_calls()]
    nested = [child for run in runs for child in run.nested()]
    return {
        'steps': sum(len(run.steps) for run in runs),
        'tool_calls': dict(Counter(call.name for call in calls)),
        'tool_errors': sum(call.status == 'error' for call in calls),
        'errors': sum(len(step.errors) for run in runs for step in run.steps),
        'tokens': sum(run.tokens() for run in runs),  # its own model calls, nested runs not included
        'expert_calls': sum(child.kind == 'expert' for child in nested),
        'nested_runs': len(nested),  # expert calls and subagent runs
        'nested_tokens': sum(run.nested_tokens() for run in runs),
    }


# --- the record -----------------------------------------------------------------------------------------------------

EVENTS = """SELECT e.run_key,e.seq,e.kind,e.agent_id,e.payload,e.created_at,p.email AS "user"
    FROM ka.agent_run_events e LEFT JOIN ka.principals p ON p.id=e.actor_id
    WHERE e.project_id=%s AND (e.run_key=%s OR (%s::text IS NOT NULL AND starts_with(e.run_key, %s)))
    ORDER BY e.run_key,e.seq"""


def session_secrets(conn, store, project, session_id: str | None) -> list[str]:
    """The secrets the session's pinned configuration references (agents.runner.known_secrets), for the second
    redaction pass, as the session Debug tab redacts (records were redacted when written, experts' own keys too)."""
    if session_id is None or store is None:
        return []
    from knowledge_admin.agents.runner import known_secrets

    pinned = conn.execute(
        'SELECT agent_config FROM ka.agent_sessions WHERE project_id=%s AND id::text=%s', (project, session_id)
    ).fetchone()
    return known_secrets(store, pinned['agent_config']) if pinned and pinned['agent_config'] else []


def session_of(run_key: str) -> tuple[str | None, int]:
    """(session id, turn) of a run key; (None, 1) for a run outside a session."""
    match = SESSION_KEY.fullmatch(run_key)
    return (match.group(1), int(match.group(2))) if match else (None, 1)


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
    def load(cls, conn, store, project, run_key: str) -> 'SessionRecord':
        """The whole session of a run key (`cc:<session_id>:<n>`; a run outside a session alone), every turn read once,
        in this workspace, redacted again with the session's pinned secrets (session_secrets; store: the repository
        store that decrypts them); RunNotFound unless this workspace has that run key's own record."""
        session_id, _ = session_of(run_key)
        return cls._read(conn, store, project, run_key, session_id, focus=run_key)

    @classmethod
    def load_session(cls, conn, store, project, session_id: str) -> 'SessionRecord':
        """A session by its id (for internal callers such as the session reviewer; the API takes run keys only), its
        last turn the focus turn, redacted as load; RunNotFound when it has no record."""
        return cls._read(conn, store, project, None, session_id, focus=None)

    @classmethod
    def _read(cls, conn, store, project, run_key, session_id, focus) -> 'SessionRecord':
        prefix = f'cc:{session_id}:' if session_id is not None else None
        rows = conn.execute(EVENTS, (project, run_key, prefix, prefix)).fetchall()
        rows = [row for row in rows if row['run_key'] == run_key or SESSION_KEY.fullmatch(row['run_key'])]
        if not rows or (focus is not None and not any(row['run_key'] == focus for row in rows)):
            raise RunNotFound(NOT_FOUND)
        model = None
        if session_id is not None:
            found = conn.execute(
                """SELECT m.name FROM ka.agent_sessions s JOIN ka.llm_models m ON m.id=s.model_id
                WHERE s.project_id=%s AND s.id::text=%s""",
                (project, session_id),
            ).fetchone()
            model = found['name'] if found else None
        focus = focus or max((row['run_key'] for row in rows), key=lambda key: _turn_number(key) or 0)
        secrets = session_secrets(conn, store, project, session_id)  # every load path: no caller can forget them
        return build(rows, run_key=focus, meta={'session_id': session_id, 'model': model}, secrets=secrets)

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

    def _nested(self, call_id: str) -> tuple[Call | None, Run]:
        """The call (None for a nested run whose call is not recorded) and the nested run it started."""
        for turn in self.turns:
            if call_id in turn.unlinked:
                return None, turn.unlinked[call_id]
        call = self._call(call_id)
        if call.child is None:
            raise ValueError(f'call_id: {call_id} is not an expert call or subagent run.')
        return call, call.child

    # methods

    def overview(self, offset: int = 0, limit: int | None = None) -> dict:
        """The session's metadata and totals, and one line per turn (with its step count), paged."""
        head = {
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
            'totals': {'turns': len(self.turns), **_totals(self.turns)},
        }
        lines = [
            {
                'turn': turn.turn,
                'run_key': turn.run_key,
                'input': _shorten(turn.input, PREVIEW),
                'steps': len(turn.steps),
                'tools': dict(Counter(call.name for call in turn.own_calls())),
                'errors': sum(len(s.errors) for s in turn.steps) + sum(c.status == 'error' for c in turn.own_calls()),
                'status': turn.status,
                'tokens': turn.tokens(),
            }
            for turn in self.turns
        ]
        return _page(head, 'turns', lines, offset, limit)

    def timeline(
        self,
        kinds: list[str] | None = None,
        turn: int | None = None,
        tool: str | None = None,
        call_id: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> dict:
        """The events in order, each with its turn and step (of the session, or of one nested run: call_id), filtered
        by kind, turn and tool name, paged; tool outputs as their size only."""
        runs = [self._nested(call_id)[1]] if call_id is not None else self._turns(turn)
        events = [
            event
            for run in runs
            for event in run.events
            if (kinds is None or event['kind'] in kinds) and (tool is None or event.get('name') == tool)
        ]
        return _page({}, 'events', events, offset, limit)

    def tool_calls(
        self,
        turn: int | None = None,
        name: str | None = None,
        errors_only: bool = False,
        offset: int = 0,
        limit: int | None = None,
    ) -> dict:
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
        return _page({}, 'tool_calls', found, offset, limit)

    def tool_output(self, call_id: str, offset: int = 0, max_chars: int = OUTPUT_PAGE) -> dict:
        call = self._call(call_id)
        while True:  # escaped characters can make a page's JSON longer than its text: the page shrinks to the cap
            text = call.output[offset : offset + max_chars]
            end = offset + len(text)
            result = {
                'call_id': call.call_id,
                'turn': call.turn,
                'name': call.name,
                'status': call.status,
                'total_chars': call.size,
                'offset': offset,
                'text': text,
                'next_offset': end if end < call.size else None,
            }
            if _size(result) <= MAX_CHARS or max_chars <= 100:
                return result
            max_chars = max(100, max_chars * 3 // 4)

    def turn(self, n: int, offset: int = 0, limit: int | None = None) -> dict:
        """Session turn n with its steps in full (paged: offset is a step offset), outputs as their size."""
        run = self._turn(n)
        head = {
            'turn': run.turn,
            'run_key': run.run_key,
            'status': run.status,
            'started_at': run.started_at,
            'ended_at': run.ended_at,
            'tokens': run.tokens(),
            'answer': _shorten(run.answer, HEAD_CHARS),
        }
        return _page(head, 'steps', [_step_in_full(step) for step in run.steps], offset, limit)

    def search(
        self,
        text: str,
        kinds: list[str] | None = None,
        turn: int | None = None,
        offset: int = 0,
        max_hits: int = 20,
    ) -> dict:
        """Case-insensitive substring search; one hit per text, with how often it matched and a snippet around the
        first match; paged (max_hits per page)."""
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
        return _page({'total_hits': len(hits)}, 'hits', hits, offset, max_hits)

    def expert_call(self, call_id: str, offset: int = 0, limit: int | None = None) -> dict:
        """The call, the nested run it started with its totals, and one line per step of it, paged."""
        call, run = self._nested(call_id)
        head = {
            'call_id': call_id,
            'turn': run.turn,
            'name': call.name if call else None,
            'arguments': _shorten(call.arguments, HEAD_CHARS // 4, 20) if call else None,
            'status': call.status if call else 'unlinked',
            'run': {
                'kind': run.kind,
                'agent': run.agent,
                'agent_id': run.agent_id,
                'status': run.status,
                'started_at': run.started_at,
                'answer': _shorten(run.answer, HEAD_CHARS),
            },
            'totals': _totals([run]),
        }
        return _page(head, 'steps', [_step_line(step) for step in run.steps], offset, limit)

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
# How a turn shows, from the most to the least: every step in full; its first, last and failed steps in full and the
# others one line each; the same with the other steps grouped into one line per run of them; one line; left out
# (consecutive left-out turns share one line). The first, focus and last turns go no further than compact, then brief
# (only the first and last steps in full; failed steps as their error kind and a short reason, a few, then a count),
# then head (the header alone: the digest is truncated).
FORMS = ('full', 'steps', 'compact', 'line', 'dropped')
ESSENTIAL_FORMS = ('steps', 'compact', 'brief')
FAILED_SHOWN = 5  # failed steps listed in a brief turn
REASON_CHARS = 100


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f' … [{len(text) - limit} more chars]'


def _call_line(call: Call) -> str:
    line = f'- {call.name}({_cut(_text(call.arguments), ARGUMENT_CHARS)}) -> {call.status}, {call.size} chars'
    if call.child is not None:
        child = call.child
        line += (
            f' [{child.kind} {child.agent}: {len(child.steps)} steps, {len(child.own_calls())} tool calls; '
            f'record_expert_call({call.call_id})]'
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
    calls = ', '.join(f'{name} x{n}' for name, n in Counter(call.name for call in step.calls).items())
    return f'Step {step.n}: {calls or "no tool calls"} · {len(step.messages)} messages · {step.usage.get("total_tokens", 0)} tokens'


def _step_group(run: Run, steps: list[Step]) -> str:
    calls = sum(len(step.calls) for step in steps)
    span = f'{steps[0].n}' if len(steps) == 1 else f'{steps[0].n}-{steps[-1].n}'
    return (
        f'[Steps {span}: {len(steps)} steps, {calls} tool calls left out; '
        f'record_turn(turn={run.turn}, offset={steps[0].n - 1}) reads them]'
    )


def _failure(step: Step) -> str:
    """A failed step as its error kind and a short reason."""
    if step.errors:
        kind, reason = step.errors[0]['type'], step.errors[0]['message']
    else:
        failed = next(call for call in step.calls if call.status == 'error')
        kind, reason = failed.name, failed.output
    return f'Step {step.n} failed: {kind}: {_cut(" ".join(reason.split()), REASON_CHARS)}'


def _brief_turn(run: Run, head: str) -> str:
    first, last = run.steps[0], run.steps[-1]
    middle = run.steps[1:-1]
    failed = [step for step in middle if step.failed]
    parts = [head, _full_step(first)]
    parts += [_failure(step) for step in failed[:FAILED_SHOWN]]
    if middle:
        calls = sum(len(step.calls) for step in middle)
        parts.append(
            f'[Steps {middle[0].n}-{middle[-1].n}: {len(middle)} steps ({len(failed)} failed steps, '
            f'{max(0, len(failed) - FAILED_SHOWN)} more not listed), {calls} tool calls left out; '
            f'record_turn(turn={run.turn}, offset={middle[0].n - 1}) reads them]'
        )
    if last is not first:
        parts.append(_full_step(last))
    parts.append(f'Answer: {_cut(run.answer, MESSAGE_CHARS)}' if run.answer else 'Answer: (none)')
    return '\n'.join(parts)


def _turn_text(run: Run, form: str) -> str:
    if form == 'line':
        return (
            f'Turn {run.turn}: {_cut(run.input, 120)!r} · {len(run.steps)} steps · {len(run.own_calls())} tool calls · '
            f'status {run.status} · {run.tokens()} tokens'
        )
    head = f'## Turn {run.turn} ({run.run_key}) · {len(run.steps)} steps · status {run.status} · {run.tokens()} tokens'
    if form == 'head':
        return f'{head}\n[shortened to its header: over budget; record_turn(turn={run.turn}) reads it]'
    if form == 'brief' and run.steps:
        return _brief_turn(run, head)
    numbers = [step.n for step in run.steps]
    kept = (
        set(numbers)
        if form == 'full'
        else {numbers[0], numbers[-1], *(s.n for s in run.steps if s.failed)}
        if numbers
        else set()
    )
    parts, group = [head], []
    for step in run.steps:
        if step.n in kept or form == 'steps':
            if group:
                parts.append(_step_group(run, group))
                group = []
            parts.append(_full_step(step) if step.n in kept else _short_step(step))
        else:
            group.append(step)
    if group:
        parts.append(_step_group(run, group))
    parts.append(f'Answer: {_cut(run.answer, MESSAGE_CHARS)}' if run.answer else 'Answer: (none)')
    return '\n'.join(parts)


def _dropped_line(runs: list[Run]) -> str:
    calls = sum(len(run.own_calls()) for run in runs)
    errors = sum(run.failed for run in runs)
    span = f'{runs[0].turn}' if len(runs) == 1 else f'{runs[0].turn}-{runs[-1].turn}'
    return (
        f'[Turns {span}: {len(runs)} turns, {calls} tool calls, {errors} with errors left out; '
        f'record_overview(offset={runs[0].turn - 1}) lists them, record_turn(n) reads one]'
    )


def digest(record: SessionRecord, budget_chars: int = DEFAULT_DIGEST_CHARS, focus: int | None = None) -> dict:
    """The session compressed to text (a model reads it as is; JSON would spend the budget on keys and quotes): the
    header, the instructions (at most a quarter of the budget), every turn (its steps: inputs, reasoning, messages,
    tool calls as name(arguments) -> ok/error, output size; outputs never; then its answer and status) and the end.

    Over budget, turns are reduced one at a time, oldest first, until it fits (FORMS): the middle turns (not the first,
    last, focus or errored ones) to steps, then compact, then one line, then left out; then the errored turns the same
    way; then the first, focus and last turns to steps, compact and brief (ESSENTIAL_FORMS). Then the instructions are
    left out and, as the last resort, the first, focus and last turns become their headers (truncated: true); their
    headers and the end always stay. Then what was left out comes back as one line per turn, the latest first, while it
    fits, so the budget is used."""
    focus = record.focus_turn if focus is None else record._turn(focus).turn
    runs = {run.turn: run for run in record.turns}
    numbers = list(runs)
    essential = list(dict.fromkeys([numbers[0], focus, numbers[-1]]))
    errored = [n for n in numbers if runs[n].failed and n not in essential]
    middle = [n for n in numbers if n not in essential and n not in errored]
    calls = sum(len(run.own_calls()) for run in record.turns)
    header = (
        f'Session {record.session_id or record.run_key} · agent {record.agent or record.agent_id} · '
        f'model {record.model or "unknown"} · status {record.status} · {len(numbers)} turns · '
        f'{sum(len(run.steps) for run in record.turns)} steps · {calls} tool calls · focus turn {focus}'
    )
    instructions = record.turns[0].instructions
    end = f'End: {record.status}'
    form = dict.fromkeys(numbers, 'full')
    pieces: dict[tuple[int, str], str] = {}
    shown_instructions = [_cut(instructions, max(500, budget_chars // INSTRUCTIONS_SHARE))]

    def piece(n: int) -> str:
        key = (n, form[n])
        if key not in pieces:
            pieces[key] = _turn_text(runs[n], form[n])
        return pieces[key]

    def render() -> str:
        reduced = sum(f != 'full' for f in form.values())
        parts = [header, f'Instructions ({len(instructions)} chars):\n{shown_instructions[0]}']
        if reduced:
            parts.append(f'[{reduced} turns shortened or left out; record_turn(n) reads one in full]')
        dropped: list[Run] = []
        for n in numbers:
            if form[n] == 'dropped':
                dropped.append(runs[n])
                continue
            if dropped:
                parts.append(_dropped_line(dropped))
                dropped = []
            parts.append(piece(n))
        if dropped:
            parts.append(_dropped_line(dropped))
        return '\n\n'.join([*parts, end])

    def fits() -> bool:
        return len(render()) <= budget_chars

    actions = [(n, f) for f in FORMS[1:] for n in middle]
    actions += [(n, f) for f in FORMS[1:] for n in errored]
    actions += [(n, f) for f in ESSENTIAL_FORMS for n in essential]
    for n, f in actions:
        if fits():
            break
        form[n] = f
    if not fits():
        shown_instructions[0] = '(left out: over budget)'
    for n in essential:  # the last resort: the first, focus and last turns as their headers
        if fits():
            break
        form[n] = 'head'
    # refill: what was left out comes back as one line each, the latest first, while it fits
    for n in reversed(numbers):
        if form[n] == 'dropped':
            form[n] = 'line'
            if not fits():
                form[n] = 'dropped'
    text = render()
    truncated = any(form[n] == 'head' for n in essential)
    if len(text) > budget_chars:  # headers alone over budget: the session header, the turn headers and the end
        truncated = True
        heads = [_turn_text(runs[n], 'head').split('\n')[0] for n in essential]
        text = '\n\n'.join(
            [header[: max(0, budget_chars - sum(len(h) + 2 for h in heads) - len(end) - 2)], *heads, end]
        )
    return {
        'format': 'text',
        'text': text,
        'size_chars': len(text),
        'full_chars': record.full_chars,
        'focus_turn': focus,
        'reduced': {str(n): f for n, f in form.items() if f != 'full'},
        'truncated': truncated,
    }


# --- the method table: tools and API --------------------------------------------------------------------------------


class _Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


TURN = 'Only this session turn (1 is the first; record_overview lists them); null for all turns.'
OFFSET = 'Where the page starts (next_offset of the previous page); null for 0.'
LIMIT = 'The most items on the page; null for as many as fit in the cap.'


class PageArguments(_Arguments):
    offset: int | None = Field(None, ge=0, description=OFFSET)
    limit: int | None = Field(None, ge=1, le=1_000, description=LIMIT)


class OverviewArguments(PageArguments):
    pass


class TimelineArguments(PageArguments):
    kinds: list[EventKind] | None = Field(None, description='Only these event kinds; null for all.')
    turn: int | None = Field(None, ge=1, description=TURN)
    tool: str | None = Field(None, max_length=200, description='Only tool calls and results of this tool name.')
    call_id: str | None = Field(
        None, max_length=200, description='The events of the expert call or subagent run this call started instead.'
    )


class ToolCallsArguments(PageArguments):
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
    offset: int | None = Field(None, ge=0, description='The first step on the page, as a step offset; null for 0.')
    limit: int | None = Field(None, ge=1, le=1_000, description='The most steps on the page; null for as many as fit.')


class SearchArguments(_Arguments):
    text: str = Field(min_length=1, max_length=200, description='The text to find (case does not matter).')
    kinds: list[SearchKind] | None = Field(None, description='Only in these kinds of text; null for all.')
    turn: int | None = Field(None, ge=1, description=TURN)
    offset: int | None = Field(None, ge=0, description=OFFSET)
    max_hits: int | None = Field(None, ge=1, le=50, description='The most hits on the page; null for 20.')


class ExpertCallArguments(PageArguments):
    call_id: str = Field(min_length=1, max_length=200, description='The id of the call that started the nested run.')


class DigestArguments(_Arguments):
    budget_chars: int | None = Field(
        None,
        ge=2_000,
        le=MAX_DIGEST_CHARS,
        description=f'The most characters the digest may have; null for {DEFAULT_DIGEST_CHARS}.',
    )
    turn: int | None = Field(
        None, ge=1, description='The turn kept whatever the budget; null for the turn of the run looked at.'
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
PAGED = (
    f' Paged and capped at {MAX_CHARS} characters: next_offset (null at the end) is the offset of the next page, '
    'total_items how many there are; truncated is true when the cap cut the page or shortened its content.'
)
CAPPED = f' Capped at {MAX_CHARS} characters.'


def _page_args(value) -> dict:
    return {'offset': value.offset or 0, 'limit': value.limit}


METHODS: dict[str, Method] = {
    method.name: method
    for method in (
        Method(
            'overview',
            'record_overview',
            'The session at a glance: its agent, model, status and times, the turn being looked at (focus_turn) and '
            'totals (tool calls by name, errors, tokens of its own model calls, expert calls and nested runs with '
            'their tokens); then one line per turn (input preview, step count, tools called, errors, status, '
            'tokens). Start here.' + SESSION + PAGED,
            OverviewArguments,
            lambda record, value: record.overview(**_page_args(value)),
        ),
        Method(
            'digest',
            'record_digest',
            'The whole session as compact text within a character budget: the instructions, then per turn its steps '
            '(input, reasoning, messages, tool calls as name(arguments) -> ok/error with the output size; outputs '
            'are never included), its answer and status, and the end. Over budget, middle turns are shortened first '
            '(their middle steps, then one line, then left out with a count), errored turns last; the first, last '
            'and focus turns and the end always stay. The notes in the text say which record_turn or '
            'record_overview call reads what was left out. Use it to read the session once from start to end.',
            DigestArguments,
            lambda record, value: record.digest(value.budget_chars or DEFAULT_DIGEST_CHARS, value.turn),
        ),
        Method(
            'timeline',
            'record_timeline',
            'The session events in order (input, instructions, reasoning, message, tool_call, tool_result, usage, '
            'error, end), each with its turn and step; tool results give their status and size, not the output. '
            'Filter by kinds, turn or tool name, or give call_id for the events of one expert call.' + PAGED,
            TimelineArguments,
            lambda record, value: record.timeline(
                value.kinds, value.turn, value.tool, value.call_id, **_page_args(value)
            ),
        ),
        Method(
            'tool_calls',
            'record_tool_calls',
            'The tool calls of the session: call id, turn, step, tool name, arguments, status (ok, error, or pending '
            'without a result), output size and whether it was an expert call (open it with record_expert_call). '
            'Filter by turn, tool name or errors only.' + PAGED,
            ToolCallsArguments,
            lambda record, value: record.tool_calls(
                value.turn, value.name, bool(value.errors_only), **_page_args(value)
            ),
        ),
        Method(
            'tool_output',
            'record_tool_output',
            "One page of one tool call's output, in any turn and also inside an expert call: the text from offset, "
            'the total size and next_offset (null at the end). Use it when the size or an error says the output '
            'matters; read further pages with next_offset.' + CAPPED,
            ToolOutputArguments,
            lambda record, value: record.tool_output(value.call_id, value.offset or 0, value.max_chars or OUTPUT_PAGE),
        ),
        Method(
            'turn',
            'record_turn',
            'One session turn in full: its status and answer, then its steps, each with its inputs, reasoning, '
            'messages, tool calls with arguments and status (outputs as their size, with a short error text for a '
            'failed call), errors, usage and end. offset is a step offset: a long turn is read page by page. Use it '
            'for a turn the digest shortened or one with errors.' + PAGED,
            TurnArguments,
            lambda record, value: record.turn(value.turn, value.offset or 0, value.limit),
        ),
        Method(
            'search',
            'record_search',
            'Find text in the session (messages, inputs, reasoning, tool arguments, tool outputs and errors, expert '
            'calls included), case-insensitive: per hit its kind, turn, step, call id (in_call: the expert call it '
            'is inside) and a snippet around it; total_hits counts them all. Use it to find where something was '
            'said or returned.' + PAGED,
            SearchArguments,
            lambda record, value: record.search(
                value.text, value.kinds, value.turn, value.offset or 0, value.max_hits or 20
            ),
        ),
        Method(
            'expert_call',
            'record_expert_call',
            'One expert call (or subagent run) started by a tool call: the call, the nested run (agent, status, '
            'answer) with its totals, and one line per step of it. Its events: record_timeline with call_id; its '
            'tool outputs: record_tool_output.' + PAGED,
            ExpertCallArguments,
            lambda record, value: record.expert_call(value.call_id, **_page_args(value)),
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
    return loaded_tools(lambda: record)


def loaded_tools(load: Callable[[], SessionRecord]) -> list[FunctionTool]:
    """tools() for a record read on first use: `load` (synchronous, run in a thread) returns the one session the tools
    read; its run key never comes from the model (sessions/review_agent.py binds it to the review's pinned run key)."""
    from knowledge_admin.tools.sdk import _function_tool

    def bound(method: Method):
        async def run(context: dict, value: BaseModel) -> str:
            record = await asyncio.to_thread(load)
            return json.dumps(method.run(record, value), ensure_ascii=False)

        return _function_tool(method.tool, method.description, method.arguments, run)

    return [bound(method) for method in METHODS.values()]
