"""The Claude Code worker with a fake ClaudeSDKClient: turns, queued messages, Stop, Close, failures and leases."""
import asyncio
import base64
import json
import logging
import os
import time
from types import SimpleNamespace
from uuid import UUID

import pytest

from claude_code_fakes import AssistantMessage, FakeClient, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage
from knowledge_admin import claude_code_cli as cli
from knowledge_admin import claude_code_worker as worker
from knowledge_admin import run_record
from test_claude_code_sessions import sessions, start  # noqa: F401  (fixture)
from test_repositories import HEADERS

KEY = 'bedrock-secret-token'


def done(text: str, **usage) -> ResultMessage:
    return ResultMessage('success', False, 'cli-session', result=text, usage=usage or None)


@pytest.fixture
def started(sessions, monkeypatch):
    monkeypatch.setattr(run_record, '_store', sessions.store)
    return sessions, UUID(start(sessions)['id'])


def claimed(sessions, worker_id: str = 'worker-1') -> dict:
    [row] = worker.claim(sessions.store, worker_id, [], 4)
    return row


def post(sessions, path: str, body: dict | None = None):
    return asyncio.to_thread(sessions.client.post, path, headers=HEADERS, json=body)


def session_row(store, session_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.agent_sessions_cc WHERE id=%s', (session_id,)).fetchone()


async def wait_for(store, session_id, predicate, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        row = session_row(store, session_id)
        if predicate(row):
            return row
        if time.monotonic() > deadline:
            raise AssertionError(f"Session state not reached: {row['status']}, turn {row['turns']}")
        await asyncio.sleep(0.02)


def events(store, session_id) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('''SELECT run_key,seq,kind,payload FROM ka.agent_run_events WHERE starts_with(run_key,%s)
            ORDER BY id''', (f'cc:{session_id}:',)).fetchall()


def test_draft_is_the_final_answer_of_each_completed_turn(started):
    sessions, sid = started
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    client = FakeClient([
        [AssistantMessage([ToolUseBlock('t1', 'Read', {'file_path': '/work/data/code/app/main.py'})]),
         UserMessage([ToolResultBlock('t1', 'import app')]), AssistantMessage([TextBlock('# Payments API')]),
         done('# Payments API', input_tokens=10, output_tokens=5)],
        [AssistantMessage([TextBlock('Working on it')]),
         ResultMessage('error_during_execution', True, 'cli-session', result='Model overloaded')],
        [AssistantMessage([TextBlock('# Payments API\n\nShort.')]), done('# Payments API\n\nShort.', input_tokens=20, output_tokens=6)]])

    async def scenario():
        stopped = asyncio.Event()
        serving = asyncio.create_task(worker.serve(store, client, stopped=stopped, worker_id='worker-1', limit=2, poll=0.02))
        first = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        assert (first['draft'], first['draft_turn'], first['worker_id']) == ('# Payments API', 1, 'worker-1')
        await post(sessions, url + '/messages', {'text': 'Shorten it.'})
        failed = await wait_for(store, sid, lambda s: s['status'] == 'failed' and s['turns'] == 2)
        assert failed['draft'] == '# Payments API'  # a failed turn keeps the previous draft
        assert failed['status_message'].startswith('Model overloaded ')
        await post(sessions, url + '/messages', {'text': 'Try again.'})
        last = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 3)
        assert (last['draft'], last['draft_turn']) == ('# Payments API\n\nShort.', 3)
        await post(sessions, url + '/close')
        await wait_for(store, sid, lambda s: s['worker_id'] is None)
        stopped.set()
        await asyncio.wait_for(serving, 5)

    asyncio.run(scenario())
    assert client.queries[1:] == ['Shorten it.', 'Try again.']
    assert client.entered == client.exited == 1  # one client for the whole session, released on close
    row = session_row(store, sid)
    assert (row['status'], row['claude_session_id'], row['input_tokens'], row['output_tokens'], row['lease_expires_at']) == (
        'closed', 'cli-session', 30, 11, None)
    recorded = events(store, sid)
    assert [e['kind'] for e in recorded if e['run_key'] == f'cc:{sid}:1'] == [
        'start', 'instructions', 'input', 'tool_call', 'tool_result', 'message', 'usage', 'end']
    assert [e['kind'] for e in recorded if e['run_key'] == f'cc:{sid}:3'] == ['start', 'input', 'message', 'usage', 'end']
    runs = sessions.client.get('/api/agent-runs?kind=claude_code', headers=HEADERS).json()['runs']
    assert [(r['run_key'], r['status'], r['agent']) for r in runs] == [
        (f'cc:{sid}:3', 'completed', 'Technical writer'), (f'cc:{sid}:2', 'failed', 'Technical writer'),
        (f'cc:{sid}:1', 'completed', 'Technical writer')]


def test_queued_message_is_delivered_once_as_the_next_turn(started):
    sessions, sid = started
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    gate = asyncio.Event()
    client = FakeClient([[AssistantMessage([TextBlock('Reading')]), done('# Draft one')], [done('# Draft two')]], gates={0: gate})

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'working')
        for text in ('Also cover retries.', 'And the error codes.'):
            assert (await post(sessions, url + '/messages', {'text': text})).json()['working'] is True
        await asyncio.sleep(0.1)
        assert len(client.queries) == 1  # nothing reaches the agent while it responds
        gate.set()
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert client.queries[1:] == ['Also cover retries.\n\nAnd the error codes.']
    with store.connect() as conn:
        inputs = conn.execute('SELECT seq,status,turn FROM ka.agent_session_inputs WHERE session_id=%s ORDER BY seq', (sid,)).fetchall()
    assert [(i['seq'], i['status'], i['turn']) for i in inputs] == [(1, 'delivered', 1), (2, 'delivered', 2), (3, 'delivered', 2)]
    assert session_row(store, sid)['draft'] == '# Draft two'


