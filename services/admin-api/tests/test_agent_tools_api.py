"""The Tools tab API: catalog, an agent's tools, replacing them with the save rules and the revision check; the
wizard's tools; the list summary. Runs no longer read these rows (every run gets every tool, test_every_tool.py)."""

from uuid import uuid4

from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.agents.tools_api import MCP_NOT_SELECTED, STALE, UNKNOWN
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_agent_resources import listed
from test_profiles import create, update, wizard_payload
from test_repositories import HEADERS


def view(client, agent_id='operations') -> dict:
    response = client.get(f'/api/agents/{agent_id}/tools', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def put(client, agent_id, revision, tools):
    return client.put(
        f'/api/agents/{agent_id}/tools', headers=HEADERS, json={'expected_revision': revision, 'tools': tools}
    )


def on(value) -> set[str]:
    return {tool['tool'] for tool in value['tools'] if tool['mode'] == 'on'}


def rows(store, agent_id) -> set[str]:
    with store.connect() as conn:
        return {
            row['tool']
            for row in conn.execute("SELECT tool FROM ka.agent_tools WHERE agent_id=%s AND mode='on'", (agent_id,))
        }


def access_of(*references) -> dict:
    return {'resources': [{'type': t, 'id': str(i)} for t, i in references], 'roles': {'content_editor': False}}


def test_the_catalog_and_an_agents_tools(store, saved_agent):
    client, mcp = saved_agent[0], saved_agent[4]
    assert client.get('/api/tools').status_code == 403
    catalog = client.get('/api/tools', headers=HEADERS).json()['tools']
    assert [tool['name'] for tool in catalog] == list(CATALOG)
    assert set(catalog[0]) == {'name', 'group', 'label', 'summary', 'usage'}
    current = view(client)
    names = [tool['tool'] for tool in current['tools']]
    assert names == [
        *(name for name in CATALOG if name != 'run_subagent'),
        f'mcp:{mcp["id"]}:lookup',
    ]  # run_subagent: only with its switch
    lookup = current['tools'][-1]
    assert (lookup['label'], lookup['group'], lookup['mode'], lookup['available']) == (
        'Docs / lookup',
        'mcp',
        'on',
        True,
    )
    read = current['tools'][1]
    assert read['default_usage'] == CATALOG['read_file'].usage and read['available'] and read['reason'] is None


def test_put_replaces_the_tools_and_enforces_the_save_rules(store, saved_agent):
    client, mcp = saved_agent[0], saved_agent[4]
    lookup = f'mcp:{mcp["id"]}:lookup'
    revision = view(client)['revision']
    saved = put(
        client,
        'operations',
        revision,
        [
            {'tool': 'read_file', 'mode': 'on', 'usage': 'Read narrowly.'},
            {'tool': 'ssh_run', 'mode': 'on', 'usage': '  '},
            {'tool': lookup, 'mode': 'off', 'usage': None},
            {'tool': 'list_files', 'mode': 'off', 'usage': None},
        ],
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert on(body) == {'read_file', 'ssh_run'} and body['revision'] == revision + 1
    tools = {tool['tool']: tool for tool in body['tools']}
    assert tools['read_file']['usage'] == 'Read narrowly.' and tools['ssh_run']['usage'] is None  # blank: built-in
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert {e['tool'] for e in config['tools']['items'] if e['mode'] == 'on'} == {'read_file', 'ssh_run'}
    for tool, message in (
        ('grep', UNKNOWN.format(tool='grep')),
        (f'mcp:{uuid4()}:lookup', None),
        (f'mcp:{mcp["id"]}:missing', None),
    ):
        refused = put(client, 'operations', body['revision'], [{'tool': tool, 'mode': 'on', 'usage': None}])
        assert refused.status_code == 400
        assert refused.json()['error']['message'] == (message or MCP_NOT_SELECTED.format(tool=tool))
    assert (
        put(
            client, 'operations', body['revision'], [{'tool': 'read_file', 'mode': 'per_run', 'usage': None}]
        ).status_code
        == 400
    )  # validation: 400 in this app


def test_a_stale_revision_is_refused(store, saved_agent):
    client = saved_agent[0]
    profile = client.get('/api/agents/operations', headers=HEADERS).json()
    assert put(client, 'operations', profile['revision'], []).status_code == 200
    stale = put(client, 'operations', profile['revision'], [])
    assert stale.status_code == 409 and stale.json()['error']['message'] == STALE
    assert update(client, profile).status_code == 409  # General must reload the new revision


def test_the_wizard_saves_only_the_chosen_tools(store, saved_agent):
    client, server, repo = saved_agent[0], saved_agent[1], saved_agent[5]
    code = access_of(('code_project', repo['id']))
    options = client.get('/api/agents/wizard/options', headers=HEADERS).json()
    assert [tool['name'] for tool in options['tools']] == list(CATALOG)
    implied = client.post(
        '/api/agents/wizard',
        headers=HEADERS,
        json=wizard_payload('Implied', access=access_of(('server', server['id']))),
    ).json()
    assert rows(store, implied['id']) == set()  # no rows implied: every run gets every tool
    chosen = client.post(
        '/api/agents/wizard',
        headers=HEADERS,
        json=wizard_payload('Chosen', access=code, tools=['read_file', 'code_graph']),
    ).json()
    assert rows(store, chosen['id']) == {'read_file', 'code_graph'}
    refused = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Odd', tools=['grep']))
    assert refused.status_code == 400 and refused.json()['error']['message'] == UNKNOWN.format(tool='grep')
    empty = client.post(
        '/api/agents/wizard', headers=HEADERS, json=wizard_payload('Empty', access=code, tools=[])
    ).json()
    assert rows(store, empty['id']) == set()  # an explicit empty selection is kept


def test_the_list_summarises_the_tools_that_run(store, saved_agent):
    client = saved_agent[0]
    summary = listed(client)['operations']['tools_summary']
    groups = {'code': 0, 'documents': 0, 'servers': 0, 'inventory': 0, 'memory': 0, 'mcp': 1, 'agents': 0}
    for spec in CATALOG.values():
        groups[spec.group] += 1
    assert summary == {'on': len(CATALOG) + 1, 'per_run': 0, 'groups': groups}


def test_saving_tools_is_audited_against_the_agent(store, saved_agent):
    client = saved_agent[0]
    blank = create(client, 'Audited')
    assert put(client, blank['id'], view(client, blank['id'])['revision'], []).status_code == 200
    assert put(client, 'operations', view(client)['revision'], []).status_code == 200  # not a UUID: no resource_id
    with store.connect() as conn:
        audited = conn.execute("""SELECT resource_id::text AS resource_id,details->>'agent_id' AS agent FROM ka.audit_events
            WHERE action='agent.tools.updated' ORDER BY created_at""").fetchall()
    assert [(row['agent'], row['resource_id']) for row in audited] == [(blank['id'], blank['id']), ('operations', None)]
