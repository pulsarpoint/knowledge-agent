"""Per-run agents' tools reach exactly what the run was given: anything else is refused like any resource outside an
agent's configuration, and a kind the run got nothing of is refused, never widened to every resource."""

import asyncio
import json

import pytest
from agents.tool_context import ToolContext
from knowledge_admin.agents.runner import get_prompt
from access_helpers import entry, with_access
from knowledge_admin import access as access_rules
from tool_helpers import tool, tools

ast_grep, code_graph, code_search, list_files, read_file, shell_run, ssh_run = tools(
    'ast_grep',
    'code_graph',
    'code_search',
    'list_files',
    'read_file',
    'shell_run',
    'ssh_run',
)

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


def run_context(folders, *, code=(), servers=()) -> dict:
    """A run's tool context: its access object holds exactly the run's resources."""
    host = entry(
        'server',
        'host',
        'Host',
        hostname='host.test',
        port=22,
        username='operator',
        enabled=True,
        ssh_allowed=True,
        allow_sudo=False,
        known_hosts='',
        private_key_ref={'id': 'key'},
    )
    entries = [entry('code_project', 'given', 'Given', folder_paths=[str(folders['given'])])] if 'given' in code else []
    entries += [host] if 'host' in servers else []
    config = {
        'id': 'analyst',
        'name': 'Code analyst',
        'description': '',
        'enabled': True,
        'instructions': '',
        'system_prompt': {'content': 'Analyse code.'},
    }
    return {'config': with_access(config, entries), 'credentials': {}, 'workspace': folders['workspace']}


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
    code = access_rules.refuse_none('code_project')
    files = access_rules.refuse_none('code_project', 'skill')  # the file and shell tools read code and skill folders
    assert invoke(list_files, context) == files
    assert invoke(shell_run, context, command='ls /') == files
    assert invoke(code_search, context, path=str(folders['given']), language='Python', name='given') == code
    assert invoke(ast_grep, context, path=str(folders['given']), language='Python', pattern=PATTERN) == code
    assert invoke(code_graph, context, project_id='given') == code
    assert invoke(ssh_run, context, server_id='host', command='uptime') == access_rules.refuse_none('server')


def test_ssh_reaches_only_the_given_servers(folders):
    context = run_context(folders, servers=['host'])
    assert 'This server is not available in this run.' in invoke(
        ssh_run, context, server_id='elsewhere', command='uptime'
    )


def test_the_prompt_no_longer_lists_the_runs_resources(folders):
    prompt = get_prompt(run_context(folders, code=['given'])['config'])
    assert 'This run was given only' not in prompt and str(folders['given']) not in prompt


def test_a_runs_access_decides_what_tools_reach(folders):
    """A run with every file tool switched on still reads only the code project its access holds."""
    context = run_context(folders, code=['given'])
    read = tool('read_file')
    assert 'given' in invoke(read, context, path=str(folders['given'] / 'a.py'))
    refused = invoke(read, context, path=str(folders['other'] / 'b.py'))
    assert 'outside the agent' in refused
