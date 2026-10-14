"""run_subagent at run time: the Subagents of one native turn (parallel limit, 16 per turn, closed turns, failures,
credentials, events in the parent's turn record with parent_call_id), the adapter's call id, and the worker running
subagents in a session (one record, session totals, Stop)."""

import asyncio
import dataclasses
import gc
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import RunHooks
from agents.tool_context import ToolContext

from native_fakes import FakeAgent, FakeSubagent, Recorder, call, delegate, hang, llm, message, stubborn, together, tool
from knowledge_admin.sessions import tracing as agent_tracing, run_record
from knowledge_admin.sessions.activity import tool_failed
from knowledge_admin.sessions.tracing import trace_secrets
from knowledge_admin.agents.runner import agent
from knowledge_admin.capabilities.context import TOOL_CALL_ID
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents.native_sessions import SessionEvents
from knowledge_admin.knowledge_agents.subagents import FAILED, LIMIT, NOT_OFFERED, PROMPT, Subagents, run_subagent
from knowledge_admin.sessions.run_record import RunRecorder
from knowledge_admin.tools.catalog import CATALOG, item
from knowledge_admin.tools.sdk import function_tool
from run_helpers import events as run_events, post, session_row, wait_for
from test_generations import sessions  # noqa: F401  (fixture)
from test_native_sessions import KEY, claimed, leftover_tasks, native  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)
from test_subagent_config import CREDENTIALS, MCP, NO_RESOURCES, OFFERED, parent

SECRET = CREDENTIALS['model-key']
SCRATCH = Path('/work/agent-workspaces/lead')


def host(turns: dict, *, limit: int = 4, usage: list | None = None):
    """Subagents of a lead turn whose events record into a fake recorder; usage collects record_usage calls."""
    recorder = Recorder()
    lead = SessionEvents(recorder, secrets=list(CREDENTIALS.values()), first=False)
    fake = FakeSubagent(turns)
    subagents = Subagents(
        lead,
        secrets=list(CREDENTIALS.values()),
        run_agent=fake,
        limit=limit,
        record_usage=usage.append if usage is not None else None,
    )
    return subagents, fake, recorder, lead


async def start(subagents, call_id, task, tools=('read_file',), resources=None):
    """The run_subagent tool as the adapter runs it for one call."""
    context = {
        'config': parent(),
        'credentials': dict(CREDENTIALS),
        'workspace': SCRATCH,
        'shell_enabled': True,
        'tools': OFFERED,
        'mcp_tools': MCP,
        'subagents': subagents,
    }
    token = TOOL_CALL_ID.set(call_id)
    try:
        return await run_subagent(context, task, list(tools), resources)
    finally:
        TOOL_CALL_ID.reset(token)


def test_a_subagent_runs_its_task_with_its_configuration_and_is_recorded_in_the_turn():
    usage: list[dict] = []
    subagents, fake, recorder, _ = host(
        {
            'Map the worker': {
                'steps': [
                    llm(call('r1', 'read_file', {'path': '/code/worker/main.py'}), input_tokens=40, output_tokens=5),
                    tool('r1', 'read_file', 'def main(): ...'),
                    llm(message(f'The worker starts in main.py ({SECRET}).'), input_tokens=60, output_tokens=12),
                ],
                'answer': f'The worker starts in main.py ({SECRET}).',
            }
        },
        usage=usage,
    )
    answer = asyncio.run(
        start(subagents, 's1', 'Map the worker', resources={**NO_RESOURCES, 'code_project_ids': ['worker']})
    )
    assert answer == 'The worker starts in main.py ([redacted]).'
    [run] = fake.calls
    assert run['config']['system_prompt'] == {'content': PROMPT}
    assert [p['id'] for p in run['config']['code']['projects']] == ['worker']
    assert run['credentials'] == {'model-key': SECRET}  # only what its configuration references
    assert (run['workspace'], run['max_turns'], run['turn_timeout_seconds'], run['stream']) == (
        SCRATCH,
        None,
        None,
        False,
    )
    assert run['filter'] == subagents.model_filter
    assert [kind for kind, _ in recorder.events] == [
        'input',
        'instructions',
        'tool_call',
        'usage',
        'tool_result',
        'message',
        'usage',
    ]
    assert recorder.events[0] == (
        'input',
        {'task': 'Map the worker', 'subagent': {'task': 'Map the worker'}, 'parent_call_id': 's1'},
    )
    assert recorder.events[1][1]['text'] == PROMPT
    assert all(payload['parent_call_id'] == 's1' for _, payload in recorder.events)
    assert [each['input_tokens'] for each in usage] == [40, 60]  # every model call reaches the session's totals
    assert subagents.started == 1 and recorder.flushes >= 1


