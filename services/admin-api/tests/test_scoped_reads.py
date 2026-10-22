"""Reads follow the allowlist: get_resource and get_document answer only about the servers, services, code projects and
documents a run was given; a refused read reads exactly like a missing entry."""

import asyncio
import json
from access_helpers import of
from types import SimpleNamespace
from uuid import uuid4

import pytest
from inventory_rows import environment_id

from knowledge_admin import access as access_rules
from discovery_world import world  # noqa: F401  (fixture)
from knowledge_admin.capabilities import inventory
from live_documents import link_document, live_document
from knowledge_admin.tools.catalog import CATALOG
from test_sessions_saved_agent import agent, start  # noqa: F401  (fixture)
from test_generations import sessions  # noqa: F401  (fixture)
from test_sessions_world import ask  # noqa: F401  (fixture)
from test_repositories import HEADERS

NO_DOCUMENT = 'This document is not available in this run.'


def refused(resource_type, none=()) -> str:
    """Not in the run's list, or (a kind in `none`) the run has none of its kind; a missing entry reads the same."""
    return access_rules.refuse_none(resource_type) if resource_type in none else access_rules.refuse_use(resource_type)


def access(*given: tuple[str, object]) -> dict:
    return {'resources': [{'type': kind, 'id': str(key), 'source': 'given'} for kind, key in given], 'roles': {}}


def get(config, resource_type, resource_id):
    return call(config, 'get_resource', resource_type=resource_type, resource_id=str(resource_id))


def code_project_id(store, name) -> str:
    with store.connect() as conn:
        return str(conn.execute('SELECT id FROM ka.repositories WHERE name=%s', (name,)).fetchone()['id'])


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
            server = conn.execute(
                """INSERT INTO ka.servers(project_id,name,hostname,username) VALUES (%s,'db-1','10.0.0.20','ops')
                RETURNING id""",
                (project,),
            ).fetchone()['id']
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'postgres')", (service, project))
        conn.execute(
            """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id,runtime_name)
            VALUES (%s,%s,%s,'Production',%s,'postgres')""",
            (project, service, server, environment_id(conn, project, 'Production')),
        )
        saved = live_document(
            conn, project, 'db-1-overview', 'db-1 overview', '# db-1 overview\n\nBody.', description='About db-1'
        )
        link_document(conn, project, saved['id'], 'server_id', server)
    return SimpleNamespace(server=str(server), service=str(service), document=str(saved['id']))


def assert_scoped(config, *, web, web_document, db, service=None, project=None, none=()):
    """web (and its attached document) is readable; db-1, its service and its document read as missing."""
    assert get(config, 'server', web)['id'] == web
    for resource_type, resource_id in (
        ('server', db.server),
        ('service', db.service),
        ('server', uuid4()),
        ('code_project', uuid4()),
        ('server', 'db-1'),
    ):
        assert get(config, resource_type, resource_id) == refused(resource_type, none)
    assert web_document in [doc['id'] for doc in get(config, 'server', web)['documents']]
    if service:
        assert get(config, 'service', service)['id'] == service
    if project:
        assert get(config, 'code_project', project) == refused('code_project', none)
    assert call(config, 'get_document', document_id=web_document)['id'] == web_document
    assert call(config, 'get_document', document_id=db.document) == NO_DOCUMENT


@pytest.fixture
def scoped(world):
    db = add_db(world.store, world.project)
    config = {
        'project_id': str(world.project),
        'ssh': {'enabled': True, 'servers': [{'id': world.server['id']}]},
        'services': {'enabled': True, 'items': [{'id': str(world.nginx)}]},
        'code': {'enabled': True, 'projects': []},
        'access': access(('server', world.server['id']), ('service', world.nginx)),
    }
    return world, db, config


def test_a_pinned_config_reads_only_what_it_was_given(scoped):
    world, db, config = scoped
    web_document = get(config, 'server', world.server['id'])['documents'][0]['id']
    assert_scoped(
        config,
        web=world.server['id'],
        web_document=web_document,
        db=db,
        service=str(world.nginx),
        project=world.shop,
        none=('code_project',),
    )
    [server] = [item for item in get(config, 'service', world.nginx)['links'] if item['type'] == 'server']
    assert server['name'] == 'web-1'


def test_a_document_attached_to_a_given_service_or_code_project_is_readable(scoped):
    world, db, config = scoped
    runbook = get(config, 'service', world.nginx)['documents'][0]['id']
    assert call(config, 'get_document', document_id=runbook)['title'] == 'nginx runbook'
    with world.store.connect() as conn:
        conn.execute('DELETE FROM ka.document_resources WHERE document_id=%s', (runbook,))
        link_document(conn, world.project, runbook, 'code_project_id', world.shop)
    assert call(config, 'get_document', document_id=runbook) == NO_DOCUMENT
    given = {
        **config,
        'access': access(('server', world.server['id']), ('service', world.nginx), ('code_project', world.shop)),
    }
    assert call(given, 'get_document', document_id=runbook)['title'] == 'nginx runbook'
    assert get(given, 'code_project', world.shop)['name'] == 'Shop'
    assert call(config, 'get_document', document_id=runbook) == NO_DOCUMENT
    assert get(config, 'code_project', world.shop) == refused('code_project', ('code_project',))


def test_turned_off_kinds_read_nothing(scoped):
    world, db, config = scoped
    off = {**config, 'access': access()}  # kinds turned off at start are not in the run's access object
    assert get(off, 'server', world.server['id']) == access_rules.refuse_none('server')
    assert get(off, 'service', world.nginx) == access_rules.refuse_none('service')


def test_a_saved_agents_pinned_config_reads_only_its_servers(agent, ask, monkeypatch):
    s = ask.s
    monkeypatch.setattr(inventory, '_store', s.store)
    with s.store.connect() as conn:
        project = s.store.project(conn, 'shop')
        link_document(conn, project, ask.document['id'], 'server_id', ask.web['id'])
    db = add_db(s.store, project)
    response = start(agent.client, '/preview', agent_id=agent.id, resources={'server_ids': [agent.web]})
    assert response.status_code == 200, response.text
    config = response.json()['config']
    assert [str(server['id']) for server in of(config, 'server')] == [agent.web]
    project = code_project_id(s.store, 'Payments API')
    assert_scoped(
        config, web=agent.web, web_document=agent.document, db=db, project=project, none=('service', 'code_project')
    )


def test_an_ad_hoc_runs_preview_config_reads_only_its_selection(ask, monkeypatch):
    s = ask.s
    monkeypatch.setattr(inventory, '_store', s.store)
    with s.store.connect() as conn:
        project = s.store.project(conn, 'shop')
        link_document(conn, project, ask.document['id'], 'server_id', ask.web['id'])
    db = add_db(s.store, project)
    body = {
        **ask.body,
        'resources': {'server_ids': [ask.web['id']]},
    }
    response = s.client.post('/api/sessions/preview', headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    config = response.json()['config']
    assert not [item for item in config['access']['resources'] if item['type'] == 'document']
    project = code_project_id(s.store, 'Payments API')
    assert_scoped(
        config,
        web=ask.web['id'],
        web_document=ask.document['id'],
        db=db,
        project=project,
        none=('service', 'code_project'),
    )
