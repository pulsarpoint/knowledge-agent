"""inventory/api.py: the one REST API over inventory/store.py."""

from uuid import uuid4

import pytest

from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def error(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def made(client, segment: str, **values) -> dict:
    response = client.post(f'/api/{segment}', headers=HEADERS, json=values)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def world(client):
    """Two servers, two services, an environment, a deployment of each service."""
    w = {
        'a': made(client, 'servers', name='a', hostname='a.internal'),
        'b': made(client, 'servers', name='b', hostname='b.internal'),
        'api': made(client, 'services', name='api', service_role='application'),
        'db': made(client, 'services', name='db', service_role='database'),
        'prod': made(client, 'environments', name='prod'),
    }
    w['api_a'] = made(
        client,
        'deployments',
        service_id=w['api']['id'],
        server_id=w['a']['id'],
        environment_id=w['prod']['id'],
        port=8080,
    )
    w['db_b'] = made(
        client,
        'deployments',
        service_id=w['db']['id'],
        server_id=w['b']['id'],
        environment_id=w['prod']['id'],
        port=5432,
    )
    return w


def audit_actions(store, item_id) -> list[tuple]:
    with store.connect() as conn:
        return [
            (row['action'], row['actor_id'] is not None)
            for row in conn.execute(
                'SELECT action,actor_id FROM ka.audit_events WHERE resource_id=%s ORDER BY id', (item_id,)
            )
        ]


@pytest.mark.parametrize(
    'segment,values,change',
    [
        ('servers', {'name': 'web', 'hostname': 'Web.Internal.'}, {'description': 'Front end'}),
        ('services', {'name': 'shop', 'service_role': 'application'}, {'owner': 'web team'}),
        ('environments', {'name': 'staging'}, {'description': 'Before production'}),
    ],
)
def test_create_update_and_delete_an_item_with_revisions_and_audit(client, store, segment, values, change):
    item = made(client, segment, **values)
    assert item['revision'] == 1 and item['name'] == values['name']
    url = f'/api/{segment}/{item["id"]}'
    updated = client.put(url, headers=HEADERS, json={'revision': 1, **change})
    assert updated.status_code == 200, updated.text
    assert updated.json()['revision'] == 2 and updated.json()['name'] == values['name']  # omitted fields stay
    assert {k: updated.json()[k] for k in change} == change
    stale = client.put(url, headers=HEADERS, json={'revision': 1, **change})
    assert stale.status_code == 409 and 'changed since you read it' in stale.json()['error']['message']
    assert stale.json()['error']['code'] == 'STALE_REVISION'  # the UI tells a stale read from other refusals
    assert client.delete(url, headers=HEADERS, params={'revision': 1}).status_code == 409
    deleted = client.delete(url, headers=HEADERS, params={'revision': 2})
    assert deleted.status_code == 200 and deleted.json() == {'id': item['id']}
    assert client.delete(url, headers=HEADERS, params={'revision': 2}).status_code == 404
    kind = segment[:-1]
    assert audit_actions(store, item['id']) == [
        (f'{kind}.created', True),
        (f'{kind}.updated', True),
        (f'{kind}.deleted', True),
    ]


def test_a_new_server_has_no_ssh_access_and_the_lists_read_it(client):
    server = made(client, 'servers', name='web', hostname='10.0.0.1', ssh_allowed=True)
    assert server['ssh_allowed'] and not server['access_enabled'] and server['ssh_username'] is None
    assert client.get('/api/servers', headers=HEADERS).json()['servers'] == [server]


@pytest.mark.parametrize(
    'segment,values,message',
    [
        ('servers', {'name': 'web', 'hostname': 'https://web'}, 'Enter an IP address or hostname without a URL'),
        ('servers', {'name': 'web', 'hostname': 'web', 'enabled': True}, 'Unknown field: enabled'),
        ('servers', {'name': 'web', 'hostname': 'web', 'credential_id': str(uuid4())}, 'Unknown field: credential_id'),
        ('services', {'name': '  '}, 'name is required'),
        ('services', {'name': 'x', 'service_role': 'boss'}, 'service_role must be one of'),
        ('services', {'name': 'x', 'repository_ids': []}, 'Unknown field: repository_ids'),
        ('environments', {'name': 'x', 'service_ids': []}, 'Unknown field: service_ids'),
        ('environments', {'name': 'x', 'revision': 1}, 'Unknown field: revision'),
        ('deployments', {'service_id': 'nope'}, 'service_id must be an id'),
        (
            'connections',
            {'from_deployment_id': str(uuid4())},
            'Connect to a deployment, an external service or an external host',
        ),
        ('relations', {'from_service_id': str(uuid4()), 'kind': 'likes'}, 'kind must be one of'),
    ],
)
def test_invalid_input_is_a_400_with_the_rules_message(client, segment, values, message):
    status, text = error(client.post(f'/api/{segment}', headers=HEADERS, json=values))
    assert status == 400 and text.startswith(message)


def test_updates_need_the_revision_and_unknown_or_foreign_items_are_not_found(client, store, world):
    url = f'/api/services/{world["api"]["id"]}'
    assert error(client.put(url, headers=HEADERS, json={'owner': 'x'})) == (
        400,
        'revision is required: the revision you read',
    )
    assert error(client.put(url, headers=HEADERS, json={'revision': 1, 'token': 'x'})) == (400, 'Unknown field: token')
    assert client.delete(url, headers=HEADERS).status_code == 400
    assert client.delete(url, headers=HEADERS, params={'revision': 0}).status_code == 400
    with store.connect() as conn:
        foreign = conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'Foreign') RETURNING id",
            (store.project(conn, 'another-app'),),
        ).fetchone()['id']
    for item_id in (foreign, uuid4()):
        assert error(client.put(f'/api/services/{item_id}', headers=HEADERS, json={'revision': 1})) == (
            404,
            'Service not found',
        )
        assert client.delete(f'/api/services/{item_id}', headers=HEADERS, params={'revision': 1}).status_code == 404
        assert client.get(f'/api/services/{item_id}/deployments', headers=HEADERS).status_code == 404
    # a reference to another project's item is invalid input
    status, text = error(
        client.post(
            '/api/deployments',
            headers=HEADERS,
            json={'service_id': str(foreign), 'server_id': world['a']['id'], 'environment_id': world['prod']['id']},
        )
    )
    assert status == 400 and text == 'Choose an existing service in this project'


