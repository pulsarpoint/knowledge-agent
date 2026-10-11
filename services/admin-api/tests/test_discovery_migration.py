"""0097: discovery runs are agent sessions with a server; proposals; the seeded Server discovery prompt."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from session_rows import a_model

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0097_server_discovery.up.sql', '0097_server_discovery.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def world(conn, store):
    project = store.project(conn, 'shop')
    server = conn.execute("INSERT INTO ka.servers(project_id,name,hostname,username,enabled) VALUES (%s,'web-1','10.0.0.7','ops',false) RETURNING id",
                          (project,)).fetchone()['id']
    return project, server


def actor_and_model(conn):
    actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
    return actor, a_model(conn, actor)


def session(conn, project, server, purpose='discovery'):
    sid = uuid4()
    actor, model = actor_and_model(conn)
    conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,context_snapshot,purpose,server_id,agent_config)
        VALUES (%s,%s,NULL,%s,%s,'Discovery','{}','{}',%s,%s,%s)""", (sid, project, actor, model, purpose, server, Jsonb({})))
    return sid


def test_every_workspace_gets_the_server_discovery_prompt(store):
    with store.connect() as conn:
        project = store.project(conn, f'shop-{uuid4().hex[:6]}')
        prompt = conn.execute("""SELECT p.tags,p.enabled,v.content FROM ka.documentation_prompts p
            JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
            WHERE p.project_id=%s AND p.name='Server discovery'""", (project,)).fetchone()
    assert prompt['tags'] == ['server-discovery', 'ssh'] and prompt['enabled']
    assert 'get_server_info_basic' in prompt['content'] and 'read-only' in prompt['content']


def test_a_discovery_session_needs_its_server_and_other_sessions_have_none(store):
    with store.connect() as conn:
        project, server = world(conn, store)
        session(conn, project, server)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            session(conn, project, None)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            actor, model = actor_and_model(conn)
            conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,context_snapshot,purpose,server_id,document_name,folder,agent_config)
                VALUES (%s,%s,NULL,%s,%s,'Docs','{}','{}','documents',%s,'Doc','docs',%s)""", (uuid4(), project, actor, model, server, Jsonb({})))


def test_proposals_are_numbered_per_run_and_go_with_the_run(store):
    with store.connect() as conn:
        project, server = world(conn, store)
        sid = session(conn, project, server)
        add = """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING id"""
        conn.execute(add, (project, sid, server, 1, 'service', Jsonb({'name': 'nginx'})))
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(add, (project, sid, server, 1, 'document', Jsonb({'title': 'x'})))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("""UPDATE ka.discovery_proposals SET status='accepted',decided_at=now() WHERE session_id=%s""", (sid,))
        conn.execute('DELETE FROM ka.agent_sessions WHERE id=%s', (sid,))
        assert conn.execute('SELECT count(*) AS n FROM ka.discovery_proposals').fetchone()['n'] == 0


def test_down_refuses_while_discovery_runs_exist_then_round_trips(store):
    with store.connect() as conn:
        project, server = world(conn, store)
        sid = session(conn, project, server)
        conn.commit()  # the migration files carry their own BEGIN/COMMIT
        with pytest.raises(psycopg.errors.RaiseException, match='Discovery runs exist'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute('DELETE FROM ka.agent_sessions WHERE id=%s', (sid,))
        conn.commit()
        run(conn, DOWN)
        assert conn.execute("SELECT to_regclass('ka.discovery_proposals') IS NULL AS gone").fetchone()['gone']
        assert conn.execute("SELECT count(*) AS n FROM ka.documentation_prompts WHERE name='Server discovery'").fetchone()['n'] == 0
        run(conn, UP)
        assert conn.execute("SELECT count(*) AS n FROM ka.documentation_prompts WHERE project_id=%s AND name='Server discovery'",
                            (project,)).fetchone()['n'] == 1


def test_down_refuses_a_customized_discovery_prompt(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("UPDATE ka.documentation_prompts SET revision=revision+1 WHERE project_id=%s AND name='Server discovery'", (project,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='customized'):
            run(conn, DOWN)
        conn.rollback()