def test_at_most_the_limit_run_at_once_and_later_ones_wait():
    gates = {f'Task {n}': asyncio.Event() for n in range(5)}
    running: list[str] = []

    def script(task):
        async def work(turn):
            running.append(task)
            await gates[task].wait()

        return {'steps': [work], 'answer': task}

    subagents, _, _, _ = host({task: script(task) for task in gates}, limit=4)

    async def scenario():
        calls = [asyncio.create_task(start(subagents, f'c{n}', f'Task {n}')) for n in range(5)]
        while len(running) < 4:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert sorted(running) == ['Task 0', 'Task 1', 'Task 2', 'Task 3']  # the fifth waits for a slot
        gates['Task 0'].set()
        while len(running) < 5:
            await asyncio.sleep(0.01)
        for gate in gates.values():
            gate.set()
        return await asyncio.gather(*calls)

    assert asyncio.run(asyncio.wait_for(scenario(), 5)) == [f'Task {n}' for n in range(5)]


def test_a_turn_starts_at_most_16_subagents_and_refused_calls_do_not_count():
    subagents, fake, _, _ = host({f'Task {n}': {'steps': [], 'answer': 'ok'} for n in range(17)}, limit=32)

    async def scenario():
        with pytest.raises(ValueError) as refused:
            await start(subagents, 'bad', 'Task 0', tools=('ast_grep',))
        assert str(refused.value) == NOT_OFFERED.format(name='ast_grep')
        return await asyncio.gather(
            *(start(subagents, f'c{n}', f'Task {n}') for n in range(17)), return_exceptions=True
        )

    results = asyncio.run(scenario())
    assert len(fake.calls) == 16 and subagents.started == 16
    assert [str(result) for result in results if isinstance(result, ValueError)] == [LIMIT]
    assert LIMIT == 'This turn already started 16 helpers.'  # subagents and expert calls together


def test_a_failed_subagent_returns_a_redacted_failure_and_is_recorded():
    subagents, _, recorder, _ = host(
        {
            'Check prod': {'steps': [], 'error': ValueError(f'SSH key {SECRET} refused')},
            'Crash': {'steps': [], 'error': RuntimeError(SECRET)},
        }
    )

    async def scenario():
        return [await start(subagents, 's1', 'Check prod'), await start(subagents, 's2', 'Crash')]

    assert asyncio.run(scenario()) == [
        FAILED.format(message='SSH key [redacted] refused'),
        FAILED.format(message='Agent execution failed (RuntimeError). Check the model connection and agent resources.'),
    ]
    errors = [payload for kind, payload in recorder.events if kind == 'error']
    assert [error['parent_call_id'] for error in errors] == ['s1', 's2'] and SECRET not in json.dumps(errors)
    assert tool_failed(FAILED.format(message='boom')) and not tool_failed('Subagent found the bug.')


def test_a_closed_turn_starts_no_subagent_model_call_tool_call_or_subagent():
    for task, late in (('Model', llm(message('late'))), ('Tool', tool('r1', 'read_file', 'late'))):
        subagents, fake, recorder, lead = host({})

        async def stop(turn, lead=lead):
            lead.closed = True  # Stop, Close or the end of the turn while the subagent works

        fake.turns[task] = {'steps': [stop, late], 'answer': 'late'}
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(start(subagents, 's1', task))
        assert [kind for kind, _ in recorder.events] == ['input', 'instructions'], task
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(start(subagents, 's2', task))  # a call after the turn closed starts nothing
        assert (len(fake.calls), subagents.started) == (1, 1)


