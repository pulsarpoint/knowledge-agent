"""Environments → System map API: nodes in four layers, views of one node's inside, search."""
from uuid import uuid4

import pytest

from knowledge_admin import system_graph_render
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def make(client, name, parent=None, layer='software', kind='service', **extra) -> dict:
    made = client.post('/api/graph/nodes', headers=HEADERS,
                       json={'layer': layer, 'kind': kind, 'name': name, 'parent_id': parent, **extra})
    assert made.status_code == 201, made.text
    return made.json()['node']


def post(client, **body):
    return client.post('/api/graph/nodes', headers=HEADERS, json={'layer': 'software', 'kind': 'service', 'name': 'x', **body})


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def actions(store, prefix='graph.') -> list[str]:
    with store.connect() as conn:
        return [row['action'] for row in conn.execute(
            "SELECT action FROM ka.audit_events WHERE starts_with(action, %s) ORDER BY created_at", (prefix,)).fetchall()]


def link(store, a, b, type='calls') -> None:
    with store.connect() as conn:
        conn.execute("""INSERT INTO ka.kg_edges(project_id,from_id,to_id,type)
            SELECT project_id,%s,%s,%s FROM ka.kg_nodes WHERE id=%s""", (a, b, type, a))


def change(client, node, **values):
    current = client.get(f"/api/graph/nodes/{node['id']}", headers=HEADERS).json()['node']
    body = {key: current[key] for key in ('kind', 'name', 'description', 'properties', 'parent_id', 'revision')}
    return client.put(f"/api/graph/nodes/{node['id']}", headers=HEADERS, json={**body, **values})


def test_create_and_read(client, store):
    api = make(client, ' admin-api ', description='The admin API', properties={'port': 8766})
    assert (api['name'], api['layer'], api['kind'], api['revision'], api['parent_id'], api['properties'], api['description']) == (
        'admin-api', 'software', 'service', 1, None, {'port': 8766}, 'The admin API')
    events = make(client, 'Event processor', parent=api['id'], kind='module')
    assert [crumb['name'] for crumb in events['breadcrumb']] == ['admin-api', 'Event processor']
    got = client.get(f"/api/graph/nodes/{api['id']}", headers=HEADERS).json()['node']
    assert got['children'] == [{'id': events['id'], 'name': 'Event processor', 'kind': 'module', 'child_count': 0}]
    assert (got['outgoing'], got['incoming'], got['documents'], got['breadcrumb']) == (
        [], [], [], [{'id': api['id'], 'name': 'admin-api', 'kind': 'service'}])
    assert message(client.get(f'/api/graph/nodes/{uuid4()}', headers=HEADERS)) == (404, 'Node not found.')
    assert actions(store) == ['graph.node.created', 'graph.node.created']


def test_input_is_validated(client):
    assert message(post(client, name='  ')) == (400, 'Enter a name.')
    assert message(post(client, kind=' ')) == (400, 'Enter a kind.')
    for body in [{'layer': 'network'}, {'name': 'x' * 161}, {'kind': 'k' * 61}, {'description': 'd' * 4001},
                 {'properties': [1]}, {'surprise': 1}, {'parent_id': 'nope'}]:
        assert post(client, **body).status_code == 400, body
    assert message(post(client, properties={'x': 'y' * 16400})) == (400, 'Properties are too large (16 KB at most).')


def test_nul_characters_are_refused(client):
    for body, label in [({'name': 'a\x00b'}, 'Name'), ({'kind': 'k\x00'}, 'Kind'), ({'description': '\x00'}, 'Description'),
                        ({'properties': {'deep': ['\x00']}}, 'Properties'), ({'properties': {'k\x00': 1}}, 'Properties')]:
        assert message(post(client, **body)) == (400, f'Remove the NUL character from {label}.')


