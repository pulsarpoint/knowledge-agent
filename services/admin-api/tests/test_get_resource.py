"""get_resource: everything recorded about one server, service, environment or code project the run was given, with
links the model follows by type and id; anything else (a linked resource outside the allowlist too) is refused alike.
A run's prompt names its resource in one line and lists servers and services as an index only."""

import asyncio
import json
from access_helpers import of
from uuid import uuid4

import pytest
from pydantic import ValidationError

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin import access as access_rules
from knowledge_admin.agents.runner import get_prompt, instructions_and_tools, load_credentials
from knowledge_admin.capabilities import document_tools, inventory, ssh
from live_documents import link_document, live_document
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.sdk import function_tool
from test_discovery_runs import config_of
from test_generations import sessions  # noqa: F401  (fixture)
from test_sessions_saved_agent import agent  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_session_debug import debug, ssh_key

OLD_TOOLS = (
    'get_server_info_basic',
    'get_server_services',
    'get_server_docs',
    'get_service_info',
    'get_service_docs',
    'get_project_info',
)
TYPES = ['server', 'service', 'environment', 'code_project']


def refused(resource_type) -> str:
    return access_rules.refuse_use(resource_type)  # a refused resource and a missing one answer alike


def given(world, *, servers=True, services=True, code=True, environments=True, project=None) -> dict:
    """A run given web-1, nginx, Shop and Production (each unless switched off), with its access object."""
    picks = [
        ('server', world.server['id'], servers),
        ('service', world.nginx, services),
        ('code_project', world.shop, code),
        ('environment', world.environment, environments),
    ]
    return {
        'project_id': str(project or world.project),
        'ssh': {'enabled': True, 'servers': [{'id': world.server['id']}] if servers else []},
        'services': {'enabled': True, 'items': [{'id': str(world.nginx)}] if services else []},
        'code': {'enabled': True, 'projects': [{'id': str(world.shop)}] if code else []},
        'chosen_resources': {'environment_ids': [str(world.environment)] if environments else []},
        'access': {
            'resources': [{'type': kind, 'id': str(key), 'source': 'given'} for kind, key, on in picks if on],
            'roles': {'content_editor': False},
        },
    }


def get(config, resource_type, resource_id):
    spec = CATALOG['get_resource']
    arguments = spec.arguments.model_validate({'resource_type': resource_type, 'resource_id': str(resource_id)})
    text = asyncio.run(spec.run({'config': config}, arguments))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def link(found, resource_type) -> dict:
    [only] = [item for item in found['links'] if item['type'] == resource_type]
    return only


def test_a_server_has_its_fields_environments_services_and_documents(world):
    found = get(given(world), 'server', world.server['id'])
    assert found['type'] == 'server' and found['id'] == world.server['id'] and found['name'] == 'web-1'
    assert found['hostname'] == '10.0.0.7' and found['description'] == 'Shop front end'
    assert {'port', 'username', 'tags', 'allow_sudo'} <= set(found)
    assert link(found, 'environment') == {
        'type': 'environment',
        'id': str(world.environment),
        'name': 'Production',
        'relation': 'in_environment',
    }
    service = link(found, 'service')
    assert (service['id'], service['name'], service['relation']) == (str(world.nginx), 'nginx', 'runs')
    assert (service['environment'], service['runtime'], service['port']) == ('Production', 'systemd', 80)
    document, child = found['documents']
    assert set(document) == {'id', 'title', 'tags', 'description_short', 'connection', 'resource'}
    assert (document['title'], document['resource']['type']) == ('web-1 overview', 'server')
    assert (child['title'], child['resource']['name']) == ('nginx runbook', 'nginx')  # the service it runs
    assert found['truncated'] is False


