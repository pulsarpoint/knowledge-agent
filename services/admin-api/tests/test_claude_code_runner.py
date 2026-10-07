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
PROXY_AND_CA = {name: 'x' for name in (
    'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy', 'NODE_EXTRA_CA_CERTS',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'USER', 'LOGNAME', 'TERM', 'SHELL')}


def done(text: str, **usage) -> ResultMessage:
    return ResultMessage('success', False, 'cli-session', result=text, usage=usage or None)


RECLAIMED: list[tuple] = []


@pytest.fixture
def started(sessions, monkeypatch):
    monkeypatch.setattr(run_record, '_store', sessions.store)
    RECLAIMED.clear()
    monkeypatch.setattr(worker, 'reclaim_slot', lambda session_id, slot: RECLAIMED.append((session_id, slot)))
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


def test_null_characters_in_tool_output_and_draft_do_not_fail_the_turn(started):
    sessions, sid = started
    store = sessions.store
    client = FakeClient([[AssistantMessage([ToolUseBlock('t1', 'Bash', {'command': 'head -c 64 app.bin'})]),
                          UserMessage([ToolResultBlock('t1', 'ELF\x00\x01binary')]), done('# Binary\x00 files')]])

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        row = await wait_for(store, sid, lambda s: s['status'] in ('waiting', 'failed') and s['turns'] == 1)
        await post(sessions, f'/api/claude-code/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)
        return row

    row = asyncio.run(scenario())
    assert (row['status'], row['draft']) == ('waiting', '# Binary files')
    [result] = [e['payload'] for e in events(store, sid) if e['kind'] == 'tool_result']
    assert result['output'] == 'ELF\x01binary'


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


class LostClient(FakeClient):
    """Its interrupt() is ignored: the turn only ends when the worker gives up waiting."""

    async def interrupt(self) -> None:
        self.interrupts += 1


def test_stuck_interrupt_falls_back_to_dropping_the_client(started, monkeypatch):
    monkeypatch.setattr(worker, 'INTERRUPT_GRACE_SECONDS', 0.1)
    sessions, sid = started
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    client = LostClient([[AssistantMessage([TextBlock('Partial')]), done('never reached')]], gates={0: asyncio.Event()})

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'working')
        await post(sessions, url + '/stop')
        row = await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        assert row['status_message'] == worker.STOPPED
        assert (client.entered, client.exited) == (1, 1)  # the unresponsive client was dropped
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert client.interrupts == 1
    assert [e['payload'] for e in events(store, sid) if e['kind'] == 'end'] == [{'status': 'stopped'}]


def test_a_runner_that_lost_its_lease_stops_without_touching_the_session(started):
    sessions, sid = started
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    client = FakeClient([[AssistantMessage([TextBlock('Partial')]), done('never reached')], [done('# Not mine')]],
                        gates={0: asyncio.Event()})

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'working')
        with store.connect() as conn:  # worker-1 looked dead; worker-2 took the session over
            conn.execute("""UPDATE ka.agent_sessions_cc SET worker_id='worker-2',lease_expires_at=now()+interval '1 minute',
                status='failed',status_message='owned by worker-2' WHERE id=%s""", (sid,))
        await post(sessions, url + '/messages', {'text': 'For worker-2'})
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert (client.interrupts, client.queries[1:], client.entered, client.exited) == (1, [], 1, 1)
    row = session_row(store, sid)
    assert (row['worker_id'], row['status'], row['status_message'], row['turns'], row['draft']) == (
        'worker-2', 'failed', 'owned by worker-2', 1, None)
    with store.connect() as conn:
        assert conn.execute("SELECT status FROM ka.agent_session_inputs WHERE session_id=%s AND seq=2",
                            (sid,)).fetchone()['status'] == 'queued'  # left for the new owner


def test_serve_leaves_a_reclaimed_interrupted_session_until_a_message_arrives(started):
    sessions, sid = started
    store = sessions.store
    with store.connect() as conn:  # a dead worker left turn 1 working, with nothing queued
        conn.execute("UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s", (sid,))
        conn.execute("""UPDATE ka.agent_sessions_cc SET status='working',turns=1,worker_id='dead',
            lease_expires_at=now()-interval '1 second' WHERE id=%s""", (sid,))
    client = FakeClient([])

    async def scenario():
        stopped = asyncio.Event()
        serving = asyncio.create_task(worker.serve(store, client, stopped=stopped, worker_id='worker-1', limit=2, poll=0.02))
        row = await wait_for(store, sid, lambda s: s['status'] == 'failed')
        await asyncio.sleep(0.1)
        current = session_row(store, sid)
        assert (current['worker_id'], current['lease_expires_at']) == (None, None)  # released at once, not held by a runner
        stopped.set()
        await asyncio.wait_for(serving, 5)
        return row

    row = asyncio.run(scenario())
    assert row['status_message'] == worker.INTERRUPTED
    assert client.entered == 0  # no runner started: the session waits for a message