def test_sibling_names_are_unique(client):
    api = make(client, 'admin-api')
    assert message(post(client, name='ADMIN-API')) == (409, 'A node named "ADMIN-API" already exists here.')
    make(client, 'admin-api', layer='infrastructure', kind='container')
    make(client, 'admin-api', parent=api['id'], kind='module')
    other = make(client, 'other')
    assert message(change(client, other, name='Admin-Api')) == (409, 'A node named "Admin-Api" already exists here.')


def test_parents_must_exist_in_the_same_layer(client):
    api = make(client, 'admin-api')
    assert message(post(client, parent_id=str(uuid4()))) == (404, 'Parent not found.')
    assert message(post(client, layer='data', kind='table', parent_id=api['id'])) == (422, 'Choose a parent in the same layer.')


def test_edit_and_stale_revision(client, store):
    api = make(client, 'admin-api')
    body = {'kind': 'service', 'name': 'Admin API', 'description': 'd', 'properties': {'a': 1}, 'parent_id': None, 'revision': 1}
    saved = client.put(f"/api/graph/nodes/{api['id']}", headers=HEADERS, json=body)
    assert saved.status_code == 200, saved.text
    assert (saved.json()['node']['name'], saved.json()['node']['revision']) == ('Admin API', 2)
    assert message(client.put(f"/api/graph/nodes/{api['id']}", headers=HEADERS, json=body)) == (
        409, 'This node was changed by someone else. Reload and try again.')
    assert message(client.put(f'/api/graph/nodes/{uuid4()}', headers=HEADERS, json=body)) == (404, 'Node not found.')
    assert client.put(f"/api/graph/nodes/{api['id']}", headers=HEADERS, json={**body, 'revision': 2, 'layer': 'data'}).status_code == 400
    assert actions(store)[-1] == 'graph.node.updated'


def test_moves_are_checked(client, store):
    api = make(client, 'admin-api')
    events = make(client, 'Event processor', parent=api['id'], kind='module')
    match = make(client, 'matching', parent=events['id'], kind='module')
    worker = make(client, 'repository-worker')
    assert message(change(client, api, parent_id=match['id'])) == (422, 'A node cannot be moved inside itself.')
    assert message(change(client, api, parent_id=api['id'])) == (422, 'A node cannot be moved inside itself.')
    moved = change(client, events, parent_id=worker['id'])
    assert moved.status_code == 200, moved.text
    assert [crumb['name'] for crumb in moved.json()['node']['breadcrumb']] == ['repository-worker', 'Event processor']
    assert actions(store)[-1] == 'graph.node.moved'
    assert change(client, events, parent_id=None).status_code == 200
    deepest = worker['id']
    for level in range(10):  # worker is level 1; this chain reaches level 11
        deepest = make(client, f'level {level + 2}', parent=deepest, kind='module')['id']
    assert message(change(client, events, parent_id=deepest)) == (422, 'Too deep: a layer has at most 12 levels.')  # 11 + 2


def test_delete(client, store):
    api = make(client, 'admin-api')
    events = make(client, 'Event processor', parent=api['id'], kind='module')
    assert message(client.delete(f"/api/graph/nodes/{api['id']}", headers=HEADERS)) == (409, 'Move or delete its children first.')
    assert client.delete(f"/api/graph/nodes/{events['id']}", headers=HEADERS).status_code == 204
    assert client.delete(f"/api/graph/nodes/{api['id']}", headers=HEADERS).status_code == 204
    assert message(client.delete(f"/api/graph/nodes/{api['id']}", headers=HEADERS)) == (404, 'Node not found.')
    assert actions(store)[-2:] == ['graph.node.deleted', 'graph.node.deleted']


