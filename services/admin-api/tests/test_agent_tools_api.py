"""The Tools tab API: catalog, an agent's tools, replacing them with the save rules and the revision check; default
tools when a capability is switched on; the wizard's tools; the list summary."""
from uuid import uuid4

from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.tools.catalog import CATALOG, effective_tools
from knowledge_admin.tools_api import CLAUDE_CODE, GROUNDED, MCP_NOT_SELECTED, STALE, UNKNOWN
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_agent_resources import listed
from test_claude_code_agents import claude_code_agent
from test_profiles import create, update, wizard_payload
from test_repositories import HEADERS


def view(client, agent_id='operations') -> dict:
    response = client.get(f'/api/agents/{agent_id}/tools', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def put(client, agent_id, revision, tools):
    return client.put(f'/api/agents/{agent_id}/tools', headers=HEADERS, json={'expected_revision': revision, 'tools': tools})


def on(value) -> set[str]:
    return {tool['tool'] for tool in value['tools'] if tool['mode'] == 'on'}


def rows(store, agent_id) -> set[str]:
    with store.connect() as conn:
        return {row['tool'] for row in conn.execute("SELECT tool FROM ka.agent_tools WHERE agent_id=%s AND mode='on'", (agent_id,))}


def test_the_catalog_and_an_agents_tools(store, saved_agent):
    client, mcp = saved_agent[0], saved_agent[4]
    assert client.get('/api/tools').status_code == 403
    catalog = client.get('/api/tools', headers=HEADERS).json()['tools']
    assert [tool['name'] for tool in catalog] == list(CATALOG)
    assert set(catalog[0]) == {'name', 'group', 'label', 'summary', 'usage', 'needs'}
    current = view(client)
    names = [tool['tool'] for tool in current['tools']]
    assert names == [*CATALOG, f"mcp:{mcp['id']}:lookup"]
    lookup = current['tools'][-1]
    assert (lookup['label'], lookup['group'], lookup['mode'], lookup['available']) == ('Docs / lookup', 'mcp', 'on', True)
    read = current['tools'][1]
    assert read['default_usage'] == CATALOG['read_file'].usage and read['available'] and read['tab'] == 'git'
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET repositories_enabled=false,documents_enabled=false,ssh_enabled=false WHERE id='operations'")
    unmet = {tool['tool']: tool for tool in view(client)['tools']}
    assert not unmet['read_file']['available']
    assert unmet['read_file']['reason'] == 'Needs code projects, or documents without "Answer only from documents"'
    assert unmet['ssh_run']['reason'] == 'Needs SSH servers' and unmet['ssh_run']['tab'] == 'ssh'


def test_put_replaces_the_tools_and_enforces_the_save_rules(store, saved_agent):
    client, mcp = saved_agent[0], saved_agent[4]
    lookup = f"mcp:{mcp['id']}:lookup"
    revision = view(client)['revision']
    saved = put(client, 'operations', revision, [
        {'tool': 'read_file', 'mode': 'on', 'usage': 'Read narrowly.'}, {'tool': 'ssh_run', 'mode': 'on', 'usage': '  '},
        {'tool': lookup, 'mode': 'off', 'usage': None}, {'tool': 'list_files', 'mode': 'off', 'usage': None}])
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert on(body) == {'read_file', 'ssh_run'} and body['revision'] == revision + 1
    tools = {tool['tool']: tool for tool in body['tools']}
    assert tools['read_file']['usage'] == 'Read narrowly.' and tools['ssh_run']['usage'] is None  # blank: built-in
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert {e['tool'] for e in config['tools']['items'] if e['mode'] == 'on'} == {'read_file', 'ssh_run'}
    for tool, message in (('grep', UNKNOWN.format(tool='grep')),
                          (f'mcp:{uuid4()}:lookup', None), (f"mcp:{mcp['id']}:missing", None)):
        refused = put(client, 'operations', body['revision'], [{'tool': tool, 'mode': 'on', 'usage': None}])
        assert refused.status_code == 400
        assert refused.json()['error']['message'] == (message or MCP_NOT_SELECTED.format(tool=tool))
    assert put(client, 'operations', body['revision'], [{'tool': 'read_file', 'mode': 'per_run', 'usage': None}]).status_code == 400  # validation: 400 in this app
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_enabled=true,documents_grounded=true WHERE id='operations'")
    grounded = put(client, 'operations', body['revision'], [{'tool': 'search_documents', 'mode': 'on', 'usage': None}])
    assert grounded.status_code == 400 and grounded.json()['error']['message'] == GROUNDED
    assert put(client, 'operations', body['revision'], [{'tool': 'search_documents', 'mode': 'on', 'usage': None},
               {'tool': 'report_not_covered', 'mode': 'on', 'usage': None}]).status_code == 200


def test_a_stale_revision_is_refused(store, saved_agent):
    client = saved_agent[0]
    profile = client.get('/api/agents/operations', headers=HEADERS).json()
    assert put(client, 'operations', profile['revision'], []).status_code == 200
    stale = put(client, 'operations', profile['revision'], [])
    assert stale.status_code == 409 and stale.json()['error']['message'] == STALE
    assert update(client, profile).status_code == 409  # General must reload the new revision


def test_claude_code_agents_keep_their_claude_code_tools(store, saved_agent):
    client = saved_agent[0]
    writer = claude_code_agent(client)
    current = view(client, writer)
    assert current['runtime'] == 'claude_code' and current['tools'] == []
    assert current['claude_code_tools'] == ['Read', 'Grep', 'Glob', 'Bash']
    refused = put(client, writer, current['revision'], [{'tool': 'read_file', 'mode': 'on', 'usage': None}])
    assert refused.status_code == 400 and refused.json()['error']['message'] == CLAUDE_CODE


def test_switching_a_capability_on_switches_its_default_tools_on(store, saved_agent):
    client = saved_agent[0]
    blank = create(client, 'Blank')
    assert rows(store, blank['id']) == set()
    code = update(client, blank, repositories_enabled=True).json()
    defaults = {'list_files', 'read_file', 'search_files', 'shell_run', 'code_search', 'ast_grep', 'code_graph'}
    assert rows(store, blank['id']) == defaults
    only = put(client, blank['id'], code['revision'], [{'tool': 'read_file', 'mode': 'on', 'usage': None}]).json()
    profile = client.get(f"/api/agents/{blank['id']}", headers=HEADERS).json()
    assert profile['revision'] == only['revision']
    documents = update(client, profile, documents_enabled=True).json()
    assert rows(store, blank['id']) == {'read_file', 'search_documents', 'read_document'}  # code keeps its file tools
    grounded = update(client, documents, documents_grounded=True).json()
    assert 'report_not_covered' in rows(store, blank['id'])
    ssh = update(client, grounded, ssh_enabled=True).json()
    assert 'ssh_run' in rows(store, blank['id'])
    off = update(client, ssh, repositories_enabled=False).json()
    assert 'read_file' in rows(store, blank['id'])  # switching off keeps the rows
    update(client, off, repositories_enabled=True)  # none of code's own tools is on: its defaults come back
    assert rows(store, blank['id']) >= defaults


def test_a_claude_code_agent_switched_to_native_gets_todays_tools(store, saved_agent):
    client = saved_agent[0]
    writer = claude_code_agent(client, 'Writer', repositories_enabled=True)
    assert rows(store, writer) == set()
    profile = client.get(f'/api/agents/{writer}', headers=HEADERS).json()
    assert update(client, profile, runtime='openai').status_code == 200
    assert rows(store, writer) == {'list_files', 'read_file', 'search_files', 'shell_run', 'code_search', 'ast_grep', 'code_graph'}


def test_the_wizard_saves_the_chosen_or_implied_tools(store, saved_agent):
    client = saved_agent[0]
    options = client.get('/api/agents/wizard/options', headers=HEADERS).json()
    assert [tool['name'] for tool in options['tools']] == list(CATALOG)
    implied = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Implied', ssh_enabled=True)).json()
    assert rows(store, implied['id']) == {'ssh_run'}
    chosen = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload(
        'Chosen', repositories_enabled=True, tools=['read_file', 'code_graph'])).json()
    assert rows(store, chosen['id']) == {'read_file', 'code_graph'}
    refused = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Odd', tools=['grep']))
    assert refused.status_code == 400 and refused.json()['error']['message'] == UNKNOWN.format(tool='grep')
    writer = claude_code_agent(client, 'Writer', repositories_enabled=True)
    assert rows(store, writer) == set()
    empty = claude_code_agent(client, 'Empty', tools=[])  # the wizard may send no tools for Claude Code
    assert rows(store, empty) == set()
    some = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload(
        'Some', description='', when_to_use='', runtime='claude_code', tools=['read_file']))
    assert some.status_code == 400 and some.json()['error']['message'] == CLAUDE_CODE


