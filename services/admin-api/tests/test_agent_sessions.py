"""Conversation lifecycle, native resume, and suggestions without configuration writes."""
import asyncio
from agents import ModelBehaviorError
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

from knowledge_admin.agent_sessions import expire_sessions
from test_api import _client
from test_agent_runs import direct, events
from test_sdk_agent import responses_api
from test_repositories import HEADERS


def test_followup_resumes_native_conversation_and_keeps_all_turns(direct, responses_api):
    client, agent_id, path = direct
    first = client.post(path,headers=HEADERS,json={'task':'Remember test word papaya.'})
    started = events(first)[0]
    session_id = started['session_id']
    second = client.post(path,headers=HEADERS,json={'task':'What word did I give you?','session_id':session_id})
    assert events(second)[-1]['status'] == 'completed'
    assert events(second)[0]['session_id'] == session_id
    saved = client.get(f'/api/agents/{agent_id}/sessions/{session_id}',headers=HEADERS).json()
    assert saved['status'] == 'active'
    assert [r['task'] for r in saved['runs']] == ['Remember test word papaya.','What word did I give you?']
    assert 'Remember test word papaya.' in json.dumps(responses_api[1][-1]['body'])
    assert 'Configured agent completed the task.' in json.dumps(responses_api[1][-1]['body'])
    assert 'direct-test-secret' not in json.dumps(saved)
    assert len(client.get(f'/api/agents/{agent_id}/sessions',headers=HEADERS).json()['sessions']) == 1
    assert client.get(f'/api/agents/code/sessions/{session_id}',headers=HEADERS).status_code == 404
    assert client.post('/api/agents/code/runs',headers=HEADERS,json={'task':'Cross-agent','session_id':session_id}).status_code in (404,409)


def test_close_and_expire_reject_followups_and_keep_database_history(direct, store, tmp_path):
    client, agent_id, path = direct
    for action in ('close','expire'):
        response = client.post(path,headers=HEADERS,json={'task':'First message'})
        session_id = events(response)[0]['session_id']
        directory = tmp_path/'state'/'agent-sessions'/session_id
        assert not directory.exists()
        with store.connect() as conn:
            history = conn.execute('SELECT history FROM ka.agent_sessions WHERE id=%s',(session_id,)).fetchone()['history']
        assert history
        if action == 'close':
            assert client.post(f'/api/agents/{agent_id}/sessions/{session_id}/close',headers=HEADERS).status_code == 200
        else:
            with store.connect() as conn:
                conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()-interval '61 minutes' WHERE id=%s",(session_id,))
            expire_sessions(store)
        assert not directory.exists()
        with store.connect() as conn:
            assert conn.execute('SELECT history FROM ka.agent_sessions WHERE id=%s',(session_id,)).fetchone()['history'] == history
        saved = client.get(f'/api/agents/{agent_id}/sessions/{session_id}',headers=HEADERS).json()
        assert saved['status'] == ('closed' if action == 'close' else 'expired') and len(saved['runs']) == 1
        assert client.post(path,headers=HEADERS,json={'task':'Follow-up','session_id':session_id}).status_code == 409
        assert client.post(f'/api/agents/code/sessions/{session_id}/close',headers=HEADERS).status_code == 404


def test_active_turn_survives_idle_deadline_and_rejects_concurrent_messages(direct, store, tmp_path, monkeypatch):
    client, agent_id, path = direct
    first = client.post(path,headers=HEADERS,json={'task':'First'})
    session_id = events(first)[0]['session_id']
    entered = threading.Event()
    async def slow(*args,**kwargs):
        entered.set()
        await asyncio.sleep(30)
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent',slow)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(client.post,path,headers=HEADERS,json={'task':'Running','session_id':session_id})
        assert entered.wait(10)
        with store.connect() as conn:
            conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()-interval '61 minutes' WHERE id=%s",(session_id,))
        expire_sessions(store)
        saved = client.get(f'/api/agents/{agent_id}/sessions/{session_id}',headers=HEADERS).json()
        assert saved['status'] == 'active'
        duplicate = client.post(path,headers=HEADERS,json={'task':'Concurrent','session_id':session_id})
        assert duplicate.status_code == 409 and 'running' in duplicate.text
        assert client.post(f'/api/agents/{agent_id}/sessions/{session_id}/close',headers=HEADERS).status_code == 200
        assert events(future.result(timeout=10))[-1]['status'] == 'cancelled'
    assert not (tmp_path/'state'/'agent-sessions'/session_id).exists()


