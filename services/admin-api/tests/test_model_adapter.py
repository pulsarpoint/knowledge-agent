"""Agents SDK model connections: HTTP protocols, native providers and tool loops."""

import asyncio
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import litellm
import httpx
import pytest
from pydantic import BaseModel

from knowledge_admin.agents.runner import agent
from test_sdk_agent import config, responses_api


def test_responses_reasoning_and_cached_usage_survive_tool_cycle(config, responses_api):
    config['model']['reasoning_effort'] = 'max'
    result = asyncio.run(
        agent(config, 'RESPONSES_REASONING_CYCLE', credentials={'model-key': 'fixture-secret'}, stream=False)
    )
    requests = responses_api[1]
    assert len(requests) == 2
    for request in requests:
        assert request['body']['reasoning'] == {'effort': 'max'}
        assert request['body']['store'] is False
        assert request['body'].get('previous_response_id') is None
    history = requests[1]['body']['input']
    reasoning = next(item for item in history if item.get('type') == 'reasoning')
    assert reasoning['content'] == [{'type': 'reasoning_text', 'text': 'Synthetic fixture reasoning.'}]
    tool_result = next(item for item in history if item.get('type') == 'function_call_output')
    assert tool_result['call_id'] == 'files-call'
    assert config['code']['projects'][0]['folder_paths'][0] in tool_result['output']
    usage = result.context_wrapper.usage
    assert (
        usage.input_tokens,
        usage.input_tokens_details.cached_tokens,
        usage.output_tokens,
        usage.output_tokens_details.reasoning_tokens,
    ) == (200, 160, 40, 24)


@pytest.fixture
def chat_api():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'body': body, 'auth': self.headers.get('Authorization'), 'path': self.path})
            message = {'role': 'assistant', 'content': 'Adapter completed the task.'}
            if body.get('response_format'):
                message['content'] = json.dumps(
                    {'agent_id': 'f4053eaa-48d6-4314-8927-ac9ac13e8939', 'task': 'Compute 19 + 23.\nReturn the result.'}
                )
            finish_reason = 'stop'
            messages = json.dumps(body['messages'])
            arguments = '{}'
            if ('ADAPTER_TOOL_CYCLE' in messages or 'ADAPTER_FILE_CYCLE' in messages) and not any(
                message['role'] == 'tool' for message in body['messages']
            ):
                tool_name = 'lookup' if 'ADAPTER_TOOL_CYCLE' in messages else 'read_file'
                tool = next(tool['function']['name'] for tool in body['tools'] if tool_name in tool['function']['name'])
                if tool_name == 'read_file':
                    import re

                    path = re.search(r'/[^\" ]+/source', messages).group()
                    arguments = json.dumps({'path': path + '/README.md'})
                message = {
                    'role': 'assistant',
                    'tool_calls': [
                        {
                            'index': 0,
                            'id': 'call_fixture',
                            'type': 'function',
                            'function': {'name': tool, 'arguments': arguments[:1]},
                        }
                    ],
                }
                finish_reason = 'tool_calls'
            if body.get('stream'):
                chunks = [
                    {
                        'id': 'chat_fixture',
                        'object': 'chat.completion.chunk',
                        'created': 1,
                        'model': body['model'],
                        'choices': [{'index': 0, 'delta': message, 'finish_reason': None}],
                    },
                    {
                        'id': 'chat_fixture',
                        'object': 'chat.completion.chunk',
                        'created': 1,
                        'model': body['model'],
                        'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish_reason}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
                    },
                ]
                if not body.get('stream_options', {}).get('include_usage'):
                    chunks[-1].pop('usage')
                if finish_reason == 'tool_calls':
                    chunks.insert(
                        1,
                        {
                            'id': 'chat_fixture',
                            'object': 'chat.completion.chunk',
                            'created': 1,
                            'model': body['model'],
                            'choices': [
                                {
                                    'index': 0,
                                    'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': arguments[1:]}}]},
                                    'finish_reason': None,
                                }
                            ],
                        },
                    )
                payload = (
                    ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
                ).encode()
                content_type = 'text/event-stream'
            else:
                payload = json.dumps(
                    {
                        'id': 'chat_fixture',
                        'object': 'chat.completion',
                        'created': 1,
                        'model': body['model'],
                        'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
                    }
                ).encode()
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
    result = asyncio.run(agent(config, 'Explain the selected code.', credentials={'model-key': 'chat-secret'}))
    assert result.final_output == 'Adapter completed the task.'
    assert len(requests) == 1 and requests[0]['path'] == '/v1/chat/completions'
    assert requests[0]['auth'] == 'Bearer chat-secret'
    assert 'Use the active saved system prompt.' in json.dumps(requests[0]['body'])
    assert 'chat-secret' not in json.dumps(requests[0]['body'])
    assert result.context_wrapper.usage.total_tokens == 15


