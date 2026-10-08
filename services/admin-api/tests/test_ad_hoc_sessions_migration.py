"""0082: Generate documents sessions may run an ad-hoc agent (agent_id NULL, native, pinned config); Test sessions and
Claude Code sessions keep their agent. Down deletes ad-hoc sessions and restores NOT NULL."""
from pathlib import Path

import psycopg
import pytest

from session_rows import a_model, add_ad_hoc_session, add_test_session

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0082_ad_hoc_sessions.up.sql', '0082_ad_hoc_sessions.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def test_only_native_documents_sessions_run_without_an_agent_and_down_removes_them(store):
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        model = a_model(conn, actor)
        ad_hoc = add_ad_hoc_session(conn, project, actor, model, message='Document it')
        tested = add_test_session(conn, project, actor, 'search', model)
        conn.commit()
        for statement in ("UPDATE ka.agent_sessions_cc SET purpose='test',document_name=NULL,folder=NULL WHERE id=%s",
                          "UPDATE ka.agent_sessions_cc SET runtime='claude_code',agent_config=NULL WHERE id=%s"):
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(statement, (ad_hoc,))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute('UPDATE ka.agent_sessions_cc SET agent_id=NULL WHERE id=%s', (tested,))
        run(conn, DOWN)
        conn.commit()
        ids = [row['id'] for row in conn.execute('SELECT id FROM ka.agent_sessions_cc')]
        assert ids == [tested]
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_session_inputs WHERE session_id=%s',
                            (ad_hoc,)).fetchone()['n'] == 0
        with pytest.raises(psycopg.errors.NotNullViolation), conn.transaction():
            add_ad_hoc_session(conn, project, actor, model)
        run(conn, UP)
        conn.commit()
        add_ad_hoc_session(conn, project, actor, model)
