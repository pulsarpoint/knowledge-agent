"""Searchable code projects keep one RAG path per mapped folder in their chosen collection."""
import json
import re
from uuid import UUID

import httpx
import pytest

from knowledge_admin.code_project_search import CODE_INCLUDE, default_collection, top_folders
from test_api import _client
from test_repositories import HEADERS


class FakeRag:
    """Owned paths as the RAG service keeps them: one path per owner, re-pointed when added again."""

    def __init__(self):
        self.paths: dict[tuple, dict] = {}
        self.down = False
        self.calls: list[tuple] = []
        self.bodies: list[dict] = []

    def __call__(self, request):
        self.calls.append((request.method, request.url.path, dict(request.url.params)))
        if self.down:
            raise httpx.ConnectError('refused')
        path = request.url.path
        if request.method == 'GET' and path.endswith('/collections'):
            names = sorted({p['collection'] for p in self.paths.values()})
            return httpx.Response(200, json={'collections': [{'name': name, 'paths': [
                {'source_path': p['source_path'], 'owner_kind': k[0], 'owner_id': k[1], 'owner_key': k[2]}
                for k, p in self.paths.items() if p['collection'] == name]} for name in names]})
        if request.method == 'PUT' and path.endswith('/paths'):
            body = json.loads(request.content)
            self.bodies.append(body)
            if not re.fullmatch(r'code(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*', body['source_path']):
                return httpx.Response(400, json={'error': {'message': 'Choose a folder inside code, code-index, rag-data, generated-documents or docs'}})
            name = path.split('/by-name/')[1].split('/')[0]
            self.paths[(body['owner_kind'], body.get('owner_id'), body.get('owner_key'))] = {
                'collection': name, 'source_path': body['source_path']}
            return httpx.Response(200, json={'name': name, 'paths': []})
        if request.method == 'DELETE' and path.endswith('/paths'):
            query = request.url.params
            gone = [k for k in self.paths if k[0] == query['owner_kind'] and k[1] == query.get('owner_id')
                    and ('owner_key' not in query or k[2] == query['owner_key'])]
            for k in gone:
                del self.paths[k]
            return httpx.Response(200, json={'removed': len(gone)})
        raise AssertionError(path)

    def owned(self, project_id: str) -> set[tuple[str, str]]:
        return {(p['collection'], p['source_path']) for k, p in self.paths.items() if k[1] == project_id}


@pytest.fixture
def searchable(store, tmp_path, monkeypatch):
    root = tmp_path / 'code'
    root.mkdir()
    for folder in ('api', 'shared', 'other', 'my api'):
        (root / folder).mkdir()
    monkeypatch.setenv('CODE_ROOT', str(root))
    monkeypatch.setenv('RAG_BACKOFFICE_KEY', 'rag-key')
    rag = FakeRag()
    with httpx.Client(base_url='http://rag.test', transport=httpx.MockTransport(rag)) as backend:
        client = _client(tmp_path, repository_store=store, backoffice_client=backend)
        created = client.post('/api/code-projects', headers=HEADERS, json={
            'name': 'Payments API', 'description': '', 'remote_url': None, 'folder_paths': ['api', 'shared']})
        assert created.status_code == 201, created.text
        yield client, rag, created.json()['id']


def project(client, project_id):
    return next(p for p in client.get('/api/code-projects', headers=HEADERS).json()['projects'] if p['id'] == project_id)


def search(client, project_id, **body):
    return client.put(f'/api/code-projects/{project_id}/search', headers=HEADERS, json=body)


def edit_folders(client, project_id, folders):
    row = project(client, project_id)
    return client.put(f'/api/code-projects/{project_id}', headers=HEADERS, json={
        'name': row['name'], 'description': row['description'], 'remote_url': None, 'folder_paths': folders, 'revision': row['revision']})


def test_defaults():
    assert default_collection('Payments API', UUID(int=1)) == 'payments-api'
    assert default_collection('  ### ', UUID('12345678-0000-4000-8000-000000000000')) == 'code-12345678'
    assert len(default_collection('x' * 80, UUID(int=1))) == 60
    assert top_folders(['api', 'api/src', 'shared', 'apis']) == ['api', 'shared', 'apis']


def test_searchable_on_adds_every_folder_to_the_default_collection(searchable):
    client, rag, project_id = searchable
    row = project(client, project_id)
    assert (row['searchable'], row['collection'], row['search_error']) == (False, None, None)
    response = search(client, project_id, enabled=True)
    assert response.status_code == 200, response.text
    assert response.json() == {'searchable': True, 'collection': 'payments-api', 'search_error': None}
    assert rag.owned(project_id) == {('payments-api', 'code/api'), ('payments-api', 'code/shared')}
    row = project(client, project_id)
    assert (row['searchable'], row['collection'], row['search_error']) == (True, 'payments-api', None)


