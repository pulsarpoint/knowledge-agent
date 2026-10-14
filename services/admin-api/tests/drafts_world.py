"""Documents for draft tests: the client, the export folder and the document Runbook (version 1, '# Runbook v1')."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from test_api import _client
from test_repositories import HEADERS

API = '/api/documentation'


@pytest.fixture
def docs(store, tmp_path, monkeypatch):
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    client = _client(tmp_path, repository_store=store)
    created = client.post(
        f'{API}/documents',
        headers=HEADERS,
        json={'slug': 'runbook', 'title': 'Runbook', 'description': 'How to run it', 'markdown': '# Runbook v1'},
    )
    assert created.status_code == 201, created.text
    with store.connect() as conn:
        project = store.project(conn, 'shop')
    return SimpleNamespace(client=client, store=store, out=out, project=project, runbook=created.json()['id'])


def make_document(docs, slug: str, title: str) -> str:
    response = docs.client.post(
        f'{API}/documents', headers=HEADERS, json={'slug': slug, 'title': title, 'markdown': f'# {title}'}
    )
    assert response.status_code == 201, response.text
    return response.json()['id']


def version_of(docs, document_id: str) -> str:
    return docs.client.get(f'{API}/documents/{document_id}', headers=HEADERS).json()['current_version_id']


def new_draft(docs, **body) -> dict:
    response = docs.client.post(f'{API}/drafts', headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def save_live(docs, document_id: str, markdown: str) -> dict:
    """A person saves a new live version in the editor."""
    current = docs.client.get(f'{API}/documents/{document_id}', headers=HEADERS).json()
    response = docs.client.put(
        f'{API}/documents/{document_id}',
        headers=HEADERS,
        json={
            'slug': current['slug'],
            'title': current['title'],
            'description': current['description'],
            'markdown': markdown,
            'revision': current['revision'],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def promote(docs, draft: dict, **extra):
    return docs.client.post(
        f'{API}/drafts/{draft["id"]}/promote', headers=HEADERS, json={'revision': draft['revision'], **extra}
    )


def audit(docs, resource_id) -> list[str]:
    with docs.store.connect() as conn:
        return [
            row['action']
            for row in conn.execute(
                'SELECT action FROM ka.audit_events WHERE resource_id=%s ORDER BY id', (UUID(str(resource_id)),)
            )
        ]
