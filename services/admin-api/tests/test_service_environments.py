from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from test_api import _client
from test_repositories import HEADERS


def test_a_deployment_names_its_environment_by_id_and_follows_a_rename(store, tmp_path):
    """A service or server is in an environment when one of its deployments is (0125)."""
    client = _client(tmp_path, repository_store=store)
    env = client.post('/api/environments', headers=HEADERS, json={'name': 'Dev'}).json()
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'Host', 'hostname': 'host.test'}).json()
    service = client.post('/api/services', headers=HEADERS, json={'name': 'API'}).json()
    url = f'/api/services/{service["id"]}/context'
    assert client.get(url, headers=HEADERS).json()['service']['environments'] == []
    untrusted = {'service_id': service['id'], 'server_id': host['id'], 'environment': 'untrusted-name'}
    assert client.post('/api/deployments', headers=HEADERS, json=untrusted).status_code == 400
    response = client.post(
        '/api/deployments',
        headers=HEADERS,
        json={'service_id': service['id'], 'server_id': host['id'], 'environment_id': env['id']},
    )
    assert response.status_code == 201, response.text
    deployment = client.get(url, headers=HEADERS).json()['service']['deployments'][0]
    assert deployment['environment'] == 'Dev' and deployment['environment_id'] == env['id']
    current = client.get(f'/api/environments/{env["id"]}', headers=HEADERS).json()
    assert current['services'][0]['id'] == service['id'] and current['servers'][0]['id'] == host['id']
    renamed = client.put(
        f'/api/environments/{env["id"]}', headers=HEADERS, json={'name': 'Development', 'revision': current['revision']}
    )
    assert renamed.status_code == 200, renamed.text
    asset = client.get(url, headers=HEADERS).json()['service']
    assert asset['deployments'][0]['environment'] == 'Development'
    assert asset['deployments'][0]['id'] == deployment['id']
    assert [e['name'] for e in asset['environments']] == ['Development']
    resources = client.get('/api/documentation/resources', headers=HEADERS).json()
    assert resources['deployments'][0]['environment'] == 'Development'
    # An environment with deployments cannot be deleted (0125: every deployment is in an environment).
    refused = client.delete(f'/api/environments/{env["id"]}?revision={renamed.json()["revision"]}', headers=HEADERS)
    assert refused.status_code == 409 and 'deployment' in refused.json()['error']['message']
    assert client.get(url, headers=HEADERS).json()['service']['deployments'][0]['environment_id'] == env['id']


def test_a_deployment_in_another_projects_environment_is_refused(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        foreign_project = store.project(conn, 'foreign')
        foreign_id = conn.execute(
            "INSERT INTO ka.environments(project_id,name) VALUES (%s,'Secret') RETURNING id", (foreign_project,)
        ).fetchone()['id']
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'Host', 'hostname': 'host.test'}).json()
    service = client.post('/api/services', headers=HEADERS, json={'name': 'Valid'}).json()
    for environment_id in (foreign_id, uuid4()):
        response = client.post(
            '/api/deployments',
            headers=HEADERS,
            json={'service_id': service['id'], 'server_id': host['id'], 'environment_id': str(environment_id)},
        )
        assert response.status_code == 400 and 'Secret' not in response.text
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment_id)
                VALUES (%s,%s,%s,%s)""",
                (UUID(service['project_id']), UUID(service['id']), UUID(host['id']), foreign_id),
            )


def test_migration_maps_exact_labels_and_retains_unmatched_deployments(store):
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    with store.connect() as conn:
        conn.execute(
            (migrations / '0127_environment_workspace.down.sql').read_text(), prepare=False
        )  # 0127 makes environment_servers, which 0126 down makes again
        conn.execute((migrations / '0126_inventory_drop_old.down.sql').read_text(), prepare=False)
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
