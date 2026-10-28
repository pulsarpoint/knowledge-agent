"""Run retries in the session worker and the API: an event run's turn that fails on a transient model error is queued
again (not_before, the retry counter, the 'retrying' status) and runs as the same turn when due; it notifies once,
for its final outcome. When it gives up, a Jira-started run posts a short failure comment and gives the ticket back.
Chats never retry by themselves; POST /api/sessions/{id}/retry retries any failed turn now."""

import asyncio
from datetime import timedelta

import httpx
import openai
import pytest
from knowledge_admin.events import jira_replies
from knowledge_admin.knowledge_agents import native_sessions
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.knowledge_agents.retries import RETRIES, failure_comment
from knowledge_admin.sessions import run_record
from knowledge_admin.sessions.rows import CLOSED
from native_fakes import FakeAgent, llm, message
from run_helpers import events, post, session_row
from test_event_run_notifications import a_run, notifications
from test_generations import sessions  # noqa: F401  (fixture)
from test_jira import setup
from test_jira_give_back import prepared_with
from test_jira_replies import jira, note, pending
from test_jira_reply_notifications import prepared

CONNECTION = openai.APIConnectionError(request=httpx.Request('POST', 'http://model.test/v1/responses'))
FAILING = {'steps': [llm(message('Reading the ticket'))], 'error': CONNECTION}


def answering(text='Triaged: a timeout.') -> dict:
    return {'steps': [llm(message(text))], 'answer': text}


@pytest.fixture
def worker(sessions, monkeypatch):  # noqa: F811
    monkeypatch.setattr(run_record, '_store', sessions.store)
    return sessions


def attempt(store, fake) -> None:
    """Claim the run and serve it until nothing is due (as an event run does: it gives its slot back at once)."""

    async def scenario():
        [row] = shared.claim(store, 'worker-1', [], 4)
        await asyncio.wait_for(native_sessions.NativeRunner(store, row, fake, poll=0.02, idle_release=0).run(), 5)

    asyncio.run(scenario())


def inputs(store, sid) -> list[dict]:
    with store.connect() as conn:
        return conn.execute(
            """SELECT status,turn,delivered_at,not_before,not_before-now() AS wait FROM ka.agent_session_inputs
            WHERE session_id=%s ORDER BY seq""",
            (sid,),
        ).fetchall()


def due(store, sid) -> None:
    with store.connect() as conn:
        conn.execute(
            """UPDATE ka.agent_session_inputs SET not_before=now()-interval '1 second'
            WHERE session_id=%s AND status='queued' AND not_before IS NOT NULL""",
            (sid,),
        )


def jira_notes(store, sid) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.notifications WHERE session_id=%s ORDER BY id', (sid,)).fetchall()


def test_a_transient_failure_queues_the_turn_again_with_its_wait_and_attempt(worker):
    sid, *_ = a_run(worker)
    store = worker.store
    attempt(store, FakeAgent([FAILING]))
    row = session_row(store, sid)
    assert (row['status'], row['turns'], row['retries']) == ('retrying', 1, 1)
    assert row['status_message'].startswith('Retry 1 of 3 at ')
    assert row['status_message'].endswith(' UTC: connection error')
    [queued] = inputs(store, sid)
    assert (queued['status'], queued['turn'], queued['delivered_at']) == ('queued', None, None)
    assert RETRIES[0] - timedelta(seconds=5) < queued['wait'] <= RETRIES[0]
    assert notifications(store) == []  # not before the final outcome
    assert shared.claim(store, 'worker-1', [], 4) == []  # not due yet
    recorded = [(e['kind'], e['payload']) for e in events(store, sid)]
    error = next(payload for kind, payload in recorded if kind == 'error')
    assert (error['type'], error['attempt'], error['retry_in_seconds']) == ('APIConnectionError', 1, 30)
    assert recorded[-1] == ('end', {'status': 'retrying'})


