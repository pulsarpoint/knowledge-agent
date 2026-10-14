"""Part 5: when an event run's first turn ends (finished, failed or stopped), the worker writes one notification in the
turn's finishing transaction — the answer, or why it failed — linked to the run, its event and handler. Later turns and
Test runs write none. The worker no longer runs agent tasks."""

import asyncio
import importlib.util
import inspect

import pytest
from fastapi.encoders import jsonable_encoder

from native_fakes import FakeAgent, llm, message
from session_rows import add_test_session
from knowledge_admin.sessions import run_record
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.sessions.text import IDLE_CLOSED
from run_helpers import post, session_row, wait_for
from test_event_processor import a_handler, an_event
from test_generations import native_agent, sessions  # noqa: F401  (fixture)

FAILED = 'Agent execution failed (RuntimeError). Check the model connection and agent resources.'


def a_run(sessions, purpose='event'):
    """A run of a native agent whose first message is queued; for purpose event, started by a handler for an event."""
    agent_id = native_agent(sessions.client, 'Event agent', model_id=sessions.claude)
    config = jsonable_encoder(get_agent_config(agent_id, conninfo=sessions.store.conninfo))
    event = an_event(sessions.store)
    handler = a_handler(sessions.store, action='agent', emit=None, agent_id=agent_id, prompt='Triage')
    with sessions.store.connect() as conn:
        project = sessions.store.project(conn, 'shop')
        actor = sessions.store.actor(conn, 'admin-id', 'admin@example.test')
        sid = add_test_session(
            conn, project, actor, agent_id, sessions.claude, config=config, message='Triage DEMO-1', purpose=purpose
        )
        conn.execute(
            "UPDATE ka.agent_sessions SET title='Event agent · jira.ticket.updated DEMO-1' WHERE id=%s", (sid,)
        )
        if purpose == 'event':
            conn.execute(
                'UPDATE ka.agent_sessions SET event_id=%s,handler_id=%s WHERE id=%s', (event['id'], handler['id'], sid)
            )
    return sid, agent_id, event, handler


