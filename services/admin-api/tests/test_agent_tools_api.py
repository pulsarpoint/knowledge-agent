"""The Tools tab API: catalog, an agent's tools, replacing them with the save rules and the revision check; default
tools when a capability is switched on; the wizard's tools; the list summary."""

from uuid import uuid4

from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.tools.catalog import CATALOG, RUN_ONLY_NEEDS, effective_tools
from knowledge_admin.agents.tools_api import DISCOVERY_ONLY, GROUNDED, MCP_NOT_SELECTED, STALE, UNKNOWN
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


CODE_TOOLS = {'list_files', 'read_file', 'search_files', 'shell_run', 'code_search', 'ast_grep', 'code_graph'}


def usage_of(store, agent_id, tool):
    with store.connect() as conn:
        return conn.execute(
            'SELECT usage FROM ka.agent_tools WHERE agent_id=%s AND tool=%s', (agent_id, tool)
        ).fetchone()['usage']


def profile_of(client, agent_id):
    return client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()


def test_the_catalog_and_an_agents_tools(store, saved_agent):
    client, mcp = saved_agent[0], saved_agent[4]
    assert client.get('/api/tools').status_code == 403
    catalog = client.get('/api/tools', headers=HEADERS).json()['tools']
    assert [tool['name'] for tool in catalog] == [
        name for name, spec in CATALOG.items() if spec.needs not in RUN_ONLY_NEEDS
    ]
    assert set(catalog[0]) == {'name', 'group', 'label', 'summary', 'usage', 'needs'}
    current = view(client)
    names = [tool['tool'] for tool in current['tools']]
    assert names == [
        *(name for name, spec in CATALOG.items() if name != 'run_subagent' and spec.needs not in RUN_ONLY_NEEDS),
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
    assert read['default_usage'] == CATALOG['read_file'].usage and read['available'] and read['tab'] == 'git'
    with store.connect() as conn:
        conn.execute(
            "UPDATE ka.agents SET repositories_enabled=false,documents_enabled=false,ssh_enabled=false WHERE id='operations'"
        )
    unmet = {tool['tool']: tool for tool in view(client)['tools']}
    assert not unmet['read_file']['available']
    assert unmet['read_file']['reason'] == 'Needs code projects, or documents without "Answer only from documents"'
    assert unmet['ssh_run']['reason'] == 'Needs SSH servers' and unmet['ssh_run']['tab'] == 'ssh'


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
    hidden = put(client, 'operations', body['revision'], [{'tool': 'propose_service', 'mode': 'on', 'usage': None}])
    assert hidden.status_code == 400 and hidden.json()['error']['message'] == DISCOVERY_ONLY
    revision_only = put(
        client, 'operations', body['revision'], [{'tool': 'save_document', 'mode': 'on', 'usage': None}]
    )
    assert revision_only.status_code == 400 and revision_only.json()['error']['message'] == DISCOVERY_ONLY
    assert 'propose_service' not in tools
    assert (
        put(
            client, 'operations', body['revision'], [{'tool': 'read_file', 'mode': 'per_run', 'usage': None}]
        ).status_code
        == 400
    )  # validation: 400 in this app
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_enabled=true,documents_grounded=true WHERE id='operations'")
    grounded = put(client, 'operations', body['revision'], [{'tool': 'search_documents', 'mode': 'on', 'usage': None}])
    assert grounded.status_code == 400 and grounded.json()['error']['message'] == GROUNDED
    assert (
        put(
            client,
            'operations',
            body['revision'],
            [
                {'tool': 'search_documents', 'mode': 'on', 'usage': None},
                {'tool': 'report_not_covered', 'mode': 'on', 'usage': None},
            ],
        ).status_code
        == 200
    )


def test_a_stale_revision_is_refused(store, saved_agent):
    client = saved_agent[0]
    profile = client.get('/api/agents/operations', headers=HEADERS).json()
    assert put(client, 'operations', profile['revision'], []).status_code == 200
    stale = put(client, 'operations', profile['revision'], [])
    assert stale.status_code == 409 and stale.json()['error']['message'] == STALE
    assert update(client, profile).status_code == 409  # General must reload the new revision


def test_switching_a_capability_on_switches_all_its_tools_on(store, saved_agent):
    client = saved_agent[0]
    blank = create(client, 'Blank')
    assert rows(store, blank['id']) == set()
    code = update(client, blank, repositories_enabled=True).json()
    assert rows(store, blank['id']) == CODE_TOOLS
    only = put(
        client,
        blank['id'],
        code['revision'],
        [
            {'tool': 'read_file', 'mode': 'on', 'usage': 'Read narrowly.'},
            *({'tool': name, 'mode': 'off', 'usage': None} for name in sorted(CODE_TOOLS - {'read_file'})),
        ],
    ).json()
    profile = profile_of(client, blank['id'])
    assert profile['revision'] == only['revision']
    documents = update(client, profile, documents_enabled=True).json()
    assert rows(store, blank['id']) == {'read_file', 'search_documents', 'read_document'}  # code keeps its choices
    grounded = update(client, documents, documents_grounded=True).json()
    assert 'report_not_covered' in rows(store, blank['id'])
    ssh = update(client, grounded, ssh_enabled=True).json()
    assert 'ssh_run' in rows(store, blank['id'])
    off = update(client, ssh, repositories_enabled=False).json()
    assert 'read_file' in rows(store, blank['id']) and 'shell_run' not in rows(store, blank['id'])  # off keeps rows
    update(client, off, repositories_enabled=True)  # on again: all of its tools, replacing the off rows
    assert rows(store, blank['id']) >= CODE_TOOLS
    assert usage_of(store, blank['id'], 'read_file') == 'Read narrowly.'  # switching on keeps edited usage


def test_a_grounded_documents_only_agent_gets_no_file_tools_on_switch_on(store, saved_agent):
    """The file tools come with documents only without "Answer only from documents" (their need, files)."""
    client = saved_agent[0]
    blank = create(client, 'Grounded')
    update(client, blank, documents_enabled=True, documents_grounded=True)
    assert rows(store, blank['id']) == {'search_documents', 'read_document', 'report_not_covered'}


def test_a_per_run_agent_gets_all_tools_of_its_enabled_capabilities(store, saved_agent):
    client = saved_agent[0]
    role = client.post(
        '/api/agents/wizard',
        headers=HEADERS,
        json=wizard_payload('Role', resources='per_run', repositories_enabled=True, documents_enabled=True),
    ).json()
    assert rows(store, role['id']) == CODE_TOOLS | {'search_documents', 'read_document'}
    profile = profile_of(client, role['id'])
    trimmed = put(
        client,
        role['id'],
        profile['revision'],
        [
            {'tool': name, 'mode': 'off' if name == 'shell_run' else 'on', 'usage': None}
            for name in sorted(CODE_TOOLS | {'search_documents', 'read_document'})
        ],
    ).json()
    assert 'shell_run' not in rows(store, role['id'])
    ssh = update(client, profile_of(client, role['id']), ssh_enabled=True).json()  # a capability on while per run
    assert rows(store, role['id']) == CODE_TOOLS | {'search_documents', 'read_document', 'ssh_run'}
    assert ssh['revision'] == trimmed['revision'] + 1
    fixed = create(client, 'Fixed first')
    coded = update(client, fixed, repositories_enabled=True).json()
    put(
        client,
        fixed['id'],
        coded['revision'],
        [
            {'tool': name, 'mode': 'off' if name == 'code_search' else 'on', 'usage': None}
            for name in sorted(CODE_TOOLS)
        ],
    )
    update(client, profile_of(client, fixed['id']), resources='per_run')  # switched to per run
    assert rows(store, fixed['id']) == CODE_TOOLS


def test_a_save_that_switches_nothing_on_leaves_the_tools_alone(store, saved_agent):
    """Existing agents are not changed: only switching a capability on (or to per run) switches tools on."""
    client = saved_agent[0]
    for resources in ('fixed', 'per_run'):
        agent = client.post(
            '/api/agents/wizard',
            headers=HEADERS,
            json=wizard_payload(f'Kept {resources}', resources=resources, repositories_enabled=True),
        ).json()
        profile = profile_of(client, agent['id'])
        put(client, agent['id'], profile['revision'], [{'tool': 'read_file', 'mode': 'off', 'usage': None}])
        update(client, profile_of(client, agent['id']), name=f'Renamed {resources}')
        assert rows(store, agent['id']) == set(), resources


def test_the_wizard_saves_the_chosen_or_implied_tools(store, saved_agent):
    client = saved_agent[0]
    options = client.get('/api/agents/wizard/options', headers=HEADERS).json()
    assert [tool['name'] for tool in options['tools']] == [
        name for name, spec in CATALOG.items() if spec.needs not in RUN_ONLY_NEEDS
    ]
    implied = client.post(
        '/api/agents/wizard', headers=HEADERS, json=wizard_payload('Implied', ssh_enabled=True)
    ).json()
    assert rows(store, implied['id']) == {'ssh_run'}
    chosen = client.post(
        '/api/agents/wizard',
        headers=HEADERS,
        json=wizard_payload('Chosen', repositories_enabled=True, tools=['read_file', 'code_graph']),
    ).json()
    assert rows(store, chosen['id']) == {'read_file', 'code_graph'}
    refused = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Odd', tools=['grep']))
    assert refused.status_code == 400 and refused.json()['error']['message'] == UNKNOWN.format(tool='grep')
    only = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Hidden', tools=['propose_document']))
    assert only.status_code == 400 and only.json()['error']['message'] == DISCOVERY_ONLY
    revision_only = client.post(
        '/api/agents/wizard', headers=HEADERS, json=wizard_payload('Doc', tools=['save_document'])
    )
    assert revision_only.status_code == 400 and revision_only.json()['error']['message'] == DISCOVERY_ONLY
    empty = client.post(
        '/api/agents/wizard', headers=HEADERS, json=wizard_payload('Empty', repositories_enabled=True, tools=[])
    ).json()
    assert rows(store, empty['id']) == set()  # an explicit empty selection is kept


def test_the_list_summarises_the_tools_that_run(store, saved_agent):
    client = saved_agent[0]
    revision = view(client)['revision']
    put(
        client,
        'operations',
        revision,
        [{'tool': 'read_file', 'mode': 'on', 'usage': None}, {'tool': 'ssh_run', 'mode': 'on', 'usage': None}],
    )
    summary = listed(client)['operations']['tools_summary']
    assert summary == {
        'on': 3,
        'per_run': 0,
        'groups': {'code': 1, 'documents': 0, 'servers': 1, 'inventory': 0, 'mcp': 1, 'agents': 0},
    }


def offered(store, agent_id, ssh_servers=()) -> set[str]:
    config = get_agent_config(agent_id, conninfo=store.conninfo)
    return {spec.name for spec, _ in effective_tools(config, list(ssh_servers))}


def test_new_agents_and_switched_on_capabilities_run_with_their_tools(store, saved_agent):
    """After 0078 tools come only from rows: the wizard and a profile save must leave rows the run actually offers."""
    client = saved_agent[0]
    fresh = client.post(
        '/api/agents/wizard', headers=HEADERS, json=wizard_payload('Fresh', repositories_enabled=True, ssh_enabled=True)
    ).json()
    assert offered(store, fresh['id'], [{'id': 'server'}]) == {
        'list_files',
        'read_file',
        'search_files',
        'shell_run',
        'code_search',
        'ast_grep',
        'ssh_run',
    }  # no graph yet
    blank = create(client, 'Blank')
    assert offered(store, blank['id']) == set()
    documents = update(client, blank, documents_enabled=True).json()
    assert offered(store, blank['id']) == {
        'list_files',
        'read_file',
        'search_files',
        'shell_run',
        'search_documents',
        'read_document',
    }
    update(client, documents, documents_grounded=True)
    assert offered(store, blank['id']) == {'search_documents', 'read_document', 'report_not_covered'}


def test_saving_tools_is_audited_against_the_agent(store, saved_agent):
    client = saved_agent[0]
    blank = create(client, 'Audited')
    assert put(client, blank['id'], view(client, blank['id'])['revision'], []).status_code == 200
    assert put(client, 'operations', view(client)['revision'], []).status_code == 200  # not a UUID: no resource_id
    with store.connect() as conn:
        audited = conn.execute("""SELECT resource_id::text AS resource_id,details->>'agent_id' AS agent FROM ka.audit_events
            WHERE action='agent.tools.updated' ORDER BY created_at""").fetchall()
    assert [(row['agent'], row['resource_id']) for row in audited] == [(blank['id'], blank['id']), ('operations', None)]
