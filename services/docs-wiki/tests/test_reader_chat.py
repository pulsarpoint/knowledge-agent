"""Reader chat forwards to admin-api and maps events to the Reader protocol."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from docs_wiki import gaps, search_api
from docs_wiki.reader_client import ReaderClient

SESSION = '0b6f1e1a-1111-4a5b-8c9d-000000000001'


def sse_body(*events):
    return ''.join(f'event: {name}\ndata: {json.dumps(data)}\n\n' for name, data in events).encode()


@pytest.fixture
def upstream():
    state = {'events': [], 'status': 200, 'error': None, 'requests': []}
    def handle(request):
        state['requests'].append(request)
        if state['status'] != 200:
            return httpx.Response(state['status'], json={'error': {'message': state['error']}})
        if request.url.path == '/internal/reader/runs':
            return httpx.Response(200, content=sse_body(*state['events']), headers={'Content-Type': 'text/event-stream'})
        if request.url.path == '/internal/reader/sessions':
            return httpx.Response(200, json={'sessions': [{'id': SESSION, 'title': 'How many retries?', 'status': 'active',
                'created_at': '2026-10-02T10:00:00Z', 'last_activity_at': '2026-10-02T10:05:00Z'}]})
        if request.url.path == f'/internal/reader/sessions/{SESSION}':
            if request.method == 'DELETE':
                return httpx.Response(204)
            return httpx.Response(200, json={'id': SESSION, 'title': 'q', 'status': 'active', 'folders': ['payments'],
                'include_subfolders': True, 'messages': [{'role': 'user', 'content': 'q'},
                {'role': 'assistant', 'content': 'a [1]', 'sources': [{'n': 1, 'path': 'p/r.md', 'heading_path': 'p', 'anchor': None}], 'not_covered': False}]})
        raise AssertionError(request.url)
    return state, httpx.MockTransport(handle)


@pytest.fixture
def client(tmp_path, upstream):
    _, transport = upstream
    app = FastAPI()
    reader = ReaderClient('http://admin.test', 'reader-key', transport=transport)
    app.include_router(search_api.build_api('/api/docs', tmp_path / 'unused', None, None, reader=reader, gaps_dir=tmp_path / 'gaps'))
    with TestClient(app, headers={'X-Forwarded-User': 'alice', 'X-Forwarded-Email': 'alice@example.test',
                                  'X-Forwarded-Groups': 'docs-admins'}) as test_client:
        yield test_client


def parse(response):
    events, name = [], None
    for line in response.text.splitlines():
        if line.startswith('event: '):
            name = line[7:]
        elif line.startswith('data: '):
            events.append((name, json.loads(line[6:])))
    return events


def test_answer_maps_to_reader_events(client, upstream, tmp_path):
    state, _ = upstream
    grounding = {'sources': [{'n': 1, 'path': 'p/r.md', 'heading_path': 'p', 'anchor': None}], 'not_covered': False,
                 'near_misses': [], 'results': 1, 'reason': None}
    state['events'] = [('session', {'id': SESSION}), ('activity', {'tool': 'search_documents', 'argument': '{"query":"retries"}'}),
                       ('completed', {'status': 'completed', 'answer': 'Three [1].', 'error': '', 'grounding': grounding})]
    response = client.post('/api/docs/chat', json={'question': 'How many retries?', 'context': {'folders': ['payments'], 'include_subfolders': False, 'repositories': None}})
    assert response.status_code == 200
    assert parse(response) == [
        ('session', {'key': SESSION}), ('tool', {'name': 'search_documents', 'argument': '{"query":"retries"}'}),
        ('sources', {'sources': grounding['sources']}),
        ('done', {'text': 'Three [1].', 'not_covered': False, 'sources_used': ['docs']})]
    sent = state['requests'][0]
    assert sent.headers['x-reader-key'] == 'reader-key' and sent.headers['x-reader-user'] == 'alice'
    assert 'x-forwarded-groups' not in sent.headers and 'x-forwarded-user' not in sent.headers
    assert json.loads(sent.content) == {'question': 'How many retries?', 'folders': ['payments'], 'include_subfolders': False}
    assert gaps.load_gaps(tmp_path / 'gaps') == ([], 0)


def test_not_covered_logs_a_gap(client, upstream, tmp_path):
    state, _ = upstream
    state['events'] = [('session', {'id': SESSION}), ('completed', {'status': 'completed', 'answer': 'Not documented.', 'error': '',
        'grounding': {'sources': [], 'not_covered': True, 'near_misses': [{'path': 'p/r.md', 'heading_path': 'p'}], 'results': 1, 'reason': 'x'}})]
    events = parse(client.post('/api/docs/chat', json={'question': 'Refund SLA?'}))
    assert events[-1] == ('done', {'text': 'Not documented.', 'not_covered': True, 'sources_used': []})
    logged, skipped = gaps.load_gaps(tmp_path / 'gaps')
    assert skipped == 0 and logged[0]['question'] == 'Refund SLA?' and logged[0]['near_misses'][0]['path'] == 'p/r.md'
    assert 'session_ref' in logged[0] and SESSION not in json.dumps(logged)


def test_failed_turn_becomes_error(client, upstream, tmp_path):
    state, _ = upstream
    state['events'] = [('completed', {'status': 'failed', 'answer': '', 'error': 'The agent exceeded the time limit. Send a smaller task.', 'grounding': None})]
    assert parse(client.post('/api/docs/chat', json={'question': 'q', 'session': SESSION}))[-1] == (
        'error', {'message': 'The agent exceeded the time limit. Send a smaller task.'})


def test_stream_without_completion_reports_unavailable(client, upstream, tmp_path):
    state, _ = upstream
    state['events'] = [('session', {'id': SESSION})]
    assert parse(client.post('/api/docs/chat', json={'question': 'q'}))[-1] == ('error', {'message': 'Chat is temporarily unavailable.'})
    assert gaps.load_gaps(tmp_path / 'gaps') == ([], 0)


@pytest.mark.parametrize('status, message, expected_status, expected', [
    (409, 'Wait for the current answer.', 409, 'Wait for the current answer.'),
    (404, 'Unknown conversation', 404, 'Unknown conversation'),
    (401, 'Invalid reader key.', 503, 'Chat is temporarily unavailable.'),
    (500, 'boom', 503, 'Chat is temporarily unavailable.'),
])
def test_upstream_errors(client, upstream, status, message, expected_status, expected):
    state, _ = upstream
    state['status'], state['error'] = status, message
    response = client.post('/api/docs/chat', json={'question': 'q'})
    assert response.status_code == expected_status and response.json()['error']['message'] == expected


def test_unreachable_admin_api(tmp_path):
    def down(request):
        raise httpx.ConnectError('down')
    app = FastAPI()
    app.include_router(search_api.build_api('/api/docs', tmp_path, None, None,
        reader=ReaderClient('http://admin.test', 'k', transport=httpx.MockTransport(down)), gaps_dir=tmp_path))
    with TestClient(app, headers={'X-Forwarded-User': 'alice'}) as test_client:
        response = test_client.post('/api/docs/chat', json={'question': 'q'})
    assert response.status_code == 503 and response.json()['error']['message'] == 'Chat is temporarily unavailable.'


def test_history_transcript_and_delete(client):
    sessions = client.get('/api/docs/chats').json()['sessions']
    assert sessions == [{'session_key': SESSION, 'first_question': 'How many retries?',
                         'first_ts': '2026-10-02T10:00:00Z', 'last_ts': '2026-10-02T10:05:00Z'}]
    transcript = client.get(f'/api/docs/chats/{SESSION}').json()
    assert transcript['session'] == SESSION
    assert transcript['context'] == {'folders': ['payments'], 'include_subfolders': True, 'repositories': None}
    assert transcript['sources'] == {'docs': True}
    assert transcript['messages'][1] == {'role': 'assistant', 'content': 'a [1]', 'sources_used': ['docs'],
        'sources': [{'n': 1, 'path': 'p/r.md', 'heading_path': 'p', 'anchor': None}], 'not_covered': False}
    assert client.delete(f'/api/docs/chats/{SESSION}').status_code == 204


def test_identity_required(client):
    client.headers.pop('X-Forwarded-User')
    assert client.post('/api/docs/chat', json={'question': 'q'}).status_code == 403


@pytest.mark.parametrize('method, path, body', [
    ('POST', '/api/docs/chat', {'question': 'q', 'session': 'legacy-file-key'}),
    ('GET', '/api/docs/chats/legacy-file-key', None),
    ('DELETE', '/api/docs/chats/legacy-file-key', None),
])
def test_non_uuid_conversation_is_unknown(client, upstream, method, path, body):
    state, _ = upstream
    response = client.request(method, path, json=body)
    assert response.status_code == 404 and response.json() == {'error': {'message': 'Unknown conversation'}}
    assert state['requests'] == []


def test_heartbeats_are_forwarded(tmp_path):
    def handle(request):
        body = b': heartbeat\n\n' + sse_body(('session', {'id': SESSION})) + b': heartbeat\n\n' + sse_body(
            ('completed', {'status': 'completed', 'answer': 'A.', 'error': '', 'grounding': {'sources': [], 'not_covered': False}}))
        return httpx.Response(200, content=body, headers={'Content-Type': 'text/event-stream'})
    app = FastAPI()
    app.include_router(search_api.build_api('/api/docs', tmp_path, None, None,
        reader=ReaderClient('http://admin.test', 'k', transport=httpx.MockTransport(handle)), gaps_dir=tmp_path))
    with TestClient(app, headers={'X-Forwarded-User': 'alice'}) as test_client:
        response = test_client.post('/api/docs/chat', json={'question': 'q'})
    assert response.text.count(': heartbeat\n\n') == 2
    assert [name for name, _ in parse(response)] == ['session', 'sources', 'done']


def test_client_yields_heartbeat_events():
    def handle(request):
        return httpx.Response(200, content=b': heartbeat\n\n' + sse_body(('session', {'id': SESSION})),
                              headers={'Content-Type': 'text/event-stream'})
    reader = ReaderClient('http://admin.test', 'k', transport=httpx.MockTransport(handle))
    async def collect():
        return [event async for event in await reader.run('alice', '', {'question': 'q'})]
    events = asyncio.run(collect())
    assert events == [('heartbeat', {}), ('session', {'id': SESSION})]


def test_rejected_reader_key_is_logged(client, upstream, caplog):
    state, _ = upstream
    state['status'], state['error'] = 401, 'Invalid reader key.'
    with caplog.at_level('ERROR', logger='docs_wiki.reader_client'):
        client.post('/api/docs/chat', json={'question': 'q'})
    rejected = [record for record in caplog.records if record.name == 'docs_wiki.reader_client' and record.levelname == 'ERROR']
    assert rejected and 'reader-key' not in rejected[0].getMessage()


def test_gap_is_logged_before_done_is_sent(tmp_path, upstream):
    from starlette.requests import Request
    state, transport = upstream
    state['events'] = [('session', {'id': SESSION}), ('completed', {'status': 'completed', 'answer': 'Not documented.', 'error': '',
        'grounding': {'sources': [], 'not_covered': True, 'near_misses': [], 'results': 0, 'reason': 'x'}})]
    router = search_api.build_api('/api/docs', tmp_path, None, None,
        reader=ReaderClient('http://admin.test', 'reader-key', transport=transport), gaps_dir=tmp_path / 'gaps')
    endpoint = next(route.endpoint for route in router.routes if route.path == '/api/docs/chat' and 'POST' in route.methods)
    request = Request({'type': 'http', 'method': 'POST', 'headers': [(b'x-forwarded-user', b'alice')]})
    async def disconnect_after_done():
        response = await endpoint(search_api.ChatInput(question='Refund SLA?'), request)
        async for chunk in response.body_iterator:
            if chunk.startswith('event: done'):
                break  # the user disconnects as soon as the answer arrives
        await response.body_iterator.aclose()
    asyncio.run(disconnect_after_done())
    assert gaps.load_gaps(tmp_path / 'gaps')[0][0]['question'] == 'Refund SLA?'
