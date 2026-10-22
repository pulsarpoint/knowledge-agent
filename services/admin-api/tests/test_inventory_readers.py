"""Every reader on the new inventory model: an environment holds the services deployed in it and its member servers
(ka.environment_servers, so also an empty one), a service brings its servers (deployments) and code projects (built_from relations), a server its deployed services;
get_resource shows relations and connections with port and protocol."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from knowledge_admin.agents import wizard
from knowledge_admin.capabilities import inventory as inventory_tool
from knowledge_admin.documents.store import resource_document_list
from knowledge_admin.inventory import store as inventory
from knowledge_admin.knowledge_agents.config import load_entries
from knowledge_admin.sessions import run_access
from knowledge_admin.tools.catalog import CATALOG
from live_documents import link_document, live_document
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def world(store, tmp_path, monkeypatch):
    """prod holds api on web (8080) and pg on db (5432); api is built from shop, depends on pg, has part worker and
    connects to pg (postgres) and to smtp.example.com:587. ghost (service) and lonely (server) are not deployed; old is a
    code project nothing is built from."""
    monkeypatch.setattr(inventory_tool, '_store', store)
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('CODE_ROOT', str(tmp_path / 'data' / 'code'))
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        author = {'actor_id': None}
        w = SimpleNamespace(store=store, project=project, client=_client(tmp_path, repository_store=store))
        w.prod = inventory.create_environment(conn, project, {'name': 'prod'}, author)['id']
        w.web, w.db, w.lonely = (
            inventory.create_server(
                conn, project, {'name': n, 'hostname': f'{n}.internal', 'ssh_allowed': True}, author
            )['id']
            for n in ('web', 'db', 'lonely')
        )
        w.api, w.pg, w.worker, w.ghost = (
            inventory.create_service(conn, project, {'name': n}, author)['id'] for n in ('api', 'pg', 'worker', 'ghost')
        )
        w.shop, w.old = uuid4(), uuid4()
        for key, slug in ((w.shop, 'shop'), (w.old, 'old')):
            conn.execute(
                'INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,%s,%s)', (key, project, slug, slug)
            )
        api_web = inventory.create_deployment(
            conn,
            project,
            {'service_id': w.api, 'server_id': w.web, 'environment_id': w.prod, 'runtime_name': 'api', 'port': 8080},
            author,
        )['id']
        pg_db = inventory.create_deployment(
            conn,
            project,
            {'service_id': w.pg, 'server_id': w.db, 'environment_id': w.prod, 'runtime_name': 'pg', 'port': 5432},
            author,
        )['id']
        for values in (
            {'from_service_id': w.api, 'kind': 'built_from', 'to_code_project_id': w.shop},
            {'from_service_id': w.api, 'kind': 'depends_on', 'to_service_id': w.pg, 'description': 'orders'},
            {'from_service_id': w.worker, 'kind': 'part_of', 'to_service_id': w.api},
        ):
            inventory.create_relation(conn, project, values, author)
        w.to_pg = inventory.create_connection(
            conn,
            project,
            {'from_deployment_id': api_web, 'to_deployment_id': pg_db, 'protocol': 'postgres', 'description': 'SQL'},
            author,
        )['id']
        inventory.create_connection(
            conn,
            project,
            {'from_deployment_id': api_web, 'to_host': 'smtp.example.com', 'port': 587, 'protocol': 'smtp'},
            author,
        )
    return w


def held(w, **ids) -> dict:
    """The run's access object holding these ids by type (each given), with what they bring."""
    with w.store.connect() as conn:
        holds = load_entries(conn, w.project, {kind: [str(item) for item in items] for kind, items in ids.items()})
        picked = [{'type': kind, 'id': str(item)} for kind, items in ids.items() for item in items]
        return run_access.run_access(conn, w.project, holds, picked)


def kinds(access) -> dict:
    return {(item['type'], item['id']): item['source'] for item in access['resources']}


def test_an_environment_brings_what_is_deployed_in_it(world):
    w = world
    found = kinds(held(w, environment=[w.prod]))
    via = f'via environment {w.prod}'
    assert found == {
        ('environment', str(w.prod)): 'given',
        ('service', str(w.api)): via,
        ('service', str(w.pg)): via,
        ('server', str(w.web)): via,
        ('server', str(w.db)): via,
        ('code_project', str(w.shop)): f'via service {w.api}',
    }  # ghost and lonely are not deployed
    with w.store.connect() as conn:
        brought = run_access.bring(conn, w.project, [], [w.prod])
    assert brought['service_ids'] == [str(w.api), str(w.pg)]
    assert brought['code_project_ids'] == [str(w.shop)]
    assert set(brought['server_ids']) == {str(w.web), str(w.db)}


