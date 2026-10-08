"""agent-session-worker with a fake native runtime: turns, events, the draft, tokens, history, Stop at any point,
comments delivered inside a running turn, failures, credentials, and both workers side by side."""
import asyncio
import json
import tomllib
from pathlib import Path
from uuid import UUID

import pytest
from agents import RunHooks
from agents.run_config import ModelInputData
from psycopg.types.json import Jsonb

from claude_code_fakes import FakeClient
from native_fakes import FakeAgent, call, hang, llm, message, stubborn, tool
from knowledge_admin import claude_code_worker, run_record
from knowledge_admin.agents import agent
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.history import HistorySession
from test_claude_code_runner import done, events, post, session_row, wait_for
from test_claude_code_sessions import native_agent, sessions, start  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)

KEY = 'bedrock-secret-token'  # the Claude connection's key: the native writer's model
FAILED = 'Agent execution failed (RuntimeError). Check the model connection and agent resources.'


@pytest.fixture
def native(sessions, monkeypatch):
    """A native writer with the Claude model and the Payments worker as its own code project, and a session that
    documents the Payments API code project (named in the first message) and the prod-1 server."""
    monkeypatch.setattr(run_record, '_store', sessions.store)
    monkeypatch.setattr(claude_code_worker, 'reclaim_slot', lambda session_id, slot: None)
    agent_id = native_agent(sessions.client, model_id=sessions.claude, repositories_enabled=True,
                            repository_ids=[sessions.worker])
    session = start(sessions, agent_id=agent_id, environment_ids=[], service_ids=[])
    return sessions, UUID(session['id'])


def claimed(store) -> dict:
    [row] = shared.claim(store, 'worker-1', [], 4, 'openai')
    return row


def comment(text: str) -> dict:
    return {'role': 'user', 'content': 'Comment from the user while you work: ' + text}


def inputs(store, sid) -> list[tuple]:
    with store.connect() as conn:
        return [(row['text'], row['status'], row['turn']) for row in conn.execute(
            'SELECT text,status,turn FROM ka.agent_session_inputs WHERE session_id=%s ORDER BY seq', (sid,))]


