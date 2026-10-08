"""The catalog: SDK-independent tools with usage text, the "offered today" rule, selection and the adapter."""
import asyncio
import dataclasses
import itertools
import json

import pytest
from agents import FunctionTool
from agents.tool_context import ToolContext

from knowledge_admin.agents import get_prompt
from knowledge_admin.tools.catalog import (CATALOG, capability_tools, effective_tools, enabled_capability_tools,
                                           implied_tools, tools_summary)
from knowledge_admin.tools.sdk import NOT_OFFERED, function_tool

SERVER = {'id': 'host', 'name': 'Host', 'hostname': 'host.test', 'port': 22, 'username': 'ops', 'description': '',
          'tags': [], 'allow_sudo': False, 'instructions': '', 'enabled': True, 'ssh_allowed': True, 'skills': [],
          'mcp_servers': []}


def config(code=False, documents=False, grounded=False, ssh=False, graphs=(), tools=None):
    value = {'id': 'agent', 'name': 'Agent', 'description': '', 'instructions': '', 'enabled': True,
             'runtime': 'openai', 'resources': 'fixed', 'system_prompt': {'content': 'Answer.'},
             'code': {'enabled': code, 'projects': [], 'graphs': list(graphs)},
             'documents': {'enabled': documents, 'grounded': grounded, 'all_collections': False, 'collections': []},
             'ssh': {'enabled': ssh, 'servers': [SERVER] if ssh else []},
             'skills': {'enabled': False, 'items': []}, 'services': {'enabled': False, 'items': []},
             'mcp': {'enabled': False, 'servers': []}}
    if tools is not None:
        value['tools'] = {'items': [{'tool': name, 'group': CATALOG[name].group, 'mode': 'on', 'usage': None,
                                     'subagent_allowed': True} for name in tools]}
    return value


def todays_tools(c, ssh_servers) -> set[str]:
    """the former agents tool selection before this change, kept here as the reference."""
    names = set()
    grounded = c['documents']['enabled'] and c['documents'].get('grounded', False)
    if c['code']['enabled'] or c['documents']['enabled'] and not grounded:
        names |= {'list_files', 'read_file', 'search_files', 'shell_run'}
    if c['documents']['enabled']:
        names |= {'search_documents', 'read_document'}
    if grounded:
        names.add('report_not_covered')
    if c['code']['enabled']:
        names |= {'code_search', 'ast_grep'}
    if c['code']['enabled'] and c['code'].get('graphs'):
        names.add('code_graph')
    if ssh_servers:
        names.add('ssh_run')
    return names


def offered(c, ssh_servers=()) -> set[str]:
    return {spec.name for spec, _ in effective_tools(c, list(ssh_servers))}


@pytest.mark.parametrize('code, documents, grounded, ssh, graph', list(itertools.product([False, True], repeat=5)))
def test_catalog_offers_todays_tools_for_every_capability_combination(code, documents, grounded, ssh, graph):
    graphs = [{'id': 'p', 'name': 'P', 'graph_path': '/g', 'code_root': '/c', 'source_hash': 'h'}] if graph else []
    c = config(code, documents, grounded, ssh, graphs)
    servers = [SERVER] if ssh else []
    assert offered(c, servers) == todays_tools(c, servers)  # without 'tools': the implied tools
    c['tools'] = implied_tools(c)
    assert offered(c, servers) == todays_tools(c, servers)  # the same rule, written out


def test_only_selected_tools_with_met_needs_are_offered():
    c = config(code=True, documents=True, ssh=True, tools=['read_file', 'ssh_run', 'search_documents', 'code_graph'])
    assert offered(c, [SERVER]) == {'read_file', 'ssh_run', 'search_documents'}  # code_graph: no graph
    assert offered(c, []) == {'read_file', 'search_documents'}  # ssh_run: no usable server
    c['code']['enabled'] = False
    assert offered(c, [SERVER]) == {'read_file', 'ssh_run', 'search_documents'}  # documents ungrounded: files
    c['documents']['grounded'] = True
    assert offered(c, [SERVER]) == {'ssh_run', 'search_documents'}
    c['tools']['items'][0]['mode'] = 'off'
    c['tools']['items'][2]['mode'] = 'per_run'  # not offered in step 1
    assert offered(c, [SERVER]) == {'ssh_run'}
    assert offered(config(code=True, tools=[]), []) == set()


