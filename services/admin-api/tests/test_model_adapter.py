"""Agents SDK model connections: HTTP protocols, native providers and tool loops."""
import asyncio
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import litellm
import pytest
from pydantic import BaseModel

from knowledge_admin.agents import agent
from test_sdk_agent import config, responses_api


@pytest.fixture
def chat_api():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'body': body, 'auth': self.headers.get('Authorization'), 'path': self.path})
            message = {'role': 'assistant', 'content': 'Adapter completed the task.'}
            if body.get('response_format'):
                message['content'] = json.dumps({'agent_id': 'f4053eaa-48d6-4314-8927-ac9ac13e8939', 'task': 'Compute 19 + 23.\nReturn the result.'})
            finish_reason = 'stop'
            messages = json.dumps(body['messages'])
            arguments = '{}'
            if ('ADAPTER_TOOL_CYCLE' in messages or 'ADAPTER_FILE_CYCLE' in messages) and not any(message['role'] == 'tool' for message in body['messages']):
                tool_name = 'lookup' if 'ADAPTER_TOOL_CYCLE' in messages else 'read_file'
                tool = next(tool['function']['name'] for tool in body['tools'] if tool_name in tool['function']['name'])
                if tool_name == 'read_file':
                    import re
                    path = re.search(r'/[^\" ]+/source', messages).group()
                    arguments = json.dumps({'path': path + '/README.md'})
                message = {'role': 'assistant', 'tool_calls': [
                    {'index': 0, 'id': 'call_fixture', 'type': 'function', 'function': {'name': tool, 'arguments': arguments[:1]}}]}
                finish_reason = 'tool_calls'
            if body.get('stream'):
                chunks = [
                    {'id': 'chat_fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': body['model'],
                     'choices': [{'index': 0, 'delta': message, 'finish_reason': None}]},
                    {'id': 'chat_fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': body['model'],
                     'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish_reason}],
                     'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}},
                ]
                if not body.get('stream_options',{}).get('include_usage'):
                    chunks[-1].pop('usage')
                if finish_reason == 'tool_calls':
                    chunks.insert(1, {'id': 'chat_fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': body['model'],
                        'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': arguments[1:]}}]}, 'finish_reason': None}]})
                payload = (''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n').encode()
                content_type = 'text/event-stream'
            else:
                payload = json.dumps({'id': 'chat_fixture', 'object': 'chat.completion', 'created': 1, 'model': body['model'],
                    'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
                content_type = 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join()



def test_direct_chat_connection(config, chat_api):
    url, requests = chat_api
    config['model'].update(api_format='chat_completions', base_url=url)
    result = asyncio.run(agent(config, 'Explain the selected code.', credentials={'model-key':'chat-secret'}))
    assert result.final_output == 'Adapter completed the task.'
    assert len(requests) == 1 and requests[0]['path'] == '/v1/chat/completions'
    assert requests[0]['auth'] == 'Bearer chat-secret'
    assert 'Local source' in json.dumps(requests[0]['body'])
    assert 'chat-secret' not in json.dumps(requests[0]['body'])
    assert result.context_wrapper.usage.total_tokens == 15


@pytest.mark.parametrize('stream', [True, False])
def test_structured_decision(config, chat_api, stream):
    class Assignment(BaseModel):
        agent_id: str
        task: str
    config['model'].update(api_format='chat_completions', base_url=chat_api[0])
    result = asyncio.run(agent(config, 'Return an assignment.', credentials={'model-key':'chat-secret'}, output_type=Assignment,
                              stream=stream, max_output_tokens=16384))
    assert isinstance(result.final_output, Assignment)
    assert result.final_output.task == 'Compute 19 + 23.\nReturn the result.'
    assert chat_api[1][0]['body']['response_format']['json_schema']['schema'] == Assignment.model_json_schema()
    assert chat_api[1][0]['body'].get('stream', False) is stream
    assert chat_api[1][0]['body']['max_tokens'] == 16384


def test_sdk_executes_mcp_call_and_returns_matching_result(config, chat_api, responses_api):
    config['model'].update(api_format='chat_completions', base_url=chat_api[0])
    config['mcp'] = {'enabled':True, 'servers':[{'id':'fixture-mcp', 'name':'Fixture', 'description':'Lookup',
        'enabled':True, 'url':responses_api[0].removesuffix('/v1')+'/mcp', 'bearer_token_ref':None}]}
    public = []
    async def capture(item):
        public.append(item)
    result = asyncio.run(agent(config, 'ADAPTER_TOOL_CYCLE: call lookup.', credentials={'model-key':'key'}, on_activity=capture))
    assert result.final_output == 'Adapter completed the task.'
    tool_result = next(m for m in chat_api[1][1]['body']['messages'] if m['role']=='tool')
    assert tool_result['tool_call_id']=='call_fixture' and 'MCP_FIXTURE_RESULT' in json.dumps(tool_result)
    assert any(item['status']=='inProgress' and 'lookup' in item['title'] for item in public)
    assert any(item['status']=='completed' and 'MCP_FIXTURE_RESULT' in item['detail'] for item in public)
    assert result.context_wrapper.usage.requests == 2
    assert result.context_wrapper.usage.input_tokens == 20
    assert result.context_wrapper.usage.output_tokens == 10
    assert result.context_wrapper.usage.total_tokens == 30


def test_sdk_executes_file_tool_and_preserves_tool_history(config, chat_api, tmp_path):
    config['model'].update(api_format='chat_completions', base_url=chat_api[0])
    result=asyncio.run(agent(config,'ADAPTER_FILE_CYCLE: read the README.',credentials={'model-key':'key'}))
    assert result.final_output=='Adapter completed the task.'
    messages=chat_api[1][1]['body']['messages']
    assert any(m['role']=='tool' and 'Source fixture.' in m['content'] for m in messages)
    asyncio.run(agent(config,'Explain the file you read.',credentials={'model-key':'key'},history=result.to_input_list()))
    assert any(m['role']=='tool' and 'Source fixture.' in m['content'] for m in chat_api[1][-1]['body']['messages'])
    assert not any('shell' in tool['function']['name'] for tool in chat_api[1][0]['body']['tools'])


@pytest.mark.parametrize('provider', ['bedrock','anthropic'])
def test_native_model_routing(config, monkeypatch, provider):
    captured=[]
    real=litellm.acompletion
    async def completion(**kwargs):
        captured.append(copy.deepcopy(kwargs))
        return await real(**kwargs,mock_response='Native fixture.')
    monkeypatch.setattr('agents.extensions.models.litellm_model.litellm.acompletion',completion)
    config['model'].update(provider=provider,api_format='native',model='eu.anthropic.claude-opus-5',
        aws_region='eu-central-1',base_url=None if provider=='bedrock' else 'https://api.anthropic.com')
    result=asyncio.run(agent(config,'Inspect.',credentials={'model-key':'native-secret'}))
    assert result.final_output=='Native fixture.'
    call=captured[0]
    assert call['api_key']=='native-secret'
    assert call['stream_options']=={'include_usage':True}
    assert result.context_wrapper.usage.total_tokens > 0
    assert call['model']==('bedrock/invoke/' if provider=='bedrock' else 'anthropic/')+config['model']['model']
    if provider=='bedrock':
        assert call['aws_region_name']=='eu-central-1'
    else:
        assert call['base_url']=='https://api.anthropic.com'


def test_concurrent_runs_keep_credentials_separate(config, chat_api):
    config['model'].update(api_format='chat_completions',base_url=chat_api[0])
    second=copy.deepcopy(config)
    second['model']['model']='second-model'
    async def run():
        return await asyncio.gather(agent(config,'Hello.',credentials={'model-key':'first-key'}),
            agent(second,'Hello.',credentials={'model-key':'second-key'}))
    assert len(asyncio.run(run()))==2
    assert {(r['body']['model'],r['auth']) for r in chat_api[1]}=={('fixture-model','Bearer first-key'),('second-model','Bearer second-key')}


@pytest.mark.parametrize('cancel', [False,True])
def test_timeout_and_cancel_clean_provider_and_preserve_history(config, monkeypatch, tmp_path, cancel):
    config['model'].update(provider='bedrock',api_format='native',aws_region='eu-central-1')
    history = [{'role':'user','content':'Earlier message.'},{'role':'assistant','content':'Earlier answer.'}]
    original_history = copy.deepcopy(history)
    async def run():
        started=asyncio.Event();stopped=asyncio.Event()
        async def slow(**kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        monkeypatch.setattr('agents.extensions.models.litellm_model.litellm.acompletion',slow)
        pending=asyncio.create_task(agent(config,'Wait.',credentials={'model-key':'key'},timeout_seconds=2,history=history))
        await started.wait()
        if cancel:
            pending.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await pending
        assert stopped.is_set()
    asyncio.run(run())
    assert history == original_history


def test_native_connection_without_saved_key_does_not_use_environment(config, monkeypatch):
    config['model'].update(provider='bedrock',api_format='native',aws_region='eu-central-1',api_key_ref=None)
    monkeypatch.setenv('AWS_BEARER_TOKEN_BEDROCK','unrelated-key')
    with pytest.raises(ValueError,match='saved API key'):
        asyncio.run(agent(config,'Inspect.',credentials={}))