def test_searchable_off_removes_the_project_paths_everywhere(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    response = search(client, project_id, enabled=False)
    assert response.json() == {'searchable': False, 'collection': None, 'search_error': None}
    assert rag.owned(project_id) == set()
    assert ('DELETE', 'code_project', project_id) in {(m, q.get('owner_kind'), q.get('owner_id')) for m, _, q in rag.calls}
    assert project(client, project_id)['searchable'] is False


def test_folder_edit_adds_and_removes_paths(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    edited = edit_folders(client, project_id, ['shared', 'other'])
    assert edited.status_code == 200 and edited.json()['search_error'] is None
    assert rag.owned(project_id) == {('payments-api', 'code/other'), ('payments-api', 'code/shared')}


def test_changing_collection_moves_project_paths(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    moved = search(client, project_id, enabled=True, collection='payments')
    assert moved.json()['collection'] == 'payments'
    assert rag.owned(project_id) == {('payments', 'code/api'), ('payments', 'code/shared')}


def test_archive_removes_paths_and_hides_the_project(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    archived = client.post(f'/api/code-projects/{project_id}/archive', headers=HEADERS)
    assert archived.status_code == 200 and archived.json() == {'id': project_id, 'search_error': None}
    assert rag.owned(project_id) == set()
    assert all(p['id'] != project_id for p in client.get('/api/code-projects', headers=HEADERS).json()['projects'])
    assert client.post(f'/api/code-projects/{project_id}/archive', headers=HEADERS).status_code == 404


def test_rag_failure_keeps_project_and_retry_reconciles(searchable):
    client, rag, project_id = searchable
    rag.down = True
    response = search(client, project_id, enabled=True)
    assert response.status_code == 200
    assert response.json() == {'searchable': True, 'collection': 'payments-api', 'search_error': 'RAG service is unavailable. Retry later.'}
    edited = edit_folders(client, project_id, ['api'])
    assert edited.status_code == 200 and edited.json()['search_error'] == 'RAG service is unavailable. Retry later.'
    row = project(client, project_id)
    assert row['folder_paths'] == ['api'] and row['searchable'] is True and row['search_error'] == 'RAG service is unavailable. Retry later.'
    rag.down = False
    retried = search(client, project_id, enabled=True, collection='payments-api')
    assert retried.json()['search_error'] is None
    assert rag.owned(project_id) == {('payments-api', 'code/api')}
    assert project(client, project_id)['search_error'] is None


def test_invalid_collection_names_are_rejected_before_rag(searchable):
    client, rag, project_id = searchable
    for bad in ('Payments', 'a--b', 'x' * 61, '../x', 'jira_tickets'):
        assert search(client, project_id, enabled=True, collection=bad).status_code == 400, bad
    assert search(client, '00000000-0000-4000-8000-000000000000', enabled=True).status_code == 404
    assert rag.calls == []


def test_projects_that_are_not_searchable_never_call_rag(searchable):
    client, rag, project_id = searchable
    assert edit_folders(client, project_id, ['other']).status_code == 200
    assert rag.calls == []


def test_archive_with_rag_down_keeps_project_and_retry_releases_paths(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    rag.down = True
    failed = client.post(f'/api/code-projects/{project_id}/archive', headers=HEADERS)
    assert failed.status_code == 502
    row = project(client, project_id)
    assert row['searchable'] is True and row['search_error']
    assert rag.owned(project_id) == {('payments-api', 'code/api'), ('payments-api', 'code/shared')}
    rag.down = False
    assert client.post(f'/api/code-projects/{project_id}/archive', headers=HEADERS).status_code == 200
    assert rag.owned(project_id) == set()
    assert all(p['id'] != project_id for p in client.get('/api/code-projects', headers=HEADERS).json()['projects'])


def test_retry_without_collection_keeps_the_stored_collection(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True, collection='payments')
    assert search(client, project_id, enabled=True).json()['collection'] == 'payments'
    assert rag.owned(project_id) == {('payments', 'code/api'), ('payments', 'code/shared')}


def test_a_new_collection_indexes_code_and_docs(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    assert rag.bodies and all(body['include'] == CODE_INCLUDE for body in rag.bodies)
    assert {'**/*.md', '**/*.py', '**/*.ts', '**/*.java', '**/*.go', '**/*.yaml', '**/*.json'} <= set(CODE_INCLUDE)


def test_a_folder_rag_cannot_index_is_named_in_the_error(searchable):
    client, rag, project_id = searchable
    edit_folders(client, project_id, ['my api'])
    response = search(client, project_id, enabled=True)
    message = "Folder my api cannot be indexed: names may use letters, numbers, '.', '_' and '-'"
    assert response.status_code == 200 and response.json()['search_error'] == message
    assert project(client, project_id)['search_error'] == message


def test_archive_after_a_failed_switch_off_still_releases_paths(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    rag.down = True
    off = search(client, project_id, enabled=False)
    assert off.json()['searchable'] is False and off.json()['search_error']
    assert rag.owned(project_id)  # the paths are still in RAG
    rag.down = False
    archived = client.post(f'/api/code-projects/{project_id}/archive', headers=HEADERS)
    assert archived.status_code == 200 and archived.json() == {'id': project_id, 'search_error': None}
    assert rag.owned(project_id) == set()
