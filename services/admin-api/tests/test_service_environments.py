from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from test_api import _client
from test_repositories import HEADERS


def test_membership_round_trip_from_either_form_and_stale_edit_protection(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    dev = client.post('/api/environments', headers=HEADERS, json={'name': 'Dev'}).json()
    prod = client.post('/api/environments', headers=HEADERS, json={'name': 'Production'}).json()
    service = client.post(
        '/api/services', headers=HEADERS, json={'name': 'API', 'environment_ids': [dev['id'], prod['id']]}
    ).json()
    url = f'/api/services/{service["id"]}'
    asset = client.get(url + '/context', headers=HEADERS).json()['service']
    assert {e['id'] for e in asset['environments']} == {dev['id'], prod['id']}
    current_dev = client.get(f'/api/environments/{dev["id"]}', headers=HEADERS).json()
    assert current_dev['services'] == [{'id': service['id'], 'name': 'API'}]
    assert current_dev['revision'] == 2
    stale = client.put(f'/api/environments/{dev["id"]}', headers=HEADERS, json={'name': 'Dev', 'revision': 1})
    assert stale.status_code == 409
    other = client.post(
        '/api/services', headers=HEADERS, json={'name': 'Worker', 'environment_ids': [dev['id']]}
    ).json()
    assert len(client.get(f'/api/environments/{dev["id"]}', headers=HEADERS).json()['services']) == 2
    # Omitting the selection preserves it; explicitly clearing it removes only this service's links.
    assert client.put(url, headers=HEADERS, json={'name': 'Renamed', 'revision': 1}).status_code == 200
    assert len(client.get(url + '/context', headers=HEADERS).json()['service']['environments']) == 2
    assert (
        client.put(url, headers=HEADERS, json={'name': 'Renamed', 'revision': 2, 'environment_ids': []}).status_code
        == 200
    )
    current_dev = client.get(f'/api/environments/{dev["id"]}', headers=HEADERS).json()
    assert current_dev['services'] == [{'id': other['id'], 'name': 'Worker'}]
    assert (
        client.put(
            f'/api/environments/{dev["id"]}',
            headers=HEADERS,
            json={'name': 'Dev', 'revision': current_dev['revision'], 'service_ids': [service['id'], other['id']]},
        ).status_code
        == 200
    )
    assert (
        client.put(url, headers=HEADERS, json={'name': 'Stale', 'revision': 3, 'environment_ids': []}).status_code
        == 409
    )


def test_deployment_uses_environment_id_and_tracks_renames_and_deletion(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    env = client.post('/api/environments', headers=HEADERS, json={'name': 'Dev'}).json()
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'Host', 'hostname': 'host.test'}).json()
    response = client.post(
        '/api/services',
        headers=HEADERS,
        json={
            'name': 'API',
            'environment_ids': [env['id']],
            'deployments': [{'server_id': host['id'], 'environment_id': env['id'], 'environment': 'untrusted-name'}],
        },
    )
    assert response.status_code == 201, response.text
    service = response.json()
    asset = client.get(f'/api/services/{service["id"]}/context', headers=HEADERS).json()['service']
    deployment = asset['deployments'][0]
    assert deployment['environment'] == 'Dev' and deployment['environment_id'] == env['id']
    current = client.get(f'/api/environments/{env["id"]}', headers=HEADERS).json()
    assert current['services'][0]['id'] == service['id'] and current['servers'][0]['id'] == host['id']
    response = client.put(
        f'/api/environments/{env["id"]}',
        headers=HEADERS,
        json={
            'name': 'Development',
            'revision': current['revision'],
            'service_ids': [service['id']],
            'server_ids': [host['id']],
        },
    )
    assert response.status_code == 200, response.text
    renamed = response.json()
    asset = client.get(f'/api/services/{service["id"]}/context', headers=HEADERS).json()['service']
    assert asset['deployments'][0]['environment'] == 'Development'
    assert asset['deployments'][0]['id'] == deployment['id']
    resources = client.get('/api/documentation/resources', headers=HEADERS).json()
    assert resources['deployments'][0]['environment'] == 'Development'
    assert (
        client.put(
            f'/api/environments/{env["id"]}',
            headers=HEADERS,
            json={'name': 'Development', 'revision': renamed['revision']},
        ).status_code
        == 400
    )
    assert (
        client.delete(
            f'/api/environments/{env["id"]}?confirmed=true&revision={renamed["revision"]}', headers=HEADERS
        ).status_code
        == 200
    )
    asset = client.get(f'/api/services/{service["id"]}/context', headers=HEADERS).json()['service']
    assert asset['environments'] == []
    assert asset['deployments'][0]['environment_id'] is None
    assert asset['deployments'][0]['environment'] == 'Development'
    assert asset['deployments'][0]['id'] == deployment['id']