def test_search(client):
    api = make(client, 'admin-api')
    make(client, 'Event processor', parent=api['id'], kind='module')
    make(client, 'Events table', layer='data', kind='table')
    make(client, '100% done', kind='module')
    found = lambda **params: [(row['name'], row['path'], row['layer']) for row in
                              client.get('/api/graph/nodes', headers=HEADERS, params=params).json()['nodes']]
    assert found(q='EVENT') == [('Event processor', ['admin-api'], 'software'), ('Events table', [], 'data')]
    assert found(q='event', layer='software') == [('Event processor', ['admin-api'], 'software')]
    assert found(q='%') == [('100% done', [], 'software')]
    assert [name for name, _, _ in found()] == ['100% done', 'admin-api', 'Event processor', 'Events table']
    assert len(found(limit=2)) == 2
    assert client.get('/api/graph/nodes', headers=HEADERS, params={'layer': 'network'}).status_code == 400


def test_views(client, store):
    api = make(client, 'admin-api')
    events = make(client, 'Event processor', parent=api['id'], kind='module')
    worker = make(client, 'repository-worker')
    link(store, events['id'], worker['id'])
    top = client.get('/api/graph/view/software', headers=HEADERS).json()
    assert [node['name'] for node in top['nodes']] == ['admin-api', 'repository-worker']
    assert [(edge['from'], edge['to'], edge['type'], edge['count']) for edge in top['edges']] == [(api['id'], worker['id'], 'calls', 1)]
    inside = client.get('/api/graph/view/software', headers=HEADERS, params={'root': api['id']}).json()
    assert ([node['name'] for node in inside['nodes']], [node['name'] for node in inside['boundary']]) == (
        ['Event processor'], ['repository-worker'])
    assert inside['root']['name'] == 'admin-api' and [crumb['name'] for crumb in inside['breadcrumb']] == ['admin-api']
    assert message(client.get('/api/graph/view/software', headers=HEADERS, params={'root': str(uuid4())})) == (404, 'Node not found.')
    assert message(client.get('/api/graph/view/data', headers=HEADERS, params={'root': api['id']})) == (
        422, 'That node is in the software layer.')
    for params in [{'depth': 0}, {'depth': 6}, {'format': 'png'}, {'layout': 'neato'}, {'root': 'nope'}]:
        assert client.get('/api/graph/view/software', headers=HEADERS, params=params).status_code == 400, params
    assert client.get('/api/graph/view/network', headers=HEADERS).status_code == 400


def test_view_formats(client, store, monkeypatch):
    api = make(client, 'admin-api')
    mermaid = client.get('/api/graph/view/software', headers=HEADERS, params={'format': 'mermaid'})
    assert mermaid.headers['content-type'].startswith('text/plain') and mermaid.text == 'flowchart LR\n  n1["admin-api"]\n'
    d2 = client.get('/api/graph/view/software', headers=HEADERS, params={'format': 'd2'})
    assert d2.headers['content-type'].startswith('text/plain') and d2.text == 'n1: "admin-api" {shape: rectangle}\n'
    monkeypatch.setattr(system_graph_render, 'svg', lambda text, layout: f'<svg data-layout="{layout}">{text}</svg>'.encode())
    svg = client.get('/api/graph/view/software', headers=HEADERS, params={'format': 'svg', 'layout': 'elk'})
    assert svg.headers['content-type'] == 'image/svg+xml'
    assert svg.text == '<svg data-layout="elk">n1: "admin-api" {shape: rectangle}\n</svg>'


def test_svg_without_d2(client, monkeypatch):
    make(client, 'admin-api')
    monkeypatch.setattr(system_graph_render.shutil, 'which', lambda name: None)
    assert message(client.get('/api/graph/view/software', headers=HEADERS, params={'format': 'svg'})) == (503, 'd2 is not installed.')


def test_graph_requires_admin(client):
    readers = {'X-Forwarded-Groups': 'readers', 'X-Forwarded-User': 'reader-id', 'X-Forwarded-Email': 'reader@example.test'}
    assert client.get('/api/graph/view/software', headers=readers).status_code == 403
    assert client.post('/api/graph/nodes', headers=readers, json={}).status_code == 403