def test_duplicates_are_a_409_and_change_nothing(client, world):
    assert error(client.post('/api/services', headers=HEADERS, json={'name': ' API '})) == (
        409,
        'A service with this name already exists',
    )
    assert 'code' not in client.post('/api/services', headers=HEADERS, json={'name': 'api'}).json()['error']
    duplicate = {k: world['api_a'][k] for k in ('service_id', 'server_id', 'environment_id')}
    assert client.post('/api/deployments', headers=HEADERS, json=duplicate).status_code == 409
    assert [s['name'] for s in client.get('/api/services', headers=HEADERS).json()['services']] == ['api', 'db']
    assert (
        len(client.get(f'/api/services/{world["api"]["id"]}/deployments', headers=HEADERS).json()['deployments']) == 1
    )


def test_deployments_connections_and_their_list_reads(client, store, world):
    api, db = world['api_a'], world['db_b']
    connection = made(
        client, 'connections', from_deployment_id=api['id'], to_deployment_id=db['id'], protocol='postgres'
    )
    assert connection['port'] == 5432  # the target deployment's port
    external = made(client, 'connections', from_deployment_id=db['id'], to_host='Backup.Example.', port=873)
    assert external['to_host'] == 'backup.example' and external['protocol'] == 'tcp'
    zoned = {'from_deployment_id': db['id'], 'to_host': 'fe80::1%eth0', 'port': 873}
    assert error(client.post('/api/connections', headers=HEADERS, json=zoned)) == (
        400,
        'Enter an IP address without a zone id (%…)',
    )  # the rules refuse it, not the database's check
    ids = lambda response: [item['id'] for item in response.json()['connections']]
    assert ids(client.get(f'/api/services/{world["db"]["id"]}/connections', headers=HEADERS)) == [
        connection['id'],
        external['id'],
    ]
    assert ids(client.get(f'/api/deployments/{api["id"]}/connections', headers=HEADERS)) == [connection['id']]
    changed = client.put(
        f'/api/connections/{connection["id"]}', headers=HEADERS, json={'revision': 1, 'description': 'orders'}
    )
    assert changed.status_code == 200 and changed.json()['port'] == 5432 and changed.json()['revision'] == 2
    servers = client.get(f'/api/environments/{world["prod"]["id"]}/servers', headers=HEADERS).json()['servers']
    assert [s['name'] for s in servers] == ['a', 'b']
    deployments = client.get(f'/api/servers/{world["a"]["id"]}/deployments', headers=HEADERS).json()['deployments']
    assert [d['id'] for d in deployments] == [api['id']]
    moved = client.put(
        f'/api/deployments/{api["id"]}', headers=HEADERS, json={'revision': 1, 'server_id': world['b']['id']}
    )
    assert moved.status_code == 200 and moved.json()['server_id'] == world['b']['id']
    assert client.get(f'/api/servers/{world["a"]["id"]}/deployments', headers=HEADERS).json()['deployments'] == []
    # Deleting a deployment removes its connections; the others stay.
    assert client.delete(f'/api/deployments/{api["id"]}', headers=HEADERS, params={'revision': 2}).status_code == 200
    assert ids(client.get(f'/api/services/{world["db"]["id"]}/connections', headers=HEADERS)) == [external['id']]
    assert client.get(f'/api/deployments/{api["id"]}/connections', headers=HEADERS).status_code == 404
    assert audit_actions(store, connection['id']) == [('connection.created', True), ('connection.updated', True)]
    assert (
        client.delete(f'/api/connections/{external["id"]}', headers=HEADERS, params={'revision': 1}).status_code == 200
    )
    assert client.get(f'/api/services/{world["db"]["id"]}/connections', headers=HEADERS).json()['connections'] == []