@pytest.mark.parametrize('stream', [True, False])
def test_structured_decision(config, chat_api, stream):
    class Assignment(BaseModel):
        agent_id: str
        task: str

    config['model'].update(api_format='chat_completions', base_url=chat_api[0])
    result = asyncio.run(
        agent(
            config,
            'Return an assignment.',
            credentials={'model-key': 'chat-secret'},
            output_type=Assignment,
            stream=stream,
            max_output_tokens=16384,
        )
    )
    assert isinstance(result.final_output, Assignment)
    assert result.final_output.task == 'Compute 19 + 23.\nReturn the result.'
    assert chat_api[1][0]['body']['response_format']['json_schema']['schema'] == Assignment.model_json_schema()
    assert chat_api[1][0]['body'].get('stream', False) is stream
    assert chat_api[1][0]['body']['max_tokens'] == 16384


def test_sdk_executes_mcp_call_and_returns_matching_result(config, chat_api, responses_api):
    config['model'].update(api_format='chat_completions', base_url=chat_api[0])
    config['mcp'] = {
        'enabled': True,
        'servers': [
            {
                'id': 'fixture-mcp',
                'name': 'Fixture',
                'description': 'Lookup',
                'enabled': True,
                'url': responses_api[0].removesuffix('/v1') + '/mcp',
                'bearer_token_ref': None,
            }
        ],
    }
    public = []

    async def capture(item):
        public.append(item)

    result = asyncio.run(
        agent(config, 'ADAPTER_TOOL_CYCLE: call lookup.', credentials={'model-key': 'key'}, on_activity=capture)
    )
    assert result.final_output == 'Adapter completed the task.'
    tool_result = next(m for m in chat_api[1][1]['body']['messages'] if m['role'] == 'tool')
    assert tool_result['tool_call_id'] == 'call_fixture' and 'MCP_FIXTURE_RESULT' in json.dumps(tool_result)
    assert any(item['status'] == 'inProgress' and 'lookup' in item['title'] for item in public)
    assert any(item['status'] == 'completed' and 'MCP_FIXTURE_RESULT' in item['detail'] for item in public)
    assert result.context_wrapper.usage.requests == 2
    assert result.context_wrapper.usage.input_tokens == 20
    assert result.context_wrapper.usage.output_tokens == 10
    assert result.context_wrapper.usage.total_tokens == 30


def test_sdk_executes_file_tool_and_preserves_tool_history(config, chat_api, tmp_path):
    config['model'].update(api_format='chat_completions', base_url=chat_api[0])
    folder = config['code']['projects'][0]['folder_paths'][0]  # the fixture model reads it from the message
    result = asyncio.run(
        agent(config, f'ADAPTER_FILE_CYCLE: read the README in {folder}', credentials={'model-key': 'key'})
    )
    assert result.final_output == 'Adapter completed the task.'
    messages = chat_api[1][1]['body']['messages']
    assert any(m['role'] == 'tool' and 'Source fixture.' in m['content'] for m in messages)
    asyncio.run(
        agent(config, 'Explain the file you read.', credentials={'model-key': 'key'}, history=result.to_input_list())
    )
    assert any(m['role'] == 'tool' and 'Source fixture.' in m['content'] for m in chat_api[1][-1]['body']['messages'])
    assert any(tool['function']['name'] == 'shell_run' for tool in chat_api[1][0]['body']['tools'])


