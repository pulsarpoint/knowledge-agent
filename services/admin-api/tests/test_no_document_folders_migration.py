"""0144: documents lose their folder (a leftover of RAG): a document is named by its slug in the project, slugs that
would collide are renamed (the oldest keeps its slug), and a new-document run has no folder either; down gives back
the 0143 schema (the folder columns come back at the end of their tables, empty)."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from session_rows import a_model
from test_rag_gone_migration import ignoring_folder_order, schema

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0144_no_document_folders.up.sql', '0144_no_document_folders.down.sql'
LATER = '0145_write_with_an_agent.down.sql'  # drops agent_sessions.document_name, which 0144 down checks


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


@pytest.fixture
def at_0143(postgres):
    database = f'test_{uuid4().hex}'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}', row_factory=dict_row) as conn:
            for migration in sorted(MIGRATIONS.glob('*.up.sql')):
                if migration.name[:4] <= '0143':
                    conn.execute(migration.read_text(), prepare=False)
            yield schema(conn)
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


def document(conn, project, slug, folder=None, *, draft=False, age=0, draft_of=None):
    """A live document (or a brand-new draft, or with draft_of a draft of one) created `age` days ago."""
    return conn.execute(
        """INSERT INTO ka.documents(project_id,slug,folder,is_draft,draft_of,version,version_created_at,title,
        description,purpose,audience,markdown,content_hash,origin,created_at)
        VALUES (%(p)s,%(slug)s,%(folder)s,%(draft)s,%(of)s,CASE WHEN %(draft)s THEN NULL ELSE 1 END,
        CASE WHEN %(draft)s THEN NULL ELSE now() END,'T','','explanation','both','# T',
        encode(sha256(convert_to('# T','UTF8')),'hex'),'manual',now() - make_interval(days => %(age)s))
        RETURNING id""",
        {
            'p': project,
            'slug': slug,
            'folder': folder,
            'draft': draft or draft_of is not None,
            'of': draft_of,
            'age': age,
        },
    ).fetchone()['id']


def slugs(conn) -> dict:
    return {row['id']: row['slug'] for row in conn.execute('SELECT id,slug FROM ka.documents')}


def test_down_gives_back_the_0143_schema_and_up_again(store, at_0143):
    with store.connect() as conn:
        run(conn, '0147_session_review.down.sql')  # newer migrations first
        run(conn, '0146_run_retries.down.sql')
        run(conn, LATER)
        run(conn, DOWN)
        assert ignoring_folder_order(schema(conn)) == ignoring_folder_order(at_0143)
        run(conn, UP)
        names = {
            row['column_name']
            for row in conn.execute(
                """SELECT column_name FROM information_schema.columns WHERE table_schema='ka'
                AND table_name IN ('documents','live_documents','document_details','document_drafts_view',
                'agent_sessions')"""
            )
        }
        assert 'folder' not in names


def test_up_renames_colliding_slugs_keeping_live_documents_then_the_oldest(store):
    with store.connect() as conn:
        run(conn, LATER)
        run(conn, DOWN)
        shop, other = store.project(conn, 'shop'), store.project(conn, 'other')
        oldest = document(conn, shop, 'runbook', 'ops', age=5)
        middle = document(conn, shop, 'runbook', None, age=3)
        newest = document(conn, shop, 'runbook', 'team/ops', draft=True, age=1)
        taken = document(conn, shop, 'runbook-2', 'misc', age=4)  # the first suffix is already a slug
        revision = document(conn, shop, None, draft_of=oldest)
        elsewhere = document(conn, other, 'runbook', 'ops', age=0)
        long = 'a' * 120
        long_old = document(conn, shop, long, 'x', age=2)
        long_new = document(conn, shop, long, 'y', age=1)
        old_draft = document(conn, shop, 'guide', 'drafts', draft=True, age=9)  # older, but only a brand-new draft
        live_guide = document(conn, shop, 'guide', 'ops', age=1)
        run(conn, UP)
        after = slugs(conn)
        assert after[oldest] == 'runbook' and after[taken] == 'runbook-2'
        assert after[middle] == 'runbook-3' and after[newest] == 'runbook-4'
        assert after[revision] is None and after[elsewhere] == 'runbook'
        assert after[long_old] == long and after[long_new] == 'a' * 118 + '-2'
        assert (after[live_guide], after[old_draft]) == ('guide', 'guide-2')  # a live document keeps its name
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute("UPDATE ka.documents SET slug='runbook' WHERE id=%s", (newest,))
        document_id = conn.execute(
            "SELECT id FROM ka.document_details WHERE project_id=%s AND slug='runbook'", (shop,)
        ).fetchone()['id']
        assert document_id == oldest


def test_a_new_document_run_needs_only_its_document_name(store):
    with store.connect() as conn:
        run(conn, LATER)
        project = store.project(conn, 'shop')
        actor = conn.execute(
            "INSERT INTO ka.principals(issuer,subject) VALUES ('test','writer') RETURNING id"
        ).fetchone()['id']
        model = a_model(conn, actor)
        insert = """INSERT INTO ka.agent_sessions(id,project_id,actor_id,model_id,title,purpose,document_name,
            prompt_snapshot,context_snapshot,agent_config) VALUES (gen_random_uuid(),%s,%s,%s,'Write','documents',%s,'{}','{}','{}')"""
        conn.execute(insert, (project, actor, model, 'Payments API'))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(insert, (project, actor, model, None))
