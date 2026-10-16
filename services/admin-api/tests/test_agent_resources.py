"""Resources (0075): an agent works with fixed resources or receives them per run. The column stays until 0114;
neither the API nor runs read it: every run narrows the agent's access object (test_agent_access)."""

from pathlib import Path

import psycopg
import pytest
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile

from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0075_agent_resources.up.sql', '0075_agent_resources.down.sql'
REMOVED = '0077_remove_coordinator.down.sql'  # 0075 down drops a check 0077 removed
LATER = (
    '0084_remove_claude_code.down.sql',
    '0081_test_sessions.down.sql',
    '0080_clone_agents.down.sql',
)  # 0075 down reads ka.agent_versions


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def has_column(conn) -> bool:
    return (
        conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
        AND column_name='resources'""").fetchone()
        is not None
    )


def listed(client) -> dict:
    return {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}


def test_agents_are_fixed_by_default(store):
    with store.connect() as conn:
        assert {row['resources'] for row in conn.execute('SELECT resources FROM ka.agents')} == {'fixed'}
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='search'")
        for statement, error in (
            ("UPDATE ka.agents SET resources='sometimes' WHERE id='code'", psycopg.errors.CheckViolation),
            ("UPDATE ka.agents SET resources=NULL WHERE id='code'", psycopg.errors.NotNullViolation),
        ):
            with pytest.raises(error), conn.transaction():
                conn.execute(statement)


def test_down_refuses_per_run_agents_then_up_makes_every_agent_fixed(store, saved_agent):
    with store.connect() as conn:
        for name in LATER:
            run(conn, name)
        run(conn, REMOVED)
        conn.commit()
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Per-run agents'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id='operations'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        assert not has_column(conn)
        run(conn, UP)
        conn.commit()
        assert has_column(conn)
        assert conn.execute("SELECT resources FROM ka.agents WHERE id='operations'").fetchone()['resources'] == 'fixed'


def test_the_llm_profile_of_a_per_run_agent_is_not_runnable(store, saved_agent):
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    with store.connect() as conn:
        with pytest.raises(ModelUnavailable):
            profile(conn, 'operations', runnable=True)
