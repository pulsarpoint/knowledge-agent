"""Documents API on the document model: built-for links, link history, versions by number, and readers that never
show drafts (disposable DB)."""

from uuid import UUID, uuid4

import pytest

from knowledge_admin.documents.api import DocumentInput, backfill_exports, save_document
from knowledge_admin.documents.store import Provenance, create_draft
from test_api import _client
from test_repositories import HEADERS

VALUE = {'slug': 'runbook', 'title': 'Runbook', 'markdown': '# Runbook\n'}
DOCUMENTS = '/api/documentation/documents'


def resource(store, table: str, name: str) -> str:
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        return str(
            conn.execute(
                f'INSERT INTO ka.{table}(project_id,name) VALUES (%s,%s) RETURNING id', (project, name)
            ).fetchone()['id']
        )


@pytest.fixture
def client(store, tmp_path, monkeypatch):
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path / 'missing'))  # exports fail quietly unless a test sets it
    return _client(tmp_path, repository_store=store)


def test_a_created_document_is_built_for_its_resources_and_unticking_is_refused(store, client):
    env = resource(store, 'environments', 'Production')
    created = client.post(DOCUMENTS, headers=HEADERS, json={**VALUE, 'environment_ids': [env]})
    assert created.status_code == 201, created.text
    endpoint = f'{DOCUMENTS}/{created.json()["id"]}'
    document = client.get(endpoint, headers=HEADERS).json()
    assert [
        (link['type'], link['id'], link['name'], link['connection'], link['added_how'])
        for link in document['resource_links']
    ] == [('environment', env, 'Production', 'built_for', 'person')]
    assert document['resource_links'][0]['added_by'] == 'admin@example.test'
    assert 'current_version' not in document and 'current_version_id' not in document
    refused = client.put(endpoint, headers=HEADERS, json={**VALUE, 'revision': 1, 'environment_ids': []})
    assert refused.status_code == 409
    assert (
        refused.json()['error']['message']
        == "This document was built for environment Production; that link can't be removed."
    )
    after = client.get(endpoint, headers=HEADERS).json()
    assert after['revision'] == 1 and after['environment_ids'] == [env]
    assert [link['connection'] for link in after['resource_links']] == ['built_for']


def test_added_and_removed_links_are_kept_in_the_resource_history(store, client):
    service = resource(store, 'services', 'Payments')
    endpoint = f'{DOCUMENTS}/{client.post(DOCUMENTS, headers=HEADERS, json=VALUE).json()["id"]}'
    added = client.put(endpoint, headers=HEADERS, json={**VALUE, 'revision': 1, 'service_ids': [service]})
    assert added.status_code == 200, added.text
    links = client.get(endpoint, headers=HEADERS).json()['resource_links']
    assert [(link['name'], link['connection'], link['added_how']) for link in links] == [
        ('Payments', 'linked', 'person')
    ]
    removed = client.put(endpoint, headers=HEADERS, json={**VALUE, 'revision': 2, 'service_ids': []})
    assert removed.status_code == 200, removed.text
    assert client.get(endpoint, headers=HEADERS).json()['resource_links'] == []
    history = client.get(endpoint + '/resource-history', headers=HEADERS)
    assert history.status_code == 200, history.text
    rows = history.json()['history']
    assert [(row['name'], row['connection'], row['removed_by']) for row in rows] == [
        ('Payments', 'linked', 'admin@example.test')
    ]
    assert rows[0]['removed_at'] is not None and rows[0]['created_at'] < rows[0]['removed_at']
    # Added again: a new row, newest change first.
    again = client.put(endpoint, headers=HEADERS, json={**VALUE, 'revision': 3, 'service_ids': [service]})
    assert again.status_code == 200, again.text
    rows = client.get(endpoint + '/resource-history', headers=HEADERS).json()['history']
    assert [row['removed_at'] is None for row in rows] == [True, False]
    assert client.get(f'{DOCUMENTS}/{uuid4()}/resource-history', headers=HEADERS).status_code == 404