def test_the_adapter_publishes_the_call_id_to_the_tool():
    seen = []

    async def remember(context, arguments):
        seen.append(TOOL_CALL_ID.get())
        return 'ok'

    read = function_tool(dataclasses.replace(CATALOG['read_file'], run=remember), CATALOG['read_file'].usage)
    payload = json.dumps({'path': '/a.txt'})
    wrapped = ToolContext(
        context={'tools': frozenset({'read_file'}), 'credentials': {}},
        tool_name='read_file',
        tool_call_id='call-7',
        tool_arguments=payload,
    )
    assert asyncio.run(read.on_invoke_tool(wrapped, payload)) == 'ok'
    assert seen == ['call-7'] and TOOL_CALL_ID.get() is None


def test_a_run_can_share_a_scratch_workspace_and_carries_the_turns_subagents(config, responses_api, tmp_path):
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    seen = {}

    class Capture(RunHooks):
        async def on_agent_start(self, context, agent):
            seen.update(workspace=context.context['workspace'], subagents=context.context['subagents'])

    marker = object()
    asyncio.run(
        agent(
            config,
            'Describe the tools.',
            credentials={'model-key': 'fixture-api-secret'},
            hooks=Capture(),
            workspace=scratch,
            subagents=marker,
            stream=False,
        )
    )
    assert seen == {'workspace': scratch.resolve(), 'subagents': marker}
    assert scratch.is_dir()  # the parent's workspace outlives the subagent's run


def test_a_session_turn_records_its_subagents_in_one_record_and_counts_their_usage(native):
    sessions, sid = native
    store = sessions.store
    results: list[str] = []
    subagent = FakeSubagent(
        {
            'Read the worker': {
                'steps': [
                    llm(call('r1', 'read_file', {'path': 'w.py'}), input_tokens=30, output_tokens=3),
                    tool('r1', 'read_file', f'token {KEY}'),
                    llm(message('Worker reads w.py.'), input_tokens=20, output_tokens=4),
                ],
                'answer': 'Worker reads w.py.',
            },
            'Read the app': {
                'steps': [llm(message('App reads a.py.'), input_tokens=10, output_tokens=2)],
                'answer': 'App reads a.py.',
            },
        }
    )
    fake = FakeAgent(
        [
            {
                'offered': ['read_file', 'run_subagent'],
                'steps': [
                    llm(
                        call(
                            's1', 'run_subagent', {'task': 'Read the worker', 'tools': ['read_file'], 'resources': None}
                        ),
                        call('s2', 'run_subagent', {'task': 'Read the app', 'tools': ['read_file'], 'resources': None}),
                        input_tokens=100,
                        output_tokens=10,
                    ),
                    together(
                        delegate('s1', 'Read the worker', ['read_file'], results=results),
                        delegate('s2', 'Read the app', ['read_file'], results=results),
                    ),
                    llm(message('Both read.'), input_tokens=50, output_tokens=5),
                ],
                'answer': 'Both read.',
            }
        ]
    )

    async def scenario():
        runner = asyncio.create_task(
            native_sessions.NativeRunner(store, claimed(store), fake, run_subagent=subagent, poll=0.02).run()
        )
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, f'/api/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert sorted(results) == ['App reads a.py.', 'Worker reads w.py.']
    assert subagent.calls[0]['credentials'] == {sessions.claude: KEY}  # the turn's own, not decrypted again
    recorded = [e for e in run_events(store, sid) if e['run_key'] == f'cc:{sid}:1']
    assert [e['seq'] for e in recorded] == list(range(1, len(recorded) + 1))  # one record, every event once, in order
    by_call = {cid: [e['kind'] for e in recorded if e['payload'].get('parent_call_id') == cid] for cid in ('s1', 's2')}
    assert by_call == {
        's1': ['input', 'instructions', 'tool_call', 'usage', 'tool_result', 'message', 'usage'],
        's2': ['input', 'instructions', 'message', 'usage'],
    }
    assert [e['kind'] for e in recorded if 'parent_call_id' not in e['payload']][-1] == 'end'
    assert [
        e['payload']['summary'] for e in recorded if e['kind'] == 'tool_call' and e['payload']['name'] == 'run_subagent'
    ] == ['Read the worker', 'Read the app']
    assert KEY not in json.dumps(recorded, default=str)
    row = session_row(store, sid)
    assert (row['input_tokens'], row['output_tokens'], row['model_calls']) == (210, 24, 5)  # subagents included
    detail = sessions.client.get(f'/api/sessions/{sid}', headers=HEADERS).json()
    assert {count['name']: count['calls'] for count in detail['tool_counts']} == {'run_subagent': 2, 'read_file': 1}


