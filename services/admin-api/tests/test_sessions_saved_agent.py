"""Starting a saved agent through /api/sessions: its configuration is the ceiling; the request may only narrow it."""

from types import SimpleNamespace

import pytest

from access_helpers import give
from knowledge_admin.capabilities.document_tools import _run_links
from knowledge_admin.sessions.run_access import built_for, given
from test_profiles import create, update
from test_generations import sessions  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)
from test_repositories import HEADERS


def saved(ask, name):
    """A saved, enabled agent with the GPT model, the Payments service, the web-1 server and both code projects in its
    access object, and the one expert."""
    s = ask.s
    agent = create(s.client, name)
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=%s,enabled=true WHERE id=%s', (s.gpt, agent['id']))
        give(
            conn,
            agent['id'],
            [('service', s.service), ('server', ask.web['id']), ('code_project', s.repo), ('code_project', s.worker)],
        )
    return agent['id']


@pytest.fixture
def agent(ask):
    s = ask.s
    expert = create(s.client, 'Payments expert')
    update(s.client, expert, expert=True, when_to_use='Questions about payments.')
    with s.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET model_id=%s,enabled=true WHERE id=%s', (s.gpt, expert['id']))
    other_model = s.client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'GPT two',
            'provider': 'openai',
            'model': 'gpt-5-mini',
            'api_format': 'responses',
            'base_url': 'http://model.test/v1',
        },
    ).json()['id']
    outside = s.client.post(
        '/api/servers', headers=HEADERS, json={'name': 'web-2', 'hostname': '10.0.0.8', 'ssh_allowed': True}
    ).json()
    return SimpleNamespace(
        client=s.client,
        id=saved(ask, 'Operator'),
        web=ask.web['id'],
        service=s.service,
        repo=s.repo,
        model=s.gpt,
        other_model=other_model,
        environment=s.environment,
        document=ask.document['id'],
        outside_server=outside['id'],
        expert=expert['id'],
    )


@pytest.fixture
def whole(ask):
    """A saved agent a run gets with everything it has (no pick)."""
    return SimpleNamespace(client=ask.s.client, id=saved(ask, 'Whole'), web=ask.web['id'])


def start(client, path='', **body):
    return client.post(f'/api/sessions{path}', headers=HEADERS, json={'message': 'Check the disk.', **body})


def test_a_saved_agent_starts_with_its_own_configuration_narrowed(agent):
    for path in ('/preview', ''):
        response = start(
            agent.client,
            path,
            agent_id=agent.id,
            expert_ids=[],
            resources={'server_ids': [agent.web]},
        )
        assert response.status_code in (200, 201), response.text
    session = response.json()
    assert (session['purpose'], session['agent_id'], session['target_document_id']) == ('test', agent.id, None)
    assert session['config']['experts'] == []
    given = [
        (item['type'], item['id']) for item in session['config']['access']['resources'] if item['source'] == 'given'
    ]
    assert given == [('server', agent.web)] and session['given_resources'] == [{'type': 'server', 'id': agent.web}]
    preview = start(agent.client, '/preview', agent_id=agent.id, resources={'server_ids': [agent.web]}).json()
    assert preview['config']['system_prompt'] == session['config']['system_prompt']


def test_nothing_can_be_added_to_a_saved_agent(agent):
    other_expert = '00000000-0000-0000-0000-000000000009'
    for body, message in (
        ({'tools': ['get_resource']}, 'Extra inputs are not permitted'),
        ({'expert_ids': [other_expert]}, f'This expert is not available: {other_expert}.'),
        ({'subagents': True}, 'Extra inputs are not permitted'),
        ({'content_editor': True}, 'A run cannot make a saved agent a content editor.'),
        ({'model_id': agent.other_model}, 'A saved agent uses its own model and prompt.'),
        ({'system_prompt': 'Be someone else.'}, 'A saved agent uses its own model and prompt.'),
        (
            {'resources': {'environment_ids': [agent.environment]}},
            f"environment {agent.environment} is not in the agent's access",
        ),
        (
            {'resources': {'documents': [{'id': agent.document, 'editable': True}]}},
            f"document {agent.document} is not in the agent's access",
        ),
        (
            {'resources': {'server_ids': [agent.outside_server]}},
            f"server {agent.outside_server} is not in the agent's access",
        ),
    ):
        for path in ('/preview', ''):
            response = start(agent.client, path, **{'agent_id': agent.id, **body})
            assert response.status_code == 400, (body, response.text)
            if message:
                assert response.json()['error']['message'] == message


