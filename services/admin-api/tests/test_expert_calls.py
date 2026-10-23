"""Calling an expert: checks, the expert's own configuration and credentials, the shared limits, Stop, recording."""

import asyncio
import json
import threading
from access_helpers import of
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext
from psycopg.types.json import Jsonb

from native_fakes import FakeAgent, FakeSubagent, Recorder, call, delegate, llm, message, tool
from knowledge_admin.sessions import run_record
from knowledge_admin.sessions.activity import tool_failed
from knowledge_admin.agents.runner import agent
from knowledge_admin.capabilities.context import TOOL_CALL_ID
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents.experts import FAILED, UNAVAILABLE, load, offered, pin
from knowledge_admin.knowledge_agents.native_sessions import SessionEvents
from knowledge_admin.knowledge_agents.subagents import LIMIT, MAX_PER_TURN, Subagents, subagent_config
from knowledge_admin.sessions.run_record import RunRecorder
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.sdk import NOT_OFFERED, expert_tool
from run_helpers import post, wait_for
from test_generations import native_agent, sessions  # noqa: F401  (fixture)
from test_native_sessions import claimed, native  # noqa: F401  (fixture)
from test_run_resources import narrowed_agent  # noqa: F401  (fixture)
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)
from test_subagent_config import CREDENTIALS, NO_RESOURCES, parent
from test_subagents import SCRATCH, SECRET, start

ENTRY = {
    'id': 'payments',
    'name': 'Payments',
    'when_to_use': 'Payments questions.',
    'tool': 'ask_payments',
    'accepts': ['code_project_ids'],
    'description': 'Ask Payments, an expert agent. Payments questions. It works only on the resources you pass '
    '(it accepts: code projects).',
}
FIXED = {
    **ENTRY,
    'id': 'ledger',
    'name': 'Ledger',
    'tool': 'ask_ledger',
    'accepts': [],
    'description': 'Ask Ledger, an expert agent. Ledger questions. It uses its own resources.',
}
EXPERT_SECRET = 'expert-ssh-secret'


def expert_config(name: str = 'Payments') -> dict:
    """What experts.load returns: the expert's own saved configuration, without experts or a record."""
    config = parent(id=name.lower(), name=name, system_prompt={'content': f'Know {name}.'}, instructions='', experts=[])
    del config['record']
    return config


class Loader:
    """Stands in for experts.load: records each call and returns the configuration and a fresh credential map."""

    def __init__(self, credentials: dict | None = None, error: Exception | None = None):
        self.credentials = credentials if credentials is not None else {'expert-model': 'expert-model-secret'}
        self.error = error
        self.calls: list[tuple] = []
        self.roles: list[bool] = []  # the caller's content editor of each call
        self.issued: list[dict] = []

    def __call__(self, entry, given, editor=False):
        self.calls.append((entry, given))
        self.roles.append(editor)
        if self.error is not None:
            raise self.error
        issued = dict(self.credentials)
        self.issued.append(issued)
        return expert_config(entry['name']), issued


def host(turns: dict, loader: Loader | None = None, *, limit: int = 4, recorder=None):
    """A lead turn's Subagents with an expert loader; events record into `recorder` (a fake one by default)."""
    recorder = recorder if recorder is not None else Recorder()
    secrets = list(CREDENTIALS.values())  # the turn's list, shared as NativeRunner.run_turn shares it
    lead = SessionEvents(recorder, secrets=secrets, first=False)
    fake = FakeSubagent(turns)
    subagents = Subagents(
        lead,
        secrets=secrets,
        run_agent=fake,
        limit=limit,
        record_usage=None,
        load_expert=loader if loader is not None else Loader(),
    )
    return subagents, fake, recorder, lead


async def ask(subagents, call_id, task, resources=None, entry=ENTRY):
    """The ask_<name> tool as the adapter runs it for one call."""
    context = {
        'config': {**parent(), 'experts': [entry]},
        'credentials': dict(CREDENTIALS),
        'workspace': SCRATCH,
        'tools': frozenset(CATALOG) | {entry['tool']},
        'subagents': subagents,
    }
    token = TOOL_CALL_ID.set(call_id)
    try:
        return await subagents.ask(context, entry, task, resources, call_id)
    finally:
        TOOL_CALL_ID.reset(token)