def test_a_service_brings_its_servers_and_code_projects_and_a_server_its_services(world):
    w = world
    assert kinds(held(w, service=[w.api])) == {
        ('service', str(w.api)): 'given',
        ('server', str(w.web)): f'via service {w.api}',
        ('code_project', str(w.shop)): f'via service {w.api}',
    }
    assert kinds(held(w, server=[w.web])) == {
        ('server', str(w.web)): 'given',
        ('service', str(w.api)): f'via server {w.web}',
    }  # a deployed service is a child of the server, not given: it brings nothing of its own
    assert kinds(held(w, service=[w.pg])) == {
        ('service', str(w.pg)): 'given',
        ('server', str(w.db)): f'via service {w.pg}',
    }


def get(w, resource_type, resource_id):
    config = {
        'project_id': str(w.project),
        'access': {
            'resources': [
                {'type': kind, 'id': str(key), 'source': 'given'}
                for kind, key in (
                    ('environment', w.prod),
                    ('service', w.api),
                    ('service', w.pg),
                    ('server', w.web),
                    ('server', w.lonely),
                    ('code_project', w.shop),
                )
            ],
            'roles': {'content_editor': False},
        },
    }
    spec = CATALOG['get_resource']
    arguments = spec.arguments.model_validate({'resource_type': resource_type, 'resource_id': str(resource_id)})
    return json.loads(asyncio.run(spec.run({'config': config}, arguments)))


def links(found) -> list[tuple]:
    return [(item['type'], item['name'], item['relation']) for item in found['links']]


def test_get_resource_reads_deployments_relations_and_connections(world):
    w = world
    assert links(get(w, 'environment', w.prod)) == [
        ('service', 'api', 'contains'),
        ('service', 'pg', 'contains'),
        ('server', 'db', 'contains'),
        ('server', 'web', 'contains'),
    ]
    assert links(get(w, 'server', w.lonely)) == []
    assert links(get(w, 'server', w.web)) == [('environment', 'prod', 'in_environment'), ('service', 'api', 'runs')]
    api = get(w, 'service', w.api)
    assert links(api) == [
        ('server', 'web', 'deployed_on'),
        ('environment', 'prod', 'in_environment'),
        ('code_project', 'shop', 'built_from'),
        ('service', 'pg', 'depends_on'),
        ('service', 'worker', 'has_part'),
        ('service', 'pg', 'connects_to'),
    ]
    connection = api['links'][-1]
    assert {key: connection[key] for key in ('id', 'port', 'protocol', 'description', 'from_server', 'to_server')} == {
        'id': str(w.pg),
        'port': 5432,
        'protocol': 'postgres',
        'description': 'SQL',
        'from_server': 'web',
        'to_server': 'db',
    }
    assert api['links'][3]['description'] == 'orders'
    assert api['external'] == [
        {'host': 'smtp.example.com', 'port': 587, 'protocol': 'smtp', 'description': '', 'from_server': 'web'}
    ]
    pg = get(w, 'service', w.pg)
    assert links(pg) == [
        ('server', 'db', 'deployed_on'),
        ('environment', 'prod', 'in_environment'),
        ('service', 'api', 'needed_by'),
        ('service', 'api', 'connected_from'),
    ]
    assert pg['links'][-1]['port'] == 5432 and pg['external'] == []
    assert links(get(w, 'code_project', w.shop)) == [('service', 'api', 'builds')]


def test_documents_of_an_environment_list_what_is_deployed_in_it(world):
    w = world
    with w.store.connect() as conn:
        for title, column, key in (
            ('api notes', 'service_id', w.api),
            ('web notes', 'server_id', w.web),
            ('ghost notes', 'service_id', w.ghost),
            ('lonely notes', 'server_id', w.lonely),
            ('old notes', 'code_project_id', w.old),
            ('shop notes', 'code_project_id', w.shop),
        ):
            saved = live_document(conn, w.project, title.replace(' ', '-'), title, f'# {title}')
            link_document(conn, w.project, saved['id'], column, key)
        assert [d['title'] for d in resource_document_list(conn, w.project, 'environment', w.prod)] == [
            'api notes',
            'web notes',
        ]
        assert [d['title'] for d in resource_document_list(conn, w.project, 'service', w.pg)] == []
        assert [d['title'] for d in resource_document_list(conn, w.project, 'service', w.api)] == [
            'api notes',
            'shop notes',
            'web notes',
        ]


def test_the_agent_wizard_reads_code_projects_from_relations(world):
    w = world
    with w.store.connect() as conn:
        services = {row['name']: row for row in wizard.inventory(conn, w.project)['services']}
    assert services['api']['repository_ids'] == [w.shop] and services['pg']['repository_ids'] == []


def test_a_servers_environments_come_from_its_deployments(world):
    w = world
    with w.store.connect() as conn:
        assert [e['name'] for e in inventory.list_environments(conn, w.project, server_id=w.web)] == ['prod']
        assert inventory.list_environments(conn, w.project, server_id=w.lonely) == []
    page = w.client.get(f'/api/servers/{w.lonely}', headers=HEADERS).json()
    assert page['environments'] == []
    page = w.client.get(f'/api/servers/{w.web}', headers=HEADERS).json()
    assert [e['name'] for e in page['environments']] == ['prod']


