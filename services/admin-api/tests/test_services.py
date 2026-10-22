from uuid import UUID, uuid4

import psycopg
import pytest
from inventory_rows import environment_id

from access_helpers import give
from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS


def test_service_assets_link_multiple_repositories_and_server_deployments(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    first = store.save('shop', RepositoryInput(remote_url='example/api', token='private-api-token'), 'admin', '')
    second = store.save('shop', RepositoryInput(remote_url='example/shared'), 'admin', '')
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'App host', 'hostname': 'app.internal'})
    assert host.status_code == 201, host.text
    server = host.json()
    assert not server['ssh_allowed']
    assert not server['access_enabled'] and not server['allow_sudo'] and server['ssh_username'] is None
    value = {
        'name': 'Payments',
        'description': 'Handles payments',
        'owner': 'Backend team',
        'repository_ids': [str(first), str(second)],
        'deployments': [
            {
                'server_id': server['id'],
                'environment': 'production',
                'runtime': 'systemd',
                'runtime_name': 'payments.service',
                'port': 8080,
                'log_location': '/var/log/payments.log',
                'deployed_version': 'v1.2',
            },
            {'server_id': server['id'], 'environment': 'staging', 'runtime_name': 'payments-staging.service'},
        ],
    }
    response = client.post('/api/services', headers=HEADERS, json=value)
    assert response.status_code == 201, response.text
    service_id = response.json()['id']
    asset = client.get('/api/services', headers=HEADERS).json()['services'][0]
    assert {repo['id'] for repo in asset['repositories']} == {str(first), str(second)}
    assert len(asset['deployments']) == 2 and asset['revision'] == 1
    assert asset['deployments'][0]['server']['hostname'] == 'app.internal'
    context = client.get(f'/api/services/{service_id}/context', headers=HEADERS)
    assert context.status_code == 200 and context.json()['service'] == asset
    assert context.json()['schema_version'] == 1 and 'not live monitoring' in context.json()['provenance']
    assert (
        'private-api-token' not in context.text
        and 'ciphertext' not in context.text
        and 'credential_id' not in context.text
    )
    assert asset['deployments'][0]['deployed_version'] == 'v1.2' and asset['repositories'][0]['current_commit'] is None
    # A successful later source pull updates context without claiming that version is deployed.
    with store.connect() as conn:
        conn.execute(
            "UPDATE ka.repositories SET current_commit=%s,checkout_path='source/checkout' WHERE id=%s",
            ('a' * 40, first),
        )
    updated = client.get(f'/api/services/{service_id}/context', headers=HEADERS).json()['service']
    assert next(repo for repo in updated['repositories'] if repo['id'] == str(first))['current_commit'] == 'a' * 40
    assert updated['deployments'][0]['deployed_version'] == 'v1.2'
    # Repositories are shared assets, not exclusively owned by one service.
    other = client.post('/api/services', headers=HEADERS, json={'name': 'Reports', 'repository_ids': [str(second)]})
    assert other.status_code == 201
    with store.connect() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM ka.service_relations WHERE kind='built_from' AND to_code_project_id=%s",
                (second,),
            ).fetchone()['n']
            == 2
        )
        actions = conn.execute(
            'SELECT action FROM ka.audit_events WHERE resource_id=%s', (UUID(service_id),)
        ).fetchall()
        assert actions == [{'action': 'service.created'}]


