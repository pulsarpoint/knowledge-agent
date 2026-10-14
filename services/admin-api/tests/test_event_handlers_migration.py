"""0089: events get a status and a place in a chain; handlers and their runs; agent sessions may have purpose event."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_events_migration import an_event

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def test_a_new_event_is_pending_and_its_own_root(store):
    with store.connect() as conn:
        event = an_event(conn)
    assert (event['status'], event['processed_at'], event['root_id'], event['depth'], event['parent_id']) == (
        'pending',
        None,
        event['id'],
        0,
        None,
    )


def test_existing_events_are_processed_by_the_migration(store):
    with store.connect() as conn:
        run(conn, '0103_event_runs.down.sql')  # 0103 references event_handlers
        run(conn, '0089_event_handlers.down.sql')
        old = an_event(conn)
        run(conn, '0089_event_handlers.up.sql')
        run(conn, '0103_event_runs.up.sql')
        row = conn.execute(
            'SELECT status,processed_at,created_at,root_id FROM ka.events WHERE id=%s', (old['id'],)
        ).fetchone()
    assert (row['status'], row['processed_at'], row['root_id']) == ('processed', row['created_at'], old['id'])


def test_checks(store):
    with store.connect() as conn:
        root = an_event(conn)
        child = an_event(
            conn,
            project=root['project_id'],
            source='handler',
            handler_id=uuid4(),
            parent_id=root['id'],
            root_id=root['id'],
            depth=1,
        )
        assert (child['root_id'], child['source']) == (root['id'], 'handler')
    project = root['project_id']
    for bad in [
        {'source': 'handler'},  # a handler event names its handler
        {'parent_id': root['id']},  # a Jira event has no parent
        {'handler_id': uuid4()},
        {'depth': 1},
        {'source': 'handler', 'handler_id': uuid4(), 'depth': 6},
        {'status': 'processed'},  # processed needs processed_at
        {'status': 'pending', 'processed_at': '2026-10-09T10:00:00Z'},
        {'status': 'done', 'processed_at': '2026-10-09T10:00:00Z'},
    ]:
        with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
            an_event(conn, project=project, **bad)


def test_handlers_and_runs(store):
    with store.connect() as conn:
        event = an_event(conn)
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        handler = conn.execute(
            """INSERT INTO ka.event_handlers(project_id,name,type_pattern,action,emit,created_by)
            VALUES (%s,'Triage','jira.ticket.*','emit',%s,%s) RETURNING *""",
            (event['project_id'], Jsonb({'type': 'triage.requested'}), actor),
        ).fetchone()
        assert (handler['enabled'], handler['conditions'], handler['revision']) == (True, [], 1)
        conn.execute(
            """INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message)
            VALUES (%s,%s,'Triage','succeeded','Emitted triage.requested.')""",
            (event['id'], handler['id']),
        )
    bad_handlers = [
        {'action': 'emit', 'emit': None},  # emit needs its templates
        {'action': 'agent', 'emit': None, 'agent_id': None, 'prompt': 'x'},  # agent needs an agent
        {'action': 'agent', 'emit': None, 'agent_id': 'a', 'prompt': None},  # and a prompt
        {'action': 'mail'},
        {'name': ' '},
        {'conditions': Jsonb({'field': 'x'})},
    ]
    for bad in bad_handlers:
        values = {
            'project_id': event['project_id'],
            'name': f'H{uuid4().hex[:6]}',
            'type_pattern': '*',
            'action': 'emit',
            'emit': Jsonb({'type': 'a.b'}),
            'created_by': actor,
            **bad,
        }
        with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
            conn.execute(
                f'INSERT INTO ka.event_handlers({",".join(values)}) VALUES ({",".join(["%s"] * len(values))})',
                tuple(values.values()),
            )
    with pytest.raises(psycopg.errors.UniqueViolation), store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.event_handlers(project_id,name,type_pattern,action,emit,created_by)
            VALUES (%s,'Triage','*','emit',%s,%s)""",
            (event['project_id'], Jsonb({'type': 'a.b'}), actor),
        )
    with store.connect() as conn:  # deleting the event deletes its runs
        conn.execute('DELETE FROM ka.events WHERE id=%s', (event['id'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.event_handler_runs').fetchone()['n'] == 0


def test_event_is_a_session_purpose(store):
    with store.connect() as conn:
        definition = conn.execute("""SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint
            WHERE conname='agent_sessions_purpose' AND conrelid='ka.agent_sessions'::regclass""").fetchone()['d']
    assert "'event'" in definition


def test_down_and_up(store):
    with store.connect() as conn:
        root = an_event(conn)
        an_event(
            conn,
            project=root['project_id'],
            source='handler',
            handler_id=uuid4(),
            parent_id=root['id'],
            root_id=root['id'],
            depth=1,
        )
        run(conn, '0103_event_runs.down.sql')  # 0103 references event_handlers
        run(conn, '0089_event_handlers.down.sql')
        assert conn.execute("SELECT to_regclass('ka.event_handlers') IS NULL AS gone").fetchone()['gone']
        assert conn.execute('SELECT count(*) AS n FROM ka.events').fetchone()['n'] == 1  # handler events are deleted
        run(conn, '0089_event_handlers.up.sql')
        run(conn, '0103_event_runs.up.sql')
        assert conn.execute("SELECT to_regclass('ka.events_pending') IS NOT NULL AS found").fetchone()['found']
