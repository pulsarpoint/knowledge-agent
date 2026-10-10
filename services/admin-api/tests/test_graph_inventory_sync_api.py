"""Inventory pages write the graph: Servers, Services (deployments, dependencies, code projects), Environments."""
import pytest

from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def nodes(client, layer, root=None) -> dict:
    params = {'root': root} if root else {}
    return {row['name']: row for row in client.get(f'/api/graph/view/{layer}', headers=HEADERS, params=params).json()['nodes']}


def detail(client, node_id) -> dict:
    return client.get(f'/api/graph/nodes/{node_id}', headers=HEADERS).json()['node']


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def test_services_page_saves_sync_the_graph(client, store):
    made = client.post('/api/graph/nodes', headers=HEADERS, json={'layer': 'software', 'kind': 'service', 'name': 'API'})
    assert made.status_code == 201  # a hand-made node that clashes with the service below
    one = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()
    two = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-2', 'hostname': 'two.internal'}).json()
    prod = client.post('/api/environments', headers=HEADERS, json={'name': 'production', 'server_ids': [one['id']]}).json()
    worker = client.post('/api/services', headers=HEADERS, json={'name': 'Worker'}).json()
    repo = str(store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', ''))
    response = client.post('/api/services', headers=HEADERS, json={
        'name': 'API', 'environment_ids': [prod['id']], 'repository_ids': [repo],
        'dependencies': [{'service_id': worker['id']}],
        'deployments': [{'server_id': one['id'], 'environment_id': prod['id'], 'runtime_name': 'api'},
                        {'server_id': two['id'], 'environment_id': prod['id']}]})
    assert response.status_code in (200, 201), response.text
    api = response.json()
    software = nodes(client, 'software')
    assert {'API', 'API (2)', 'Worker'} <= set(software)
    assert software['API (2)']['source_kind'] == 'service' and software['API']['source_kind'] is None
    infrastructure = nodes(client, 'infrastructure')
    assert {name: row['source_kind'] for name, row in infrastructure.items()} == {'production': 'environment'}
    in_production = nodes(client, 'infrastructure', infrastructure['production']['id'])
    assert {name: row['source_kind'] for name, row in in_production.items()} == {'prod-1': 'server', 'prod-2': 'server'}
    on_one = nodes(client, 'infrastructure', in_production['prod-1']['id'])
    on_two = nodes(client, 'infrastructure', in_production['prod-2']['id'])
    assert list(on_one) == ['api · production'] and list(on_two) == ['API · production']
    service_node = detail(client, software['API (2)']['id'])
    assert service_node['source'] == {'kind': 'service', 'id': api['id'], 'page': '/services', 'label': 'Services'}
    assert {(edge['type'], edge['node']['name'], edge['synced']) for edge in service_node['incoming']} >= {
        ('instance_of', 'api · production', True), ('instance_of', 'API · production', True), ('includes', 'production', True)}
    assert any(edge['type'] == 'uses' and edge['synced'] for edge in service_node['outgoing'])
    assert ('depends_on', 'Worker', True) in {(edge['type'], edge['node']['name'], edge['synced']) for edge in service_node['outgoing']}


def test_moving_a_deployment_moves_its_node(client):
    one = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()
    two = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-2', 'hostname': 'two.internal'}).json()
    api = client.post('/api/services', headers=HEADERS, json={'name': 'API', 'deployments': [
        {'server_id': one['id'], 'environment': 'production'}]}).json()
    current = client.get(f"/api/services/{api['id']}/context", headers=HEADERS).json()['service']
    deployment = current['deployments'][0]
    saved = client.put(f"/api/services/{api['id']}", headers=HEADERS, json={
        'name': 'API', 'revision': current['revision'],
        'deployments': [{'id': deployment['id'], 'server_id': two['id'], 'environment': 'production'}]})
    assert saved.status_code == 200, saved.text
    infrastructure = nodes(client, 'infrastructure')
    assert nodes(client, 'infrastructure', infrastructure['prod-1']['id']) == {}
    assert list(nodes(client, 'infrastructure', infrastructure['prod-2']['id'])) == ['API · production']


def test_deleting_a_service_keeps_hand_made_children(client):
    api = client.post('/api/services', headers=HEADERS, json={'name': 'API'}).json()
    service_node = nodes(client, 'software')['API']
    child = client.post('/api/graph/nodes', headers=HEADERS, json={
        'layer': 'software', 'kind': 'module', 'name': 'Event processor', 'parent_id': service_node['id']})
    assert child.status_code == 201
    current = client.get(f"/api/services/{api['id']}/context", headers=HEADERS).json()['service']
    assert client.delete(f"/api/services/{api['id']}?revision={current['revision']}&confirmed=true", headers=HEADERS).status_code == 200
    kept = detail(client, service_node['id'])
    assert kept['source'] is None and 'Removed from Services on ' in kept['description']
    bare = client.post('/api/servers', headers=HEADERS, json={'name': 'spare', 'hostname': 'spare.internal'}).json()
    assert 'spare' in nodes(client, 'infrastructure')
    assert client.delete(f"/api/servers/{bare['id']}?revision=1&confirmed=true", headers=HEADERS).status_code == 200
    assert 'spare' not in nodes(client, 'infrastructure')


def test_synced_nodes_refuse_only_what_the_inventory_owns(client):
    client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'})
    host = nodes(client, 'infrastructure')['prod-1']
    current = detail(client, host['id'])
    body = {key: current[key] for key in ('kind', 'name', 'description', 'properties', 'parent_id', 'revision')}
    url = f"/api/graph/nodes/{host['id']}"
    assert message(client.put(url, headers=HEADERS, json={**body, 'name': 'renamed'})) == (409, 'This node comes from Servers. Change it there.')
    assert message(client.put(url, headers=HEADERS, json={**body, 'kind': 'vm'})) == (409, 'This node comes from Servers. Change it there.')
    other = client.post('/api/graph/nodes', headers=HEADERS, json={'layer': 'infrastructure', 'kind': 'rack', 'name': 'Rack A'}).json()['node']
    assert message(client.put(url, headers=HEADERS, json={**body, 'parent_id': other['id']})) == (
        409, 'This node comes from Servers. Its place follows the inventory.')
    assert message(client.delete(url, headers=HEADERS)) == (409, 'This node comes from Servers. Delete it there.')
    saved = client.put(url, headers=HEADERS, json={**body, 'description': 'Main host', 'properties': {'cpu': 8}})
    assert saved.status_code == 200, saved.text
    assert saved.json()['node']['description'] == 'Main host'
    child = client.post('/api/graph/nodes', headers=HEADERS, json={'layer': 'infrastructure', 'kind': 'disk', 'name': 'disk', 'parent_id': host['id']})
    assert child.status_code == 201
    edge = client.post('/api/graph/edges', headers=HEADERS, json={'from_id': other['id'], 'to_id': host['id'], 'type': 'includes'})
    assert edge.status_code == 201 and edge.json()['edge']['type'] == 'includes'


def test_synced_edges_are_read_only(client):
    api = client.post('/api/services', headers=HEADERS, json={'name': 'API'}).json()
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()
    client.post('/api/environments', headers=HEADERS, json={'name': 'production', 'server_ids': [host['id']], 'service_ids': [api['id']]})
    environment = nodes(client, 'infrastructure')['production']
    [synced, *_] = detail(client, environment['id'])['outgoing']
    assert synced['synced'] is True
    url = f"/api/graph/edges/{synced['id']}"
    assert message(client.put(url, headers=HEADERS, json={'description': 'x', 'revision': synced['revision']})) == (
        409, 'This edge comes from the inventory. Change it in Environments.')
    assert message(client.delete(url, headers=HEADERS)) == (409, 'This edge comes from the inventory. Change it in Environments.')
    view = client.get('/api/graph/view/infrastructure', headers=HEADERS).json()
    includes = [edge for edge in view['edges'] if edge['type'] == 'includes']
    assert includes and all(edge['synced'] is True for edge in includes)


def test_graph_save_takes_the_project_lock_before_the_node_row(client, store):
    """An inventory write holds the project lock and then wants the node row; the graph save must queue behind that lock
    without already holding the row, or the two deadlock."""
    import threading
    import time

    made = client.post('/api/graph/nodes', headers=HEADERS, json={'layer': 'software', 'kind': 'module', 'name': 'Hand made'}).json()['node']
    outcome = {}

    def graph_save():
        outcome['response'] = client.put(f"/api/graph/nodes/{made['id']}", headers=HEADERS, json={
            'kind': 'module', 'name': 'Hand made', 'description': 'changed', 'revision': made['revision']})

    with store.connect() as inventory:
        project = inventory.execute('SELECT project_id FROM ka.kg_nodes WHERE id=%s', (made['id'],)).fetchone()['project_id']
        inventory.execute("SELECT pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(%s::text))", (str(project),))
        thread = threading.Thread(target=graph_save)
        thread.start()
        time.sleep(1)
        assert thread.is_alive()  # waiting for the project lock
        inventory.execute('SELECT 1 FROM ka.kg_nodes WHERE id=%s FOR NO KEY UPDATE', (made['id'],))  # would deadlock if held
        inventory.commit()
    thread.join(10)
    assert not thread.is_alive()
    assert outcome['response'].status_code == 200, outcome['response'].text


def test_every_inventory_page_keeps_the_graph(client, store):
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'h1', 'hostname': 'h1.internal'}).json()
    renamed = client.put(f"/api/servers/{host['id']}", headers=HEADERS, json={'name': 'h2', 'hostname': 'h1.internal', 'revision': 1})
    assert renamed.status_code == 200, renamed.text
    env = client.post('/api/environments', headers=HEADERS, json={'name': 'dev', 'server_ids': [host['id']]}).json()
    assert set(nodes(client, 'infrastructure')) == {'dev'}
    assert set(nodes(client, 'infrastructure', nodes(client, 'infrastructure')['dev']['id'])) == {'h2'}
    assert client.delete(f"/api/environments/{env['id']}?revision=1&confirmed=true", headers=HEADERS).status_code == 200
    assert client.delete(f"/api/servers/{host['id']}?revision=2&confirmed=true", headers=HEADERS).status_code == 200
    assert nodes(client, 'infrastructure') == {}
    repo = store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', '')
    assert len(nodes(client, 'software')) == 1
    archived = client.post(f'/api/code-projects/{repo}/archive', headers=HEADERS, json={})
    assert archived.status_code in (200, 204), archived.text
    assert nodes(client, 'software') == {}


