"""0133: a full-text index on live documents that search_documents uses; the agents' documents_grounded column goes."""

from pathlib import Path

from knowledge_admin.capabilities.document_tools import SEARCH_VECTOR

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0133_document_search.up.sql', '0133_document_search.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def index(conn):
    return conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='ka' AND indexname='documents_search'"
    ).fetchone()


def grounded_column(conn):
    return conn.execute(
        """SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
        AND column_name='documents_grounded'"""
    ).fetchone()


def test_up_indexes_live_documents_and_drops_the_grounded_column(store):
    with store.connect() as conn:
        found = index(conn)
        assert found is not None and 'gin' in found['indexdef'] and 'NOT is_draft' in found['indexdef']
        assert grounded_column(conn) is None
        conn.execute('SET LOCAL enable_seqscan = off')
        plan = conn.execute(
            f"EXPLAIN SELECT id FROM ka.documents d WHERE NOT d.is_draft AND {SEARCH_VECTOR} @@ to_tsquery('simple','x')"
        ).fetchall()
        assert any('documents_search' in row['QUERY PLAN'] for row in plan)


def test_down_drops_the_index_and_restores_the_column(store):
    with store.connect() as conn:
        run(conn, DOWN)
        assert index(conn) is None
        assert grounded_column(conn) is not None
        run(conn, UP)
        assert index(conn) is not None and grounded_column(conn) is None