def test_success_on_a_retry_is_the_same_turn_with_one_notification_and_one_jira_comment(worker):
    sid, _ = prepared(worker)
    store = worker.store
    fake = FakeAgent([FAILING, answering()])
    attempt(store, fake)
    due(store, sid)
    attempt(store, fake)
    row = session_row(store, sid)
    assert (row['status'], row['turns'], row['status_message']) == ('waiting', 1, '')
    assert fake.calls[1]['task'] == 'Triage DEMO-1' and fake.calls[1]['history'] == []  # nothing kept from attempt 1
    [note_row] = jira_notes(store, sid)
    assert (note_row['status'], note_row['body'], note_row['jira_status'], note_row['jira_text']) == (
        'succeeded',
        'Triaged: a timeout.',
        'pending',
        None,
    )
    recorded = events(store, sid)
    assert {e['run_key'] for e in recorded} == {f'cc:{sid}:1'}
    assert [e['seq'] for e in recorded] == list(range(1, len(recorded) + 1))
    kinds = [e['kind'] for e in recorded]
    assert kinds.count('start') == 1 and kinds.count('end') == 2
    assert recorded[-1]['payload'] == {'status': 'completed'}
    again = [e['payload'] for e in recorded if e['kind'] == 'input' and e['payload'].get('retry')]
    assert again == [{'task': 'Triage DEMO-1', 'retry': True}]


def test_giving_up_after_three_retries_posts_the_failure_comment_and_gives_the_ticket_back(worker):
    sid = prepared_with(worker)
    store = worker.store
    fake = FakeAgent([FAILING] * 4)
    for retries, wait in enumerate(RETRIES, start=1):
        attempt(store, fake)
        row = session_row(store, sid)
        assert (row['status'], row['retries'], row['turns']) == ('retrying', retries, 1)
        assert row['status_message'].startswith(f'Retry {retries} of 3 at ')
        [queued] = inputs(store, sid)
        assert wait - timedelta(seconds=5) < queued['wait'] <= wait
        assert jira_notes(store, sid) == []
        due(store, sid)
    attempt(store, fake)
    row = session_row(store, sid)
    assert (row['status'], row['retries'], row['turns']) == ('failed', 3, 1)
    assert len(fake.calls) == 4
    [note_row] = jira_notes(store, sid)
    assert (note_row['status'], note_row['jira_status'], note_row['jira_text']) == (
        'failed',
        'pending',
        failure_comment('connection error'),
    )
    assert note_row['body'] == row['status_message']  # the in-app notification stays as before
    assert (note_row['give_back_key'], note_row['give_back_status']) == ('ana', 'pending')
    errors = [e['payload'] for e in events(store, sid) if e['kind'] == 'error']
    assert [error['attempt'] for error in errors] == [1, 2, 3, 4]
    assert 'retry_in_seconds' not in errors[-1]


def test_a_failure_that_is_not_transient_is_not_retried_and_comments_at_once(worker):
    sid, _ = prepared(worker)
    store = worker.store
    attempt(store, FakeAgent([{'steps': [], 'error': RuntimeError('boom')}]))
    row = session_row(store, sid)
    assert (row['status'], row['retries']) == ('failed', 0)
    assert [item['status'] for item in inputs(store, sid)] == ['delivered']
    [note_row] = jira_notes(store, sid)
    assert (note_row['jira_status'], note_row['jira_text']) == (
        'pending',
        failure_comment('an unexpected error (RuntimeError)'),
    )


def test_a_chat_never_retries_by_itself(worker):
    sid, *_ = a_run(worker, purpose='chat')
    store = worker.store
    attempt(store, FakeAgent([FAILING]))
    row = session_row(store, sid)
    assert (row['status'], row['retries']) == ('failed', 0)
    assert row['status_message'].startswith('Agent execution failed (APIConnectionError')
    assert [item['status'] for item in inputs(store, sid)] == ['delivered']


def test_a_stopped_or_closed_event_run_gets_no_failure_comment(worker):
    sid, _ = prepared(worker)
    assert asyncio.run(post(worker, f'/api/sessions/{sid}/close')).status_code == 200
    [note_row] = jira_notes(worker.store, sid)
    assert (note_row['status'], note_row['jira_status'], note_row['jira_text']) == ('failed', None, None)


def test_closing_a_run_that_waits_for_a_retry_notifies_once(worker):
    sid, _ = prepared(worker)
    store = worker.store
    attempt(store, FakeAgent([FAILING]))
    assert asyncio.run(post(worker, f'/api/sessions/{sid}/close')).status_code == 200
    [note_row] = jira_notes(store, sid)
    assert (note_row['status'], note_row['body'], note_row['jira_status']) == ('failed', 'Closed.', None)
    assert [item['status'] for item in inputs(store, sid)] == ['discarded']


def test_a_message_sent_while_waiting_retries_now_with_it(worker):
    sid, *_ = a_run(worker)
    store = worker.store
    fake = FakeAgent([FAILING, answering('Both done.')])
    attempt(store, fake)
    assert asyncio.run(post(worker, f'/api/sessions/{sid}/messages', {'text': 'Also check DEMO-2.'})).status_code == 202
    attempt(store, fake)
    row = session_row(store, sid)
    assert (row['status'], row['turns']) == ('waiting', 1)
    assert fake.calls[1]['task'] == 'Triage DEMO-1\n\nAlso check DEMO-2.'


