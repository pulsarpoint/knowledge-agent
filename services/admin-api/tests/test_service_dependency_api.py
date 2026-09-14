"""Both service forms edit one directed relationship, with shared conflict checks."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest

from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def inventory(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    services = {}
    for name in ('Payments', 'PostgreSQL', 'Reporting'):
        response = client.post('/api/services', headers=HEADERS, json={'name': name})
        assert response.status_code == 201, response.text
        services[name] = response.json()['id']
    return client, services


def test_both_directions_create_edit_remove_and_preserve_other_relationships(store, inventory):
    client, ids = inventory
    payments, postgres, reporting = (ids[name] for name in ('Payments', 'PostgreSQL', 'Reporting'))
    response = client.put(f'/api/services/{reporting}', headers=HEADERS, json={
        'name': 'Reporting', 'revision': 1, 'dependencies': [{'service_id': postgres, 'description': 'Reads reports'}]})
    assert response.status_code == 200, response.text
    response = client.put(f'/api/services/{payments}', headers=HEADERS, json={
        'name': 'Payments', 'revision': 1,
        'dependencies': [{'service_id': postgres, 'description': 'Stores payments'}],
        'dependents': [{'service_id': reporting, 'description': 'Uses payment APIs'}]})
    assert response.status_code == 200, response.text
    payment_asset = client.get(f'/api/services/{payments}/context', headers=HEADERS).json()['service']
    postgres_asset = client.get(f'/api/services/{postgres}/context', headers=HEADERS).json()['service']
    assert payment_asset['dependencies'] == [{'service_id': postgres, 'name': 'PostgreSQL', 'description': 'Stores payments'}]
    assert payment_asset['dependents'] == [{'service_id': reporting, 'name': 'Reporting', 'description': 'Uses payment APIs'}]
    assert postgres_asset['dependencies'] == []
    assert postgres_asset['dependents'] == [
        {'service_id': payments, 'name': 'Payments', 'description': 'Stores payments'},
        {'service_id': reporting, 'name': 'Reporting', 'description': 'Reads reports'}]

    # Editing the reverse side updates the same row without replacing its identity/timestamps.
    with store.connect() as conn:
        original = conn.execute('SELECT created_at FROM ka.service_dependencies WHERE service_id=%s AND depends_on_service_id=%s', (UUID(payments),UUID(postgres))).fetchone()
    response = client.put(f'/api/services/{postgres}', headers=HEADERS, json={
        'name': 'PostgreSQL', 'revision': postgres_asset['revision'],
        'dependents': [{'service_id': payments, 'description': 'Stores transactions'}, {'service_id': reporting, 'description': 'Reads reports'}]})
    assert response.status_code == 200, response.text
    current = client.get(f'/api/services/{payments}/context', headers=HEADERS).json()['service']
    assert current['revision'] > payment_asset['revision']
    assert current['dependencies'][0]['description'] == 'Stores transactions'
    assert current['dependents'] == payment_asset['dependents']
    with store.connect() as conn:
        assert conn.execute('SELECT created_at FROM ka.service_dependencies WHERE service_id=%s AND depends_on_service_id=%s', (UUID(payments),UUID(postgres))).fetchone() == original
    # The opposite form cannot erase a change using an old revision.
    assert client.put(f'/api/services/{payments}', headers=HEADERS, json={
        'name': 'Payments', 'revision': payment_asset['revision'], 'dependencies': []}).status_code == 409
    # Omitted directions are preserved; explicitly empty directions are removed.
    assert client.put(f'/api/services/{payments}', headers=HEADERS, json={
        'name': 'Payments', 'revision': current['revision'], 'dependencies': []}).status_code == 200
    current = client.get(f'/api/services/{payments}/context', headers=HEADERS).json()['service']
    assert current['dependencies'] == [] and current['dependents'] == payment_asset['dependents']
    postgres_asset = client.get(f'/api/services/{postgres}/context', headers=HEADERS).json()['service']
    assert postgres_asset['dependents'] == [{'service_id': reporting, 'name': 'Reporting', 'description': 'Reads reports'}]
    assert client.put(f'/api/services/{payments}', headers=HEADERS, json={
        'name': 'Payments', 'revision': current['revision'], 'dependents': []}).status_code == 200
    with store.connect() as conn:
        links = conn.execute('SELECT service_id,depends_on_service_id FROM ka.service_dependencies').fetchall()
        assert links == [{'service_id': UUID(reporting), 'depends_on_service_id': UUID(postgres)}]


def test_create_with_both_directions_and_reject_invalid_requests_atomically(store, inventory):
    client, ids = inventory
    response = client.post('/api/services', headers=HEADERS, json={'name': 'Gateway',
        'dependencies': [{'service_id': ids['Payments']}], 'dependents': [{'service_id': ids['Reporting']} ]})
    assert response.status_code == 201, response.text
    gateway = response.json()['id']
    with store.connect() as conn:
        project = store.project(conn, 'other')
        foreign = str(conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'Private') RETURNING id", (project,)).fetchone()['id'])
    for direction in ('dependencies', 'dependents'):
        for links in ([{'service_id': gateway}], [{'service_id': foreign}], [{'service_id': str(uuid4())}],
                      [{'service_id': ids['Payments']}] * 2, [{'service_id': ids['Payments'], 'description': 'x' * 4001}],
                      [{'service_id': ids['Payments'], 'description': '\x00'}], None):
            invalid = client.put(f'/api/services/{gateway}', headers=HEADERS, json={
                'name': 'Should not save', 'revision': 1, direction: links})
            assert invalid.status_code == 400, invalid.text
    current = client.get(f'/api/services/{gateway}/context', headers=HEADERS).json()['service']
    assert current['name'] == 'Gateway' and current['revision'] == 1
    assert current['dependencies'][0]['service_id'] == ids['Payments']
    assert current['dependents'][0]['service_id'] == ids['Reporting']
    assert client.put(f'/api/services/{gateway}', json={'name': 'Not authorized', 'revision': 1, 'dependencies': []}).status_code == 403


def test_concurrent_opposite_edits_conflict_and_deletion_invalidates_neighbor(inventory):
    client, ids = inventory
    payments, postgres = ids['Payments'], ids['PostgreSQL']
    updates = [(payments, {'name': 'Payments', 'revision': 1, 'dependencies': [{'service_id': postgres, 'description': 'From Payments'}]}),
               (postgres, {'name': 'PostgreSQL', 'revision': 1, 'dependents': [{'service_id': payments, 'description': 'From PostgreSQL'}]})]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda item: client.put(f'/api/services/{item[0]}', headers=HEADERS, json=item[1]), updates))
    assert sorted(response.status_code for response in responses) == [200, 409]
    payment_asset = client.get(f'/api/services/{payments}/context', headers=HEADERS).json()['service']
    postgres_asset = client.get(f'/api/services/{postgres}/context', headers=HEADERS).json()['service']
    assert payment_asset['dependencies'][0]['description'] == postgres_asset['dependents'][0]['description']
    deleted = client.delete(f'/api/services/{payments}', headers=HEADERS, params={'revision': payment_asset['revision'], 'confirmed': True})
    assert deleted.status_code == 200, deleted.text
    current = client.get(f'/api/services/{postgres}/context', headers=HEADERS).json()['service']
    assert current['dependents'] == [] and current['revision'] > postgres_asset['revision']
    assert client.put(f'/api/services/{postgres}', headers=HEADERS, json={
        'name': 'PostgreSQL', 'revision': postgres_asset['revision'], 'dependents': []}).status_code == 409
