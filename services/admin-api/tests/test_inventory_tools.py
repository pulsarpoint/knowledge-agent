"""Inventory read tools: what an agent can read about servers, services, code projects and attached documents."""

import asyncio
import json
from uuid import uuid4

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.capabilities import document_tools, inventory
from knowledge_admin.tools.catalog import CATALOG, implied_tools, tools_summary

TOOLS = (
    'get_server_info_basic',
    'get_server_services',
    'get_server_docs',
    'get_service_info',
    'get_service_docs',
    'get_project_info',
    'get_document',
)


def pinned(world, project=None):
    """A run given web-1, nginx and Shop: the entries these tests read."""
    return {
        'project_id': str(project or world.project),
        'ssh': {'enabled': True, 'servers': [{'id': world.server['id']}]},
        'services': {'enabled': True, 'items': [{'id': str(world.nginx)}]},
        'code': {'enabled': True, 'projects': [{'id': str(world.shop)}]},
    }


def call(world, tool, **arguments):
    spec = CATALOG[tool]
    context = {'config': pinned(world)}
    text = asyncio.run(spec.run(context, spec.arguments.model_validate(arguments)))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def test_server_info_by_hostname_or_name(world):
    info = call(world, 'get_server_info_basic', hostname='10.0.0.7')
    assert info['name'] == 'web-1' and info['environments'] == ['Production']
    assert info['service_count'] == 1 and info['document_count'] == 1
    assert call(world, 'get_server_info_basic', hostname='WEB-1')['hostname'] == '10.0.0.7'
    assert call(world, 'get_server_info_basic', hostname='nope') == 'This server is not available in this run.'


def test_server_services_and_documents(world):
    services = call(world, 'get_server_services', hostname='web-1')
    [row] = services['items']
    assert row['name'] == 'nginx' and row['service_role'] == 'proxy' and row['environment'] == 'Production'
    assert (row['runtime'], row['runtime_name'], row['port']) == ('systemd', 'nginx', 80) and not services['truncated']
    [doc] = call(world, 'get_server_docs', hostname='web-1')['items']
    assert doc['title'] == 'web-1 overview' and doc['description'] == 'About web-1 overview'


def test_service_info_has_fields_deployments_connections_and_documents(world):
    info = call(world, 'get_service_info', name='NGINX')
    assert info['name'] == 'nginx' and info['owner'] == 'web team'
    assert info['deployments'][0]['server'] == 'web-1'
    assert info['built_from'] == ['Shop'] and info['documents'][0]['title'] == 'nginx runbook'
    assert call(world, 'get_service_docs', name='nginx')['items'][0]['title'] == 'nginx runbook'
    assert call(world, 'get_service_info', name='redis') == 'This service is not available in this run.'


def test_project_info_lists_the_services_built_from_it(world):
    info = call(world, 'get_project_info', name='shop')
    assert info['name'] == 'Shop' and info['services'] == ['nginx'] and info['documents'] == []


def test_get_document_reads_attached_documents_only_and_caps_them(world, monkeypatch):
    doc_id = call(world, 'get_server_docs', hostname='web-1')['items'][0]['id']
    document = call(world, 'get_document', document_id=doc_id)
    assert document['title'] == 'web-1 overview' and document['markdown'].startswith('# web-1 overview')
    monkeypatch.setattr(document_tools, 'MAX_MARKDOWN', 5)
    capped = call(world, 'get_document', document_id=doc_id)
    assert capped['markdown'] == '# web' and capped['truncated']
    assert call(world, 'get_document', document_id=str(uuid4())) == 'This document is not available in this run.'
    assert call(world, 'get_document', document_id='not-an-id') == 'This document is not available in this run.'


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
    listed = call(world, 'get_server_services', hostname='web-1')
    assert len(listed['items']) == 100 and listed['truncated']


def test_another_workspace_is_not_visible(world):
    with world.store.connect() as conn:
        other = world.store.project(conn, 'other-app')
    context = {'config': pinned(world, other)}
    spec = CATALOG['get_server_info_basic']
    text = asyncio.run(spec.run(context, spec.arguments.model_validate({'hostname': 'web-1'})))
    assert text == 'This server is not available in this run.'


def test_inventory_tools_are_a_group_that_existing_agents_have_off():
    for name in TOOLS:
        assert CATALOG[name].group == 'inventory' and CATALOG[name].needs is None
    config = {
        'documents': {'enabled': False},
        'code': {'enabled': False},
        'ssh': {'enabled': False},
        'mcp': {'enabled': False, 'servers': []},
    }
    assert all(item['mode'] == 'off' for item in implied_tools(config)['items'] if item['tool'] in TOOLS)
    assert 'inventory' in tools_summary({**config, 'tools': {'items': []}})['groups']
