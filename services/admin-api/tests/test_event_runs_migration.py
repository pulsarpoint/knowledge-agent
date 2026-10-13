"""0103: agent tasks still queued or running are failed — event handlers start agent runs now — each with a failed
notification; finished tasks stay as they were. The down migration leaves them failed."""
from pathlib import Path

from test_agent_tasks_migration import a_notification, a_task
from test_events_migration import an_event

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0103_event_runs.up.sql', '0103_event_runs.down.sql'
NOT_RUN = 'Not run: event handlers start agent runs now. Send the event again.'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def task(conn, task_id) -> dict:
    return conn.execute('SELECT * FROM ka.agent_tasks WHERE id=%s', (task_id,)).fetchone()


def test_open_tasks_fail_with_a_notification_each_and_finished_ones_stay(store):
    with store.connect() as conn:
        event = an_event(conn)
        queued = a_task(conn, event, title='Triage · queued')
        running = a_task(conn, event, title='Triage · running', status='running', attempts=1, worker_id='w1',
                         lease_expires_at='2026-10-09T10:15:00Z')
        done = a_task(conn, event, status='succeeded', attempts=1, finished_at='2026-10-09T10:20:00Z')
        old = a_notification(conn, event, agent_task_id=done['id'])
        conn.execute('UPDATE ka.agent_tasks SET notification_id=%s WHERE id=%s', (old['id'], done['id']))
        done = task(conn, done['id'])
        run(conn, DOWN)
        run(conn, UP)
        for before in (queued, running):
            after = task(conn, before['id'])
            assert (after['status'], after['error'], after['worker_id'], after['lease_expires_at']) == (
                'failed', NOT_RUN, None, None)
            assert after['finished_at'] is not None and after['notification_id'] is not None
            note = conn.execute('SELECT * FROM ka.notifications WHERE id=%s', (after['notification_id'],)).fetchone()
            assert (note['project_id'], note['kind'], note['status'], note['title'], note['body'], note['event_id'],
                    note['handler_id'], note['agent_id'], note['agent_task_id'], note['session_id']) == (
                event['project_id'], 'agent.response', 'failed', before['title'], NOT_RUN, event['id'],
                before['handler_id'], 'triage', before['id'], None)
        assert task(conn, done['id']) == done
        assert conn.execute('SELECT count(*) AS n FROM ka.notifications').fetchone()['n'] == 3
        run(conn, DOWN)  # nothing to undo: the tasks stay failed
        assert task(conn, queued['id'])['status'] == 'failed'
        run(conn, UP)
        assert conn.execute('SELECT count(*) AS n FROM ka.notifications').fetchone()['n'] == 3
