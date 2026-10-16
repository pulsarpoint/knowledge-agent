"""Exercise the official SDK against an isolated Responses API fixture."""

import asyncio
import copy
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import asyncssh
import pytest
from agents import Runner
from implied_tools import implied_tools
from knowledge_admin.agents.runner import agent
from test_ssh import ssh_server


@pytest.fixture
def responses_api():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'path': self.path, 'body': body, 'authorization': self.headers.get('Authorization')})
            if self.path == '/mcp':
                if 'id' not in body:
                    self.send_response(202)
                    self.end_headers()
                    return
                if body['method'] == 'tools/call':
                    result = {'content': [{'type': 'text', 'text': 'MCP_FIXTURE_RESULT'}], 'isError': False}
                elif body['method'] == 'initialize':
                    result = {
                        'protocolVersion': body['params']['protocolVersion'],
                        'capabilities': {'tools': {}},
                        'serverInfo': {'name': 'Fixture MCP', 'version': '1'},
                    }
                else:
                    result = {
                        'tools': [
                            {
                                'name': 'lookup',
                                'description': 'Look up fixture documentation.',
                                'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False},
                                'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                            }
                        ]
                    }
                payload = json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': result}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            message = {
                'id': 'msg_fixture',
                'type': 'message',
                'role': 'assistant',
                'status': 'completed',
                'content': [{'type': 'output_text', 'text': 'Configured agent completed the task.', 'annotations': []}],
            }
            output = [message]
            if 'SSH_TOOL_CYCLE' in json.dumps(body['input']) and not any(
                item.get('type') == 'function_call_output' for item in body['input']
            ):
                output = [
                    {
                        'id': 'fc_ssh',
                        'type': 'function_call',
                        'call_id': 'ssh-call',
                        'name': 'ssh_run',
                        'arguments': json.dumps(
                            {'server_id': 'host', 'command': 'id -un', 'as_root': False, 'timeout_seconds': 5}
                        ),
                        'status': 'completed',
                    }
                ]
            if 'SHELL_TOOL_CYCLE' in json.dumps(body['input']) and not any(
                item.get('type') == 'function_call_output' for item in body['input']
            ):
                output = [
                    {
                        'id': 'fc_shell',
                        'type': 'function_call',
                        'call_id': 'shell-call',
                        'name': 'shell_run',
                        'arguments': json.dumps(
                            {'command': 'python -c "print(\'SHELL_FIXTURE_RESULT\')"', 'timeout_seconds': 5}
                        ),
                        'status': 'completed',
                    }
                ]
            if 'FILES_TOOL_CYCLE' in json.dumps(body['input']) and not any(
                item.get('type') == 'function_call_output' for item in body['input']
            ):
                output = [
                    {
                        'id': 'fc_list',
                        'type': 'function_call',
                        'call_id': 'list-call',
                        'name': 'list_files',
                        'arguments': '{}',
                        'status': 'completed',
                    }
                ]
            events = [
                {'type': 'response.created', 'response': {'id': 'resp_fixture', 'status': 'in_progress', 'output': []}},
                {'type': 'response.output_item.done', 'output_index': 0, 'item': output[0]},
                {
                    'type': 'response.completed',
                    'response': {
                        'id': 'resp_fixture',
                        'status': 'completed',
                        'output': output,
                        'usage': {'input_tokens': 20, 'output_tokens': 10, 'total_tokens': 30},
                    },
                },
            ]
            if 'RESPONSES_REASONING_CYCLE' in json.dumps(body['input']):
                if not any(item.get('type') == 'function_call_output' for item in body['input']):
                    events[-1]['response']['output'] = [
                        {
                            'id': 'rs_fixture',
                            'type': 'reasoning',
                            'status': 'completed',
                            'summary': [],
                            'content': [{'type': 'reasoning_text', 'text': 'Synthetic fixture reasoning.'}],
                        },
                        {
                            'id': 'fc_files',
                            'type': 'function_call',
                            'call_id': 'files-call',
                            'name': 'list_files',
                            'arguments': '{}',
                            'status': 'completed',
                        },
                    ]
                events[-1]['response']['usage'] = {
                    'input_tokens': 100,
                    'input_tokens_details': {'cached_tokens': 80},
                    'output_tokens': 20,
                    'output_tokens_details': {'reasoning_tokens': 12},
                    'total_tokens': 120,
                }
            if not body.get('stream'):
                payload = json.dumps(events[-1]['response']).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            payload = ''.join(
                'event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n' for event in events
            ).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
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