def test_an_expert_runs_with_the_loaded_configuration_and_is_recorded_with_its_agent_id():
    loader = Loader()
    subagents, fake, recorder, _ = host(
        {
            'Map payments': {
                'steps': [
                    llm(call('r1', 'read_file', {'path': '/code/worker/pay.py'}), input_tokens=10, output_tokens=2),
                    tool('r1', 'read_file', 'def pay(): ...'),
                    llm(message('Payments go through pay.py.'), input_tokens=20, output_tokens=4),
                ],
                'answer': f'Payments go through pay.py ({SECRET}).',
            }
        },
        loader,
    )
    answer = asyncio.run(ask(subagents, 'e1', 'Map payments', {'code_project_ids': ['worker', 'worker']}))
    assert loader.calls == [(ENTRY, {'code_project_ids': ['worker', 'worker']})]  # experts.load takes its picks
    [run] = fake.calls
    assert run['config'] == expert_config() and run['credentials'] == {'expert-model': 'expert-model-secret'}
    assert (run['workspace'], run['max_turns'], run['turn_timeout_seconds'], run['stream']) == (
        SCRATCH,
        None,
        None,
        False,
    )
    assert answer == 'Payments go through pay.py ([redacted]).'
    assert recorder.events[0] == (
        'input',
        {'task': 'Map payments', 'expert': {'id': 'payments', 'name': 'Payments'}, 'parent_call_id': 'e1'},
    )
    assert [kind for kind, _ in recorder.events] == [
        'input',
        'instructions',
        'tool_call',
        'usage',
        'tool_result',
        'message',
        'usage',
    ]
    assert recorder.events[1][1]['text'] == 'Know Payments.'
    assert all(payload['parent_call_id'] == 'e1' for _, payload in recorder.events)
    assert recorder.agent_ids == ['payments'] * len(recorder.events)
    assert subagents.started == 1 and loader.issued == [{}]


def test_an_expert_that_is_no_longer_available_is_a_tool_error():
    loader = Loader(error=ValueError(UNAVAILABLE.format(name='Payments')))
    subagents, fake, recorder, _ = host({}, loader)
    with pytest.raises(ValueError) as refused:
        asyncio.run(ask(subagents, 'e1', 'Map payments'))
    assert str(refused.value) == 'Expert Payments is not available.'
    assert subagents.started == 0 and fake.calls == [] and recorder.events == []
    without = Subagents(
        SessionEvents(Recorder(), secrets=[], first=False), secrets=[], record_usage=None, run_agent=FakeSubagent({})
    )  # a host that cannot load experts
    with pytest.raises(ValueError) as refused:
        asyncio.run(ask(without, 'e2', 'Map payments'))
    assert str(refused.value) == 'Expert Payments is not available.' and without.started == 0


def test_a_resource_the_caller_lacks_is_refused_before_anything_is_loaded():
    loader = Loader()
    subagents, fake, _, _ = host({}, loader)
    with pytest.raises(ValueError) as refused:
        asyncio.run(ask(subagents, 'e1', 'Map payments', {'code_project_ids': ['nope']}))
    assert str(refused.value) == 'This code project is not available in this run.'
    for task in ('  ', 'x' * 20_001):
        with pytest.raises(ValueError):
            asyncio.run(ask(subagents, 'e1', task))
    assert loader.calls == [] and fake.calls == [] and subagents.started == 0


