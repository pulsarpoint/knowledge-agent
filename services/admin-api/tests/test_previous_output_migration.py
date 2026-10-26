"""0138: a run records the handler and the schedule that started it (no foreign keys: a run outlives both), a handler
may include the previous output, and existing runs are backfilled from their handler runs and their event chain."""

from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from session_rows import a_model, add_chat_session
from test_output_schemas_migration import a_handler

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0138_previous_output.up.sql', '0138_previous_output.down.sql'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def an_event(conn, project, **values) -> dict:
    row = {
        'project_id': project,
        'source': 'manual',
        'type': 'a.b',
        'subject': 's',
        'title': 't',
        'payload': Jsonb({}),
        'dedupe_key': f'k{uuid4()}',
        'occurred_at': '2026-10-24T10:00:00Z',
        **values,
    }
    return conn.execute(
        f'INSERT INTO ka.events({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING *',
        tuple(row.values()),
    ).fetchone()


def an_agent(conn) -> str:
    conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES ('checker','Checker','Check.')")
    return 'checker'


def columns(conn, table: str) -> set[str]:
    return {
        row['column_name']
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name=%s", (table,)
        ).fetchall()
    }


def test_the_columns_have_no_foreign_keys_and_the_flag_defaults_off(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        handler = a_handler(conn, project)
        assert handler['include_previous_output'] is False
        actor = handler['created_by']
        session = add_chat_session(conn, project, actor, an_agent(conn), a_model(conn, actor), purpose='event')
        conn.execute(
            'UPDATE ka.agent_sessions SET handler_id=%s,schedule_id=%s WHERE id=%s', (handler['id'], uuid4(), session)
        )
        conn.execute('DELETE FROM ka.event_handlers WHERE id=%s', (handler['id'],))
        row = conn.execute('SELECT handler_id,schedule_id FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()
        assert row['handler_id'] == handler['id'] and row['schedule_id'] is not None
        index = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='ka' AND indexname='agent_sessions_previous_output'"
        ).fetchone()
    assert 'WHERE (output IS NOT NULL)' in index['indexdef']


def test_backfill_from_handler_runs_and_the_chain_root_and_down(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        handler = a_handler(conn, project, name='Check')
        actor = handler['created_by']
        model = a_model(conn, actor)
        agent = an_agent(conn)
        schedule = uuid4()
        fired = an_event(conn, project, source='schedule', source_id=schedule, type='schedule.fired')
        emitted = an_event(
            conn,
            project,
            source='handler',
            source_id=handler['id'],
            handler_id=handler['id'],
            parent_id=fired['id'],
            root_id=fired['root_id'],
            depth=1,
        )
        manual = an_event(conn, project)
        run(conn, DOWN)
        assert 'schedule_id' not in columns(conn, 'agent_sessions')
        assert 'include_previous_output' not in columns(conn, 'event_handlers')
        scheduled = add_chat_session(conn, project, actor, agent, model, purpose='event')
        by_hand = add_chat_session(conn, project, actor, agent, model, purpose='event')
        plain = add_chat_session(conn, project, actor, agent, model)
        for event, session in ((emitted, scheduled), (manual, by_hand)):
            conn.execute('UPDATE ka.agent_sessions SET event_id=%s WHERE id=%s', (event['id'], session))
            conn.execute(
                """INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,agent_session_id)
                VALUES (%s,%s,'Check','succeeded',%s)""",
                (event['id'], handler['id'], session),
            )
        gone_handler = add_chat_session(conn, project, actor, agent, model, purpose='event')
        deleted = uuid4()  # a handler deleted before 0138 (its runs lost handler_id; its handler run keeps it)
        conn.execute(
            """INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,agent_session_id)
            VALUES (%s,%s,'Gone','succeeded',%s)""",
            (manual['id'], deleted, gone_handler),
        )
        gone_event = add_chat_session(
            conn, project, actor, agent, model, purpose='event', config={'id': agent, 'event': {'root_id': fired['id']}}
        )  # its event was deleted (event_id NULL): the pinned root says which schedule
        run(conn, UP)
        rows = {
            row['id']: (row['handler_id'], row['schedule_id'])
            for row in conn.execute('SELECT id,handler_id,schedule_id FROM ka.agent_sessions').fetchall()
        }
    assert rows[scheduled] == (handler['id'], schedule)
    assert rows[by_hand] == (handler['id'], None)
    assert rows[plain] == (None, None)
    assert rows[gone_handler] == (deleted, None)
    assert rows[gone_event] == (None, schedule)