def test_descriptions_are_the_usage_text_and_keep_todays_guidance():
    for spec in CATALOG.values():
        built = function_tool(spec, spec.usage)
        assert isinstance(built, FunctionTool) and built.name == spec.name and built.description == spec.usage
        assert built.strict_json_schema and built.params_json_schema['additionalProperties'] is False
        assert not {'ctx', 'context', 'config', 'credentials', 'workspace'} & built.params_json_schema['properties'].keys()
    assert 'SetPersonalLimits' in CATALOG['ast_grep'].usage and 'native rule' in CATALOG['ast_grep'].usage
    assert 'complete bodies' in CATALOG['code_search'].usage
    assert 'Selected resource paths are mounted read-only' in CATALOG['shell_run'].usage
    assert 'Use code_graph first' in CATALOG['code_graph'].usage
    assert 'Request as_root only when allow_sudo is true' in CATALOG['ssh_run'].usage
    assert set(CATALOG['ssh_run'].arguments.model_json_schema()['properties']) == {'server_id', 'command', 'as_root', 'timeout_seconds'}
    c = config(code=True, tools=['read_file'])
    c['tools']['items'][0]['usage'] = 'Read only the files you were asked about.'
    [(spec, usage)] = effective_tools(c, [])
    assert usage == 'Read only the files you were asked about.'
    assert function_tool(spec, usage).description == usage


def test_the_adapter_refuses_a_tool_the_run_was_not_offered(tmp_path):
    (tmp_path / 'a.txt').write_text('hello', encoding='utf-8')
    c = config(code=True)
    c['code']['projects'] = [{'id': 'p', 'name': 'P', 'folder_paths': [str(tmp_path.resolve())]}]
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    context = {'config': c, 'credentials': {}, 'workspace': workspace.resolve(), 'shell_enabled': False,
               'tools': frozenset({'list_files'})}
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
    prompt = get_prompt(config(code=True, documents=True, ssh=True), [SERVER], [])
    for sentence in ('Use shell_run for Bash pipelines', 'Use code_search to find', 'Use code_graph first',
                     'Use search_documents to find', 'Use ssh_run with the configured server ID'):
        assert sentence not in prompt
    assert 'Use only the configured resources and tools.' in prompt and '"ssh_servers"' in prompt


def test_a_refused_tool_answers_not_offered_even_with_invalid_arguments():
    context = {'config': config(code=True), 'credentials': {}, 'workspace': None, 'shell_enabled': False,
               'tools': frozenset()}
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
    context = {'config': config(code=True), 'credentials': {'key': 'TOP-SECRET'}, 'workspace': None,
               'shell_enabled': False, 'tools': frozenset({'read_file'})}
    async def fail(context, arguments):
        raise ValueError('cannot read with key TOP-SECRET')
    read = function_tool(dataclasses.replace(CATALOG['read_file'], run=fail), CATALOG['read_file'].usage)
    payload = json.dumps({'path': '/a.txt'})
    wrapped = ToolContext(context=context, tool_name='read_file', tool_call_id='t1', tool_arguments=payload)
    with caplog.at_level('ERROR', logger=sdk.logger.name):
        result = asyncio.run(read.on_invoke_tool(wrapped, payload))
    assert result == ('An error occurred while running the tool. Please try again. '
                      'Error: cannot read with key [redacted]')
    [record] = [r for r in caplog.records if r.name == sdk.logger.name]
    assert 'Tool read_file failed' in record.getMessage() and 'TOP-SECRET' not in record.getMessage()
    [error] = errors
    assert error['message'] == 'Error running tool (non-fatal)' and error['data']['tool_name'] == 'read_file'
    assert 'TOP-SECRET' not in error['data']['error'] and '[redacted]' in error['data']['error']


