"""A run started with an output schema: whether its final answer is the output (pure: verdict, the model capability,
the instructions fallback) and recording a valid output on the run with its run.output event (record)."""

import json
from dataclasses import dataclass, field
from typing import Any

from psycopg.types.json import Jsonb

from knowledge_admin.output_schemas.rules import validate

MAX_OUTPUT_BYTES = 256 * 1024
MAX_ERRORS = 20  # in the retry message and the failure record
OUTPUT_INVALID = 'Output did not match the schema'
TOO_LARGE = 'Output is larger than 256 KB.'
RETRY = 'Your final answer does not match the output schema: {errors}. Answer again with JSON only.'
FALLBACK = 'Finish with JSON only, matching this JSON Schema: {schema}'


@dataclass(frozen=True)
class Verdict:
    """valid: `output` is the answer's JSON; retry: ask again with `message`; failed: the run fails with `message`."""

    kind: str
    output: Any = None
    errors: list[str] = field(default_factory=list)
    message: str = ''


def verdict(schema: dict, raw: str, *, retried: bool) -> Verdict:
    """What a run does with its final answer `raw`: the output when it is JSON matching `schema`; otherwise one retry
    with the errors, then failure. An answer over 256 KB fails at once (a retry would not make it smaller)."""
    if len(raw.encode()) > MAX_OUTPUT_BYTES:
        return Verdict('failed', errors=[TOO_LARGE], message=TOO_LARGE)
    try:
        value = json.loads(raw)
    except ValueError as exc:
        errors = [f'$: the answer is not JSON ({getattr(exc, "msg", exc)})']
    else:
        errors = validate(schema, value)[:MAX_ERRORS]
        if not errors:
            return Verdict('valid', output=value)
    if retried:
        return Verdict('failed', errors=errors, message=OUTPUT_INVALID)
    return Verdict('retry', errors=errors, message=RETRY.format(errors='; '.join(errors)))


def supports_structured_output(model: dict) -> bool:
    """The model's Supports structured output capability; unset: on for OpenAI-style models."""
    capabilities = model.get('capabilities') or {}
    return bool(capabilities.get('supports_structured_output', model.get('provider') == 'openai'))


def fallback_instructions(schema: dict) -> str:
    """The block added to the instructions of a model without structured output."""
    return FALLBACK.format(schema=json.dumps(schema))


def record(conn, session: dict, config: dict, output: Any, turn: int) -> None:
    """Store a valid output on the run and emit run.output, in the caller's transaction (the turn's commit)."""
    pinned = config['output_schema']
    conn.execute('UPDATE ka.agent_sessions SET output=%s WHERE id=%s', (Jsonb(output), session['id']))
    agent_name = session.get('agent_name') or config['name']
    payload = {
        'run_id': str(session['id']),
        'agent_id': session['agent_id'],
        'agent_name': agent_name,
        'schema_id': pinned['id'],
        'schema_name': pinned['name'],
        'schema_version': pinned['version'],
        'output': output,
        'started_from': config.get('started_from'),
        'handler_id': None if session.get('handler_id') is None else str(session['handler_id']),
        'event_id': session.get('event_id'),
    }
    conn.execute(
        """INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,occurred_at)
        VALUES (%s,'run',%s,'run.output',%s,%s,%s,%s,now()) ON CONFLICT (project_id,dedupe_key) DO NOTHING""",
        (
            session['project_id'],
            session['id'],
            agent_name,
            f'{agent_name} · {pinned["name"]}',
            Jsonb(payload),
            f'run:{session["id"]}:{turn}:output',
        ),
    )
