"""0070: Claude Code agents are registrations; their sessions and queued messages have their own tables."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0070_claude_code_agents.up.sql', '0070_claude_code_agents.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def registration(conn, agent_id: str = 'writer', **columns):
    names = ['id', 'name', 'runtime', *columns]
    conn.execute(f"INSERT INTO ka.agents({','.join(names)}) VALUES ({','.join(['%s'] * len(names))})",
                 (agent_id, agent_id.title(), 'claude_code', *columns.values()))


def model(conn, actor):
    return conn.execute("""INSERT INTO ka.llm_models(name,provider,model,api_format,aws_region,api_key_ciphertext,created_by,updated_by)
        VALUES ('Claude','bedrock','eu.anthropic.claude-test','native','eu-central-1',%s,%s,%s) RETURNING id""",
        (b'\x00', actor, actor)).fetchone()['id']


def test_claude_code_agents_are_registrations_only(store):
    with store.connect() as conn:
        registration(conn)  # no system prompt row: the prompt FK applies to OpenAI agents only
        conn.commit()
        assert conn.execute("SELECT runtime,prompt_agent FROM ka.agents WHERE id='writer'").fetchone() == {
            'runtime': 'claude_code', 'prompt_agent': None}
        assert conn.execute("SELECT runtime FROM ka.agents WHERE id='search'").fetchone()['runtime'] == 'openai'
        for column in ('ssh_enabled', 'documents_enabled', 'documents_grounded', 'repositories_enabled', 'skills_enabled',
                       'mcp_enabled', 'services_enabled', 'all_collections', 'all_repositories', 'is_coordinator'):
            with pytest.raises(psycopg.errors.CheckViolation, match='claude_code_agent_is_a_registration'):
                with conn.transaction():
                    registration(conn, 'other', **{column: True})
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        with pytest.raises(psycopg.errors.CheckViolation, match='claude_code_agent_is_a_registration'):
            with conn.transaction():
                conn.execute("UPDATE ka.agents SET model_id=%s WHERE id='writer'", (model(conn, actor),))
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("UPDATE ka.agents SET runtime='other' WHERE id='writer'")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):  # OpenAI agents still need a system prompt
        with store.connect() as conn:
            conn.execute("INSERT INTO ka.agents(id,name) VALUES ('missing-prompt','Missing prompt')")


def test_sessions_hold_status_folder_and_ordered_inputs(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        registration(conn)
        session = uuid4()
        conn.execute("""INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,document_name,folder) VALUES (%s,%s,'writer',%s,%s,'Payments','{}','{}','Payments','payments-docs')""",
            (session, project, actor, model(conn, actor)))
        assert conn.execute('SELECT status,turns,draft,input_tokens FROM ka.agent_sessions_cc WHERE id=%s', (session,)).fetchone() == {
            'status': 'starting', 'turns': 0, 'draft': None, 'input_tokens': 0}
        conn.execute("INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,'Document it')", (session, actor))
        for statement, error in (
                ("INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%(s)s,1,%(a)s,'Again')", psycopg.errors.UniqueViolation),
                ("UPDATE ka.agent_session_inputs SET status='delivered' WHERE session_id=%(s)s", psycopg.errors.CheckViolation),
                ("UPDATE ka.agent_session_inputs SET status='later' WHERE session_id=%(s)s", psycopg.errors.CheckViolation),
                ("UPDATE ka.agent_sessions_cc SET status='closed' WHERE id=%(s)s", psycopg.errors.CheckViolation),
                ("UPDATE ka.agent_sessions_cc SET status='busy' WHERE id=%(s)s", psycopg.errors.CheckViolation),
                ("UPDATE ka.agent_sessions_cc SET folder='Payments' WHERE id=%(s)s", psycopg.errors.CheckViolation),
                ("UPDATE ka.agent_sessions_cc SET folder='services/x' WHERE id=%(s)s", psycopg.errors.CheckViolation)):
            with pytest.raises(error):
                with conn.transaction():
                    conn.execute(statement, {'s': session, 'a': actor})
        conn.execute("UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s", (session,))
        conn.execute("UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now() WHERE id=%s", (session,))


def test_down_refuses_claude_code_agents_and_restores_the_prompt_rule(store):
    with store.connect() as conn:
        registration(conn)
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Claude Code agents exist'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("DELETE FROM ka.agents WHERE id='writer'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        assert conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
            AND column_name='runtime'""").fetchone() is None
        assert conn.execute("SELECT to_regclass('ka.agent_sessions_cc') AS t").fetchone()['t'] is None
        run(conn, UP)
        conn.commit()
