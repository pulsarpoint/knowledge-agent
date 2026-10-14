"""Chats on servers, services and environments: runs with purpose resource, tagged with the resource they start from
(servers/<id>, services/<id>, environments/<id>), listed by tag; each type's assistant prompt is a new chat's default;
the proposal tools only when the request turns them on."""

from uuid import uuid4

import pytest

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.capabilities.discovery import NOT_FROM_SERVER
from knowledge_admin.sessions.start import BAD_TAG, RESOURCE_TAG, TOO_MANY_TAGS, assistant_prompt_id
from knowledge_admin.tools.catalog import effective_tools
from test_discovery_runs import call, config_of, pinned_of, ssh_ready
from test_repositories import HEADERS

PLAIN = ['get_server_info_basic', 'get_server_services', 'get_service_info', 'get_document']
PROPOSALS = ['propose_service', 'propose_document']


def body(world, started_from: dict, **change) -> dict:
    return {
        'started_from': started_from,
        'model_id': world.model,
        'system_prompt': 'You answer.',
        'message': 'Is it alive?',
        'tools': PLAIN,
        **change,
    }


def start(world, started_from: dict, **change):
    return world.client.post('/api/sessions', headers=HEADERS, json=body(world, started_from, **change))


def session_row(world, session_id):
    with world.store.connect() as conn:
        return conn.execute(
            'SELECT purpose,tags,server_id,context_snapshot FROM ka.agent_sessions WHERE id=%s', (session_id,)
        ).fetchone()


def offered(config) -> set[str]:
    return {spec.name for spec, _ in effective_tools(config, config['ssh']['servers'])}


def test_a_chat_on_a_service_is_a_resource_run_tagged_with_it_that_brings_its_code_and_servers(world):
    created = start(world, {'service_id': str(world.nginx)})  # its server has no SSH key yet: it starts anyway
    assert created.status_code == 201, created.text
    sid = created.json()['id']
    row = session_row(world, sid)
    assert row['purpose'] == 'resource' and row['tags'] == [f'services/{world.nginx}'] and row['server_id'] is None
    assert row['context_snapshot'] == {'service_id': str(world.nginx)}
    assert created.json()['tags'] == [f'services/{world.nginx}']
    pinned = pinned_of(world, sid)
    assert pinned['started_from'] == {'type': 'service', 'id': str(world.nginx), 'name': 'nginx'}
    assert pinned['chosen_resources']['service_ids'] == [str(world.nginx)]
    assert pinned['chosen_resources']['server_ids'] == [] and pinned['chosen_resources']['environment_ids'] == []
    assert pinned['run_resources']['code_project_ids'] == [str(world.shop)]
    assert pinned['ssh']['servers'] == [] and not pinned['ssh']['enabled']  # nothing it brings is reachable
    assert pinned['system_prompt']['content'] == f'You answer.\n\n# Context\n\nService: nginx (id {world.nginx}).'
    assert pinned['proposal_tools'] == []
    ssh_ready(world)
    pinned = pinned_of(world, start(world, {'service_id': str(world.nginx)}).json()['id'])
    assert [server['id'] for server in pinned['ssh']['servers']] == [world.server['id']]
    assert pinned['run_resources']['server_ids'] == [world.server['id']]


def test_a_chat_on_an_environment_brings_its_services_and_servers(world):
    ssh_ready(world)
    created = start(world, {'environment_id': str(world.environment)})
    assert created.status_code == 201, created.text
    sid = created.json()['id']
    row = session_row(world, sid)
    assert row['purpose'] == 'resource' and row['tags'] == [f'environments/{world.environment}']
    assert row['server_id'] is None
    pinned = pinned_of(world, sid)
    assert pinned['started_from'] == {'type': 'environment', 'id': str(world.environment), 'name': 'Production'}
    assert pinned['chosen_resources']['environment_ids'] == [str(world.environment)]
    assert pinned['chosen_resources']['service_ids'] == [] and pinned['chosen_resources']['server_ids'] == []
    assert pinned['run_resources']['service_ids'] == [str(world.nginx)]
    assert pinned['run_resources']['server_ids'] == [world.server['id']]
    assert pinned['system_prompt']['content'].endswith(
        f'Environment: Production (id {world.environment}). Services: nginx. Servers: web-1.'
    )


def test_a_chat_on_a_server_is_tagged_with_it_and_keeps_its_server(world):
    ssh_ready(world)
    created = start(world, {'server_id': world.server['id']})
    assert created.status_code == 201, created.text
    row = session_row(world, created.json()['id'])
    assert row['purpose'] == 'resource' and row['tags'] == [f'servers/{world.server["id"]}']
    assert str(row['server_id']) == world.server['id']
    pinned = pinned_of(world, created.json()['id'])
    assert pinned['started_from'] == {'type': 'server', 'id': world.server['id'], 'name': 'web-1'}
    assert 'started_from_server' not in pinned
    assert pinned['chosen_resources']['server_ids'] == [world.server['id']]