@pytest.fixture
def config(responses_api, tmp_path):
    url, _ = responses_api
    source = (tmp_path / 'source').resolve()
    source.mkdir()
    (source / 'README.md').write_text('Source fixture.', encoding='utf-8')
    docs = (tmp_path / 'documents').resolve()
    docs.mkdir(exist_ok=True)
    value = {
        'id': 'fixture',
        'name': 'Fixture agent',
        'description': 'Investigate configured resources.',
        'enabled': True,
        'system_prompt': {'content': 'Use the active saved system prompt.'},
        'instructions': "Follow this agent's runbook.",
        'model': {
            'name': 'Fixture model',
            'provider': 'openai',
            'api_format': 'responses',
            'model': 'fixture-model',
            'base_url': url,
            'enabled': True,
            'api_key_ref': {'id': 'model-key'},
        },
        'code': {
            'enabled': True,
            'projects': [
                {'id': 'source', 'name': 'Source', 'description': 'Local source', 'folder_paths': [str(source)]}
            ],
        },
        'documents': {
            'enabled': True,
            'all_collections': False,
            'collections': [
                {'id': 'runbooks', 'name': 'runbooks', 'path': str(docs), 'description': 'Operations docs'}
            ],
        },
        'skills': {
            'enabled': True,
            'items': [
                {'name': 'Inspection', 'instructions': 'Read before suggesting changes.', 'enabled': True},
                {'name': 'Disabled skill', 'instructions': 'MUST_NOT_APPEAR', 'enabled': False},
            ],
        },
        'mcp': {'enabled': False, 'servers': [{'url': 'http://must-not-contact.test/mcp'}]},
        'ssh': {'enabled': False, 'servers': [{'hostname': 'must-not-connect.test'}]},
        'services': {
            'enabled': True,
            'items': [{'id': 'payments', 'name': 'Payments', 'owner': 'Inspect the queue.'}],
        },
    }
    value['tools'] = implied_tools(value)  # every saved configuration has its tools
    return value


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('invalid_document', [False, True])
def test_usage_is_reported_before_output_validation(config, stream, invalid_document):
    from agents import ModelBehaviorError
    from pydantic import BaseModel

    class Document(BaseModel):
        markdown: str

    recorded = []

    async def record(usage):
        recorded.append((usage.input_tokens, usage.output_tokens, usage.total_tokens))

    async def run():
        return await agent(
            config,
            'Reply with a message.',
            credentials={'model-key': 'fixture-secret'},
            stream=stream,
            on_usage=record,
            output_type=Document if invalid_document else None,
        )

    if invalid_document:
        with pytest.raises(ModelBehaviorError):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert recorded == [(20, 10, 30)]


def test_official_sdk_runs_task_with_saved_prompt_model_and_enabled_context(config, responses_api, monkeypatch):
    _, requests = responses_api
    before = copy.deepcopy(config)
    monkeypatch.setattr(
        'knowledge_admin.agents.runner.psycopg.connect',
        lambda *a, **kw: pytest.fail('Runner must not query PostgreSQL'),
    )
    result = asyncio.run(
        agent(
            config, 'Explain the source fixture.', credentials={'model-key': 'fixture-api-secret'}, timeout_seconds=45
        )
    )
    assert result.final_output == 'Configured agent completed the task.'
    assert result.context_wrapper.usage.total_tokens == 30
    assert config == before and len(requests) == 1
    request = requests[0]
    assert request['path'] == '/v1/responses' and request['authorization'] == 'Bearer fixture-api-secret'
    assert request['body']['model'] == 'fixture-model'
    serialized = json.dumps(request['body'])
    # the skills stay in the prompt until load_skill (Phase 3)
    for expected in (
        'Use the active saved system prompt.',
        'Explain the source fixture.',
        'Read before suggesting changes.',
    ):
        assert expected in serialized
    # the run's resources are not in the prompt: get_available_resources lists them, get_resource details them
    for listed in ('Local source', 'Operations docs', 'Inspect the queue.'):
        assert listed not in serialized
    for excluded in ('fixture-api-secret', 'MUST_NOT_APPEAR', 'must-not-connect.test', 'must-not-contact.test'):
        assert excluded not in serialized
    assert config['code']['projects'][0]['folder_paths'][0] not in serialized
    structural = next(tool for tool in request['body']['tools'] if tool.get('name') == 'ast_grep')
    assert 'SetPersonalLimits' in structural['description']
    assert 'native rule' in structural['description']
    source_search = next(tool for tool in request['body']['tools'] if tool.get('name') == 'code_search')
    assert 'complete bodies' in source_search['description']
    shell = next(tool for tool in request['body']['tools'] if tool.get('name') == 'shell_run')
    assert 'Python scripts' in shell['description'] and 'Bash' in shell['description']
    assert request['body'].get('max_output_tokens') is None


def test_document_output_budget_reaches_the_provider(config, responses_api):
    _, requests = responses_api
    asyncio.run(
        agent(
            config,
            'Write a complete document.',
            credentials={'model-key': 'fixture-api-secret'},
            max_output_tokens=16384,
        )
    )
    assert requests[0]['body']['max_output_tokens'] == 16384
    assert requests[0]['body']['store'] is False


