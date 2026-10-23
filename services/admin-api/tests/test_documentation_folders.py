"""Documentation folders: listed from the database for browsing; indexing happens in RAG → Collections."""

from uuid import uuid4

import pytest

from knowledge_admin.data_root import FOLDER_RULE
from live_documents import live_document
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def folders(store, tmp_path):
    """Two documents in payments, one in team/payments and one without a folder, all for the Payments API project."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        repo = uuid4()
        conn.execute(
            "INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'payments-api','Payments API')",
            (repo, project),
        )
        for slug, folder in (
            ('api-reference', 'payments'),
            ('runbook', 'payments'),
            ('api-reference', 'team/payments'),
            ('handbook', None),
        ):
            live_document(
                conn,
                project,
                slug,
                slug.replace('-', ' ').capitalize(),
                '# ' + slug,
                folder=folder,
                code_project_ids=[repo],
            )
    return _client(tmp_path, repository_store=store)


def test_folders_list_their_documents_projects(folders):
    body = folders.get('/api/documentation/folders', headers=HEADERS).json()
    assert [
        (f['folder'], f['document_count'], f['code_projects'], f['last_generated_at']) for f in body['folders']
    ] == [
        ('payments', 2, ['Payments API'], None),
        ('team/payments', 1, ['Payments API'], None),
    ]
    detail = folders.get('/api/documentation/folders/team/payments', headers=HEADERS).json()
    assert detail['folder'] == 'team/payments'
    assert [(d['slug'], d['title'], d['code_projects'], d['last_generated_at']) for d in detail['documents']] == [
        ('api-reference', 'Api reference', ['Payments API'], None)
    ]
    assert folders.get('/api/documentation/folders/missing', headers=HEADERS).status_code == 404


@pytest.mark.parametrize(
    'folder', ['.hidden', 'team/.git', 'a%20b', 'team/node_modules', 'services/payments', 'x' * 201]
)
def test_folder_routes_reject_bad_names(folders, folder):
    response = folders.get(f'/api/documentation/folders/{folder}', headers=HEADERS)
    assert response.status_code == 400 and response.json()['error']['message'] == FOLDER_RULE


def test_folders_require_admin(folders):
    assert folders.get('/api/documentation/folders').status_code == 403