def test_edit_replaces_links_preserves_deployment_ids_and_rejects_stale_revision(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    repo = store.save('shop', RepositoryInput(remote_url='example/old'), 'admin', '')
    server_id = client.post('/api/servers', headers=HEADERS, json={'name': 'Local', 'hostname': '127.0.0.1'}).json()[
        'id'
    ]
    value = {
        'name': 'API',
        'repository_ids': [str(repo)],
        'deployments': [{'server_id': server_id, 'environment': 'local'}],
    }
    service_id = client.post('/api/services', headers=HEADERS, json=value).json()['id']
    original = client.get('/api/services', headers=HEADERS).json()['services'][0]
    deployment_id = original['deployments'][0]['id']
    value.update(revision=1, repository_ids=[], owner='Updated owner')
    value['deployments'][0].update(id=deployment_id, deployed_version='release-2')
    response = client.put(f'/api/services/{service_id}', headers=HEADERS, json=value)
    assert response.status_code == 200 and response.json()['revision'] == 2
    current = client.get('/api/services', headers=HEADERS).json()['services'][0]
    assert current['repositories'] == [] and current['owner'] == 'Updated owner'
    assert current['deployments'][0]['id'] == deployment_id
    assert client.put(f'/api/services/{service_id}', headers=HEADERS, json=value).status_code == 409
    assert client.get('/api/services', headers=HEADERS).json()['services'][0] == current
    value.update(revision=2, deployments=[])
    assert client.put(f'/api/services/{service_id}', headers=HEADERS, json=value).status_code == 200
    assert client.get('/api/services', headers=HEADERS).json()['services'][0]['deployments'] == []


def test_duplicate_name_rolls_back_all_changes(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert client.post('/api/services', headers=HEADERS, json={'name': 'Payments'}).status_code == 201
    response = client.post('/api/services', headers=HEADERS, json={'name': ' payments '})
    assert response.status_code == 409
    assert len(client.get('/api/services', headers=HEADERS).json()['services']) == 1


def test_cross_project_references_and_ids_fail_without_mutation(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    repo = store.save('another-app', RepositoryInput(remote_url='other/secret'), 'admin', '')
    with store.connect() as conn:
        project_id = store.project(conn, 'another-app')
        foreign_server = conn.execute(
            "INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'Foreign','foreign.internal',false) RETURNING id",
            (project_id,),
        ).fetchone()['id']
        foreign_service = conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'Foreign service') RETURNING id", (project_id,)
        ).fetchone()['id']
    assert (
        client.post('/api/services', headers=HEADERS, json={'name': 'Test', 'repository_ids': [str(repo)]}).status_code
        == 400
    )
    assert (
        client.post(
            '/api/services',
            headers=HEADERS,
            json={'name': 'Test', 'deployments': [{'server_id': str(foreign_server), 'environment': 'production'}]},
        ).status_code
        == 400
    )
    assert client.get(f'/api/services/{foreign_service}/context', headers=HEADERS).status_code == 404
    assert (
        client.put(
            f'/api/services/{foreign_service}', headers=HEADERS, json={'name': 'Hijacked', 'revision': 1}
        ).status_code
        == 404
    )
    assert client.get('/api/services', headers=HEADERS).json()['services'] == []
    assert client.get('/api/servers', headers=HEADERS).json()['servers'] == []
    assert client.get('/api/unknown/services', headers=HEADERS).status_code == 404
    # The database independently rejects a cross-project join even if the API is bypassed.
    with store.connect() as conn:
        local_project = store.project(conn, 'shop')
        local_service = conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'Local') RETURNING id", (local_project,)
        ).fetchone()['id']
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO ka.inventory_links(project_id,from_service_id,to_code_project_id,kind) VALUES (%s,%s,%s,'built_from')",
                (local_project, local_service, repo),
            )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with store.connect() as conn:
            conn.execute(
                """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id)
                VALUES (%s,%s,%s,'prod',%s)""",
                (local_project, local_service, foreign_server, environment_id(conn, local_project, 'prod')),
            )


@pytest.mark.parametrize(
    'method,path',
    [
        ('GET', '/api/services'),
        ('POST', '/api/services'),
        ('PUT', f'/api/services/{uuid4()}'),
        ('GET', f'/api/services/{uuid4()}/context'),
        ('DELETE', f'/api/services/{uuid4()}?revision=1&confirmed=true'),
        ('GET', '/api/servers'),
        ('POST', '/api/servers'),
    ],
)
def test_asset_routes_require_admin_before_database(tmp_path, method, path):
    assert _client(tmp_path).request(method, path, json={'name': 'Test'}).status_code == 403


