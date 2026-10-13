"""Experts as tools: which experts a caller is offered, their tool names and descriptions, pinning into Test and
Generate documents sessions, and the Tools tab's Experts group."""
from test_agent_tools_api import put, view
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_generations import sessions, start  # noqa: F401  (fixtures)
from test_test_sessions import test_start
from test_profiles import create, update
from test_repositories import HEADERS

from knowledge_admin.knowledge_agents.experts import offered, slug
from knowledge_admin.tools.catalog import SUBAGENT


def expert(client, name, when='When it fits.', **changes):
    response = update(client, create(client, name), expert=True, when_to_use=when, **changes)
    assert response.status_code == 200, response.text
    return response.json()


def model_of(saved_agent) -> str:
    """An enabled Responses model (the fixture's own test-claude model does not fit agents)."""
    return saved_agent[0].post('/api/llm/models', headers=HEADERS, json={
        'name': 'GPT', 'provider': 'openai', 'api_format': 'responses', 'model': 'gpt-5',
        'base_url': 'http://model.test/v1', 'api_key': 'expert-model-secret'}).json()['id']


def caller(store, saved_agent, model, name='Caller'):
    """An enabled agent with a model, as the Test tab needs it."""
    agent = create(saved_agent[0], name)
    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=%s,enabled=true WHERE id=%s', (model, agent['id']))
    return agent


def test_tool_names_are_slugs_and_collisions_get_a_number(store, saved_agent):
    assert slug('Payments API') == 'payments_api'
    assert slug('  Ünïcode — only!! ') == 'n_code_only'
    assert slug('***') == ''
    assert len(slug('x' * 80)) == 50
    client, model = saved_agent[0], model_of(saved_agent)
    first = expert(client, 'Payments API', model_id=model)
    second = expert(client, 'payments-api', model_id=model)
    with store.connect() as conn:
        tools = {entry['id']: entry['tool'] for entry in offered(conn, None)}
    assert tools[first['id']] == 'ask_payments_api' and tools[second['id']] == 'ask_payments_api_2'


def test_only_enabled_experts_with_a_model_other_than_the_caller_and_not_switched_off_are_offered(store, saved_agent):
    client, model = saved_agent[0], model_of(saved_agent)
    me = update(client, caller(store, saved_agent, model), expert=True, when_to_use='Me.', model_id=model).json()
    expert(client, 'No model')
    disabled = expert(client, 'Disabled', model_id=model)
    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (disabled['id'],))
    update(client, create(client, 'Plain'), model_id=model)
    hidden = expert(client, 'Hidden', model_id=model)
    fixed = expert(client, 'Fixed one', 'Fixed question.', model_id=model)
    per_run = expert(client, 'Per run', 'Any project.', model_id=model, resources='per_run',
                     repositories_enabled=True, services_enabled=True)
    with store.connect() as conn:
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'off',false)",
                     (me['id'], f"expert:{hidden['id']}"))
        found = {entry['id']: entry for entry in offered(conn, me['id'])}
        everyone = {entry['id'] for entry in offered(conn, None)}
    assert set(found) == {fixed['id'], per_run['id']}
    assert {hidden['id'], me['id']} <= everyone and disabled['id'] not in everyone
    assert found[fixed['id']]['description'] == 'Ask Fixed one, an expert agent. Fixed question. It uses its own resources.'
    assert found[fixed['id']]['accepts'] == [] and found[fixed['id']]['resources'] == 'fixed'
    assert found[per_run['id']]['description'] == (
        'Ask Per run, an expert agent. Any project. It works only on the resources you pass '
        '(it accepts: code projects, services).')
    assert found[per_run['id']]['accepts'] == ['code_project_ids', 'service_ids']
    assert found[per_run['id']]['tool'] == 'ask_per_run'


def test_a_test_session_pins_the_offered_experts(store, saved_agent):
    client, model = saved_agent[0], model_of(saved_agent)
    me = caller(store, saved_agent, model)
    first = expert(client, 'Payments', model_id=model)
    response = client.post('/api/sessions', headers=HEADERS, json=test_start(client, me['id'], 'Hi'))
    assert response.status_code == 201, response.text
    with store.connect() as conn:
        pinned = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s',
                              (response.json()['id'],)).fetchone()['agent_config']
        expected = offered(conn, me['id'])
    assert [entry['id'] for entry in pinned['experts']] == [first['id']] and pinned['experts'] == expected
    assert pinned['experts'][0]['tool'] == 'ask_payments'


