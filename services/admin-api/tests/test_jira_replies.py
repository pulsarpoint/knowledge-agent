"""Repository-worker posts pending Jira replies, one at a time, retrying after 1 and 5 minutes."""

import copy
import json
from datetime import timedelta

import httpx
import psycopg
import pytest
from knowledge_admin.events import jira_replies
from knowledge_admin.events.jira_replies import CONNECTION_GONE, MAX_COMMENT, TRUNCATED, UNEXPECTED, comment_body
from test_jira import ISSUES, setup


def connection(store) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()


def pending(store, body='The answer', **values) -> int:
    jira = connection(store)
    row = {
        'project_id': jira['project_id'],
        'kind': 'agent.response',
        'status': 'succeeded',
        'title': 'Triage · jira.ticket.updated DEMO-1',
        'body': body,
        'agent_id': 'triage',
        'jira_status': 'pending',
        'jira_connection_id': jira['id'],
        'jira_issue_id': '10001',
        'jira_issue_key': 'DEMO-1',
        'jira_next_attempt_at': '2000-01-01T00:00:00Z',
        **values,
    }
    with store.connect() as conn:
        return conn.execute(
            f'INSERT INTO ka.notifications({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING id',
            tuple(row.values()),
        ).fetchone()['id']


def note(store, notification_id) -> dict:
    with store.connect() as conn:
        return conn.execute(
            'SELECT *,jira_next_attempt_at-now() AS wait FROM ka.notifications WHERE id=%s', (notification_id,)
        ).fetchone()


def a_comment(body, comment_id='10500', created='2026-10-15T10:00:01.000+0000') -> dict:
    return {'id': comment_id, 'body': body, 'created': created, 'updated': created, 'author': {'name': 'mock-admin'}}


def jira(status=201, posted=None, comments=(), requests=None):
    """A Jira that serves issue 10001 with `comments` and answers a POSTed comment with `status`."""

    def handler(request):
        if requests is not None:
            requests.append((request.method, request.url.path))
        if request.method == 'GET' and request.url.path.endswith('/comment'):
            return httpx.Response(200, json={'startAt': 0, 'total': len(comments), 'comments': list(comments)})
        if request.method == 'GET':
            return httpx.Response(200, json=copy.deepcopy(ISSUES[0]))
        if posted is not None:
            posted.append(json.loads(request.content)['body'])
        if status == 201:
            return httpx.Response(201, json={'id': '10500', 'created': '2026-10-15T10:00:01.000+0000'})
        return httpx.Response(status)

    return httpx.MockTransport(handler)


def due(store, notification_id) -> None:
    with store.connect() as conn:
        conn.execute(
            "UPDATE ka.notifications SET jira_next_attempt_at=now()-interval '1 second' WHERE id=%s",
            (notification_id,),
        )


def test_comment_body_adds_the_footer_and_cuts_long_answers():
    assert comment_body('Hi', 'Triage') == 'Hi\n\n— Triage (Knowledge Agent)'
    long = comment_body('x' * 40_000, 'Triage')
    assert len(long) <= MAX_COMMENT and long.endswith(TRUNCATED + '\n\n— Triage (Knowledge Agent)')


def test_a_pending_reply_is_posted(store, tmp_path):
    setup(store, tmp_path)
    nid, posted = pending(store), []
    assert jira_replies.run_once(store, jira(posted=posted)) is True
    row = note(store, nid)
    assert (row['jira_status'], row['jira_comment_id'], row['jira_attempts'], row['jira_next_attempt_at']) == (
        'posted',
        '10500',
        1,
        None,
    )
    assert row['jira_comment_created'].isoformat() == '2026-10-15T10:00:01+00:00'
    assert posted == ['The answer\n\n— triage (Knowledge Agent)']  # agent_id when the agent row is gone
    assert jira_replies.run_once(store, jira()) is False


def test_retries_after_one_and_five_minutes_then_fails(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store)
    for attempt, wait in ((1, timedelta(minutes=1)), (2, timedelta(minutes=5))):
        assert jira_replies.run_once(store, jira(500)) is True
        row = note(store, nid)
        assert (row['jira_status'], row['jira_attempts']) == ('pending', attempt)
        assert wait - timedelta(seconds=5) < row['wait'] <= wait
        assert row['jira_error'] == 'Jira could not complete the request'
        due(store, nid)
    assert jira_replies.run_once(store, jira(500)) is True
    row = note(store, nid)
    assert (row['jira_status'], row['jira_attempts'], row['jira_next_attempt_at']) == ('failed', 3, None)


@pytest.mark.parametrize('status', [400, 403, 404])
def test_a_refusal_fails_at_once(store, tmp_path, status):
    setup(store, tmp_path)
    nid = pending(store)
    jira_replies.run_once(store, jira(status))
    row = note(store, nid)
    assert (row['jira_status'], row['jira_attempts']) == ('failed', 1)


