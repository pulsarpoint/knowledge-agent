"""0074: sessions get an agent type, a pinned native config and native history; Claude Code sessions are unchanged."""
from pathlib import Path

import psycopg
import pytest

from test_claude_code_sessions import sessions, claude_code_session  # noqa: F401  (fixture)

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0074_native_agent_sessions.up.sql', '0074_native_agent_sessions.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def columns(conn) -> set[str]:
    return {row['column_name'] for row in conn.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_schema='ka' AND table_name='agent_sessions_cc'""")}


def test_existing_sessions_are_claude_code_and_native_sessions_need_a_pinned_config(sessions):
    sid = claude_code_session(sessions)['id']
    with sessions.store.connect() as conn:
        row = conn.execute('SELECT runtime,agent_config,history FROM ka.agent_sessions_cc WHERE id=%s', (sid,)).fetchone()
        assert row == {'runtime': 'claude_code', 'agent_config': None, 'history': []}
        for change in ("runtime='openai'", "agent_config='{}'", "history='{}'", "runtime='codex'",
                       "runtime='openai',agent_config='[]'"):
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(f'UPDATE ka.agent_sessions_cc SET {change} WHERE id=%s', (sid,))
        conn.execute("""UPDATE ka.agent_sessions_cc SET runtime='openai',agent_config='{"id": "writer"}',
            history='[{"role": "user", "content": "Hi"}]' WHERE id=%s""", (sid,))


def test_down_refuses_native_sessions_then_drops_the_columns_and_up_restores_them(sessions):
    store = sessions.store
    sid = claude_code_session(sessions)['id']
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET runtime='openai',agent_config='{}' WHERE id=%s", (sid,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Native agent sessions exist'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.agent_sessions_cc SET runtime='claude_code',agent_config=NULL WHERE id=%s", (sid,))
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        assert not {'runtime', 'agent_config', 'history'} & columns(conn)
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc').fetchone()['n'] == 1  # Claude Code kept
        run(conn, UP)
        conn.commit()
        assert conn.execute('SELECT runtime,history FROM ka.agent_sessions_cc WHERE id=%s', (sid,)).fetchone() == {
            'runtime': 'claude_code', 'history': []}