def test_a_fixed_expert_uses_its_own_credentials_and_they_are_redacted(monkeypatch):
    """A real RunRecorder with the turn's list: the turn's record, the expert's events, the caller's tool result and
    the answer all redact the expert's secret, which the caller's own credentials do not contain."""
    written: list[dict] = []
    monkeypatch.setattr(run_record, '_store', object())
    monkeypatch.setattr(run_record, '_insert', lambda store, events: written.extend(events))
    secrets = list(CREDENTIALS.values())
    recorder = RunRecorder(run_key='native:x:1', project_id='p', agent_id='lead', actor_id='u', secrets=secrets)
    loader = Loader({'ssh': EXPERT_SECRET})
    subagents, fake, _, lead = host(
        {
            'Check the ledger': {
                'steps': [
                    llm(call('r1', 'ssh_run', {'command': 'cat key'}), input_tokens=5, output_tokens=1),
                    tool('r1', 'ssh_run', f'key {EXPERT_SECRET}'),
                    llm(message(f'The key is {EXPERT_SECRET}.')),
                ],
                'answer': f'Done with {EXPERT_SECRET}.',
            }
        },
        loader,
        recorder=recorder,
    )
    subagents.secrets = lead.secrets = secrets  # the turn's own list, as run_turn hands it to both

    async def scenario():
        result = await ask(subagents, 'e1', 'Check the ledger', entry=FIXED)
        # The caller's SDK hooks then record the tool result the model gets.
        await lead.on_tool_end(
            SimpleNamespace(tool_call_id='e1'), SimpleNamespace(name='Lead'), SimpleNamespace(name='ask_ledger'), result
        )
        return result

    answer = asyncio.run(scenario())
    recorder.flush()
    assert loader.calls == [(FIXED, None)]
    assert fake.calls[0]['credentials'] == {'ssh': EXPERT_SECRET}
    assert answer == 'Done with [redacted].'
    assert EXPERT_SECRET not in json.dumps([event['payload'] for event in written])
    assert sum('[redacted]' in json.dumps(event['payload']) for event in written) >= 3  # tool output, message, result
    assert [event['agent_id'] for event in written] == ['ledger'] * (len(written) - 1) + ['lead']
    assert loader.issued == [{}]  # emptied once the call ended


def test_subagents_and_experts_share_the_16_per_turn_limit_and_the_slots():
    turns = {f'Task {n}': {'steps': [], 'answer': 'ok'} for n in range(17)}
    turns['Ask'] = {'steps': [], 'answer': 'expert ok'}
    subagents, fake, _, _ = host(turns, limit=32)

    async def scenario():
        first = await asyncio.gather(
            *(start(subagents, f'c{n}', f'Task {n}') for n in range(MAX_PER_TURN - 1)), ask(subagents, 'e1', 'Ask')
        )
        assert first[-1] == 'expert ok'
        for late in (start(subagents, 'c16', 'Task 16'), ask(subagents, 'e2', 'Ask')):
            with pytest.raises(ValueError) as refused:
                await late
            assert str(refused.value) == LIMIT

    asyncio.run(scenario())
    assert len(fake.calls) == MAX_PER_TURN and subagents.started == MAX_PER_TURN


def test_an_expert_waits_for_a_slot_held_by_a_subagent():
    gate, running = asyncio.Event(), []

    async def hold(turn):
        running.append('sub')
        await gate.wait()

    async def note(turn):
        running.append('expert')

    subagents, _, _, _ = host(
        {'Hold': {'steps': [hold], 'answer': 'held'}, 'Ask': {'steps': [note], 'answer': 'ok'}}, limit=1
    )

    async def scenario():
        sub = asyncio.create_task(start(subagents, 'c1', 'Hold'))
        while not running:
            await asyncio.sleep(0.01)
        expert = asyncio.create_task(ask(subagents, 'e1', 'Ask'))
        await asyncio.sleep(0.1)
        assert running == ['sub']  # the expert waits for the only slot
        gate.set()
        return await asyncio.gather(sub, expert)

    assert asyncio.run(asyncio.wait_for(scenario(), 5)) == ['held', 'ok']


def test_stop_cancels_a_running_expert_and_clears_its_credentials(monkeypatch):
    monkeypatch.setattr(native_sessions, 'STOP_GRACE_SECONDS', 0.5)
    started, cancelled = asyncio.Event(), []

    async def hang(turn):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append('expert')
            raise

    loader = Loader({'ssh': EXPERT_SECRET})
    subagents, _, recorder, _ = host({'Hang': {'steps': [hang, llm(message('late'))], 'answer': 'late'}}, loader)

    async def scenario():
        running = asyncio.create_task(ask(subagents, 'e1', 'Hang', entry=FIXED))
        await asyncio.wait_for(started.wait(), 2)
        assert loader.issued == [{'ssh': EXPERT_SECRET}]
        recorded = len(recorder.events)
        await asyncio.wait_for(subagents.close(), 2)
        assert loader.issued == [{}] and cancelled == ['expert']
        outcome = await asyncio.gather(running, return_exceptions=True)
        assert len(recorder.events) == recorded
        with pytest.raises(asyncio.CancelledError):
            await ask(subagents, 'e2', 'Hang', entry=FIXED)  # nothing starts after the turn ended
        return outcome

    [outcome] = asyncio.run(asyncio.wait_for(scenario(), 5))
    assert isinstance(outcome, asyncio.CancelledError)
    assert len(loader.calls) == 1 and subagents.started == 1


