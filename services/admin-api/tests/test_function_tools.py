"""Exercise actual function-tool invocation against files, processes, and Linux SSH."""

import asyncio
import json
import shlex
import sys
import time
from pathlib import Path

import pytest
from agents import Agent, FunctionTool
from agents.tool_context import ToolContext

from knowledge_admin.tools.files import list_files, read_file, search_files
from knowledge_admin.tools.shell import shell_run
from knowledge_admin.tools.ssh import ssh_run
from test_ssh import ssh_server


@pytest.fixture
def context(tmp_path):
    workspace = (tmp_path/'workspace').resolve()
    code = (tmp_path/'code').resolve()
    docs = (tmp_path/'documents').resolve()
    for directory in (workspace,code,docs):
        directory.mkdir()
    return {'workspace':workspace,'shell_enabled':False,'credentials':{'secret':'test-tool-secret'},
        'config':{'enabled':True,'code':{'enabled':True,'projects':[{'folder_paths':[str(code)]}]},
                  'documents':{'enabled':True,'folders':[{'path':str(docs)}]},
                  'ssh':{'enabled':False,'servers':[]}}}


async def invoke(tool, context, **arguments):
    payload = json.dumps(arguments)
    wrapped = ToolContext(context=context,tool_name=tool.name,tool_call_id='tool-test',tool_arguments=payload)
    return await tool.on_invoke_tool(wrapped,payload)


def test_tools_register_directly_and_hide_backend_context():
    tools = [list_files,read_file,search_files,ssh_run,shell_run]
    agent = Agent(name='Tool test',instructions='Use configured resources.',tools=tools)
    assert all(isinstance(tool,FunctionTool) for tool in agent.tools)
    for tool in tools:
        assert tool.description
        assert not {'ctx','config','credentials','workspace','shell_enabled'} & tool.params_json_schema['properties'].keys()
    assert set(ssh_run.params_json_schema['properties']) == {'server_id','command','as_root','timeout_seconds'}


def test_browse_read_pagination_redaction_and_folder_boundaries(context,tmp_path):
    code = tmp_path/'code'
    (code/'a.txt').write_text('abcdef',encoding='utf-8')
    (code/'b.txt').write_text('test-tool-secret',encoding='utf-8')
    outside = tmp_path/'private.txt'
    outside.write_text('private',encoding='utf-8')
    (code/'escape.txt').symlink_to(outside)
    roots = json.loads(asyncio.run(invoke(list_files,context)))
    assert {entry['path'] for entry in roots['entries']} == {str(context['workspace']),str(code.resolve()),str((tmp_path/'documents').resolve())}
    page = json.loads(asyncio.run(invoke(list_files,context,path=str(code),limit=1)))
    assert len(page['entries']) == 1 and page['next_offset']==1
    page2 = json.loads(asyncio.run(invoke(list_files,context,path=str(code),offset=1,limit=1)))
    assert page2['entries'][0]['path'].endswith('b.txt') and page2['next_offset'] is None
    first = json.loads(asyncio.run(invoke(read_file,context,path=str(code/'a.txt'),max_bytes=3)))
    second = json.loads(asyncio.run(invoke(read_file,context,path=str(code/'a.txt'),offset=first['next_offset'],max_bytes=3)))
    assert first['content']+second['content']=='abcdef' and second['next_offset'] is None
    assert 'test-tool-secret' not in asyncio.run(invoke(read_file,context,path=str(code/'b.txt')))
    for path in (outside,code/'escape.txt',code/'..'/'private.txt'):
        assert 'outside' in asyncio.run(invoke(read_file,context,path=str(path)))
    context['config']['code']['enabled']=False
    assert 'outside' in asyncio.run(invoke(read_file,context,path=str(code/'a.txt')))
    context['config']['enabled']=False
    assert 'disabled' in asyncio.run(invoke(list_files,context))