def test_stop_cancels_a_running_subagent_and_leaves_nothing_running(native):
    sessions, sid = native
    store, url = sessions.store, f'/api/sessions/{sid}'
    started, cancelled = asyncio.Event(), []
    subagent = FakeSubagent({'Watch the queue': {'steps': [hang('tool', started, cancelled)], 'answer': 'never'}})
    fake = FakeAgent(
        [
            {
                'offered': ['read_file'],
                'steps': [
                    llm(
                        call(
                            's1', 'run_subagent', {'task': 'Watch the queue', 'tools': ['read_file'], 'resources': None}
                        )
                    ),
                    delegate('s1', 'Watch the queue', ['read_file']),
                ],
                'answer': 'never',
            }
        ]
    )

    async def scenario():
        runner = asyncio.create_task(
            native_sessions.NativeRunner(store, claimed(store), fake, run_subagent=subagent, poll=0.02).run()
        )
        await asyncio.wait_for(started.wait(), 5)
        assert (await post(sessions, url + '/stop')).status_code == 202
        await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return leftover_tasks()

    assert asyncio.run(scenario()) == []
    assert cancelled == ['tool']
    recorded = run_events(store, sid)
    assert [e['payload'].get('parent_call_id') for e in recorded if e['kind'] == 'input'] == [None, 's1']
    assert [e['payload']['status'] for e in recorded if e['kind'] == 'end'] == ['stopped']


def test_closing_the_turns_subagents_cancels_them_and_clears_their_credentials(monkeypatch):
    """Stop or the end of the turn (run_turn calls close() before the events close): running subagents are cancelled,
    one that ignores cancellation is waited for at most STOP_GRACE_SECONDS, every credential map handed out is emptied,
    and no later call starts a subagent."""
    monkeypatch.setattr(native_sessions, 'STOP_GRACE_SECONDS', 0.2)
    issued: list[dict] = []
    cancelled: list[str] = []

    async def scenario():
        release = asyncio.Event()

        async def run_agent(config, task, *, credentials, **_):
            issued.append(credentials)
            if task == 'Stubborn':  # swallows cancellation and keeps working until released
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        cancelled.append(task)
                return SimpleNamespace(final_output='late')
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(task)
                raise

        lead = SessionEvents(Recorder(), secrets=list(CREDENTIALS.values()), first=False)
        subagents = Subagents(lead, secrets=list(CREDENTIALS.values()), run_agent=run_agent, record_usage=None)
        calls = [asyncio.create_task(start(subagents, f'c{n}', task)) for n, task in enumerate(('Hang', 'Stubborn'))]
        while len(issued) < 2:
            await asyncio.sleep(0.01)
        assert all(issued)  # each got the model key
        await asyncio.wait_for(subagents.close(), 2)  # does not wait for the stubborn one beyond the grace period
        assert sorted(cancelled) == ['Hang', 'Stubborn'] and issued == [{}, {}]
        with pytest.raises(asyncio.CancelledError):
            await start(subagents, 'c3', 'Hang')
        assert subagents.started == 2
        release.set()
        return await asyncio.gather(*calls, return_exceptions=True)

    outcomes = asyncio.run(asyncio.wait_for(scenario(), 5))
    assert isinstance(outcomes[0], asyncio.CancelledError)


def test_the_parallel_limit_comes_from_the_environment_and_is_at_least_one(monkeypatch):
    lead = SessionEvents(Recorder(), secrets=[], first=False)
    monkeypatch.setenv('AGENT_MAX_SUBAGENTS', '0')
    assert Subagents(lead, secrets=[], record_usage=None, run_agent=FakeSubagent({})).limit == 1
    monkeypatch.setenv('AGENT_MAX_SUBAGENTS', '6')
    assert Subagents(lead, secrets=[], record_usage=None, run_agent=FakeSubagent({})).limit == 6
    monkeypatch.delenv('AGENT_MAX_SUBAGENTS')
    assert Subagents(lead, secrets=[], record_usage=None, run_agent=FakeSubagent({})).limit == 4


