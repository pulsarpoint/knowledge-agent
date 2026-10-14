"""0068 gives documents a documentation folder with slugs unique per folder; without a folder, slugs stay unique."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0068_document_folders.up.sql', '0068_document_folders.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def document(conn, project, slug: str, folder: str | None = None, *, with_folder: bool = True):
    doc, version = uuid4(), uuid4()
    if with_folder:
        conn.execute(
            'INSERT INTO ka.documents(id,project_id,slug,folder,current_version_id) VALUES (%s,%s,%s,%s,%s)',
            (doc, project, slug, folder, version),
        )
    else:
        conn.execute(
            'INSERT INTO ka.documents(id,project_id,slug,current_version_id) VALUES (%s,%s,%s,%s)',
            (doc, project, slug, version),
        )
    conn.execute(
        """INSERT INTO ka.document_versions(id,document_id,project_id,version,title,description,purpose,audience,
        markdown,content_hash,source_snapshot,origin) VALUES (%s,%s,%s,1,%s,'','explanation','both','# T',
        encode(sha256(convert_to('# T','UTF8')),'hex'),'{}','manual')""",
        (version, doc, project, slug),
    )
    return doc


@pytest.fixture
def db(store):
    with store.connect() as conn:
        yield conn, store.project(conn, 'shop')


def test_folder_column_and_per_folder_slug_uniqueness(db):
    conn, project = db
    for folder in ('payments', 'team/payments', None):
        document(conn, project, 'runbook', folder)
    for folder in ('payments', None):  # NULLS NOT DISTINCT: folderless slugs stay unique as before
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                document(conn, project, 'runbook', folder)
    for bad in (
        '.hidden',
        'a//b',
        '/abs',
        'a/',
        '../x',
        'my docs',
        'team/node_modules',
        'services',
        'services/x',
        'x' * 201,
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match='documents_folder_check'):
            with conn.transaction():
                document(conn, project, 'other', bad)
    rows = conn.execute(
        "SELECT folder FROM ka.document_details WHERE slug='runbook' ORDER BY folder NULLS FIRST"
    ).fetchall()
    assert [row['folder'] for row in rows] == [None, 'payments', 'team/payments']


def test_down_restores_project_wide_slugs_and_refuses_shared_slugs(db):
    conn, project = db
    document(conn, project, 'runbook', 'payments')
    shared = document(conn, project, 'runbook', 'team')
    conn.commit()  # deferred version checks must not be pending while the table is altered
    with pytest.raises(psycopg.errors.RaiseException, match='share a slug'):
        with conn.transaction():
            run(conn, DOWN)
    conn.execute('DELETE FROM ka.documents WHERE id=%s', (shared,))
    conn.commit()
    run(conn, DOWN)
    assert (
        conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka'
        AND table_name='documents' AND column_name='folder'""").fetchone()
        is None
    )
    assert conn.execute("SELECT count(*) AS n FROM ka.document_details WHERE slug='runbook'").fetchone()['n'] == 1
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            document(conn, project, 'runbook', with_folder=False)
    conn.commit()
    run(conn, UP)
    document(conn, project, 'runbook', 'other')
