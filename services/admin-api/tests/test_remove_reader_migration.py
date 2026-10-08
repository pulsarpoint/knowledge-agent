"""Migration 0071 removes the Reader: its conversations and the schema only it used; grounding stays."""
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def sql(name):
    return (MIGRATIONS / name).read_text()


def column(conn, table, name):
    return conn.execute("""SELECT 1 FROM information_schema.columns
        WHERE table_schema='ka' AND table_name=%s AND column_name=%s""", (table, name)).fetchone() is not None


def test_reader_schema_is_gone_and_grounding_stays(store):
    with store.connect() as conn:
        conn.execute(sql('0081_test_sessions.down.sql'), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.reader_settings') AS t").fetchone()['t'] is None
        assert not column(conn, 'agent_sessions', 'channel') and not column(conn, 'agent_sessions', 'hidden_by_owner_at')
        assert column(conn, 'agent_sessions', 'idle_timeout')
        assert [row['id'] for row in conn.execute('SELECT id FROM ka.agents WHERE documents_grounded ORDER BY id')] == ['search']


def test_up_deletes_reader_conversations_and_keeps_admin_sessions(store):
    with store.connect() as conn:
        conn.execute(sql('0081_test_sessions.down.sql'), prepare=False)
        conn.execute(sql('0071_remove_reader.down.sql'), prepare=False)
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'alice', 'alice@test')
        sessions = {}
        for channel in ('admin', 'reader'):
            sessions[channel] = conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test,channel)
                VALUES (%s,%s,'search',%s,'Q',%s,false,%s) RETURNING id''', (uuid4(), project, actor, Jsonb({}), channel)).fetchone()['id']
            run = conn.execute('''INSERT INTO ka.operations_runs(id,project_id,agent,actor_id,session_id,task,status,context)
                VALUES (%s,%s,'search',%s,%s,'Q','completed',%s) RETURNING id''',
                (uuid4(), project, actor, sessions[channel], Jsonb({}))).fetchone()['id']
            conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                VALUES (%s,%s,1,'start','search',%s,%s)''', (project, f'run:{run}', actor, Jsonb({'kind': channel})))
        conn.execute(sql('0071_remove_reader.up.sql'), prepare=False)
        assert [row['id'] for row in conn.execute('SELECT id FROM ka.agent_sessions')] == [sessions['admin']]
        assert conn.execute('SELECT count(*) AS n FROM ka.operations_runs').fetchone()['n'] == 1
        assert [row['payload']['kind'] for row in conn.execute('SELECT payload FROM ka.agent_run_events')] == ['admin']


def test_down_restores_the_reader_schema(store):
    with store.connect() as conn:
        conn.execute(sql('0081_test_sessions.down.sql'), prepare=False)
        conn.execute(sql('0071_remove_reader.down.sql'), prepare=False)
        assert conn.execute('SELECT knowledge_agent_id FROM ka.reader_settings').fetchone()['knowledge_agent_id'] == 'search'
        assert column(conn, 'agent_sessions', 'channel')