def test_environment_selection_rejects_missing_foreign_and_duplicate_ids(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        foreign_project = store.project(conn, 'foreign')
        foreign_id = conn.execute(
            "INSERT INTO ka.environments(project_id,name) VALUES (%s,'Secret') RETURNING id", (foreign_project,)
        ).fetchone()['id']
    for ids in ([str(foreign_id)], [str(uuid4())], [str(foreign_id), str(foreign_id)]):
        response = client.post('/api/services', headers=HEADERS, json={'name': 'Rejected', 'environment_ids': ids})
        assert response.status_code == 400 and 'Secret' not in response.text
    assert client.get('/api/services', headers=HEADERS).json()['services'] == []
    env = client.post('/api/environments', headers=HEADERS, json={'name': 'Local'}).json()
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'Host', 'hostname': 'host.test'}).json()
    assert (
        client.post(
            '/api/services',
            headers=HEADERS,
            json={
                'name': 'API',
                'environment_ids': [],
                'deployments': [{'server_id': host['id'], 'environment_id': env['id']}],
            },
        ).status_code
        == 400
    )
    service = client.post('/api/services', headers=HEADERS, json={'name': 'Valid'}).json()
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment_id,environment)
                VALUES (%s,%s,%s,%s,'Secret')""",
                (UUID(env['project_id']), UUID(service['id']), UUID(host['id']), foreign_id),
            )


def test_migration_maps_exact_labels_and_retains_unmatched_deployments(store):
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    with store.connect() as conn:
        conn.execute((migrations / '0109_tags_not_flags.down.sql').read_text(), prepare=False)
        conn.execute((migrations / '0107_inventory_document.down.sql').read_text(), prepare=False)  # before 0095
        conn.execute(
            (migrations / '0095_inventory_links.down.sql').read_text(), prepare=False
        )  # 0050 reads the old link tables
        conn.execute((migrations / '0050_service_environments.down.sql').read_text(), prepare=False)
        project = store.project(conn, 'legacy')
        service = conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'API') RETURNING id", (project,)
        ).fetchone()['id']
        host = conn.execute(
            "INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'Host','host.test',false) RETURNING id",
            (project,),
        ).fetchone()['id']
        env = conn.execute(
            "INSERT INTO ka.environments(project_id,name) VALUES (%s,'Dev') RETURNING id", (project,)
        ).fetchone()['id']
        for label in ('Dev', 'Unknown'):
            conn.execute(
                'INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment) VALUES (%s,%s,%s,%s)',
                (project, service, host, label),
            )
        conn.execute((migrations / '0050_service_environments.up.sql').read_text(), prepare=False)
        rows = conn.execute(
            'SELECT id,environment,environment_id FROM ka.service_deployments ORDER BY environment'
        ).fetchall()
        assert rows[0]['environment_id'] == env and rows[1]['environment_id'] is None
        assert conn.execute('SELECT count(*) AS n FROM ka.environment_services').fetchone()['n'] == 0
        conn.execute((migrations / '0050_service_environments.down.sql').read_text(), prepare=False)
        assert {row['id'] for row in conn.execute('SELECT id FROM ka.service_deployments')} == {
            row['id'] for row in rows
        }
