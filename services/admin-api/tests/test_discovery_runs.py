"""Discovery runs: the server page's data, starting a run from a server through /api/sessions (SSH set up and allowed),
what the run pins, the list of a server's runs and the start panel's options."""

import asyncio
import json
from uuid import UUID, uuid4

import asyncssh

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.capabilities.document_tools import _run_links
from knowledge_admin.servers.discovery import NOT_ALLOWED, NOT_CONFIGURED
from knowledge_admin.sessions.start import ONE_START
from knowledge_admin.tools.catalog import CATALOG, effective_tools
from test_profiles import create, update
from test_repositories import HEADERS

TOOLS = [
    'ssh_run',
    'get_resource',
    'get_document',
    'propose_service',
    'propose_document',
]


def ssh_ready(world, server=None):
    server = server or world.server
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    current = world.client.get(f'/api/servers/{server["id"]}/ssh', headers=HEADERS).json()
    saved = world.client.put(
        f'/api/servers/{server["id"]}/ssh',
        headers=HEADERS,
        json={'revision': current['revision'], 'username': 'ops', 'port': 22, 'private_key': key, 'known_hosts': ''},
    )
    assert saved.status_code == 200, saved.text


def body(world, server=None, **change):
    server = server or world.server
    return {
        'started_from': {'server_id': server['id']},
        'model_id': world.model,
        'system_prompt': 'You explore servers.',
        'message': 'Explore web-1.\nLook at nginx first.',
        'tools': TOOLS,
        **change,
    }


def start(world, server=None, **change):
    return world.client.post('/api/sessions', headers=HEADERS, json=body(world, server, **change))


def pinned_of(world, session_id):
    with world.store.connect() as conn:
        return conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (session_id,)).fetchone()[
            'agent_config'
        ]


def config_of(world, session_id):
    """The configuration a turn of the run sees: the pinned one plus its session id (the worker sets it)."""
    return {**pinned_of(world, session_id), 'session_id': str(session_id)}


def call(config, tool, **arguments):
    spec = CATALOG[tool]
    text = asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def test_the_server_page_shows_environments_deployments_and_readiness(world):
    page = world.client.get(f'/api/servers/{world.server["id"]}', headers=HEADERS)
    assert page.status_code == 200, page.text
    body = page.json()
    assert body['server']['name'] == 'web-1' and [e['name'] for e in body['environments']] == ['Production']
    [deployment] = body['deployments']
    assert (
        deployment['service_name'] == 'nginx' and deployment['environment'] == 'Production' and deployment['port'] == 80
    )
    assert body['discovery'] == {'ready': False, 'reason': NOT_CONFIGURED}
    assert body['ssh_ready'] is False
    ssh_ready(world)
    ready = world.client.get(f'/api/servers/{world.server["id"]}', headers=HEADERS).json()
    assert ready['discovery'] == {'ready': True, 'reason': None} and ready['ssh_ready'] is True
    assert world.client.get(f'/api/servers/{uuid4()}', headers=HEADERS).status_code == 404


def test_a_run_needs_ssh_set_up_and_allowed(world):
    refused = start(world)
    assert refused.status_code == 400 and refused.json()['error']['message'] == NOT_CONFIGURED
    ssh_ready(world)
    with world.store.connect() as conn:
        conn.execute('UPDATE ka.servers SET ssh_allowed=false WHERE id=%s', (world.server['id'],))
    refused = start(world)
    assert refused.status_code == 400 and refused.json()['error']['message'] == NOT_ALLOWED
    missing = start(world, {'id': str(uuid4())})
    assert missing.status_code == 404 and missing.json()['error']['message'] == 'Server not found.'
    assert start(world, message='  ').status_code == 400


def test_one_starting_point(world):
    both = start(world, started_from={'server_id': world.server['id'], 'document_id': str(uuid4())})
    assert both.status_code == 400 and ONE_START in both.json()['error']['message']
    assert ONE_START == 'Choose one starting point: a document, a new document, a server, a service or an environment.'


