"""Agent tasks: a queued agent call runs once without streaming and ends as a notification; failures are retried
twice with backoff; problems retrying cannot fix fail at once; only the worker holding the lease may finish a task."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_admin import agent_tasks
from test_event_processor import an_event
from test_test_sessions import tester  # noqa: F401  (fixture)


def queue(t, **values) -> dict:
    event = an_event(t.store)
    handler = uuid4()
    with t.store.connect() as conn:
        row = {'id': f"agent-task:{event['id']}:{handler}", 'project_id': event['project_id'], 'agent_id': t.agent,
               'agent_name': 'Tester', 'title': 'Tester · jira.ticket.updated DEMO-1', 'prompt': 'Triage DEMO-1',
               'event_id': event['id'], 'handler_id': handler, **values}
        return conn.execute(f"INSERT INTO ka.agent_tasks({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *",
                            tuple(row.values())).fetchone()


def task(store, task_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.agent_tasks WHERE id=%s', (task_id,)).fetchone()


def notifications(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.notifications ORDER BY id').fetchall()


def due_now(store, task_id) -> None:
    with store.connect() as conn:
        conn.execute('UPDATE ka.agent_tasks SET next_attempt_at=now() WHERE id=%s', (task_id,))


class Agent:
    """A stand-in for agents.agent: records each call, then answers or raises the next outcome."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    async def __call__(self, config, task, **options):
        self.calls.append((config, task, options))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(final_output=outcome)


def run_due(store, fake, worker='w1') -> list[dict]:
    claimed = agent_tasks.claim(store, worker, 10)
    for item in claimed:
        asyncio.run(agent_tasks.run_task(store, item, fake))
    return claimed


def test_a_task_runs_once_and_its_answer_becomes_a_notification(tester):
    t = tester
    queued = queue(t)
    fake = Agent('The login times out after the proxy change.')
    [claimed] = run_due(t.store, fake)
    assert (claimed['status'], claimed['attempts'], claimed['worker_id']) == ('running', 1, 'w1')
    [(config, text, options)] = fake.calls
    assert text == 'Triage DEMO-1'
    assert (options['stream'], options['turn_timeout_seconds']) == (False, 600)
    assert 'tester-model-secret' in options['credentials'].values()
    assert config['id'] == t.agent and config['experts'] == []
    assert config['record'] == {'run_key': f"task:{queued['id']}:1", 'project_id': str(queued['project_id']),
                                'kind': 'event_task', 'title': 'Tester · jira.ticket.updated DEMO-1'}
    [note] = notifications(t.store)
    assert (note['kind'], note['status'], note['title'], note['body'], note['event_id'], note['handler_id'],
            note['agent_id'], note['agent_task_id'], note['project_id']) == (
        'agent.response', 'succeeded', 'Tester · jira.ticket.updated DEMO-1', 'The login times out after the proxy change.',
        queued['event_id'], queued['handler_id'], t.agent, queued['id'], queued['project_id'])
    done = task(t.store, queued['id'])
    assert (done['status'], done['notification_id'], done['attempts'], done['worker_id'], done['lease_expires_at'],
            done['error']) == ('succeeded', note['id'], 1, None, None, None)
    assert done['finished_at'] is not None and done['started_at'] is not None
    assert agent_tasks.claim(t.store, 'w1', 10) == []


def test_a_failing_call_is_retried_with_backoff_then_fails(tester):
    t = tester
    queued = queue(t)
    fake = Agent(RuntimeError('boom'), RuntimeError('boom'), RuntimeError('boom'))
    message = 'Agent execution failed (RuntimeError). Check the model connection and agent resources.'
    for attempt, wait in ((1, 30), (2, 120)):
        run_due(t.store, fake)
        with t.store.connect() as conn:
            row = conn.execute("""SELECT status,attempts,error,worker_id,lease_expires_at,
                extract(epoch FROM next_attempt_at - now()) AS wait FROM ka.agent_tasks WHERE id=%s""", (queued['id'],)).fetchone()
        assert (row['status'], row['attempts'], row['error'], row['worker_id'], row['lease_expires_at']) == (
            'queued', attempt, message, None, None)
        assert wait - 5 < row['wait'] <= wait
        assert agent_tasks.claim(t.store, 'w1', 10) == []  # not due yet
        due_now(t.store, queued['id'])
    run_due(t.store, fake)
    [note] = notifications(t.store)
    assert (note['status'], note['body']) == ('failed', message)
    done = task(t.store, queued['id'])
    assert (done['status'], done['attempts'], done['error'], done['notification_id']) == ('failed', 3, message, note['id'])
    assert len(fake.calls) == 3


