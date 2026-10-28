"""0110: handlers may post their agent's answer to Jira; a notification carries the comment's delivery state."""

from pathlib import Path

import psycopg
import pytest
from test_event_processor import a_handler

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_notification(conn, project, **values) -> dict:
    row = {
        'project_id': project,
        'kind': 'agent.response',
        'status': 'succeeded',
        'title': 'Triage · jira.ticket.updated DEMO-1',
        'body': 'Answer',
        **values,
    }
    return conn.execute(
        f'INSERT INTO ka.notifications({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING *',
        tuple(row.values()),
    ).fetchone()


def test_defaults(store):
    handler = a_handler(store)
    with store.connect() as conn:
        note = a_notification(conn, store.project(conn, 'shop'))
    assert handler['reply_to_jira'] is False
    assert (note['jira_status'], note['jira_attempts'], note['jira_next_attempt_at']) == (None, 0, None)


def test_only_send_to_agent_handlers_reply_to_jira(store):
    with pytest.raises(psycopg.errors.CheckViolation):
        a_handler(store, reply_to_jira=True)  # action emit


@pytest.mark.parametrize(
    'values',
    [
        {'jira_status': 'pending'},  # no next attempt, no connection
        {'jira_status': 'pending', 'jira_next_attempt_at': '2026-10-15T10:00:00Z'},  # no connection or issue
        {'jira_status': 'posted', 'jira_connection_id': '00000000-0000-0000-0000-000000000001', 'jira_issue_id': '1'},
        {'jira_status': 'sent'},
        {'jira_next_attempt_at': '2026-10-15T10:00:00Z'},  # a next attempt without pending
        {'jira_status': 'failed', 'jira_error': 'x' * 2001},
    ],
)
def test_delivery_checks(store, values):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            a_notification(conn, project, **values)


def test_a_valid_pending_posted_and_failed_row(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        connection = {'jira_connection_id': '00000000-0000-0000-0000-000000000001', 'jira_issue_id': '10001'}
        a_notification(conn, project, jira_status='pending', jira_next_attempt_at='2026-10-15T10:00:00Z', **connection)
        a_notification(
            conn,
            project,
            jira_status='posted',
            jira_comment_id='5',
            jira_comment_created='2026-10-15T10:00:01Z',
            **connection,
        )
        a_notification(
            conn, project, jira_status='failed', jira_error='This event did not come from a Jira connection.'
        )


def test_down_and_up_again(store):
    with store.connect() as conn:
        run(conn, '0147_session_review.down.sql')  # newer migrations first
        run(conn, '0146_run_retries.down.sql')
        run(conn, '0110_jira_replies.down.sql')
        columns = {
            row['column_name']
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name IN ('notifications','event_handlers')"
            )
        }
        assert not any(name.startswith('jira_') for name in columns) and 'reply_to_jira' not in columns
        run(conn, '0110_jira_replies.up.sql')
        assert conn.execute("SELECT to_regclass('ka.notifications_jira_due') IS NOT NULL AS ok").fetchone()['ok']
