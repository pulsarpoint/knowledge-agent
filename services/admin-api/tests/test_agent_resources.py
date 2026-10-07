"""Resources (0075): an agent works with fixed resources or receives them per run. The setting is saved, versioned
and refused for the coordinator; a per-run agent never runs with its whole selection."""
import asyncio
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile

from knowledge_admin.agents import create_agent
from knowledge_admin.knowledge_agents.config import NO_RUN_RESOURCES, get_agent_config, require_run_resources
from knowledge_admin.profiles_api import COORDINATOR_FIXED
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_claude_code_sessions import refused, sessions  # noqa: F401  (fixture)
from test_profiles import update, wizard_payload
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0075_agent_resources.up.sql', '0075_agent_resources.down.sql'
EMPTY_CHANGES = {'system_prompt': None, 'instructions': None, 'server_instructions': []}


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def has_column(conn) -> bool:
    return conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents'
        AND column_name='resources'""").fetchone() is not None


def listed(client) -> dict:
    return {row['id']: row for row in client.get('/api/agents', headers=HEADERS).json()['agents']}


def new_version(client, label: str) -> dict:
    source = client.get('/api/agents/operations/versions/source', headers=HEADERS).json()
    created = client.post('/api/agents/operations/versions', headers=HEADERS, json={
        'label': label, 'source_hash': source['source_hash'], 'changes': EMPTY_CHANGES})
    assert created.status_code == 201, created.text
    return {**created.json(), 'source': source}


def test_agents_are_fixed_by_default_and_the_coordinator_stays_fixed(store):
    with store.connect() as conn:
        assert {row['resources'] for row in conn.execute('SELECT resources FROM ka.agents')} == {'fixed'}
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='search'")
        for statement, error in (
                ("UPDATE ka.agents SET resources='per_run' WHERE is_coordinator", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET resources='sometimes' WHERE id='code'", psycopg.errors.CheckViolation),
                ("UPDATE ka.agents SET resources=NULL WHERE id='code'", psycopg.errors.NotNullViolation)):
            with pytest.raises(error), conn.transaction():
                conn.execute(statement)


def test_down_refuses_per_run_agents_and_versions_then_up_makes_every_agent_fixed(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    assert new_version(client, 'Per run')['source']['config']['resources'] == 'per_run'
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match='Per-run agents'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.agents SET resources='fixed' WHERE id='operations'")
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Per-run agents'):  # its versions are still per run
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.agents SET active_config_version_id=NULL WHERE id='operations'")
        conn.execute("DELETE FROM ka.agent_versions WHERE agent='operations'")
        conn.commit()
        run(conn, DOWN)
        conn.commit()
        assert not has_column(conn)
        run(conn, UP)
        conn.commit()
        assert has_column(conn)
        assert conn.execute("SELECT resources FROM ka.agents WHERE id='operations'").fetchone()['resources'] == 'fixed'


def test_the_setting_is_saved_versioned_listed_and_refused_for_the_coordinator(store, saved_agent):
    client = saved_agent[0]
    operations = client.get('/api/agents/operations', headers=HEADERS).json()
    assert operations['resources'] == 'fixed'
    saved = update(client, operations, resources='per_run')
    assert saved.status_code == 200, saved.text
    assert saved.json()['resources'] == 'per_run'
    assert update(client, saved.json(), resources=None).json()['resources'] == 'per_run'  # omitted: kept
    assert get_agent_config('operations', conninfo=store.conninfo)['resources'] == 'per_run'
    coordinator = client.get('/api/agents/coordinator', headers=HEADERS).json()
    assert coordinator['resources'] == 'fixed'
    refusal = update(client, coordinator, resources='per_run')
    assert refusal.status_code == 400 and refusal.json()['error']['message'] == COORDINATOR_FIXED
    assert (listed(client)['operations']['resources'], listed(client)['coordinator']['resources']) == ('per_run', 'fixed')
    # The first draft also activates "Initial configuration" (per run). A version keeps its mode; the list shows what
    # runs (the active version), General the working copy.
    version = new_version(client, 'Per run analyst')
    assert get_agent_config('operations', conninfo=store.conninfo, version_id=version['id'])['resources'] == 'per_run'
    back = update(client, client.get('/api/agents/operations', headers=HEADERS).json(), resources='fixed')
    assert back.json()['resources'] == 'fixed'
    assert listed(client)['operations']['resources'] == 'per_run'
    with store.connect() as conn:  # versions saved before 0075 have no mode: they are fixed
        conn.execute("UPDATE ka.agent_versions SET config=config-'resources' WHERE agent='operations'")
    assert get_agent_config('operations', conninfo=store.conninfo)['resources'] == 'fixed'
    assert listed(client)['operations']['resources'] == 'fixed'
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
    board = client.post('/api/agent-board/tasks', headers=HEADERS,
                        json={'id': str(uuid4()), 'task': 'Check the queue', 'subagent_ids': ['operations']})
    assert board.status_code == 400 and board.json()['error']['message'] == 'Per-run agents cannot join the multi-agent board yet.'
    with store.connect() as conn:
        with pytest.raises(ModelUnavailable):
            profile(conn, 'operations', runnable=True)


def test_test_chats_refuse_per_run_agents_until_runs_pass_resources(store, saved_agent):
    """Replaced in Task 4, when test chats pass run resources."""
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id='operations'")
    chat = client.post('/api/agents/operations/sessions', headers=HEADERS, json={'title': 'Test', 'is_test': True})
    assert chat.status_code == 409 and chat.json()['error']['message'] == NO_RUN_RESOURCES
    run_ = client.post('/api/agents/operations/runs', headers=HEADERS, json={'task': 'Check the queue', 'is_test': True})
    assert run_.status_code == 409 and NO_RUN_RESOURCES in run_.text
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n'] == 0


def test_generate_documents_refuses_per_run_agents_until_runs_pass_resources(sessions):
    """Replaced in Task 5, when Generate documents passes run resources."""
    with sessions.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id=%s", (sessions.agent,))
    assert refused(sessions) == NO_RUN_RESOURCES
