"""Configuration discovery and placeholder chat routes."""
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from docs_wiki import config, search_api
from docs_wiki.workspace_client import WorkspaceRagClient


@pytest.fixture
def scoped_api(tmp_path, fake_llm):
    model_transport, model = fake_llm
    calls = []
    catalog = [{'collection': 'payments', 'path': 'retries.md', 'description': 'Retries', 'collection_description': 'Payments'},
               {'collection': 'people', 'path': 'private.md', 'description': 'Private', 'collection_description': 'People'}]
    def source(request):
        calls.append(request)
        if request.url.path == '/backoffice/workspace':
            return httpx.Response(200, json={'project_id': 'test-project'})
        if request.url.path.endswith('/collections'):
            return httpx.Response(200, json={'collections': [
                {'name': 'payments', 'description': 'Payments', 'indexed_files': 2, 'folder_present': True},
                {'name': 'people', 'description': 'People', 'indexed_files': 1, 'folder_present': True},
                {'name': 'gone', 'description': 'Gone', 'indexed_files': 0, 'folder_present': False}]})
        if request.url.path.endswith('/documents'):
            return httpx.Response(200, json=catalog)
        if request.url.path == '/search':
            return httpx.Response(200, json={'results': [{'path': row['path'], 'collection': row['collection'], 'text': row['description'], 'score': 1} for row in catalog]})
        if request.url.path.endswith('/file'):
            return httpx.Response(200, json={'collection': request.url.params['collection'], 'path': request.url.params['path'], 'content': 'Allowed file content'})
        raise AssertionError(str(request.url))
    rag = WorkspaceRagClient('http://rag.test', 'key', httpx.MockTransport(source))
    cfg = config.resolve_llm({'base_url': 'http://llm.local/v1', 'chat_model': 'c'}, env={})
    app = FastAPI()
    app.include_router(search_api.build_api('/agent', tmp_path, rag))
    with TestClient(app, headers={'X-Forwarded-User': 'alice'}) as client:
        yield client, model, calls, rag
    rag.close()


def test_context_lists_collections_only(scoped_api):
    client, _, calls, _ = scoped_api
    response = client.get('/agent/context')
    assert response.status_code == 200
    assert response.json()['warnings'] == [] and 'repositories' not in response.json()
    assert response.json()['collections'] == [
        {'name': 'payments', 'description': 'Payments', 'indexed_files': 2},
        {'name': 'people', 'description': 'People', 'indexed_files': 1}]
    assert client.get('/agent/status').json()['sources'] == {'docs': True}
    listing = [c for c in calls if c.url.path.endswith('/collections')]
    assert listing and all(c.url.params['status'] == 'false' for c in listing)


@pytest.mark.parametrize('profile,expected', [
    ({'documents_enabled': True, 'all_collections': False, 'collections': ['payments']}, ['payments']),
    ({'documents_enabled': True, 'all_collections': True, 'collections': []}, ['payments', 'people']),
    ({'documents_enabled': False, 'all_collections': True, 'collections': []}, [])])
def test_context_filters_by_agent_profile(scoped_api, monkeypatch, profile, expected):
    client, _, _, _ = scoped_api
    monkeypatch.setattr(search_api, 'managed', lambda: True)
    monkeypatch.setattr(search_api, 'load_profile', lambda agent_id: {'id': 'search', 'name': 'S', **profile})
    names = [row['name'] for row in client.get('/agent/context').json()['collections']]
    assert names == expected


def test_workspace_client_maps_collection_paths(scoped_api):
    _, _, calls, rag = scoped_api
    hits = rag.search_collections('retries', ['payments'])
    assert hits[0]['path'] == 'payments/retries.md' and hits[0]['heading_path'] == 'payments'
    sent = json.loads([c for c in calls if c.url.path == '/search'][-1].content)
    assert sent['collections'] == ['payments'] and 'folder' not in sent and sent['ignore_unknown'] is True
    rag.search_collections('retries', None)
    assert 'ignore_unknown' not in json.loads([c for c in calls if c.url.path == '/search'][-1].content)
    assert rag.list_documents('x')[0]['path'] == 'payments/retries.md'
    assert rag.list_documents('x')[0]['quadrant'] == 'payments'
    doc = rag.get_document('x', 'payments/retries.md')
    assert doc['path'] == 'payments/retries.md' and doc['markdown'] == 'Allowed file content'
    last = [c for c in calls if c.url.path.endswith('/file')][-1]
    assert dict(last.url.params) == {'collection': 'payments', 'path': 'retries.md'}


def test_chat_todo_has_no_outbound_calls_or_session(scoped_api, tmp_path):
    client, model, calls, _ = scoped_api
    before = sorted(str(p) for p in tmp_path.rglob('*'))
    for _ in range(2):
        response = client.post('/agent/chat', json={'question': 'Inspect'})
        assert response.status_code == 200 and response.text == 'TODO'
        assert response.headers['content-type'].startswith('text/plain')
    assert calls == []
    assert client.get('/agent/chats').json() == {'sessions': []}
    assert sorted(str(p) for p in tmp_path.rglob('*')) == before
    assert client.post('/agent/chat', json={'question': ' '}).status_code == 400
    assert client.post('/agent/chat', json={'question': 'x', 'session': 'missing'}).status_code == 404
    client.headers.pop('X-Forwarded-User')
    assert client.post('/agent/chat', json={'question': 'x'}).status_code == 403


def test_existing_conversations_remain_private(tmp_path):
    import asyncio
    import hashlib
    from docs_wiki import sessions
    root = tmp_path / hashlib.sha256(b'alice').hexdigest()
    key = 'a' * 32
    async def seed():
        session = sessions.open_session(root, key)
        await session.add_items([{'role': 'user', 'content': 'Private old question'}, {'role': 'assistant', 'content': 'Private old answer'}])
    asyncio.run(seed())
    app = FastAPI()
    app.include_router(search_api.build_api('/agent', tmp_path, None))
    client = TestClient(app, headers={'X-Forwarded-User': 'alice'})
    before = (root / 'sessions.db').read_bytes()
    assert client.get(f'/agent/chats/{key}').status_code == 200
    assert client.post('/agent/chat', json={'question': 'Another', 'session': key}).text == 'TODO'
    assert client.post(f'/agent/chat/{key}/escalate', json={'question': 'Inspect'}).status_code in (404, 405)
    assert (root / 'sessions.db').read_bytes() == before
    for method, path, body in [('get', f'/agent/chats/{key}', None), ('post', '/agent/chat', {'question': 'Read', 'session': key})]:
        kwargs = {'headers': {'X-Forwarded-User': 'bob'}}
        if body is not None:
            kwargs['json'] = body
        assert getattr(client, method)(path, **kwargs).status_code == 404