def test_a_failed_expert_returns_a_redacted_failure():
    subagents, _, recorder, _ = host(
        {
            'Crash': {'steps': [], 'error': ValueError(f'boom {EXPERT_SECRET}')},
            'Down': {'steps': [], 'error': RuntimeError(f'down {EXPERT_SECRET}')},
        },
        Loader({'ssh': EXPERT_SECRET}),
    )

    async def scenario():
        return [await ask(subagents, 'e1', 'Crash', entry=FIXED), await ask(subagents, 'e2', 'Down', entry=FIXED)]

    assert asyncio.run(scenario()) == [
        FAILED.format(name='Ledger', message='boom [redacted]'),
        FAILED.format(
            name='Ledger',
            message=(
                'Agent execution failed (RuntimeError: down [redacted]). Check the model connection and agent resources.'
            ),
        ),
    ]
    errors = [payload for kind, payload in recorder.events if kind == 'error']
    assert [error['parent_call_id'] for error in errors] == ['e1', 'e2'] and EXPERT_SECRET not in json.dumps(errors)
    assert tool_failed(FAILED.format(name='Ledger', message='boom')) and tool_failed('Expert A · B failed: x')
    assert not tool_failed('Expert advice: nothing failed.') and not tool_failed('The Expert Ledger failed: x')


def test_a_load_error_other_than_a_refusal_fails_the_call_like_a_failed_run():
    subagents, fake, _, _ = host({}, Loader(error=RuntimeError(f'cannot decrypt {SECRET}')))
    assert asyncio.run(ask(subagents, 'e1', 'Map payments')) == FAILED.format(
        name='Payments',
        message=(
            'Agent execution failed (RuntimeError: cannot decrypt [redacted]). '
            'Check the model connection and agent resources.'
        ),
    )
    assert fake.calls == [] and subagents.started == 1  # it started, as a failed run does


def test_a_load_that_ends_after_stop_empties_the_credentials_it_returns():
    release, loading = threading.Event(), threading.Event()
    loader = Loader({'ssh': EXPERT_SECRET})

    def slow(entry, given, editor):
        loading.set()
        release.wait(5)
        return loader(entry, given)

    subagents, fake, _, _ = host({}, loader)
    subagents.load_expert = slow

    async def scenario():
        running = asyncio.create_task(ask(subagents, 'e1', 'Map payments', entry=FIXED))
        await asyncio.to_thread(loading.wait, 2)
        running.cancel()  # Stop while the expert loads
        with pytest.raises(asyncio.CancelledError):
            await running
        release.set()
        while not loader.issued or loader.issued[0]:
            await asyncio.sleep(0.01)

    asyncio.run(asyncio.wait_for(scenario(), 5))
    assert loader.issued == [{}] and fake.calls == [] and subagents.started == 0


def expert_with_a_subagent(inner: list, inner_steps: list) -> dict:
    """Scripts for an expert whose configuration has run_subagent: it starts one subagent (Inner) and answers."""
    return {
        'Ask': {
            'offered': ['read_file', 'run_subagent'],
            'steps': [delegate('n1', 'Inner', results=inner)],
            'answer': 'expert ok',
        },
        'Inner': {'steps': inner_steps, 'answer': 'inner ok'},
    }


def test_an_experts_subagent_gets_slots_of_its_own_and_is_recorded_under_the_expert():
    """With one slot per host, the expert holds the turn's only slot while its subagent runs in the expert's own."""
    inner: list[str] = []
    subagents, fake, recorder, _ = host(expert_with_a_subagent(inner, [llm(message('Inner read it.'))]), limit=1)
    assert asyncio.run(asyncio.wait_for(ask(subagents, 'e1', 'Ask'), 5)) == 'expert ok'
    assert inner == ['inner ok'] and [run['task'] for run in fake.calls] == ['Ask', 'Inner']
    assert fake.calls[0]['subagents'] is not subagents and fake.calls[1]['subagents'] is None
    by_call = [
        (payload['parent_call_id'], agent_id) for (_, payload), agent_id in zip(recorder.events, recorder.agent_ids)
    ]
    assert {agent_id for call_id, agent_id in by_call if call_id == 'n1'} == {'payments'}
    assert [kind for (kind, payload) in recorder.events if payload['parent_call_id'] == 'n1'] == [
        'input',
        'instructions',
        'message',
        'usage',
    ]
    assert subagents.started == 2  # the expert and its subagent, both of this turn