def notifications(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.notifications ORDER BY id').fetchall()


def claimed(store) -> dict:
    [row] = shared.claim(store, 'worker-1', [], 4)
    return row


@pytest.fixture
def worker(sessions, monkeypatch):
    monkeypatch.setattr(run_record, '_store', sessions.store)
    return sessions


def play(sessions, sid, fake, *, second: str | None = None, until=lambda s: s['status'] == 'waiting'):
    """Run the first turn (and `second` as the next message) and close the run."""
    store = sessions.store

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['turns'] == 1 and until(s))
        if second is not None:
            await post(sessions, f'/api/sessions/{sid}/messages', {'text': second})
            await wait_for(store, sid, lambda s: s['turns'] == 2 and s['status'] in ('waiting', 'failed'))
        await post(sessions, f'/api/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())


def test_a_finished_first_turn_writes_one_succeeded_notification_and_later_turns_none(worker):
    sid, agent_id, event, handler = a_run(worker)
    fake = FakeAgent(
        [
            {'steps': [llm(message('Triaged: a timeout.'))], 'answer': 'Triaged: a timeout.'},
            {'steps': [llm(message('More.'))], 'answer': 'More.'},
        ]
    )
    play(worker, sid, fake, second='Tell me more.')
    [note] = notifications(worker.store)
    assert (
        note['kind'],
        note['status'],
        note['title'],
        note['body'],
        note['event_id'],
        note['handler_id'],
        note['agent_id'],
        note['session_id'],
        note['agent_task_id'],
    ) == (
        'agent.response',
        'succeeded',
        'Event agent · jira.ticket.updated DEMO-1',
        'Triaged: a timeout.',
        event['id'],
        handler['id'],
        agent_id,
        sid,
        None,
    )


def test_a_failed_first_turn_writes_a_failed_notification_with_the_status_message(worker):
    sid, *_ = a_run(worker)
    fake = FakeAgent([{'steps': [llm(message('Reading'))], 'error': RuntimeError('boom')}])
    play(worker, sid, fake, until=lambda s: s['status'] == 'failed')
    [note] = notifications(worker.store)
    assert (note['status'], note['body'], note['session_id']) == ('failed', FAILED, sid)


def test_a_stopped_first_turn_writes_a_failed_notification(worker):
    sid, *_ = a_run(worker)
    store = worker.store
    fake = FakeAgent([{'steps': [llm(message('Partial'))], 'answer': 'never reached'}], gates={0: asyncio.Event()})

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'working' and s['turns'] == 1)
        await asyncio.sleep(0.1)
        assert (await post(worker, f'/api/sessions/{sid}/stop')).status_code == 202
        await wait_for(store, sid, lambda s: s['status'] == 'stopped')
        await post(worker, f'/api/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    [note] = notifications(store)
    assert (note['status'], note['body'], note['session_id']) == ('failed', shared.STOPPED, sid)


def test_a_first_turn_the_worker_lost_writes_a_failed_notification(worker):
    sid, *_ = a_run(worker)
    store = worker.store
    with store.connect() as conn:  # a worker died during turn 1
        conn.execute(
            """UPDATE ka.agent_sessions SET status='working',turns=1,worker_id='gone',
            lease_expires_at=now()-interval '1 second' WHERE id=%s""",
            (sid,),
        )
        conn.execute(
            "UPDATE ka.agent_session_inputs SET status='delivered',turn=1,delivered_at=now() WHERE session_id=%s",
            (sid,),
        )
    shared.claim(store, 'worker-1', [], 4)
    [note] = notifications(store)
    assert (note['status'], note['body'], note['session_id']) == ('failed', shared.INTERRUPTED, sid)


def test_a_run_closed_before_its_first_turn_writes_one_failed_notification(worker):
    sid, *_ = a_run(worker)
    store = worker.store
    assert asyncio.run(post(worker, f'/api/sessions/{sid}/close')).status_code == 200
    [note] = notifications(store)
    assert (note['status'], note['body'], note['session_id']) == ('failed', 'Closed.', sid)
    asyncio.run(post(worker, f'/api/sessions/{sid}/close'))  # closing again, or a worker, writes nothing more
    assert shared.claim(store, 'worker-1', [], 4) == []
    with store.connect() as conn:
        shared.notify_event_run(conn, sid, 1, 'failed', 'again')
    assert len(notifications(store)) == 1


def test_a_run_closed_idle_before_its_first_turn_writes_one_failed_notification(worker):
    sid, *_ = a_run(worker)
    store = worker.store
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET last_activity_at=now()-interval '25 hours' WHERE id=%s", (sid,))
    shared.claim(store, 'worker-1', [], 4)  # closes idle sessions first
    shared.claim(store, 'worker-1', [], 4)
    [note] = notifications(store)
    assert (note['status'], note['body'], note['session_id']) == ('failed', IDLE_CLOSED, sid)


def test_a_test_run_closed_before_its_first_turn_writes_no_notification(worker):
    sid, *_ = a_run(worker, purpose='test')
    asyncio.run(post(worker, f'/api/sessions/{sid}/close'))
    assert notifications(worker.store) == []


def test_a_run_closed_during_its_first_turn_notifies_closed(worker):
    sid, *_ = a_run(worker)
    store = worker.store
    fake = FakeAgent([{'steps': [llm(message('Partial'))], 'answer': 'never reached'}], gates={0: asyncio.Event()})

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        await wait_for(store, sid, lambda s: s['status'] == 'working' and s['turns'] == 1)
        await asyncio.sleep(0.1)
        await post(worker, f'/api/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)

    asyncio.run(scenario())
    [note] = notifications(store)
    assert (note['status'], note['body'], note['session_id']) == ('failed', 'Closed.', sid)


def serve_one(sessions, sid, fake) -> dict:
    """Serve until the run's first turn ends; the session row once the worker released it (or after 1 s)."""
    store = sessions.store

    async def scenario():
        stopped = asyncio.Event()
        serving = asyncio.create_task(
            native_sessions.serve(store, fake, stopped=stopped, worker_id='native-1', limit=1, poll=0.02)
        )
        await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        try:
            return await wait_for(store, sid, lambda s: s['worker_id'] is None, timeout=1.0)
        except (AssertionError, TimeoutError):
            return session_row(store, sid)
        finally:
            stopped.set()
            await asyncio.wait_for(serving, 5)

    return asyncio.run(scenario())


def test_an_event_run_releases_its_worker_slot_when_its_turn_ends(worker):
    sid, *_ = a_run(worker)
    row = serve_one(
        worker,
        sid,
        FakeAgent(
            [
                {'steps': [llm(message('Done.'))], 'answer': 'Done.'},
                {'steps': [llm(message('Again.'))], 'answer': 'Again.'},
            ]
        ),
    )
    assert (row['status'], row['worker_id'], row['lease_expires_at']) == ('waiting', None, None)
    asyncio.run(post(worker, f'/api/sessions/{sid}/messages', {'text': 'And?'}))
    [again] = shared.claim(worker.store, 'worker-2', [], 4)  # a later message is claimed again
    assert again['id'] == sid


def test_a_test_run_keeps_its_worker_slot_while_idle(worker):
    sid, *_ = a_run(worker, purpose='test')
    row = serve_one(worker, sid, FakeAgent([{'steps': [llm(message('Hi.'))], 'answer': 'Hi.'}]))
    assert (row['status'], row['worker_id']) == ('waiting', 'native-1')


def test_a_test_run_writes_no_notification(worker):
    sid, *_ = a_run(worker, purpose='test')
    fake = FakeAgent([{'steps': [llm(message('Hi.'))], 'answer': 'Hi.'}])
    play(worker, sid, fake)
    assert notifications(worker.store) == []


def test_the_worker_no_longer_runs_agent_tasks():
    assert importlib.util.find_spec('knowledge_admin.agent_tasks') is None
    assert 'agent_tasks' not in inspect.getsource(native_sessions.main)
