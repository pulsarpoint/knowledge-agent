"""Environment CRUD, project isolation and migration safety against PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def inventory(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'App host', 'hostname': 'app.internal'}).json()
    service = client.post('/api/services', headers=HEADERS, json={'name': 'API'}).json()
    return client, service, host


def deploy(client, service, host, environment) -> dict:
    response = client.post(
        '/api/deployments',
        headers=HEADERS,
        json={'service_id': service['id'], 'server_id': host['id'], 'environment_id': environment['id']},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_round_trip_lists_what_is_deployed_edit_and_audit(store, inventory):
    client, service, host = inventory
    response = client.post(
        '/api/environments', headers=HEADERS, json={'name': ' Development ', 'description': 'For daily testing'}
    )
    assert response.status_code == 201, response.text
    environment = response.json()
    assert environment['name'] == 'Development' and environment['revision'] == 1
    assert 'ssh' not in response.text and 'credential' not in response.text
    url = f'/api/environments/{environment["id"]}'
    assert client.get(url, headers=HEADERS).json()['services'] == []  # nothing deployed there yet
    deploy(client, service, host, environment)
    environment = client.get(url, headers=HEADERS).json()
    assert environment['services'] == [{'id': service['id'], 'name': 'API'}]
    assert environment['servers'] == [{'id': host['id'], 'name': 'App host', 'hostname': 'app.internal'}]
    second = client.post('/api/environments', headers=HEADERS, json={'name': 'Staging'}).json()
    listing = client.get('/api/environments', headers=HEADERS).json()['environments']
    assert [row['name'] for row in listing] == ['Development', 'Staging']
    assert listing[0] == environment and listing[1]['services'] == listing[1]['servers'] == []
    edited = client.put(url, headers=HEADERS, json={'name': 'Dev', 'description': 'Updated', 'revision': 1})
    assert edited.status_code == 200, edited.text
    assert edited.json()['revision'] == 2
    assert client.get(url, headers=HEADERS).json()['services'] == environment['services']
    assert client.get(f'/api/environments/{second["id"]}', headers=HEADERS).json()['name'] == 'Staging'
    assert client.put(url, headers=HEADERS, json={'name': 'Again', 'revision': 1}).status_code == 409
    assert client.get(url, headers=HEADERS).json()['name'] == 'Dev'
    with store.connect() as conn:
        events = conn.execute(
            'SELECT action,details FROM ka.audit_events WHERE resource_id=%s ORDER BY created_at',
            (UUID(environment['id']),),
        ).fetchall()
        assert [row['action'] for row in events] == ['environment.created', 'membership.added', 'environment.updated']
        assert events[-1]['details']['name'] == 'Dev'


def test_delete_requires_revision_is_refused_while_deployed_and_preserves_inventory(store, inventory):
    client, service, host = inventory
    qa = client.post('/api/environments', headers=HEADERS, json={'name': 'qa'}).json()
    deploy(client, service, host, qa)
    environment = client.post('/api/environments', headers=HEADERS, json={'name': 'Dev'}).json()
    url = f'/api/environments/{environment["id"]}'
    assert client.delete(url, headers=HEADERS).status_code == 400
    assert client.delete(url + '?revision=2', headers=HEADERS).status_code == 409
    assert client.get(url, headers=HEADERS).status_code == 200
    assert client.delete(url + '?revision=1', headers=HEADERS).status_code == 200
    assert client.get(url, headers=HEADERS).status_code == 404
    assert [e['name'] for e in client.get('/api/environments', headers=HEADERS).json()['environments']] == ['qa']
    refused = client.delete(f'/api/environments/{qa["id"]}?revision={qa["revision"]}', headers=HEADERS)
    assert refused.status_code == 409  # its deployment uses it
    with store.connect() as conn:
        for table in ('services', 'servers', 'service_deployments'):
            assert conn.execute(f'SELECT count(*) AS n FROM ka.{table}').fetchone()['n'] == 1
        assert (
            conn.execute(
                'SELECT action FROM ka.audit_events WHERE resource_id=%s ORDER BY created_at DESC LIMIT 1',
                (UUID(environment['id']),),
            ).fetchone()['action']
            == 'environment.deleted'
        )


def test_a_foreign_environment_or_member_is_refused(store, inventory):
    client, service, host = inventory
    with store.connect() as conn:
        foreign = store.project(conn, 'foreign')
        foreign_environment = conn.execute(
            "INSERT INTO ka.environments(project_id,name) VALUES (%s,'Secret') RETURNING id", (foreign,)
        ).fetchone()['id']
    url = f'/api/environments/{foreign_environment}'
    assert client.get(url, headers=HEADERS).status_code == 404
    assert client.get(f'{url}/servers', headers=HEADERS).status_code == 404
    assert client.put(url, headers=HEADERS, json={'name': 'Other', 'revision': 1}).status_code == 404
    assert client.delete(url + '?revision=1', headers=HEADERS).status_code == 404
    assert client.get('/api/environments', headers=HEADERS).json()['environments'] == []
    refused = client.post(
        '/api/deployments',
        headers=HEADERS,
        json={'service_id': service['id'], 'server_id': host['id'], 'environment_id': str(foreign_environment)},
    )
    assert refused.status_code == 400


def test_duplicate_names_and_concurrent_updates_cannot_overwrite_each_other(inventory):
    client, service, host = inventory
    environment = client.post('/api/environments', headers=HEADERS, json={'name': 'Dev'}).json()
    assert client.post('/api/environments', headers=HEADERS, json={'name': 'dev'}).status_code == 409

    def save(description):
        return client.put(
            f'/api/environments/{environment["id"]}',
            headers=HEADERS,
            json={'name': 'Dev', 'revision': 1, 'description': description},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, ['First', 'Second'])) == [200, 409]


@pytest.mark.parametrize(
    'value',
    [
        {'name': ' '},
        {'name': 'x' * 101},
        {'name': 'Bad\x00'},
        {'name': 'Dev', 'description': 'x' * 4001},
        {'name': 'Dev', 'service_ids': []},
        {'name': 'Dev', 'server_ids': []},
    ],
)
def test_invalid_inputs_are_rejected(store, tmp_path, value):
    client = _client(tmp_path, repository_store=store)
    assert client.post('/api/environments', headers=HEADERS, json=value).status_code == 400
    assert client.get('/api/environments', headers=HEADERS).json()['environments'] == []


@pytest.mark.parametrize(
    'method,url',
    [
        ('GET', '/api/environments'),
        ('POST', '/api/environments'),
        ('GET', f'/api/environments/{UUID(int=1)}'),
        ('PUT', f'/api/environments/{UUID(int=1)}'),
        ('DELETE', f'/api/environments/{UUID(int=1)}?revision=1'),
    ],
)
def test_all_routes_require_admin_before_data_access(tmp_path, method, url):
    assert _client(tmp_path).request(method, url, json={'name': 'Dev'}).status_code == 403


def test_migration_reapply_and_populated_rollback_protection(store):
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    down = (migrations / '0045_environments.down.sql').read_text(encoding='utf-8')
    up = (migrations / '0045_environments.up.sql').read_text(encoding='utf-8')
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'Retained')", (project,))
        # 0104's views read documents.phase_metadata, which 0073's rollback drops: roll 0104 back first (0107 before it).
        conn.execute(
            (migrations / '0127_environment_workspace.down.sql').read_text(), prepare=False
        )  # 0127 makes environment_servers, which 0126 down makes again
        conn.execute((migrations / '0126_inventory_drop_old.down.sql').read_text(), prepare=False)
        conn.execute((migrations / '0109_tags_not_flags.down.sql').read_text(), prepare=False)
        conn.execute((migrations / '0107_inventory_document.down.sql').read_text(), prepare=False)
        conn.execute((migrations / '0104_document_model.down.sql').read_text(), prepare=False)
        conn.execute(
            (migrations / '0095_inventory_links.down.sql').read_text(), prepare=False
        )  # 0050 reads the old link tables
        conn.execute((migrations / '0073_remove_documentation_generation.down.sql').read_text(), prepare=False)
        conn.execute((migrations / '0050_service_environments.down.sql').read_text(), prepare=False)
        conn.execute((migrations / '0049_layered_environment_architecture.down.sql').read_text(), prepare=False)
        conn.execute((migrations / '0048_server_infrastructure_documentation.down.sql').read_text(), prepare=False)
        conn.execute((migrations / '0047_documentation_workflows.down.sql').read_text(), prepare=False)
        conn.execute((migrations / '0046_documentation_generation.down.sql').read_text(), prepare=False)
        conn.execute(down, prepare=False)
        conn.execute(up, prepare=False)
        assert conn.execute('SELECT name FROM ka.services').fetchone()['name'] == 'Retained'
        assert conn.execute('SELECT count(*) AS n FROM ka.environments').fetchone()['n'] == 0
        conn.execute("INSERT INTO ka.environments(project_id,name) VALUES (%s,'Dev')", (project,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Environments exist'):
            conn.execute(down, prepare=False)
        conn.rollback()
        assert conn.execute('SELECT name FROM ka.environments').fetchone()['name'] == 'Dev'
