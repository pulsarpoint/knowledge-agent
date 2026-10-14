"""May start subagents: the run_subagent row behind the General switch, the Tools tab's rules for it and for
subagent_allowed, the wizard and clones."""
import asyncio
import json

from agents.tool_context import ToolContext

from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.knowledge_agents.subagents import UNAVAILABLE
from knowledge_admin.agents.profiles_api import ProfileInput
from knowledge_admin.tools.catalog import CATALOG, SUBAGENT
from knowledge_admin.tools.sdk import function_tool
from knowledge_admin.agents.tools_api import SUBAGENT_SWITCH, SUBAGENTS_OFF, UNKNOWN
from test_agent_tools_api import put, view
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_profiles import create, update, wizard_payload
from test_repositories import HEADERS


def profile(client, agent_id):
    return client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()


def row(store, agent_id, tool=SUBAGENT):
    with store.connect() as conn:
        return conn.execute('SELECT mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s AND tool=%s',
                            (agent_id, tool)).fetchone()


def test_the_general_switch_is_the_run_subagent_row(store, saved_agent):
    client = saved_agent[0]
    agent = create(client, 'Lead')
    assert agent['subagents'] is False and row(store, agent['id']) is None
    assert SUBAGENT not in [tool['tool'] for tool in view(client, agent['id'])['tools']]
    on = update(client, agent, subagents=True)
    assert on.status_code == 200, on.text
    assert on.json()['subagents'] is True and on.json()['revision'] == agent['revision'] + 1
    assert row(store, agent['id']) == {'mode': 'on', 'usage': None, 'subagent_allowed': False}
    listed = {tool['tool']: tool for tool in view(client, agent['id'])['tools']}
    assert (listed[SUBAGENT]['group'], listed[SUBAGENT]['label'], listed[SUBAGENT]['mode']) == ('agents', 'Start subagents', 'on')
    assert listed[SUBAGENT]['default_usage'] == CATALOG[SUBAGENT].usage
    items = {entry['tool']: entry for entry in get_agent_config(agent['id'], conninfo=store.conninfo)['tools']['items']}
    assert (items[SUBAGENT]['mode'], items[SUBAGENT]['subagent_allowed']) == ('on', False)
    current = on.json()
    body = {field: current[field] for field in ProfileInput.model_fields if field != 'subagents'}
    kept = client.put(f"/api/agents/{agent['id']}", headers=HEADERS, json={**body, 'name': 'Lead 2'})
    assert kept.status_code == 200 and kept.json()['subagents'] is True  # a client that does not send it keeps it
    off = update(client, kept.json(), subagents=False).json()
    assert off['subagents'] is False and row(store, agent['id']) is None


