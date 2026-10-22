"""Service dependencies are depends_on service relations, written through the inventory API (disposable PostgreSQL)."""

from uuid import UUID, uuid4

import psycopg
import pytest

from test_api import _client
from test_repositories import HEADERS


def depends_on(client, source, target, description=''):
    return client.post(
        '/api/relations',
        headers=HEADERS,
        json={
            'from_service_id': str(source),
            'kind': 'depends_on',
            'to_service_id': str(target),
            'description': description,
        },
    )


def pairs(store) -> set[tuple[UUID, UUID]]:
    with store.connect() as conn:
        return {
            (r['from_service_id'], r['to_service_id'])
            for r in conn.execute(
                "SELECT from_service_id,to_service_id FROM ka.service_relations WHERE kind='depends_on'"
            )
        }


def test_dependency_direction_metadata_and_service_lifecycle(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    services = {}
    for name in ('Orders API', 'PostgreSQL', 'Worker'):
        response = client.post('/api/services', headers=HEADERS, json={'name': name})
        assert response.status_code == 201, response.text
        services[name] = UUID(response.json()['id'])
    api, postgres, worker = (services[name] for name in ('Orders API', 'PostgreSQL', 'Worker'))
    for source, target in ((api, postgres), (api, worker), (worker, postgres)):
        assert depends_on(client, source, target).status_code == 201
    context = client.get(f'/api/services/{api}/context', headers=HEADERS).json()['service']
    assert [d['name'] for d in context['dependencies']] == ['PostgreSQL', 'Worker'] and context['dependents'] == []
    context = client.get(f'/api/services/{postgres}/context', headers=HEADERS).json()['service']
    assert context['dependencies'] == [] and [d['name'] for d in context['dependents']] == ['Orders API', 'Worker']
    # An explicit reverse relationship is independent; real inventories can contain cycles.
    reverse = depends_on(client, postgres, api).json()
    updated = client.put(
        f'/api/relations/{reverse["id"]}',
        headers=HEADERS,
        json={'revision': reverse['revision'], 'description': 'Uses the API for callbacks'},
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()['description'], updated.json()['revision']) == ('Uses the API for callbacks', 2)
    assert updated.json()['created_at'] == reverse['created_at']

    saved = client.put(f'/api/services/{api}', headers=HEADERS, json={'name': 'Orders', 'revision': 1})
    assert saved.status_code == 200, saved.text
    assert len(pairs(store)) == 4
    deleted = client.delete(f'/api/services/{postgres}', headers=HEADERS, params={'revision': 1})
    assert deleted.status_code == 200, deleted.text
    assert pairs(store) == {(api, worker)}
    with store.connect() as conn:
        assert {r['id'] for r in conn.execute('SELECT id FROM ka.services')} == {api, worker}
    # Removing one dependency alone preserves both services.
    [relation] = client.get(f'/api/services/{api}/relations', headers=HEADERS).json()['relations']
    removed = client.delete(f'/api/relations/{relation["id"]}', headers=HEADERS, params={'revision': 1})
    assert removed.status_code == 200, removed.text
    assert pairs(store) == set()
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.services').fetchone()['n'] == 2


def test_dependencies_reject_services_outside_the_project_and_duplicates(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    first, second = (
        client.post('/api/services', headers=HEADERS, json={'name': name}).json()['id'] for name in ('API', 'Database')
    )
    with store.connect() as conn:
        other_project = store.project(conn, 'other')
        foreign = conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'Other API') RETURNING id", (other_project,)
        ).fetchone()['id']
    assert depends_on(client, first, second).status_code == 201
    duplicate = depends_on(client, first, second)
    assert (duplicate.status_code, duplicate.json()['error']['message']) == (409, 'This relation already exists')
    for source, target in ((first, foreign), (foreign, first), (first, uuid4())):
        assert depends_on(client, source, target).status_code == 400
    assert depends_on(client, first, first).status_code == 400
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        assert conn.execute('SELECT count(*) AS n FROM ka.service_relations').fetchone()['n'] == 1
        # The database independently rejects a cross-project relation even if the API is bypassed.
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                """INSERT INTO ka.service_relations(project_id,from_service_id,to_service_id,kind)
                VALUES (%s,%s,%s,'depends_on')""",
                (project, first, foreign),
            )
