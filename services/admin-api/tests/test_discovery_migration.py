"""0097: discovery runs are agent sessions with a server; proposals; the seeded Server discovery prompt."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from session_rows import a_model
from old_documents import DOCUMENT_MODEL, before_0104, old_document  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0097_server_discovery.up.sql', '0097_server_discovery.down.sql'


LATER = ('0102_discovery_runs.down.sql', '0102_discovery_runs.up.sql')  # rewrites the prompt 0097 seeds


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


pytestmark = pytest.mark.usefixtures('before_0104')


def down_from_latest(conn):
    run(conn, LATER[0])
    run(conn, DOWN)


def up_to_latest(conn):
    run(conn, UP)
    run(conn, LATER[1])
    run(conn, DOCUMENT_MODEL[1])


def world(conn, store):
    project = store.project(conn, 'shop')
    server = conn.execute(
        "INSERT INTO ka.servers(project_id,name,hostname,username,enabled) VALUES (%s,'web-1','10.0.0.7','ops',false) RETURNING id",
        (project,),
    ).fetchone()['id']
    return project, server


def actor_and_model(conn):
    actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
    return actor, a_model(conn, actor)


def session(conn, project, server, purpose='discovery'):
    sid = uuid4()
    actor, model = actor_and_model(conn)
    conn.execute(
        """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,context_snapshot,purpose,server_id,agent_config)
        VALUES (%s,%s,NULL,%s,%s,'Discovery','{}','{}',%s,%s,%s)""",
        (sid, project, actor, model, purpose, server, Jsonb({})),
    )
    return sid


def test_every_workspace_gets_the_server_discovery_prompt(store):
    with store.connect() as conn:
        project = store.project(conn, f'shop-{uuid4().hex[:6]}')
        prompt = conn.execute(
            """SELECT p.tags,p.enabled,v.content FROM ka.documentation_prompts p
            JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
            WHERE p.project_id=%s AND p.name='Server discovery'""",
            (project,),
        ).fetchone()
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
            conn.execute(
                """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,context_snapshot,purpose,server_id,document_name,folder,agent_config)
                VALUES (%s,%s,NULL,%s,%s,'Docs','{}','{}','documents',%s,'Doc','docs',%s)""",
                (uuid4(), project, actor, model, server, Jsonb({})),
            )


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
            conn.execute(
                """UPDATE ka.discovery_proposals SET status='accepted',decided_at=now() WHERE session_id=%s""", (sid,)
            )
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
        down_from_latest(conn)
        assert conn.execute("SELECT to_regclass('ka.discovery_proposals') IS NULL AS gone").fetchone()['gone']
        assert (
            conn.execute("SELECT count(*) AS n FROM ka.documentation_prompts WHERE name='Server discovery'").fetchone()[
                'n'
            ]
            == 0
        )
        up_to_latest(conn)
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM ka.documentation_prompts WHERE project_id=%s AND name='Server discovery'",
                (project,),
            ).fetchone()['n']
            == 1
        )


def test_down_refuses_a_customized_discovery_prompt(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute(
            "UPDATE ka.documentation_prompts SET revision=revision+1 WHERE project_id=%s AND name='Server discovery'",
            (project,),
        )
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='customized'):
            run(conn, DOWN)
        conn.rollback()


def test_a_generated_version_needs_no_generation_run_and_down_refuses_while_one_exists(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor, _ = actor_and_model(conn)
        saved = old_document(conn, project, 'disks', 'Disks', '# Disks', origin='generated')
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='without a generation run'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute('DELETE FROM ka.documents WHERE id=%s', (saved['id'],))
        conn.commit()
        down_from_latest(conn)
        up_to_latest(conn)
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM ka.documentation_prompts WHERE project_id=%s AND name='Server discovery'",
                (project,),
            ).fetchone()['n']
            == 1
        )


DISCOVERY_DRAFT = ('0105_discovery_draft.down.sql', '0105_discovery_draft.up.sql')


def test_0105_records_the_draft_an_accepted_document_proposal_made_and_round_trips(store):
    with store.connect() as conn:
        run(conn, DOCUMENT_MODEL[1])  # this module starts with 0104 rolled back
        conn.commit()
        project, server = world(conn, store)
        sid = session(conn, project, server)
        draft = conn.execute(
            """INSERT INTO ka.documents(project_id,slug,folder,revision,is_draft,title,description,purpose,audience,
            tags,markdown,content_hash,origin) VALUES (%s,'disks','servers/web-1',1,true,'Disks','','explanation','both',
            '{}','# Disks',encode(sha256(convert_to('# Disks','UTF8')),'hex'),'generated') RETURNING id""",
            (project,),
        ).fetchone()['id']
        # A proposal may name a draft (the live-document trigger covers document_id only).
        proposal = conn.execute(
            """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload,status,decided_at,
            result_id,draft_id) VALUES (%s,%s,%s,1,'document',%s,'accepted',now(),%s,%s) RETURNING id""",
            (project, sid, server, Jsonb({'title': 'Disks'}), draft, draft),
        ).fetchone()['id']
        conn.execute('DELETE FROM ka.documents WHERE id=%s', (draft,))  # discarded: the proposal forgets it
        assert (
            conn.execute('SELECT draft_id FROM ka.discovery_proposals WHERE id=%s', (proposal,)).fetchone()['draft_id']
            is None
        )
        conn.commit()
        run(conn, DISCOVERY_DRAFT[0])
        column = "SELECT count(*) AS n FROM information_schema.columns WHERE table_name='discovery_proposals' AND column_name='draft_id'"
        assert conn.execute(column).fetchone()['n'] == 0
        run(conn, DISCOVERY_DRAFT[1])
        assert conn.execute(column).fetchone()['n'] == 1
