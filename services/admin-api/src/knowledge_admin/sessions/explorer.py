"""The session explorer: one agent run's record (ka.agent_run_events of one run_key) as an in-memory object with query
methods, for a person (the run page's Debug tab, POST /api/agent-runs/{run_key}/explore) and for a model (tools(): the
same methods as SDK function tools bound to one record, so a model cannot reach another run).

build() is a pure function from the record's rows; SessionRecord.load reads them once. The record is split into
turns: a turn is one model response with what led to it, its input (the task, a comment sent while the agent worked,
a retry, an expert's or subagent's task), its reasoning and messages, its tool calls paired with their results by
call_id, the errors and the end it reached, and its usage. A new turn starts when an input or a model event follows a
turn that already has its usage or its end. Events with a parent_call_id are a nested run (an expert call, or a
subagent run_subagent started) and are built as a child record under the tool call with that call_id, as the run page
reads an expert call (runs_api: ?call=).

Every method is deterministic and returns JSON values, capped at MAX_CHARS of serialised JSON (truncated and
total_chars tell what was cut); every string has gone through redaction (activity.redact with the run's secrets) and
URL masking (http_errors.safe_url: credentials and queries hidden). METHODS is the one table of method names, argument
models and functions that the tools and the API both use."""

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

MODEL_KINDS = ('reasoning', 'message', 'tool_call', 'usage')
TOKEN_KEYS = ('input_tokens', 'output_tokens', 'cached_tokens', 'reasoning_tokens', 'total_tokens')
URL = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://[^\s"\'<>`]+')
EventKind = Literal[
    'start', 'instructions', 'input', 'reasoning', 'message', 'tool_call', 'tool_result', 'usage', 'error', 'end'
]
SearchKind = Literal['input', 'reasoning', 'message', 'tool_call', 'tool_result', 'error']


class RunNotFound(LookupError):
    """No record for this run_key in this workspace."""


@dataclass
class Call:
    call_id: str
    name: str
    arguments: Any
    turn: int
    seq: int
    status: str = 'pending'  # ok | error | pending (no result recorded)
    output: str = ''
    child: 'SessionRecord | None' = None  # the expert call or subagent run it started

    @property
    def size(self) -> int:
        return len(self.output)


@dataclass
class Turn:
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


# --- building -------------------------------------------------------------------------------------------------------


def _clean(value: Any, secrets: tuple[str, ...]) -> Any:
    """Every string with its URLs masked (safe_url), then redacted with the run's secrets."""
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


