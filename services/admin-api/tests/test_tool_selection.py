"""Tool selection: catalog tools switched off for a saved agent (ka.agents.disabled_tools) and further for a run (the
request's disabled_tools) are never offered. A run can only switch more off; subagents and experts inherit the caller's
off tools; a tool added to the catalog later is on."""

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
from agents.tool_context import ToolContext
from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.agents.runner import instructions_and_tools
from knowledge_admin.events import processor as event_processor
from knowledge_admin.knowledge_agents.experts import load, offered
from knowledge_admin.knowledge_agents.subagents import subagent_config
from knowledge_admin.tools import selection
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.sdk import NOT_OFFERED, function_tool
from test_discovery_runs import config_of, ssh_ready
from test_event_processor import a_handler, an_event
from test_event_runs import PROMPT
from test_event_runs import sessions as event_sessions
from test_expert_calls import ENTRY, Loader, expert_config, host
from test_generations import native_agent, sessions  # noqa: F401  (fixture)
from test_profiles import create, update
from test_repositories import HEADERS
from test_run_resources import narrowed_agent  # noqa: F401  (fixture)
from test_sessions_saved_agent import agent, start  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)
from test_subagent_config import NO_RESOURCES, parent
from test_test_sessions import tester  # noqa: F401  (fixture)
from test_tool_catalog import config

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0135_agent_disabled_tools.up.sql', '0135_agent_disabled_tools.down.sql'


def offered_names(c, subagents=True) -> list[str]:
    return [tool.name for tool in instructions_and_tools(c, subagents=subagents)[1]]


def forged(name: str, c: dict, subagents=True) -> str:
    """A call of a catalog tool the model was not given, as the adapter runs it: context['tools'] is what the runner
    sets from the offered tools."""
    context = {
        'config': c,
        'credentials': {},
        'workspace': Path('/tmp'),
        'tools': frozenset(offered_names(c, subagents)),
    }
    payload = json.dumps({'server_id': 'x', 'command': 'uptime'})
    wrapped = ToolContext(context=context, tool_name=name, tool_call_id='t1', tool_arguments=payload)
    return asyncio.run(function_tool(CATALOG[name], CATALOG[name].usage).on_invoke_tool(wrapped, payload))


# The pure rules


def test_a_saved_agent_disables_the_union_and_an_ad_hoc_run_the_request():
    assert selection.disabled_for(['ssh_run'], ['read_file', 'ssh_run']) == frozenset({'ssh_run', 'read_file'})
    assert selection.disabled_for(['ssh_run'], []) == frozenset({'ssh_run'})  # a request cannot switch it back on
    assert selection.disabled_for((), ['read_file']) == frozenset({'read_file'})


def test_a_stored_list_keeps_only_catalog_tools():
    assert selection.known(['ssh_run', 'gone_tool', 'read_file', 'ssh_run']) == ['read_file', 'ssh_run']


def test_names_are_checked_against_the_catalog():
    assert selection.validate(['ssh_run', 'read_file', 'ssh_run']) == ['read_file', 'ssh_run']
    assert selection.validate([]) == []
    with pytest.raises(ValueError) as refused:
        selection.validate(['read_file', 'ssh_agent'])
    assert str(refused.value) == 'Unknown tool: ssh_agent.'


# The runner


def test_an_off_tool_is_not_offered_and_the_backstop_refuses_it():
    c = {**config(ssh=True), 'disabled_tools': ['ssh_run', 'search_documents']}
    names = offered_names(c)
    assert names == [name for name in CATALOG if name not in {'ssh_run', 'search_documents'}]
    assert NOT_OFFERED.format(name='ssh_run') in forged('ssh_run', c)


def test_a_tool_added_to_the_catalog_later_is_on(monkeypatch):
    """Review Focus 5: the off tools are stored, so a new catalog tool is not in an agent's list and is offered."""
    monkeypatch.setitem(CATALOG, 'new_tool', dataclasses.replace(CATALOG['list_files'], name='new_tool'))
    c = {**config(), 'disabled_tools': ['ssh_run']}
    assert 'new_tool' in offered_names(c) and 'ssh_run' not in offered_names(c)