def test_a_model_calls_events_and_tokens_commit_while_no_other_flush_can_run(monkeypatch):
    """add_usage holds the recorder's lock for its whole transaction, so a parallel subagent's flush cannot commit
    later events first (the live stream follows ids and would skip the ones still uncommitted)."""
    recorder = RunRecorder(run_key='cc:x:1', project_id='p', agent_id='a', actor_id='u', secrets=[])
    monkeypatch.setattr(run_record, '_write', lambda conn, store, events: None)
    blocked: list[bool] = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            outside = threading.Thread(target=lambda: blocked.append(not recorder._lock.acquire(blocking=False)))
            outside.start()
            outside.join()

    recorder.add('usage', {'input_tokens': 1})
    runner = SimpleNamespace(store=SimpleNamespace(connect=Connection), id='x', worker_id='w')
    native_sessions.NativeRunner.add_usage(
        runner, recorder, {'input_tokens': 1, 'output_tokens': 1, 'cached_tokens': 0, 'reasoning_tokens': 0}
    )
    assert blocked == [True] and recorder.flushed == 1


def test_a_nested_run_redacts_its_traces_with_the_enclosing_secrets_too():
    with trace_secrets(['parent-secret']):
        with trace_secrets(['sub-secret']):
            assert set(agent_tracing._secrets.get()) == {'parent-secret', 'sub-secret'}
        assert agent_tracing._secrets.get() == ('parent-secret',)


def test_an_abandoned_subagent_that_fails_later_logs_no_traceback(monkeypatch, caplog):
    """Stop cancels the call at once although the subagent ignores it; after close()'s grace period the subagent is
    left to end, and the exception it raises then is retrieved, never logged as 'never retrieved' with the secret."""
    monkeypatch.setattr(native_sessions, 'STOP_GRACE_SECONDS', 0.1)

    async def scenario():
        started, release, ended = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def run_agent(config, task, *, credentials, **_):
            started.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            ended.set()
            raise RuntimeError(f'connection with {SECRET} lost')

        lead = SessionEvents(Recorder(), secrets=list(CREDENTIALS.values()), first=False)
        subagents = Subagents(lead, secrets=list(CREDENTIALS.values()), run_agent=run_agent, record_usage=None)
        call = asyncio.create_task(start(subagents, 's1', 'Stubborn'))
        await asyncio.wait_for(started.wait(), 2)
        call.cancel()  # Stop cancels the parent's turn and with it this tool call
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, 1)  # returns at once, though the subagent keeps going
        await asyncio.wait_for(subagents.close(), 2)
        assert native_sessions._abandoned
        release.set()
        await asyncio.wait_for(ended.wait(), 2)
        for _ in range(5):
            await asyncio.sleep(0)
        assert not native_sessions._abandoned
        gc.collect()

    with caplog.at_level(logging.DEBUG):
        asyncio.run(asyncio.wait_for(scenario(), 5))
        gc.collect()
    assert 'never retrieved' not in caplog.text and SECRET not in caplog.text


def test_a_call_waiting_for_a_slot_starts_nothing_once_the_turn_ends():
    gate = asyncio.Event()

    async def wait(turn):
        await gate.wait()

    subagents, fake, _, lead = host(
        {'First': {'steps': [wait], 'answer': 'first'}, 'Second': {'steps': [], 'answer': 'second'}}, limit=1
    )

    async def scenario():
        first = asyncio.create_task(start(subagents, 'c1', 'First'))
        second = asyncio.create_task(start(subagents, 'c2', 'Second'))
        while not fake.calls:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        lead.closed = True  # the turn ends while Second waits for the slot
        gate.set()
        assert await first == 'first'
        with pytest.raises(asyncio.CancelledError):
            await second

    asyncio.run(asyncio.wait_for(scenario(), 5))
    assert [call['task'] for call in fake.calls] == ['First']


