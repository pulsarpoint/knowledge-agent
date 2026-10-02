"""Reader chat forwards to admin-api and maps events to the Reader protocol."""
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
        ('done', {'text': 'Three [1].', 'not_covered': False, 'escalation_offered': False, 'sources_used': ['docs']})]
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
    assert events[-1] == ('done', {'text': 'Not documented.', 'not_covered': True, 'escalation_offered': False, 'sources_used': []})
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
    assert transcript['sources'] == {'docs': True, 'codex': False}
    assert transcript['messages'][1] == {'role': 'assistant', 'content': 'a [1]', 'sources_used': ['docs'],
        'sources': [{'n': 1, 'path': 'p/r.md', 'heading_path': 'p', 'anchor': None}], 'not_covered': False}
    assert client.delete(f'/api/docs/chats/{SESSION}').status_code == 204


def test_identity_required(client):
    client.headers.pop('X-Forwarded-User')
    assert client.post('/api/docs/chat', json={'question': 'q'}).status_code == 403
