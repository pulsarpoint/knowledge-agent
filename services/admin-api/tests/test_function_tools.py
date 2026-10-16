"""Exercise actual function-tool invocation against files, processes, and Linux SSH."""

import asyncio
import json
from pathlib import Path

import pytest
from access_helpers import entry, of, with_access
from agents import Agent, FunctionTool
from agents.tool_context import ToolContext
from knowledge_admin.access import SSH_NOT_ALLOWED

from tool_helpers import tools
from test_ssh import ssh_server

list_files, read_file, search_files, ast_grep, code_graph, code_search, shell_run, ssh_run = tools(
    'list_files', 'read_file', 'search_files', 'ast_grep', 'code_graph', 'code_search', 'shell_run', 'ssh_run'
)


@pytest.fixture
def context(tmp_path):
    workspace = (tmp_path / 'workspace').resolve()
    code = (tmp_path / 'code').resolve()
    docs = (tmp_path / 'documents').resolve()
    for directory in (workspace, code, docs):
        directory.mkdir()
    return {
        'workspace': workspace,
        'credentials': {'secret': 'test-tool-secret'},
        'config': with_access(
            {'enabled': True},
            [
                entry('code_project', 'code', 'Code', folder_paths=[str(code)]),
                entry('collection', 'docs', 'docs', paths=[str(docs)]),
            ],
        ),
    }


async def invoke(tool, context, **arguments):
    payload = json.dumps(arguments)
    wrapped = ToolContext(context=context, tool_name=tool.name, tool_call_id='tool-test', tool_arguments=payload)
    return await tool.on_invoke_tool(wrapped, payload)


def test_tools_register_directly_and_hide_backend_context():
    tools = [list_files, read_file, search_files, ast_grep, code_search, ssh_run, shell_run]
    agent = Agent(name='Tool test', instructions='Use configured resources.', tools=tools)
    assert all(isinstance(tool, FunctionTool) for tool in agent.tools)
    for tool in tools:
        assert tool.description
        assert not {'ctx', 'config', 'credentials', 'workspace'} & tool.params_json_schema['properties'].keys()
    assert set(ssh_run.params_json_schema['properties']) == {'server_id', 'command', 'as_root', 'timeout_seconds'}


def test_browse_read_pagination_redaction_and_folder_boundaries(context, tmp_path):
    code = tmp_path / 'code'
    (code / 'a.txt').write_text('abcdef', encoding='utf-8')
    (code / 'b.txt').write_text('test-tool-secret', encoding='utf-8')
    outside = tmp_path / 'private.txt'
    outside.write_text('private', encoding='utf-8')
    (code / 'escape.txt').symlink_to(outside)
    roots = json.loads(asyncio.run(invoke(list_files, context)))
    assert {entry['path'] for entry in roots['entries']} == {
        str(context['workspace']),
        str(code.resolve()),
        str((tmp_path / 'documents').resolve()),
    }
    page = json.loads(asyncio.run(invoke(list_files, context, path=str(code), limit=1)))
    assert len(page['entries']) == 1 and page['next_offset'] == 1
    page2 = json.loads(asyncio.run(invoke(list_files, context, path=str(code), offset=1, limit=1)))
    assert page2['entries'][0]['path'].endswith('b.txt') and page2['next_offset'] is None
    first = json.loads(asyncio.run(invoke(read_file, context, path=str(code / 'a.txt'), max_bytes=3)))
    second = json.loads(
        asyncio.run(invoke(read_file, context, path=str(code / 'a.txt'), offset=first['next_offset'], max_bytes=3))
    )
    assert first['content'] + second['content'] == 'abcdef' and second['next_offset'] is None
    assert 'test-tool-secret' not in asyncio.run(invoke(read_file, context, path=str(code / 'b.txt')))
    for path in (outside, code / 'escape.txt', code / '..' / 'private.txt'):
        assert 'outside' in asyncio.run(invoke(read_file, context, path=str(path)))
    resources = context['config']['access']['resources']
    resources[:] = [item for item in resources if item['type'] != 'code_project']  # its access: no code project now
    assert 'outside' in asyncio.run(invoke(read_file, context, path=str(code / 'a.txt')))


