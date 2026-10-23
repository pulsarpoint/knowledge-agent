"""0069 makes documentation folders lowercase-only: Payments and payments are one directory on macOS."""

import psycopg
import pytest

from test_document_folders_migration import DOCUMENT_MODEL, document, run

UP, DOWN = '0069_document_folders_lowercase.up.sql', '0069_document_folders_lowercase.down.sql'


@pytest.fixture(autouse=True)
def before_0104(store):
    """These tests use the 0103 document tables: 0104 is rolled back first (see test_document_folders_migration)."""
    with store.connect() as conn:
        run(conn, '0131_proposals_gone.down.sql')  # 0131 drops discovery_proposals, which older migrations change
        run(conn, '0127_environment_workspace.down.sql')  # 0127 makes environment_servers, which 0126 down makes again
        run(conn, '0126_inventory_drop_old.down.sql')  # 0126 drops what 0109 and older change
        run(conn, '0109_tags_not_flags.down.sql')
        run(conn, '0107_inventory_document.down.sql')  # its flag lives on 0104's tables
        run(conn, DOCUMENT_MODEL[0])
        conn.commit()


@pytest.fixture
def db(store):
    with store.connect() as conn:
        yield conn, store.project(conn, 'shop')


def test_folders_are_lowercase_only(db):
    conn, project = db
    for folder in ('payments', 'team/v1.2_docs-x'):
        document(conn, project, 'runbook', folder)
    for bad in ('Payments', 'team/Payments', 'A/b', '.hidden', 'services/x', 'team/node_modules', 'x' * 201):
        with pytest.raises(psycopg.errors.CheckViolation, match='documents_folder_check'):
            with conn.transaction():
                document(conn, project, 'other', bad)


def test_down_allows_uppercase_again_and_up_refuses_existing_uppercase_folders(db):
    conn, project = db
    conn.commit()
    run(conn, DOWN)
    upper = document(conn, project, 'runbook', 'Team/Payments')
    conn.commit()
    with pytest.raises(psycopg.errors.RaiseException, match='Team/Payments.*lowercase'):
        with conn.transaction():
            run(conn, UP)
    conn.execute("UPDATE ka.documents SET folder='team/payments' WHERE id=%s", (upper,))
    conn.commit()
    run(conn, UP)
    with pytest.raises(psycopg.errors.CheckViolation, match='documents_folder_check'):
        with conn.transaction():
            document(conn, project, 'other', 'Team')
