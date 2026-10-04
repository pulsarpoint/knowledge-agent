"""Migration 0062 stores where a document is published and exposes it on document_details."""
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def columns(conn, table):
    return {r['column_name'] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name=%s", (table,))}


def test_publish_columns_and_view(store):
    with store.connect() as conn:
        assert {'published_path', 'published_at', 'publish_error', 'index_pending'} <= columns(conn, 'documents')
        assert {'published_path', 'publish_error', 'index_pending', 'published_version'} <= columns(conn, 'document_details')


def test_down_then_up(store):
    down = (MIGRATIONS / '0062_document_publishing.down.sql').read_text()
    up = (MIGRATIONS / '0062_document_publishing.up.sql').read_text()
    with store.connect() as conn:
        conn.execute(down, prepare=False)
        assert 'published_path' not in columns(conn, 'documents')
        assert 'service_ids' in columns(conn, 'document_details')
        conn.execute(up, prepare=False)
        assert 'published_version' in columns(conn, 'document_details')
