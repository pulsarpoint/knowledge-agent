"""The catalog: SDK-independent tools with usage text and the adapter (every run gets every tool:
test_every_tool.py)."""

import asyncio
import dataclasses
import json

from access_helpers import entry, with_access
from agents import FunctionTool
from agents.tool_context import ToolContext
from knowledge_admin.agents.runner import get_prompt
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.sdk import NOT_OFFERED, function_tool

SERVER = entry(
    'server',
    'host',
    'Host',
    hostname='host.test',
    port=22,
    username='ops',
    allow_sudo=False,
    known_hosts='',
    enabled=True,
    ssh_allowed=True,
    private_key_ref=None,
)
MCP = entry('mcp_server', 'm1', 'Docs', url='http://mcp.test', enabled=True, bearer_token_ref=None)


def config(documents=False, grounded=False, ssh=False, entries=()):
    """A run's configuration: its access holds the SSH server (ssh) and entries; grounded only with documents."""
    value = {
        'id': 'agent',
        'name': 'Agent',
        'description': '',
        'instructions': '',
        'enabled': True,
        'system_prompt': {'content': 'Answer.'},
        'grounded': documents and grounded,
    }
    return with_access(value, [*([SERVER] if ssh else []), *entries])


def test_descriptions_are_the_usage_text_and_keep_todays_guidance():
    for spec in CATALOG.values():
        built = function_tool(spec, spec.usage)
        assert isinstance(built, FunctionTool) and built.name == spec.name and built.description == spec.usage
        assert built.strict_json_schema and built.params_json_schema['additionalProperties'] is False
        assert (
            not {'ctx', 'context', 'config', 'credentials', 'workspace'} & built.params_json_schema['properties'].keys()
        )
    assert 'SetPersonalLimits' in CATALOG['ast_grep'].usage and 'native rule' in CATALOG['ast_grep'].usage
    assert 'complete bodies' in CATALOG['code_search'].usage
    assert 'Selected resource paths are mounted read-only' in CATALOG['shell_run'].usage
    assert 'Use code_graph first' in CATALOG['code_graph'].usage
    assert 'Read the server with get_resource first' in CATALOG['ssh_run'].usage
    assert 'as_root for sudo only when' in CATALOG['ssh_run'].usage
    assert set(CATALOG['ssh_run'].arguments.model_json_schema()['properties']) == {
        'server_id',
        'command',
        'as_root',
        'timeout_seconds',
    }


def test_the_adapter_refuses_a_tool_the_run_was_not_offered(tmp_path):
    (tmp_path / 'a.txt').write_text('hello', encoding='utf-8')
    c = config(entries=[entry('code_project', 'p', 'P', folder_paths=[str(tmp_path.resolve())])])
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    context = {
        'config': c,
        'credentials': {},
        'workspace': workspace.resolve(),
        'tools': frozenset({'list_files'}),
    }
    read = function_tool(CATALOG['read_file'], CATALOG['read_file'].usage)
    payload = json.dumps({'path': str((tmp_path / 'a.txt').resolve())})
    wrapped = ToolContext(context=context, tool_name='read_file', tool_call_id='t1', tool_arguments=payload)
    refused = asyncio.run(read.on_invoke_tool(wrapped, payload))
    assert NOT_OFFERED.format(name='read_file') in refused and 'hello' not in refused
    context['tools'] = frozenset({'read_file'})
    assert 'hello' in asyncio.run(read.on_invoke_tool(wrapped, payload))
    invalid = asyncio.run(read.on_invoke_tool(wrapped, json.dumps({'offset': 1})))
    assert invalid.startswith('An error occurred while running the tool.')  # the SDK's own error text


def test_the_prompt_no_longer_carries_tool_sentences():
    prompt = get_prompt(config(documents=True, ssh=True))
    for sentence in (
        'Use shell_run for Bash pipelines',
        'Use code_search to find',
        'Use code_graph first',
        'Use search_documents to find',
        'Use ssh_run with the configured server ID',
    ):
        assert sentence not in prompt
    assert (
        'Use only the configured resources and tools.' in prompt
        and 'get_available_resources lists' in prompt
        and '"ssh_servers"' not in prompt
    )


def test_a_refused_tool_answers_not_offered_even_with_invalid_arguments():
    context = {
        'config': config(),
        'credentials': {},
        'workspace': None,
        'tools': frozenset(),
    }
    read = function_tool(CATALOG['read_file'], CATALOG['read_file'].usage)
    payload = json.dumps({'offset': 1})
    wrapped = ToolContext(context=context, tool_name='read_file', tool_call_id='t1', tool_arguments=payload)
    assert NOT_OFFERED.format(name='read_file') in asyncio.run(read.on_invoke_tool(wrapped, payload))


def test_a_failing_tool_is_logged_and_traced_with_secrets_redacted(caplog, monkeypatch):
    from knowledge_admin.tools import sdk

    errors = []

    class Span:
        def set_error(self, error):
            errors.append(error)

    monkeypatch.setattr(sdk, 'get_current_span', lambda: Span())
    context = {
        'config': config(),
        'credentials': {'key': 'TOP-SECRET'},
        'workspace': None,
        'tools': frozenset({'read_file'}),
    }

    async def fail(context, arguments):
        raise ValueError('cannot read with key TOP-SECRET')

    read = function_tool(dataclasses.replace(CATALOG['read_file'], run=fail), CATALOG['read_file'].usage)
    payload = json.dumps({'path': '/a.txt'})
    wrapped = ToolContext(context=context, tool_name='read_file', tool_call_id='t1', tool_arguments=payload)
    with caplog.at_level('ERROR', logger=sdk.logger.name):
        result = asyncio.run(read.on_invoke_tool(wrapped, payload))
    assert result == (
        'An error occurred while running the tool. Please try again. Error: cannot read with key [redacted]'
    )
    [record] = [r for r in caplog.records if r.name == sdk.logger.name]
    assert 'Tool read_file failed' in record.getMessage() and 'TOP-SECRET' not in record.getMessage()
    [error] = errors
    assert error['message'] == 'Error running tool (non-fatal)' and error['data']['tool_name'] == 'read_file'
    assert 'TOP-SECRET' not in error['data']['error'] and '[redacted]' in error['data']['error']


def test_run_subagent_is_in_the_catalog():
    spec = CATALOG['run_subagent']
    assert (spec.group, spec.label) == ('agents', 'Start subagents')
    assert spec.usage.startswith('Start a helper for a separate, well-defined part of your work')