def test_a_subagent_started_by_an_expert_counts_towards_the_turns_16():
    inner: list[str] = []
    turns = {**expert_with_a_subagent(inner, []), **{f'Task {n}': {'steps': [], 'answer': 'ok'} for n in range(15)}}
    subagents, fake, _, _ = host(turns, limit=32)

    async def scenario():
        await asyncio.gather(*(start(subagents, f'c{n}', f'Task {n}') for n in range(MAX_PER_TURN - 1)))
        return await ask(subagents, 'e1', 'Ask')

    assert asyncio.run(scenario()) == 'expert ok'
    assert inner == [f'An error occurred while running the tool. Please try again. Error: {LIMIT}']
    assert subagents.started == MAX_PER_TURN and 'Inner' not in [run['task'] for run in fake.calls]


def test_stop_cancels_an_experts_running_subagent(monkeypatch):
    monkeypatch.setattr(native_sessions, 'STOP_GRACE_SECONDS', 0.5)
    started, cancelled, inner = asyncio.Event(), [], []

    async def hang(turn):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append('inner')
            raise

    subagents, _, recorder, _ = host(expert_with_a_subagent(inner, [hang, llm(message('late'))]))

    async def scenario():
        running = asyncio.create_task(ask(subagents, 'e1', 'Ask'))
        await asyncio.wait_for(started.wait(), 2)
        [nested] = subagents.nested
        assert nested.running
        await asyncio.wait_for(subagents.close(), 2)
        assert cancelled == ['inner'] and nested.closed and not nested.running
        return await asyncio.gather(running, return_exceptions=True)

    [outcome] = asyncio.run(asyncio.wait_for(scenario(), 5))
    assert isinstance(outcome, asyncio.CancelledError) and inner == []
    assert 'late' not in json.dumps([payload for _, payload in recorder.events])


def test_experts_are_offered_only_to_a_run_with_the_turns_host_and_never_inside_an_expert_or_subagent(
    config, responses_api
):
    _, requests = responses_api
    config['experts'] = [ENTRY, FIXED]

    def offered_tools(**extra) -> dict[str, dict]:
        requests.clear()
        asyncio.run(
            agent(config, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}, stream=False, **extra)
        )
        return {tool['name']: tool for tool in requests[0]['body'].get('tools', [])}

    assert 'ask_payments' not in offered_tools() and 'ask_ledger' not in offered_tools()
    tools = offered_tools(subagents=object())
    assert tools['ask_payments']['description'] == ENTRY['description']
    assert set(tools['ask_payments']['parameters']['properties']) == {'task', 'resources'}
    assert set(tools['ask_payments']['parameters']['$defs']['ExpertResources']['properties']) == set(NO_RESOURCES)
    assert tools['ask_ledger']['description'] == FIXED['description']
    assert set(tools['ask_ledger']['parameters']['properties']) == {'task'}
    config['experts'] = [{**ENTRY, 'tool': 'read_file'}, FIXED]  # a name another tool of the run already has
    tools = offered_tools(subagents=object())
    assert tools['read_file']['description'] != ENTRY['description'] and 'ask_ledger' in tools
    lead = parent(experts=[ENTRY])
    sub = subagent_config(lead, None)
    assert sub['experts'] == []