def test_worker_strips_database_and_encryption_secrets_from_its_environment(monkeypatch):
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', base64.urlsafe_b64encode(b'k' * 32).decode())
    monkeypatch.setenv('PGPASSWORD', 'db-secret')
    monkeypatch.setenv('PGHOST', 'postgres')
    key, conninfo = worker.secrets_from_environment()
    assert key == b'k' * 32 and 'db-secret' in conninfo
    assert 'CREDENTIAL_ENCRYPTION_KEY' not in os.environ and 'PGPASSWORD' not in os.environ
    assert os.environ['PGHOST'] == 'postgres'


HOME = '/home/claude/sessions/0b5d2c3e-1f2a-4b5c-8d9e-0f1a2b3c4d5e'


def test_cli_wrapper_drops_to_the_slot_user_before_running_the_bundled_cli(monkeypatch):
    calls = []

    class FakeOs:
        def __init__(self, uid: int, keep_gid: bool = False, **env):
            self.uid, self.gid, self.keep_gid = uid, uid, keep_gid
            self.environ = {**PROXY_AND_CA, 'PATH': '/usr/bin', 'HOME': HOME, 'LC_ALL': 'C.UTF-8', 'FOO_SECRET': 'planted',
                            'PGPASSWORD': 'db-secret', 'CREDENTIAL_ENCRYPTION_KEY': 'encryption-key', 'PGHOST': 'postgres',
                            'AUTH_ISSUER_URL': 'http://auth', 'AWS_BEARER_TOKEN_BEDROCK': KEY, 'AWS_REGION': 'eu-central-1',
                            'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CODE_ENTRYPOINT': 'sdk-py', 'TMPDIR': '/tmp',
                            'CLAUDE_AGENT_SDK_VERSION': '0.1', 'ANTHROPIC_SMALL_FAST_MODEL': 'm', 'KNOWLEDGE_CLAUDE_SLOT': '2', **env}

        def getuid(self):
            return self.uid

        def getgid(self):
            return self.gid

        def setgroups(self, groups):
            calls.append(('setgroups', groups))

        def setgid(self, gid):
            calls.append(('setgid', gid))
            if not self.keep_gid:
                self.gid = gid

        def setuid(self, uid):
            calls.append(('setuid', uid))
            self.uid = uid

        def kill(self, pid, sig):
            calls.append(('kill', pid, sig, self.uid))

        def execve(self, path, argv, env):
            calls.append(('execve', path, argv, env))

    slot = SimpleNamespace(pw_uid=1002, pw_gid=1001)
    monkeypatch.setattr(cli.pwd, 'getpwnam', lambda name: slot if name == 'claude2' else None)
    monkeypatch.setattr(cli, 'bundled_cli', lambda: '/venv/claude_agent_sdk/_bundled/claude')
    monkeypatch.setattr(cli, 'prepare_home', lambda home, uid, gid, os_module: calls.append(('home', home, uid, gid)))
    monkeypatch.setattr(cli, 'kill_slot', lambda uid, gid: calls.append(('kill-slot', uid, gid)))
    cli.main(['--output-format', 'stream-json'], os_module=FakeOs(0))
    assert calls == [('kill-slot', 1002, 1001),  # leftovers die before the HOME is handed to the slot user
                     ('home', HOME, 1002, 1001), ('setgroups', []), ('setgid', 1001), ('setuid', 1002),
                     ('kill', -1, cli.signal.SIGKILL, 1002),  # a second guard, as the slot user, never as root
                     ('execve', '/venv/claude_agent_sdk/_bundled/claude',
                      ['/venv/claude_agent_sdk/_bundled/claude', '--output-format', 'stream-json'], {
                          **PROXY_AND_CA, 'PATH': '/usr/bin', 'HOME': HOME, 'TMPDIR': HOME + '/tmp', 'LC_ALL': 'C.UTF-8',
                          'AWS_BEARER_TOKEN_BEDROCK': KEY, 'AWS_REGION': 'eu-central-1', 'CLAUDE_CODE_USE_BEDROCK': '1',
                          'CLAUDE_CODE_ENTRYPOINT': 'sdk-py', 'CLAUDE_AGENT_SDK_VERSION': '0.1',
                          'ANTHROPIC_SMALL_FAST_MODEL': 'm'})]  # only the allowlist, without the slot
    with pytest.raises(SystemExit):  # neither root nor the slot user: refuse instead of running as another user
        cli.main([], os_module=FakeOs(501))
    with pytest.raises(SystemExit, match='group'):  # the gid did not change: refuse
        cli.main([], os_module=FakeOs(0, keep_gid=True))
    for env in ({'KNOWLEDGE_CLAUDE_SLOT': ''}, {'KNOWLEDGE_CLAUDE_SLOT': '9'}, {'KNOWLEDGE_CLAUDE_SLOT': '0'},
                {'HOME': '/home/claude'}, {'HOME': '/home/claude/sessions/../x'}, {'HOME': '/root'}):
        calls.clear()
        with pytest.raises(SystemExit, match='slot|HOME'):
            cli.main([], os_module=FakeOs(0, **env))
        assert calls == []
    monkeypatch.setattr(cli.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=0, pw_gid=0))
    with pytest.raises(SystemExit):
        cli.main([], os_module=FakeOs(0))

    def missing(name):
        raise KeyError(name)
    monkeypatch.setattr(cli.pwd, 'getpwnam', missing)
    with pytest.raises(SystemExit, match='The claude2 user does not exist'):
        cli.main([], os_module=FakeOs(0))


