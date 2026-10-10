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
    production = client.post('/api/environments', headers=HEADERS, json={'name': 'production', 'server_ids': [server['id']]}).json()
    api = client.post('/api/services', headers=HEADERS, json={'name': 'api', 'environment_ids': [production['id']],
        'deployments': [{'server_id': server['id'], 'environment_id': production['id'], 'runtime_name': 'api'}]}).json()
    return server['id'], production['id'], api['id']


def test_infrastructure_view_from_the_inventory(client):
    server, production, api = setup(client)
    view = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 3}).json()
    assert [(n['name'], n['entry_kind']) for n in view['nodes']] == [('production', 'environment'), ('prod-1', 'server'), ('api', 'service')]
    assert set(view) == {'view', 'depth', 'root', 'breadcrumb', 'nodes', 'boundary', 'edges'}
    for fmt, kind in (('mermaid', 'text/plain'), ('d2', 'text/plain')):
        response = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'format': fmt})
        assert response.status_code == 200 and response.headers['content-type'].startswith(kind) and '"production"' in response.text
    if shutil.which('d2'):
        svg = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'format': 'svg', 'layout': 'elk'})
        assert svg.status_code == 200 and svg.headers['content-type'] == 'image/svg+xml'


def test_a_removed_placement_is_not_found_and_bad_input_is_refused(client):
    server, production, api = setup(client)
    root = f'server:{uuid4()}:{server}'
    response = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'root': root})
    assert (response.status_code, response.json()['error']['message']) == (404, 'Node not found.')
    assert client.get('/api/map/view/business', headers=HEADERS).status_code == 400  # the app maps request validation to 400
    assert client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 4}).status_code == 400


def test_links_and_search(client):
    server, production, api = setup(client)
    worker = client.post('/api/services', headers=HEADERS, json={'name': 'worker'}).json()['id']
    client.post('/api/inventory/links', headers=HEADERS, json={'from': {'kind': 'service', 'id': api}, 'to': {'kind': 'service', 'id': worker},
                                                             'kind': 'depends_on'})
    view = client.get('/api/map/view/services', headers=HEADERS).json()
    assert [(e['from'], e['to'], e['type']) for e in view['edges']] == [(f'service:{api}', f'service:{worker}', 'depends_on')]
    hits = client.get('/api/map/search', headers=HEADERS, params={'q': 'wor'}).json()['entries']
    assert hits == [{'entry_kind': 'service', 'entry_id': worker, 'name': 'worker'}]
    assert client.get('/api/map/search', headers=HEADERS, params={'q': 'p', 'kind': 'server'}).json()['entries'][0]['name'] == 'prod-1'