def leftover_tasks() -> list[asyncio.Task]:
    return [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


def test_native_turns_write_session_events_the_draft_tokens_and_history(native):
    sessions, sid = native
    store, url, data = sessions.store, f'/api/claude-code/sessions/{sid}', sessions.data
    with store.connect() as conn:  # a run record setting never reaches a session turn (agent() would record it)
        conn.execute('UPDATE ka.agent_sessions_cc SET agent_config=agent_config || %s WHERE id=%s',
                     (Jsonb({'record': {'run_key': 'run:x'}}), sid))
    fake = FakeAgent([
        {'steps': [llm(call('c1', 'read_file', {'path': f'{data}/code/app/main.py'}), input_tokens=100, output_tokens=10,
                       cached=40),
                   tool('c1', 'read_file', 'import app'),
                   llm(message('# Payments API'), input_tokens=120, output_tokens=30)],
         'answer': '# Payments API'},
        {'steps': [llm(message('# Payments API\n\nShort.'), input_tokens=50, output_tokens=8)],
         'answer': '# Payments API\n\nShort.'}])

    async def scenario():
        stopped = asyncio.Event()
        serving = asyncio.create_task(native_sessions.serve(store, fake, stopped=stopped, worker_id='native-1', limit=2,
                                                            poll=0.02))
        first = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        assert (first['draft'], first['draft_turn'], first['worker_id']) == ('# Payments API', 1, 'native-1')
        await post(sessions, url + '/messages', {'text': 'Shorten it.'})
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await wait_for(store, sid, lambda s: s['worker_id'] is None)
        stopped.set()
        await asyncio.wait_for(serving, 5)

    asyncio.run(scenario())
    first, second = fake.calls
    assert first['task'].startswith('Document the following.') and second['task'] == 'Shorten it.'
    assert first['history'] == [] and second['history'] == [
        {'role': 'user', 'content': first['task']}, {'role': 'assistant', 'content': '# Payments API'}]
    # No turn limits: a session turn runs until it ends or Stop.
    assert (first['turn_timeout_seconds'], first['max_turns'], first['stream']) == (None, None, False)
    assert first['credentials'] == {sessions.claude: KEY}  # decrypted in the worker, for this turn
    assert 'record' not in first['config']
    code = first['config']['code']
    assert code['enabled'] and [p['folder_paths'] for p in code['projects']] == [[f'{data}/code/worker']]
    row = session_row(store, sid)
    assert (row['status'], row['draft'], row['draft_turn'], row['input_tokens'], row['output_tokens'], row['cached_tokens'],
            row['cost_usd']) == ('closed', '# Payments API\n\nShort.', 2, 270, 48, 40, 0)
    assert row['history'] == [*second['history'], {'role': 'user', 'content': 'Shorten it.'},
                              {'role': 'assistant', 'content': '# Payments API\n\nShort.'}]
    recorded = events(store, sid)
    turn1 = [e for e in recorded if e['run_key'] == f'cc:{sid}:1']
    assert [e['kind'] for e in turn1] == ['start', 'input', 'instructions', 'tool_call', 'usage', 'tool_result',
                                          'message', 'usage', 'end']
    assert turn1[0]['payload'] == {'kind': 'native_session', 'title': 'Payments – API reference', 'agent': 'Native writer',
                                   'turn': 1}
    assert turn1[1]['payload'] == {'task': first['task']}  # no empty history from agent()'s own run record
    assert turn1[2]['payload']['text'] == 'Native prompt for Native writer'
    assert turn1[3]['payload']['summary'] == f'{data}/code/app/main.py' and turn1[5]['payload']['preview'] == 'import app'
    assert [e['payload'] for e in turn1 if e['kind'] == 'usage'] == [  # one per model call
        {'input_tokens': 100, 'output_tokens': 10, 'cached_tokens': 40, 'reasoning_tokens': 0, 'total_tokens': 110, 'cost_usd': 0.0},
        {'input_tokens': 120, 'output_tokens': 30, 'cached_tokens': 0, 'reasoning_tokens': 0, 'total_tokens': 150, 'cost_usd': 0.0}]
    assert [e['kind'] for e in recorded if e['run_key'] == f'cc:{sid}:2'] == ['start', 'input', 'message', 'usage', 'end']
    assert {e['run_key'] for e in recorded} == {f'cc:{sid}:1', f'cc:{sid}:2'}
    runs = sessions.client.get('/api/agent-runs?kind=native_session', headers=HEADERS).json()['runs']
    assert [(r['run_key'], r['status'], r['agent'], r['total_tokens']) for r in runs] == [
        (f'cc:{sid}:2', 'completed', 'Native writer', 58), (f'cc:{sid}:1', 'completed', 'Native writer', 260)]
    page = sessions.client.get(url + '/events/history', headers=HEADERS).json()['events']
    assert [e['kind'] for e in page if e['turn'] == 1][3:6] == ['tool_call', 'usage', 'tool_result']
    assert 'output' not in next(e['payload'] for e in page if e['kind'] == 'tool_result')  # the page gets the preview


def test_failed_and_stopped_turns_keep_the_history_and_draft(native):
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    fake = FakeAgent([
        {'steps': [llm(message('# Draft one'), input_tokens=10, output_tokens=5)], 'answer': '# Draft one'},
        {'steps': [llm(message('Reading more'), input_tokens=7, output_tokens=1)], 'error': RuntimeError('boom')},
        {'steps': [llm(message('Partial'), input_tokens=3, output_tokens=1)], 'answer': 'never reached'},
    ], gates={2: asyncio.Event()})

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, url + '/messages', {'text': 'Add the error codes.'})
        failed = await wait_for(store, sid, lambda s: s['status'] == 'failed' and s['turns'] == 2)
        await post(sessions, url + '/messages', {'text': 'Once more.'})
        await wait_for(store, sid, lambda s: s['status'] == 'working' and s['turns'] == 3)
        await asyncio.sleep(0.1)
        assert (await post(sessions, url + '/stop')).status_code == 202
        stopped = await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return failed, stopped

    failed, stopped = asyncio.run(scenario())
    assert failed['status_message'] == FAILED and stopped['status_message'] == shared.STOPPED
    row = session_row(store, sid)
    assert (row['draft'], row['draft_turn'], row['input_tokens'], row['output_tokens']) == ('# Draft one', 1, 20, 7)
    turn1 = [{'role': 'user', 'content': fake.calls[0]['task']}, {'role': 'assistant', 'content': '# Draft one'}]
    assert row['history'] == turn1  # the later turns' buffered items were dropped
    assert [c['history'] for c in fake.calls[1:]] == [turn1, turn1]
    recorded = events(store, sid)
    assert [e['payload']['status'] for e in recorded if e['kind'] == 'end'] == ['completed', 'failed', 'stopped']
    assert [e['payload']['message'] for e in recorded if e['kind'] == 'error'] == [FAILED]