def test_tuning_receives_full_configuration_conversation_and_feedback_without_applying(direct, store, monkeypatch):
    client, agent_id, path = direct
    first = client.post(path,headers=HEADERS,json={'task':'First task'})
    session_id = events(first)[0]['session_id']
    client.post(path,headers=HEADERS,json={'task':'Follow-up correction','session_id':session_id})
    before = client.get(f'/api/agents/{agent_id}/config',headers=HEADERS).json()
    calls = []
    async def review(config,task,**kwargs):
        calls.append((config,json.loads(task),kwargs))
        return SimpleNamespace(final_output=kwargs['output_type'].model_validate({'suggestion':'Return a concise summary.', 'changes': {'system_prompt':None,'instructions':'Be concise and explain uncertainty.','server_instructions':[]}}))
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent',review)
    response = client.post(f'/api/agents/{agent_id}/sessions/{session_id}/review',headers=HEADERS,json={'comment':'Be concise and explain uncertainty.','response_id':events(first)[0]['id']})
    assert response.status_code == 200,response.text
    config,context,kwargs = calls[0]
    assert config['id'] == 'agent-tuning'
    assert all(not config[cap]['enabled'] for cap in ('ssh','code','documents','skills','mcp','services'))
    assert set(kwargs['credentials']) == set()  # Cleared when the request finishes.
    assert context['agent_config'] == before
    assert [r['task'] for r in context['conversation']] == ['First task','Follow-up correction']
    assert all(r['context']['config']['id'] == agent_id for r in context['conversation'])
    assert context['comment'] == 'Be concise and explain uncertainty.'
    assert context['response_id'] == events(first)[0]['id']
    assert context['conversation'][0]['id'] == context['response_id']
    assert 'direct-test-secret' not in json.dumps(context)
    assert client.get(f'/api/agents/{agent_id}/config',headers=HEADERS).json() == before
    saved = client.get(f'/api/agents/{agent_id}/sessions/{session_id}',headers=HEADERS).json()
    assert saved['reviews'][0]['suggestion'] == response.json()['suggestion']
    assert saved['reviews'][0]['response_id'] == events(first)[0]['id']
    assert client.post(f'/api/agents/{agent_id}/sessions/{session_id}/review',headers=HEADERS,json={'comment':'Wrong response','response_id':str(uuid4())}).status_code == 404
    assert len(calls) == 1
    assert client.post(f'/api/agents/code/sessions/{session_id}/review',headers=HEADERS,json={'comment':'Wrong agent'}).status_code == 404
    assert client.post(f'/api/agents/{agent_id}/sessions/{session_id}/review',json={'comment':'Unauthenticated'}).status_code == 403


def test_another_api_instance_resumes_full_database_history_without_shared_disk(direct, responses_api, store, tmp_path):
    client, agent_id, path = direct
    first = client.post(path,headers=HEADERS,json={'task':'Earlier session message.'})
    session_id = events(first)[0]['session_id']
    with store.connect() as conn:
        history = conn.execute('SELECT history FROM ka.agent_sessions WHERE id=%s',(session_id,)).fetchone()['history']
    assert history[-1]['type'] == 'message' and isinstance(history[-1]['content'],list)
    other = _client(tmp_path/'another-worker',repository_store=store)
    result = other.post(path,headers=HEADERS,json={'task':'Continue that conversation.','session_id':session_id})
    assert events(result)[-1]['status']=='completed'
    request = json.dumps(responses_api[1][-1]['body']['input'])
    assert 'Earlier session message.' in request and 'Configured agent completed the task.' in request
    assert 'Continue that conversation.' in request
    assert responses_api[1][-1]['body']['input'][:-1] == history
    assert not (tmp_path/'state'/'agent-sessions').exists()
    assert not (tmp_path/'another-worker'/'state'/'agent-sessions').exists()


def test_live_and_test_sessions_share_creation_execution_and_filters(direct, store, responses_api):
    client, agent_id, run_path = direct
    path = f'/api/agents/{agent_id}/sessions'
    ids = []
    for is_test in (False,True):
        created = client.post(path,headers=HEADERS,json={'title':'Shared session','is_test':is_test})
        assert created.status_code == 201,created.text
        saved = created.json()
        ids.append(saved['id'])
        assert saved['is_test'] is is_test and saved['config']['id'] == agent_id
        assert saved['status'] == 'active' and saved['expires_at']
        assert 'history' not in saved and 'direct-test-secret' not in created.text
        profile = client.get(f'/api/agents/{agent_id}',headers=HEADERS).json()
        deletion = client.request('DELETE',f'/api/agents/{agent_id}',headers=HEADERS,
            json={'revision':profile['revision'],'confirmed':True})
        assert deletion.status_code == 409 and 'sessions' in deletion.text
        for message in ('Remember test word papaya.','What word did I give you?'):
            result = client.post(run_path,headers=HEADERS,json={'session_id':saved['id'],'task':message})
            assert events(result)[-1]['status'] == 'completed'
        wrong_mode = client.post(run_path,headers=HEADERS,json={'session_id':saved['id'],'task':'Wrong mode','is_test':not is_test})
        assert wrong_mode.status_code == 409
        listed = client.get(path,headers=HEADERS,params={'is_test':str(is_test).lower()}).json()['sessions']
        assert [s['id'] for s in listed] == [saved['id']]
        restored = client.get(path+'/'+saved['id'],headers=HEADERS).json()
        assert restored['is_test'] is is_test and len(restored['runs']) == 2
        assert 'history' not in restored
    assert len(client.get(path,headers=HEADERS).json()['sessions']) == 2
    with store.connect() as conn:
        histories = conn.execute('SELECT history FROM ka.agent_sessions WHERE id=ANY(%s)',(ids,)).fetchall()
        assert histories[0]['history'] == histories[1]['history']
        assert conn.execute("SELECT to_regclass('ka.agent_test_sessions') AS old").fetchone()['old'] is None
    assert client.post(path,json={}).status_code == 403
    assert client.post(path,headers=HEADERS,json={'id':ids[0]}).status_code == 409
    assert client.post(path,headers=HEADERS,json={'is_test':'false'}).status_code == 400


def test_failed_turn_does_not_change_committed_history(direct, store, monkeypatch):
    client, agent_id, path = direct
    session_id = events(client.post(path,headers=HEADERS,json={'task':'First','is_test':True}))[0]['session_id']
    with store.connect() as conn:
        previous = conn.execute('SELECT history FROM ka.agent_sessions WHERE id=%s',(session_id,)).fetchone()['history']
    async def fail(*args,**kwargs):
        raise ValueError('Provider unavailable.')
    monkeypatch.setattr('knowledge_admin.agent_sessions.agent',fail)
    result = client.post(path,headers=HEADERS,json={'task':'Failed turn','session_id':session_id})
    assert events(result)[-1]['status'] == 'failed'
    with store.connect() as conn:
        assert conn.execute('SELECT history FROM ka.agent_sessions WHERE id=%s',(session_id,)).fetchone()['history'] == previous
