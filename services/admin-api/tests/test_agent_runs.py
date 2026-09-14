"""Direct Test-tab runs use the selected saved agent, without the coordinator."""
import asyncio
from agents.usage import Usage
import json
import threading
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from test_api import _client
from test_sdk_agent import responses_api
from test_profiles import create
from test_repositories import HEADERS


@pytest.fixture
def direct(store, tmp_path, monkeypatch, responses_api):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('RAG_SOURCE_ROOT', str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    profile = create(client, 'Direct specialist')
    model = client.post('/api/llm/models', headers=HEADERS, json={
        'name':'Direct model', 'provider':'openai', 'api_format':'responses',
        'model':'direct-fixture', 'base_url':responses_api[0], 'api_key':'direct-test-secret'}).json()
    with store.connect() as conn:
        conn.execute('''UPDATE ka.agents SET model_id=%s,enabled=true,documents_enabled=false,
            repositories_enabled=false,ssh_enabled=false,skills_enabled=false,mcp_enabled=false,services_enabled=false
            WHERE id=%s''', (model['id'],profile['id']))
    return client, profile['id'], f"/api/agents/{profile['id']}/runs"


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_direct_endpoint_runs_official_sdk_and_keeps_scoped_history(direct, store, responses_api):
    client, agent_id, path = direct
    run_id = str(uuid4())
    result = client.post(path, headers=HEADERS, json={'id':run_id, 'task':'Inspect this specific agent.'})
    assert result.status_code == 200, result.text
    assert result.headers['content-type'].startswith('text/event-stream')
    started, *_, completed = events(result)
    assert started['id'] == run_id and started['status'] == 'running'
    assert completed['status'] == 'completed'
    assert completed['answer'] == 'Configured agent completed the task.'
    assert completed['context']['config']['id'] == agent_id
    assert completed['context']['activity']
    assert 'direct-test-secret' not in result.text
    requests = responses_api[1]
    assert len(requests) == 1
    payload = json.dumps(requests[0]['body'])
    assert 'Instructions for Direct specialist.' in payload and 'Inspect this specific agent.' in payload
    assert requests[0]['authorization'] == 'Bearer direct-test-secret'
    assert client.get(path, headers=HEADERS).json()['runs'][0]['status'] == 'completed'
    detail = f'/api/agents/{agent_id}/runs/{run_id}'
    assert client.get(detail, headers=HEADERS).json()['answer'] == completed['answer']
    assert client.get(f'/api/agents/code/runs/{run_id}', headers=HEADERS).status_code == 404
    assert client.post(f'/api/agents/code/runs/{run_id}/cancel', headers=HEADERS).status_code == 404
    assert client.post(path, headers=HEADERS, json={'id':run_id,'task':'Duplicate'}).status_code == 409
    assert len(requests) == 1
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_board_tasks').fetchone()['n'] == 0


@pytest.mark.parametrize('change, expected', [
    ('enabled=false', 'disabled'), ('model_id=NULL', 'Select an enabled model'),
])
def test_unconfigured_and_disabled_agents_do_not_start(direct, store, change, expected):
    client, agent_id, path = direct
    with store.connect() as conn:
        conn.execute(f'UPDATE ka.agents SET {change} WHERE id=%s', (agent_id,))
    result = client.post(path, headers=HEADERS, json={'task':'Inspect'})
    assert result.status_code == 409 and expected in result.text
    assert client.get(path, headers=HEADERS).json()['runs'] == []


def test_direct_endpoint_requires_admin_actor_and_rejects_coordinator(direct):
    client, _, path = direct
    assert client.post(path, json={'task':'Inspect'}).status_code == 403
    assert client.post(path, headers={'X-Forwarded-Groups':'docs-admins'}, json={'task':'Inspect'}).status_code == 403
    assert client.post('/api/agents/coordinator/runs', headers=HEADERS, json={'task':'Inspect'}).status_code == 409
    assert client.post('/api/agents/missing/runs', headers=HEADERS, json={'task':'Inspect'}).status_code == 404
    assert client.post(path, headers=HEADERS, json={'task':' '}).status_code == 400


def test_failure_is_persisted_and_secret_redacted(direct, monkeypatch):
    async def fail(config, task, **kwargs):
        raise ValueError('Unavailable direct-test-secret')
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent', fail)
    client, agent_id, path = direct
    result = client.post(path, headers=HEADERS, json={'task':'Inspect'})
    completed = events(result)[-1]
    assert completed['status'] == 'failed' and 'Unavailable' in completed['error']
    assert 'direct-test-secret' not in result.text
    saved = client.get(f"/api/agents/{agent_id}/runs/{completed['id']}", headers=HEADERS)
    assert saved.json()['status'] == 'failed' and 'direct-test-secret' not in saved.text


def test_cancellation_stops_turn_and_running_history_does_not_interrupt_it(direct, monkeypatch):
    entered, stopped = threading.Event(), threading.Event()
    async def slow(config, task, **kwargs):
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            stopped.set()
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent', slow)
    client, agent_id, path = direct
    run_id = str(uuid4())
    with ThreadPoolExecutor() as pool:
        future = pool.submit(client.post, path, headers=HEADERS, json={'id':run_id,'task':'Wait'})
        assert entered.wait(10)
        assert client.get(path, headers=HEADERS).json()['runs'][0]['status'] == 'running'
        assert client.post(f'/api/agents/{agent_id}/runs/{run_id}/cancel', headers=HEADERS).status_code == 200
        result = future.result(timeout=10)
    assert stopped.is_set()
    assert events(result)[-1]['status'] == 'cancelled'


def test_history_recovers_interrupted_run(direct, store):
    client, agent_id, path = direct
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'test-admin', 'admin@test')
        conn.execute('''INSERT INTO ka.operations_runs(project_id,actor_id,agent,task,context)
            VALUES (%s,%s,%s,'Interrupted test','{"runtime":"codex-direct"}')''', (project,actor,agent_id))
    assert client.get(path, headers=HEADERS).json()['runs'][0]['status'] == 'failed'