def test_stop_interrupts_the_turn_and_discards_queued_messages(started):
    sessions, sid = started
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    client = FakeClient([[AssistantMessage([TextBlock('Partial text')]), done('never reached')], [done('# After stop')]],
                        gates={0: asyncio.Event()})  # the gate never opens: only Stop ends turn 1

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'working')
        await post(sessions, url + '/messages', {'text': 'Queued before stop'})
        assert (await post(sessions, url + '/stop')).json()['discarded'] == 1
        row = await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        assert (row['status_message'], row['draft'], row['stop_requested_at']) == (worker.STOPPED, None, None)
        await asyncio.sleep(0.1)
        assert len(client.queries) == 1  # the discarded message never starts a turn
        await post(sessions, url + '/messages', {'text': 'Continue please'})
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert (client.interrupts, client.queries[1:]) == (1, ['Continue please'])
    assert [e['payload'] for e in events(store, sid) if e['kind'] == 'end'] == [{'status': 'stopped'}, {'status': 'completed'}]
    assert (session_row(store, sid)['draft'], session_row(store, sid)['draft_turn']) == ('# After stop', 2)


def test_close_ends_the_session_and_releases_the_worker(started):
    sessions, sid = started
    store = sessions.store
    client = FakeClient([[AssistantMessage([TextBlock('Partial')]), done('never reached')]], gates={0: asyncio.Event()})

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'working')
        await post(sessions, f'/api/claude-code/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert (client.interrupts, client.entered, client.exited) == (1, 1, 1)
    row = session_row(store, sid)
    assert (row['status'], row['status_message'], row['worker_id'], row['lease_expires_at'], row['draft']) == (
        'closed', 'Closed.', None, None, None)
    assert [e['payload'] for e in events(store, sid) if e['kind'] == 'end'] == [{'status': 'stopped'}]


def test_no_key_in_events_logs_or_status(started, caplog):
    caplog.set_level(logging.DEBUG)
    sessions, sid = started
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    client = FakeClient([[RuntimeError(f'AccessDeniedException: bearer {KEY} may not invoke this model')],
                         [AssistantMessage([TextBlock(f'Never print {KEY}')]), done(f'# Draft without {KEY}')]])

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        failed = await wait_for(store, sid, lambda s: s['status'] == 'failed')
        assert failed['status_message'].endswith('Check the key and model access of this connection in LLM Settings.')
        assert KEY not in failed['status_message']
        await post(sessions, url + '/messages', {'text': 'Try again.'})
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert (client.entered, client.exited) == (2, 2)  # a failed client is dropped and opened again
    env = client.options[0]['env']
    assert (env['AWS_BEARER_TOKEN_BEDROCK'], env['CLAUDE_CODE_USE_BEDROCK'], env['AWS_REGION']) == (KEY, '1', 'eu-central-1')
    recorded = json.dumps(events(store, sid))
    row = session_row(store, sid)
    for text in (recorded, json.dumps(row, default=str), caplog.text):
        assert KEY not in text
    assert '[redacted]' in recorded and row['draft'] == '# Draft without [redacted]'


def test_key_echoed_by_a_tool_result_is_never_stored(started):
    sessions, sid = started
    store = sessions.store
    client = FakeClient([[AssistantMessage([ToolUseBlock('t1', 'Bash', {'command': 'env'})]),
                          UserMessage([ToolResultBlock('t1', [{'type': 'text', 'text': f'AWS_BEARER_TOKEN_BEDROCK={KEY}'}])]),
                          done('# Env')]])

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, f'/api/claude-code/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    [result] = [e['payload'] for e in events(store, sid) if e['kind'] == 'tool_result']
    assert result['output'] == result['preview'] == 'AWS_BEARER_TOKEN_BEDROCK=[redacted]'
    assert KEY not in json.dumps(events(store, sid))


def test_idle_session_releases_its_client_and_resumes_on_the_next_message(started):
    sessions, sid = started
    store = sessions.store
    client = FakeClient([[done('# One')], [done('# Two')]])

    async def scenario():
        await asyncio.wait_for(worker.SessionRunner(store, claimed(sessions), client, poll=0.02, idle_release=0.1).run(), 5)
        row = session_row(store, sid)
        assert (row['status'], row['worker_id']) == ('waiting', None)
        assert worker.claim(store, 'worker-2', [], 4) == []  # nothing queued: no worker needed
        await post(sessions, f'/api/claude-code/sessions/{sid}/messages', {'text': 'Shorten it.'})
        runner = worker.SessionRunner(store, claimed(sessions, 'worker-2'), client, poll=0.02, idle_release=0.1)
        await asyncio.wait_for(runner.run(), 5)

    asyncio.run(scenario())
    assert (client.entered, client.exited) == (2, 2)
    assert [options['resume'] for options in client.options] == [None, 'cli-session']
    assert session_row(store, sid)['draft'] == '# Two'


def test_expired_lease_is_reclaimed_and_the_interrupted_turn_fails(started):
    sessions, sid = started
    store = sessions.store
    first = claimed(sessions)
    assert (first['worker_id'], first['previous'], first['agent_name']) == ('worker-1', 'starting', 'Technical writer')
    assert worker.claim(store, 'worker-2', [], 4) == []  # leased by worker-1
    with store.connect() as conn:  # worker-1 started turn 1, then died without finishing it
        conn.execute("UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s", (sid,))
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working',turns=1 WHERE id=%s", (sid,))
        conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            SELECT project_id,%s,1,'start',agent_id,'{"kind": "claude_code"}' FROM ka.agent_sessions_cc WHERE id=%s''', (f'cc:{sid}:1', sid))
    assert worker.claim(store, 'worker-1', [sid], 0) == []  # the heartbeat renews worker-1's lease
    with store.connect() as conn:
        assert conn.execute("SELECT lease_expires_at > now() + interval '50 seconds' AS live FROM ka.agent_sessions_cc WHERE id=%s",
                            (sid,)).fetchone()['live']
        conn.execute("UPDATE ka.agent_sessions_cc SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (sid,))
    [row] = worker.claim(store, 'worker-2', [], 4)
    assert (row['worker_id'], row['previous'], row['status'], row['status_message']) == (
        'worker-2', 'working', 'failed', worker.INTERRUPTED)
    assert [(e['seq'], e['kind'], e['payload']) for e in events(store, sid)] == [
        (1, 'start', {'kind': 'claude_code'}), (2, 'error', {'message': worker.INTERRUPTED}), (3, 'end', {'status': 'failed'})]
    with store.connect() as conn:  # closed sessions are never claimed, even with input
        conn.execute("UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now(),lease_expires_at=NULL WHERE id=%s", (sid,))
        conn.execute('''INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text)
            SELECT id,2,actor_id,'late' FROM ka.agent_sessions_cc WHERE id=%s''', (sid,))
    assert worker.claim(store, 'worker-3', [], 4) == []


def test_worker_strips_database_and_encryption_secrets_from_its_environment(monkeypatch):
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', base64.urlsafe_b64encode(b'k' * 32).decode())
    monkeypatch.setenv('PGPASSWORD', 'db-secret')
    monkeypatch.setenv('PGHOST', 'postgres')
    key, conninfo = worker.secrets_from_environment()
    assert key == b'k' * 32 and 'db-secret' in conninfo
    assert 'CREDENTIAL_ENCRYPTION_KEY' not in os.environ and 'PGPASSWORD' not in os.environ
    assert os.environ['PGHOST'] == 'postgres'


def test_cli_wrapper_drops_to_the_claude_user_before_running_the_bundled_cli(monkeypatch):
    calls = []

    class FakeOs:
        def __init__(self, uid: int):
            self.uid = uid
            self.environ = {'PATH': '/usr/bin', 'HOME': '/home/claude', 'LC_ALL': 'C.UTF-8', 'FOO_SECRET': 'planted',
                            'PGPASSWORD': 'db-secret', 'CREDENTIAL_ENCRYPTION_KEY': 'encryption-key', 'PGHOST': 'postgres',
                            'AUTH_ISSUER_URL': 'http://auth', 'AWS_BEARER_TOKEN_BEDROCK': KEY, 'AWS_REGION': 'eu-central-1',
                            'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_ENTRYPOINT': 'sdk-py',
                            'CLAUDE_AGENT_SDK_VERSION': '0.1', 'ANTHROPIC_SMALL_FAST_MODEL': 'm'}

        def getuid(self):
            return self.uid

        def setgroups(self, groups):
            calls.append(('setgroups', groups))

        def setgid(self, gid):
            calls.append(('setgid', gid))

        def setuid(self, uid):
            calls.append(('setuid', uid))
            self.uid = uid

        def execve(self, path, argv, env):
            calls.append(('execve', path, argv, env))

    claude = SimpleNamespace(pw_uid=1001, pw_gid=1001)
    monkeypatch.setattr(cli.pwd, 'getpwnam', lambda name: claude if name == 'claude' else None)
    monkeypatch.setattr(cli, 'bundled_cli', lambda: '/venv/claude_agent_sdk/_bundled/claude')
    cli.main(['--output-format', 'stream-json'], os_module=FakeOs(0))
    assert calls == [('setgroups', []), ('setgid', 1001), ('setuid', 1001),
                     ('execve', '/venv/claude_agent_sdk/_bundled/claude',
                      ['/venv/claude_agent_sdk/_bundled/claude', '--output-format', 'stream-json'], {
                          'PATH': '/usr/bin', 'HOME': '/home/claude', 'LC_ALL': 'C.UTF-8', 'AWS_BEARER_TOKEN_BEDROCK': KEY,
                          'AWS_REGION': 'eu-central-1', 'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_ENTRYPOINT': 'sdk-py',
                          'CLAUDE_AGENT_SDK_VERSION': '0.1', 'ANTHROPIC_SMALL_FAST_MODEL': 'm'})]  # only the allowlist
    with pytest.raises(SystemExit):  # neither root nor claude: refuse instead of running as another user
        cli.main([], os_module=FakeOs(501))
    monkeypatch.setattr(cli.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=0, pw_gid=0))
    with pytest.raises(SystemExit):
        cli.main([], os_module=FakeOs(0))
