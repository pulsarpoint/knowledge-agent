"""Experts as tools: which experts a caller is offered, their tool names and descriptions, and pinning into Test and
Generate documents sessions."""

from access_helpers import give
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_generations import sessions, start  # noqa: F401  (fixtures)
from test_test_sessions import test_start
from test_profiles import create, update
from test_repositories import HEADERS

from knowledge_admin.knowledge_agents.experts import offered, slug


def expert(client, name, when='When it fits.', **changes):
    response = update(client, create(client, name), expert=True, when_to_use=when, **changes)
    assert response.status_code == 200, response.text
    return response.json()


def model_of(saved_agent) -> str:
    """An enabled Responses model (the fixture's own test-claude model does not fit agents)."""
    return (
        saved_agent[0]
        .post(
            '/api/llm/models',
            headers=HEADERS,
            json={
                'name': 'GPT',
                'provider': 'openai',
                'api_format': 'responses',
                'model': 'gpt-5',
                'base_url': 'http://model.test/v1',
                'api_key': 'expert-model-secret',
            },
        )
        .json()['id']
    )


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


def test_only_enabled_experts_with_a_model_other_than_the_caller_are_offered(store, saved_agent):
    client, model = saved_agent[0], model_of(saved_agent)
    me = update(client, caller(store, saved_agent, model), expert=True, when_to_use='Me.', model_id=model).json()
    expert(client, 'No model')
    disabled = expert(client, 'Disabled', model_id=model)
    with store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (disabled['id'],))
    update(client, create(client, 'Plain'), model_id=model)
    hidden = expert(client, 'Hidden', model_id=model)
    fixed = expert(client, 'Fixed one', 'Fixed question.', model_id=model)
    per_run = expert(client, 'Per run', 'Any project.', model_id=model)
    with store.connect() as conn:
        service = conn.execute('SELECT id FROM ka.services ORDER BY id LIMIT 1').fetchone()['id']
        give(conn, per_run['id'], [('code_project', saved_agent[5]['id']), ('service', service)])
        found = {entry['id']: entry for entry in offered(conn, me['id'])}
        everyone = {entry['id'] for entry in offered(conn, None)}
    assert set(found) == {hidden['id'], fixed['id'], per_run['id']}  # every other expert
    assert {hidden['id'], me['id']} <= everyone and disabled['id'] not in everyone
    assert (
        found[fixed['id']]['description']
        == 'Ask Fixed one, an expert agent. Fixed question. It uses its own resources.'
    )
    assert found[fixed['id']]['accepts'] == [] and 'resources' not in found[fixed['id']]
    assert found[per_run['id']]['description'] == (
        'Ask Per run, an expert agent. Any project. It uses its own resources; pass some of them to narrow it to '
        'those (it accepts: code projects, services).'
    )
    assert found[per_run['id']]['accepts'] == ['code_project_ids', 'service_ids']
    assert found[per_run['id']]['tool'] == 'ask_per_run'


def test_a_test_session_pins_the_offered_experts(store, saved_agent):
    client, model = saved_agent[0], model_of(saved_agent)
    me = caller(store, saved_agent, model)
    first = expert(client, 'Payments', model_id=model)
    response = client.post('/api/sessions', headers=HEADERS, json=test_start(client, me['id'], 'Hi'))
    assert response.status_code == 201, response.text
    with store.connect() as conn:
        pinned = conn.execute(
            'SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (response.json()['id'],)
        ).fetchone()['agent_config']
        expected = offered(conn, me['id'])
    assert [entry['id'] for entry in pinned['experts']] == [first['id']] and pinned['experts'] == expected
    assert pinned['experts'][0]['tool'] == 'ask_payments'


def test_generate_documents_pins_every_expert_and_offers_run_subagent(sessions):
    s = sessions
    first = expert(s.client, 'Payments', model_id=s.claude)
    second = expert(s.client, 'Billing', model_id=s.claude)
    session = start(s, expert_ids=[second['id'], first['id']])
    with s.store.connect() as conn:
        pinned = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (session['id'],)).fetchone()[
            'agent_config'
        ]
    assert [entry['id'] for entry in pinned['experts']] == [second['id'], first['id']]  # by name
    assert 'tools' not in pinned  # every run gets every tool, run_subagent too
    every = start(s)
    with s.store.connect() as conn:
        pinned = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (every['id'],)).fetchone()[
            'agent_config'
        ]
    assert {entry['id'] for entry in pinned['experts']} == {first['id'], second['id']}  # none named: all of them


def test_an_expert_without_resources_lists_no_kinds():
    from knowledge_admin.knowledge_agents.experts import description

    assert description('Solo', 'Thinks.', []) == 'Ask Solo, an expert agent. Thinks. It uses its own resources.'
    assert description('Ops', 'Runs.', ['server_ids']) == (
        'Ask Ops, an expert agent. Runs. It uses its own resources; pass some of them to narrow it to those (it '
        'accepts: servers).'
    )