def test_each_attempt_records_under_its_own_run_key(tester):
    t = tester
    queued = queue(t)
    fake = Agent(TimeoutError(), 'Done.')
    run_due(t.store, fake)
    due_now(t.store, queued['id'])
    run_due(t.store, fake)
    assert [config['record']['run_key'] for config, _, _ in fake.calls] == [
        f"task:{queued['id']}:1", f"task:{queued['id']}:2"]
    assert [note['status'] for note in notifications(t.store)] == ['succeeded']


@pytest.mark.parametrize('change, agent_id, message', [
    ('UPDATE ka.agents SET enabled=false WHERE id=%s', None, 'Agent is disabled.'),
    ("UPDATE ka.agents SET resources='per_run' WHERE id=%s", None,
     'This agent gets its resources per run; handlers can only send to agents with fixed resources.'),
    ('UPDATE ka.llm_models SET enabled=false WHERE id=(SELECT model_id FROM ka.agents WHERE id=%s)', None,
     'Select an enabled model in this agent’s General tab.'),
    (None, 'gone', 'Agent not found.')])
def test_problems_that_retrying_cannot_fix_fail_at_once(tester, change, agent_id, message):
    t = tester
    if change:
        with t.store.connect() as conn:
            conn.execute(change, (t.agent,))
    queued = queue(t, **({'agent_id': agent_id} if agent_id else {}))
    fake = Agent('never')
    run_due(t.store, fake)
    assert fake.calls == []
    [note] = notifications(t.store)
    assert (note['status'], note['body']) == ('failed', message)
    assert (task(t.store, queued['id'])['status'], task(t.store, queued['id'])['attempts']) == ('failed', 1)


def test_an_expired_lease_is_claimed_again_and_the_old_worker_records_nothing(tester):
    t = tester
    queued = queue(t)
    [first] = agent_tasks.claim(t.store, 'w1', 10)
    with t.store.connect() as conn:  # w1 stalled past its lease
        conn.execute("UPDATE ka.agent_tasks SET lease_expires_at=now() - interval '1 second' WHERE id=%s", (queued['id'],))
    [second] = agent_tasks.claim(t.store, 'w2', 10)
    assert (second['worker_id'], second['attempts']) == ('w2', 2)
    assert agent_tasks.finish(t.store, first, 'succeeded', 'late answer') is False
    asyncio.run(agent_tasks.run_task(t.store, second, Agent('fresh answer')))
    assert [note['body'] for note in notifications(t.store)] == ['fresh answer']


def test_a_task_lost_on_its_last_attempt_fails(tester):
    t = tester
    queued = queue(t, status='running', attempts=3, worker_id='gone', started_at='2026-10-09T10:00:00Z',
                   lease_expires_at='2026-10-09T10:15:00Z')
    fake = Agent('never')
    [claimed] = run_due(t.store, fake)
    assert claimed['attempts'] == 4 and fake.calls == []
    [note] = notifications(t.store)
    assert (note['status'], note['body']) == ('failed', agent_tasks.LOST)
    assert task(t.store, queued['id'])['status'] == 'failed'


def test_two_workers_never_claim_the_same_task(tester):
    t = tester
    queue(t)
    with t.store.connect() as conn:
        conn.execute('SELECT id FROM ka.agent_tasks FOR UPDATE')  # another worker is claiming it right now
        assert agent_tasks.claim(t.store, 'w2', 10) == []
    assert len(agent_tasks.claim(t.store, 'w1', 10)) == 1
    assert agent_tasks.claim(t.store, 'w2', 10) == []  # running under a live lease


def test_answers_are_cleaned_capped_and_redacted(tester):
    t = tester
    queue(t)
    run_due(t.store, Agent('a\x00b key=tester-model-secret ' + 'x' * 300_000))
    [note] = notifications(t.store)
    assert note['body'].startswith('ab key=[redacted] ')
    assert len(note['body']) == agent_tasks.MAX_BODY and '\x00' not in note['body']


def test_a_deleted_event_still_gets_its_notification(tester):
    t = tester
    queued = queue(t)
    [claimed] = agent_tasks.claim(t.store, 'w1', 10)
    with t.store.connect() as conn:
        conn.execute('DELETE FROM ka.events WHERE id=%s', (queued['event_id'],))
    asyncio.run(agent_tasks.run_task(t.store, claimed, Agent('Done.')))
    [note] = notifications(t.store)
    assert (note['status'], note['event_id']) == ('succeeded', None)


def test_release_gives_a_task_back_without_counting_the_attempt(tester):
    t = tester
    queued = queue(t)
    [claimed] = agent_tasks.claim(t.store, 'w1', 10)
    agent_tasks.release(t.store, claimed)
    back = task(t.store, queued['id'])
    assert (back['status'], back['attempts'], back['worker_id'], back['lease_expires_at']) == ('queued', 0, None, None)