def test_a_run_pins_its_server_and_the_context_facts(world):
    ssh_ready(world)
    created = start(world, message='  Explore web-1.\nLook at nginx first.  ')
    assert created.status_code == 201, created.text
    sid = created.json()['id']
    config = pinned_of(world, sid)
    assert [server['id'] for server in config['ssh']['servers']] == [world.server['id']]
    assert config['started_from'] == {'type': 'server', 'id': world.server['id'], 'name': 'web-1'}
    assert config['proposal_tools'] == ['propose_service', 'propose_document']
    assert 'discovery' not in config and 'session_id' not in config
    assert config['project_id'] == str(world.project)
    assert config['system_prompt']['content'] == 'You explore servers.'  # the Resource line names the server
    rows = {item['tool']: item for item in config['tools']['items']}
    assert rows['propose_service']['mode'] == 'on' and not rows['propose_service']['subagent_allowed']
    assert rows['propose_document']['mode'] == 'on' and not rows['propose_document']['subagent_allowed']
    offered = {spec.name for spec, _ in effective_tools(config, config['ssh']['servers'])}
    assert {'ssh_run', 'get_resource', 'propose_service', 'propose_document'} <= offered
    with world.store.connect() as conn:
        session = conn.execute(
            """SELECT purpose,server_id,agent_id,title,target_document_id,context_snapshot,prompt_snapshot
            FROM ka.agent_sessions WHERE id=%s""",
            (sid,),
        ).fetchone()
        [first] = conn.execute('SELECT text FROM ka.agent_session_inputs WHERE session_id=%s', (sid,)).fetchall()
    assert (
        session['purpose'] == 'resource'
        and str(session['server_id']) == world.server['id']
        and session['agent_id'] is None
    )
    assert session['target_document_id'] is None and session['title'] == 'Explore web-1.'
    assert session['context_snapshot'] == {'server_id': world.server['id']}
    assert first['text'] == 'Explore web-1.\nLook at nginx first.'


def test_a_context_given_by_the_request_is_kept(world):
    ssh_ready(world)
    sid = start(world, context='Look only at the web tier.').json()['id']
    assert (
        pinned_of(world, sid)['system_prompt']['content']
        == 'You explore servers.\n\n# Context\n\nLook only at the web tier.'
    )


def test_the_server_is_added_to_the_runs_servers_and_other_chosen_servers_stay(world):
    ssh_ready(world)
    other = world.client.post(
        '/api/servers', headers=HEADERS, json={'name': 'web-2', 'hostname': '10.0.0.8', 'ssh_allowed': True}
    ).json()
    ssh_ready(world, other)
    sid = start(world, resources={'server_ids': [other['id']]}).json()['id']
    assert sorted(server['id'] for server in pinned_of(world, sid)['ssh']['servers']) == sorted(
        [world.server['id'], other['id']]
    )


def test_the_services_on_the_server_are_pinned_without_bringing_their_other_servers(world):
    ssh_ready(world)
    other = world.client.post(
        '/api/servers', headers=HEADERS, json={'name': 'db-1', 'hostname': '10.0.0.20', 'ssh_allowed': True}
    ).json()
    ssh_ready(world, other)
    postgres = uuid4()
    with world.store.connect() as conn:  # nginx also runs on db-1; postgres runs only there
        conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name)
            VALUES (%s,%s,%s,'Production','nginx')""",
            (world.project, world.nginx, other['id']),
        )
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'postgres')", (postgres, world.project))
        conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name)
            VALUES (%s,%s,%s,'Production','postgres')""",
            (world.project, postgres, other['id']),
        )
    sid = start(world).json()['id']
    pinned = pinned_of(world, sid)
    assert [server['id'] for server in pinned['ssh']['servers']] == [world.server['id']]
    assert pinned['services']['enabled'] and [item['id'] for item in pinned['services']['items']] == [str(world.nginx)]
    assert pinned['code']['projects'] == []
    config = config_of(world, sid)
    assert call(config, 'get_resource', resource_type='service', resource_id=str(world.nginx))['name'] == 'nginx'
    refused = f'The service {postgres} is not available in this run.'
    assert call(config, 'get_resource', resource_type='service', resource_id=str(postgres)) == refused


def test_a_document_of_a_server_run_links_its_server_and_not_the_services_on_it(world):
    ssh_ready(world)
    sid = start(world).json()['id']
    pinned = pinned_of(world, sid)
    assert pinned['chosen_resources']['server_ids'] == [world.server['id']]
    assert pinned['run_resources']['server_ids'] == [world.server['id']]
    assert _run_links(pinned) == {
        'code_project_ids': [],
        'environment_ids': [],
        'service_ids': [],  # nginx runs on it: read by the run, not linked
        'server_ids': [world.server['id']],
        'deployment_ids': [],
    }


def test_two_runs_on_one_server_can_run_at_once_and_are_listed_with_their_counts(world):
    ssh_ready(world)
    first = start(world).json()['id']
    second = start(world, message='Look at the databases.').json()['id']
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='working' WHERE id=%s", (first,))
        conn.execute(
            """INSERT INTO ka.discovery_proposals(project_id,session_id,server_id,number,kind,payload,status,decided_at,result_id)
            VALUES (%s,%s,%s,1,'service','{"name":"a"}','pending',NULL,NULL),
                   (%s,%s,%s,2,'service','{"name":"b"}','rejected',now(),NULL)""",
            (world.project, first, world.server['id'], world.project, first, world.server['id']),
        )
    other = world.client.post(
        '/api/servers', headers=HEADERS, json={'name': 'web-2', 'hostname': '10.0.0.8', 'ssh_allowed': True}
    ).json()
    ssh_ready(world, other)
    start(world, other)
    runs = world.client.get(f'/api/sessions?tag=servers/{world.server["id"]}', headers=HEADERS).json()['sessions']
    assert [run['id'] for run in runs] == [second, first]
    assert (runs[1]['pending'], runs[1]['accepted'], runs[1]['rejected']) == (1, 0, 1)
    assert (runs[0]['pending'], runs[0]['accepted'], runs[0]['rejected']) == (0, 0, 0)
    assert runs[1]['status'] == 'working' and runs[0]['model_name'] == 'GPT' and runs[0]['purpose'] == 'resource'


