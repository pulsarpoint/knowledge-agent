"""Include the previous output: a handler run is keyed by its handler and the schedule at its event chain's root (none
for a chain that did not start with a schedule). With the handler's include_previous_output on, the last valid output
of the same key goes into the new run's task, after the handler's prompt, so the agent can compare."""

import json
from datetime import UTC
from uuid import UUID

MAX_BYTES = 16 * 1024
TRUNCATED = ' (truncated)'
COMPARE = 'Compare with it: say what is new, what changed and what is resolved.'

# Valid outputs only: a run whose answer did not match its schema has output null. Its time is last_activity_at (the
# last model call): updated_at moves later, e.g. when close_idle closes the run a day after it ended.
LATEST = """SELECT id,last_activity_at,output,output_schema_version,agent_config->'output_schema'->>'name' AS schema_name
    FROM ka.agent_sessions WHERE handler_id=%s AND {schedule} AND output IS NOT NULL
    ORDER BY created_at DESC LIMIT 1"""


def schedule_of(conn, event: dict) -> UUID | None:
    """The schedule whose schedule.fired event is the root of the event's chain."""
    row = conn.execute(
        "SELECT source_id FROM ka.events WHERE id=%s AND source='schedule'", (event['root_id'],)
    ).fetchone()
    return row and row['source_id']


def latest(conn, handler_id: UUID, schedule_id: UUID | None) -> dict | None:
    """The last run of the handler for the schedule (or for no schedule) that finished with a valid output."""
    if schedule_id is None:
        return conn.execute(LATEST.format(schedule='schedule_id IS NULL'), (handler_id,)).fetchone()
    return conn.execute(LATEST.format(schedule='schedule_id=%s'), (handler_id, schedule_id)).fetchone()


def block(row: dict) -> str:
    """The text added to the task: when and which run, its schema, the compact JSON (at most 16 KB)."""
    text = json.dumps(row['output'], separators=(',', ':'), ensure_ascii=False)
    data = text.encode()
    if len(data) > MAX_BYTES:
        text = data[:MAX_BYTES].decode(errors='ignore') + TRUNCATED
    finished = row['last_activity_at'].astimezone(UTC).strftime('%Y-%m-%d %H:%M')
    return (
        f'# Previous run\nFinished {finished} UTC (run {str(row["id"])[:8]}), output schema "{row["schema_name"]}" '
        f'v{row["output_schema_version"]}:\n{text}\n{COMPARE}'
    )
