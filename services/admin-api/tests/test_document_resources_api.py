"""Documents API on the document model: built-for links, link history, versions by number, and readers that never
show drafts (disposable DB)."""

from uuid import UUID, uuid4

import pytest

from knowledge_admin.documents.api import backfill_exports
from knowledge_admin.documents.store import Provenance, create_draft
from live_documents import write_live_document
from test_api import _client
from test_repositories import HEADERS

VALUE = {'slug': 'runbook', 'title': 'Runbook', 'markdown': '# Runbook\n'}
DOCUMENTS = '/api/documentation/documents'
DRAFTS = '/api/documentation/drafts'
NO_LINKS = {'code_project_ids': [], 'service_ids': [], 'server_ids': [], 'deployment_ids': [], 'environment_ids': []}


def resource(store, table: str, name: str) -> str:
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        return str(
            conn.execute(
                f'INSERT INTO ka.{table}(project_id,name) VALUES (%s,%s) RETURNING id', (project, name)
            ).fetchone()['id']
        )


def live(store) -> str:
    """The live document Runbook (VALUE), version 1, revision 1, no links."""
    return write_live_document(store, VALUE['slug'], VALUE['title'], VALUE['markdown'])['id']


def save_links(client, endpoint: str, revision: int, **ids):
    return client.put(endpoint + '/links', headers=HEADERS, json={**NO_LINKS, **ids, 'revision': revision})


@pytest.fixture
def client(store, tmp_path, monkeypatch):
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(tmp_path / 'missing'))  # exports fail quietly unless a test sets it
    return _client(tmp_path, repository_store=store)


def test_a_published_document_is_built_for_its_resources_and_unticking_is_refused(store, client):
    env = resource(store, 'environments', 'Production')
    draft = client.post(DRAFTS, headers=HEADERS, json={**VALUE, 'resource_ids': {'environment_ids': [env]}})
    assert draft.status_code == 201, draft.text
    published = client.post(f'{DRAFTS}/{draft.json()["id"]}/promote', headers=HEADERS, json={'revision': 1})
    assert published.status_code == 200, published.text
    revision = published.json()['revision']
    endpoint = f'{DOCUMENTS}/{draft.json()["id"]}'
    document = client.get(endpoint, headers=HEADERS).json()
    assert [
        (link['type'], link['id'], link['name'], link['connection'], link['added_how'])
        for link in document['resource_links']
    ] == [('environment', env, 'Production', 'built_for', 'person')]
    assert document['resource_links'][0]['added_by'] == 'admin@example.test'
    assert 'current_version' not in document and 'current_version_id' not in document
    refused = save_links(client, endpoint, revision, environment_ids=[])
    assert refused.status_code == 409
    assert (
        refused.json()['error']['message']
        == "This document was built for environment Production; that link can't be removed."
    )
    after = client.get(endpoint, headers=HEADERS).json()
    assert after['revision'] == revision and after['environment_ids'] == [env]
    assert [link['connection'] for link in after['resource_links']] == ['built_for']


def test_added_and_removed_links_are_kept_in_the_resource_history(store, client):
    service = resource(store, 'services', 'Payments')
    endpoint = f'{DOCUMENTS}/{live(store)}'
    added = save_links(client, endpoint, 1, service_ids=[service])
    assert added.status_code == 200, added.text
    links = client.get(endpoint, headers=HEADERS).json()['resource_links']
    assert [(link['name'], link['connection'], link['added_how']) for link in links] == [
        ('Payments', 'linked', 'person')
    ]
    removed = save_links(client, endpoint, 2, service_ids=[])
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
    again = save_links(client, endpoint, 3, service_ids=[service])
    assert again.status_code == 200, again.text
    rows = client.get(endpoint + '/resource-history', headers=HEADERS).json()['history']
    assert [row['removed_at'] is None for row in rows] == [True, False]
    assert client.get(f'{DOCUMENTS}/{uuid4()}/resource-history', headers=HEADERS).status_code == 404