def test_the_summary_counts_tools_that_are_on_with_their_need_met_and_enabled_mcp_servers():
    c = config(code=True, tools=['read_file', 'code_search', 'ssh_run', 'search_documents', 'code_graph'])
    c['mcp'] = {'enabled': True, 'servers': [
        {'id': 'm1', 'name': 'One', 'enabled': True, 'tools': [{'name': 'a'}, {'name': 'b'}]},
        {'id': 'm2', 'name': 'Two', 'enabled': False, 'tools': [{'name': 'c'}]}]}
    summary = tools_summary(c)  # ssh_run: no SSH; search_documents: no documents; code_graph counts by the switch
    assert summary['groups'] == {'code': 3, 'documents': 0, 'servers': 0, 'mcp': 2, 'agents': 0}
    assert summary['on'] == 5


def test_code_graphs_are_listed_only_when_a_tool_that_reads_them_is_offered():
    graph = {'id': 'p', 'name': 'P', 'graph_path': '/g', 'code_root': '/c', 'source_hash': 'h'}
    for tools, listed in ((['read_file'], False), (['read_file', 'code_graph'], True), (['shell_run'], True)):
        c = config(code=True, graphs=[graph], tools=tools)
        assert ('"code_graphs"' in get_prompt(c, [], [])) is listed, tools
        assert ('"code_graphs"' in get_prompt(c, [], [], tools)) is listed, tools
    assert '"code_graphs"' not in get_prompt(config(code=True, graphs=[graph], tools=['code_graph']), [], [], [])


CODE_TOOLS = ['list_files', 'read_file', 'search_files', 'shell_run', 'code_search', 'ast_grep', 'code_graph']
FILE_TOOLS = ['list_files', 'read_file', 'search_files', 'shell_run']


def test_a_capabilitys_tools_are_every_tool_of_its_needs():
    assert capability_tools('code', config(code=True)) == CODE_TOOLS  # code_graph too, graph or not
    assert capability_tools('documents', config(documents=True)) == [*FILE_TOOLS, 'search_documents', 'read_document']
    # with code projects on the file tools belong to code; report_not_covered only while grounded
    assert capability_tools('documents', config(code=True, documents=True, grounded=True)) == [
        'search_documents', 'read_document', 'report_not_covered']
    # grounded documents without code projects: no file tools (their need, files, is not met)
    assert capability_tools('documents', config(documents=True, grounded=True)) == [
        'search_documents', 'read_document', 'report_not_covered']
    assert capability_tools('ssh', config(ssh=True)) == ['ssh_run']
    assert enabled_capability_tools(config(code=True, ssh=True)) == [*CODE_TOOLS, 'ssh_run']
    assert enabled_capability_tools(config(code=True, documents=True)) == [*CODE_TOOLS, 'search_documents',
                                                                          'read_document']  # each once
    assert enabled_capability_tools(config()) == []


def test_run_subagent_is_never_on_by_default_and_never_implied():
    spec = CATALOG['run_subagent']
    assert (spec.group, spec.label, spec.needs) == ('agents', 'Start subagents', None)
    assert spec.usage.startswith('Start a helper for a separate, well-defined part of your work')
    every = config(code=True, documents=True, ssh=True)
    assert not spec.default_on(every)
    assert 'run_subagent' not in {entry['tool'] for entry in implied_tools(every)['items'] if entry['mode'] == 'on'}
    assert 'run_subagent' not in enabled_capability_tools(every)
    assert 'run_subagent' in offered(config(tools=['run_subagent']))  # offered exactly when its row is on
