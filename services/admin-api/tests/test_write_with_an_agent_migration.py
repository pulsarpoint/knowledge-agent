"""0145: Write with an agent names no document up front: agent_sessions.document_name goes (a documents run needs
none), the drafts of a conversation are found by their creation audit event (an index), and the seeded Write a document
prompt gets a new version (new_document, mentions) only where nobody changed it; down gives back the 0144 schema."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from session_rows import a_model
from test_proposals_gone_migration import edit, prompt, shape
from test_rag_gone_migration import schema

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0145_write_with_an_agent.up.sql', '0145_write_with_an_agent.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def ignoring_document_name_order(described: dict) -> dict:
    """Down adds document_name back at the end of agent_sessions (PostgreSQL cannot put a column back in its place)."""
    columns = sorted((row[0], None, *row[2:]) if row[0] == 'agent_sessions' else row for row in described['columns'])
    return {**described, 'columns': columns}


@pytest.fixture
def at_0144(postgres):
    database = f'test_{uuid4().hex}'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}', row_factory=dict_row) as conn:
            for migration in sorted(MIGRATIONS.glob('*.up.sql')):
                if migration.name[:4] <= '0144':
                    conn.execute(migration.read_text(), prepare=False)
            yield schema(conn)
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


def columns(conn) -> set:
    return {
        row['column_name']
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='agent_sessions'"
        )
    }


def documents_run(conn, project, **extra):
    actor = conn.execute(
        "INSERT INTO ka.principals(issuer,subject) VALUES ('test',%s) RETURNING id", (uuid4().hex,)
    ).fetchone()['id']
    names = ''.join(f',{key}' for key in extra)
    values = ''.join(',%s' for _ in extra)
    return conn.execute(
        f"""INSERT INTO ka.agent_sessions(id,project_id,actor_id,model_id,title,purpose,prompt_snapshot,
        context_snapshot,agent_config{names}) VALUES (gen_random_uuid(),%s,%s,%s,'Write docs','documents','{{}}','{{}}',
        '{{}}'{values}) RETURNING id""",
        (project, actor, a_model(conn, actor), *extra.values()),
    ).fetchone()['id']


def test_down_gives_back_the_0144_schema_and_up_again(store, at_0144):
    with store.connect() as conn:
        assert 'document_name' not in columns(conn)
        project = store.project(conn, 'shop')
        written = documents_run(conn, project)  # a documents run names no document
        conn.commit()
        run(conn, '0146_run_retries.down.sql')  # newer migrations first
        run(conn, DOWN)
        assert ignoring_document_name_order(schema(conn)) == ignoring_document_name_order(at_0144)
        name = conn.execute('SELECT document_name FROM ka.agent_sessions WHERE id=%s', (written,)).fetchone()
        assert name['document_name'] == 'Write docs'  # an existing run gets its title
        conn.commit()
        run(conn, UP)
        assert 'document_name' not in columns(conn)


def test_up_rewrites_the_write_prompt_only_where_unchanged_and_down_restores_it(store):
    name = 'Write a document'
    with store.connect() as conn:
        fresh = store.project(conn, f'shop-{uuid4().hex[:6]}')
        content = prompt(conn, fresh, name)['content']
        assert 'new_document' in content and '[Draft "<title>" · <id>]' in content
        conn.commit()
        run(conn, DOWN)
        untouched, edited = (store.project(conn, f'shop-{uuid4().hex[:6]}') for _ in range(2))
        before = prompt(conn, untouched, name)
        assert 'new_document' not in before['content']
        edit(conn, edited, name)
        edited_before = shape(prompt(conn, edited, name))
        conn.commit()
        run(conn, UP)
        after = prompt(conn, untouched, name)
        assert (after['version'], after['versions'], after['revision']) == (2, 2, before['revision'] + 1)
        assert after['content'] == content and after['description'].startswith('Write new technical documents')
        assert shape(prompt(conn, edited, name)) == edited_before
        conn.commit()
        run(conn, DOWN)
        keys = ('description', 'version', 'versions', 'next_version', 'content')
        restored = prompt(conn, untouched, name)
        assert {k: restored[k] for k in keys} == {k: before[k] for k in keys}
        assert shape(prompt(conn, edited, name)) == edited_before
        conn.commit()
        run(conn, UP)
