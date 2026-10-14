"""Per-run agents' tools reach exactly what the run was given: anything else is refused like any resource outside an
agent's configuration, and a kind the run got nothing of is refused, never widened to every resource."""
import asyncio
import json

import pytest
from agents.tool_context import ToolContext

from knowledge_admin.agents.runner import get_prompt
from knowledge_admin.knowledge_agents.config import NO_RUN_RESOURCES
from knowledge_admin.capabilities.context import NOTHING_GIVEN
from knowledge_admin.tools.catalog import implied_tools
from tool_helpers import tool, tools
from test_document_tools import rag  # noqa: F401  (fixture)

ast_grep, code_graph, code_search, read_document, search_documents, list_files, read_file, shell_run, ssh_run = tools(
    'ast_grep', 'code_graph', 'code_search', 'read_document', 'search_documents', 'list_files', 'read_file', 'shell_run',
    'ssh_run')

PATTERN = 'def $F($$$A): $$$B'


def invoke(tool, context, **arguments) -> str:
    payload = json.dumps(arguments)
    wrapped = ToolContext(context=context, tool_name=tool.name, tool_call_id='t1', tool_arguments=payload)
    return asyncio.run(tool.on_invoke_tool(wrapped, payload))


@pytest.fixture
def folders(tmp_path):
    for name in ('workspace', 'given', 'other'):
        (tmp_path / name).mkdir()
    (tmp_path / 'given' / 'a.py').write_text('def given():\n    return 1\n', encoding='utf-8')
    (tmp_path / 'other' / 'b.py').write_text('def other():\n    return 2\n', encoding='utf-8')
    return {name: (tmp_path / name).resolve() for name in ('workspace', 'given', 'other')}


def run_context(folders, *, code=(), collections=(), servers=()) -> dict:
    """A per-run agent's tool context with the configuration resolve() builds: the lists are the run's resources."""
    host = {'id': 'host', 'name': 'Host', 'hostname': 'host.test', 'port': 22, 'username': 'operator',
            'enabled': True, 'ssh_allowed': True, 'allow_sudo': False, 'known_hosts': '', 'private_key_ref': {'id': 'key'}}
    config = {
        'id': 'analyst', 'name': 'Code analyst', 'description': '', 'enabled': True, 'resources': 'per_run',
        'instructions': '', 'system_prompt': {'content': 'Analyse code.'},
        'code': {'enabled': True, 'all_projects': False, 'projects': [
            {'id': 'given', 'name': 'Given', 'folder_paths': [str(folders['given'])]}] if 'given' in code else []},
        'documents': {'enabled': True, 'grounded': False, 'all_collections': False, 'collections': [
            {'id': '1', 'name': 'payments', 'description': '', 'paths': []}] if 'payments' in collections else []},
        'ssh': {'enabled': True, 'servers': [host] if 'host' in servers else []},
        'skills': {'enabled': False, 'items': []}, 'mcp': {'enabled': False, 'servers': []},
        'services': {'enabled': False, 'items': []},
        'run_resources': {'code_project_ids': list(code), 'collection_ids': list(collections),
                          'server_ids': list(servers), 'service_ids': []}}
    return {'config': config, 'credentials': {}, 'workspace': folders['workspace'], 'shell_enabled': True}


def test_file_and_code_tools_reach_only_the_given_code_projects(folders):
    context = run_context(folders, code=['given'])
    roots = json.loads(invoke(list_files, context))['entries']
    assert {entry['path'] for entry in roots} == {str(folders['workspace']), str(folders['given'])}
    assert 'def given' in invoke(read_file, context, path=str(folders['given'] / 'a.py'))
    outside = 'outside the agent’s selected folders'
    assert outside in invoke(read_file, context, path=str(folders['other'] / 'b.py'))
    assert outside in invoke(code_search, context, path=str(folders['other']), language='Python', name='other')
    assert outside in invoke(ast_grep, context, path=str(folders['other']), language='Python', pattern=PATTERN)


def test_a_kind_the_run_got_nothing_of_is_refused_never_widened(folders, monkeypatch):
    monkeypatch.setenv('AGENT_SHELL_URL', 'http://shell.invalid')  # refused before any request
    context = run_context(folders)
    assert NOTHING_GIVEN['files'] in invoke(list_files, context)
    assert NOTHING_GIVEN['files'] in invoke(shell_run, context, command='ls /')
    assert NOTHING_GIVEN['code'] in invoke(code_search, context, path=str(folders['given']), language='Python', name='given')
    assert NOTHING_GIVEN['code'] in invoke(ast_grep, context, path=str(folders['given']), language='Python', pattern=PATTERN)
    assert NOTHING_GIVEN['code'] in invoke(code_graph, context, project_id='given')
    assert NOTHING_GIVEN['ssh'] in invoke(ssh_run, context, server_id='host', command='uptime')
    assert NOTHING_GIVEN['documents'] in invoke(search_documents, context, query='limits')
    assert NOTHING_GIVEN['documents'] in invoke(read_document, context, collection='payments', path='limits.md')
    unstarted = run_context(folders, code=['given'])
    del unstarted['config']['run_resources']
    assert NO_RUN_RESOURCES in invoke(list_files, unstarted)


def test_document_search_uses_only_the_given_collections(rag, folders):
    calls, _ = rag
    context = run_context(folders, collections=['payments'])
    context['config']['documents']['all_collections'] = True  # even a stray "all" stays what the run was given
    assert '[1] payments/retries.md' in invoke(search_documents, context, query='retries')
    search = next(json.loads(call.content) for call in calls if call.url.path == '/search')
    assert search['collections'] == ['payments']
    assert 'outside the agent’s document collections' in invoke(search_documents, context, query='x', collection='private')


def test_ssh_reaches_only_the_given_servers(folders):
    context = run_context(folders, servers=['host'])
    assert 'This server is not available in this run.' in invoke(ssh_run, context, server_id='elsewhere', command='uptime')


def test_the_prompt_says_the_run_got_only_these_resources(folders):
    prompt = get_prompt(run_context(folders, code=['given'])['config'], [], [])
    assert 'This run was given only the resources listed below.' in prompt
    assert str(folders['given']) in prompt and str(folders['other']) not in prompt
    fixed = run_context(folders, code=['given'])['config'] | {'resources': 'fixed'}
    assert 'This run was given only' not in get_prompt(fixed, [], [])


def test_per_run_narrowing_still_decides_what_tools_reach(folders):
    """A per-run run with every file tool switched on still reads only its own code project."""
    context = run_context(folders, code=['given'])
    context['config']['tools'] = implied_tools(context['config'])
    read = tool('read_file')
    assert 'given' in invoke(read, context, path=str(folders['given'] / 'a.py'))
    refused = invoke(read, context, path=str(folders['other'] / 'b.py'))
    assert 'outside the agent' in refused
