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
    paths = ['payments/retries.md', 'payments/runbooks/oncall.md', 'payments-old/secret.md', 'people/private.md']
    catalog = [{'path': path, 'folder': path.rsplit('/', 1)[0], 'description': f'Description of {path}'} for path in paths]
    def source(request):
        calls.append(request)
        if request.url.path == '/backoffice/workspace':
            return httpx.Response(200, json={'project_id': 'test-project'})
        if request.url.path.endswith('/tree'):
            return httpx.Response(200, json={'folders': [{'path': path, 'present': True, 'description': path, 'indexed_files': 1} for path in ['payments', 'payments/runbooks', 'payments-old', 'people']]})
        if request.url.path.endswith('/documents'):
            return httpx.Response(200, json=catalog)
        if request.url.path == '/search':
            # Include an out-of-scope hit to exercise the client's boundary as well as the server SQL tests.
            return httpx.Response(200, json={'results': [{'path': row['path'], 'folder': row['folder'].replace('/', '.'), 'text': row['description'], 'score': 1} for row in catalog]})
        if request.url.path.endswith('/file'):
            return httpx.Response(200, json={'path': request.url.params['path'], 'content': 'Allowed file content'})
        raise AssertionError(str(request.url))
    rag = WorkspaceRagClient('http://rag.test', 'key', httpx.MockTransport(source))
    cfg = config.resolve_llm({'base_url': 'http://llm.local/v1', 'chat_model': 'c'}, env={})
    app = FastAPI()
    app.include_router(search_api.build_api('/agent', tmp_path, rag))
    with TestClient(app, headers={'X-Forwarded-User': 'alice'}) as client:
        yield client, model, calls, rag
    rag.close()


def test_context_lists_document_folders_only(scoped_api):
    client, _, _, _ = scoped_api
    response = client.get('/agent/context')
    assert response.status_code == 200
    assert response.json()['repositories'] == [] and response.json()['warnings'] == []
    assert response.json()['folders'][0]['path'] == 'payments'
    assert client.get('/agent/status').json()['sources'] == {'docs': True}


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
