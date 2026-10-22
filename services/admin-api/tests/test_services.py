from uuid import UUID, uuid4

import psycopg
import pytest
from inventory_rows import environment_id

from access_helpers import give
from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS


def post(client, segment: str, **values) -> dict:
    response = client.post(f'/api/{segment}', headers=HEADERS, json=values)
    assert response.status_code == 201, response.text
    return response.json()


def test_service_assets_link_multiple_repositories_and_server_deployments(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    first = store.save('shop', RepositoryInput(remote_url='example/api', token='private-api-token'), 'admin', '')
    second = store.save('shop', RepositoryInput(remote_url='example/shared'), 'admin', '')
    server = post(client, 'servers', name='App host', hostname='app.internal')
    assert not server['ssh_allowed']
    assert not server['access_enabled'] and not server['allow_sudo'] and server['ssh_username'] is None
    production, staging = post(client, 'environments', name='production'), post(client, 'environments', name='staging')
    service_id = post(client, 'services', name='Payments', description='Handles payments', owner='Backend team')['id']
    for repository in (first, second):
        post(client, 'relations', from_service_id=service_id, kind='built_from', to_code_project_id=str(repository))
    post(
        client,
        'deployments',
        service_id=service_id,
        server_id=server['id'],
        environment_id=production['id'],
        runtime='systemd',
        runtime_name='payments.service',
        port=8080,
        log_location='/var/log/payments.log',
        deployed_version='v1.2',
    )
    post(
        client,
        'deployments',
        service_id=service_id,
        server_id=server['id'],
        environment_id=staging['id'],
        runtime_name='payments-staging.service',
    )
    asset = client.get('/api/services', headers=HEADERS).json()['services'][0]
    assert {repo['id'] for repo in asset['repositories']} == {str(first), str(second)}
    assert len(asset['deployments']) == 2 and asset['revision'] == 1
    assert [e['name'] for e in asset['environments']] == ['production', 'staging']
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
    other = post(client, 'services', name='Reports')
    post(client, 'relations', from_service_id=other['id'], kind='built_from', to_code_project_id=str(second))
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
    service = post(client, 'services', name='Test')
    environment = post(client, 'environments', name='production')
    built_from = {'from_service_id': service['id'], 'kind': 'built_from', 'to_code_project_id': str(repo)}
    assert client.post('/api/relations', headers=HEADERS, json=built_from).status_code == 400
    deployment = {'service_id': service['id'], 'server_id': str(foreign_server), 'environment_id': environment['id']}
    assert client.post('/api/deployments', headers=HEADERS, json=deployment).status_code == 400
    assert client.get(f'/api/services/{foreign_service}/context', headers=HEADERS).status_code == 404
    assert (
        client.put(
            f'/api/services/{foreign_service}', headers=HEADERS, json={'name': 'Hijacked', 'revision': 1}
        ).status_code
        == 404
    )
    [saved] = client.get('/api/services', headers=HEADERS).json()['services']
    assert saved['name'] == 'Test' and saved['revision'] == 1 and saved['deployments'] == saved['repositories'] == []
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
                "INSERT INTO ka.service_relations(project_id,from_service_id,to_code_project_id,kind) VALUES (%s,%s,%s,'built_from')",
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
        ('DELETE', f'/api/services/{uuid4()}?revision=1'),
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
        {'name': 'x' * 201},
        {'name': 'Test', 'service_type': 'other'},
        {'name': 'Test', 'repository_ids': []},
        {'name': 'Test', 'deployments': []},
        {'name': 'Test', 'token': 'not-a-service-setting'},
    ],
)
def test_invalid_service_input_is_rejected(store, tmp_path, value):
    client = _client(tmp_path, repository_store=store)
    assert client.post('/api/services', headers=HEADERS, json=value).status_code == 400
    assert client.get('/api/services', headers=HEADERS).json()['services'] == []


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


def test_delete_service_requires_revision_identity_and_scope(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    service = client.post('/api/services', headers=HEADERS, json={'name': 'Disposable service'}).json()
    endpoint = f'/api/services/{service["id"]}'
    params = {'revision': 1}
    assert client.delete(endpoint, params=params).status_code == 403
    assert client.delete(endpoint, headers={'X-Forwarded-Groups': 'docs-admins'}, params=params).status_code == 403
    assert client.delete(endpoint, headers=HEADERS).status_code == 400
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
    assert 'changed since you read it' in stale.text
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


def test_delete_service_removes_its_relations_but_preserves_shared_assets_and_audits(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    repository_id = store.save('shop', RepositoryInput(remote_url='example/shared', token='repo-secret'), 'admin', '')
    server = post(client, 'servers', name='Shared host', hostname='shared.internal')
    service_id = post(client, 'services', name='Disposable service')['id']
    other_id = post(client, 'services', name='Keep service')['id']
    for source in (service_id, other_id):
        post(client, 'relations', from_service_id=source, kind='built_from', to_code_project_id=str(repository_id))
    post(client, 'relations', from_service_id=other_id, kind='depends_on', to_service_id=service_id)
    with store.connect() as conn:
        give(conn, 'search', [('service', service_id), ('service', other_id)])
        repository = conn.execute('SELECT * FROM ka.repositories WHERE id=%s', (repository_id,)).fetchone()
    endpoint = f'/api/services/{service_id}'
    params = {'revision': 1}
    response = client.delete(endpoint, headers=HEADERS, params=params)
    assert response.status_code == 200, response.text
    assert response.json() == {'id': service_id}
    assert client.get(f'{endpoint}/context', headers=HEADERS).status_code == 404
    assert client.delete(endpoint, headers=HEADERS, params=params).status_code == 404
    [other] = client.get('/api/services', headers=HEADERS).json()['services']
    assert other['id'] == other_id and other['dependencies'] == [] and len(other['repositories']) == 1
    assert client.get('/api/servers', headers=HEADERS).json()['servers'] == [server]
    with store.connect() as conn:
        assert conn.execute('SELECT * FROM ka.repositories WHERE id=%s', (repository_id,)).fetchone() == repository
        assert (
            conn.execute(
                'SELECT 1 FROM ka.service_relations WHERE %s IN (from_service_id,to_service_id)', (UUID(service_id),)
            ).fetchone()
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
        assert audit[0]['details']['name'] == 'Disposable service' and audit[0]['details']['revision'] == 1
