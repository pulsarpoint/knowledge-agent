"""Documents for draft tests: the client and the document Runbook (version 1, '# Runbook v1')."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from live_documents import rewrite_live_document, write_live_document
from test_api import _client
from test_repositories import HEADERS

API = '/api/documentation'


@pytest.fixture
def docs(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    created = write_live_document(store, 'runbook', 'Runbook', '# Runbook v1', description='How to run it')
    with store.connect() as conn:
        project = store.project(conn, 'shop')
    return SimpleNamespace(client=client, store=store, project=project, runbook=created['id'])


def make_document(docs, slug: str, title: str) -> str:
    return write_live_document(docs.store, slug, title, f'# {title}')['id']


def version_of(docs, document_id: str) -> int:
    """The document's live version number."""
    return docs.client.get(f'{API}/documents/{document_id}', headers=HEADERS).json()['version']


def new_draft(docs, **body) -> dict:
    response = docs.client.post(f'{API}/drafts', headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def save_live(docs, document_id: str, markdown: str) -> dict:
    """A new live version written in place (not through a draft); the document's drafts stay."""
    return rewrite_live_document(docs.store, document_id, markdown)


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