def test_a_links_only_save_keeps_the_version_and_a_text_change_makes_a_new_one(store, client):
    service = resource(store, 'services', 'Payments')
    endpoint = f'{DOCUMENTS}/{client.post(DOCUMENTS, headers=HEADERS, json=VALUE).json()["id"]}'
    linked = client.put(endpoint, headers=HEADERS, json={**VALUE, 'revision': 1, 'service_ids': [service]})
    assert linked.status_code == 200, linked.text
    assert linked.json()['version'] == 1 and linked.json()['revision'] == 2
    document = client.get(endpoint, headers=HEADERS).json()
    assert (document['version'], document['revision'], document['service_ids']) == (1, 2, [service])
    assert [v['version'] for v in document['versions']] == [1]
    edited = client.put(
        endpoint, headers=HEADERS, json={**VALUE, 'revision': 2, 'service_ids': [service], 'markdown': '# Two\n'}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()['version'] == 2 and edited.json()['revision'] == 3 and 'version_id' not in edited.json()
    document = client.get(endpoint, headers=HEADERS).json()
    assert document['markdown'] == '# Two\n'
    assert [(v['version'], v['live']) for v in document['versions']] == [(2, True), (1, False)]
    old = client.get(endpoint + '/versions/1', headers=HEADERS)
    assert old.status_code == 200 and old.json()['markdown'] == '# Runbook\n' and old.json()['version'] == 1
    assert client.get(endpoint + '/versions/2', headers=HEADERS).json()['markdown'] == '# Two\n'
    assert client.get(endpoint + '/versions/3', headers=HEADERS).status_code == 404


def test_readers_never_show_drafts_as_documents(store, client, tmp_path, monkeypatch):
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path))
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    live = client.post(DOCUMENTS, headers=HEADERS, json={**VALUE, 'title': 'Payments runbook'}).json()
    content = {
        'title': 'Payments draft',
        'description': '',
        'purpose': 'explanation',
        'audience': 'both',
        'tags': [],
        'markdown': '# Draft\n',
    }
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        save_document(
            conn, project, None, DocumentInput(slug='guide', title='Payments guide', markdown='# G'), folder='team'
        )
        prov = Provenance('person', None)
        brand_new = create_draft(conn, project, prov, content, origin='manual', slug='draft-only', folder='team')
        of_live = create_draft(conn, project, prov, content, origin='manual', document_id=UUID(live['id']))
    listing = client.get(DOCUMENTS, params={'q': 'payments'}, headers=HEADERS).json()
    assert listing['total'] == 2
    assert {row['title'] for row in listing['documents']} == {'Payments runbook', 'Payments guide'}
    with_drafts = client.get(DOCUMENTS, params={'has_drafts': True}, headers=HEADERS).json()
    assert [(row['id'], row['draft_count']) for row in with_drafts['documents']] == [(live['id'], 1)]
    assert with_drafts['total'] == 1
    for draft in (brand_new, of_live):
        assert client.get(f'{DOCUMENTS}/{draft}', headers=HEADERS).status_code == 404
        assert client.get(f'{DOCUMENTS}/{draft}/versions/1', headers=HEADERS).status_code == 404
        assert client.get(f'{DOCUMENTS}/{draft}/resource-history', headers=HEADERS).status_code == 404
        response = client.delete(f'{DOCUMENTS}/{draft}', headers=HEADERS, params={'revision': 1, 'confirmed': True})
        assert response.status_code == 404
    drafts = client.get(f'{DOCUMENTS}/{live["id"]}', headers=HEADERS).json()['drafts']
    assert [(d['id'], d['title'], d['origin'], d['base_version'], d['stale']) for d in drafts] == [
        (str(of_live), 'Payments draft', 'manual', 1, False)
    ]
    folders = client.get('/api/documentation/folders', headers=HEADERS).json()['folders']
    assert [(f['folder'], f['document_count']) for f in folders] == [('team', 1)]
    detail = client.get('/api/documentation/folders/team', headers=HEADERS).json()['documents']
    assert [d['slug'] for d in detail] == ['guide']
    # The export backfill writes live documents only.
    assert backfill_exports(store) == 1  # the guide (saved without the route) — the runbook already has its export
    assert (out / 'team' / 'guide' / 'README.md').is_file() and not (out / 'team' / 'draft-only').exists()
    assert backfill_exports(store) == 0
