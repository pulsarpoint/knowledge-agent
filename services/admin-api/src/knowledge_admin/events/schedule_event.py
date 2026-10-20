"""The event.emit schedule target: each firing records one schedule.fired event, which the event processor routes to
handlers like any event (a handler runs a schedule through its type pattern and the condition payload.schedule_id
equals <id>)."""

from psycopg.types.json import Jsonb

from knowledge_admin.repositories import RepositoryStore

TYPE = 'schedule.fired'


def payload(schedule: dict) -> dict:
    """fired_at is this firing (the claim stamped last_run_at); last_run_at is the previous successful firing, read from
    the claimed row, which still holds the previous run's result."""
    previous = schedule['last_finished_at'] if schedule['last_status'] == 'succeeded' else None
    return {
        'schedule_id': str(schedule['id']),
        'name': schedule['name'],
        'fired_at': schedule['last_run_at'].isoformat(),
        'last_run_at': previous and previous.isoformat(),
        'trigger': 'manual' if schedule['run_requested'] else 'scheduled',
    }


def fire(store: RepositoryStore, schedule: dict) -> tuple[str, str]:
    """Record the event once per firing: a repeated call for the same firing finds the event it recorded before."""
    value = payload(schedule)
    key = f'schedule:{schedule["id"]}:{value["fired_at"]}'
    with store.connect() as conn:
        row = conn.execute(
            """INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,occurred_at)
            VALUES (%s,'schedule',%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id,dedupe_key) DO NOTHING RETURNING id""",
            (
                schedule['project_id'],
                schedule['id'],
                TYPE,
                schedule['name'],
                schedule['name'],
                Jsonb(value),
                key,
                schedule['last_run_at'],
            ),
        ).fetchone()
        if row is None:
            row = conn.execute(
                'SELECT id FROM ka.events WHERE project_id=%s AND dedupe_key=%s', (schedule['project_id'], key)
            ).fetchone()
    return 'succeeded', f'Recorded event {row["id"]}.'
