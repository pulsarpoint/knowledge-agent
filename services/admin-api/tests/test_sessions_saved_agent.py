"""Starting a saved agent through /api/sessions: its configuration is the ceiling; the request may only narrow it."""

from types import SimpleNamespace

import pytest

from test_profiles import create, update
from test_generations import sessions  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)
from test_repositories import HEADERS


def saved(ask, name, *, resources='per_run', subagent=True):
    """A saved, enabled agent with the GPT model, tools ssh_run and read_file (and run_subagent) on, the Payments
    service and the web-1 server in its selection, and the one expert."""
    s = ask.s
    agent = create(s.client, name)
    with s.store.connect() as conn:
        project = s.store.project(conn, 'shop')
        conn.execute(
            """UPDATE ka.agents SET model_id=%s,enabled=true,resources=%s,repositories_enabled=true,
            all_repositories=true,ssh_enabled=true,services_enabled=true,documents_enabled=false,skills_enabled=false,
            mcp_enabled=false WHERE id=%s""",
            (s.gpt, resources, agent['id']),
        )
        conn.execute('INSERT INTO ka.agent_services(agent_id,service_id) VALUES (%s,%s)', (agent['id'], s.service))
        conn.execute(
            'INSERT INTO ka.operations_servers(project_id,agent,server_id) VALUES (%s,%s,%s)',
            (project, agent['id'], ask.web['id']),
        )
        for tool in ('ssh_run', 'read_file', *(('run_subagent',) if subagent else ())):
            conn.execute(
                "INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,%s,'on',%s)",
                (agent['id'], tool, tool != 'run_subagent'),
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
        model=s.gpt,
        other_model=other_model,
        environment=s.environment,
        document=ask.document['id'],
        outside_server=outside['id'],
        no_subagents=saved(ask, 'Solo', subagent=False),
        expert=expert['id'],
    )


@pytest.fixture
def fixed(ask):
    return SimpleNamespace(client=ask.s.client, id=saved(ask, 'Fixed', resources='fixed'), web=ask.web['id'])


def start(client, path='', **body):
    return client.post(f'/api/sessions{path}', headers=HEADERS, json={'message': 'Check the disk.', **body})


def test_a_saved_agent_starts_with_its_own_configuration_narrowed(agent):
    for path in ('/preview', ''):
        response = start(
            agent.client,
            path,
            agent_id=agent.id,
            tools=['read_file'],
            expert_ids=[],
            subagents=False,
            resources={'server_ids': [agent.web]},
        )
        assert response.status_code in (200, 201), response.text
    session = response.json()
    assert (session['purpose'], session['agent_id'], session['target_document_id']) == ('test', agent.id, None)
    modes = {item['tool']: item['mode'] for item in session['config']['tools']['items']}
    assert modes['read_file'] == 'on' and modes['ssh_run'] == 'off' and modes['run_subagent'] == 'off'
    assert session['config']['experts'] == []
    assert session['run_resources']['server_ids'] == [agent.web]
    preview = start(
        agent.client, '/preview', agent_id=agent.id, tools=['read_file'], resources={'server_ids': [agent.web]}
    ).json()
    assert preview['config']['system_prompt'] == session['config']['system_prompt']


def test_nothing_can_be_added_to_a_saved_agent(agent, fixed):
    other_expert = '00000000-0000-0000-0000-000000000009'
    for body, message in (
        ({'tools': ['get_service_info']}, "get_service_info is not one of this agent's tools."),
        ({'expert_ids': [other_expert]}, f'This expert is not available: {other_expert}.'),
        ({'subagents': True, 'agent_id': agent.no_subagents}, 'This agent does not allow subagents.'),
        ({'model_id': agent.other_model}, 'A saved agent uses its own model and prompt.'),
        ({'system_prompt': 'Be someone else.'}, 'A saved agent uses its own model and prompt.'),
        (
            {'resources': {'environment_ids': [agent.environment]}},
            'A saved agent takes no environments; choose its servers or services.',
        ),
        (
            {'resources': {'documents': [{'id': agent.document, 'editable': True}]}},
            'A saved agent takes no documents yet.',
        ),
        ({'resources': {'server_ids': [agent.outside_server]}}, None),
    ):
        for path in ('/preview', ''):
            response = start(agent.client, path, **{'agent_id': agent.id, **body})
            assert response.status_code == 400, (body, response.text)
            if message:
                assert response.json()['error']['message'] == message
    refused = start(fixed.client, agent_id=fixed.id, resources={'server_ids': [fixed.web]})
    assert refused.status_code == 400 and refused.json()['error']['message'] == 'This agent uses fixed resources.'