def test_renaming_an_environment_renames_its_deployment_nodes(client):
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()
    prod = client.post('/api/environments', headers=HEADERS, json={'name': 'production', 'server_ids': [host['id']]}).json()
    api = client.post('/api/services', headers=HEADERS, json={
        'name': 'API', 'environment_ids': [prod['id']],
        'deployments': [{'server_id': host['id'], 'environment_id': prod['id']}]}).json()
    production = nodes(client, 'infrastructure')['production']
    server_node = nodes(client, 'infrastructure', production['id'])['prod-1']
    assert list(nodes(client, 'infrastructure', server_node['id'])) == ['API · production']
    current = client.get(f"/api/environments/{prod['id']}", headers=HEADERS).json()
    renamed = client.put(f"/api/environments/{prod['id']}", headers=HEADERS, json={
        'name': 'live', 'revision': current['revision'], 'server_ids': [host['id']], 'service_ids': [api['id']]})
    assert renamed.status_code == 200, renamed.text
    live = nodes(client, 'infrastructure')['live']
    renamed_server = nodes(client, 'infrastructure', live['id'])['prod-1']
    assert renamed_server['id'] == server_node['id']  # the server's node is kept across the rename
    assert list(nodes(client, 'infrastructure', renamed_server['id'])) == ['API · live']


def test_a_graph_name_clash_is_not_reported_as_an_inventory_duplicate(client, monkeypatch):
    from knowledge_admin import graph_sync
    client.post('/api/graph/nodes', headers=HEADERS, json={'layer': 'infrastructure', 'kind': 'server', 'name': 'prod-1'}).raise_for_status()
    monkeypatch.setattr(graph_sync, 'free_name', lambda taken, wanted: wanted.strip())  # the sync stops avoiding clashes
    response = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'})
    assert message(response) == (409, graph_sync.GRAPH_CLASH)
    environment = client.post('/api/environments', headers=HEADERS, json={'name': 'prod-1'})
    assert message(environment) == (409, graph_sync.GRAPH_CLASH)
