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
from test_jira_replies import a_comment, jira, note, pending
from test_jira_reply_notifications import prepared
from test_repositories import HEADERS

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
    """The retry's time has come: the session's retry_at and its re-queued inputs."""
    with store.connect() as conn:
        retry_at = conn.execute('SELECT retry_at FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()['retry_at']
        conn.execute(
            """UPDATE ka.agent_session_inputs SET not_before=now()-interval '1 second'
            WHERE session_id=%s AND status='queued' AND not_before=%s""",
            (sid, retry_at),
        )
        conn.execute("UPDATE ka.agent_sessions SET retry_at=now()-interval '1 second' WHERE id=%s", (sid,))


def now(store):
    with store.connect() as conn:
        return conn.execute('SELECT now() AS now').fetchone()['now']


def add_input(store, sid, text, *, kind='message', due_in='-1 second') -> None:
    """A queued input of the run: a person's message, or a wake-up due `due_in` from now."""
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text,kind,not_before)
            SELECT s.id,(SELECT coalesce(max(seq),0)+1 FROM ka.agent_session_inputs WHERE session_id=s.id),s.actor_id,
                %s,%s,CASE WHEN %s='wakeup' THEN now()+%s::interval END
            FROM ka.agent_sessions s WHERE s.id=%s""",
            (text, kind, kind, due_in, sid),
        )


def last_end(store, sid) -> dict:
    return [e['payload'] for e in events(store, sid) if e['kind'] == 'end'][-1]


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
    assert row['status_message'].endswith(' UTC: the model could not be reached')
    assert (
        row['retry_at'] is not None and RETRIES[0] - timedelta(seconds=5) < row['retry_at'] - now(store) <= RETRIES[0]
    )
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
        failure_comment('the model could not be reached'),
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
    assert (queued['status'], queued['not_before']) == ('queued', row['retry_at'])  # due now: Stop and Send find it
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


# I1: the retry delivers exactly the failed turn's inputs, at retry_at


def test_a_wake_up_due_during_the_wait_joins_the_retry_instead_of_running_alone(worker):
    sid, _ = prepared(worker)
    store = worker.store
    fake = FakeAgent([FAILING, answering('Triaged with the check.')])
    attempt(store, fake)
    add_input(store, sid, 'Check the deploy.', kind='wakeup')  # due now, during the wait
    assert shared.claim(store, 'worker-1', [], 4) == []  # nothing runs before retry_at
    due(store, sid)
    attempt(store, fake)
    row = session_row(store, sid)
    assert (row['status'], row['turns'], row['retry_at']) == ('waiting', 1, None)
    assert fake.calls[1]['task'].startswith('Triage DEMO-1\n\nScheduled wake-up (set ')
    assert fake.calls[1]['task'].endswith('Check the deploy.')
    assert [item['status'] for item in inputs(store, sid)] == ['delivered', 'delivered']
    [note_row] = jira_notes(store, sid)
    assert note_row['body'] == 'Triaged with the check.'


def test_a_message_sent_while_a_wake_up_turn_waits_runs_the_retry_now_with_the_wake_up(worker):
    sid, *_ = a_run(worker)
    store = worker.store
    with store.connect() as conn:  # the run's turn 1 was a wake-up
        conn.execute(
            "UPDATE ka.agent_session_inputs SET kind='wakeup',not_before=now()-interval '1 second' WHERE session_id=%s",
            (sid,),
        )
    fake = FakeAgent([FAILING, answering('Done.')])
    attempt(store, fake)
    assert asyncio.run(post(worker, f'/api/sessions/{sid}/messages', {'text': 'Go on.'})).status_code == 202
    attempt(store, fake)
    row = session_row(store, sid)
    assert (row['status'], row['turns']) == ('waiting', 1)
    assert fake.calls[1]['task'].startswith('Scheduled wake-up (set ')
    assert fake.calls[1]['task'].endswith('Triage DEMO-1\n\nGo on.')
    assert [item['status'] for item in inputs(store, sid)] == ['delivered', 'delivered']


def test_a_waiting_run_is_taken_by_a_second_worker_after_the_first_ones_lease(worker):
    sid, _ = prepared(worker)
    store = worker.store
    fake = FakeAgent([FAILING, answering()])
    attempt(store, fake)
    with store.connect() as conn:  # worker-1 died holding the lease
        conn.execute(
            "UPDATE ka.agent_sessions SET worker_id='worker-1',lease_expires_at=now()+interval '1 minute' WHERE id=%s",
            (sid,),
        )
    due(store, sid)
    assert shared.claim(store, 'worker-2', [], 4) == []  # still leased
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (sid,))

    async def scenario():
        [row] = shared.claim(store, 'worker-2', [], 4)
        assert row['status'] == 'retrying'
        await asyncio.wait_for(native_sessions.NativeRunner(store, row, fake, poll=0.02, idle_release=0).run(), 5)

    asyncio.run(scenario())
    row = session_row(store, sid)
    assert (row['status'], row['turns']) == ('waiting', 1)
    assert len(jira_notes(store, sid)) == 1


# I2: Stop and Close end a retrying run for good


def test_stop_while_waiting_for_a_retry_stops_the_run_without_a_failure_comment(worker):
    sid, _ = prepared(worker)
    store = worker.store
    attempt(store, FakeAgent([FAILING]))
    response = asyncio.run(post(worker, f'/api/sessions/{sid}/stop'))
    assert response.status_code == 202, response.text
    row = session_row(store, sid)
    assert (row['status'], row['status_message'], row['retries'], row['retry_at']) == (
        'stopped',
        shared.STOPPED,
        0,
        None,
    )
    assert [item['status'] for item in inputs(store, sid)] == ['discarded']
    assert shared.claim(store, 'worker-1', [], 4) == []
    assert last_end(store, sid) == {'status': 'stopped'}
    [note_row] = jira_notes(store, sid)
    assert (note_row['status'], note_row['body'], note_row['jira_status'], note_row['jira_text']) == (
        'failed',
        shared.STOPPED,
        None,
        None,
    )


def test_stop_during_an_attempt_that_then_fails_stops_the_run_without_a_failure_comment(worker):
    sid, _ = prepared(worker)
    store = worker.store

    async def stop_pressed(turn):
        with store.connect() as conn:
            conn.execute('UPDATE ka.agent_sessions SET stop_requested_at=now() WHERE id=%s', (sid,))

    attempt(store, FakeAgent([{'steps': [stop_pressed], 'error': CONNECTION}]))
    row = session_row(store, sid)
    assert (row['status'], row['retries'], row['retry_at']) == ('stopped', 0, None)
    assert last_end(store, sid) == {'status': 'stopped'}
    [note_row] = jira_notes(store, sid)
    assert (note_row['jira_status'], note_row['jira_text']) == (None, None)


def test_close_while_waiting_leaves_a_final_end_event(worker):
    sid, _ = prepared(worker)
    store = worker.store
    attempt(store, FakeAgent([FAILING]))
    asyncio.run(post(worker, f'/api/sessions/{sid}/close'))
    assert last_end(store, sid) == {'status': 'stopped'}
    listed = worker.client.get(f'/api/agent-runs?key_prefix=cc:{sid}:', headers=HEADERS).json()
    assert [run['status'] for run in listed['runs']] == ['stopped']


def test_a_second_failure_comment_is_not_taken_for_an_older_identical_one(store, tmp_path):
    setup(store, tmp_path)
    comment = failure_comment('the model could not be reached')
    footer = f'{comment}\n\n— triage (Knowledge Agent)'
    nid = pending(  # a retried post of a failure comment written after an earlier, identical one was posted
        store, body='failed', status='failed', jira_text=comment, jira_attempts=1, created_at='2026-10-20T00:00:00Z'
    )
    posted: list[str] = []
    assert jira_replies.run_once(store, jira(posted=posted, comments=[a_comment(footer, '10400')])) is True
    assert posted == [footer]
    assert note(store, nid)['jira_comment_id'] == '10500'


# Re-review: manual Retry and Stop, a Stop that turns a failure into stopped


def test_stop_after_a_manual_retry_before_a_worker_takes_it_discards_every_input(worker):
    sid, *_ = a_run(worker)
    store = worker.store
    with store.connect() as conn:  # turn 1 was a wake-up
        conn.execute(
            "UPDATE ka.agent_session_inputs SET kind='wakeup',not_before=now()-interval '1 second' WHERE session_id=%s",
            (sid,),
        )
    attempt(store, FakeAgent([{'steps': [], 'error': RuntimeError('boom')}]))
    assert retry(worker, sid).status_code == 202
    row = session_row(store, sid)
    [queued] = inputs(store, sid)
    assert row['retry_at'] is not None and queued['not_before'] == row['retry_at']
    assert asyncio.run(post(worker, f'/api/sessions/{sid}/stop')).status_code == 202
    assert [item['status'] for item in inputs(store, sid)] == ['discarded']
    assert session_row(store, sid)['status'] == 'stopped'
    assert shared.claim(store, 'worker-1', [], 4) == []


def test_finish_turn_ends_the_record_stopped_when_stop_turns_a_failure_into_stopped(worker):
    from datetime import UTC, datetime

    sid, _ = prepared(worker)
    store = worker.store
    [row] = shared.claim(store, 'worker-1', [], 4)
    runner = shared.SessionRunner(store, row)
    number, _ = runner.next_turn()
    with store.connect() as conn:  # the attempt's record already ended 'retrying', then Stop was pressed
        shared.append_run_events(conn, sid, [('end', {'status': 'retrying'})])
        conn.execute('UPDATE ka.agent_sessions SET stop_requested_at=now() WHERE id=%s', (sid,))
    runner.finish_turn(
        number,
        'failed',
        'Agent execution failed.',
        usage=None,
        reason='the model could not be reached',
        retry_at=datetime.now(UTC) + RETRIES[0],
        retry_status='Retry 1 of 3',
    )
    assert session_row(store, sid)['status'] == 'stopped'
    assert last_end(store, sid) == {'status': 'stopped'}
    [note_row] = jira_notes(store, sid)
    assert note_row['jira_status'] is None
