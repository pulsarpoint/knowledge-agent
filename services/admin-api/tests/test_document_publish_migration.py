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
        run(conn, '0144_no_document_folders.down.sql')  # 0144 drops the folder columns 0109 and 0104 change
        run(conn, '0131_proposals_gone.down.sql')  # 0131 drops discovery_proposals, which older migrations change
        run(conn, '0127_environment_workspace.down.sql')  # 0127 makes environment_servers, which 0126 down makes again
        run(conn, '0126_inventory_drop_old.down.sql')  # 0126 drops what 0109 and older change
        run(conn, '0109_tags_not_flags.down.sql')
        run(conn, '0107_inventory_document.down.sql')  # its flag lives on 0104's tables
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
