from uuid import UUID, uuid4

import pytest

from test_api import _client
from test_repositories import HEADERS


def test_delete_server_requires_confirmation_revision_identity_and_scope(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    value = {'name': 'Disposable host', 'hostname': 'delete.example.test', 'description': 'Test host', 'tags': ['test']}
    server = client.post('/api/servers', headers=HEADERS, json=value).json()
    endpoint = f'/api/servers/{server["id"]}'
    params = {'revision': server['revision'], 'confirmed': True}
    assert client.delete(endpoint, params=params).status_code == 403
    assert client.delete(endpoint, params=params, headers={'X-Forwarded-Groups': 'docs-admins'}).status_code == 403
    assert client.delete(endpoint, params={'revision': 1}, headers=HEADERS).status_code == 400
    assert client.delete(endpoint, params={'confirmed': True}, headers=HEADERS).status_code == 400
    assert (
        client.put(endpoint, headers=HEADERS, json={**value, 'revision': 1, 'name': 'Renamed host'}).status_code == 200
    )
    assert client.delete(endpoint, params=params, headers=HEADERS).status_code == 409
    with store.connect() as conn:
        foreign_project = store.project(conn, 'foreign')
        foreign = conn.execute(
            "INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'Foreign','foreign.test',false) RETURNING id",
            (foreign_project,),
        ).fetchone()['id']
    assert client.delete(f'/api/servers/{foreign}', params=params, headers=HEADERS).status_code == 404
    response = client.delete(endpoint, params={**params, 'revision': 2}, headers=HEADERS)
    assert response.status_code == 200 and response.json() == {'id': server['id']}
    assert client.get('/api/servers', headers=HEADERS).json()['servers'] == []
    assert client.delete(endpoint, params={**params, 'revision': 2}, headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert conn.execute('SELECT 1 FROM ka.servers WHERE id=%s', (UUID(server['id']),)).fetchone() is None
        assert conn.execute('SELECT 1 FROM ka.servers WHERE id=%s', (foreign,)).fetchone() is not None
        event = conn.execute(
            "SELECT details FROM ka.audit_events WHERE resource_id=%s AND action='server.deleted'",
            (UUID(server['id']),),
        ).fetchone()
        assert event['details'] == {'name': 'Renamed host', 'hostname': value['hostname'], 'revision': 2}


def test_linked_server_requires_removing_deployments_first(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    server = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'Linked host', 'hostname': 'linked.test'}
    ).json()
    service = client.post(
        '/api/services',
        headers=HEADERS,
        json={'name': 'API', 'deployments': [{'server_id': server['id'], 'environment': 'test'}]},
    ).json()
    endpoint = f'/api/servers/{server["id"]}'
    params = {'revision': 1, 'confirmed': True}
    response = client.delete(endpoint, headers=HEADERS, params=params)
    assert response.status_code == 409 and 'service deployments' in response.json()['error']['message']
    context = client.get(f'/api/services/{service["id"]}/context', headers=HEADERS).json()['service']
    assert context['deployments'][0]['server']['id'] == server['id']
    assert (
        client.put(
            f'/api/services/{service["id"]}', headers=HEADERS, json={'name': 'API', 'revision': 1, 'deployments': []}
        ).status_code
        == 200
    )
    assert client.delete(endpoint, headers=HEADERS, params=params).status_code == 200
    assert client.get(f'/api/services/{service["id"]}/context', headers=HEADERS).json()['service']['name'] == 'API'


def test_server_with_test_history_is_preserved(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    server = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'Tested host', 'hostname': 'tested.test'}
    ).json()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute(
            "INSERT INTO ka.jobs(project_id,kind,server_id) VALUES (%s,'server_test',%s)", (project, UUID(server['id']))
        )
    response = client.delete(f'/api/servers/{server["id"]}', headers=HEADERS, params={'revision': 1, 'confirmed': True})
    assert response.status_code == 409 and 'history' in response.json()['error']['message']
    assert client.get('/api/servers', headers=HEADERS).json()['servers'] == [server]