def test_generate_documents_pins_every_expert_and_offers_run_subagent(sessions):
    s = sessions
    first = expert(s.client, 'Payments', model_id=s.claude)
    second = expert(s.client, 'Billing', model_id=s.claude)
    session = start(s, expert_ids=[second['id'], first['id']], subagents=True)
    with s.store.connect() as conn:
        pinned = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s',
                              (session['id'],)).fetchone()['agent_config']
    assert [entry['id'] for entry in pinned['experts']] == [second['id'], first['id']]  # by name
    tools = {entry['tool']: entry for entry in pinned['tools']['items']}
    assert tools[SUBAGENT]['mode'] == 'on' and tools[SUBAGENT]['subagent_allowed'] is False


def test_the_tools_tab_lists_other_experts_on_by_default_and_saves_off_rows(store, saved_agent):
    client = saved_agent[0]
    me = expert(client, 'Lead', 'Leads.')
    other = expert(client, 'Payments', 'Payments questions.', resources='per_run')
    plain = create(client, 'Plain')
    tab = view(client, me['id'])
    experts = [tool for tool in tab['tools'] if tool['group'] == 'experts']
    assert tab['expert'] is True and view(client, plain['id'])['expert'] is False
    assert experts == [{'tool': f"expert:{other['id']}", 'label': 'Payments', 'summary': 'Payments questions.',
                        'group': 'experts', 'mode': 'on', 'usage': None, 'default_usage': '',
                        'subagent_allowed': False, 'available': True, 'reason': None, 'tab': None,
                        'resources': 'per_run'}]
    name = f"expert:{other['id']}"

    def stored():
        with store.connect() as conn:
            return conn.execute('SELECT mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s AND tool=%s',
                                (me['id'], name)).fetchone()

    off = put(client, me['id'], tab['revision'], [{'tool': name, 'mode': 'off'}])
    assert off.status_code == 200, off.text
    assert stored() == {'mode': 'off', 'usage': None, 'subagent_allowed': False}
    assert [t['mode'] for t in off.json()['tools'] if t['group'] == 'experts'] == ['off']
    back = put(client, me['id'], off.json()['revision'], [{'tool': name, 'mode': 'on'}])
    assert back.status_code == 200 and stored() is None
    for bad in ('expert:nope', f"expert:{me['id']}"):
        refused = put(client, me['id'], back.json()['revision'], [{'tool': bad, 'mode': 'off'}])
        assert refused.status_code == 400 and refused.json()['error']['message'] == f'Unknown expert: {bad}.'
    # An off choice about an agent that stops being an expert survives a save.
    assert put(client, me['id'], back.json()['revision'], [{'tool': name, 'mode': 'off'}]).status_code == 200
    update(client, client.get(f"/api/agents/{other['id']}", headers=HEADERS).json(), expert=False)
    kept = put(client, me['id'], client.get(f"/api/agents/{me['id']}/tools", headers=HEADERS).json()['revision'], [])
    assert kept.status_code == 200 and stored() == {'mode': 'off', 'usage': None, 'subagent_allowed': False}
    # A tab that was open while the owner switched the expert off still saves: on is skipped, off is kept.
    rev = lambda: client.get(f"/api/agents/{me['id']}/tools", headers=HEADERS).json()['revision']
    assert put(client, me['id'], rev(), [{'tool': name, 'mode': 'on'}]).status_code == 200 and stored() is None
    assert put(client, me['id'], rev(), [{'tool': name, 'mode': 'off'}]).status_code == 200
    assert stored() == {'mode': 'off', 'usage': None, 'subagent_allowed': False}
    assert put(client, me['id'], rev(), [{'tool': name, 'mode': 'off'}]).status_code == 200  # already stored
    plain_name = f"expert:{plain['id']}"
    assert put(client, me['id'], rev(), [{'tool': plain_name, 'mode': 'on'}]).status_code == 200
    assert put(client, me['id'], rev(), [{'tool': plain_name, 'mode': 'off'}]).status_code == 200
    with store.connect() as conn:
        assert conn.execute("SELECT mode FROM ka.agent_tools WHERE agent_id=%s AND tool=%s",
                            (me['id'], plain_name)).fetchone() == {'mode': 'off'}
    for bad in ('expert:nope', f"expert:{me['id']}"):
        refused = put(client, me['id'], rev(), [{'tool': bad, 'mode': 'on'}])
        assert refused.status_code == 400 and refused.json()['error']['message'] == f'Unknown expert: {bad}.'


def test_a_per_run_expert_that_accepts_no_resource_kind_has_no_empty_list():
    from knowledge_admin.knowledge_agents.experts import description
    assert description('Solo', 'Thinks.', 'per_run', []) == 'Ask Solo, an expert agent. Thinks. It works only on the resources you pass.'
