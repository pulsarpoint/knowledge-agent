"""Resources (0075): an agent works with fixed resources or receives them per run. The setting is saved;
a per-run agent never runs with its whole selection."""
import asyncio
from pathlib import Path

import psycopg
import pytest
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile

from knowledge_admin.agents import create_agent
from knowledge_admin.knowledge_agents.config import get_agent_config, require_run_resources
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_profiles import update, wizard_payload
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0075_agent_resources.up.sql', '0075_agent_resources.down.sql'
REMOVED = '0077_remove_coordinator.down.sql'  # 0075 down drops a check 0077 removed
LATER = '0080_clone_agents.down.sql'  # 0075 down reads ka.agent_versions


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def has_column(conn) -> bool:
    return conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
        AND column_name='resources'""").fetchone() is not None


def listed(client) -> dict:
    return {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}


def test_agents_are_fixed_by_default(store):
    with store.connect() as conn:
        assert {row['resources'] for row in conn.execute('SELECT resources FROM ka.agents')} == {'fixed'}
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='search'")
        for statement, error in (
                ("UPDATE ka.agents SET resources='sometimes' WHERE id='code'", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET resources=NULL WHERE id='code'", psycopg.errors.NotNullViolation)):
            with pytest.raises(error), conn.transaction():
                conn.execute(statement)


def test_down_refuses_per_run_agents_then_up_makes_every_agent_fixed(store, saved_agent):
    with store.connect() as conn:
        run(conn, LATER)
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


def test_the_setting_is_saved_and_listed(store, saved_agent):
    client = saved_agent[0]
    operations = client.get('/api/agents/operations', headers=HEADERS).json()
    assert operations['resources'] == 'fixed'
    saved = update(client, operations, resources='per_run')
    assert saved.status_code == 200, saved.text
    assert saved.json()['resources'] == 'per_run'
    assert update(client, saved.json(), resources=None).json()['resources'] == 'per_run'  # omitted: kept
    assert get_agent_config('operations', conninfo=store.conninfo)['resources'] == 'per_run'
    assert listed(client)['operations']['resources'] == 'per_run'
    back = update(client, client.get('/api/agents/operations', headers=HEADERS).json(), resources='fixed')
    assert back.json()['resources'] == 'fixed'
    assert listed(client)['operations']['resources'] == 'fixed'  # the list shows the agent: what runs
    created = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Code analyst', resources='per_run'))
    assert created.status_code == 201, created.text
    assert client.get(f"/api/agents/{created.json()['id']}", headers=HEADERS).json()['resources'] == 'per_run'
    assert client.post('/api/agents/wizard', headers=HEADERS,
                       json=wizard_payload('Odd agent', resources='sometimes')).status_code == 400


def test_a_per_run_agent_never_runs_with_its_whole_selection(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    config = get_agent_config('operations', conninfo=store.conninfo)
    with pytest.raises(ValueError, match='per run'):
        require_run_resources(config)
    require_run_resources({**config, 'run_resources': {}})  # a run that passed its resources
    require_run_resources({**config, 'resources': 'fixed'})

    async def build():
        async with create_agent(config, credentials={}, timeout_seconds=5):
            pass
    with pytest.raises(ValueError, match='per run'):
        asyncio.run(build())
    with store.connect() as conn:
        with pytest.raises(ModelUnavailable):
            profile(conn, 'operations', runnable=True)



def test_the_list_says_what_a_per_run_agent_accepts(store, saved_agent):
    client = saved_agent[0]
    assert listed(client)['operations']['accepts'] == []  # fixed
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    assert listed(client)['operations']['accepts'] == [
        {'kind': 'code', 'limit': 1}, {'kind': 'documents', 'limit': 1}, {'kind': 'servers', 'limit': 1},
        {'kind': 'services', 'limit': 1}]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET all_repositories=true,ssh_enabled=false WHERE id='operations'")
        conn.execute("DELETE FROM ka.agent_repositories WHERE agent_id='operations'")
    expected = [{'kind': 'code', 'limit': 'any'}, {'kind': 'documents', 'limit': 1}, {'kind': 'services', 'limit': 1}]
    assert listed(client)['operations']['accepts'] == expected
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_enabled=false,services_enabled=false WHERE id='operations'")
    assert listed(client)['operations']['accepts'] == [{'kind': 'code', 'limit': 'any'}]


def test_the_list_counts_only_servers_a_run_may_use(store, saved_agent):
    """As run resource options: a server counts when it is enabled and allows SSH."""
    client = saved_agent[0]

    def servers() -> int:
        return next(item['limit'] for item in listed(client)['operations']['accepts'] if item['kind'] == 'servers')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    assert servers() == 1
    with store.connect() as conn:
        conn.execute("UPDATE ka.servers SET ssh_allowed=false")
    assert servers() == 0
    with store.connect() as conn:
        conn.execute("UPDATE ka.servers SET ssh_allowed=true, enabled=false")
        conn.execute("DELETE FROM ka.agent_server_connections WHERE agent_id='operations'")
    assert servers() == 0