def test_file_input_limits_binary_files_and_literal_search(context,tmp_path):
    code = tmp_path/'code'
    (code/'a.py').write_text('Alpha [test]\nalpha test\nALPHA [test]\n',encoding='utf-8')
    (code/'hidden.bin').write_bytes(b'\0binary')
    (code/'empty.txt').touch()
    outside = tmp_path/'private.py'; outside.write_text('LEAK [test]',encoding='utf-8')
    (code/'escape.py').symlink_to(outside)
    result = json.loads(asyncio.run(invoke(search_files,context,path=str(code),query='[test]',max_matches=1)))
    assert result['truncated'] and len(result['matches'])==1 and 'a.py:1:Alpha [test]' in result['matches'][0]
    assert 'LEAK' not in json.dumps(result)
    exact = json.loads(asyncio.run(invoke(search_files,context,path=str(code),query='ALPHA',case_sensitive=True)))
    assert len(exact['matches'])==1 and ':3:' in exact['matches'][0]
    none = json.loads(asyncio.run(invoke(search_files,context,path=str(code),query='nonexistent')))
    assert none['matches']==[] and none['error'] is None
    assert 'binary' in asyncio.run(invoke(read_file,context,path=str(code/'hidden.bin')))
    assert json.loads(asyncio.run(invoke(read_file,context,path=str(code/'empty.txt'))))['next_offset'] is None
    assert 'absolute' in asyncio.run(invoke(read_file,context,path='a.py'))
    assert 'max_bytes' in asyncio.run(invoke(read_file,context,path=str(code/'a.py'),max_bytes=100000))


def test_shell_opt_in_outputs_failures_environment_and_redaction(context,monkeypatch):
    assert 'disabled' in asyncio.run(invoke(shell_run,context,command='pwd'))
    context['shell_enabled']=True
    monkeypatch.setenv('TOOL_TEST_PARENT_SECRET','must-not-leak')
    result = json.loads(asyncio.run(invoke(shell_run,context,
        command="pwd; printf '%s' \"${TOOL_TEST_PARENT_SECRET-unset}\"; printf 'test-tool-secret'; printf 'failure' >&2; exit 7")))
    assert result['exit_code']==7 and not result['timed_out']
    assert str(context['workspace']) in result['stdout'] and 'unset' in result['stdout']
    assert 'must-not-leak' not in json.dumps(result) and 'test-tool-secret' not in json.dumps(result)
    assert result['stderr']=='failure'
    big = shlex.quote(sys.executable)+" -c "+shlex.quote("print('x'*100000)")
    limited = json.loads(asyncio.run(invoke(shell_run,context,command=big)))
    assert len(limited['stdout'])==32768 and limited['truncated']['stdout']


def test_shell_timeout_and_cancellation_terminate_child_processes(context):
    context['shell_enabled']=True
    start=time.monotonic()
    result=json.loads(asyncio.run(invoke(shell_run,context,command='sleep 10',timeout_seconds=1)))
    assert result['timed_out'] and time.monotonic()-start<4
    marker=context['workspace']/'should-not-exist'
    async def cancel():
        task=asyncio.create_task(invoke(shell_run,context,command=f'(sleep 2; touch {shlex.quote(str(marker))}) & wait'))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(2.1)
    asyncio.run(cancel())
    assert not marker.exists()


def test_ssh_uses_selected_connection_and_secret_with_real_server(context,ssh_server):
    server={'id':'test-server','hostname':ssh_server.hostname,'port':ssh_server.port,
            'username':ssh_server.username,'known_hosts':ssh_server.known_hosts,
            'enabled':True,'ssh_allowed':True,'allow_sudo':False,'private_key_ref':{'id':'ssh-key'}}
    context['credentials']['ssh-key']=ssh_server.private_key
    context['config']['ssh']={'enabled':True,'servers':[server]}
    result=json.loads(asyncio.run(invoke(ssh_run,context,server_id=server['id'],command='id -un; printf diagnostic >&2; exit 3')))
    assert result['exit_code']==3 and result['stdout'].strip()=='operator' and result['stderr']=='diagnostic'
    assert 'not available' in asyncio.run(invoke(ssh_run,context,server_id='other-server',command='id'))
    assert 'not allowed' in asyncio.run(invoke(ssh_run,context,server_id=server['id'],command='id -u',as_root=True))
    server['allow_sudo']=True
    root=json.loads(asyncio.run(invoke(ssh_run,context,server_id=server['id'],command='id -u',as_root=True)))
    assert root['exit_code']==0 and root['stdout'].strip()=='0'
    server['known_hosts']=''
    optional=json.loads(asyncio.run(invoke(ssh_run,context,server_id=server['id'],command='id -un')))
    assert optional['stdout'].strip()=='operator'
    server['ssh_allowed']=False
    assert 'not available' in asyncio.run(invoke(ssh_run,context,server_id=server['id'],command='id'))
    server['ssh_allowed']=True
    context['credentials'].pop('ssh-key')
    assert 'credential' in asyncio.run(invoke(ssh_run,context,server_id=server['id'],command='id'))