def _build_group(rows: list[dict], groups: dict[str | None, list[dict]], parent: str | None) -> 'SessionRecord':
    """One record (the run, or a nested run under call `parent`) out of its rows in seq order; nested runs recursively."""
    record = SessionRecord(run_key='', parent_call_id=parent)
    turns: list[Turn] = []
    calls: dict[str, Call] = {}
    answer = ''

    def current() -> Turn:
        if not turns:
            turns.append(Turn(1))
        return turns[-1]

    for row in rows:
        kind, payload = row['kind'], row['payload']
        turn = current()
        if kind in ('input', *MODEL_KINDS) and (turn.responded or turn.end is not None):
            turn = Turn(len(turns) + 1)
            turns.append(turn)
        entry: dict[str, Any] = {'seq': row['seq'], 'turn': turn.n, 'kind': kind}
        if kind == 'start':
            record.title, record.agent = payload.get('title'), payload.get('agent')
            record.run_kind, record.session_turn = payload.get('kind'), payload.get('turn')
            entry.update(title=payload.get('title'), run_kind=payload.get('kind'))
        elif kind == 'instructions':
            if not record.instructions:
                record.instructions = payload.get('text', '')
            entry.update(agent=payload.get('agent'), text=payload.get('text', ''))
        elif kind == 'input':
            item = {'kind': _input_kind(payload), 'text': payload.get('task', '')}
            if item['kind'] in ('expert', 'subagent'):
                record.kind = item['kind']
                record.agent = (payload.get('expert') or {}).get('name') or record.agent
            turn.inputs.append(item)
            entry.update(item, kind='input', input=item['kind'])
        elif kind == 'reasoning':
            text = payload.get('text', '') if payload.get('available', True) else '(not returned by the provider)'
            turn.reasoning.append(text)
            entry['text'] = text
        elif kind == 'message':
            turn.messages.append(payload.get('text', ''))
            answer = payload.get('text', '')
            entry['text'] = answer
        elif kind == 'tool_call':
            call_id = payload.get('call_id') or f'#{row["seq"]}'  # an old event without one
            found = Call(call_id, payload.get('name', ''), payload.get('arguments'), turn.n, row['seq'])
            if call_id in groups:
                found.child = _build_group(groups[call_id], groups, call_id)
            turn.calls.append(found)
            calls[call_id] = found
            answer = ''  # a message before a tool call was not the answer
            entry.update(name=found.name, call_id=call_id, arguments=found.arguments)
            if found.child is not None:
                entry['nested_run'] = found.child.kind
        elif kind == 'tool_result':
            call_id = payload.get('call_id') or ''
            output = payload['output'] if 'output' in payload else payload.get('preview', '')
            output = output if isinstance(output, str) else _text(output)
            error = payload['is_error'] if isinstance(payload.get('is_error'), bool) else tool_failed(output)
            found = calls.get(call_id)
            if found is None:  # a result without its call in this record
                found = Call(call_id or f'#{row["seq"]}', payload.get('name', ''), None, turn.n, row['seq'])
                turn.calls.append(found)
                calls[found.call_id] = found
            found.output, found.status = output, 'error' if error else 'ok'
            entry.update(name=found.name, call_id=found.call_id, status=found.status, size=found.size)
        elif kind == 'usage':
            for key in TOKEN_KEYS:
                if isinstance(payload.get(key), int):
                    turn.usage[key] = turn.usage.get(key, 0) + payload[key]
            turn.responded = True
            entry['tokens'] = {key: payload[key] for key in TOKEN_KEYS if isinstance(payload.get(key), int)}
        elif kind == 'error':
            error = {'type': payload.get('type', ''), 'message': payload.get('message', '')}
            turn.errors.append(error)
            entry.update(error)
        elif kind == 'end':
            turn.end = payload.get('status')
            record.status, record.ended_at = turn.end, _time(row['created_at'])
            entry['status'] = turn.end
        record.events.append(entry)
    record.turns, record.calls, record.answer = turns, calls, answer
    if rows:
        record.agent_id, record.started_at = rows[0]['agent_id'], _time(rows[0]['created_at'])
    if not turns or turns[-1].end is None:  # no end yet, or a retry continued after one
        record.status, record.ended_at = None, None
    for call in calls.values():
        if call.child is not None:  # a nested run has no end of its own: its call's result tells
            call.child.status = {'ok': 'completed', 'error': 'failed'}.get(call.status, 'unfinished')
    return record


def build(
    run_key: str, rows: Iterable[dict], *, meta: dict | None = None, secrets: Iterable[str] = ()
) -> 'SessionRecord':
    """The record of `run_key` out of its event rows ({seq, kind, agent_id, payload, created_at[, user]}); meta: what
    the events do not hold ({model}). RunNotFound without rows."""
    secret_values = tuple(value for value in secrets if value)
    rows = sorted(
        ({**row, 'payload': _clean(row['payload'] or {}, secret_values)} for row in rows), key=lambda row: row['seq']
    )
    if not rows:
        raise RunNotFound(NOT_FOUND)
    groups: dict[str | None, list[dict]] = {}
    for row in rows:
        groups.setdefault(row['payload'].get('parent_call_id'), []).append(row)
    record = _build_group(groups.get(None, []), groups, None)
    linked = {call.call_id for call in record.all_calls() if call.child is not None}
    for parent, child_rows in groups.items():  # a nested run whose tool call is not in the record
        if parent is not None and parent not in linked:
            record.unlinked[parent] = _build_group(child_rows, groups, parent)
    record.run_key, record.user = run_key, rows[0].get('user')
    nested = [call.child for call in record.all_calls() if call.child is not None]
    for child in [*nested, *record.unlinked.values()]:
        child.run_key = run_key
    record.model = (meta or {}).get('model')
    record.full_chars = sum(len(json.dumps(row['payload'], ensure_ascii=False, default=str)) for row in rows)
    if record.status is None:
        record.status = 'unfinished'
    return record


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
        result = {**_shorten({k: v for k, v in result.items()}, limit), 'truncated': True, 'total_chars': full}
        limit //= 2
    return result