@pytest.mark.parametrize('kind', ['server', 'service', 'environment'])
def test_deleting_an_item_deployments_use_is_refused(client, world, kind):
    item = world[{'server': 'a', 'service': 'api', 'environment': 'prod'}[kind]]
    refused = client.delete(f'/api/{kind}s/{item["id"]}', headers=HEADERS, params={'revision': 1})
    count = 2 if kind == 'environment' else 1
    assert error(refused) == (409, f'This {kind} is used by {count} deployment(s). Remove those deployments first')


def test_relations_and_the_services_list_built_from_them(client, store, world):
    repository = str(store.save('shop', RepositoryInput(remote_url='example/api'), 'admin', ''))
    depends = made(
        client, 'relations', from_service_id=world['api']['id'], kind='depends_on', to_service_id=world['db']['id']
    )
    built = made(
        client, 'relations', from_service_id=world['api']['id'], kind='built_from', to_code_project_id=repository
    )
    assert error(
        client.post(
            '/api/relations',
            headers=HEADERS,
            json={'from_service_id': world['api']['id'], 'kind': 'built_from', 'to_service_id': world['db']['id']},
        )
    ) == (400, 'built_from points at a code project')
    relations = client.get(f'/api/services/{world["db"]["id"]}/relations', headers=HEADERS).json()['relations']
    assert [r['id'] for r in relations] == [depends['id']]
    services = {s['name']: s for s in client.get('/api/services', headers=HEADERS).json()['services']}
    assert [d['name'] for d in services['api']['dependencies']] == ['db']
    assert [d['name'] for d in services['db']['dependents']] == ['api']
    assert [r['id'] for r in services['api']['repositories']] == [repository]
    assert services['api']['revision'] == 1  # a relation does not change the services
    described = client.put(
        f'/api/relations/{depends["id"]}', headers=HEADERS, json={'revision': 1, 'description': 'orders'}
    )
    assert described.status_code == 200 and described.json()['description'] == 'orders'
    assert client.delete(f'/api/relations/{built["id"]}', headers=HEADERS, params={'revision': 1}).status_code == 200
    assert audit_actions(store, built['id']) == [('relation.created', True), ('relation.deleted', True)]
    assert client.get('/api/services', headers=HEADERS).json()['services'][0]['repositories'] == []