def test_the_tools_tab_keeps_run_subagent_on_and_stores_subagent_allowed(store, saved_agent):
    client, mcp = saved_agent[0], saved_agent[4]
    agent = create(client, 'Lead')
    refused = put(client, agent['id'], view(client, agent['id'])['revision'], [{'tool': SUBAGENT, 'mode': 'on', 'usage': None}])
    assert refused.status_code == 400 and refused.json()['error']['message'] == SUBAGENTS_OFF
    on = update(client, profile(client, agent['id']), subagents=True).json()
    saved = put(client, agent['id'], on['revision'], [
        {'tool': SUBAGENT, 'mode': 'off', 'usage': 'Split the work by project.'},
        {'tool': 'read_file', 'mode': 'on', 'usage': None, 'subagent_allowed': False},
        {'tool': 'list_files', 'mode': 'on', 'usage': None}])
    assert saved.status_code == 200, saved.text
    assert row(store, agent['id']) == {'mode': 'on', 'usage': 'Split the work by project.', 'subagent_allowed': False}
    assert row(store, agent['id'], 'read_file')['subagent_allowed'] is False
    assert row(store, agent['id'], 'list_files')['subagent_allowed'] is True
    tools = {tool['tool']: tool for tool in saved.json()['tools']}
    assert tools['read_file']['subagent_allowed'] is False and tools[SUBAGENT]['usage'] == 'Split the work by project.'
    items = {entry['tool']: entry for entry in get_agent_config(agent['id'], conninfo=store.conninfo)['tools']['items']}
    assert items['read_file']['subagent_allowed'] is False and items['list_files']['subagent_allowed'] is True
    again = put(client, agent['id'], saved.json()['revision'], [{'tool': 'read_file', 'mode': 'on', 'usage': None}])
    assert again.status_code == 200
    assert row(store, agent['id'])['usage'] == 'Split the work by project.'  # only the switch removes it
    lookup = f"mcp:{mcp['id']}:lookup"
    revision = view(client)['revision']
    hidden = put(client, 'operations', revision, [{'tool': lookup, 'mode': 'on', 'usage': None, 'subagent_allowed': False}])
    assert hidden.status_code == 200 and row(store, 'operations', lookup)['subagent_allowed'] is False
    put(client, 'operations', hidden.json()['revision'], [{'tool': lookup, 'mode': 'on', 'usage': None}])
    assert row(store, 'operations', lookup) is None  # on, default text, allowed: an MCP tool's default has no row


def test_the_wizard_switches_subagents_on_with_the_tools_a_subagent_may_use(store, saved_agent):
    client = saved_agent[0]
    created = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload(
        'Lead', repositories_enabled=True, subagents=True, subagent_blocked=['shell_run']))
    assert created.status_code == 201, created.text
    agent_id = created.json()['id']
    assert row(store, agent_id) == {'mode': 'on', 'usage': None, 'subagent_allowed': False}
    assert row(store, agent_id, 'shell_run')['subagent_allowed'] is False
    assert row(store, agent_id, 'read_file')['subagent_allowed'] is True
    assert profile(client, agent_id)['subagents'] is True
    for body, message in ((wizard_payload('Odd', tools=[SUBAGENT]), SUBAGENT_SWITCH),
                          (wizard_payload('Odder', subagent_blocked=['grep']), UNKNOWN.format(tool='grep'))):
        refused = client.post('/api/agents/wizard', headers=HEADERS, json=body)
        assert refused.status_code == 400 and refused.json()['error']['message'] == message


def test_a_clone_copies_the_switch_and_what_subagents_may_use(store, saved_agent):
    client = saved_agent[0]
    agent = create(client, 'Lead')
    on = update(client, agent, subagents=True, repositories_enabled=True).json()
    put(client, agent['id'], on['revision'], [{'tool': SUBAGENT, 'mode': 'on', 'usage': 'Split by project.'},
                                              {'tool': 'read_file', 'mode': 'on', 'usage': None, 'subagent_allowed': False}])
    copied = client.post(f"/api/agents/{agent['id']}/clone", headers=HEADERS)
    assert copied.status_code == 201, copied.text
    clone = copied.json()['id']
    assert profile(client, clone)['subagents'] is True
    assert row(store, clone) == {'mode': 'on', 'usage': 'Split by project.', 'subagent_allowed': False}
    assert row(store, clone, 'read_file')['subagent_allowed'] is False


def test_run_subagent_outside_a_session_is_a_tool_error():
    tool = function_tool(CATALOG[SUBAGENT], CATALOG[SUBAGENT].usage)
    context = {'config': {}, 'credentials': {}, 'workspace': None, 'shell_enabled': False,
               'tools': frozenset({SUBAGENT})}
    payload = json.dumps({'task': 'Read the worker', 'tools': ['read_file'], 'resources': None})
    wrapped = ToolContext(context=context, tool_name=SUBAGENT, tool_call_id='s1', tool_arguments=payload)
    result = asyncio.run(tool.on_invoke_tool(wrapped, payload))
    assert result.startswith('An error occurred while running the tool.') and UNAVAILABLE in result