def test_a_server_chat_still_needs_ssh_and_unknown_resources_are_not_found(world):
    assert start(world, {'server_id': world.server['id']}).status_code == 400  # no SSH set up
    for key, message in (('service_id', 'Service not found.'), ('environment_id', 'Environment not found.')):
        missing = start(world, {key: str(uuid4())})
        assert missing.status_code == 404 and missing.json()['error']['message'] == message
    both = start(world, {'service_id': str(world.nginx), 'environment_id': str(world.environment)})
    assert both.status_code == 400


def test_request_tags_are_added_and_validated(world):
    created = start(world, {'service_id': str(world.nginx)}, tags=['proposals', 'proposals'])
    assert created.status_code == 201, created.text
    assert session_row(world, created.json()['id'])['tags'] == ['proposals', f'services/{world.nginx}']
    own = start(world, {'service_id': str(world.nginx)}, tags=[f'services/{world.nginx}'])
    assert session_row(world, own.json()['id'])['tags'] == [f'services/{world.nginx}']
    bad = start(world, {'service_id': str(world.nginx)}, tags=['Not A Tag'])
    assert bad.status_code == 400 and BAD_TAG.format(tag='Not A Tag') in bad.json()['error']['message']
    assert start(world, {'service_id': str(world.nginx)}, tags=['a' * 121]).status_code == 400
    assert start(world, {'service_id': str(world.nginx)}, tags=['a//b']).status_code == 400
    eleven = start(world, {'service_id': str(world.nginx)}, tags=[f't{n}' for n in range(11)])
    assert eleven.status_code == 400 and TOO_MANY_TAGS in eleven.json()['error']['message']
    ten = start(world, {'service_id': str(world.nginx)}, tags=[f't{n}' for n in range(10)])  # + its resource tag
    assert ten.status_code == 400 and TOO_MANY_TAGS in ten.json()['error']['message']
    assert start(world, {'service_id': str(world.nginx)}, tags=[f't{n}' for n in range(9)]).status_code == 201
    other = start(world, {'service_id': str(world.nginx)}, tags=[f'servers/{world.server["id"]}'])
    assert other.status_code == 400
    assert RESOURCE_TAG.format(tag=f'servers/{world.server["id"]}') in other.json()['error']['message']


def test_the_runs_of_a_tag_are_listed_open_first_then_by_activity(world):
    ssh_ready(world)
    first = start(world, {'server_id': world.server['id']}).json()['id']
    second = start(world, {'server_id': world.server['id']}, tags=['proposals']).json()['id']
    closed = start(world, {'server_id': world.server['id']}).json()['id']
    service_run = start(world, {'service_id': str(world.nginx)}).json()['id']
    with world.store.connect() as conn:
        conn.execute(
            "UPDATE ka.agent_sessions SET status='closed',closed_at=now(),last_activity_at=now() WHERE id=%s", (closed,)
        )
        conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()+interval '1 minute' WHERE id=%s", (first,))
    listed = world.client.get('/api/sessions', headers=HEADERS, params={'tag': f'servers/{world.server["id"]}'})
    assert listed.status_code == 200, listed.text
    runs = listed.json()['sessions']
    assert [run['id'] for run in runs] == [first, second, closed]
    assert runs[1]['tags'] == ['proposals', f'servers/{world.server["id"]}'] and runs[0]['purpose'] == 'resource'
    assert (runs[0]['pending'], runs[0]['accepted'], runs[0]['rejected']) == (0, 0, 0)
    wizard = world.client.get('/api/sessions', headers=HEADERS, params={'tag': 'proposals'}).json()['sessions']
    assert [run['id'] for run in wizard] == [second]
    service = world.client.get('/api/sessions', headers=HEADERS, params={'tag': f'services/{world.nginx}'})
    assert [run['id'] for run in service.json()['sessions']] == [service_run]
    gone = world.client.get('/api/sessions', headers=HEADERS, params={'server_id': world.server['id']})
    assert len(gone.json()['sessions']) == 4  # the old server filter is gone: an unknown parameter is ignored


def test_a_plain_server_chat_has_no_proposal_tools_even_when_its_configuration_has_them_on(world):
    ssh_ready(world)
    sid = start(world, {'server_id': world.server['id']}, tools=['ssh_run', *PLAIN]).json()['id']
    config = config_of(world, sid)
    assert config['proposal_tools'] == []
    assert {'ssh_run', 'get_server_info_basic'} <= offered(config)
    assert not set(PROPOSALS) & offered(config)
    for item in config['tools']['items']:  # as a saved agent's configuration listing them on would have them
        if item['tool'] in PROPOSALS:
            item['mode'] = 'on'
    assert not set(PROPOSALS) & offered(config)


def test_a_saved_agents_run_never_gets_the_proposal_tools(world):
    config = config_of(world, start(world, {'service_id': str(world.nginx)}).json()['id'])
    del config['started_from']
    config['proposal_tools'] = PROPOSALS  # without a resource start nothing turns them on
    for item in config['tools']['items']:
        if item['tool'] in PROPOSALS:
            item['mode'] = 'on'
    assert not set(PROPOSALS) & offered(config)


