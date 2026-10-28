"""When an event run's first turn ends, its notification says whether the answer (or, for a failed run, the failure
comment) goes to Jira."""

import pytest
from knowledge_admin.knowledge_agents.retries import failure_comment
from knowledge_admin.sessions.text import NO_ANSWER, NOT_FROM_JIRA, notify_event_run
from psycopg.types.json import Jsonb
from test_event_run_notifications import a_run
from test_generations import sessions  # noqa: F401  (fixture)


def prepared(sessions, *, reply=True, source='jira', source_id=True, issue_id='10001'):
    sid, agent_id, event, handler = a_run(sessions)
    with sessions.store.connect() as conn:
        conn.execute('UPDATE ka.event_handlers SET reply_to_jira=%s WHERE id=%s', (reply, handler['id']))
        conn.execute(
            'UPDATE ka.events SET source=%s,source_id=%s,payload=%s WHERE id=%s',
            (
                source,
                event['source_id'] if source_id else None,
                Jsonb({'issue_id': issue_id, 'key': 'DEMO-1'} if issue_id else {}),
                event['id'],
            ),
        )
    return sid, event


def notified(sessions, sid, status='waiting', text='The answer') -> dict:
    with sessions.store.connect() as conn:
        notify_event_run(conn, sid, 1, status, text)
        return conn.execute('SELECT * FROM ka.notifications WHERE session_id=%s', (sid,)).fetchone()


def test_a_succeeded_jira_run_with_the_option_is_pending(sessions):
    sid, event = prepared(sessions)
    row = notified(sessions, sid)
    assert (row['jira_status'], row['jira_connection_id'], row['jira_issue_id'], row['jira_issue_key']) == (
        'pending',
        event['source_id'],
        '10001',
        'DEMO-1',
    )
    assert row['jira_next_attempt_at'] is not None and row['jira_attempts'] == 0


def test_an_empty_answer_is_not_posted(sessions):
    sid, _ = prepared(sessions)
    row = notified(sessions, sid, text='  ')
    assert (row['jira_status'], row['jira_issue_key'], row['jira_next_attempt_at'], row['body']) == (
        None,
        None,
        None,
        NO_ANSWER,
    )


def test_a_simulated_jira_event_fails_the_delivery(sessions):
    sid, _ = prepared(sessions, source_id=False)
    row = notified(sessions, sid)
    assert (row['jira_status'], row['jira_error'], row['jira_next_attempt_at']) == ('failed', NOT_FROM_JIRA, None)


@pytest.mark.parametrize(
    'options,status',
    [
        ({'reply': False}, 'waiting'),
        ({'source': 'manual', 'source_id': False}, 'waiting'),
        ({'reply': False}, 'failed'),
        ({}, 'stopped'),
        ({}, 'closed'),
    ],
)
def test_no_delivery(sessions, options, status):
    sid, _ = prepared(sessions, **options)
    row = notified(sessions, sid, status=status, text='Agent execution failed.')
    assert (row['jira_status'], row['jira_connection_id'], row['jira_issue_id'], row['jira_text']) == (
        None,
        None,
        None,
        None,
    )


def test_a_failed_jira_run_posts_the_failure_comment(sessions):
    sid, _ = prepared(sessions)
    with sessions.store.connect() as conn:
        notify_event_run(conn, sid, 1, 'failed', 'Agent execution failed (APITimeoutError).', reason='timeout')
        row = conn.execute('SELECT * FROM ka.notifications WHERE session_id=%s', (sid,)).fetchone()
    assert (row['status'], row['body'], row['jira_status'], row['jira_issue_id'], row['jira_text']) == (
        'failed',
        'Agent execution failed (APITimeoutError).',
        'pending',
        '10001',
        failure_comment('timeout'),
    )
