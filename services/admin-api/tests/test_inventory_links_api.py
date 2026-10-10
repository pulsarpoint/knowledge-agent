"""Inventory → connections between services, servers, environments and code projects."""
from uuid import uuid4

import pytest

from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def entries(client, store):
    service = lambda name: client.post('/api/services', headers=HEADERS, json={'name': name}).json()['id']
    made = {'api': service('api'), 'worker': service('worker'),
            'prod-1': client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()['id'],
            'production': client.post('/api/environments', headers=HEADERS, json={'name': 'production'}).json()['id'],
            'code': str(store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', ''))}
    return made


def connect(client, kind, start, end, **fields):
    return client.post('/api/inventory/links', headers=HEADERS, json={'from': {'kind': start[0], 'id': start[1]},
                                                                      'to': {'kind': end[0], 'id': end[1]}, 'kind': kind, **fields})


def test_create_list_update_and_delete_a_connection(client, store):
    ids = entries(client, store)
    made = connect(client, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']),
                   description='reads its database', attributes={'port': '5432'})
    assert made.status_code == 201, made.text
    link = made.json()['link']
    assert (link['from'], link['to']) == ({'kind': 'service', 'id': ids['api'], 'name': 'api'},
                                          {'kind': 'server', 'id': ids['prod-1'], 'name': 'prod-1'})
    assert (link['description'], link['attributes'], link['revision']) == ('reads its database', {'port': '5432'}, 1)
    for kind, entry_id in (('service', ids['api']), ('server', ids['prod-1'])):  # either end lists it
        listed = client.get('/api/inventory/links', headers=HEADERS, params={'entry_kind': kind, 'entry_id': entry_id}).json()['links']
        assert [row['id'] for row in listed] == [link['id']]
    assert client.get('/api/inventory/links', headers=HEADERS, params={'entry_kind': 'service', 'entry_id': ids['worker']}).json()['links'] == []
    saved = client.put(f"/api/inventory/links/{link['id']}", headers=HEADERS,
                       json={'description': 'replica', 'attributes': {}, 'revision': 1})
    assert saved.status_code == 200 and saved.json()['link']['revision'] == 2
    assert message(client.put(f"/api/inventory/links/{link['id']}", headers=HEADERS,
                              json={'description': 'x', 'attributes': {}, 'revision': 1})) == (
        409, 'This connection was changed by someone else. Reload and try again.')
    assert client.delete(f"/api/inventory/links/{link['id']}", headers=HEADERS).status_code == 204
    assert message(client.delete(f"/api/inventory/links/{link['id']}", headers=HEADERS)) == (404, 'Connection not found.')
    with store.connect() as conn:
        actions = [row['action'] for row in conn.execute(
            "SELECT action FROM ka.audit_events WHERE action LIKE 'inventory.link.%' ORDER BY created_at,id")]
    assert actions == ['inventory.link.created', 'inventory.link.updated', 'inventory.link.deleted']


def test_kinds_only_join_the_entries_they_allow(client, store):
    ids = entries(client, store)
    assert message(connect(client, 'depends_on', ('service', ids['api']), ('server', ids['prod-1']))) == (
        422, 'A service can only depend on another service.')
    assert message(connect(client, 'built_from', ('server', ids['prod-1']), ('code_project', ids['code']))) == (
        422, 'Only a service can be built from a code project.')
    assert message(connect(client, 'connects_to', ('server', ids['prod-1']), ('server', ids['prod-1']))) == (
        422, 'An entry cannot connect to itself.')
    assert connect(client, 'depends_on', ('service', ids['api']), ('service', ids['worker'])).status_code == 201
    assert connect(client, 'depends_on', ('service', ids['worker']), ('service', ids['api'])).status_code == 201  # mutual
    assert message(connect(client, 'depends_on', ('service', ids['api']), ('service', ids['worker']))) == (
        409, 'This connection already exists.')
    assert connect(client, 'connects_to', ('service', ids['api']), ('service', ids['worker'])).status_code == 201  # another kind
    assert connect(client, 'built_from', ('service', ids['api']), ('code_project', ids['code'])).status_code == 201
    assert connect(client, 'connects_to', ('environment', ids['production']), ('server', ids['prod-1'])).status_code == 201


def test_unknown_archived_and_bad_input_are_refused(client, store):
    ids = entries(client, store)
    assert message(connect(client, 'connects_to', ('server', str(uuid4())), ('service', ids['api']))) == (
        404, 'That server was not found.')
    with store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (ids['code'],))
    assert message(connect(client, 'built_from', ('service', ids['api']), ('code_project', ids['code']))) == (
        404, 'That code project was not found.')
    too_many = {f'k{i}': 'v' for i in range(21)}
    assert message(connect(client, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']), attributes=too_many)) == (
        422, 'Use at most 20 attributes.')
    assert message(connect(client, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']), attributes={' ': 'v'})) == (
        422, 'Attribute names are 1–64 characters.')
    assert message(connect(client, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']), attributes={'k': 'v' * 501})) == (
        422, 'Attribute values are at most 500 characters.')
    assert connect(client, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']), attributes={'port': 5432}).status_code == 400  # schema errors are 400 in this app
    assert message(client.get('/api/inventory/links', headers=HEADERS, params={'entry_kind': 'service'})) == (
        400, 'Give both entry_kind and entry_id, or neither.')


def test_entries_of_another_project_are_not_found(client, store):
    ids = entries(client, store)
    with store.connect() as conn:
        other = store.project(conn, 'elsewhere')
        foreign = conn.execute("INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'far',%s,false) RETURNING id",
                               (other, f'{uuid4().hex[:8]}.internal')).fetchone()['id']
    assert message(connect(client, 'connects_to', ('service', ids['api']), ('server', str(foreign)))) == (
        404, 'That server was not found.')
    assert message(client.put(f'/api/inventory/links/{uuid4()}', headers=HEADERS,
                              json={'description': '', 'attributes': {}, 'revision': 1})) == (404, 'Connection not found.')


def test_a_map_edit_makes_an_open_services_form_stale(client, store):
    ids = entries(client, store)
    revision = next(s['revision'] for s in client.get('/api/services', headers=HEADERS).json()['services'] if s['id'] == ids['api'])
    assert connect(client, 'depends_on', ('service', ids['api']), ('service', ids['worker'])).status_code == 201
    stale = client.put(f"/api/services/{ids['api']}", headers=HEADERS, json={'name': 'api', 'revision': revision})
    assert stale.status_code == 409
    services = {s['name']: s for s in client.get('/api/services', headers=HEADERS).json()['services']}
    assert [d['service_id'] for d in services['api']['dependencies']] == [ids['worker']]
    assert [d['service_id'] for d in services['worker']['dependents']] == [ids['api']]


def test_deleting_an_entry_deletes_its_links(client, store):
    ids = entries(client, store)
    connect(client, 'connects_to', ('service', ids['worker']), ('server', ids['prod-1']))
    revision = next(s['revision'] for s in client.get('/api/services', headers=HEADERS).json()['services'] if s['id'] == ids['worker'])
    assert client.delete(f"/api/services/{ids['worker']}", headers=HEADERS,
                         params={'revision': revision, 'confirmed': True}).status_code == 200
    assert client.get('/api/inventory/links', headers=HEADERS).json()['links'] == []