def test_a_server_run_that_asks_for_the_proposal_tools_has_both(world):
    ssh_ready(world)
    created = start(world, {'server_id': world.server['id']}, tools=[*PLAIN, *PROPOSALS], tags=['proposals'])
    assert created.status_code == 201, created.text
    config = config_of(world, created.json()['id'])
    assert config['proposal_tools'] == PROPOSALS
    assert set(PROPOSALS) <= offered(config)
    only_documents = config_of(
        world, start(world, {'server_id': world.server['id']}, tools=[*PLAIN, 'propose_document']).json()['id']
    )
    assert 'propose_document' in offered(only_documents) and 'propose_service' not in offered(only_documents)


def test_propose_service_is_refused_on_a_service_or_environment_run_and_propose_document_works_there(world):
    for started_from in ({'service_id': str(world.nginx)}, {'environment_id': str(world.environment)}):
        refused = start(world, started_from, tools=[*PLAIN, 'propose_service'])
        assert refused.status_code == 400
        assert refused.json()['error']['message'] == 'propose_service needs a run started from a server.'
    created = start(world, {'service_id': str(world.nginx)}, tools=[*PLAIN, 'propose_document'])
    assert created.status_code == 201, created.text
    config = config_of(world, created.json()['id'])
    assert offered(config) & set(PROPOSALS) == {'propose_document'}
    said = call(config, 'propose_document', title='Health checks', description='How to check nginx.', markdown='# x')
    assert said == 'Proposal 1: new document "Health checks" about service nginx.'
    with pytest.raises(ValueError, match=NOT_FROM_SERVER):  # even if it were offered
        call(config, 'propose_service', name='redis', deployment={'runtime': 'systemd'})
    environment = config_of(
        world, start(world, {'environment_id': str(world.environment)}, tools=['propose_document']).json()['id']
    )
    said = call(environment, 'propose_document', title='Production', description='The environment.', markdown='# x')
    assert said == 'Proposal 1: new document "Production" about environment Production.'
    with world.store.connect() as conn:
        rows = conn.execute(
            'SELECT session_id,server_id,service_id FROM ka.discovery_proposals ORDER BY created_at'
        ).fetchall()
    assert [(str(row['session_id']), row['server_id'], row['service_id']) for row in rows] == [
        (created.json()['id'], None, world.nginx),
        (environment['session_id'], None, None),
    ]


def test_a_new_chats_prompt_is_its_types_assistant_prompt(world):
    for kind, query, name in (
        ('server', f'server_id={world.server["id"]}', 'Server assistant'),
        ('service', f'service_id={world.nginx}', 'Service assistant'),
        ('environment', f'environment_id={world.environment}', 'Environment assistant'),
    ):
        response = world.client.get(f'/api/sessions/options?{query}', headers=HEADERS)
        assert response.status_code == 200, response.text
        options = response.json()
        assert options['default_prompt_id'] == str(assistant_prompt_id(world.project, kind))
        names = [prompt['name'] for prompt in options['prompts']]
        assert name in names
        tools = {tool['name'] for tool in options['tools']}
        assert not {'save_document', 'edit_document'} & tools
        assert ('propose_service' in tools) == (kind == 'server') and 'propose_document' in tools
    server = world.client.get(f'/api/sessions/options?server_id={world.server["id"]}', headers=HEADERS).json()
    assert [prompt['name'] for prompt in server['prompts']] == ['Server assistant']  # tagged assistant and server
    service = world.client.get(f'/api/sessions/options?service_id={world.nginx}', headers=HEADERS).json()
    assert [entry['id'] for entry in service['resources']['services'] if entry['linked']] == [str(world.nginx)]
    assert world.client.get(f'/api/sessions/options?service_id={uuid4()}', headers=HEADERS).status_code == 404
    both = world.client.get(
        f'/api/sessions/options?service_id={world.nginx}&environment_id={world.environment}', headers=HEADERS
    )
    assert both.status_code == 400


def test_the_assistant_prompts_are_seeded_for_new_workspaces(world):
    with world.store.connect() as conn:
        project = world.store.project(conn, f'shop-{uuid4().hex[:6]}')
        rows = conn.execute(
            """SELECT p.id,p.name,p.input_kind,p.tags,v.content FROM ka.documentation_prompts p
            JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
            WHERE p.project_id=%s AND 'assistant'=ANY(p.tags) ORDER BY p.name""",
            (project,),
        ).fetchall()
    assert [(row['name'], row['input_kind']) for row in rows] == [
        ('Environment assistant', 'environment'),
        ('Server assistant', 'server'),
        ('Service assistant', 'service'),
    ]
    for row in rows:
        assert row['id'] == assistant_prompt_id(project, row['input_kind'])
        assert 'Never show passwords, keys or tokens.' in row['content']
    assert 'df -h' in rows[1]['content'] and 'get_service_docs' in rows[2]['content']