def test_a_run_has_only_the_experts_it_chose(world):
    expert = update(
        world.client, create(world.client, 'Payments'), expert=True, when_to_use='Payments.', model_id=world.model
    )
    assert expert.status_code == 200, expert.text
    ssh_ready(world)
    config = pinned_of(world, start(world).json()['id'])
    assert config['experts'] == []
    chosen = start(world, expert_ids=[expert.json()['id']])
    assert chosen.status_code == 201, chosen.text
    assert [entry['id'] for entry in pinned_of(world, chosen.json()['id'])['experts']] == [expert.json()['id']]


def test_switching_a_proposal_tool_off_is_audited(world):
    ssh_ready(world)
    started = start(world, tools=[tool for tool in TOOLS if tool != 'propose_document'])
    assert started.status_code == 201, started.text
    with world.store.connect() as conn:
        details = conn.execute(
            """SELECT details FROM ka.audit_events WHERE action='agent_run.created'
            AND resource_id=%s""",
            (started.json()['id'],),
        ).fetchone()['details']
    assert 'propose_document' in details['tools_off'] and 'propose_service' not in details['tools_off']


def test_the_options_for_a_server(world):
    response = world.client.get(f'/api/sessions/options?server_id={world.server["id"]}', headers=HEADERS)
    assert response.status_code == 200, response.text
    options = response.json()
    [server] = options['resources']['servers']
    assert server['linked'] and server['id'] == world.server['id'] and server['reason'] == NOT_CONFIGURED
    with world.store.connect() as conn:
        seeded = conn.execute(
            """SELECT id FROM ka.documentation_prompts WHERE project_id=%s AND name='Server assistant'""",
            (world.project,),
        ).fetchone()['id']
    assert options['default_prompt_id'] == str(seeded)
    assert [prompt['name'] for prompt in options['prompts']] == ['Server assistant']
    tools = {tool['name']: tool for tool in options['tools']}
    assert {'propose_service', 'propose_document', 'ssh_run'} <= set(tools)
    assert tools['propose_service']['requires'] == 'a run started from a server'
    assert not {'save_document', 'edit_document'} & set(tools)  # a server run has no document to edit
    ssh_ready(world)
    [server] = world.client.get(f'/api/sessions/options?server_id={world.server["id"]}', headers=HEADERS).json()[
        'resources'
    ]['servers']
    assert server['usable'] and server['reason'] is None
    elsewhere = world.client.get('/api/sessions/options?new_document=1', headers=HEADERS).json()
    assert 'propose_service' not in {tool['name'] for tool in elsewhere['tools']}
    assert {'save_document', 'edit_document'} <= {tool['name'] for tool in elsewhere['tools']}
    both = world.client.get(f'/api/sessions/options?server_id={world.server["id"]}&new_document=1', headers=HEADERS)
    assert both.status_code == 400 and both.json()['error']['message'] == ONE_START
    assert world.client.get(f'/api/sessions/options?server_id={uuid4()}', headers=HEADERS).status_code == 404


def test_the_proposal_tools_are_refused_in_a_run_not_started_from_a_server(world):
    ssh_ready(world)
    started = world.client.post(
        '/api/sessions',
        headers=HEADERS,
        json={
            'started_from': {'new_document': {'folder': 'servers', 'name': 'Web'}},
            'model_id': world.model,
            'system_prompt': 'You write.',
            'message': 'Write.',
            'resources': {'server_ids': [world.server['id']]},
            'tools': ['propose_service'],
        },
    )
    assert started.status_code == 400
    assert started.json()['error']['message'] == 'propose_service needs a run started from a server.'


def test_the_old_discovery_routes_are_gone(world):
    ssh_ready(world)
    assert world.client.post(
        f'/api/servers/{world.server["id"]}/discovery', headers=HEADERS, json={'model_id': world.model, 'prompt': 'x'}
    ).status_code in (404, 405)
    assert world.client.get(f'/api/servers/{world.server["id"]}/discovery', headers=HEADERS).status_code in (404, 405)


def test_a_started_run_is_found_by_its_id(world):
    ssh_ready(world)
    detail = start(world).json()
    assert UUID(detail['id']) and detail['purpose'] == 'resource'
