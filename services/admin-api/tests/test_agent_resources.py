"""Resources (0075): an agent worked with fixed resources or received them per run. 0114 dropped the column: every
run narrows the agent's access object (test_agent_access)."""

from pathlib import Path

import psycopg
import pytest

from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0075_agent_resources.up.sql', '0075_agent_resources.down.sql'
REMOVED = '0077_remove_coordinator.down.sql'  # 0075 down drops a check 0077 removed
LATER = (
    '0145_write_with_an_agent.down.sql',
    '0144_no_document_folders.down.sql',  # 0084 down moves agent_sessions.folder
    '0134_rag_gone.down.sql',  # 0114 down rebuilds the collection selections from rag_collections
    '0114_drop_agent_resource_tables.down.sql',
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