def test_server_edits_propagate_to_service_context_preserving_access_and_links(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    value = {
        'name': 'App host',
        'hostname': 'App.Internal.',
        'description': '  Runs the API  ',
        'tags': [' Production ', 'EU-West', 'production'],
    }
    response = client.post('/api/servers', headers=HEADERS, json=value)
    assert response.status_code == 201, response.text
    server = response.json()
    assert server['hostname'] == 'app.internal' and server['description'] == 'Runs the API'
    assert server['tags'] == ['production', 'eu-west'] and server['revision'] == 1
    service_id = client.post(
        '/api/services',
        headers=HEADERS,
        json={'name': 'API', 'deployments': [{'server_id': server['id'], 'environment': 'production'}]},
    ).json()['id']
    initial = client.get(f'/api/services/{service_id}/context', headers=HEADERS).json()['service']
    with store.connect() as conn:
        conn.execute(
            """UPDATE ka.servers SET username='operator',enabled=true,allow_sudo=true,
            auth_kind='ssh_agent',known_hosts='private-host-key',last_test_error='private-error'
            WHERE id=%s""",
            (UUID(server['id']),),
        )
    updated_value = {
        'name': 'Renamed app host',
        'hostname': '2001:db8::2',
        'description': 'Runs API and worker',
        'tags': ['staging', 'workers'],
        'revision': 1,
    }
    response = client.put(f'/api/servers/{server["id"]}', headers=HEADERS, json=updated_value)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated['id'] == server['id'] and updated['revision'] == 2
    assert updated['created_at'] == server['created_at'] and updated['updated_at'] > server['updated_at']
    assert updated['access_enabled'] and updated['allow_sudo'] and updated['ssh_username'] == 'operator'
    context = client.get(f'/api/services/{service_id}/context', headers=HEADERS)
    deployment = context.json()['service']['deployments'][0]
    assert deployment['id'] == initial['deployments'][0]['id']
    assert deployment['server']['description'] == updated_value['description']
    assert deployment['server']['tags'] == updated_value['tags']
    assert deployment['server']['hostname'] == '2001:db8::2'
    inventory = client.get('/api/servers', headers=HEADERS)
    assert inventory.json()['servers'] == [updated]
    for body in [response.text, context.text, inventory.text]:
        for private in ['credential', 'known_hosts', 'private-host-key', 'private-error', 'last_test_error']:
            assert private not in body
    with store.connect() as conn:
        raw = conn.execute('SELECT known_hosts,auth_kind FROM ka.servers WHERE id=%s', (UUID(server['id']),)).fetchone()
        assert raw == {'known_hosts': 'private-host-key', 'auth_kind': 'ssh_agent'}
        events = conn.execute(
            'SELECT action,details FROM ka.audit_events WHERE resource_id=%s ORDER BY created_at,id',
            (UUID(server['id']),),
        ).fetchall()
        assert events == [
            {'action': 'server.registered', 'details': {'revision': 1, 'ssh_allowed': False}},
            {'action': 'server.updated', 'details': {'revision': 2, 'ssh_allowed': False}},
        ]


def test_stale_server_edit_and_duplicate_name_roll_back(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    value = {'name': 'App host', 'hostname': 'localhost', 'tags': ['prod']}
    server = client.post('/api/servers', headers=HEADERS, json=value).json()
    client.post('/api/servers', headers=HEADERS, json={'name': 'Taken', 'hostname': 'localhost'})
    endpoint = f'/api/servers/{server["id"]}'
    assert client.put(endpoint, headers=HEADERS, json={**value, 'name': 'Taken', 'revision': 1}).status_code == 409
    assert client.put(endpoint, headers=HEADERS, json=value).status_code == 409
    saved = client.put(endpoint, headers=HEADERS, json={**value, 'revision': 1, 'tags': []})
    assert saved.status_code == 200 and saved.json()['tags'] == [] and saved.json()['revision'] == 2
    assert client.put(endpoint, headers=HEADERS, json={**value, 'revision': 1}).status_code == 409
    assert client.post('/api/servers', headers=HEADERS, json=value).status_code == 409
    servers = client.get('/api/servers', headers=HEADERS).json()['servers']
    assert next(s for s in servers if s['id'] == server['id']) == saved.json()
    with store.connect() as conn:
        assert (
            conn.execute(
                'SELECT count(*) AS n FROM ka.audit_events WHERE resource_id=%s', (UUID(server['id']),)
            ).fetchone()['n']
            == 2
        )


def test_server_updates_require_identity_and_project_scope(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        project = store.project(conn, 'another-app')
        foreign = conn.execute(
            "INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'Foreign','localhost',false) RETURNING id",
            (project,),
        ).fetchone()['id']
    value = {'name': 'Changed', 'hostname': 'localhost', 'revision': 1}
    assert client.put(f'/api/servers/{foreign}', headers=HEADERS, json=value).status_code == 404
    assert client.put(f'/api/unknown/servers/{foreign}', headers=HEADERS, json=value).status_code == 404
    assert (
        client.put(f'/api/servers/{foreign}', headers={'X-Forwarded-Groups': 'docs-admins'}, json=value).status_code
        == 403
    )
    assert _client(tmp_path).put(f'/api/servers/{foreign}', json=value).status_code == 403
    assert client.get('/api/servers', headers=HEADERS).json()['servers'] == []
    with store.connect() as conn:
        assert conn.execute('SELECT name,revision FROM ka.servers WHERE id=%s', (foreign,)).fetchone() == {
            'name': 'Foreign',
            'revision': 1,
        }


@pytest.mark.parametrize(
    'extra',
    [
        {'tags': ['']},
        {'tags': ['x' * 65]},
        {'tags': ['x'] * 33},
        {'tags': ['a,b']},
        {'tags': ['a\nb']},
        {'tags': [None]},
        {'tags': 'prod'},
        {'description': 'x' * 4001},
        {'allow_sudo': True},
        {'credential_id': str(uuid4())},
        {'revision': True},
        {'ssh_allowed': 'true'},
        {'ssh_allowed': 1},
        {'ssh_allowed': None},
    ],
)
def test_invalid_server_attributes_rejected_before_storage(tmp_path, extra):
    client = _client(tmp_path)
    value = {'name': 'Host', 'hostname': 'localhost', **extra}
    assert client.post('/api/servers', headers=HEADERS, json=value).status_code == 400
    assert client.put(f'/api/servers/{uuid4()}', headers=HEADERS, json=value).status_code == 400


def test_ssh_permission_persists_in_inventory_and_service_context_without_enabling_connection(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    value = {'name': 'SSH policy host', 'hostname': 'policy.test', 'ssh_allowed': True}
    response = client.post('/api/servers', headers=HEADERS, json=value)
    assert response.status_code == 201, response.text
    server = response.json()
    assert server['ssh_allowed'] is True
    assert server['access_enabled'] is False and server['allow_sudo'] is False and server['ssh_username'] is None
    service = client.post(
        '/api/services',
        headers=HEADERS,
        json={'name': 'Policy service', 'deployments': [{'server_id': server['id'], 'environment': 'test'}]},
    ).json()
    context_url = f'/api/services/{service["id"]}/context'
    assert client.get(context_url, headers=HEADERS).json()['service']['deployments'][0]['server']['ssh_allowed'] is True
    path = f'/api/servers/{server["id"]}'
    # Inventory callers that omit the new field preserve the current policy.
    saved = client.put(
        path, headers=HEADERS, json={'name': value['name'], 'hostname': value['hostname'], 'revision': 1}
    )
    assert saved.status_code == 200 and saved.json()['ssh_allowed'] is True
    disabled = client.put(path, headers=HEADERS, json={**value, 'ssh_allowed': False, 'revision': 2})
    assert disabled.status_code == 200 and disabled.json()['ssh_allowed'] is False
    assert client.put(path, headers=HEADERS, json={**value, 'revision': 2}).status_code == 409
    assert (
        client.get(context_url, headers=HEADERS).json()['service']['deployments'][0]['server']['ssh_allowed'] is False
    )
    assert client.get('/api/servers', headers=HEADERS).json()['servers'] == [disabled.json()]
    with store.connect() as conn:
        stored = conn.execute(
            'SELECT ssh_allowed,enabled,allow_sudo,username,credential_id FROM ka.servers WHERE id=%s',
            (UUID(server['id']),),
        ).fetchone()
        assert stored == {
            'ssh_allowed': False,
            'enabled': False,
            'allow_sudo': False,
            'username': None,
            'credential_id': None,
        }
        assert (
            conn.execute('SELECT count(*) AS n FROM ka.jobs WHERE server_id=%s', (UUID(server['id']),)).fetchone()['n']
            == 0
        )
        events = conn.execute(
            "SELECT details->'ssh_allowed' AS policy FROM ka.audit_events WHERE resource_id=%s ORDER BY created_at",
            (UUID(server['id']),),
        ).fetchall()
        assert [row['policy'] for row in events] == [True, True, False]