def test_the_expert_tool_passes_the_call_to_the_turns_host():
    asked = []

    class Host:
        async def ask(self, context, entry, task, resources, call_id):
            asked.append((entry['tool'], task, resources, call_id))
            return 'answer'

    def invoke(entry, arguments, context):
        payload = json.dumps(arguments)
        wrapped = ToolContext(context=context, tool_name=entry['tool'], tool_call_id='call-9', tool_arguments=payload)
        return asyncio.run(expert_tool(entry).on_invoke_tool(wrapped, payload))

    context = {'tools': frozenset({'ask_payments', 'ask_ledger'}), 'credentials': {}, 'subagents': Host()}
    assert (
        invoke(ENTRY, {'task': 'Map', 'resources': {**NO_RESOURCES, 'code_project_ids': ['worker']}}, context)
        == 'answer'
    )
    assert invoke(ENTRY, {'task': 'Map', 'resources': None}, context) == 'answer'
    assert invoke(FIXED, {'task': 'Check'}, context) == 'answer'
    assert asked == [
        ('ask_payments', 'Map', {**NO_RESOURCES, 'code_project_ids': ['worker']}, 'call-9'),
        ('ask_payments', 'Map', None, 'call-9'),
        ('ask_ledger', 'Check', None, 'call-9'),
    ]
    assert NOT_OFFERED.format(name='ask_payments') in invoke(
        ENTRY, {'task': 'Map', 'resources': None}, {**context, 'tools': frozenset()}
    )
    assert NOT_OFFERED.format(name='ask_ledger') in invoke(FIXED, {'task': 'Check'}, {**context, 'subagents': None})
    assert len(asked) == 3


def test_load_narrows_the_experts_own_access_and_refuses_unavailable_experts(narrowed_agent):
    s = narrowed_agent
    with s.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET expert=true,when_to_use='Payments code.' WHERE id=%s", (s.analyst,))
    with s.store.connect() as conn:
        project = s.store.project(conn, 'shop')
        [entry] = [found for found in offered(conn, None) if found['id'] == s.analyst]
    given = {**NO_RESOURCES, 'code_project_ids': [s.worker]}  # the caller's, but outside the expert's access
    with pytest.raises(ValueError) as refused:
        load(s.store, project, entry, given)
    assert str(refused.value) == f"code project {s.worker} is not in the agent's access"
    given = {**NO_RESOURCES, 'code_project_ids': [s.repo], 'server_ids': [s.host['id']]}
    config, credentials = load(s.store, project, entry, given)
    assert config['experts'] == [] and 'record' not in config
    assert config['expert'] is True  # no wake-up tools in an expert's call
    assert [p['name'] for p in of(config, 'code_project')] == ['Payments API']
    assert of(config, 'collection') == []  # its access object holds only what it was given
    # its access object, built as a run start builds it: what it was given, no content editor
    given_access = [(e['type'], e['id'], e['source']) for e in config['access']['resources'] if e['source'] == 'given']
    assert given_access == [('code_project', s.repo, 'given'), ('server', s.host['id'], 'given')]
    assert config['access']['roles'] == {'content_editor': False}
    key_id = of(config, 'server')[0]['private_key_ref']['id']
    assert credentials[key_id] == s.key['private_key']  # its own SSH key, decrypted for this call
    whole, _ = load(s.store, project, entry, None)  # nothing given: everything it has
    assert [(e['type'], e['source'].split()[0]) for e in whole['access']['resources']] == [
        ('server', 'given'),
        ('service', 'given'),
        ('code_project', 'given'),
        ('collection', 'given'),
        ('code_project', 'via'),  # its service brings the code project it is built from
    ]
    for change in ('enabled=false', 'expert=false'):
        with s.store.connect() as conn:
            conn.execute(f'UPDATE ka.agents SET {change} WHERE id=%s', (s.analyst,))
        with pytest.raises(ValueError) as refused:
            load(s.store, project, entry, given)
        assert str(refused.value) == UNAVAILABLE.format(name=entry['name'])
        with s.store.connect() as conn:
            conn.execute('UPDATE ka.agents SET enabled=true,expert=true WHERE id=%s', (s.analyst,))
    with pytest.raises(ValueError) as refused:
        load(s.store, project, {**entry, 'id': 'gone'}, given)
    assert str(refused.value) == UNAVAILABLE.format(name=entry['name'])


def consult(call_id: str, name: str, task: str, results: list):
    """The caller's ask_<name> call as the SDK runs it in a session turn: on_tool_start, the host, on_tool_end."""

    async def step(turn):
        context, function = SimpleNamespace(tool_call_id=call_id), SimpleNamespace(name=name)
        await turn.hooks.on_tool_start(context, turn.writer, function)
        [entry] = [entry for entry in turn.context['config']['experts'] if entry['tool'] == name]
        output = await turn.context['subagents'].ask(turn.context, entry, task, None, call_id)
        results.append(output)
        await turn.hooks.on_tool_end(context, turn.writer, function, output)

    return step