def test_concurrent_sessions_run_in_different_slots_and_keep_their_home(started):
    sessions, first = started
    second = UUID(start(sessions)['id'])
    store = sessions.store
    clients: list[FakeClient] = []

    def factory(options):
        client = FakeClient([[done('# Draft')], [done('# Again')]])
        clients.append(client)
        return client(options)

    async def scenario():
        stopped = asyncio.Event()
        serving = asyncio.create_task(worker.serve(store, factory, stopped=stopped, worker_id='worker-1', limit=20, poll=0.02,
                                                   idle_release=0.2))
        for sid in (first, second):
            await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await wait_for(store, first, lambda s: s['worker_id'] is None)  # idle: its slot is free again
        await post(sessions, f'/api/claude-code/sessions/{first}/messages', {'text': 'Again'})
        await wait_for(store, first, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        stopped.set()
        await asyncio.wait_for(serving, 5)

    asyncio.run(scenario())
    envs = [client.options[0]['env'] for client in clients]
    assert sorted(env['HOME'] for env in envs[:2]) == sorted(f'/home/claude/sessions/{sid}' for sid in (first, second))
    assert envs[2]['HOME'] == f'/home/claude/sessions/{first}'  # the transcript stays with the session in any slot
    assert envs[0]['KNOWLEDGE_CLAUDE_SLOT'] != envs[1]['KNOWLEDGE_CLAUDE_SLOT']  # one uid per running session
    assert {env['KNOWLEDGE_CLAUDE_SLOT'] for env in envs} <= {str(n) for n in range(1, 9)}  # at most 8 slots
    assert clients[2].options[0]['resume'] == 'cli-session'
    assert {session for session, _ in RECLAIMED} == {first, second}  # each release took the slot and HOME back
    first_slot = envs[0]['KNOWLEDGE_CLAUDE_SLOT'] if envs[0]['HOME'].endswith(str(first)) else envs[1]['KNOWLEDGE_CLAUDE_SLOT']
    assert (first, int(first_slot)) in RECLAIMED


def test_a_slot_whose_home_cannot_be_taken_back_is_not_reused(started, monkeypatch):
    sessions, first = started
    store = sessions.store
    clients: list[FakeClient] = []

    def refuse(session_id, slot):
        raise OSError('busy')
    monkeypatch.setattr(worker, 'reclaim_slot', refuse)

    def factory(options):
        client = FakeClient([[done('# Draft')], [done('# Again')]])
        clients.append(client)
        return client(options)

    async def scenario():
        stopped = asyncio.Event()
        serving = asyncio.create_task(worker.serve(store, factory, stopped=stopped, worker_id='worker-1', limit=1, poll=0.02,
                                                   idle_release=0.1))
        await wait_for(store, first, lambda s: s['status'] == 'waiting' and s['worker_id'] is None)
        await post(sessions, f'/api/claude-code/sessions/{first}/messages', {'text': 'Again'})
        await asyncio.sleep(0.4)
        stopped.set()
        await asyncio.wait_for(serving, 5)

    asyncio.run(scenario())
    assert len(clients) == 1  # its only slot stayed out of use


def test_every_enabled_claude_connection_key_is_redacted(started):
    sessions, sid = started
    store, client_api = sessions.store, sessions.client
    other = 'sk-ant-other-connection'
    created = client_api.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Claude API', 'provider': 'anthropic', 'model': 'claude-sonnet-4-5', 'api_format': 'native',
        'base_url': 'https://api.anthropic.com', 'api_key': other})
    assert created.status_code == 201, created.text
    client = FakeClient([[AssistantMessage([ToolUseBlock('t1', 'Bash', {'command': 'cat /proc/*/environ'})]),
                          UserMessage([ToolResultBlock('t1', f'AWS_BEARER_TOKEN_BEDROCK={KEY} ANTHROPIC_API_KEY={other}')]),
                          done(f'# Keys {other}')],
                         [RuntimeError(f'failed with {other}')]])

    async def scenario():
        runner = asyncio.create_task(worker.SessionRunner(store, claimed(sessions), client, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, f'/api/claude-code/sessions/{sid}/messages', {'text': 'Again'})
        failed = await wait_for(store, sid, lambda s: s['status'] == 'failed' and s['turns'] == 2)
        await post(sessions, f'/api/claude-code/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)
        return failed

    failed = asyncio.run(scenario())
    row = session_row(store, sid)
    stored = json.dumps(events(store, sid)) + json.dumps([row, failed], default=str)
    assert KEY not in stored and other not in stored
    assert row['draft'] == '# Keys [redacted]' and failed['status_message'].startswith('RuntimeError: failed with [redacted]')
    [result] = [e['payload'] for e in events(store, sid) if e['kind'] == 'tool_result']
    assert result['output'] == 'AWS_BEARER_TOKEN_BEDROCK=[redacted] ANTHROPIC_API_KEY=[redacted]'