def test_a_subagent_keeps_its_parents_off_tools():
    lead = parent(disabled_tools=['search_documents'])
    sub = subagent_config(lead, None)
    assert sub['disabled_tools'] == ['search_documents']
    assert 'search_documents' not in offered_names(sub, subagents=False)


# Review Focus 3: an expert called from a run with search_documents off


def call_expert(loader, caller: dict) -> dict:
    """One ask_<name> call from a run of `caller`; the configuration the expert ran with."""
    from knowledge_admin.capabilities.context import TOOL_CALL_ID

    subagents, fake, _, _ = host({'Map payments': {'steps': [], 'answer': 'Done.'}}, loader)
    context = {
        'config': {**caller, 'experts': [ENTRY]},
        'credentials': {},
        'workspace': Path('/tmp'),
        'tools': frozenset(CATALOG) | {ENTRY['tool']},
        'subagents': subagents,
    }
    token = TOOL_CALL_ID.set('e1')
    try:
        asyncio.run(subagents.ask(context, ENTRY, 'Map payments', None, 'e1'))
    finally:
        TOOL_CALL_ID.reset(token)
    [run] = fake.calls
    return run['config']


class Own(Loader):
    """experts.load with the expert's own off tools."""

    def __call__(self, entry, given):
        config, credentials = super().__call__(entry, given)
        return {**config, 'disabled_tools': ['ssh_run']}, credentials


def test_an_expert_inherits_the_callers_off_tools_and_keeps_its_own():
    ran = call_expert(Own(), parent(disabled_tools=['search_documents']))
    assert ran['disabled_tools'] == ['search_documents', 'ssh_run']
    assert not {'search_documents', 'ssh_run'} & set(offered_names(ran))
    assert call_expert(Loader(), parent(disabled_tools=['search_documents']))['disabled_tools'] == ['search_documents']


def test_an_expert_called_with_nothing_off_runs_with_its_own():
    assert call_expert(Own(), parent())['disabled_tools'] == ['ssh_run']
    assert call_expert(Loader(), parent()) == expert_config()


def test_load_gives_the_experts_own_off_tools(narrowed_agent):
    s = narrowed_agent
    with s.store.connect() as conn:
        conn.execute(
            "UPDATE ka.agents SET expert=true,when_to_use='Payments code.',disabled_tools='{ssh_run}' WHERE id=%s",
            (s.analyst,),
        )
        project = s.store.project(conn, 'shop')
        [entry] = [found for found in offered(conn, None) if found['id'] == s.analyst]
    config, _ = load(s.store, project, entry, {**NO_RESOURCES, 'code_project_ids': [s.repo]})
    assert config['disabled_tools'] == ['ssh_run']


# Saved agents: the profile API


def test_the_profile_reads_and_writes_the_off_tools(agent):
    profile = agent.client.get(f'/api/agents/{agent.id}', headers=HEADERS).json()
    assert profile['disabled_tools'] == []
    saved = update(agent.client, profile, disabled_tools=['ssh_run', 'read_file', 'ssh_run'])
    assert saved.status_code == 200, saved.text
    assert saved.json()['disabled_tools'] == ['read_file', 'ssh_run']
    kept = {key: value for key, value in saved.json().items() if key != 'disabled_tools'}
    response = agent.client.put(
        f'/api/agents/{agent.id}',
        headers=HEADERS,
        json={field: kept[field] for field in ('revision', 'name', 'description', 'when_to_use', 'enabled')}
        | {'access': {'resources': []}, 'instructions': ''},
    )
    assert response.status_code == 200, response.text
    assert response.json()['disabled_tools'] == ['read_file', 'ssh_run']  # left out: kept
    refused = update(agent.client, response.json(), disabled_tools=['ssh_agent'])
    assert refused.status_code == 400 and refused.json()['error']['message'] == 'Unknown tool: ssh_agent.'
    listed = {row['id']: row for row in agent.client.get('/api/agents', headers=HEADERS).json()['agents']}
    assert listed[agent.id]['disabled_tools'] == ['read_file', 'ssh_run']
    config = agent.client.get(f'/api/agents/{agent.id}/config', headers=HEADERS).json()
    assert config['disabled_tools'] == ['read_file', 'ssh_run']


