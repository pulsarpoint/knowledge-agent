"""Documents can be published to the RAG knowledge base and stay up to date with new versions."""
import hashlib
import json
from uuid import uuid4

import httpx
import pytest

from knowledge_admin import document_publish
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def rag(store, tmp_path, monkeypatch):
    monkeypatch.setenv('RAG_BACKOFFICE_KEY', 'rag-publish-key')
    state = {'files': {}, 'folders': {}, 'jobs': [], 'busy': False, 'down': False, 'deleted': []}

    def handle(request):
        if state['down']:
            raise httpx.ConnectError('RAG is down')
        assert request.headers['x-rag-key'] == 'rag-publish-key'
        path = request.url.path
        if path.endswith('/tree'):
            return httpx.Response(200, json={'revision': 1, 'folders': list(state['folders'].values())})
        if path.endswith('/folders'):
            body = json.loads(request.content)
            folder = {'id': str(uuid4()), 'path': body['path'], 'description': body['description'], 'present': True}
            state['folders'][body['path']] = folder
            return httpx.Response(200, json={'id': folder['id']})
        if path.endswith('/jobs'):
            if state['busy']:
                return httpx.Response(409, json={'error': {'message': 'A processing job is already active for this workspace'}})
            state['jobs'].append(json.loads(request.content))
            return httpx.Response(202, json={'id': str(uuid4())})
        assert path.endswith('/file')
        if request.method == 'GET':
            found = state['files'].get(request.url.params['path'])
            return httpx.Response(200, json=found) if found else httpx.Response(404, json={'error': {'message': 'File not found'}})
        if request.method == 'DELETE':
            target = request.url.params['path']
            assert request.url.params['expected_hash'] == state['files'][target]['hash']
            state['files'].pop(target)
            state['deleted'].append(target)
            return httpx.Response(200, json={'message': 'deleted'})
        body = json.loads(request.content)
        previous = state['files'].get(body['path'])
        assert body['expected_hash'] == (previous['hash'] if previous else None)
        saved = {'path': body['path'], 'content': body['content'], 'hash': hashlib.sha256(body['content'].encode()).hexdigest()}
        state['files'][body['path']] = saved
        return httpx.Response(200, json={'path': saved['path'], 'hash': saved['hash']})

    with httpx.Client(base_url='http://rag.test', transport=httpx.MockTransport(handle)) as backend:
        yield _client(tmp_path, repository_store=store, backoffice_client=backend), state, backend


def new_document(client, markdown='# Responsible gaming\n\nPlayers can set deposit limits.'):
    response = client.post('/api/documentation/documents', headers=HEADERS, json={
        'slug': 'responsible-gaming', 'title': 'Responsible gaming', 'markdown': markdown})
    assert response.status_code == 201, response.text
    return str(response.json()['id'])


def detail(client, document_id):
    return client.get(f'/api/documentation/documents/{document_id}', headers=HEADERS).json()


def test_publish_writes_marked_file_and_queues_indexing(rag):
    client, state, _ = rag
    document_id = new_document(client)
    response = client.post(f'/api/documentation/documents/{document_id}/publish', headers=HEADERS, json={'folder': 'documents'})
    assert response.status_code == 200, response.text
    file = state['files']['documents/responsible-gaming.md']
    assert file['content'].splitlines()[0] == f'<!-- knowledge-agent document {document_id} -->'
    assert 'Players can set deposit limits.' in file['content']
    assert state['jobs'] == [{'folder_id': state['folders']['documents']['id'], 'recursive': False}]
    saved = detail(client, document_id)
    assert saved['published_path'] == 'documents/responsible-gaming.md' and saved['published_version'] == 1
    assert saved['publish_error'] is None and saved['index_pending'] is False


def test_new_version_is_republished(rag):
    client, state, _ = rag
    document_id = new_document(client)
    client.post(f'/api/documentation/documents/{document_id}/publish', headers=HEADERS, json={'folder': 'documents'})
    updated = client.put(f'/api/documentation/documents/{document_id}', headers=HEADERS, json={
        'slug': 'responsible-gaming', 'title': 'Responsible gaming', 'markdown': '# Responsible gaming\n\nSelf-exclusion lasts 6 months.', 'revision': 1})
    assert updated.status_code == 200, updated.text
    assert 'Self-exclusion lasts 6 months.' in state['files']['documents/responsible-gaming.md']['content']
    assert len(state['jobs']) == 2
    assert detail(client, document_id)['published_version'] == 2