def test_a_links_only_save_keeps_the_version_and_a_promoted_draft_makes_a_new_one(store, client):
    service = resource(store, 'services', 'Payments')
    document_id = live(store)
    endpoint = f'{DOCUMENTS}/{document_id}'
    linked = save_links(client, endpoint, 1, service_ids=[service])
    assert linked.status_code == 200, linked.text
    assert linked.json()['revision'] == 2 and 'version' not in linked.json()
    document = client.get(endpoint, headers=HEADERS).json()
    assert (document['version'], document['revision'], document['service_ids']) == (1, 2, [service])
    assert [v['version'] for v in document['versions']] == [1]
    draft = client.post(DRAFTS, headers=HEADERS, json={'document_id': document_id, 'markdown': '# Two\n'}).json()
    edited = client.post(f'{DRAFTS}/{draft["id"]}/promote', headers=HEADERS, json={'revision': 1})
    assert edited.status_code == 200, edited.text
    assert edited.json()['version'] == 2 and edited.json()['revision'] == 3 and 'version_id' not in edited.json()
    document = client.get(endpoint, headers=HEADERS).json()
    assert (document['markdown'], document['service_ids']) == ('# Two\n', [service])
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
    live = write_live_document(store, VALUE['slug'], 'Payments runbook', VALUE['markdown'])
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
        prov = Provenance('person', None)
        brand_new = create_draft(conn, project, prov, content, origin='manual', slug='draft-only', folder='team')
        of_live = create_draft(conn, project, prov, content, origin='manual', document_id=UUID(live['id']))
    write_live_document(store, 'guide', 'Payments guide', '# G', folder='team', export=False)
    listing = client.get(DOCUMENTS, params={'q': 'payments'}, headers=HEADERS).json()
    assert listing['total'] == 2
    assert {row['title'] for row in listing['documents']} == {'Payments runbook', 'Payments guide'}
    with_drafts = client.get(DOCUMENTS, params={'has_drafts': True}, headers=HEADERS).json()
    assert [(row['id'], row['draft_count']) for row in with_drafts['documents']] == [(live['id'], 1)]
    assert with_drafts['total'] == 1
    for draft in (brand_new, of_live):
        assert client.get(f'{DOCUMENTS}/{draft}/versions/1', headers=HEADERS).status_code == 404
        response = client.delete(f'{DOCUMENTS}/{draft}', headers=HEADERS, params={'revision': 1, 'confirmed': True})
        assert response.status_code == 404
        assert client.get(f'{DOCUMENTS}/{draft}/bundle', headers=HEADERS).status_code == 404
        assert client.post(f'{DOCUMENTS}/{draft}/export', headers=HEADERS).status_code == 404
    # A brand-new draft opens as the document it will become (is_draft) and owns its links; a draft of a document
    # opens only as a draft.
    opened = client.get(f'{DOCUMENTS}/{brand_new}', headers=HEADERS).json()
    assert (opened['is_draft'], opened['published'], opened['versions']) == (True, False, [])
    assert client.get(f'{DOCUMENTS}/{brand_new}/resource-history', headers=HEADERS).json() == {'history': []}
    assert client.get(f'{DOCUMENTS}/{of_live}', headers=HEADERS).status_code == 404
    assert client.get(f'{DOCUMENTS}/{of_live}/resource-history', headers=HEADERS).status_code == 404
    drafts = client.get(f'{DOCUMENTS}/{live["id"]}', headers=HEADERS).json()['drafts']
    assert [(d['id'], d['title'], d['origin'], d['base_version'], d['stale']) for d in drafts] == [
        (str(of_live), 'Payments draft', 'manual', 1, False)
    ]
    folders = client.get('/api/documentation/folders', headers=HEADERS).json()['folders']
    assert [(f['folder'], f['document_count']) for f in folders] == [('team', 1)]
    detail = client.get('/api/documentation/folders/team', headers=HEADERS).json()['documents']
    assert [d['slug'] for d in detail] == ['guide']
    # The export backfill writes live documents only.
    assert backfill_exports(store) == 1  # the guide (written without an export) — the runbook already has its export
    assert (out / 'team' / 'guide' / 'README.md').is_file() and not (out / 'team' / 'draft-only').exists()
    assert backfill_exports(store) == 0