def stale(ask, agent_id):
    """The agent's stored list names ssh_run and a tool since removed from the catalog."""
    with ask.s.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET disabled_tools='{gone_tool,ssh_run}' WHERE id=%s", (agent_id,))


def test_a_removed_tool_in_the_stored_list_is_dropped_and_never_refuses_a_save(agent, ask):
    stale(ask, agent.id)
    profile = agent.client.get(f'/api/agents/{agent.id}', headers=HEADERS).json()
    assert profile['disabled_tools'] == ['ssh_run']
    listed = {row['id']: row for row in agent.client.get('/api/agents', headers=HEADERS).json()['agents']}
    assert listed[agent.id]['disabled_tools'] == ['ssh_run']
    saved = update(agent.client, profile)  # sends back the list it read
    assert saved.status_code == 200, saved.text
    assert saved.json()['disabled_tools'] == ['ssh_run']
    stale(ask, agent.id)
    kept = {field: saved.json()[field] for field in ('name', 'description', 'when_to_use', 'enabled', 'instructions')}
    response = agent.client.put(
        f'/api/agents/{agent.id}',
        headers=HEADERS,
        json={**kept, 'revision': saved.json()['revision'], 'access': {'resources': []}},
    )  # the list left out: kept, without the removed tool
    assert response.status_code == 200 and response.json()['disabled_tools'] == ['ssh_run']


def test_a_run_of_an_agent_with_a_removed_tool_stored_starts(agent, ask):
    stale(ask, agent.id)
    preview = start(agent.client, '/preview', agent_id=agent.id, disabled_tools=['read_file'])
    assert preview.status_code == 200, preview.text
    assert preview.json()['config']['disabled_tools'] == ['read_file', 'ssh_run']
    created = start(agent.client, agent_id=agent.id)
    assert created.status_code == 201, created.text
    detail = agent.client.get(f'/api/sessions/{created.json()["id"]}', headers=HEADERS).json()
    assert detail['disabled_tools'] == ['ssh_run']
    refused = start(agent.client, '/preview', agent_id=agent.id, disabled_tools=['gone_tool'])
    assert refused.status_code == 400 and refused.json()['error']['message'] == 'Unknown tool: gone_tool.'
    clone = agent.client.post(f'/api/agents/{agent.id}/clone', headers=HEADERS).json()
    assert agent.client.get(f'/api/agents/{clone["id"]}', headers=HEADERS).json()['disabled_tools'] == ['ssh_run']


def test_a_clone_copies_the_off_tools(agent):
    profile = agent.client.get(f'/api/agents/{agent.id}', headers=HEADERS).json()
    update(agent.client, profile, disabled_tools=['ssh_run'])
    clone = agent.client.post(f'/api/agents/{agent.id}/clone', headers=HEADERS).json()
    assert agent.client.get(f'/api/agents/{clone["id"]}', headers=HEADERS).json()['disabled_tools'] == ['ssh_run']


# Runs


def switch_off(client, agent_id, names):
    profile = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    response = update(client, profile, disabled_tools=names)
    assert response.status_code == 200, response.text