def test_service_and_environment_lists_read_deployments_and_relations(world):
    w = world
    services = {s['name']: s for s in w.client.get('/api/services', headers=HEADERS).json()['services']}
    api, pg = services['api'], services['pg']
    assert [e['name'] for e in api['environments']] == ['prod'] and services['ghost']['environments'] == []
    assert [r['name'] for r in api['repositories']] == ['shop'] and pg['repositories'] == []
    assert [(d['name'], d['description']) for d in api['dependencies']] == [('pg', 'orders')]
    assert [d['name'] for d in pg['dependents']] == ['api']
    [deployment] = api['deployments']
    assert (deployment['environment'], deployment['server']['name'], deployment['port']) == ('prod', 'web', 8080)
    context = w.client.get(f'/api/services/{w.api}/context', headers=HEADERS).json()['service']
    assert context == api
    environments = w.client.get('/api/environments', headers=HEADERS).json()['environments']
    [prod] = environments
    assert [s['name'] for s in prod['services']] == ['api', 'pg']
    assert [s['name'] for s in prod['servers']] == ['db', 'web']
    assert w.client.get(f'/api/environments/{w.prod}', headers=HEADERS).json() == prod


def test_the_lists_leave_out_rows_whose_ends_were_added_after_they_were_read(world, monkeypatch):
    """The lists read rows one query after another: a deployment or relation added meanwhile (its new server or
    service not read yet) is left out instead of failing the read."""
    w = world
    late = uuid4()
    real_deployments, real_relations = inventory.list_deployments, inventory.list_relations

    def deployments(*args, **kwargs):
        rows = real_deployments(*args, **kwargs)
        return [*rows, {**rows[0], 'id': uuid4(), 'server_id': late}, {**rows[0], 'id': uuid4(), 'service_id': late}]

    def relations(*args, **kwargs):
        rows = real_relations(*args, **kwargs)
        return [*rows, {**rows[0], 'id': uuid4(), 'to_code_project_id': late}]

    monkeypatch.setattr(inventory, 'list_deployments', deployments)
    monkeypatch.setattr(inventory, 'list_relations', relations)
    services = {s['name']: s for s in w.client.get('/api/services', headers=HEADERS).json()['services']}
    assert len(services['api']['deployments']) == 1 and [r['name'] for r in services['api']['repositories']] == ['shop']
    [prod] = w.client.get('/api/environments', headers=HEADERS).json()['environments']
    assert [s['name'] for s in prod['servers']] == ['db', 'web']


def test_the_map_draws_connections_between_deployments_and_external_hosts(world):
    w = world
    view = w.client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 3}).json()
    names = {n['id']: n['name'] for n in [*view['nodes'], *view['boundary']]}
    assert sorted((names[e['from']], names[e['to']], e['type'], e['count']) for e in view['edges']) == [
        ('api', 'pg', 'connects_to', 1),
        ('api', 'smtp.example.com', 'connects_to', 1),
    ]
    assert [(b['name'], b['kind']) for b in view['boundary']] == [('smtp.example.com', 'external_host')]
    assert 'ghost' not in names.values()
    services = w.client.get('/api/map/view/services', headers=HEADERS).json()
    names = {n['id']: n['name'] for n in [*services['nodes'], *services['boundary']]}
    assert sorted((names[e['from']], names[e['to']], e['type']) for e in services['edges']) == [
        ('api', 'pg', 'connects_to'),
        ('api', 'pg', 'depends_on'),
        ('api', 'shop', 'built_from'),
        ('api', 'smtp.example.com', 'connects_to'),
        ('worker', 'api', 'part_of'),
    ]


def test_an_empty_member_server_counts_wherever_an_environment_holds_its_servers(world):
    """lonely, put in prod with no deployment, is brought by prod, listed by get_resource, its documents and its page."""
    w = world
    with w.store.connect() as conn:
        inventory.add_server_to_environment(conn, w.project, w.prod, w.lonely, {'actor_id': None})
        saved = live_document(conn, w.project, 'lonely-notes', 'lonely notes', '# lonely notes')
        link_document(conn, w.project, saved['id'], 'server_id', w.lonely)
        assert 'lonely notes' in [d['title'] for d in resource_document_list(conn, w.project, 'environment', w.prod)]
        assert str(w.lonely) in run_access.bring(conn, w.project, [], [w.prod])['server_ids']
    assert kinds(held(w, environment=[w.prod]))[('server', str(w.lonely))] == f'via environment {w.prod}'
    assert ('server', 'lonely', 'contains') in links(get(w, 'environment', w.prod))
    assert links(get(w, 'server', w.lonely)) == [('environment', 'prod', 'in_environment')]
    page = w.client.get(f'/api/servers/{w.lonely}', headers=HEADERS).json()
    assert [e['name'] for e in page['environments']] == ['prod']
