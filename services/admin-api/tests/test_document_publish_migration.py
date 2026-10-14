"""Migration 0064 removes the publish-to-RAG columns; its down migration restores them."""

from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
PUBLISH = {'published_path', 'published_at', 'publish_error', 'index_pending'}
# 0104 rebuilds document_details over its own tables; this test uses the 0103 view, so 0104 is rolled back first and
# applied again at the end.
DOCUMENT_MODEL = ('0104_document_model.down.sql', '0104_document_model.up.sql')


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


@pytest.fixture(autouse=True)
def before_0104(store):
    with store.connect() as conn:
        run(conn, DOCUMENT_MODEL[0])
        conn.commit()


def columns(conn, table):
    return {
        r['column_name']
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name=%s", (table,)
        )
    }


def test_0064_removes_publish_columns_and_down_restores_them(store):
    with store.connect() as conn:
        assert not PUBLISH & columns(conn, 'documents')
        assert not {'published_version'} & columns(conn, 'document_details')
        assert 'service_ids' in columns(conn, 'document_details')
        conn.execute((MIGRATIONS / '0064_remove_document_publishing.down.sql').read_text(), prepare=False)
        assert PUBLISH <= columns(conn, 'documents')
        assert 'published_version' in columns(conn, 'document_details')
        conn.execute((MIGRATIONS / '0064_remove_document_publishing.up.sql').read_text(), prepare=False)
        assert not PUBLISH & columns(conn, 'documents')
        conn.commit()
        run(conn, DOCUMENT_MODEL[1])
        assert 'draft_count' in columns(conn, 'document_details')