def test_a_chat_cannot_switch_an_agents_off_tool_back_on(agent):
    """Review Focus 2: the request omits ssh_run; it stays off. The request switches read_file off too."""
    switch_off(agent.client, agent.id, ['ssh_run'])
    for path in ('/preview', ''):
        response = start(agent.client, path, agent_id=agent.id, disabled_tools=['read_file'])
        assert response.status_code in (200, 201), response.text
    preview = start(agent.client, '/preview', agent_id=agent.id, disabled_tools=['read_file']).json()
    assert preview['config']['disabled_tools'] == ['read_file', 'ssh_run']
    assert not {'ssh_run', 'read_file'} & {tool['name'] for tool in preview['tools']}
    created = start(agent.client, agent_id=agent.id, disabled_tools=[]).json()
    detail = agent.client.get(f'/api/sessions/{created["id"]}', headers=HEADERS).json()
    assert detail['disabled_tools'] == ['ssh_run'] and detail['config']['disabled_tools'] == ['ssh_run']
    debug = agent.client.get(f'/api/sessions/{created["id"]}/debug', headers=HEADERS).json()
    assert 'ssh_run' not in {tool['name'] for tool in debug['tools']}
    options = agent.client.get(f'/api/sessions/options?agent_id={agent.id}', headers=HEADERS).json()
    assert options['agent']['disabled_tools'] == ['ssh_run']  # the chat settings show them locked


def test_an_unknown_tool_refuses_the_start(agent):
    for path in ('/preview', ''):
        response = start(agent.client, path, agent_id=agent.id, disabled_tools=['ssh_agent'])
        assert response.status_code == 400 and response.json()['error']['message'] == 'Unknown tool: ssh_agent.'


def body(world, **change) -> dict:
    return {
        'started_from': {'server_id': world.server['id']},
        'model_id': world.model,
        'system_prompt': 'You answer.',
        'message': 'Is it alive?',
        **change,
    }


def test_an_ad_hoc_run_disables_the_requests_tools(world):
    ssh_ready(world)
    off = ['ssh_run', 'shell_run']
    preview = world.client.post('/api/sessions/preview', headers=HEADERS, json=body(world, disabled_tools=off))
    assert preview.status_code == 200, preview.text
    assert [tool['name'] for tool in preview.json()['tools']] == [name for name in CATALOG if name not in off]
    created = world.client.post('/api/sessions', headers=HEADERS, json=body(world, disabled_tools=off))
    assert created.status_code == 201, created.text
    assert config_of(world, created.json()['id'])['disabled_tools'] == ['shell_run', 'ssh_run']
    detail = world.client.get(f'/api/sessions/{created.json()["id"]}', headers=HEADERS).json()
    assert detail['disabled_tools'] == ['shell_run', 'ssh_run']
    refused = world.client.post('/api/sessions', headers=HEADERS, json=body(world, disabled_tools=['nope']))
    assert refused.status_code == 400 and refused.json()['error']['message'] == 'Unknown tool: nope.'


def test_an_event_run_of_an_agent_never_gets_its_off_tool(tester):
    """Review Focus 1: a handler starts the saved agent; ssh_run is not offered and a forged call is refused."""
    t = tester
    switch_off(t.client, t.agent, ['ssh_run'])
    a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt=PROMPT)
    an_event(t.store)
    assert event_processor.run_once(t.store) is True
    [session] = event_sessions(t.store)
    pinned = session['agent_config']
    assert pinned['disabled_tools'] == ['ssh_run']
    assert 'ssh_run' not in offered_names(pinned)
    assert NOT_OFFERED.format(name='ssh_run') in forged('ssh_run', pinned)


# Migration


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def test_0135_adds_the_column_off_nothing_and_down_drops_it(store, tmp_path):
    from test_api import _client

    client = _client(tmp_path, repository_store=store)
    created = create(client, 'Payments')
    column = "SELECT 1 FROM information_schema.columns WHERE table_schema='ka' AND table_name='agents' AND column_name='disabled_tools'"
    with store.connect() as conn:
        run(conn, DOWN)
        assert conn.execute(column).fetchone() is None
        run(conn, UP)
        row = conn.execute('SELECT disabled_tools FROM ka.agents WHERE id=%s', (created['id'],)).fetchone()
        assert row['disabled_tools'] == []  # every existing agent keeps every tool
