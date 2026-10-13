"""Reads follow the allowlist: the inventory tools and get_document answer only about the servers, services, code
projects and documents a run was given; a refused read reads exactly like a missing entry."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.capabilities import inventory
from knowledge_admin.documents import DocumentInput, save_document
from knowledge_admin.tools.catalog import CATALOG
from test_sessions_saved_agent import agent, start  # noqa: F401  (fixture)
from test_generations import sessions  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)
from test_repositories import HEADERS

NO_SERVER = 'This server is not available in this run.'
NO_SERVICE = 'This service is not available in this run.'
NO_PROJECT = 'This code project is not available in this run.'
NO_DOCUMENT = 'This document is not available in this run.'


def call(config, tool, **arguments):
    spec = CATALOG[tool]
    text = asyncio.run(spec.run({'config': config}, spec.arguments.model_validate(arguments)))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def add_db(store, project, *, server=None):
    """A second server db-1 with a postgres service deployed on it and a document attached to it."""
    service = uuid4()
    with store.connect() as conn:
        if server is None:
            server = conn.execute("""INSERT INTO ka.servers(project_id,name,hostname,username) VALUES (%s,'db-1','10.0.0.20','ops')
                RETURNING id""", (project,)).fetchone()['id']
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'postgres')", (service, project))
        conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name)
            VALUES (%s,%s,%s,'Production','postgres')""", (project, service, server))
        saved = save_document(conn, project, None, DocumentInput(slug='db-1-overview', title='db-1 overview',
                              description='About db-1', markdown='# db-1 overview\n\nBody.'))
        conn.execute('INSERT INTO ka.inventory_documents(project_id,server_id,document_id) VALUES (%s,%s,%s)',
                     (project, server, saved['id']))
    return SimpleNamespace(server=str(server), service=str(service), document=str(saved['id']))


def assert_scoped(config, *, web='web-1', web_document, db, service=None, project='nope'):
    """web-1 (and its attached document) is readable; db-1, its service and its document read as missing."""
    assert call(config, 'get_server_info_basic', hostname=web)['name'] == web
    assert call(config, 'get_server_info_basic', hostname='db-1') == NO_SERVER
    assert call(config, 'get_server_services', hostname='db-1') == NO_SERVER
    assert call(config, 'get_server_docs', hostname='db-1') == NO_SERVER
    assert call(config, 'get_server_info_basic', hostname='nowhere') == NO_SERVER
    assert web_document in [doc['id'] for doc in call(config, 'get_server_docs', hostname=web)['items']]
    assert call(config, 'get_service_info', name='postgres') == NO_SERVICE
    assert call(config, 'get_service_docs', name='postgres') == NO_SERVICE
    assert call(config, 'get_service_info', name='redis') == NO_SERVICE
    if service:
        assert call(config, 'get_service_info', name=service)['name'] == service
    assert call(config, 'get_project_info', name=project) == NO_PROJECT
    assert call(config, 'get_project_info', name='nope') == NO_PROJECT
    assert call(config, 'get_document', document_id=web_document)['id'] == web_document
    assert call(config, 'get_document', document_id=db.document) == NO_DOCUMENT


@pytest.fixture
def scoped(world):
    db = add_db(world.store, world.project)
    config = {'project_id': str(world.project),
              'ssh': {'enabled': True, 'servers': [{'id': world.server['id']}]},
              'services': {'enabled': True, 'items': [{'id': str(world.nginx)}]},
              'code': {'enabled': True, 'projects': []}}
    return world, db, config


def test_a_pinned_config_reads_only_what_it_was_given(scoped):
    world, db, config = scoped
    web_document = call(config, 'get_server_docs', hostname='web-1')['items'][0]['id']
    assert_scoped(config, web_document=web_document, db=db, service='nginx', project='shop')
    assert call(config, 'get_service_info', name='nginx')['deployments'][0]['server'] == 'web-1'