def test_a_choice_may_carry_subagent_allowed_which_is_ignored(store, saved_agent):
    client = saved_agent[0]
    revision = view(client)['revision']
    saved = put(client, 'operations', revision, [{'tool': 'read_file', 'mode': 'on', 'usage': None,
                                                  'subagent_allowed': False}])
    assert saved.status_code == 200, saved.text
    with store.connect() as conn:
        assert conn.execute("SELECT subagent_allowed FROM ka.agent_tools WHERE agent_id='operations' AND tool='read_file'"
                            ).fetchone()['subagent_allowed'] is True


def test_the_list_summarises_the_tools_that_run(store, saved_agent):
    client = saved_agent[0]
    revision = view(client)['revision']
    put(client, 'operations', revision, [{'tool': 'read_file', 'mode': 'on', 'usage': None},
                                         {'tool': 'ssh_run', 'mode': 'on', 'usage': None}])
    summary = listed(client)['operations']['tools_summary']
    assert summary == {'on': 3, 'per_run': 0, 'groups': {'code': 1, 'documents': 0, 'servers': 1, 'mcp': 1, 'agents': 0}}
    writer = claude_code_agent(client)
    assert listed(client)[writer]['tools_summary'] == {'claude_code_tools': ['Read', 'Grep', 'Glob', 'Bash']}


def offered(store, agent_id, ssh_servers=()) -> set[str]:
    config = get_agent_config(agent_id, conninfo=store.conninfo)
    return {spec.name for spec, _ in effective_tools(config, list(ssh_servers))}


def test_new_agents_and_switched_on_capabilities_run_with_their_tools(store, saved_agent):
    """After 0078 tools come only from rows: the wizard and a profile save must leave rows the run actually offers."""
    client = saved_agent[0]
    fresh = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload(
        'Fresh', repositories_enabled=True, ssh_enabled=True)).json()
    assert offered(store, fresh['id'], [{'id': 'server'}]) == {
        'list_files', 'read_file', 'search_files', 'shell_run', 'code_search', 'ast_grep', 'ssh_run'}  # no graph yet
    blank = create(client, 'Blank')
    assert offered(store, blank['id']) == set()
    documents = update(client, blank, documents_enabled=True).json()
    assert offered(store, blank['id']) == {'list_files', 'read_file', 'search_files', 'shell_run',
                                           'search_documents', 'read_document'}
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