def test_a_turn_the_worker_lost_posts_the_failure_comment(worker):
    sid, _ = prepared(worker)
    store = worker.store
    with store.connect() as conn:
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
    [note_row] = jira_notes(store, sid)
    assert (note_row['jira_status'], note_row['jira_text']) == (
        'pending',
        failure_comment('the worker restarted during the turn'),
    )


# The Jira reply worker posts the failure comment in place of the notification's body


def test_the_failure_comment_is_posted_in_place_of_the_body(store, tmp_path):
    setup(store, tmp_path)
    comment = failure_comment('timeout')
    nid, posted = (
        pending(store, body='Agent execution failed (APITimeoutError).', status='failed', jira_text=comment),
        [],
    )
    assert jira_replies.run_once(store, jira(posted=posted)) is True
    assert note(store, nid)['jira_status'] == 'posted'
    assert posted == [f'{comment}\n\n— triage (Knowledge Agent)']


# POST /api/sessions/{id}/retry


def retry(sessions, sid):
    return asyncio.run(post(sessions, f'/api/sessions/{sid}/retry'))


def test_retry_queues_a_failed_chat_turn_now_and_it_runs_as_the_same_turn(worker):
    sid, *_ = a_run(worker, purpose='chat')
    store = worker.store
    fake = FakeAgent([FAILING, answering('Hello again.')])
    attempt(store, fake)
    response = retry(worker, sid)
    assert response.status_code == 202, response.text
    assert response.json() == {'id': str(sid), 'status': 'retrying'}
    row = session_row(store, sid)
    assert (row['status'], row['retries'], row['turns']) == ('retrying', 0, 1)
    assert row['retried_at'] is not None
    [queued] = inputs(store, sid)
    assert (queued['status'], queued['not_before']) == ('queued', None)
    attempt(store, fake)
    row = session_row(store, sid)
    assert (row['status'], row['turns']) == ('waiting', 1)
    assert fake.calls[1]['task'] == 'Triage DEMO-1'


@pytest.mark.parametrize('status', ['starting', 'working', 'waiting', 'retrying', 'stopped'])
def test_retry_is_refused_unless_the_latest_turn_failed(worker, status):
    sid, *_ = a_run(worker, purpose='chat')
    with worker.store.connect() as conn:
        conn.execute('UPDATE ka.agent_sessions SET status=%s,turns=1 WHERE id=%s', (status, sid))
    response = retry(worker, sid)
    assert response.status_code == 409
    assert response.json()['error']['message'] in (
        'The agent is working on this run; there is nothing to retry.',
        'Only a failed turn can be retried.',
    )


def test_retry_is_refused_on_a_closed_run_and_a_failed_run_without_turns(worker):
    sid, *_ = a_run(worker, purpose='chat')
    with worker.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='failed' WHERE id=%s", (sid,))
    assert retry(worker, sid).status_code == 409  # failed before its first turn: nothing was delivered
    asyncio.run(post(worker, f'/api/sessions/{sid}/close'))
    response = retry(worker, sid)
    assert (response.status_code, response.json()['error']['message']) == (409, CLOSED)


def test_retry_of_an_unknown_run_is_404(worker):
    assert retry(worker, '00000000-0000-0000-0000-000000000000').status_code == 404


def test_a_manual_retry_after_the_failure_comment_posts_its_answer_as_a_normal_reply(worker):
    sid, _ = prepared(worker)
    store = worker.store
    fake = FakeAgent([{'steps': [], 'error': RuntimeError('boom')}, answering('Fixed: restart the pod.')])
    attempt(store, fake)
    [failed] = jira_notes(store, sid)
    assert (failed['status'], failed['jira_status']) == ('failed', 'pending')
    assert retry(worker, sid).status_code == 202
    attempt(store, fake)
    first, second = jira_notes(store, sid)
    assert first['id'] == failed['id']
    assert (second['status'], second['body'], second['jira_status'], second['jira_text']) == (
        'succeeded',
        'Fixed: restart the pod.',
        'pending',
        None,
    )
    with store.connect() as conn:  # still one notification per outcome: a late duplicate writes nothing
        shared.notify_event_run(conn, sid, 1, 'waiting', 'again')
    assert len(jira_notes(store, sid)) == 2