def test_the_agents_own_model_and_prompt_may_be_sent_back(agent):
    response = start(
        agent.client,
        '/preview',
        agent_id=agent.id,
        model_id=agent.model,
        tools=['read_file'],
        resources={'server_ids': [agent.web]},
    )
    assert response.status_code == 200, response.text


def test_narrowing_away_what_a_tool_needs_is_refused(agent):
    response = start(agent.client, agent_id=agent.id, tools=['ssh_run'], resources={})
    assert response.status_code == 400 and response.json()['error']['message'] == 'ssh_run needs at least one server.'


def test_context_is_added_as_facts(agent):
    pinned = start(
        agent.client,
        '/preview',
        agent_id=agent.id,
        tools=['read_file'],
        context='Ticket OPS-1',
        resources={'server_ids': [agent.web]},
    ).json()['config']
    assert pinned['system_prompt']['content'].endswith('# Context\n\nTicket OPS-1')


def test_options_for_a_saved_agent_offer_only_what_may_be_narrowed(agent, fixed):
    options = agent.client.get(f'/api/sessions/options?agent_id={agent.id}', headers=HEADERS).json()
    assert options['agent']['id'] == agent.id and not options['agent']['resources_fixed']
    assert options['agent']['subagents_allowed'] and options['prompts'] == [] and options['default_prompt_id'] is None
    assert {tool['name'] for tool in options['tools']} == {'ssh_run', 'read_file'}
    assert [server['id'] for server in options['resources']['servers']] == [agent.web]
    fixed_options = fixed.client.get(f'/api/sessions/options?agent_id={fixed.id}', headers=HEADERS).json()
    assert fixed_options['agent']['resources_fixed'] and fixed_options['resources']['servers'] == []
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


def test_code_graph_without_a_graph_still_starts(fixed, ask):
    with ask.s.store.connect() as conn:
        conn.execute(
            "INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,'code_graph','on',true)",
            (fixed.id,),
        )
    for path, status in (('/preview', 200), ('', 201)):
        response = start(fixed.client, path, agent_id=fixed.id, tools=['code_graph', 'read_file'])
        assert response.status_code == status, response.text


def test_ssh_run_needs_a_usable_saved_server(fixed, ask):
    with ask.s.store.connect() as conn:
        conn.execute('UPDATE ka.servers SET enabled=false WHERE id=%s', (fixed.web,))
    response = start(fixed.client, '/preview', agent_id=fixed.id, tools=['ssh_run'])
    assert response.status_code == 400 and response.json()['error']['message'] == 'ssh_run needs at least one server.'
    with ask.s.store.connect() as conn:
        conn.execute('UPDATE ka.servers SET enabled=true WHERE id=%s', (fixed.web,))
        conn.execute('UPDATE ka.agents SET ssh_enabled=false WHERE id=%s', (fixed.id,))
    response = start(fixed.client, '/preview', agent_id=fixed.id, tools=['ssh_run'])
    assert response.status_code == 400 and response.json()['error']['message'] == 'ssh_run needs at least one server.'


def test_tools_the_request_does_not_control_keep_their_mode(ask):
    grounded = saved(ask, 'Grounded', resources='fixed')
    with ask.s.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET documents_enabled=true,documents_grounded=true WHERE id=%s', (grounded,))
        conn.execute(
            "INSERT INTO ka.agent_tools(agent_id,tool,mode,subagent_allowed) VALUES (%s,'report_not_covered','on',true)",
            (grounded,),
        )
    options = ask.s.client.get(f'/api/sessions/options?agent_id={grounded}', headers=HEADERS).json()
    tools = [tool['name'] for tool in options['tools']]
    assert 'report_not_covered' not in tools
    response = start(ask.s.client, '/preview', agent_id=grounded, tools=tools)
    assert response.status_code == 200, response.text
    modes = {item['tool']: item['mode'] for item in response.json()['config']['tools']['items']}
    assert modes['report_not_covered'] == 'on'


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