@pytest.mark.parametrize('provider', ['bedrock', 'anthropic'])
@pytest.mark.parametrize('stream', [True, False])
def test_native_model_routing(config, monkeypatch, provider, stream):
    captured = []
    real = litellm.acompletion

    async def completion(**kwargs):
        captured.append(copy.deepcopy(kwargs))
        return await real(**kwargs, mock_response='Native fixture.')

    monkeypatch.setattr('agents.extensions.models.litellm_model.litellm.acompletion', completion)
    config['model'].update(
        provider=provider,
        api_format='native',
        model='eu.anthropic.claude-opus-5',
        aws_region='eu-central-1',
        base_url=None if provider == 'bedrock' else 'https://api.anthropic.com',
    )
    recorded = []

    async def record(usage):
        recorded.append(usage)

    result = asyncio.run(
        agent(config, 'Inspect.', credentials={'model-key': 'native-secret'}, stream=stream, on_usage=record)
    )
    assert result.final_output == 'Native fixture.'
    call = captured[0]
    assert call['api_key'] == 'native-secret'
    assert call['max_tokens'] == litellm.model_cost[config['model']['model']]['max_output_tokens']
    assert call['max_tokens'] > 4096
    if stream:
        assert call['stream_options'] == {'include_usage': True}
    assert result.context_wrapper.usage.total_tokens > 0
    assert len(recorded) == 1
    assert recorded[0].total_tokens == result.context_wrapper.usage.total_tokens
    assert call['model'] == ('bedrock/invoke/' if provider == 'bedrock' else 'anthropic/') + config['model']['model']
    if provider == 'bedrock':
        assert call['aws_region_name'] == 'eu-central-1'
    else:
        assert call['base_url'] == 'https://api.anthropic.com'


def test_concurrent_runs_keep_credentials_separate(config, chat_api):
    config['model'].update(api_format='chat_completions', base_url=chat_api[0])
    second = copy.deepcopy(config)
    second['model']['model'] = 'second-model'

    async def run():
        return await asyncio.gather(
            agent(config, 'Hello.', credentials={'model-key': 'first-key'}),
            agent(second, 'Hello.', credentials={'model-key': 'second-key'}),
        )

    assert len(asyncio.run(run())) == 2
    assert {(r['body']['model'], r['auth']) for r in chat_api[1]} == {
        ('fixture-model', 'Bearer first-key'),
        ('second-model', 'Bearer second-key'),
    }


def test_bedrock_wire_caching_reasoning_replay_and_natural_completion(config, monkeypatch):
    """Exercise the SDK and LiteLLM converters, replacing only Bedrock's HTTP transport."""
    requests = []
    source = config['code']['projects'][0]['folder_paths'][0]
    config['model'].update(
        provider='bedrock',
        api_format='native',
        model='eu.anthropic.claude-opus-5',
        aws_region='eu-central-1',
        base_url=None,
    )

    async def post(self, url, **kwargs):
        assert 'bedrock-runtime.eu-central-1.amazonaws.com' in str(url)
        body = json.loads(kwargs['data'])
        requests.append(body)
        more = len(requests) <= 41
        content = (
            [
                {'type': 'thinking', 'thinking': 'Synthetic fixture reasoning.', 'signature': 'fixture-signature'},
                {
                    'type': 'tool_use',
                    'id': f'fixture-{len(requests)}',
                    'name': 'read_file',
                    'input': {'path': source + '/README.md'},
                },
            ]
            if more
            else [{'type': 'text', 'text': 'Inspection completed naturally.'}]
        )
        return httpx.Response(
            200,
            request=httpx.Request('POST', str(url)),
            json={
                'id': f'msg-{len(requests)}',
                'type': 'message',
                'role': 'assistant',
                'model': config['model']['model'],
                'content': content,
                'stop_reason': 'tool_use' if more else 'end_turn',
                'stop_sequence': None,
                'usage': {
                    'input_tokens': 100,
                    'output_tokens': 10,
                    'cache_read_input_tokens': 60,
                    'cache_creation_input_tokens': 20,
                },
            },
        )

    monkeypatch.setattr('litellm.llms.custom_httpx.http_handler.AsyncHTTPHandler.post', post)
    result = asyncio.run(
        agent(config, 'Inspect the README.', credentials={'model-key': 'fixture-bearer'}, stream=False, max_turns=None)
    )
    assert result.final_output == 'Inspection completed naturally.' and len(requests) == 42
    for body in requests:
        assert body['thinking']['type'] == 'adaptive'
        assert body['output_config']['effort'] == 'xhigh'
        assert body['system'][-1]['cache_control'] == {'type': 'ephemeral'}
        assert body['messages'][-1]['content'][-1]['cache_control'] == {'type': 'ephemeral'}
    assistant = next(message for message in requests[1]['messages'] if message['role'] == 'assistant')
    assert assistant['content'][0] == {
        'type': 'thinking',
        'thinking': 'Synthetic fixture reasoning.',
        'signature': 'fixture-signature',
    }
    assert 'Source fixture.' in json.dumps(requests[1]['messages'][-1])
    usage = result.context_wrapper.usage
    assert usage.input_tokens == 42 * 180 and usage.output_tokens == 42 * 10
    assert usage.input_tokens_details.cached_tokens == 42 * 60
    assert usage.input_tokens_details.cache_write_tokens == 42 * 20


