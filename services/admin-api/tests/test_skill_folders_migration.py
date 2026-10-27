"""0139: skills become folders. Existing skills and the skill entries of stored access go, instructions gives way to
source and folder, the API analysis seed stops; down gives back the 0138 schema (no skills)."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from session_rows import a_model
from test_rag_gone_migration import ignoring_folder_order, schema

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0139_skill_folders.up.sql', '0139_skill_folders.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


@pytest.fixture
def at_0138(postgres):
    database = f'test_{uuid4().hex}'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}', row_factory=dict_row) as conn:
            for migration in sorted(MIGRATIONS.glob('*.up.sql')):
                if migration.name[:4] <= '0138':
                    conn.execute(migration.read_text(), prepare=False)
            yield schema(conn)
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


def columns(conn) -> set[str]:
    return {
        row['column_name']
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='agent_skills'"
        )
    }


def test_down_gives_back_the_0138_schema(store, at_0138):
    with store.connect() as conn:
        run(conn, '0144_no_document_folders.down.sql')
        run(conn, '0143_code_project_dependencies.down.sql')
        run(conn, '0142_test_is_chat.down.sql')
        run(conn, '0140_code_project_prompt.down.sql')
        run(conn, DOWN)
        assert ignoring_folder_order(schema(conn)) == ignoring_folder_order(at_0138)


def test_up_removes_the_skills_and_the_skill_access_entries(store):
    with store.connect() as conn:
        run(conn, DOWN)
        project = store.project(conn, 'shop')
        actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_skills').fetchone()['n'] == 1  # the seed is back
        skill = conn.execute(
            """INSERT INTO ka.agent_skills(project_id,name,description,instructions,created_by,updated_by)
            VALUES (%s,'inspect','Inspect','Look.',%s,%s) RETURNING id""",
            (project, actor, actor),
        ).fetchone()['id']
        given = [
            {'type': 'skill', 'id': str(skill)},
            {'type': 'service', 'id': str(uuid4())},
            {'type': 'skill', 'id': str(uuid4())},
        ]
        agent = f'agent-{uuid4().hex[:6]}'
        conn.execute(
            'INSERT INTO ka.agents(id,name,system_prompt,access) VALUES (%s,%s,%s,%s)',
            (agent, 'Agent', 'Be brief.', Jsonb({'resources': given, 'roles': {'content_editor': False}})),
        )
        session = uuid4()
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,agent_config,access) VALUES (%s,%s,%s,%s,%s,'Run','{}','{}','chat',%s,%s)""",
            (
                session,
                project,
                agent,
                actor,
                a_model(conn, actor),
                Jsonb({'name': 'Agent', 'access': {'resources': given}}),
                Jsonb({'resources': given, 'roles': {'content_editor': True}}),
            ),
        )
        run(conn, UP)

        kept = [given[1]]
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_skills').fetchone()['n'] == 0
        assert conn.execute('SELECT access FROM ka.agents WHERE id=%s', (agent,)).fetchone()['access'] == {
            'resources': kept,
            'roles': {'content_editor': False},
        }
        row = conn.execute('SELECT access,agent_config FROM ka.agent_sessions WHERE id=%s', (session,)).fetchone()
        assert row['access'] == {'resources': kept, 'roles': {'content_editor': True}}
        assert row['agent_config'] == {'name': 'Agent', 'access': {'resources': kept}}
        assert {'source', 'folder'} <= columns(conn) and 'instructions' not in columns(conn)
        store.project(conn, 'another')
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_skills').fetchone()['n'] == 0  # no seed
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                """INSERT INTO ka.agent_skills(project_id,name,description,source,folder,created_by,updated_by)
                VALUES (%s,'x','X','{"kind":"ftp"}','skills/a/b',%s,%s)""",
                (project, actor, actor),
            )


def test_up_down_up(store):
    with store.connect() as conn:
        run(conn, DOWN)
        run(conn, UP)
        assert 'instructions' not in columns(conn)