# --- the record -----------------------------------------------------------------------------------------------------


@dataclass
class SessionRecord:
    run_key: str
    parent_call_id: str | None = None
    kind: str = 'run'  # run | expert | subagent
    run_kind: str | None = None  # the start event's kind (chat_session, event_session, ...)
    title: str | None = None
    agent: str | None = None
    agent_id: str | None = None
    model: str | None = None
    user: str | None = None
    session_turn: int | None = None
    status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    instructions: str = ''
    answer: str = ''
    turns: list[Turn] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)  # timeline entries, tool outputs left out
    calls: dict[str, Call] = field(default_factory=dict)
    unlinked: dict[str, 'SessionRecord'] = field(default_factory=dict)
    full_chars: int = 0

    @classmethod
    def load(cls, conn, project, run_key: str, secrets: Iterable[str] = ()) -> 'SessionRecord':
        """The run's record in this workspace, read once (RunNotFound when it has none)."""
        rows = conn.execute(
            """SELECT e.seq,e.kind,e.agent_id,e.payload,e.created_at,p.email AS "user"
            FROM ka.agent_run_events e LEFT JOIN ka.principals p ON p.id=e.actor_id
            WHERE e.run_key=%s AND e.project_id=%s ORDER BY e.seq""",
            (run_key, project),
        ).fetchall()
        if not rows:
            raise RunNotFound(NOT_FOUND)
        model = conn.execute(
            """SELECT m.name FROM ka.agent_sessions s JOIN ka.llm_models m ON m.id=s.model_id
            WHERE s.project_id=%s AND s.id::text=split_part(%s, ':', 2) AND starts_with(%s, 'cc:')""",
            (project, run_key, run_key),
        ).fetchone()
        return build(run_key, rows, meta={'model': model['name'] if model else None}, secrets=secrets)

    # helpers

    def all_calls(self) -> list[Call]:
        """Every tool call of the record and of its nested runs, depth first."""
        found = []
        for call in self.calls.values():
            found.append(call)
            if call.child is not None:
                found += call.child.all_calls()
        for child in self.unlinked.values():
            found += child.all_calls()
        return found

    def _call(self, call_id: str) -> Call:
        for call in self.all_calls():
            if call.call_id == call_id:
                return call
        raise ValueError(f'call_id: no tool call {call_id} in this run.')

    def _turn(self, n: int) -> Turn:
        if not 1 <= n <= len(self.turns):
            raise ValueError(f'turn: there is no turn {n}; the run has {len(self.turns)} turns.')
        return self.turns[n - 1]

    def _nested(self) -> list['SessionRecord']:
        return [call.child for call in self.calls.values() if call.child is not None] + list(self.unlinked.values())

    def _tokens(self) -> int:
        return sum(turn.usage.get('total_tokens', 0) for turn in self.turns)

    def _nested_tokens(self) -> int:
        return sum(child._tokens() + child._nested_tokens() for child in self._nested())

    @staticmethod
    def _nested_run(call: Call) -> dict | None:
        return None if call.child is None else {'kind': call.child.kind, 'agent': call.child.agent}

    # methods

    def overview(self) -> dict:
        """The run's metadata, one line per turn and the totals."""
        calls = [call for turn in self.turns for call in turn.calls]
        result = {
            'run': {
                'run_key': self.run_key,
                'kind': self.kind,
                'run_kind': self.run_kind,
                'title': self.title,
                'agent': self.agent,
                'agent_id': self.agent_id,
                'model': self.model,
                'user': self.user,
                'session_turn': self.session_turn,
                'status': self.status,
                'started_at': self.started_at,
                'ended_at': self.ended_at,
                'instructions_chars': len(self.instructions),
            },
            'turns': [
                {
                    'turn': turn.n,
                    'input': _shorten(turn.inputs[0]['text'], PREVIEW) if turn.inputs else None,
                    'tools': [call.name for call in turn.calls],
                    'errors': len(turn.errors) + sum(call.status == 'error' for call in turn.calls),
                    'end': turn.end,
                    'tokens': turn.usage.get('total_tokens', 0),
                }
                for turn in self.turns
            ],
            'totals': {
                'turns': len(self.turns),
                'tool_calls': dict(Counter(call.name for call in calls)),
                'tool_errors': sum(call.status == 'error' for call in calls),
                'errors': sum(len(turn.errors) for turn in self.turns),
                'tokens': self._tokens(),
                'expert_calls': len(self._nested()),
                'expert_tokens': self._nested_tokens(),
            },
        }
        return _fit(result, 'turns')

    def _timeline(self, kinds=None, turn=None, tool=None) -> list[dict]:
        return [
            event
            for event in self.events
            if (kinds is None or event['kind'] in kinds)
            and (turn is None or event['turn'] == turn)
            and (tool is None or event.get('name') == tool)
        ]

    def timeline(self, kinds: list[str] | None = None, turn: int | None = None, tool: str | None = None) -> dict:
        """The record's events in order, filtered by kind, turn and tool name; tool outputs as their size only."""
        return _fit({'events': self._timeline(kinds, turn, tool)}, 'events')

    def tool_calls(self, turn: int | None = None, name: str | None = None, errors_only: bool = False) -> dict:
        found = [
            {
                'call_id': call.call_id,
                'turn': call.turn,
                'name': call.name,
                'arguments': call.arguments,
                'status': call.status,
                'output_size': call.size,
                'expert_call': call.child is not None and call.child.kind == 'expert',
                'nested_run': self._nested_run(call),
            }
            for item in self.turns
            for call in item.calls
            if (turn is None or call.turn == turn)
            and (name is None or call.name == name)
            and (not errors_only or call.status == 'error')
        ]
        return _fit({'tool_calls': found}, 'tool_calls')

    def tool_output(self, call_id: str, offset: int = 0, max_chars: int = OUTPUT_PAGE) -> dict:
        call = self._call(call_id)
        text = call.output[offset : offset + max_chars]
        end = offset + len(text)
        return {
            'call_id': call.call_id,
            'name': call.name,
            'status': call.status,
            'total_chars': call.size,
            'offset': offset,
            'text': text,
            'next_offset': end if end < call.size else None,
        }

    def turn(self, n: int) -> dict:
        item = self._turn(n)
        calls = []
        for call in item.calls:
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
                    'nested_run': self._nested_run(call),
                }
            )
        result = {
            'turn': item.n,
            'inputs': item.inputs,
            'reasoning': item.reasoning,
            'messages': item.messages,
            'tool_calls': calls,
            'errors': item.errors,
            'usage': item.usage,
            'end': item.end,
        }
        return _fit(result)

    def _texts(self, in_call: str | None = None):
        """(kind, turn, call_id, in_call, text) of every searchable text, nested runs after the record's own."""
        for item in self.turns:
            for value in item.inputs:
                yield 'input', item.n, None, in_call, value['text']
            for text in item.reasoning:
                yield 'reasoning', item.n, None, in_call, text
            for text in item.messages:
                yield 'message', item.n, None, in_call, text
            for call in item.calls:
                yield 'tool_call', item.n, call.call_id, in_call, _text(call.arguments)
                yield 'tool_result', item.n, call.call_id, in_call, call.output
            for error in item.errors:
                yield 'error', item.n, None, in_call, f'{error["type"]}: {error["message"]}'
        for child in self._nested():
            yield from child._texts(child.parent_call_id)

    def search(self, text: str, kinds: list[str] | None = None, max_hits: int = 20) -> dict:
        """Case-insensitive substring search; one hit per text, with how often it matched and a snippet around the
        first match."""
        needle = text.lower()
        hits = []
        for kind, turn, call_id, in_call, value in self._texts():
            if kinds is not None and kind not in kinds:
                continue
            lowered = value.lower()
            at = lowered.find(needle)
            if at < 0:
                continue
            start, end = max(0, at - SNIPPET), min(len(value), at + len(text) + SNIPPET)
            hit = {
                'kind': kind,
                'turn': turn,
                'count': lowered.count(needle),
                'snippet': ('…' if start else '') + value[start:end] + ('…' if end < len(value) else ''),
            }
            if call_id is not None:
                hit['call_id'] = call_id
            if in_call is not None:
                hit['in_call'] = in_call
            hits.append(hit)
        return _fit({'total_hits': len(hits), 'hits': hits[:max_hits]}, 'hits')

    def expert_call(self, call_id: str) -> dict:
        call = self._call(call_id)
        child = call.child or self.unlinked.get(call_id)
        if child is None:
            raise ValueError(f'call_id: {call_id} is not an expert call or subagent run.')
        return _fit(
            {
                'call_id': call.call_id,
                'name': call.name,
                'arguments': call.arguments,
                'status': call.status,
                'overview': {key: value for key, value in child.overview().items() if key != 'total_chars'},
                'timeline': child._timeline(),
            },
            'timeline',
        )

    def digest(self, budget_chars: int = DEFAULT_DIGEST_CHARS) -> dict:
        return digest(self, budget_chars)