def test_file_input_limits_binary_files_and_literal_search(context, tmp_path):
    code = tmp_path / 'code'
    (code / 'a.py').write_text('Alpha [test]\nalpha test\nALPHA [test]\n', encoding='utf-8')
    (code / 'hidden.bin').write_bytes(b'\0binary')
    (code / 'empty.txt').touch()
    outside = tmp_path / 'private.py'
    outside.write_text('LEAK [test]', encoding='utf-8')
    (code / 'escape.py').symlink_to(outside)
    result = json.loads(asyncio.run(invoke(search_files, context, path=str(code), query='[test]', max_matches=1)))
    assert result['truncated'] and len(result['matches']) == 1 and 'a.py:1:Alpha [test]' in result['matches'][0]
    assert 'LEAK' not in json.dumps(result)
    exact = json.loads(asyncio.run(invoke(search_files, context, path=str(code), query='ALPHA', case_sensitive=True)))
    assert len(exact['matches']) == 1 and ':3:' in exact['matches'][0]
    none = json.loads(asyncio.run(invoke(search_files, context, path=str(code), query='nonexistent')))
    assert none['matches'] == [] and none['error'] is None
    assert 'binary' in asyncio.run(invoke(read_file, context, path=str(code / 'hidden.bin')))
    assert json.loads(asyncio.run(invoke(read_file, context, path=str(code / 'empty.txt'))))['next_offset'] is None
    assert 'absolute' in asyncio.run(invoke(read_file, context, path='a.py'))
    assert 'max_bytes' in asyncio.run(invoke(read_file, context, path=str(code / 'a.py'), max_bytes=100000))


def test_read_graph_line_ranges_without_loading_unrelated_source(context, tmp_path):
    path = tmp_path / 'code' / 'handlers.py'
    path.write_text('UNRELATED_BEFORE\ndef handler():\n    return "test-tool-secret"\nUNRELATED_AFTER\n')
    result = json.loads(asyncio.run(invoke(read_file, context, path=str(path), start_line=2, end_line=3)))
    assert result['start_line'] == 2 and result['end_line'] == 3
    assert '2: def handler():' in result['content']
    assert 'UNRELATED' not in result['content'] and 'test-tool-secret' not in result['content']
    for args in (
        {'start_line': 0, 'end_line': 2},
        {'start_line': 3, 'end_line': 2},
        {'start_line': 2},
        {'start_line': 2, 'end_line': 3, 'offset': 1},
    ):
        assert 'Supply start_line' in asyncio.run(invoke(read_file, context, path=str(path), **args))


def test_ssh_uses_selected_connection_and_secret_with_real_server(context, ssh_server):
    server = {
        **entry(
            'server',
            'test-server',
            'Test server',
            hostname=ssh_server.hostname,
            port=ssh_server.port,
            username=ssh_server.username,
            known_hosts=ssh_server.known_hosts,
            enabled=True,
            ssh_allowed=True,
            allow_sudo=False,
            private_key_ref={'id': 'ssh-key'},
        ),
        'source': 'given',
    }
    context['credentials']['ssh-key'] = ssh_server.private_key
    context['config']['access']['resources'].append(server)  # the tool reads the entry itself
    result = json.loads(
        asyncio.run(invoke(ssh_run, context, server_id=server['id'], command='id -un; printf diagnostic >&2; exit 3'))
    )
    assert result['exit_code'] == 3 and result['stdout'].strip() == 'operator' and result['stderr'] == 'diagnostic'
    assert 'not available' in asyncio.run(invoke(ssh_run, context, server_id='other-server', command='id'))
    assert 'not allowed' in asyncio.run(invoke(ssh_run, context, server_id=server['id'], command='id -u', as_root=True))
    server['allow_sudo'] = True
    root = json.loads(asyncio.run(invoke(ssh_run, context, server_id=server['id'], command='id -u', as_root=True)))
    assert root['exit_code'] == 0 and root['stdout'].strip() == '0'
    server['known_hosts'] = ''
    optional = json.loads(asyncio.run(invoke(ssh_run, context, server_id=server['id'], command='id -un')))
    assert optional['stdout'].strip() == 'operator'
    server['ssh_allowed'] = False  # still in the run's access: refused with the server's reason
    assert asyncio.run(invoke(ssh_run, context, server_id=server['id'], command='id')) == SSH_NOT_ALLOWED
    server['ssh_allowed'] = True
    context['credentials'].pop('ssh-key')
    assert 'credential' in asyncio.run(invoke(ssh_run, context, server_id=server['id'], command='id'))


def test_missing_document_paths_are_skipped_but_code_folders_stay_required(context, tmp_path, caplog):
    docs = str((tmp_path / 'documents').resolve())
    collection, code = of(context['config'], 'collection')[0], of(context['config'], 'code_project')[0]
    collection['paths'] = [docs, '/missing/rag-data/jira']
    with caplog.at_level('WARNING', logger='knowledge_admin.capabilities.files'):
        roots = json.loads(asyncio.run(invoke(list_files, context)))
    assert {entry['path'] for entry in roots['entries']} == {
        str(context['workspace']),
        str((tmp_path / 'code').resolve()),
        docs,
    }
    assert '/missing/rag-data/jira' in caplog.text
    code['folder_paths'].append('/missing/code')
    assert 'Configured folder is unavailable: /missing/code' in asyncio.run(invoke(list_files, context))
