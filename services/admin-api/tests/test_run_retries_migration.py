"""0146: run retries: agent_sessions takes the 'retrying' status, a retry counter and when a person last pressed Retry;
notifications get the Jira failure comment (jira_text). Down gives back the 0145 schema; a retrying run becomes
failed."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from test_rag_gone_migration import schema
from test_write_with_an_agent_migration import documents_run

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0146_run_retries.up.sql', '0146_run_retries.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


@pytest.fixture
def at_0145(postgres):
    database = f'test_{uuid4().hex}'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}', row_factory=dict_row) as conn:
            for migration in sorted(MIGRATIONS.glob('*.up.sql')):
                if migration.name[:4] <= '0145':
                    conn.execute(migration.read_text(), prepare=False)
            yield schema(conn)
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


def test_up_adds_retries_and_down_gives_back_the_0145_schema(store, at_0145):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        waiting = documents_run(conn, project)
        conn.execute(
            "UPDATE ka.agent_sessions SET status='retrying',retries=2,retried_at=now(),retry_at=now() WHERE id=%s",
            (waiting,),
        )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute('UPDATE ka.agent_sessions SET retries=-1 WHERE id=%s', (waiting,))
        conn.commit()
        run(conn, '0147_session_review.down.sql')  # newer migrations first
        run(conn, DOWN)
        assert schema(conn) == at_0145
        row = conn.execute('SELECT status FROM ka.agent_sessions WHERE id=%s', (waiting,)).fetchone()
        assert row['status'] == 'failed'
        conn.commit()
        run(conn, UP)
        row = conn.execute(
            'SELECT status,retries,retried_at,retry_at FROM ka.agent_sessions WHERE id=%s', (waiting,)
        ).fetchone()
        assert (row['status'], row['retries'], row['retried_at'], row['retry_at']) == ('failed', 0, None, None)
        jira_text = conn.execute(
            """SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='notifications'
            AND column_name='jira_text'"""
        ).fetchone()
        assert jira_text is not None