def test_a_service_has_its_fields_servers_code_projects_and_documents(world):
    found = get(given(world), 'service', world.nginx)
    assert found['type'] == 'service' and found['name'] == 'nginx' and found['owner'] == 'web team'
    assert found['service_role'] == 'proxy' and 'operations_notes' not in found
    server = link(found, 'server')
    assert (server['id'], server['name'], server['relation']) == (world.server['id'], 'web-1', 'deployed_on')
    assert server['port'] == 80 and server['environment'] == 'Production'
    code = link(found, 'code_project')
    assert (code['id'], code['name'], code['relation']) == (str(world.shop), 'Shop', 'built_from')
    assert link(found, 'environment')['id'] == str(world.environment)
    assert [document['title'] for document in found['documents']] == ['nginx runbook', 'web-1 overview']


def test_a_service_lists_its_connections(world):
    with world.store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.inventory_links(project_id,from_service_id,to_server_id,kind,description)
            VALUES (%s,%s,%s,'connects_to','Health checks')""",
            (world.project, world.nginx, world.server['id']),
        )
    found = get(given(world), 'service', world.nginx)
    [connection] = [item for item in found['links'] if item['relation'] == 'connects_to']
    assert (connection['type'], connection['id'], connection['description']) == (
        'server',
        world.server['id'],
        'Health checks',
    )


def test_an_environment_has_its_services_and_servers(world):
    found = get(given(world), 'environment', world.environment)
    assert found['type'] == 'environment' and found['name'] == 'Production' and found['description'] == 'Live'
    assert link(found, 'service')['id'] == str(world.nginx) and link(found, 'server')['id'] == world.server['id']
    assert [(item['title'], item['resource']['type']) for item in found['documents']] == [
        ('nginx runbook', 'service'),
        ('web-1 overview', 'server'),
    ]


def test_a_code_project_has_its_ref_commit_and_services(world):
    found = get(given(world), 'code_project', world.shop)
    assert found['type'] == 'code_project' and found['name'] == 'Shop'
    assert found['remote_url'] == 'https://git.example/shop.git' and {'ref', 'current_commit'} <= set(found)
    service = link(found, 'service')
    assert (service['id'], service['relation']) == (str(world.nginx), 'builds')


def test_every_link_is_followed_by_its_type_and_id(world):
    config = given(world)
    for resource_type, resource_id in (
        ('server', world.server['id']),
        ('service', world.nginx),
        ('environment', world.environment),
        ('code_project', world.shop),
    ):
        for item in get(config, resource_type, resource_id)['links']:
            followed = get(config, item['type'], item['id'])
            assert isinstance(followed, dict) and followed['id'] == item['id'], (resource_type, item)


def test_what_the_run_was_not_given_is_refused_alike(world):
    config = given(world, services=False, environments=False)
    found = get(config, 'server', world.server['id'])
    service, environment = link(found, 'service'), link(found, 'environment')
    # linked, not given: the run has none of their kind
    assert get(config, 'service', service['id']) == access_rules.refuse_none('service')
    assert get(config, 'environment', environment['id']) == access_rules.refuse_none('environment')
    assert get(given(world, environments=False), 'service', uuid4()) == refused('service')
    missing = uuid4()
    assert get(config, 'server', missing) == refused('server')
    assert get(config, 'code_project', world.environment) == refused('code_project')
    assert get(config, 'server', 'not-a-uuid') == refused('server')
    off = given(world, servers=False)  # SSH off at start: the server is not in the run's access object
    assert get(off, 'server', world.server['id']) == access_rules.refuse_none('server')


def read(config, document_id, **options):
    spec = CATALOG['get_document']
    arguments = spec.arguments.model_validate({'document_id': document_id, **options})
    text = asyncio.run(spec.run({'config': config}, arguments))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def test_get_document_reads_the_documents_get_resource_lists_and_caps_them(world, monkeypatch):
    config = given(world)
    doc_id = get(config, 'server', world.server['id'])['documents'][0]['id']
    document = read(config, doc_id)
    assert document['title'] == 'web-1 overview' and document['markdown'].startswith('# web-1 overview')
    monkeypatch.setattr(document_tools, 'MAX_MARKDOWN', 5)
    capped = read(config, doc_id)
    assert capped['markdown'] == '# web' and capped['truncated']
    assert read(config, str(uuid4())) == 'This document is not available in this run.'
    assert read(config, 'not-an-id') == 'This document is not available in this run.'


def test_an_environments_documents_are_listed_and_readable_when_it_was_given(world):
    with world.store.connect() as conn:
        saved = live_document(conn, world.project, 'production', 'Production notes', '# Production notes\n\nBody.')
        link_document(conn, world.project, saved['id'], 'environment_id', world.environment)
    config = given(world)
    document = get(config, 'environment', world.environment)['documents'][0]
    assert (document['id'], document['title']) == (str(saved['id']), 'Production notes')
    assert read(config, document['id'])['title'] == 'Production notes'
    hidden = given(world, environments=False)
    assert read(hidden, document['id']) == 'This document is not available in this run.'


def test_another_workspace_is_not_visible(world):
    with world.store.connect() as conn:
        other = world.store.project(conn, 'other-app')
    assert get(given(world, project=other), 'server', world.server['id']) == refused('server')


def test_lists_stop_at_100(world):
    with world.store.connect() as conn:
        for number in range(101):
            service = uuid4()
            conn.execute(
                'INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,%s)',
                (service, world.project, f'svc-{number:03}'),
            )
            conn.execute(
                """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name)
                VALUES (%s,%s,%s,'Production','x')""",
                (world.project, service, world.server['id']),
            )
    found = get(given(world), 'server', world.server['id'])
    assert len(found['links']) == 100 and found['truncated'] is True


def test_the_type_is_a_fixed_enum_in_the_schema():
    spec = CATALOG['get_resource']
    schema = function_tool(spec, spec.usage).params_json_schema
    assert schema['properties']['resource_type']['enum'] == TYPES
    assert set(schema['properties']) == {'resource_type', 'resource_id'}
    with pytest.raises(ValidationError):
        spec.arguments.model_validate({'resource_type': 'database', 'resource_id': str(uuid4())})


def test_the_catalog_has_one_inventory_reader():
    assert not set(OLD_TOOLS) & set(CATALOG)
    assert CATALOG['get_resource'].group == 'inventory'


def test_a_saved_agent_with_ssh_run_reads_its_servers_sudo(agent, ask, monkeypatch):
    monkeypatch.setattr(inventory, '_store', ask.s.store)
    response = ask.s.client.post(
        '/api/sessions/preview',
        headers=HEADERS,
        json={
            'message': 'Check the disk.',
            'agent_id': agent.id,
            'resources': {'server_ids': [agent.web]},
        },
    )
    assert response.status_code == 200, response.text
    config = response.json()['config']
    _, tools, _, _ = instructions_and_tools(config, subagents=False)
    names = [tool.name for tool in tools]
    assert 'ssh_run' in names and 'get_resource' in names
    server = of(config, 'server')[0]
    server.update(allow_sudo=True)
    found = get(config, 'server', agent.web)
    assert found['allow_sudo'] is True and 'instructions' not in found  # the agent's instructions are in its prompt


def server_run(world) -> str:
    created = world.client.post(
        '/api/sessions',
        headers=HEADERS,
        json={
            'started_from': {'server_id': world.server['id']},
            'model_id': world.model,
            'system_prompt': 'You answer.',
            'message': 'Is it alive?',
        },
    )
    assert created.status_code == 201, created.text
    return created.json()['id']


def test_a_server_runs_prompt_names_it_in_one_line_and_lists_no_resources(world):
    ssh_key(world)
    sid = server_run(world)
    instructions = debug(world, sid)['instructions']
    assert f'Resource: server {world.server["id"]} (web-1)' in instructions
    assert 'Shop front end' not in instructions and 'nginx' not in instructions  # get_available_resources lists them
    assert '10.0.0.7' not in instructions and '"hostname"' not in instructions
    assert '"username"' not in instructions and '"allow_sudo"' not in instructions
    assert 'servers/' not in instructions  # tags stay internal
    config = config_of(world, sid)
    of(config, 'server')[0]['instructions'] = 'Never reboot web-1 during business hours.'
    prompt = get_prompt(config)
    assert 'Never reboot' not in prompt and '"instructions"' not in prompt


def test_ssh_run_takes_the_index_entrys_id(world, monkeypatch):
    ssh_key(world)
    config = config_of(world, server_run(world))
    seen = {}

    async def execute(connection, command, **options):
        seen['host'] = connection.hostname
        return {'exit_code': 0, 'stdout': 'up', 'stderr': ''}

    monkeypatch.setattr(ssh, 'execute', execute)
    context = {'config': config, 'credentials': load_credentials(world.store, config)}
    result = json.loads(asyncio.run(ssh.ssh_run(context, world.server['id'], 'uptime')))
    assert result['server_id'] == world.server['id'] and result['exit_code'] == 0 and seen['host'] == '10.0.0.7'


def test_get_resource_names_the_inventory_document_apart_from_the_other_documents(world):
    config = given(world)
    found = get(config, 'server', world.server['id'])
    assert found['inventory_document'] is None
    assert [document['title'] for document in found['documents']] == ['web-1 overview', 'nginx runbook']
    with world.store.connect() as conn:
        saved = live_document(
            conn,
            world.project,
            'web-1-inventory',
            'web-1 inventory',
            '# web-1 inventory',
            server_ids=[world.server['id']],
            tags=['inventory'],
        )
    found = get(config, 'server', world.server['id'])
    assert found['inventory_document'] == {'id': str(saved['id']), 'title': 'web-1 inventory'}
    assert [(item['title'], item['tags']) for item in found['documents']] == [
        ('web-1 inventory', ['inventory']),
        ('web-1 overview', []),
        ('nginx runbook', []),
    ]
    assert read(config, found['inventory_document']['id'])['title'] == 'web-1 inventory'
    # A brand-new inventory draft is not a document get_document reads: get_resource does not name it.
    with world.store.connect() as conn:
        conn.execute("UPDATE ka.documents SET tags='{}' WHERE id=%s", (saved['id'],))
        conn.execute(
            """INSERT INTO ka.documents(project_id,slug,folder,revision,is_draft,title,description,purpose,
            audience,tags,markdown,content_hash,origin)
            VALUES (%s,'web-1-draft','servers/web-1',1,true,'web-1 draft','','explanation','both','{inventory}','# d',encode(sha256('# d'),'hex'),
            'generated') RETURNING id""",
            (world.project,),
        )
        draft = conn.execute("SELECT id FROM ka.documents WHERE slug='web-1-draft'").fetchone()['id']
        link_document(conn, world.project, draft, 'server_id', world.server['id'], 'built_for')
    found = get(config, 'server', world.server['id'])
    assert found['inventory_document'] is None
    titles = [document['title'] for document in found['documents']]
    assert titles == ['web-1 inventory', 'web-1 overview', 'nginx runbook']  # the draft is not listed
    assert get(config, 'code_project', world.shop)['inventory_document'] is None


def test_get_document_reads_only_documents_linked_to_resources_the_run_was_given(world):
    with world.store.connect() as conn:
        other = conn.execute(
            """INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'db-1','db-1.internal',false)
            RETURNING id""",
            (world.project,),
        ).fetchone()['id']
        elsewhere = live_document(conn, world.project, 'db-notes', 'db-1 notes', '# db-1', server_ids=[other])
        linked = live_document(conn, world.project, 'db-linked', 'db-1 linked', '# db-1 linked', server_ids=[other])
        link_document(conn, world.project, linked['id'], 'service_id', world.nginx)
    config = given(world)
    # Built for a server the run was not given, linked to nothing it was given: refused.
    assert read(config, str(elsewhere['id'])) == 'This document is not available in this run.'
    # Also linked to a service the run was given: readable.
    assert read(config, str(linked['id']))['title'] == 'db-1 linked'
    assert read(given(world, services=False), str(linked['id'])) == 'This document is not available in this run.'
    # A removed link no longer gives access.
    with world.store.connect() as conn:
        conn.execute(
            """UPDATE ka.document_resources SET removed_at=now(),removed_by=(SELECT id FROM ka.principals LIMIT 1)
            WHERE document_id=%s AND service_id=%s""",
            (linked['id'], world.nginx),
        )
    assert read(config, str(linked['id'])) == 'This document is not available in this run.'