def test_environment_reads_list_what_is_deployed_in_it(client, world):
    [environment] = client.get('/api/environments', headers=HEADERS).json()['environments']
    assert [s['name'] for s in environment['services']] == ['api', 'db']
    assert [s['name'] for s in environment['servers']] == ['a', 'b']
    assert client.get(f'/api/environments/{world["prod"]["id"]}', headers=HEADERS).json() == environment
    assert client.get(f'/api/environments/{uuid4()}', headers=HEADERS).status_code == 404
    assert client.get(f'/api/environments/{uuid4()}/servers', headers=HEADERS).status_code == 404


@pytest.mark.parametrize(
    'method,path',
    [
        ('POST', '/api/servers'),
        ('PUT', f'/api/services/{uuid4()}'),
        ('DELETE', f'/api/environments/{uuid4()}?revision=1'),
        ('POST', '/api/deployments'),
        ('POST', '/api/connections'),
        ('DELETE', f'/api/relations/{uuid4()}?revision=1'),
        ('GET', f'/api/services/{uuid4()}/connections'),
        ('GET', f'/api/environments/{uuid4()}/servers'),
    ],
)
def test_every_route_requires_admin_before_the_database(tmp_path, method, path):
    client = _client(tmp_path)
    assert client.request(method, path, json={'name': 'x'}).status_code == 403
    assert (
        client.request(method, path, json={'name': 'x'}, headers={'X-Forwarded-Groups': 'readers'}).status_code == 403
    )


def test_a_write_needs_the_persons_identity(client):
    headers = {k: v for k, v in HEADERS.items() if k.lower() != 'x-forwarded-user'}
    assert client.post('/api/services', headers=headers, json={'name': 'x'}).status_code == 403
    assert client.get('/api/services', headers=HEADERS).json()['services'] == []


def test_a_server_is_put_in_and_removed_from_an_environment(client, store, world):
    spare = made(client, 'servers', name='spare', hostname='spare.internal')
    prod = world['prod']['id']
    url = f'/api/environments/{prod}/servers/{spare["id"]}'
    assert client.put(url, headers=HEADERS).status_code == 204
    assert client.put(url, headers=HEADERS).status_code == 204  # already a member
    servers = client.get(f'/api/environments/{prod}/servers', headers=HEADERS).json()['servers']
    assert [s['name'] for s in servers] == ['a', 'b', 'spare']
    [environment] = client.get('/api/environments', headers=HEADERS).json()['environments']
    assert [s['name'] for s in environment['servers']] == ['a', 'b', 'spare']
    assert client.delete(url, headers=HEADERS).status_code == 204
    assert client.delete(url, headers=HEADERS).status_code == 404
    assert audit_actions(store, prod).count(('membership.added', True)) == 3
    assert audit_actions(store, prod).count(('membership.removed', True)) == 1

    refused = client.delete(f'/api/environments/{prod}/servers/{world["a"]["id"]}', headers=HEADERS)
    assert error(refused) == (400, 'Remove the deployments on this server in this environment first.')
    for path in (f'/api/environments/{uuid4()}/servers/{spare["id"]}', f'/api/environments/{prod}/servers/{uuid4()}'):
        assert client.put(path, headers=HEADERS).status_code == 404
        assert client.delete(path, headers=HEADERS).status_code == 404


def test_an_external_service_with_an_address_is_a_connection_target(client, world):
    mail = made(client, 'services', name='mail', service_type='external', address='mail.example.com')
    assert mail['address'] == 'mail.example.com'
    connection = made(client, 'connections', from_deployment_id=world['api_a']['id'], to_service_id=mail['id'], port=25)
    assert connection['to_service_id'] == mail['id']
    changed = client.put(f'/api/services/{mail["id"]}', headers=HEADERS, json={'revision': 1, 'service_type': 'custom'})
    assert error(changed) == (400, 'This service is the target of connections; remove them or keep the type external.')
    custom = client.post(
        '/api/connections',
        headers=HEADERS,
        json={'from_deployment_id': world['api_a']['id'], 'to_service_id': world['db']['id']},
    )
    assert error(custom) == (400, 'Connect to an external service, or to a deployment of this service.')
