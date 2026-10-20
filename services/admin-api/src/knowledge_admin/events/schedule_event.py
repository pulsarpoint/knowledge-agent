"""The event.emit schedule target: each firing records one schedule.fired event, which the event processor routes to
handlers like any event (a handler runs a schedule through its type pattern and the condition payload.schedule_id
equals <id>)."""

from datetime import datetime

from psycopg.types.json import Jsonb

from knowledge_admin.repositories import RepositoryStore

TYPE = 'schedule.fired'


def payload(schedule: dict, last_success: datetime | None) -> dict:
    """fired_at is the slot this firing is for, the claimed next_run_at: a claim after an expired lease leaves it as it
    was (only finishing a run moves it on), and Run now sets its own. last_run_at is the previous successful run."""
    return {
        'schedule_id': str(schedule['id']),
        'name': schedule['name'],
        'fired_at': schedule['next_run_at'].isoformat(),
        'last_run_at': last_success and last_success.isoformat(),
        'trigger': 'manual' if schedule['run_requested'] else 'scheduled',
    }


def fire(store: RepositoryStore, schedule: dict) -> tuple[str, str]:
    """Record the event once per firing: a repeated call for the same firing finds the event it recorded before."""
    with store.connect() as conn:
        # this run is recorded only when it finishes, so the newest succeeded row is an earlier run
        previous = conn.execute(
            """SELECT started_at FROM ka.schedule_runs WHERE schedule_id=%s AND status='succeeded'
            ORDER BY id DESC LIMIT 1""",
            (schedule['id'],),
        ).fetchone()
        value = payload(schedule, previous and previous['started_at'])
        key = f'schedule:{schedule["id"]}:{value["fired_at"]}'
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
                schedule['next_run_at'],
            ),
        ).fetchone()
        if row is None:
            row = conn.execute(
                'SELECT id FROM ka.events WHERE project_id=%s AND dedupe_key=%s', (schedule['project_id'], key)
            ).fetchone()
    return 'succeeded', f'Recorded event {row["id"]}.'
