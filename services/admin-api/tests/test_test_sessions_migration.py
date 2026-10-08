"""0081: Test chats become sessions run by agent-session-worker (ka.agent_sessions_cc.purpose = 'test'). The old Test
chat tables are dropped with their rows; their run records stay. Down deletes Test sessions and restores the old
tables empty."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from session_rows import a_model, add_review, add_test_session

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0081_test_sessions.up.sql', '0081_test_sessions.down.sql'
OLD = ('agent_sessions', 'operations_runs', 'operations_commands')
NEW_COLUMNS = {'purpose', 'model_calls', 'reasoning_tokens'}


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def exists(conn, table: str) -> bool:
    return conn.execute('SELECT to_regclass(%s) AS r', (f'ka.{table}',)).fetchone()['r'] is not None


def count(conn, table: str) -> int:
    return conn.execute(f'SELECT count(*) AS n FROM ka.{table}').fetchone()['n']


def columns(conn) -> set[str]:
    return {row['column_name'] for row in conn.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_schema='ka' AND table_name='agent_sessions_cc'""")}


def reviews_on_sessions_cc(conn) -> bool:
    return conn.execute("""SELECT confrelid = 'ka.agent_sessions_cc'::regclass AS cc FROM pg_constraint
        WHERE conname='agent_session_reviews_session_id_fkey'""").fetchone()['cc']


def documents_session(conn, project, actor, model):
    session = uuid4()
    conn.execute('''INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,document_name,folder) VALUES (%s,%s,'code',%s,%s,'Payments','{}','{}','Payments','payments')''',
        (session, project, actor, model))
    return session


def people(store, conn):
    project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
    return project, actor, a_model(conn, actor)


def test_purpose_checks_keep_documents_and_test_sessions_apart(store):
    with store.connect() as conn:
        project, actor, model = people(store, conn)
        documents = documents_session(conn, project, actor, model)
        tested = add_test_session(conn, project, actor, 'search', model)
        conn.commit()
        rows = {row['id']: row for row in conn.execute(
            'SELECT id,purpose,model_calls,reasoning_tokens,document_name,folder FROM ka.agent_sessions_cc')}
        assert (rows[documents]['purpose'], rows[documents]['model_calls'], rows[documents]['reasoning_tokens']) == (
            'documents', 0, 0)
        assert (rows[tested]['purpose'], rows[tested]['document_name'], rows[tested]['folder']) == ('test', None, None)
        for statement, session in (
                ('UPDATE ka.agent_sessions_cc SET folder=NULL WHERE id=%s', documents),
                ('UPDATE ka.agent_sessions_cc SET document_name=NULL WHERE id=%s', documents),
                ("UPDATE ka.agent_sessions_cc SET document_name='Payments',folder='payments' WHERE id=%s", tested),
                ("UPDATE ka.agent_sessions_cc SET draft='# Docs',draft_turn=1 WHERE id=%s", tested),
                ("UPDATE ka.agent_sessions_cc SET runtime='claude_code',agent_config=NULL WHERE id=%s", tested),
                ("UPDATE ka.agent_sessions_cc SET purpose='other' WHERE id=%s", tested),
                ('UPDATE ka.agent_sessions_cc SET model_calls=-1 WHERE id=%s', documents),
                ('UPDATE ka.agent_sessions_cc SET reasoning_tokens=-1 WHERE id=%s', documents)):
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(statement, (session,))
        review = add_review(conn, tested, actor, changes={}, turn=2)
        assert reviews_on_sessions_cc(conn)
        conn.execute('DELETE FROM ka.agent_sessions_cc WHERE id=%s', (tested,))
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_session_reviews WHERE id=%s', (review,)).fetchone()['n'] == 0


def test_up_drops_the_old_test_chats_and_keeps_their_run_records(store):
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        assert all(exists(conn, table) for table in (*OLD, 'agent_session_reviews'))
        assert not NEW_COLUMNS & columns(conn)
        project, actor, model = people(store, conn)
        documents = documents_session(conn, project, actor, model)
        chat, turn = uuid4(), uuid4()
        conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent,actor_id,title,config,is_test)
            VALUES (%s,%s,'search',%s,'Chat','{}',true)''', (chat, project, actor))
        conn.execute('''INSERT INTO ka.operations_runs(id,session_id,project_id,actor_id,agent,task,context,status)
            VALUES (%s,%s,%s,%s,'search','How many retries?','{}','completed')''', (turn, chat, project, actor))
        conn.execute('''INSERT INTO ka.operations_commands(run_id,server_name,hostname,username,command,as_root)
            VALUES (%s,'web','web.local','ops','df -h',false)''', (turn,))
        conn.execute('''INSERT INTO ka.agent_session_reviews(session_id,actor_id,comment,context,suggestion)
            VALUES (%s,%s,'Shorter','{}','Be brief.')''', (chat, actor))
        conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
            VALUES (%s,%s,1,'start','search',%s,%s)''', (project, f'run:{turn}', actor, Jsonb({'kind': 'test'})))
        conn.commit()
        run(conn, UP)
        conn.commit()
        assert not any(exists(conn, table) for table in OLD)
        assert reviews_on_sessions_cc(conn) and count(conn, 'agent_session_reviews') == 0
        assert NEW_COLUMNS <= columns(conn)
        assert conn.execute('''SELECT purpose,model_calls,reasoning_tokens,document_name,folder FROM ka.agent_sessions_cc
            WHERE id=%s''', (documents,)).fetchone() == {'purpose': 'documents', 'model_calls': 0, 'reasoning_tokens': 0,
                                                         'document_name': 'Payments', 'folder': 'payments'}
        assert [row['run_key'] for row in conn.execute('SELECT run_key FROM ka.agent_run_events')] == [f'run:{turn}']


def test_down_deletes_test_sessions_and_restores_the_old_tables_empty(store):
    with store.connect() as conn:
        project, actor, model = people(store, conn)
        documents = documents_session(conn, project, actor, model)
        tested = add_test_session(conn, project, actor, 'search', model, message='Hi', status='waiting', turns=1)
        add_review(conn, tested, actor, changes={})
        conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
            VALUES (%s,%s,1,'start','search',%s,'{}')''', (project, f'cc:{tested}:1', actor))
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        for table in (*OLD, 'agent_session_reviews'):
            assert exists(conn, table) and count(conn, table) == 0, table
        assert not reviews_on_sessions_cc(conn)  # the old reviews point at ka.agent_sessions again
        assert [row['id'] for row in conn.execute('SELECT id FROM ka.agent_sessions_cc')] == [documents]
        assert count(conn, 'agent_session_inputs') == 0  # the Test session's input went with it
        assert not NEW_COLUMNS & columns(conn)
        assert [row['is_nullable'] for row in conn.execute("""SELECT is_nullable FROM information_schema.columns
            WHERE table_schema='ka' AND table_name='agent_sessions_cc' AND column_name IN ('document_name','folder')""")] == [
            'NO', 'NO']
        assert [row['run_key'] for row in conn.execute('SELECT run_key FROM ka.agent_run_events')] == [f'cc:{tested}:1']
        run(conn, UP)
        conn.commit()
        assert not any(exists(conn, table) for table in OLD)
