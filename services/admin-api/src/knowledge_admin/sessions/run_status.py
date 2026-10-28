"""A run as an API client polls it: /status, /output and the answer of a start that waits.

The session statuses map to the client statuses as follows (`client_status`):
starting -> queued, working -> running, retrying (a failed turn waits to run again) -> running, waiting -> finished for a run with an output schema (its turn is done) and
waiting for any other run, stopped -> failed ("Stopped."), failed -> failed, closed -> finished, except a run with a
schema that closed without an output (failed). A run with a schema whose turn is done reports finished even when a
wake-up or a queued message may later add a turn (and replace its output).

Counted tool calls include the expert (subagent) calls made inside the run's turns."""

from uuid import UUID

NOT_FINISHED = 'The run has not finished.'
NO_SCHEMA = 'This run has no output schema.'
THINKING = 'thinking'

STATUSES = {'starting': 'queued', 'working': 'running', 'retrying': 'running', 'stopped': 'failed', 'failed': 'failed'}

# The tool call of the current turn that has no result yet: the step a running turn is in.
OPEN_CALL = """SELECT c.payload->>'name' AS name FROM ka.agent_run_events c
    WHERE c.run_key=%s AND c.kind='tool_call'
    AND NOT EXISTS (SELECT 1 FROM ka.agent_run_events r WHERE r.run_key=c.run_key AND r.kind='tool_result'
                    AND r.payload->>'call_id'=c.payload->>'call_id')
    ORDER BY c.seq DESC LIMIT 1"""
TOOL_CALLS = "SELECT count(*)::int AS n FROM ka.agent_run_events WHERE run_key=ANY(%s) AND kind='tool_call'"


def client_status(status: str, *, has_schema: bool, has_output: bool) -> str:
    if status == 'waiting':
        return 'finished' if has_schema else 'waiting'
    if status == 'closed':
        return 'failed' if has_schema and not has_output else 'finished'
    return STATUSES[status]


def failure(row: dict) -> str | None:
    """Why a failed run failed (the session's message; "Stopped." for a stopped one)."""
    if (
        client_status(
            row['status'], has_schema=row['output_schema_id'] is not None, has_output=row['output'] is not None
        )
        != 'failed'
    ):
        return None
    return row['status_message'] or ('Stopped.' if row['status'] == 'stopped' else 'The run failed.')


def run_row(conn, project: UUID, session_id: UUID) -> dict | None:
    return conn.execute(
        """SELECT id,status,status_message,turns,created_at,last_activity_at,output,output_error,output_schema_id,
        output_schema_version,agent_config->'output_schema'->>'name' AS schema_name
        FROM ka.agent_sessions WHERE id=%s AND project_id=%s""",
        (session_id, project),
    ).fetchone()


def status_of(row: dict) -> str:
    return client_status(
        row['status'], has_schema=row['output_schema_id'] is not None, has_output=row['output'] is not None
    )


def output_ready(row: dict) -> bool:
    """/output answers with the output: the run is finished, or failed after a turn that left one (a follow-up turn
    that crashed); not while a follow-up turn runs."""
    return row['output'] is not None and status_of(row) in ('finished', 'failed')


def view(conn, row: dict) -> dict:
    """The /status answer."""
    keys = [f'cc:{row["id"]}:{turn}' for turn in range(1, row['turns'] + 1)]
    step = None
    if row['status'] == 'working':  # `turns` counts the turn in progress
        current = conn.execute(OPEN_CALL, (f'cc:{row["id"]}:{row["turns"]}',)).fetchone()
        step = current['name'] if current else THINKING
    answer = {
        'id': row['id'],
        'status': status_of(row),
        'started_at': row['created_at'],
        'last_activity_at': row['last_activity_at'],
        'progress': {
            'turns': row['turns'],
            'tool_calls': conn.execute(TOOL_CALLS, (keys,)).fetchone()['n'] if keys else 0,
            'step': step,
        },
        'output_ready': output_ready(row),
    }
    error = failure(row)
    if error is not None:
        answer['error'] = error
    return answer


def result(row: dict) -> dict:
    """The answer of a start that waited and saw the run end: {id, status, output}, or the failure with its errors."""
    answer = {'id': row['id'], 'status': status_of(row), 'output': row['output']}
    error = failure(row)
    if error is not None:
        answer['error'] = error
        answer.update(row['output_error'] or {})
    return answer