def test_run_subagent_is_offered_only_to_a_run_with_the_turns_subagents(config, responses_api):
    """A run without a Subagents host (the tuning reviewer, a test chat, a live run) is never offered run_subagent,
    even when its row is on: the call could only fail with UNAVAILABLE."""
    _, requests = responses_api
    config['tools'] = {'items': [item('read_file', 'on'), item('run_subagent', 'on', subagent_allowed=False)]}

    def offered(**extra) -> list[str]:
        requests.clear()
        asyncio.run(
            agent(config, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}, stream=False, **extra)
        )
        return [tool.get('name') for tool in requests[0]['body'].get('tools', [])]

    assert 'run_subagent' not in offered()
    assert 'Start a helper' not in json.dumps(requests[0]['body'])  # nor described anywhere
    assert 'run_subagent' in offered(subagents=object())


def test_a_subagent_ignoring_cancellation_starts_no_tool_call_while_close_waits_and_has_no_credentials(monkeypatch):
    """close() runs before the parent's events close: during its grace wait, a subagent that swallows its cancellation
    and goes on starts no tool call (nor model call), and the credentials it was handed are already empty."""
    monkeypatch.setattr(native_sessions, 'STOP_GRACE_SECONDS', 2.0)
    started, seen = asyncio.Event(), []

    async def swallow(turn):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            seen.append(dict(subagents.issued[0]))  # what it still holds once cancelled

    subagents, fake, recorder, lead = host(
        {'Stubborn': {'steps': [swallow, tool('r1', 'read_file', 'late')], 'answer': 'late'}}
    )

    async def scenario():
        call = asyncio.create_task(start(subagents, 's1', 'Stubborn'))
        await asyncio.wait_for(started.wait(), 2)
        assert subagents.issued[0]  # it got the model key
        await asyncio.wait_for(subagents.close(), 1)  # the subagent ends at its next tool call, well within the grace
        assert not lead.closed
        return await asyncio.gather(call, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 5))
    assert seen == [{}]
    assert fake.calls[0]['tools'] == []
    assert 'tool_result' not in [kind for kind, _ in recorder.events]


def test_stop_takes_at_most_one_grace_period_when_the_turn_and_a_subagent_ignore_cancellation(native, monkeypatch):
    """close() gets only what is left of the grace that started at Stop, so Stop ends the turn within about
    STOP_GRACE_SECONDS, not twice that."""
    sessions, sid = native
    store, url = sessions.store, f'/api/sessions/{sid}'
    grace = 0.8
    monkeypatch.setattr(native_sessions, 'STOP_GRACE_SECONDS', grace)
    lead_started, sub_started, ignored, release = asyncio.Event(), asyncio.Event(), [], asyncio.Event()
    subagent = FakeSubagent({'Stubborn': {'steps': [stubborn(sub_started, ignored, release)], 'answer': 'late'}})

    async def lead(turn):  # the run_subagent call, in a turn that ignores its own cancellation
        call = asyncio.ensure_future(delegate('s1', 'Stubborn', ['read_file'])(turn))
        lead_started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                ignored.append('lead')
                call.cancel()  # Stop cancels the tool call; the subagent ignores it
        await asyncio.gather(call, return_exceptions=True)

    fake = FakeAgent(
        [
            {
                'offered': ['read_file'],
                'steps': [
                    llm(call('s1', 'run_subagent', {'task': 'Stubborn', 'tools': ['read_file'], 'resources': None})),
                    lead,
                ],
                'answer': 'late',
            }
        ]
    )

    async def scenario():
        loop = asyncio.get_running_loop()
        runner = asyncio.create_task(
            native_sessions.NativeRunner(store, claimed(store), fake, run_subagent=subagent, poll=0.02).run()
        )
        await asyncio.wait_for(asyncio.gather(lead_started.wait(), sub_started.wait()), 5)
        stopping = loop.time()
        assert (await post(sessions, url + '/stop')).status_code == 202
        await wait_for(store, sid, lambda s: s['status'] == 'stopped', timeout=5)
        took = loop.time() - stopping
        release.set()
        await post(sessions, url + '/close')
        await asyncio.wait_for(runner, 5)
        return took

    took = asyncio.run(scenario())
    assert 'lead' in ignored and 'cancel' in ignored  # both the turn and the subagent ignored their cancellation
    assert took < grace * 1.6, took