def test_bedrock_structured_output_keeps_adaptive_thinking(config, monkeypatch):
    from pydantic import BaseModel

    class Answer(BaseModel):
        answer: str

    config['model'].update(
        provider='bedrock',
        api_format='native',
        model='eu.anthropic.claude-opus-5',
        aws_region='eu-central-1',
        base_url=None,
    )

    async def post(self, url, **kwargs):
        body = json.loads(kwargs['data'])
        assert body['thinking'] == {'type': 'adaptive'}
        assert body['output_config']['effort'] == 'xhigh'
        return httpx.Response(
            200,
            request=httpx.Request('POST', str(url)),
            json={
                'id': 'structured',
                'type': 'message',
                'role': 'assistant',
                'model': config['model']['model'],
                'content': [{'type': 'text', 'text': '{"answer":"42"}'}],
                'stop_reason': 'end_turn',
                'usage': {'input_tokens': 100, 'output_tokens': 10},
            },
        )

    monkeypatch.setattr('litellm.llms.custom_httpx.http_handler.AsyncHTTPHandler.post', post)
    result = asyncio.run(
        agent(config, 'Answer with a number.', credentials={'model-key': 'fixture'}, stream=False, output_type=Answer)
    )
    assert result.final_output.answer == '42'


@pytest.mark.parametrize('cancel', [False, True])
def test_timeout_and_cancel_clean_provider_and_preserve_history(config, monkeypatch, tmp_path, cancel):
    config['model'].update(provider='bedrock', api_format='native', aws_region='eu-central-1')
    history = [{'role': 'user', 'content': 'Earlier message.'}, {'role': 'assistant', 'content': 'Earlier answer.'}]
    original_history = copy.deepcopy(history)

    async def run():
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def slow(**kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        monkeypatch.setattr('agents.extensions.models.litellm_model.litellm.acompletion', slow)
        pending = asyncio.create_task(
            agent(
                config,
                'Wait.',
                credentials={'model-key': 'key'},
                timeout_seconds=2,
                turn_timeout_seconds=2,
                history=history,
            )
        )
        await started.wait()
        if cancel:
            pending.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await pending
        assert stopped.is_set()

    asyncio.run(run())
    assert history == original_history


def test_native_connection_without_saved_key_does_not_use_environment(config, monkeypatch):
    config['model'].update(provider='bedrock', api_format='native', aws_region='eu-central-1', api_key_ref=None)
    monkeypatch.setenv('AWS_BEARER_TOKEN_BEDROCK', 'unrelated-key')
    with pytest.raises(ValueError, match='saved API key'):
        asyncio.run(agent(config, 'Inspect.', credentials={}))
