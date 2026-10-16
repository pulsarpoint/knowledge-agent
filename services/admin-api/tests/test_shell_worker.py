"""Real Linux worker and SDK function-tool checks; run in the shell test container."""

import asyncio
import json
import shutil
import socket
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import uvicorn

from knowledge_admin.capabilities.shell.worker import ShellRequest, app, execute
from knowledge_admin.agents.runner import agent
from tool_helpers import tool
from test_function_tools import context, invoke
from test_sdk_agent import config, responses_api

shell_run = tool('shell_run')

pytestmark = pytest.mark.skipif(
    shutil.which('bwrap') is None or not Path('/work/agent-workspaces').is_dir(),
    reason='Requires the Linux shell-worker container with /work test mounts.',
)


@pytest.fixture
def shell_context(context, monkeypatch):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    monkeypatch.setenv('AGENT_SHELL_URL', f'http://127.0.0.1:{listener.getsockname()[1]}')
    monkeypatch.setenv('PARENT_TEST_SECRET', 'do-not-expose')
    try:
        with TemporaryDirectory(dir='/work/agent-workspaces') as directory:
            context['workspace'] = Path(directory)
            yield context
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


def test_bash_python_native_tools_and_scratch_persistence(shell_context):
    command = """set -e
values=(bash python rg ast-grep)
printf '%s\n' "${values[@]}"
python - <<'SCRIPT'
import json, sqlite3
from pathlib import Path
Path('notes.json').write_text(json.dumps({'found': 42}))
print(sqlite3.connect(':memory:').execute('select 42').fetchone()[0])
SCRIPT
rg -o '42' notes.json
ast-grep --version
"""
    result = json.loads(asyncio.run(invoke(shell_run, shell_context, command=command)))
    assert result['exit_code'] == 0, result
    assert 'bash\npython\nrg\nast-grep' in result['stdout'] and 'ast-grep 0.39.4' in result['stdout']
    again = json.loads(
        asyncio.run(
            invoke(
                shell_run,
                shell_context,
                command="python -c \"import json; print(json.load(open('notes.json'))['found'])\"",
            )
        )
    )
    assert again['stdout'].strip() == '42'


def test_configured_source_mount_is_allowed_without_exposing_other_roots(monkeypatch):
    from fastapi import HTTPException

    with TemporaryDirectory(dir='/tmp') as directory, TemporaryDirectory(dir='/work/agent-workspaces') as scratch:
        source = Path(directory) / 'example.py'
        source.write_text('print("selected source")\n')
        request = ShellRequest(command=f'cat {source}', workspace=scratch, read_paths=[str(source)])
        with pytest.raises(HTTPException):
            asyncio.run(execute(request))
        monkeypatch.setenv('DATA_ROOT_DIR', directory)
        result = asyncio.run(execute(request))
        assert result['exit_code'] == 0 and 'selected source' in result['stdout']
        request.read_paths = ['/etc/passwd']
        with pytest.raises(HTTPException):
            asyncio.run(execute(request))


def test_selected_source_read_only_other_projects_and_network_hidden(shell_context, tmp_path):
    source = tmp_path / 'code' / 'source.py'
    source.write_text('def inspected():\n    return 42\n')
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'private.txt').write_text('UNSELECTED_PROJECT')
    command = f"""python - <<'SCRIPT'
import os, socket
from pathlib import Path
assert 'def inspected' in Path({str(source)!r}).read_text()
try:
    Path({str(source)!r}).write_text('changed')
    raise AssertionError('Source must be read-only')
except OSError:
    pass
assert not Path({str(other)!r}).exists()
assert not Path('/services').exists()
assert not Path('/var/run/docker.sock').exists()
assert not Path('/proc/1/environ').exists()
assert 'PARENT_TEST_SECRET' not in os.environ
try:
    socket.create_connection(('1.1.1.1', 443), timeout=0.3)
    raise AssertionError('Network must be unavailable')
except OSError:
    pass
print('isolated')
SCRIPT
"""
    result = json.loads(asyncio.run(invoke(shell_run, shell_context, command=command)))
    assert result['exit_code'] == 0 and result['stdout'].strip() == 'isolated', result
    assert source.read_text().endswith('42\n')


def test_errors_limits_redaction_and_disabled_access(shell_context):
    result = json.loads(
        asyncio.run(invoke(shell_run, shell_context, command="printf 'test-tool-secret'; printf 'failure' >&2; exit 7"))
    )
    assert result['exit_code'] == 7 and result['stderr'] == 'failure'
    assert 'test-tool-secret' not in json.dumps(result)
    limited = json.loads(asyncio.run(invoke(shell_run, shell_context, command='python -c "print(\'x\'*100000)"')))
    assert len(limited['stdout']) == 32768 and limited['truncated']['stdout']


def test_timeout_and_http_disconnect_stop_background_commands(shell_context):
    result = json.loads(asyncio.run(invoke(shell_run, shell_context, command='sleep 10', timeout_seconds=1)))
    assert result['timed_out']
    marker = shell_context['workspace'] / 'must-not-exist'

    async def cancel():
        task = asyncio.create_task(invoke(shell_run, shell_context, command=f'(sleep 2; touch {marker}) & wait'))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(2.2)

    asyncio.run(cancel())
    assert not marker.exists()


def test_rejects_paths_outside_worker_mounts_and_symlink_escape(shell_context, tmp_path):
    from fastapi import HTTPException

    outside = tmp_path / 'code' / 'escape'
    outside.symlink_to('/etc')
    for path in ('/etc', str(outside)):
        with pytest.raises(HTTPException) as error:
            asyncio.run(
                execute(ShellRequest(command='pwd', workspace=str(shell_context['workspace']), read_paths=[path]))
            )
        assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        asyncio.run(execute(ShellRequest(command='pwd', workspace='/tmp', read_paths=[])))


def test_sdk_calls_shell_worker_and_receives_python_result(shell_context, config, responses_api, monkeypatch):
    monkeypatch.setenv('AGENT_WORKSPACE_ROOT', '/work/agent-workspaces')
    before = set(Path('/work/agent-workspaces').iterdir())
    result = asyncio.run(
        agent(config, 'SHELL_TOOL_CYCLE', credentials={'model-key': 'fixture-api-secret'}, stream=False)
    )
    assert result.final_output == 'Configured agent completed the task.'
    output = next(item for item in responses_api[1][-1]['body']['input'] if item.get('type') == 'function_call_output')
    executed = json.loads(output['output'])
    assert executed['exit_code'] == 0 and executed['stdout'].strip() == 'SHELL_FIXTURE_RESULT'
    assert set(Path('/work/agent-workspaces').iterdir()) == before