def test_a_missing_or_replaced_connection_fails_at_once(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store, jira_connection_id='00000000-0000-0000-0000-000000000001')
    jira_replies.run_once(store, jira())
    assert (note(store, nid)['jira_status'], note(store, nid)['jira_error']) == ('failed', CONNECTION_GONE)


def test_a_disabled_connection_fails_at_once_with_the_settings_message(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store)
    with store.connect() as conn:
        conn.execute('UPDATE ka.jira_connections SET enabled=false')
    jira_replies.run_once(store, jira())
    row = note(store, nid)
    assert (row['jira_status'], row['jira_error']) == (
        'failed',
        'This Jira Data Center connection is disabled or unsupported',
    )


def test_an_invalid_jira_response_is_retried_and_the_retry_finds_the_comment(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store)
    jira_replies.run_once(store, httpx.MockTransport(lambda request: httpx.Response(201, json={'id': '1'})))
    assert (note(store, nid)['jira_status'], note(store, nid)['jira_attempts']) == ('pending', 1)
    due(store, nid)
    body = 'The answer\n\n— triage (Knowledge Agent)'
    requests = []
    jira_replies.run_once(store, jira(comments=[a_comment(body, '1')], requests=requests))
    row = note(store, nid)
    assert (row['jira_status'], row['jira_comment_id'], row['jira_attempts'], row['jira_error']) == (
        'posted',
        '1',
        2,
        None,
    )
    assert [method for method, _ in requests if method == 'POST'] == []


def test_a_retry_records_our_comment_already_on_the_ticket_without_posting(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store, jira_attempts=1)
    body = 'The answer\n\n— triage (Knowledge Agent)'
    comments = [a_comment('Someone else', '10400'), a_comment(body, '10501', '2026-10-15T10:00:02.000+0000')]
    requests = []
    assert jira_replies.run_once(store, jira(comments=comments, requests=requests)) is True
    row = note(store, nid)
    assert (row['jira_status'], row['jira_comment_id'], row['jira_attempts']) == ('posted', '10501', 2)
    assert row['jira_comment_created'].isoformat() == '2026-10-15T10:00:02+00:00'
    assert ('POST', '/jira/rest/api/2/issue/10001/comment') not in requests


def test_a_retry_that_does_not_find_our_comment_posts_it(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store, jira_attempts=1)
    posted = []
    jira_replies.run_once(store, jira(posted=posted, comments=[a_comment('The answer')]))  # no footer: not ours
    row = note(store, nid)
    assert (row['jira_status'], row['jira_comment_id'], row['jira_attempts']) == ('posted', '10500', 2)
    assert posted == ['The answer\n\n— triage (Knowledge Agent)']


def test_the_first_attempt_does_not_read_the_comments(store, tmp_path):
    setup(store, tmp_path)
    pending(store)
    requests = []
    jira_replies.run_once(store, jira(requests=requests))
    assert [method for method, _ in requests] == ['POST']


def test_a_failed_comment_read_follows_the_retry_rules(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store, jira_attempts=1)
    jira_replies.run_once(store, httpx.MockTransport(lambda request: httpx.Response(500)))
    row = note(store, nid)
    assert (row['jira_status'], row['jira_attempts']) == ('pending', 2)
    due(store, nid)
    jira_replies.run_once(store, httpx.MockTransport(lambda request: httpx.Response(404)))
    assert (note(store, nid)['jira_status'], note(store, nid)['jira_attempts']) == ('failed', 3)


def test_a_database_error_looking_up_the_connection_is_retried(store, tmp_path, monkeypatch):
    setup(store, tmp_path)
    nid = pending(store)

    def unavailable(*_):
        raise psycopg.OperationalError('connection lost')

    monkeypatch.setattr(jira_replies.jira_sync, 'credentials', unavailable)
    assert jira_replies.run_once(store, jira()) is True
    row = note(store, nid)
    assert (row['jira_status'], row['jira_attempts']) == ('pending', 1)
    assert row['jira_error'] != UNEXPECTED


def test_a_row_locked_by_another_worker_is_skipped(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store)
    with store.connect() as other:
        other.execute('SELECT id FROM ka.notifications WHERE id=%s FOR UPDATE', (nid,))
        assert jira_replies.run_once(store, jira()) is False
    assert jira_replies.run_once(store, jira()) is True


def test_an_unexpected_error_fails_the_row_and_the_loop_goes_on(store, tmp_path, monkeypatch):
    setup(store, tmp_path)
    nid = pending(store)
    monkeypatch.setattr(jira_replies, 'comment_body', lambda *_: 1 / 0)
    assert jira_replies.run_once(store, jira()) is True
    assert (note(store, nid)['jira_status'], note(store, nid)['jira_error']) == ('failed', UNEXPECTED)
