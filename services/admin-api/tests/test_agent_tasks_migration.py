"""0090: agent tasks (an agent call a handler queued for an event) and notifications (the answers, one list for all)."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from test_events_migration import an_event

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_task(conn, event, **values) -> dict:
    row = {
        'id': f'agent-task:{event["id"]}:{uuid4()}',
        'project_id': event['project_id'],
        'agent_id': 'triage',
        'agent_name': 'Triage',
        'title': 'Triage · jira.ticket.created DEMO-1',
        'prompt': 'Triage DEMO-1',
        'event_id': event['id'],
        'handler_id': uuid4(),
        **values,
    }
    return conn.execute(
        f'INSERT INTO ka.agent_tasks({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING *',
        tuple(row.values()),
    ).fetchone()


def a_notification(conn, event, **values) -> dict:
    row = {
        'project_id': event['project_id'],
        'kind': 'agent.response',
        'status': 'succeeded',
        'title': 'Triage · jira.ticket.created DEMO-1',
        'body': 'Looks like a timeout.',
        'event_id': event['id'],
        **values,
    }
    return conn.execute(
        f'INSERT INTO ka.notifications({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING *',
        tuple(row.values()),
    ).fetchone()


def test_a_new_task_is_queued_and_due_now(store):
    with store.connect() as conn:
        task = a_task(conn, an_event(conn))
        now = conn.execute('SELECT now() AS now').fetchone()['now']
    assert (task['status'], task['attempts'], task['lease_expires_at'], task['finished_at'], task['worker_id']) == (
        'queued',
        0,
        None,
        None,
        None,
    )
    assert task['next_attempt_at'] <= now


@pytest.mark.parametrize(
    'bad',
    [
        {'id': 'task-1'},
        {'prompt': ''},
        {'prompt': 'x' * 20001},
        {'title': ' '},
        {'status': 'done'},
        {'status': 'running'},  # running needs a lease
        {'status': 'succeeded'},  # finished needs finished_at
        {'attempts': -1},
        {'error': 'x' * 2001},
    ],
)
def test_task_checks(store, bad):
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        a_task(conn, an_event(conn), **bad)


@pytest.mark.parametrize('bad', [{'kind': 'mail'}, {'status': 'queued'}, {'title': ' '}, {'body': 'x' * 200001}])
def test_notification_checks(store, bad):
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        a_notification(conn, an_event(conn), **bad)


def test_a_running_and_a_finished_task_are_valid(store):
    with store.connect() as conn:
        event = an_event(conn)
        running = a_task(
            conn, event, status='running', attempts=1, worker_id='w1', lease_expires_at='2026-10-09T10:15:00Z'
        )
        done = a_task(conn, event, status='failed', attempts=3, finished_at='2026-10-09T10:20:00Z', error='boom')
    assert (running['status'], done['status']) == ('running', 'failed')


def test_deleting_the_event_keeps_task_and_notification(store):
    with store.connect() as conn:
        event = an_event(conn)
        task = a_task(conn, event)
        notification = a_notification(conn, event, agent_task_id=task['id'])
        conn.execute('UPDATE ka.agent_tasks SET notification_id=%s WHERE id=%s', (notification['id'], task['id']))
        conn.execute('DELETE FROM ka.events WHERE id=%s', (event['id'],))
        assert conn.execute(
            'SELECT event_id,notification_id FROM ka.agent_tasks WHERE id=%s', (task['id'],)
        ).fetchone() == {'event_id': None, 'notification_id': notification['id']}
        assert (
            conn.execute('SELECT event_id FROM ka.notifications WHERE id=%s', (notification['id'],)).fetchone()[
                'event_id'
            ]
            is None
        )


def test_handler_runs_can_point_at_a_task(store):
    with store.connect() as conn:
        event = an_event(conn)
        task = a_task(conn, event)
        conn.execute(
            """INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message,agent_task_id)
            VALUES (%s,%s,'Triage','succeeded','Queued for Triage.',%s)""",
            (event['id'], task['handler_id'], task['id']),
        )


def test_down_and_up(store):
    with store.connect() as conn:
        event = an_event(conn)
        a_notification(conn, event, agent_task_id=a_task(conn, event)['id'])
        run(conn, '0090_agent_tasks_notifications.down.sql')
        gone = conn.execute("""SELECT to_regclass('ka.agent_tasks') IS NULL AND to_regclass('ka.notifications') IS NULL
            AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='ka'
                AND table_name='event_handler_runs' AND column_name='agent_task_id') AS gone""").fetchone()['gone']
        assert gone
        run(conn, '0090_agent_tasks_notifications.up.sql')
        assert conn.execute("SELECT to_regclass('ka.agent_tasks_due') IS NOT NULL AS found").fetchone()['found']
