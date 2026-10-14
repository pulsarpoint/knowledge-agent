"""Inventory → documents attached to services, servers, environments and code projects."""

from uuid import UUID, uuid4

import pytest

from live_documents import write_live_document
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    made = _client(tmp_path, repository_store=store)
    made.store = store  # document() writes live documents through the store
    return made


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def document(client, slug='runbook', title='Runbook'):
    return write_live_document(client.store, slug, title, f'# {title}\n')['id']


def test_attach_list_and_detach(client, store):
    server = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()[
        'id'
    ]
    runbook, backups = document(client), document(client, 'backups', 'Backups')
    entry = {'kind': 'server', 'id': server}
    first = client.post('/api/inventory/documents', headers=HEADERS, json={'entry': entry, 'document_id': runbook})
    assert first.status_code == 201, first.text
    assert (
        client.post(
            '/api/inventory/documents', headers=HEADERS, json={'entry': entry, 'document_id': backups}
        ).status_code
        == 201
    )
    assert message(
        client.post('/api/inventory/documents', headers=HEADERS, json={'entry': entry, 'document_id': runbook})
    ) == (409, 'This document is already attached.')
    listed = client.get(
        '/api/inventory/documents', headers=HEADERS, params={'entry_kind': 'server', 'entry_id': server}
    ).json()['documents']
    assert [(row['title'], row['slug'], row['document_id']) for row in listed] == [
        ('Backups', 'backups', backups),
        ('Runbook', 'runbook', runbook),
    ]
    attachment = first.json()['attachment']['id']
    assert client.delete(f'/api/inventory/documents/{attachment}', headers=HEADERS).status_code == 204
    assert message(client.delete(f'/api/inventory/documents/{attachment}', headers=HEADERS)) == (
        404,
        'Attachment not found.',
    )
    assert (
        client.get(f'/api/documentation/documents/{runbook}', headers=HEADERS).status_code == 200
    )  # the document stays
    with store.connect() as conn:
        actions = [
            row['action']
            for row in conn.execute(
                "SELECT action FROM ka.audit_events WHERE action LIKE 'inventory.document.%' ORDER BY created_at,id"
            )
        ]
    assert actions == ['inventory.document.attached', 'inventory.document.attached', 'inventory.document.detached']


def test_unknown_entries_and_documents_are_refused(client):
    service = client.post('/api/services', headers=HEADERS, json={'name': 'api'}).json()['id']
    assert message(
        client.post(
            '/api/inventory/documents',
            headers=HEADERS,
            json={'entry': {'kind': 'service', 'id': service}, 'document_id': str(uuid4())},
        )
    ) == (404, 'Document not found.')
    assert message(
        client.post(
            '/api/inventory/documents',
            headers=HEADERS,
            json={'entry': {'kind': 'environment', 'id': str(uuid4())}, 'document_id': document(client)},
        )
    ) == (404, 'That environment was not found.')
    assert client.get('/api/inventory/documents', headers=HEADERS, params={'entry_kind': 'service'}).status_code == 400


def count(store) -> int:
    with store.connect() as conn:
        return conn.execute('SELECT count(*) AS n FROM ka.inventory_documents').fetchone()['n']


def test_deleting_the_entry_or_the_document_removes_the_attachment(client, store):
    environment = client.post('/api/environments', headers=HEADERS, json={'name': 'production'}).json()
    runbook = document(client)
    attached = client.post(
        '/api/inventory/documents',
        headers=HEADERS,
        json={'entry': {'kind': 'environment', 'id': environment['id']}, 'document_id': runbook},
    )
    assert attached.status_code == 201, attached.text
    assert count(store) == 1
    assert (
        client.delete(
            f'/api/environments/{environment["id"]}',
            headers=HEADERS,
            params={'revision': environment['revision'], 'confirmed': True},
        ).status_code
        == 200
    )
    assert count(store) == 0
    assert (
        client.get(f'/api/documentation/documents/{runbook}', headers=HEADERS).status_code == 200
    )  # the document stays

    service = client.post('/api/services', headers=HEADERS, json={'name': 'api'}).json()['id']
    notes = document(client, 'notes', 'Notes')
    entry = {'kind': 'service', 'id': service}
    assert (
        client.post(
            '/api/inventory/documents', headers=HEADERS, json={'entry': entry, 'document_id': notes}
        ).status_code
        == 201
    )
    params = {'entry_kind': 'service', 'entry_id': service}
    assert [
        row['document_id']
        for row in client.get('/api/inventory/documents', headers=HEADERS, params=params).json()['documents']
    ] == [notes]
    revision = client.get(f'/api/documentation/documents/{notes}', headers=HEADERS).json()['revision']
    assert (
        client.delete(
            f'/api/documentation/documents/{notes}', headers=HEADERS, params={'revision': revision, 'confirmed': True}
        ).status_code
        == 200
    )
    assert client.get('/api/inventory/documents', headers=HEADERS, params=params).json()['documents'] == []
    assert count(store) == 0
    with store.connect() as conn:  # the entry stays
        assert conn.execute('SELECT count(*) AS n FROM ka.services WHERE id=%s', (service,)).fetchone()['n'] == 1


def test_a_draft_is_never_attached_only_a_live_document(client, store):
    server = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()[
        'id'
    ]
    runbook = document(client)
    of_live = client.post('/api/documentation/drafts', headers=HEADERS, json={'document_id': runbook})
    brand_new = client.post(
        '/api/documentation/drafts', headers=HEADERS, json={'slug': 'backups', 'title': 'Backups', 'markdown': '# B\n'}
    )
    assert of_live.status_code == 201 and brand_new.status_code == 201, (of_live.text, brand_new.text)
    entry = {'kind': 'server', 'id': server}
    for draft in (of_live.json()['id'], brand_new.json()['id']):
        refused = client.post('/api/inventory/documents', headers=HEADERS, json={'entry': entry, 'document_id': draft})
        assert message(refused) == (404, 'Document not found.')
    attached = client.post('/api/inventory/documents', headers=HEADERS, json={'entry': entry, 'document_id': runbook})
    assert attached.status_code == 201, attached.text
    with store.connect() as conn:
        assert [
            row['document_id'] for row in conn.execute('SELECT document_id FROM ka.inventory_documents').fetchall()
        ] == [UUID(runbook)]