def test_the_agents_own_model_and_prompt_may_be_sent_back(agent):
    response = start(
        agent.client,
        '/preview',
        agent_id=agent.id,
        model_id=agent.model,
        resources={'server_ids': [agent.web]},
    )
    assert response.status_code == 200, response.text


def test_context_is_added_as_facts(agent):
    pinned = start(
        agent.client,
        '/preview',
        agent_id=agent.id,
        context='Ticket OPS-1',
        resources={'server_ids': [agent.web]},
    ).json()['config']
    assert pinned['system_prompt']['content'].endswith('# Context\n\nTicket OPS-1')


def test_options_for_a_saved_agent_offer_only_what_may_be_narrowed(agent):
    options = agent.client.get(f'/api/sessions/options?agent_id={agent.id}', headers=HEADERS).json()
    assert options['agent']['id'] == agent.id and 'resources_fixed' not in options['agent']
    assert options['prompts'] == [] and options['default_prompt_id'] is None and 'tools' not in options
    assert [server['id'] for server in options['resources']['servers']] == [agent.web]
    assert [service['id'] for service in options['resources']['services']] == [agent.service]
    assert options['resources']['environments'] == options['resources']['documents'] == []
    assert agent.client.get('/api/sessions/options?agent_id=nope', headers=HEADERS).status_code == 404


def test_the_old_test_routes_are_gone(agent):
    some = '00000000-0000-0000-0000-000000000001'
    for method, path in (
        ('post', f'/api/agents/{agent.id}/sessions'),
        ('get', f'/api/agents/{agent.id}/sessions'),
        ('post', f'/api/agents/{agent.id}/sessions/{some}/review'),
    ):
        assert getattr(agent.client, method)(
            path, headers=HEADERS, **({'json': {}} if method == 'post' else {})
        ).status_code in (404, 405)


def test_code_graph_without_a_graph_still_starts(whole, ask):
    for path, status in (('/preview', 200), ('', 201)):
        response = start(whole.client, path, agent_id=whole.id)
        assert response.status_code == status, response.text


def test_a_saved_agent_without_a_usable_server_still_starts(whole, ask):
    with ask.s.store.connect() as conn:
        conn.execute('UPDATE ka.servers SET enabled=false WHERE id=%s', (whole.web,))
    response = start(whole.client, '/preview', agent_id=whole.id)
    assert response.status_code == 200, response.text
    assert 'ssh_run' in [tool['name'] for tool in response.json()['tools']]  # its check refuses what it cannot use


def test_options_tell_what_a_service_brings(agent, ask):
    with ask.s.store.connect() as conn:
        project = ask.s.store.project(conn, 'shop')
        conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name,port)
            VALUES (%s,%s,%s,'Staging','payments-api',8081)""",
            (project, ask.s.service, agent.web),
        )
    options = agent.client.get(f'/api/sessions/options?agent_id={agent.id}', headers=HEADERS).json()
    service = next(item for item in options['resources']['services'] if item['id'] == ask.s.service)
    assert service['brings_servers'] and service['brings_code']


def test_a_document_of_a_saved_agent_run_links_the_chosen_service_and_not_what_it_brings(agent, ask):
    with ask.s.store.connect() as conn:  # Payments is deployed on web-1 and built from both code projects
        conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name,port)
            VALUES (%s,%s,%s,'Staging','payments-api',8081)""",
            (ask.s.store.project(conn, 'shop'), ask.s.service, agent.web),
        )
    response = start(agent.client, agent_id=agent.id, resources={'service_ids': [ask.s.service]})
    assert response.status_code == 201, response.text
    session = response.json()
    reached = {(item['type'], item['id']) for item in session['config']['access']['resources']}
    assert reached == {  # the tools still reach what it brought
        ('service', ask.s.service),
        ('server', agent.web),
        ('code_project', ask.s.repo),
        ('code_project', ask.s.worker),
    }
    assert session['given_resources'] == [{'type': 'service', 'id': ask.s.service}]
    assert _run_links({'built_for': built_for(given(session['config']['access']))}) == {
        'code_project_ids': [],
        'environment_ids': [],
        'service_ids': [ask.s.service],
        'server_ids': [],
        'deployment_ids': [],
    }