def test_closing_stream_keeps_session_and_turn_alive(direct, store, monkeypatch):
    client, agent_id, _ = direct
    run_id = str(uuid4())
    stopped = []
    async def slow(config, task, **kwargs):
        try:
            await asyncio.sleep(0.1)
            return SimpleNamespace(final_output='Continued after disconnect',context_wrapper=SimpleNamespace(usage=Usage()),
                to_input_list=lambda: [{'role':'user','content':task},{'role':'assistant','content':'Continued after disconnect'}])
        finally:
            stopped.append(True)
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent', slow)

    async def disconnect():
        incoming = asyncio.Queue()
        await incoming.put({'type':'http.request', 'body':json.dumps({'id':run_id,'task':'Wait'}).encode()})
        async def send(message):
            if message['type'] == 'http.response.body' and b'event: started' in message.get('body', b''):
                await asyncio.sleep(0)
                await incoming.put({'type':'http.disconnect'})
        scope = {'type':'http', 'asgi':{'version':'3.0','spec_version':'2.3'}, 'http_version':'1.1',
            'method':'POST', 'scheme':'http', 'path':f'/api/agents/{agent_id}/runs',
            'query_string':b'', 'root_path':'',
            'server':('testserver',80), 'client':('testclient',123),
            'headers':[(key.lower().encode(),value.encode()) for key,value in {**HEADERS,'Content-Type':'application/json'}.items()]}
        await asyncio.wait_for(client.app(scope, incoming.get, send), timeout=10)
        assert client.app.state.agent_runs
        await asyncio.gather(*client.app.state.agent_runs)
    asyncio.run(disconnect())
    assert stopped
    with store.connect() as conn:
        assert conn.execute('SELECT status FROM ka.operations_runs WHERE id=%s', (run_id,)).fetchone()['status'] == 'completed'
        assert conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,27002)) AS unlocked', (run_id,)).fetchone()['unlocked']
