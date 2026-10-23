"""Environments → System map over HTTP: views from the inventory in every format, and search."""

import shutil
from uuid import uuid4

import pytest

from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def setup(client):
    server = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()
    production = client.post('/api/environments', headers=HEADERS, json={'name': 'production'}).json()
    api = client.post('/api/services', headers=HEADERS, json={'name': 'api'}).json()
    client.post(
        '/api/deployments',
        headers=HEADERS,
        json={
            'service_id': api['id'],
            'server_id': server['id'],
            'environment_id': production['id'],
            'runtime_name': 'api',
        },
    )
    return server['id'], production['id'], api['id']


def test_infrastructure_view_from_the_inventory(client):
    server, production, api = setup(client)
    view = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 3}).json()
    assert [(n['name'], n['entry_kind']) for n in view['nodes']] == [
        ('production', 'environment'),
        ('prod-1', 'server'),
        ('api', 'service'),
    ]
    assert set(view) == {'view', 'depth', 'root', 'breadcrumb', 'nodes', 'boundary', 'edges'}
    for fmt, kind in (('mermaid', 'text/plain'), ('d2', 'text/plain')):
        response = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'format': fmt})
        assert (
            response.status_code == 200
            and response.headers['content-type'].startswith(kind)
            and '"production"' in response.text
        )
    if shutil.which('d2'):
        svg = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'format': 'svg', 'layout': 'elk'})
        assert svg.status_code == 200 and svg.headers['content-type'] == 'image/svg+xml'


def test_a_removed_placement_is_not_found_and_bad_input_is_refused(client):
    server, production, api = setup(client)
    root = f'server:{uuid4()}:{server}'
    response = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'root': root})
    assert (response.status_code, response.json()['error']['message']) == (404, 'Node not found.')
    assert (
        client.get('/api/map/view/business', headers=HEADERS).status_code == 400
    )  # the app maps request validation to 400
    assert client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 4}).status_code == 400


def test_relations(client):
    server, production, api = setup(client)
    worker = client.post('/api/services', headers=HEADERS, json={'name': 'worker'}).json()['id']
    relation = {'from_service_id': api, 'kind': 'depends_on', 'to_service_id': worker}
    assert client.post('/api/relations', headers=HEADERS, json=relation).status_code == 201
    view = client.get('/api/map/view/services', headers=HEADERS).json()
    assert [(e['from'], e['to'], e['type']) for e in view['edges']] == [
        (f'service:{api}', f'service:{worker}', 'depends_on')
    ]
    assert client.get('/api/map/search', headers=HEADERS, params={'q': 'wor'}).status_code == 404


def test_drafts_are_drawn_marked_and_hidden_with_drafts_0(client, store):
    from knowledge_admin.inventory import store as inventory

    server, production, api = setup(client)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        cache = inventory.create_service(conn, project, {'name': 'cache'}, {'actor_id': None}, status='draft')
        inventory.create_deployment(
            conn,
            project,
            {'service_id': cache['id'], 'server_id': server, 'environment_id': production},
            {'actor_id': None},
            status='draft',
        )
    shown = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 3}).json()
    assert [(n['name'], n.get('draft')) for n in shown['nodes']] == [
        ('production', None),
        ('prod-1', None),
        ('api', None),
        ('cache', True),
    ]
    hidden = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 3, 'drafts': 0}).json()
    assert [n['name'] for n in hidden['nodes']] == ['production', 'prod-1', 'api']
    d2 = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 3, 'format': 'd2'}).text
    assert '"cache" {shape: rectangle; style.stroke-dash: 4}' in d2