def test_a_document_attached_to_a_given_service_or_code_project_is_readable(scoped):
    world, db, config = scoped
    runbook = call(config, 'get_service_docs', name='nginx')['items'][0]['id']
    assert call(config, 'get_document', document_id=runbook)['title'] == 'nginx runbook'
    with world.store.connect() as conn:
        conn.execute('DELETE FROM ka.inventory_documents WHERE document_id=%s', (runbook,))
        conn.execute('INSERT INTO ka.inventory_documents(project_id,code_project_id,document_id) VALUES (%s,%s,%s)',
                     (world.project, world.shop, runbook))
    assert call(config, 'get_document', document_id=runbook) == NO_DOCUMENT
    given = {**config, 'code': {'enabled': True, 'projects': [{'id': str(world.shop)}]}}
    assert call(given, 'get_document', document_id=runbook)['title'] == 'nginx runbook'
    assert call(given, 'get_project_info', name='shop')['name'] == 'Shop'
    off = {**given, 'code': {'enabled': False, 'projects': [{'id': str(world.shop)}]}}
    assert call(off, 'get_document', document_id=runbook) == NO_DOCUMENT
    assert call(off, 'get_project_info', name='shop') == NO_PROJECT


def test_turned_off_kinds_read_nothing(scoped):
    world, db, config = scoped
    off = {**config, 'ssh': {'enabled': False, 'servers': [{'id': world.server['id']}]},
           'services': {'enabled': False, 'items': [{'id': str(world.nginx)}]}}
    assert call(off, 'get_server_info_basic', hostname='web-1') == NO_SERVER
    assert call(off, 'get_service_info', name='nginx') == NO_SERVICE


def test_a_saved_agents_pinned_config_reads_only_its_servers(agent, ask, monkeypatch):
    s = ask.s
    monkeypatch.setattr(inventory, '_store', s.store)
    with s.store.connect() as conn:
        project = s.store.project(conn, 'shop')
        conn.execute('INSERT INTO ka.inventory_documents(project_id,server_id,document_id) VALUES (%s,%s,%s)',
                     (project, ask.web['id'], ask.document['id']))
    db = add_db(s.store, project)
    response = start(agent.client, '/preview', agent_id=agent.id, tools=['read_file'], resources={'server_ids': [agent.web]})
    assert response.status_code == 200, response.text
    config = response.json()['config']
    assert [str(server['id']) for server in config['ssh']['servers']] == [agent.web]
    assert_scoped(config, web_document=agent.document, db=db, project='Payments API')


def test_an_ad_hoc_runs_preview_config_reads_only_its_selection(ask, monkeypatch):
    s = ask.s
    monkeypatch.setattr(inventory, '_store', s.store)
    with s.store.connect() as conn:
        project = s.store.project(conn, 'shop')
        conn.execute('INSERT INTO ka.inventory_documents(project_id,server_id,document_id) VALUES (%s,%s,%s)',
                     (project, ask.web['id'], ask.document['id']))
    db = add_db(s.store, project)
    body = {**ask.body, 'resources': {'server_ids': [ask.web['id']]}, 'tools': ['get_server_info_basic', 'get_document']}
    response = s.client.post('/api/sessions/preview', headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    config = response.json()['config']
    assert config.get('document_access', []) == []
    assert_scoped(config, web_document=ask.document['id'], db=db, project='Payments API')


def test_a_shared_hostname_or_name_finds_the_runs_own_entry(world):
    """A server sharing web-1's hostname and a code project sharing Shop's name, both sorted first: a run given only
    web-1 and Shop still reads them; a run given neither reads both as missing."""
    with world.store.connect() as conn:
        conn.execute("""INSERT INTO ka.servers(id,project_id,name,hostname,username)
            VALUES ('00000000-0000-0000-0000-000000000001',%s,'web-0','10.0.0.7','ops')""", (world.project,))
        conn.execute("""INSERT INTO ka.repositories(id,project_id,slug,name,remote_url)
            VALUES ('00000000-0000-0000-0000-000000000001',%s,'shop-copy','Shop','https://git.example/shop-copy.git')""",
                     (world.project,))
    config = {'project_id': str(world.project), 'ssh': {'enabled': True, 'servers': [{'id': world.server['id']}]},
              'services': {'enabled': True, 'items': []}, 'code': {'enabled': True, 'projects': [{'id': str(world.shop)}]}}
    assert call(config, 'get_server_info_basic', hostname='10.0.0.7')['name'] == 'web-1'
    assert call(config, 'get_project_info', name='shop')['remote_url'] == 'https://git.example/shop.git'
    neither = {**config, 'ssh': {'enabled': True, 'servers': []}, 'code': {'enabled': True, 'projects': []}}
    assert call(neither, 'get_server_info_basic', hostname='10.0.0.7') == NO_SERVER
    assert call(neither, 'get_project_info', name='shop') == NO_PROJECT