def test_disabled_resources_are_not_used_or_validated(config, responses_api):
    _, requests = responses_api
    for section in ('code', 'documents', 'skills', 'services'):
        config[section]['enabled'] = False
    config['code']['projects'][0]['folder_paths'] = ['/missing/disabled-source']
    result = asyncio.run(
        agent(config, 'Answer without resources.', credentials={'model-key': 'fixture-api-secret'}, timeout_seconds=45)
    )
    assert result.final_output
    serialized = json.dumps(requests[0]['body'])
    for excluded in ('disabled-source', 'Operations docs', 'Inspection', 'Payments'):
        assert excluded not in serialized
    assert not any(
        tool.get('type') in ('local_shell', 'shell', 'custom') for tool in requests[0]['body'].get('tools', [])
    )
    assert not any(tool.get('name') == 'ast_grep' for tool in requests[0]['body'].get('tools', []))
    assert 'ast_grep' not in serialized and 'shell_run' not in serialized and 'code_search' not in serialized


@pytest.mark.parametrize('final_newline', [True, False])
def test_sdk_connects_selected_mcp_and_keeps_ssh_keys_out_of_prompt(config, responses_api, monkeypatch, final_newline):
    url, requests = responses_api
    mcp = {
        'id': 'fixture-mcp',
        'name': 'Fixture MCP',
        'description': 'Shared documentation.',
        'enabled': True,
        'url': url.removesuffix('/v1') + '/mcp',
        'bearer_token_ref': {'id': 'mcp-key'},
    }
    config['mcp'] = {'enabled': True, 'servers': [mcp]}
    config['ssh'] = {
        'enabled': True,
        'servers': [
            {
                'id': 'host',
                'name': 'Fixture server',
                'hostname': 'never-connect.test',
                'port': 2222,
                'username': 'operator',
                'description': 'Fixture machine.',
                'tags': ['test'],
                'enabled': True,
                'ssh_allowed': True,
                'known_hosts': '',
                'allow_sudo': False,
                'instructions': 'Inspect fixture logs only.',
                'private_key_ref': {'id': 'ssh-key'},
                'skills': [],
                'mcp_servers': [mcp],
            }
        ],
    }
    config['tools'] = implied_tools(config)
    directories = []
    ssh_key = asyncssh.generate_private_key('ssh-ed25519')
    private_key = ssh_key.export_private_key('openssh').decode()
    if not final_newline:
        private_key = private_key.rstrip('\n')

    @contextmanager
    def temporary_directory(**kwargs):
        with TemporaryDirectory(**kwargs) as directory:
            directories.append(Path(directory))
            yield directory
            assert not (Path(directory) / 'ssh-key-0').exists()

    monkeypatch.setattr('knowledge_admin.agents.runner.TemporaryDirectory', temporary_directory)
    result = asyncio.run(
        agent(
            config,
            'Describe the available resources; do not connect to SSH.',
            credentials={'model-key': 'fixture-api-secret', 'mcp-key': 'fixture-mcp-secret', 'ssh-key': private_key},
            timeout_seconds=45,
        )
    )
    assert result.final_output
    assert all(not directory.exists() for directory in directories)
    mcp_requests = [request for request in requests if request['path'] == '/mcp']
    assert mcp_requests and all(request['authorization'] == 'Bearer fixture-mcp-secret' for request in mcp_requests)
    assert sum(request['body']['method'] == 'initialize' for request in mcp_requests) == 1
    request = next(request for request in requests if request['path'] == '/v1/responses')
    serialized = json.dumps(request['body'])
    for expected in ('lookup', 'ssh_run', 'get_resource'):
        assert expected in serialized
    assert 'Inspect fixture logs only.' not in serialized  # server instructions come from get_resource
    for secret in ('fixture-api-secret', 'fixture-mcp-secret', private_key, 'private_key_path', 'ssh-key-0'):
        assert secret not in serialized


def test_missing_mcp_credential_cleans_temporary_runtime(config, monkeypatch, tmp_path):
    config['mcp'] = {
        'enabled': True,
        'servers': [
            {
                'id': 'fixture-mcp',
                'name': 'Fixture MCP',
                'description': '',
                'enabled': True,
                'url': 'http://unused.test',
                'bearer_token_ref': {'id': 'missing'},
            }
        ],
    }
    config['tools'] = implied_tools(config)
    directory = TemporaryDirectory(dir=tmp_path)
    monkeypatch.setattr('knowledge_admin.agents.runner.TemporaryDirectory', lambda **kwargs: directory)
    with pytest.raises(ValueError, match='MCP token'):
        asyncio.run(agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}))
    assert not Path(directory.name).exists()