# --- digest ---------------------------------------------------------------------------------------------------------

INSTRUCTIONS_SHARE = 4  # the instructions take at most a quarter of the budget
INPUT_CHARS, MESSAGE_CHARS, REASONING_CHARS, ARGUMENT_CHARS = 1_500, 1_500, 800, 300


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f' … [{len(text) - limit} more chars]'


def _call_line(call: Call) -> str:
    line = f'- {call.name}({_cut(_text(call.arguments), ARGUMENT_CHARS)}) -> {call.status}, {call.size} chars'
    if call.child is not None:
        child = call.child
        calls = sum(len(turn.calls) for turn in child.turns)
        line += (
            f' [{child.kind} {child.agent}: {len(child.turns)} turns, {calls} tool calls; expert_call({call.call_id})]'
        )
    return line


def _full_turn(turn: Turn) -> str:
    lines = [f'## Turn {turn.n}']
    lines += [f'{item["kind"]}: {_cut(item["text"], INPUT_CHARS)}' for item in turn.inputs]
    lines += [f'reasoning: {_cut(text, REASONING_CHARS)}' for text in turn.reasoning]
    lines += [f'message: {_cut(text, MESSAGE_CHARS)}' for text in turn.messages]
    lines += [_call_line(call) for call in turn.calls]
    lines += [f'error: {error["type"]}: {_cut(error["message"], MESSAGE_CHARS)}' for error in turn.errors]
    lines.append(f'tokens: {turn.usage.get("total_tokens", 0)}' + (f' · end: {turn.end}' if turn.end else ''))
    return '\n'.join(lines)