def test_busy_indexing_is_retried_later(rag, store):
    client, state, backend = rag
    document_id = new_document(client)
    state['busy'] = True
    assert client.post(f'/api/documentation/documents/{document_id}/publish', headers=HEADERS, json={'folder': 'documents'}).status_code == 200
    assert 'documents/responsible-gaming.md' in state['files'] and state['jobs'] == []
    assert detail(client, document_id)['index_pending'] is True
    state['busy'] = False
    document_publish.retry_pending(store, backend, 'shop')
    assert len(state['jobs']) == 1 and detail(client, document_id)['index_pending'] is False


def test_rag_failure_keeps_version_and_records_error(rag):
    client, state, _ = rag
    document_id = new_document(client)
    client.post(f'/api/documentation/documents/{document_id}/publish', headers=HEADERS, json={'folder': 'documents'})
    state['down'] = True
    updated = client.put(f'/api/documentation/documents/{document_id}', headers=HEADERS, json={
        'slug': 'responsible-gaming', 'title': 'Responsible gaming', 'markdown': '# New text', 'revision': 1})
    assert updated.status_code == 200
    saved = detail(client, document_id)
    assert saved['version'] == 2 and saved['published_version'] == 1
    assert 'RAG service is unavailable' in saved['publish_error']
    state['down'] = False
    assert client.post(f'/api/documentation/documents/{document_id}/publish', headers=HEADERS, json={'folder': 'documents'}).status_code == 200
    saved = detail(client, document_id)
    assert saved['published_version'] == 2 and saved['publish_error'] is None


def test_foreign_file_is_never_overwritten(rag):
    client, state, _ = rag
    document_id = new_document(client)
    state['folders']['documents'] = {'id': str(uuid4()), 'path': 'documents', 'description': '', 'present': True}
    state['files']['documents/responsible-gaming.md'] = {'path': 'documents/responsible-gaming.md', 'content': '# Hand-written', 'hash': 'h1'}
    response = client.post(f'/api/documentation/documents/{document_id}/publish', headers=HEADERS, json={'folder': 'documents'})
    assert response.status_code == 409 and 'belongs to another source' in response.text
    assert state['files']['documents/responsible-gaming.md']['content'] == '# Hand-written'
    assert detail(client, document_id)['published_path'] is None


def test_unpublish_removes_file_and_allows_delete(rag):
    client, state, _ = rag
    document_id = new_document(client)
    client.post(f'/api/documentation/documents/{document_id}/publish', headers=HEADERS, json={'folder': 'documents'})
    response = client.delete(f'/api/documentation/documents/{document_id}/publish', headers=HEADERS)
    assert response.status_code == 200, response.text
    assert state['deleted'] == ['documents/responsible-gaming.md']
    assert state['jobs'][-1] == {'folder_id': state['folders']['documents']['id'], 'recursive': False, 'allow_empty': True}
    saved = detail(client, document_id)
    assert saved['published_path'] is None and saved['published_version'] is None
    assert client.delete(f'/api/documentation/documents/{document_id}?revision=1&confirmed=true', headers=HEADERS).status_code == 200


@pytest.mark.parametrize('folder', ['../etc', '/abs', 'a//b', 'with space', ''])
def test_invalid_folder_is_rejected(rag, folder):
    client, state, _ = rag
    document_id = new_document(client)
    assert client.post(f'/api/documentation/documents/{document_id}/publish', headers=HEADERS, json={'folder': folder}).status_code == 400
    assert state['files'] == {}


def test_publishing_is_admin_only(rag):
    client, _, _ = rag
    document_id = new_document(client)
    assert client.post(f'/api/documentation/documents/{document_id}/publish', json={'folder': 'documents'}).status_code == 403
    assert client.delete(f'/api/documentation/documents/{document_id}/publish').status_code == 403


def test_slug_change_moves_the_published_file(rag):
    client, state, _ = rag
    document_id = new_document(client)
    client.post(f'/api/documentation/documents/{document_id}/publish', headers=HEADERS, json={'folder': 'documents'})
    client.put(f'/api/documentation/documents/{document_id}', headers=HEADERS, json={
        'slug': 'player-protection', 'title': 'Player protection', 'markdown': '# Player protection', 'revision': 1})
    assert set(state['files']) == {'documents/player-protection.md'}
    assert state['deleted'] == ['documents/responsible-gaming.md']
    assert detail(client, document_id)['published_path'] == 'documents/player-protection.md'