@pytest.mark.parametrize(
    'problem, message',
    [
        ('disabled', 'agent is disabled'),
        ('prompt', 'requires a system prompt'),
        ('model', 'enabled model'),
        ('credential', 'API key'),
        ('unsupported', 'Unsupported model'),
        ('path', 'folder is unavailable'),
        ('task', 'Provide a task'),
    ],
)
def test_invalid_config_fails_before_starting_sdk(config, responses_api, monkeypatch, problem, message):
    if problem == 'disabled':
        config['enabled'] = False
    if problem == 'prompt':
        config['system_prompt']['content'] = ''
    if problem == 'model':
        config['model'] = None
    if problem == 'unsupported':
        config['model']['provider'] = 'unknown'
    if problem == 'path':
        config['code']['projects'][0]['folder_paths'] = ['/missing/source']
    monkeypatch.setattr(
        'knowledge_admin.agents.runner.Runner.run_streamed',
        lambda *a, **kw: pytest.fail('Invalid settings must not start the SDK'),
    )
    with pytest.raises(ValueError, match=message):
        asyncio.run(
            agent(
                config,
                '' if problem == 'task' else 'Inspect.',
                credentials={} if problem == 'credential' else {'model-key': 'fixture-api-secret'},
            )
        )
    assert responses_api[1] == []


def test_resume_uses_history_supplied_by_caller(config, responses_api):
    first = asyncio.run(
        agent(config, 'Remember the test word apricot.', credentials={'model-key': 'fixture-api-secret'})
    )
    first_history = first.to_input_list()
    second = asyncio.run(
        agent(
            config, 'What word did I give you?', credentials={'model-key': 'fixture-api-secret'}, history=first_history
        )
    )
    assert first.final_output and second.final_output
    assert len(second.to_input_list()) > len(first_history)
    requests = responses_api[1]
    assert len(requests) == 2
    resumed = json.dumps(requests[1]['body'])
    assert 'Remember the test word apricot.' in resumed
    assert first.final_output in resumed
    assert 'What word did I give you?' in resumed


def test_sdk_ssh_tool_uses_configured_key_and_returns_output(config, responses_api, ssh_server):
    config['ssh'] = {
        'enabled': True,
        'servers': [
            {
                'id': 'host',
                'name': 'Linux fixture',
                'hostname': ssh_server.hostname,
                'port': ssh_server.port,
                'username': ssh_server.username,
                'known_hosts': ssh_server.known_hosts,
                'enabled': True,
                'ssh_allowed': True,
                'description': 'Test server',
                'tags': [],
                'allow_sudo': False,
                'instructions': 'Inspect as operator only.',
                'private_key_ref': {'id': 'ssh-key'},
                'skills': [],
                'mcp_servers': [],
            }
        ],
    }
    config['tools'] = implied_tools(config)
    result = asyncio.run(
        agent(
            config,
            'SSH_TOOL_CYCLE: inspect the login.',
            credentials={'model-key': 'fixture-api-secret', 'ssh-key': ssh_server.private_key},
        )
    )
    assert result.final_output == 'Configured agent completed the task.'
    requests = responses_api[1]
    assert len(requests) == 2
    output = next(item for item in requests[1]['body']['input'] if item.get('type') == 'function_call_output')
    assert json.loads(output['output'])['stdout'].strip() == 'operator'
    assert 'Inspect as operator only.' not in json.dumps(requests[0]['body'])  # read with get_resource
    assert ssh_server.private_key not in json.dumps(requests)


def test_mcp_tools_with_same_local_name_remain_distinct(config, responses_api):
    config['mcp'] = {
        'enabled': True,
        'servers': [
            {
                'id': name,
                'name': name,
                'description': 'Fixture',
                'url': responses_api[0].removesuffix('/v1') + '/mcp',
                'enabled': True,
                'bearer_token_ref': None,
            }
            for name in ('docs-one', 'docs-two')
        ],
    }
    config['tools'] = implied_tools(config)
    asyncio.run(agent(config, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}))
    request = next(r for r in responses_api[1] if r['path'] == '/v1/responses')
    names = [t['name'] for t in request['body']['tools'] if 'lookup' in t['name']]
    assert len(names) == len(set(names)) == 2


def test_a_missing_document_collection_path_is_skipped_not_fatal(config, responses_api, caplog):
    config['documents']['collections'] = [
        {
            'id': 'jira',
            'name': 'jira',
            'description': 'Tickets',
            'paths': [config['documents']['collections'][0]['path'], '/missing/rag-data/jira'],
        }
    ]
    with caplog.at_level('WARNING', logger='knowledge_admin.capabilities.files'):
        result = asyncio.run(
            agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}, timeout_seconds=45)
        )
    assert result.final_output
    assert 'Skipping unavailable document collection folder: /missing/rag-data/jira' in caplog.text