def _short_turn(turn: Turn) -> str:
    calls = ', '.join(f'{call.name} -> {call.status}' for call in turn.calls) or 'no tool calls'
    return f'Turn {turn.n}: {calls} · {len(turn.messages)} messages · {turn.usage.get("total_tokens", 0)} tokens'


def digest(record: SessionRecord, budget_chars: int = DEFAULT_DIGEST_CHARS) -> dict:
    """The run compressed to text (a model reads it as is; JSON would spend the budget on keys and quotes): the
    header, the instructions (at most a quarter of the budget), every turn (inputs, reasoning, messages, tool calls as
    name(arguments) -> ok/error, output size; outputs never), the final answer and the end. Over budget, the first turn,
    the turns with errors and as many last turns as fit stay in full; the others become one line each, and a note says
    how many were shortened. Still over: only the first and last stay in full; then the text is cut at the budget."""
    calls = sum(len(turn.calls) for turn in record.turns)
    header = (
        f'Run {record.run_key} · agent {record.agent or record.agent_id} · model {record.model or "unknown"} · '
        f'status {record.status} · {len(record.turns)} turns · {calls} tool calls · {record._tokens()} tokens'
    )
    instructions = _cut(record.instructions, max(500, budget_chars // INSTRUCTIONS_SHARE))
    footer = [f'Answer: {_cut(record.answer, MESSAGE_CHARS)}' if record.answer else 'Answer: (none)']
    footer.append(f'End: {record.status}')
    full = {turn.n: _full_turn(turn) for turn in record.turns}
    short = {turn.n: _short_turn(turn) for turn in record.turns}

    def render(kept: set[int]) -> str:
        shortened = len(record.turns) - len(kept & set(full))
        parts = [header, f'Instructions ({len(record.instructions)} chars):\n{instructions}']
        if shortened:
            parts.append(f'[{shortened} turns shortened; use turn(n) to read one in full]')
        parts += [full[n] if n in kept else short[n] for n in full]
        return '\n\n'.join(parts + ['\n'.join(footer)])

    numbers = list(full)
    kept = set(numbers)
    text = render(kept)
    if len(text) > budget_chars and numbers:
        first_last = {numbers[0], numbers[-1]}
        kept = first_last | {turn.n for turn in record.turns if turn.failed}
        if len(render(kept)) > budget_chars:
            kept = first_last
        for n in reversed(numbers):  # the last turns, as many as fit
            if n not in kept and len(render(kept | {n})) <= budget_chars:
                kept.add(n)
            elif n not in kept:
                break
        text = render(kept)
    truncated = len(text) > budget_chars
    if truncated:
        marker = '\n[cut at the budget]'
        text = text[: budget_chars - len(marker)] + marker
    return {
        'format': 'text',
        'text': text,
        'size_chars': len(text),
        'full_chars': record.full_chars,
        'shortened_turns': [n for n in numbers if n not in kept],
        'truncated': truncated,
    }


# --- the method table: tools and API --------------------------------------------------------------------------------


class _Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NoArguments(_Arguments):
    pass


class TimelineArguments(_Arguments):
    kinds: list[EventKind] | None = Field(None, description='Only these event kinds; null for all.')
    turn: int | None = Field(None, ge=1, description='Only this turn (1 is the first); null for all.')
    tool: str | None = Field(None, max_length=200, description='Only tool calls and results of this tool name.')


class ToolCallsArguments(_Arguments):
    turn: int | None = Field(None, ge=1, description='Only the calls of this turn; null for all.')
    name: str | None = Field(None, max_length=200, description='Only calls of this tool name; null for all.')
    errors_only: bool | None = Field(None, description='true: only the calls whose result was an error.')


class ToolOutputArguments(_Arguments):
    call_id: str = Field(min_length=1, max_length=200, description='The call id (from record_tool_calls or a turn).')
    offset: int | None = Field(None, ge=0, description='Where the page starts in the output; null for 0.')
    max_chars: int | None = Field(
        None, ge=100, le=MAX_OUTPUT_PAGE, description=f'The page size in characters; null for {OUTPUT_PAGE}.'
    )


class TurnArguments(_Arguments):
    turn: int = Field(ge=1, description='The turn number (1 is the first; record_overview lists them).')


class SearchArguments(_Arguments):
    text: str = Field(min_length=1, max_length=200, description='The text to find (case does not matter).')
    kinds: list[SearchKind] | None = Field(None, description='Only in these kinds of text; null for all.')
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


@dataclass(frozen=True)
class Method:
    name: str  # the API's method name
    tool: str  # the tool name a model calls
    description: str  # what a model reads: what it returns and when to use it
    arguments: type[BaseModel]
    run: Callable[[SessionRecord, Any], dict]

    def schema(self) -> dict:
        return ensure_strict_json_schema(self.arguments.model_json_schema())


CAPPED = f' Results are capped at {MAX_CHARS} characters: truncated is true and total_chars gives the full size.'
METHODS: dict[str, Method] = {
    method.name: method
    for method in (
        Method(
            'overview',
            'record_overview',
            'The run at a glance: its agent, model, status and times; one line per turn (input preview, tools '
            'called, errors, end, tokens); and totals (tool calls by name, errors, tokens, expert calls). Start '
            'here.' + CAPPED,
            NoArguments,
            lambda record, value: record.overview(),
        ),
        Method(
            'digest',
            'record_digest',
            'The whole run as compact text within a character budget: the instructions, then per turn the input, '
            'reasoning, messages and tool calls as name(arguments) -> ok/error with the output size (outputs are '
            'never included), the final answer and the end. Over budget, middle turns are shortened to one line; '
            'read those with record_turn. Use it to read the run once from start to end.',
            DigestArguments,
            lambda record, value: record.digest(value.budget_chars or DEFAULT_DIGEST_CHARS),
        ),
        Method(
            'timeline',
            'record_timeline',
            'The run events in order (input, instructions, reasoning, message, tool_call, tool_result, usage, '
            'error, end), each with its turn; tool results give their status and size, not the output. Filter by '
            'kinds, turn or tool name to follow one part of the run.' + CAPPED,
            TimelineArguments,
            lambda record, value: record.timeline(value.kinds, value.turn, value.tool),
        ),
        Method(
            'tool_calls',
            'record_tool_calls',
            'The tool calls of the run: call id, turn, tool name, arguments, status (ok, error, or pending without '
            'a result), output size and whether it was an expert call (open it with record_expert_call). Filter by '
            'turn, tool name or errors only.' + CAPPED,
            ToolCallsArguments,
            lambda record, value: record.tool_calls(value.turn, value.name, bool(value.errors_only)),
        ),
        Method(
            'tool_output',
            'record_tool_output',
            "One page of one tool call's output, also of a call made inside an expert call: the text from offset, "
            'the total size and next_offset (null at the end). Use it when the size or an error says the output '
            'matters; read further pages with next_offset.',
            ToolOutputArguments,
            lambda record, value: record.tool_output(value.call_id, value.offset or 0, value.max_chars or OUTPUT_PAGE),
        ),
        Method(
            'turn',
            'record_turn',
            'One turn in full: its inputs, reasoning, messages, tool calls with arguments and status (outputs as '
            'their size, with a short error text for a failed call), errors, usage and end. Use it for a turn the '
            'digest shortened or one with errors.' + CAPPED,
            TurnArguments,
            lambda record, value: record.turn(value.turn),
        ),
        Method(
            'search',
            'record_search',
            'Find text in the run (messages, inputs, reasoning, tool arguments, tool outputs and errors, expert '
            'calls included), case-insensitive: per hit its kind, turn, call id (in_call: the expert call it is '
            'inside) and a snippet around it. Use it to find where something was said or returned.' + CAPPED,
            SearchArguments,
            lambda record, value: record.search(value.text, value.kinds, value.max_hits or 20),
        ),
        Method(
            'expert_call',
            'record_expert_call',
            "One expert call (or subagent run) started by a tool call: the call, the nested run's overview and its "
            'timeline. Read its tool outputs with record_tool_output.' + CAPPED,
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
    """The methods as SDK function tools bound to `record` (no run_key argument: a model reads only this run). Not
    in the tool catalog: the run reviewer gives them to its own model."""
    from knowledge_admin.tools.sdk import _function_tool

    def bound(method: Method):
        async def run(context: dict, value: BaseModel) -> str:
            return json.dumps(method.run(record, value), ensure_ascii=False)

        return _function_tool(method.tool, method.description, method.arguments, run)

    return [bound(method) for method in METHODS.values()]
