"""Dedicated Agents SDK coordinator with Agents SDK specialists and PostgreSQL."""
import asyncio
from agents import OpenAIResponsesModel, set_trace_processors
from knowledge_admin.agent_tracing import instrument
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from knowledge_admin.agents import get_agent_config, load_credentials
from test_agents import saved_agent
from test_mcp import create as create_mcp, remote_mcp
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture(autouse=True)
def mounts(monkeypatch, tmp_path):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('RAG_SOURCE_ROOT', str(tmp_path.resolve()))


@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrument(provider)
    monkeypatch.setenv('PHOENIX_PUBLIC_URL', 'http://phoenix.test')
    yield exporter
    set_trace_processors([])
    provider.shutdown()


@pytest.fixture
def team_api():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'body':body,'authorization':self.headers.get('Authorization')})
            outputs = []
            for item in body['input']:
                if item.get('type') == 'function_call_output':
                    try:
                        outputs.append(json.loads(item['output']))
                    except (ValueError, TypeError):
                        outputs.append(item['output'])
            text = 'Findings from ' + body['model']
            assignments = []
            is_coordinator = body['model'] == 'coordinator-fixture'
            if is_coordinator:
                if 'Clarify first' in json.dumps(body['input']):
                    text = 'Which service should I inspect?'
                elif not outputs:
                    assignments = [('search', 'Find the runbook.'), ('operations', 'Inspect the service.')]
                    if 'Many specialists' in json.dumps(body['input']):
                        assignments = [('search', 'Part '+str(i)) for i in range(4)]
                    if 'Single specialist' in json.dumps(body['input']):
                        assignments = [('code','Inspect the source.')]
                elif len(outputs) == 2:
                    assignments = [('search', 'Cross-check both findings: ' + json.dumps(outputs))]
                else:
                    text = 'Combined findings: ' + json.dumps(outputs)
            output = [{'id': 'fc_'+uuid4().hex, 'type':'function_call','call_id':'call_'+uuid4().hex,
                       'name':'delegate_'+aid,'arguments':json.dumps({'input':task}),'status':'completed'}
                      for aid,task in assignments]
            if not is_coordinator and 'Read assigned files' in json.dumps(body['input']):
                if not outputs:
                    resources = json.loads(body['instructions'].split('Never display authentication secrets.\n\n')[1])
                    root = Path(resources['code_projects'][0]['folder_paths'][0])
                    other = root.parent / ('operations' if root.name == 'search' else 'search')
                    output = [{'id':'fc_'+uuid4().hex, 'type':'function_call', 'call_id':'call_'+uuid4().hex,
                        'name':'read_file', 'arguments':json.dumps({'path':str(folder/'evidence.txt'), 'offset':0, 'max_bytes':1024}),
                        'status':'completed'} for folder in (root, other)]
                else:
                    text = json.dumps(outputs)
            if not is_coordinator and 'Read MCP' in json.dumps(body['input']):
                if not outputs:
                    mcp_tool = next(t['name'] for t in body['tools'] if t['name'].endswith('read_info'))
                    output = [{'id':'fc_'+uuid4().hex, 'type':'function_call', 'call_id':'call_'+uuid4().hex,
                        'name':mcp_tool, 'arguments':'{}', 'status':'completed'}]
                else:
                    text = json.dumps(outputs)
            if not output:
                output = [{'id':'msg_'+uuid4().hex,'type':'message','role':'assistant','status':'completed',
                           'content':[{'type':'output_text','text':text,'annotations':[]}]}]
            response = {'id':'resp_'+uuid4().hex,'object':'response','created_at':1,'model':body['model'],
                'status':'completed','output':output,'parallel_tool_calls':True,'tool_choice':'auto','tools':[],
                'usage':{'input_tokens':20,'output_tokens':10,'total_tokens':30,
                         'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':0}}}
            events = [{'type':'response.created','response':{**response,'status':'in_progress','output':[]}}]
            events.extend({'type':'response.output_item.done','output_index':i,'item':item} for i,item in enumerate(output))
            events.append({'type':'response.completed','response':response})
            payload = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def log_message(self,*args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker = threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1',requests
    finally:
        server.shutdown();server.server_close();worker.join()


@pytest.fixture
def board_client(store,tmp_path,team_api):
    client = _client(tmp_path,repository_store=store)
    for name in ('code','search','operations','coordinator'):
        response = client.post('/api/llm/models',headers=HEADERS,json={'name':name+' model','provider':'openai',
            'model':name+'-fixture','api_format':'responses','base_url':team_api[0],'api_key':name+'-secret'})
        assert response.status_code == 201,response.text
        with store.connect() as conn:
            if name == 'coordinator':
                conn.execute('UPDATE ka.agents SET model_id=%s WHERE is_coordinator',(response.json()['id'],))
            else:
                conn.execute("""UPDATE ka.agents SET model_id=%s,documents_enabled=false,repositories_enabled=false,
                    ssh_enabled=false,mcp_enabled=false,skills_enabled=false,services_enabled=false WHERE id=%s""",(response.json()['id'],name))
    return client


def submit(client, task='Investigate the incident.', subagent_ids=None):
    response = client.post('/api/agent-board/tasks',headers=HEADERS,json={'id':str(uuid4()),'task':task,
        'subagent_ids':subagent_ids if subagent_ids is not None else ['search','operations']})
    assert response.status_code == 202,response.text
    return response.json()['id']


def wait_task(client, task_id, status, seconds=30):
    deadline = time.monotonic()+seconds
    while time.monotonic()<deadline:
        response = client.get(f'/api/agent-board/tasks/{task_id}',headers=HEADERS)
        assert response.status_code == 200,response.text
        task = response.json()
        if task['status'] == status:
            return task
        if task['status'] in ('completed','failed','cancelled'):
            pytest.fail(f"Task unexpectedly finished: {task['status']} / {task['error']}")
        time.sleep(.05)
    pytest.fail(f'Task did not reach {status}')


def test_real_sdk_coordinator_delegates_workers_and_streams_results(board_client,store,team_api,spans):
    with board_client as client:
        task_id = submit(client)
        stream = client.get(f'/api/agent-board/tasks/{task_id}/activity',headers=HEADERS)
        assert stream.status_code == 200 and 'event: done' in stream.text
        assert 'Delegate to ' in stream.text and 'coordinator-secret' not in stream.text
        task = wait_task(client,task_id,'completed')
        assert task['agent_id']=='coordinator' and task['agent_name']=='Coordinator'
        assert task['model_name']=='coordinator model' and task['usage']['requests']==4
        assert len(task['subtasks'])==3 and all(t['status']=='completed' for t in task['subtasks'])
        assert 'Findings from operations-fixture' in task['result'] and 'Findings from search-fixture' in task['result']
        assert all(t['trace_id']==task['trace_id'] and t['prompt_version']==1 for t in task['subtasks'])
        assert task['trace_url'].endswith(task['trace_id'])
        assert 'config' not in task
    requests = team_api[1]
    assert len(requests)==7
    coordinator_requests=[r['body'] for r in requests if r['body']['model']=='coordinator-fixture']
    assert [t['name'] for t in coordinator_requests[0]['tools']]==['delegate_operations', 'delegate_search']
    assert 'json_schema' not in json.dumps(coordinator_requests[0].get('text'))
    assert 'ssh_command' not in json.dumps(coordinator_requests)
    assert any('Cross-check' in json.dumps(r['body']) for r in requests if r['body']['model']=='search-fixture')
    with store.connect() as conn:
        config = conn.execute('SELECT config FROM ka.agent_board_tasks WHERE id=%s',(task_id,)).fetchone()['config']
        assert config['runtime']=='agents_sdk' and '-secret' not in json.dumps(config)
    recorded = spans.get_finished_spans()
    assert len([s for s in recorded if s.name=='Agent board task'])==1
    assert len([s for s in recorded if s.name=='Coordinator'])==1
    assert len([s for s in recorded if s.name=='Summary'])==1
    by_id = {s.context.span_id:s for s in recorded}
    for child in task['subtasks']:
        span = by_id[int(child['span_id'],16)]
        assert span.name == child['agent_name']
        assert by_id[span.parent.span_id].attributes['tool.name'].startswith('delegate_')
    assert '-secret' not in json.dumps([dict(s.attributes) for s in recorded])


def test_single_specialist_still_uses_coordinator_and_can_ask_clarification(board_client,team_api):
    with board_client as client:
        task = wait_task(client,submit(client,'Single specialist',['code']),'completed')
        assert task['agent_id']=='coordinator' and len(task['subtasks'])==1
        assert 'Findings from code-fixture' in task['result']
        question = wait_task(client,submit(client,'Clarify first',['code']),'completed')
        assert question['result']=='Which service should I inspect?' and question['subtasks']==[]
    assert any(r['body']['model']=='coordinator-fixture' for r in team_api[1])


def test_settings_validation_revision_snapshot_and_submission(board_client,store):
    client=board_client
    assert client.get('/api/agent-board/settings').status_code==403
    settings=client.get('/api/agent-board/settings',headers=HEADERS).json()
    value={key:settings[key] for key in ('revision','notes','summary_prompt','max_turns','max_subtasks','timeout_seconds')}
    for patch in ({'notes':'bad\x00note'},{'summary_prompt':' '},{'summary_prompt':'bad\x00prompt'},{'max_turns':0},{'max_subtasks':51},{'model_id':None}):
        assert client.put('/api/agent-board/settings',headers=HEADERS,json={**value,**patch}).status_code==400
    task_id=submit(client,subagent_ids=['code'])
    updated=client.put('/api/agent-board/settings',headers=HEADERS,json={**value,'notes':'New coordinator instructions.', 'summary_prompt':'Summarize with citations.'})
    assert updated.status_code==200 and updated.json()['revision']==2
    assert client.put('/api/agent-board/settings',headers=HEADERS,json=value).status_code==409
    with store.connect() as conn:
        original=conn.execute('SELECT config FROM ka.agent_board_tasks WHERE id=%s',(task_id,)).fetchone()['config']
        assert original['system_prompt']==settings['system_prompt'] and original['notes']==value['notes']
        assert original['summary_prompt']==settings['summary_prompt']
        conn.execute('UPDATE ka.llm_models SET enabled=false WHERE id=%s',(settings['model_id'],))
    assert client.post('/api/agent-board/tasks',headers=HEADERS,json={'id':str(uuid4()),'task':'No model.','subagent_ids':['code']}).status_code==400


def test_team_auth_idempotency_and_legacy_coordinator_input_rejected(board_client):
    client=board_client; body={'id':str(uuid4()),'task':'Once.','subagent_ids':['code']}
    assert client.post('/api/agent-board/tasks',json=body).status_code==403
    assert client.get('/api/agent-board/tasks').status_code==403
    assert client.get(f'/api/agent-board/tasks/{body["id"]}/activity').status_code==403
    for patch in ({'agent_id':'code'},{'subagent_ids':[]},{'subagent_ids':['code','code']},{'task':' '},{'subagent_ids':['coordinator']}):
        assert client.post('/api/agent-board/tasks',headers=HEADERS,json={**body,**patch}).status_code==400
    for _ in range(2):
        assert client.post('/api/agent-board/tasks',headers=HEADERS,json=body).status_code==202
    assert len(client.get('/api/agent-board/tasks',headers=HEADERS).json()['tasks'])==1
    assert client.post('/api/agent-board/tasks',headers=HEADERS,json={**body,'task':'Other'}).status_code==409
    assert client.post('/api/agent-board/tasks',headers=HEADERS,json={**body,'id':str(uuid4()),'subagent_ids':['missing']}).status_code==404
    assert client.request('DELETE','/api/agents/code',headers=HEADERS,json={'revision':1,'confirmed':True}).status_code==409


def test_coordinator_uses_active_prompt_and_notes_snapshot(board_client, store, team_api):
    client = board_client
    settings = client.get('/api/agent-board/settings', headers=HEADERS).json()
    value = {key: settings[key] for key in ('revision','notes','summary_prompt','max_turns','max_subtasks','timeout_seconds')}
    assert client.put('/api/agent-board/settings', headers=HEADERS,
        json={**value, 'notes':'Saved coordinator notes: cite the evidence.'}).status_code == 200
    draft = client.post('/api/llm/prompts/coordinator/versions', headers=HEADERS,
        json={'title':'Incident prompt', 'content':'Active incident coordinator instructions.'}).json()
    before = submit(client, 'Clarify first', ['code'])
    assert client.get('/api/agent-board/settings', headers=HEADERS).json()['system_prompt'] == settings['system_prompt']
    activated = client.post(f"/api/llm/prompts/coordinator/versions/{draft['id']}/activate", headers=HEADERS,
        json={'revision':draft['revision'], 'prompt_revision':settings['prompt_revision']})
    assert activated.status_code == 200, activated.text
    after = submit(client, 'Clarify first', ['code'])
    current = client.get('/api/agent-board/settings', headers=HEADERS).json()
    # Editing instructions after submission must not change either queued task.
    assert client.put('/api/agent-board/settings', headers=HEADERS,
        json={**value, 'revision':current['revision'], 'notes':'Later notes, only for new tasks.',
              'summary_prompt':'Later summary, only for new tasks.'}).status_code == 200
    with client:
        old = wait_task(client, before, 'completed')
        new = wait_task(client, after, 'completed')
    assert old['prompt_version'] == 1 and new['prompt_version'] == 2
    with store.connect() as conn:
        snapshots = conn.execute('SELECT config FROM ka.agent_board_tasks ORDER BY created_at').fetchall()
        assert snapshots[0]['config']['system_prompt'] == settings['system_prompt']
        assert snapshots[1]['config']['system_prompt'] == draft['content']
    calls = [json.dumps(r['body']) for r in team_api[1] if r['body']['model']=='coordinator-fixture' and r['body']['tools']]
    assert all('Saved coordinator notes: cite the evidence.' in call for call in calls)
    assert all('Later notes' not in call for call in calls)
    assert any(draft['content'] in call for call in calls)
    summaries = [r['body'] for r in team_api[1] if r['body']['model']=='coordinator-fixture' and not r['body']['tools']]
    assert len(summaries) == 2
    assert all(call['instructions'] == settings['summary_prompt'] for call in summaries)


def test_worker_failure_is_returned_to_coordinator_without_exposing_errors(board_client,monkeypatch,spans):
    original = OpenAIResponsesModel.stream_response
    async def stream(self, *args, **kwargs):
        if self.model == 'operations-fixture':
            raise RuntimeError('private-provider-error')
        async for event in original(self, *args, **kwargs):
            yield event
    monkeypatch.setattr(OpenAIResponsesModel, 'stream_response', stream)
    with board_client as client:
        task=wait_task(client,submit(client),'completed')
        assert 'failed' in task['result'] and 'private-provider-error' not in json.dumps(task)
        assert any(t['status']=='failed' for t in task['subtasks'])
        assert any(t['status']=='completed' for t in task['subtasks'])
    assert 'private-provider-error' not in json.dumps([
        {'attributes':dict(s.attributes), 'error':s.status.description} for s in spans.get_finished_spans()])


def test_native_coordinator_cancellation_stops_active_and_queued_specialists(board_client,store,monkeypatch,spans):
    entered=[]; stopped=[]
    original = OpenAIResponsesModel.stream_response
    async def stream(self, *args, **kwargs):
        if self.model == 'search-fixture':
            entered.append(kwargs.get('input'))
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(kwargs.get('input'))
        else:
            async for event in original(self, *args, **kwargs):
                yield event
    monkeypatch.setattr(OpenAIResponsesModel, 'stream_response', stream)
    with board_client as client:
        task_id=submit(client,'Many specialists')
        deadline=time.monotonic()+10
        while len(entered)<3 and time.monotonic()<deadline:
            time.sleep(.05)
        assert len(entered)==3
        assert client.post(f'/api/agent-board/tasks/{task_id}/cancel',headers=HEADERS).status_code==200
        task=wait_task(client,task_id,'cancelled')
        assert len(task['subtasks'])==4 and all(t['status']=='cancelled' for t in task['subtasks'])
    assert len(stopped)==3
    assert any(s.name == 'Agent board task' for s in spans.get_finished_spans())
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.agent_board_tasks WHERE status IN ('queued','running')").fetchone()['n']==0


def test_queued_cancel_and_restart_never_repeat_interrupted_children(board_client, store, team_api):
    task_id = submit(board_client)
    cancelled = submit(board_client)
    assert board_client.post(f'/api/agent-board/tasks/{cancelled}/cancel', headers=HEADERS).status_code == 200
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_board_tasks SET status='running',started_at=now() WHERE id=%s", (task_id,))
        conn.execute('''INSERT INTO ka.agent_board_tasks(id,agent_id,agent_name,task,parent_id,created_by)
            SELECT %s,'search','Search','Interrupted subtask',id,created_by FROM ka.agent_board_tasks WHERE id=%s''', (uuid4(), task_id))
    with board_client as client:
        task = wait_task(client, task_id, 'failed')
        assert 'interrupted' in task['error']
        assert task['subtasks'][0]['status'] == 'cancelled'
        assert wait_task(client, cancelled, 'cancelled')['finished_at']
    assert team_api[1] == []


def test_enabled_secret_selection_and_server_overrides(store, saved_agent):
    config = get_agent_config('operations', conninfo=store.conninfo)
    values = load_credentials(store, config)
    assert set(values.values()) == {'test-model-secret', 'test-mcp-secret', saved_agent[2]['private_key']}
    config['ssh']['enabled'] = False
    config['mcp']['enabled'] = False
    assert set(load_credentials(store, config).values()) == {'test-model-secret'}


def test_parallel_delegations_respect_total_subtask_limit(board_client, team_api):
    settings = board_client.get('/api/agent-board/settings', headers=HEADERS).json()
    value = {key:settings[key] for key in ('revision','notes','summary_prompt','max_turns','max_subtasks','timeout_seconds')}
    assert board_client.put('/api/agent-board/settings', headers=HEADERS,
        json={**value, 'max_subtasks':1}).status_code == 200
    with board_client as client:
        task = wait_task(client, submit(client, 'Many specialists'), 'completed')
        assert len(task['subtasks']) == 1
        assert 'rejected' in task['result']
    assert len([r for r in team_api[1] if r['body']['model']=='search-fixture']) == 1


def test_parallel_specialists_keep_their_own_files_credentials_and_activity(board_client, store, team_api, tmp_path, monkeypatch, spans):
    original = get_agent_config
    for name in ('search', 'operations'):
        folder = tmp_path / name
        folder.mkdir()
        (folder / 'evidence.txt').write_text(name + '-evidence ' + name + '-secret')

    def configured(agent_id, **kwargs):
        config = original(agent_id, **kwargs)
        config['code'] = {'enabled':True, 'projects':[{'name':agent_id, 'folder_paths':[str((tmp_path/agent_id).resolve())]}]}
        return config

    monkeypatch.setattr('knowledge_admin.board.get_agent_config', configured)
    with board_client as client:
        task_id = submit(client, 'Read assigned files')
        stream = client.get(f'/api/agent-board/tasks/{task_id}/activity', headers=HEADERS)
        task = wait_task(client, task_id, 'completed')
        assert all(t['status']=='completed' for t in task['subtasks'])
        for child in task['subtasks']:
            assert child['agent_id'] + '-evidence' in child['result']
            other = 'operations' if child['agent_id']=='search' else 'search'
            assert other + '-evidence' not in child['result']
            assert 'outside the agent' in child['result']
            assert child['usage']['requests'] == 2
        assert 'read_file' in stream.text
        assert '-secret' not in stream.text and '-secret' not in json.dumps(task)
        assert task['usage']['requests'] == 4
    for request in team_api[1]:
        model = request['body']['model'].removesuffix('-fixture')
        assert request['authorization'] == 'Bearer ' + model + '-secret'
        assert '-secret' not in json.dumps(request['body'])
    recorded = spans.get_finished_spans()
    by_id = {s.context.span_id:s for s in recorded}
    for span in recorded:
        if span.attributes.get('tool.name') == 'read_file':
            assert by_id[span.parent.span_id].name in [child['agent_name'] for child in task['subtasks']]


def test_native_specialists_use_authenticated_mcp_connections(board_client, store, remote_mcp):
    server = create_mcp(board_client, remote_mcp[0])
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET mcp_enabled=true WHERE id IN ('search','operations')")
    for name in ('search','operations'):
        config = board_client.get('/api/mcp/code?agent_id='+name, headers=HEADERS).json()
        response = board_client.put('/api/mcp/code?agent_id='+name, headers=HEADERS,
            json={**config, 'mcp_server_ids':[server['id']]})
        assert response.status_code == 200, response.text
    with board_client as client:
        task_id = submit(client, 'Read MCP')
        stream = client.get(f'/api/agent-board/tasks/{task_id}/activity', headers=HEADERS)
        task = wait_task(client, task_id, 'completed')
        assert len(task['subtasks']) == 3
        assert all(t['status']=='completed' and 'isolated-mcp-evidence' in t['result'] for t in task['subtasks'])
        assert 'read_info' in stream.text
        assert 'isolated-token' not in stream.text and 'isolated-token' not in json.dumps(task)
    assert remote_mcp[1] == ['read_info'] * 3