def test_a_grounding_failure_still_finishes_the_turn_as_waiting_with_its_answer(native, monkeypatch, caplog):
    sessions, sid = native
    store = sessions.store

    def broken(result, answer):
        raise ValueError(f'bad citation near {KEY}')
    monkeypatch.setattr(native_sessions, 'grounding_of', broken)
    fake = FakeAgent([{'steps': [llm(message('# Payments API'), input_tokens=10, output_tokens=5)],
                       'answer': '# Payments API'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        done = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, f'/api/claude-code/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)
        return done

    done = asyncio.run(scenario())
    assert (done['draft'], done['draft_turn'], done['status_message']) == ('# Payments API', 1, '')
    [end] = [e['payload'] for e in events(store, sid) if e['kind'] == 'end']
    assert end == {'status': 'completed'}  # no grounding, the turn still ends
    assert 'grounding' in caplog.text and KEY not in caplog.text


def test_session_tokens_rise_after_every_model_call_and_count_once(native):
    """The session page's In/Out/Cached follow the turn live; the end of the turn adds nothing more, and a stopped turn
    keeps the tokens of its finished model calls."""
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    reached, go = [asyncio.Event() for _ in range(4)], [asyncio.Event() for _ in range(4)]

    def pause(n: int):
        async def step(turn):
            reached[n].set()
            await go[n].wait()
        return step
    fake = FakeAgent([
        {'steps': [llm(call('c1', 'read_file', {'path': 'a'}), input_tokens=100, output_tokens=10, cached=40), pause(0),
                   tool('c1', 'read_file', 'a'),
                   llm(call('c2', 'read_file', {'path': 'b'}), input_tokens=200, output_tokens=20, cached=80), pause(1),
                   tool('c2', 'read_file', 'b'),
                   llm(message('# Docs'), input_tokens=300, output_tokens=30), pause(2)],
         'answer': '# Docs'},
        {'steps': [llm(call('c3', 'read_file', {'path': 'c'}), input_tokens=7, output_tokens=3), pause(3),
                   hang('tool', asyncio.Event(), [])], 'answer': 'never reached'}])

    def tokens(row: dict) -> tuple:
        return row['input_tokens'], row['output_tokens'], row['cached_tokens']

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        seen = []
        for n in range(3):
            await asyncio.wait_for(reached[n].wait(), 5)
            row = session_row(store, sid)
            seen.append((row['status'], *tokens(row)))
            go[n].set()
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        ended = tokens(session_row(store, sid))
        await post(sessions, url + '/messages', {'text': 'More.'})
        await asyncio.wait_for(reached[3].wait(), 5)
        go[3].set()
        assert (await post(sessions, url + '/stop')).status_code == 202
        stopped = await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return seen, ended, tokens(stopped)

    seen, ended, stopped = asyncio.run(scenario())
    assert seen == [('working', 100, 10, 40), ('working', 300, 30, 120), ('working', 600, 60, 120)]
    assert ended == (600, 60, 120)  # the sum of the model calls, exactly once
    assert stopped == (607, 63, 120)
    recorded = events(store, sid)
    assert sum(e['payload']['total_tokens'] for e in recorded if e['kind'] == 'usage') == 670


@pytest.mark.parametrize('where', ['connect', 'model', 'tool'])
def test_stop_ends_the_turn_wherever_it_is_and_leaves_nothing_running(native, where):
    """Before the first model call (MCP connections), during a model request, during a slow tool call."""
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    started, cancelled = asyncio.Event(), []
    steps = [hang(where, started, cancelled)]
    if where == 'tool':
        steps = [llm(call('c1', 'shell_run', {'command': 'sleep 3600'}), input_tokens=4, output_tokens=2), *steps]
    fake = FakeAgent([{'steps': steps, 'answer': 'never reached'},
                      {'steps': [llm(message('# Docs'))], 'answer': '# Docs'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await asyncio.wait_for(started.wait(), 5)
        assert (await post(sessions, url + '/stop')).status_code == 202
        stopped = await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        assert cancelled == [where] and fake.running == 0
        await post(sessions, url + '/messages', {'text': 'Continue.'})  # the session stays usable
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        assert leftover_tasks() == []
        return stopped

    stopped = asyncio.run(scenario())
    assert stopped['status_message'] == shared.STOPPED
    assert fake.calls[1]['history'] == []  # the stopped turn's items were dropped
    row = session_row(store, sid)
    assert row['history'] == [{'role': 'user', 'content': 'Continue.'}, {'role': 'assistant', 'content': '# Docs'}]
    assert row['input_tokens'] == (4 if where == 'tool' else 0)  # a stopped turn's finished model calls count
    assert [e['payload']['status'] for e in events(store, sid) if e['kind'] == 'end'] == ['stopped', 'completed']


@pytest.mark.parametrize('attempt', ['tool', 'model'])
def test_stop_ends_the_turn_even_when_the_runtime_ignores_cancellation(native, monkeypatch, caplog, attempt):
    """After Stop and the grace, the turn left running starts no tool call and no model request, and records
    nothing."""
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    monkeypatch.setattr(native_sessions, 'STOP_GRACE_SECONDS', 0.2)
    started, ignored, release = asyncio.Event(), [], asyncio.Event()
    after = [tool('c9', 'shell_run', f'{KEY} printed'), llm(message(f'Key {KEY}'))]
    late = [stubborn(started, ignored, release), *(after if attempt == 'tool' else after[::-1])]  # after Stop
    fake = FakeAgent([{'steps': late, 'answer': f'# {KEY}'},
                      {'steps': [llm(message('# Docs'))], 'answer': '# Docs'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await asyncio.wait_for(started.wait(), 5)
        assert (await post(sessions, url + '/stop')).status_code == 202
        stopped = await wait_for(store, sid, lambda s: s['status'] == 'stopped', timeout=2)
        await post(sessions, url + '/messages', {'text': 'Continue.'})  # the session stays usable
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        assert fake.running == 1  # the stubborn turn is still running; it changes nothing when it ends
        release.set()
        await asyncio.sleep(0.2)
        assert fake.running == 0 and native_sessions._abandoned == set()
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return stopped

    stopped = asyncio.run(scenario())
    assert stopped['status_message'] == shared.STOPPED and ignored
    assert session_row(store, sid)['history'] == [{'role': 'user', 'content': 'Continue.'},
                                                  {'role': 'assistant', 'content': '# Docs'}]
    assert 'did not end within' in caplog.text and str(sid) in caplog.text
    assert (fake.calls[0]['tools'], fake.calls[0]['inputs']) == ([], [])  # neither started
    recorded = events(store, sid)
    turn1 = [e['kind'] for e in recorded if e['run_key'] == f'cc:{sid}:1']
    assert turn1 == ['start', 'input', 'instructions', 'end']  # nothing from the leftover, nothing after 'end'
    stored = json.dumps(recorded) + json.dumps(session_row(store, sid), default=str) + caplog.text
    assert KEY not in stored


def test_a_message_sent_after_stop_is_not_delivered_to_the_stopped_turn_but_starts_the_next(native, monkeypatch):
    """While a turn that ignores cancellation is in its Stop grace, a model call it still makes gets no message sent
    after Stop: the message stays queued and becomes the next turn's task."""
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    monkeypatch.setattr(native_sessions, 'STOP_GRACE_SECONDS', 5.0)
    started, ignored, release = asyncio.Event(), [], asyncio.Event()
    fake = FakeAgent([{'steps': [stubborn(started, ignored, release), llm(message('Late'))], 'answer': 'Late'},
                      {'steps': [llm(message('# Docs'))], 'answer': '# Docs'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await asyncio.wait_for(started.wait(), 5)
        assert (await post(sessions, url + '/stop')).status_code == 202
        for _ in range(250):  # the runner has seen Stop and is waiting out the grace
            if ignored:
                break
            await asyncio.sleep(0.02)
        assert ignored and session_row(store, sid)['status'] == 'working'
        await post(sessions, url + '/messages', {'text': 'Continue.'})
        release.set()  # the stopped turn makes one more model call
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    first, second = fake.calls
    assert first['inputs'] == [[{'role': 'user', 'content': first['task']}]]  # no comment in the stopped turn
    assert second['task'] == 'Continue.' and second['history'] == []
    assert inputs(store, sid)[1:] == [('Continue.', 'delivered', 2)]


def test_a_comment_reaches_the_next_model_call_of_the_running_turn_once(native):
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    started, gate = asyncio.Event(), asyncio.Event()
    fake = FakeAgent([
        {'steps': [llm(call('c1', 'shell_run', {'command': 'make docs'})),
                   tool('c1', 'shell_run', 'built', started=started, gate=gate),
                   llm(call('c2', 'read_file', {'path': 'README.md'})),
                   tool('c2', 'read_file', 'text'),
                   llm(message('# Docs'))], 'answer': '# Docs'},
        {'steps': [llm(message('# Short docs'))], 'answer': '# Short docs'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await asyncio.wait_for(started.wait(), 5)  # a slow tool call is running
        await post(sessions, url + '/messages', {'text': 'Also cover retries.'})
        gate.set()
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, url + '/messages', {'text': 'Shorten it.'})  # after the final answer: the next turn
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    first, second = fake.calls
    before, after, last = first['inputs']
    note = comment('Also cover retries.')
    assert note not in before and after[-1] == note
    assert last.count(note) == 1 and last[:len(after)] == after  # kept in place for the later model calls
    assert second['task'] == 'Shorten it.'
    assert second['history'] == [{'role': 'user', 'content': first['task']}, note,
                                 {'role': 'assistant', 'content': '# Docs'}]
    turn1 = [e for e in events(store, sid) if e['run_key'] == f'cc:{sid}:1']
    assert [e['kind'] for e in turn1] == ['start', 'input', 'instructions', 'tool_call', 'usage', 'tool_result',
                                          'input', 'tool_call', 'usage', 'tool_result', 'message', 'usage', 'end']
    assert turn1[6]['payload'] == {'task': 'Also cover retries.', 'comment': True}
    assert inputs(store, sid)[1:] == [('Also cover retries.', 'delivered', 1), ('Shorten it.', 'delivered', 2)]


def test_a_comment_delivered_to_a_failed_turn_is_delivered_again_as_the_next_turn(native):
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    started, gate = asyncio.Event(), asyncio.Event()
    fake = FakeAgent([
        {'steps': [llm(call('c1', 'shell_run', {'command': 'make docs'})),
                   tool('c1', 'shell_run', 'built', started=started, gate=gate),
                   llm(message('Almost'))], 'error': RuntimeError('provider down')},
        {'steps': [llm(message('# Docs with retries'))], 'answer': '# Docs with retries'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await asyncio.wait_for(started.wait(), 5)
        await post(sessions, url + '/messages', {'text': 'Also cover retries.'})
        gate.set()
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 2)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert fake.calls[0]['inputs'][1][-1] == comment('Also cover retries.')
    assert fake.calls[1]['task'] == 'Also cover retries.' and fake.calls[1]['history'] == []
    assert inputs(store, sid)[1:] == [('Also cover retries.', 'delivered', 2)]
    recorded = events(store, sid)
    assert [(e['run_key'], e['payload']) for e in recorded if e['kind'] == 'input'][1:] == [
        (f'cc:{sid}:1', {'task': 'Also cover retries.', 'comment': True}), (f'cc:{sid}:2', {'task': 'Also cover retries.'})]


def test_a_comment_delivered_to_a_turn_the_user_stopped_is_discarded_like_queued_messages(native):
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    started, gate, waiting, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event(), []
    fake = FakeAgent([
        {'steps': [llm(call('c1', 'shell_run', {'command': 'make docs'})),
                   tool('c1', 'shell_run', 'built', started=started, gate=gate),
                   hang('model', waiting, cancelled)], 'answer': 'never reached'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await asyncio.wait_for(started.wait(), 5)
        await post(sessions, url + '/messages', {'text': 'Also cover retries.'})
        gate.set()
        await asyncio.wait_for(waiting.wait(), 5)  # the comment went into this model call
        assert (await post(sessions, url + '/stop')).status_code == 202
        await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        await asyncio.sleep(0.2)  # no new turn starts
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert fake.calls[0]['inputs'][1][-1] == comment('Also cover retries.') and len(fake.calls) == 1
    assert inputs(store, sid)[1:] == [('Also cover retries.', 'discarded', None)]
    assert session_row(store, sid)['turns'] == 1


def test_returned_comments_are_queued_again_only_into_an_open_session_without_stop(native):
    sessions, sid = native
    store = sessions.store
    runner = native_sessions.NativeRunner(store, claimed(store), FakeAgent([]))
    assert runner.next_turn()[0] == 1
    url = f'/api/claude-code/sessions/{sid}'
    for text in ('One', 'Two'):
        sessions.client.post(url + '/messages', headers=HEADERS, json={'text': text})
    ids = []
    runner.queued_comments(1, on_delivered=lambda conn, rows: ids.extend(r['id'] for r in rows))
    runner.return_comments(1, ids[:1])  # failed turn, open session: queued again
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now() WHERE id=%s", (sid,))
    runner.return_comments(1, ids[1:])  # closed session: discarded
    assert inputs(store, sid)[1:] == [('One', 'queued', None), ('Two', 'discarded', None)]


def test_comments_are_marked_delivered_only_with_their_input_event(native):
    """queued_comments records the input event in the transaction that marks the comment delivered."""
    sessions, sid = native
    store = sessions.store
    runner = native_sessions.NativeRunner(store, claimed(store), FakeAgent([]))
    assert runner.next_turn()[0] == 1  # turn 1 is running
    sessions.client.post(f'/api/claude-code/sessions/{sid}/messages', headers=HEADERS, json={'text': 'Also cover retries.'})

    def broken(conn, delivered):
        raise RuntimeError('recording failed')

    with pytest.raises(RuntimeError):
        runner.queued_comments(1, on_delivered=broken)
    assert inputs(store, sid)[1:] == [('Also cover retries.', 'queued', None)]
    seen = []
    assert runner.queued_comments(1, on_delivered=lambda conn, rows: seen.extend(r['text'] for r in rows)) == [
        'Also cover retries.']
    assert seen == ['Also cover retries.'] and inputs(store, sid)[1:] == [('Also cover retries.', 'delivered', 1)]


def test_no_credential_reaches_events_history_the_draft_or_the_status(native, caplog):
    caplog.set_level('DEBUG')
    sessions, sid = native
    store, url = sessions.store, f'/api/claude-code/sessions/{sid}'
    fake = FakeAgent([
        {'steps': [llm(call('c1', 'shell_run', {'command': f'echo {KEY}'})),
                   tool('c1', 'shell_run', f'{KEY}\x00 printed'), llm(message(f'Key {KEY}'))],
         'answer': f'# Keys {KEY}'},
        {'steps': [], 'error': ValueError(f'Agent folder is unavailable: /work/{KEY}')},
        {'steps': [], 'error': RuntimeError(f'provider said {KEY}')}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, url + '/messages', {'text': 'Again'})
        config_error = await wait_for(store, sid, lambda s: s['status'] == 'failed' and s['turns'] == 2)
        await post(sessions, url + '/messages', {'text': 'Once more'})
        other = await wait_for(store, sid, lambda s: s['status'] == 'failed' and s['turns'] == 3)
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return config_error, other

    config_error, other = asyncio.run(scenario())
    row = session_row(store, sid)
    stored = json.dumps(events(store, sid)) + json.dumps([row, config_error, other], default=str) + caplog.text
    assert KEY not in stored and '\\u0000' not in stored
    assert row['draft'] == '# Keys [redacted]' and row['history'][-1] == {'role': 'assistant', 'content': '# Keys [redacted]'}
    assert config_error['status_message'] == 'Agent folder is unavailable: /work/[redacted]'
    assert other['status_message'] == ('Agent execution failed (RuntimeError). Check the model connection and agent '
                                       'resources.')
    [result] = [e['payload'] for e in events(store, sid) if e['kind'] == 'tool_result']
    assert result['output'] == '[redacted] printed'


def test_failure_messages():
    from cryptography.exceptions import InvalidTag
    assert native_sessions.failure(InvalidTag(), []) == native_sessions.UNREADABLE
    assert native_sessions.failure(ValueError('Line one\n  line two ' + 'x' * 600), []) == (
        'Line one line two ' + 'x' * 600)[:500]
    assert native_sessions.failure(ValueError(''), []) == (
        'Agent execution failed (ValueError). Check the model connection and agent resources.')
    assert native_sessions.failure(TimeoutError(), []) == (
        'Agent execution failed (TimeoutError). Check the model connection and agent resources.')


def test_a_disabled_model_fails_the_turn_before_the_runtime_starts(native):
    sessions, sid = native
    store = sessions.store
    with store.connect() as conn:
        conn.execute('UPDATE ka.llm_models SET enabled=false WHERE id=%s', (sessions.claude,))
    fake = FakeAgent([])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        failed = await wait_for(store, sid, lambda s: s['status'] == 'failed')
        await post(sessions, f'/api/claude-code/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)
        return failed

    failed = asyncio.run(scenario())
    assert failed['status_message'] == 'The configured model is unavailable or disabled.' and fake.calls == []


def test_both_workers_run_only_their_own_sessions(native):
    sessions, native_id = native
    store = sessions.store
    claude_id = UUID(start(sessions)['id'])
    client = FakeClient([[done('# Claude draft')]])
    fake = FakeAgent([{'steps': [llm(message('# Native draft'))], 'answer': '# Native draft'}])

    async def scenario():
        stopped = asyncio.Event()
        workers = [
            asyncio.create_task(claude_code_worker.serve(store, client, stopped=stopped, worker_id='cc-1', limit=2, poll=0.02)),
            asyncio.create_task(native_sessions.serve(store, fake, stopped=stopped, worker_id='native-1', limit=2, poll=0.02))]
        await wait_for(store, claude_id, lambda s: s['status'] == 'waiting')
        await wait_for(store, native_id, lambda s: s['status'] == 'waiting')
        stopped.set()
        await asyncio.wait_for(asyncio.gather(*workers), 5)

    asyncio.run(scenario())
    assert (session_row(store, claude_id)['draft'], session_row(store, native_id)['draft']) == ('# Claude draft', '# Native draft')
    assert (len(client.queries), len(fake.calls)) == (1, 1)
    assert [e['payload']['kind'] for e in events(store, claude_id) if e['kind'] == 'start'] == ['claude_code']
    assert [e['payload']['kind'] for e in events(store, native_id) if e['kind'] == 'start'] == ['native_session']


def test_a_turn_without_a_turn_limit_outlasts_the_request_timeout(config, responses_api):
    """turn_timeout_seconds=None: timeout_seconds limits each model request only, not a turn with slow steps; a filter
    passed to agent() changes what the model receives."""
    _, requests = responses_api

    class Slow(RunHooks):
        async def on_llm_end(self, context, agent, response):
            await asyncio.sleep(0.6)  # a slow step after the model request

    def extra(data):
        return ModelInputData(input=[*data.model_data.input, {'role': 'user', 'content': 'FILTER_COMMENT'}],
                              instructions=data.model_data.instructions)

    result = asyncio.run(agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}, timeout_seconds=0.3,
                               turn_timeout_seconds=None, max_turns=None, hooks=Slow(), stream=False,
                               call_model_input_filter=extra))
    assert result.final_output == 'Configured agent completed the task.'
    assert requests[-1]['body']['input'][-1]['content'] == 'FILTER_COMMENT'
    with pytest.raises(TimeoutError):
        asyncio.run(agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}, timeout_seconds=0.3,
                          turn_timeout_seconds=0.3, hooks=Slow(), stream=False))


def test_the_worker_script_is_installed():
    pyproject = Path(__file__).resolve().parents[1] / 'pyproject.toml'
    scripts = tomllib.loads(pyproject.read_text())['project']['scripts']
    assert scripts['knowledge-agent-session-worker'] == 'knowledge_admin.knowledge_agents.native_sessions:main'


def test_a_comment_stays_in_place_in_the_real_sdk_input_and_history(config, responses_api):
    """agent() with the Comments filter and a real tool call: the comment delivered before the second model request
    is in its input exactly once, and the turn's history holds it between the tool output and the answer."""
    _, requests = responses_api

    class Runner:
        def __init__(self):
            self.calls = 0

        def queued_comments(self, number, on_delivered=None):
            self.calls += 1
            return ['Also cover retries.'] if self.calls == 2 else []

    history = HistorySession('session', [])
    comments = native_sessions.Comments(Runner(), 1, None, history)
    result = asyncio.run(agent(config, 'FILES_TOOL_CYCLE', credentials={'model-key': 'fixture-api-secret'},
                               session=history, max_turns=None, turn_timeout_seconds=None,
                               call_model_input_filter=comments, stream=False))
    assert result.final_output == 'Configured agent completed the task.'
    first, second = (request['body']['input'] for request in requests if request['path'] == '/v1/responses')
    note = comment('Also cover retries.')
    assert note not in first and second.count(note) == 1
    assert [item.get('type') for item in second[-3:]] == ['function_call', 'function_call_output', None]
    assert second[-1] == note  # after the tool output, before the model answers
    kinds = [item.get('type') or item.get('role') for item in history.pending]
    assert kinds == ['user', 'function_call', 'function_call_output', 'user', 'message']
    assert history.pending[0]['content'] == 'FILES_TOOL_CYCLE' and history.pending[3] == note