@pytest.mark.parametrize(
    'value',
    [
        {'name': '   '},
        {'name': 'Test', 'repository_ids': [str(UUID(int=1))] * 2},
        {'name': 'Test', 'deployments': [{'server_id': str(UUID(int=1)), 'environment': ' '}]},
        {'name': 'Test', 'deployments': [{'server_id': str(UUID(int=1)), 'environment': 'prod', 'port': 0}]},
        {'name': 'Test', 'deployments': [{'server_id': str(UUID(int=1)), 'environment': 'prod', 'port': 65536}]},
        {'name': 'Test', 'token': 'not-a-service-setting'},
    ],
)
def test_invalid_service_input_is_rejected(tmp_path, value):
    assert _client(tmp_path).post('/api/services', headers=HEADERS, json=value).status_code == 400


def test_server_inventory_does_not_grant_access_or_accept_connection_secrets(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    for extra in [{'enabled': True}, {'allow_sudo': True}, {'credential_id': str(uuid4())}]:
        assert (
            client.post(
                '/api/servers', headers=HEADERS, json={'name': 'Test', 'hostname': 'localhost', **extra}
            ).status_code
            == 400
        )
    for hostname in ['https://example.test', 'user:pass@example.test', 'host:22', 'host/path', 'host with spaces']:
        assert (
            client.post('/api/servers', headers=HEADERS, json={'name': 'Test', 'hostname': hostname}).status_code == 400
        )
    for name, hostname in [('ipv4', '192.168.1.10'), ('ipv6', '2001:db8::1'), ('dns', 'Example.Internal.')]:
        assert (
            client.post('/api/servers', headers=HEADERS, json={'name': name, 'hostname': hostname}).status_code == 201
        )
    assert (
        client.post('/api/services', headers={'X-Forwarded-Groups': 'docs-admins'}, json={'name': 'Test'}).status_code
        == 403
    )
    assert (
        client.post(
            '/api/servers',
            headers={'X-Forwarded-Groups': 'docs-admins'},
            json={'name': 'Test', 'hostname': 'localhost'},
        ).status_code
        == 403
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        with store.connect() as conn:
            conn.execute('UPDATE ka.servers SET enabled=true')


def test_foreign_deployment_id_and_archived_repository_cannot_be_linked(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    repository = store.save('shop', RepositoryInput(remote_url='example/archive'), 'admin', '')
    with store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (repository,))
    assert (
        client.post(
            '/api/services', headers=HEADERS, json={'name': 'Test', 'repository_ids': [str(repository)]}
        ).status_code
        == 400
    )
    service = client.post('/api/services', headers=HEADERS, json={'name': 'Test'}).json()
    server = client.post('/api/servers', headers=HEADERS, json={'name': 'Test', 'hostname': 'localhost'}).json()
    response = client.put(
        f'/api/services/{service["id"]}',
        headers=HEADERS,
        json={
            'name': 'Test',
            'revision': 1,
            'deployments': [{'id': str(uuid4()), 'server_id': server['id'], 'environment': 'test'}],
        },
    )
    assert response.status_code == 400
    assert client.get('/api/services', headers=HEADERS).json()['services'][0]['revision'] == 1


def test_delete_service_requires_confirmation_revision_identity_and_scope(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    service = client.post('/api/services', headers=HEADERS, json={'name': 'Disposable service'}).json()
    endpoint = f'/api/services/{service["id"]}'
    params = {'revision': 1, 'confirmed': True}
    assert client.delete(endpoint, params=params).status_code == 403
    assert client.delete(endpoint, headers={'X-Forwarded-Groups': 'docs-admins'}, params=params).status_code == 403
    assert client.delete(endpoint, headers=HEADERS, params={'revision': 1}).status_code == 400
    assert client.delete(endpoint, headers=HEADERS, params={'confirmed': True}).status_code == 400
    assert client.delete(endpoint, headers=HEADERS, params={**params, 'revision': 0}).status_code == 400
    with store.connect() as conn:
        foreign_project = store.project(conn, 'another-app')
        foreign_id = conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'Foreign service') RETURNING id", (foreign_project,)
        ).fetchone()['id']
    assert client.delete(f'/api/services/{foreign_id}', headers=HEADERS, params=params).status_code == 404
    assert client.delete(f'/api/services/{uuid4()}', headers=HEADERS, params=params).status_code == 404
    assert client.put(endpoint, headers=HEADERS, json={'name': 'Updated service', 'revision': 1}).status_code == 200
    stale = client.delete(endpoint, headers=HEADERS, params=params)
    assert stale.status_code == 409
    assert 'changed since you opened the confirmation' in stale.text
    assert client.get(f'{endpoint}/context', headers=HEADERS).json()['service']['name'] == 'Updated service'
    with store.connect() as conn:
        assert (
            conn.execute('SELECT name FROM ka.services WHERE id=%s', (foreign_id,)).fetchone()['name']
            == 'Foreign service'
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='service.deleted'").fetchone()['n']
            == 0
        )


def test_delete_service_removes_links_but_preserves_shared_assets_and_audits(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    repository_id = store.save('shop', RepositoryInput(remote_url='example/shared', token='repo-secret'), 'admin', '')
    server = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'Shared host', 'hostname': 'shared.internal'}
    ).json()
    value = {
        'name': 'Disposable service',
        'repository_ids': [str(repository_id)],
        'deployments': [{'server_id': server['id'], 'environment': 'test'}],
    }
    service_id = client.post('/api/services', headers=HEADERS, json=value).json()['id']
    other_id = client.post('/api/services', headers=HEADERS, json={**value, 'name': 'Keep service'}).json()['id']
    other = client.get(f'/api/services/{other_id}/context', headers=HEADERS).json()['service']
    with store.connect() as conn:
        give(conn, 'search', [('service', service_id), ('service', other_id)])
        repository = conn.execute('SELECT * FROM ka.repositories WHERE id=%s', (repository_id,)).fetchone()
    endpoint = f'/api/services/{service_id}'
    params = {'revision': 1, 'confirmed': True}
    response = client.delete(endpoint, headers=HEADERS, params=params)
    assert response.status_code == 200, response.text
    assert response.json() == {'id': service_id}
    assert client.get(f'{endpoint}/context', headers=HEADERS).status_code == 404
    assert client.delete(endpoint, headers=HEADERS, params=params).status_code == 404
    assert client.get('/api/services', headers=HEADERS).json()['services'] == [other]
    assert client.get('/api/servers', headers=HEADERS).json()['servers'] == [server]
    with store.connect() as conn:
        assert conn.execute('SELECT * FROM ka.repositories WHERE id=%s', (repository_id,)).fetchone() == repository
        assert (
            conn.execute(
                "SELECT 1 FROM ka.service_relations WHERE kind='built_from' AND from_service_id=%s", (UUID(service_id),)
            ).fetchone()
            is None
        )
        assert (
            conn.execute('SELECT 1 FROM ka.service_deployments WHERE service_id=%s', (UUID(service_id),)).fetchone()
            is None
        )
        # the agent's reference to the deleted service stays (skipped at run start, missing in the editor)
        assert [
            item['id']
            for item in conn.execute("SELECT access FROM ka.agents WHERE id='search'").fetchone()['access']['resources']
        ] == [service_id, other_id]
        audit = conn.execute(
            "SELECT resource_type,actor_id,details FROM ka.audit_events WHERE action='service.deleted' AND resource_id=%s",
            (UUID(service_id),),
        ).fetchall()
        assert len(audit) == 1 and audit[0]['resource_type'] == 'service' and audit[0]['actor_id'] is not None
        assert audit[0]['details'] == {'name': 'Disposable service', 'revision': 1}