def test_a_session_turn_calls_an_expert_with_its_own_saved_configuration(native):
    """NativeRunner gives the turn's host the experts.load loader: the expert runs its saved configuration, and its
    events land in the turn's record under its own agent id."""
    sessions, sid = native
    store = sessions.store
    expert_id = native_agent(sessions.client, 'Payments expert', model_id=sessions.gpt)
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET expert=true,when_to_use='Payments questions.' WHERE id=%s", (expert_id,))
        pinned = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()
        conn.execute(
            'UPDATE ka.agent_sessions SET agent_config=%s WHERE id=%s',
            (Jsonb(pin(conn, pinned['agent_config'], None)), sid),
        )
    results: list[str] = []
    expert = FakeSubagent(
        {'Explain payments': {'steps': [llm(message('Payments settle daily.'))], 'answer': 'Payments settle daily.'}}
    )
    fake = FakeAgent(
        [
            {
                'offered': ['ask_payments_expert'],
                'steps': [
                    llm(call('x1', 'ask_payments_expert', {'task': 'Explain payments'})),
                    consult('x1', 'ask_payments_expert', 'Explain payments', results),
                    llm(message('They settle daily.')),
                ],
                'answer': 'They settle daily.',
            }
        ]
    )

    async def scenario():
        runner = asyncio.create_task(
            native_sessions.NativeRunner(store, claimed(store), fake, run_subagent=expert, poll=0.02).run()
        )
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, f'/api/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    assert results == ['Payments settle daily.']
    [run] = expert.calls
    assert (run['config']['id'], run['config']['name'], run['config']['experts']) == (expert_id, 'Payments expert', [])
    with store.connect() as conn:
        recorded = conn.execute(
            'SELECT kind,agent_id,payload FROM ka.agent_run_events WHERE run_key=%s ORDER BY seq', (f'cc:{sid}:1',)
        ).fetchall()
    called = [event for event in recorded if event['payload'].get('parent_call_id') == 'x1']
    assert [event['kind'] for event in called] == ['input', 'instructions', 'message', 'usage']
    assert {str(event['agent_id']) for event in called} == {expert_id}
    assert called[0]['payload']['expert'] == {'id': expert_id, 'name': 'Payments expert'}
    assert expert_id not in {str(event['agent_id']) for event in recorded if event not in called}


def test_an_expert_is_told_the_callers_roles():
    loader = Loader()
    subagents, _, _, _ = host({'Map payments': {'steps': [], 'answer': 'Done.'}}, loader)

    async def asked(roles):
        context = {
            'config': {**parent(), 'experts': [ENTRY]},
            'credentials': dict(CREDENTIALS),
            'workspace': SCRATCH,
            'tools': frozenset(CATALOG) | {ENTRY['tool']},
            'subagents': subagents,
        }
        context['config']['access'] = {**context['config']['access'], 'roles': roles}
        token = TOOL_CALL_ID.set('e1')
        try:
            return await subagents.ask(context, ENTRY, 'Map payments', None, 'e1')
        finally:
            TOOL_CALL_ID.reset(token)

    asyncio.run(asked({'content_editor': False, 'inventory_editor': True}))
    asyncio.run(asked({'content_editor': True}))
    assert loader.roles == [False, True]


def test_an_expert_drops_an_old_agents_inventory_editor_role(narrowed_agent):
    s = narrowed_agent
    with s.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET expert=true,when_to_use='Payments code.' WHERE id=%s", (s.analyst,))
        project = s.store.project(conn, 'shop')
        [entry] = [found for found in offered(conn, None) if found['id'] == s.analyst]

    def roles():
        config, _ = load(s.store, project, entry, None)
        return config['access']['roles']

    assert roles() == {'content_editor': False}
    with s.store.connect() as conn:
        conn.execute(
            """UPDATE ka.agents SET access=jsonb_set(access,'{roles,inventory_editor}','true') WHERE id=%s""",
            (s.analyst,),
        )
    assert roles() == {'content_editor': False}  # an agent stored before the role went loads without it
