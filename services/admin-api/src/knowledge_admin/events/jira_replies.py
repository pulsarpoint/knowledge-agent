"""Event run answers posted as Jira comments. A thread in repository-worker takes one pending notification at a time
(FOR UPDATE SKIP LOCKED: several workers never post the same comment), posts its answer on the ticket with the
connection's token and records the comment, or why it could not: retried after 1 and 5 minutes, failed after the
third attempt; Jira refusing the comment or a missing connection fails at once. A retry first looks for the comment on
the ticket (an earlier attempt may have posted it unrecorded) so a reply is posted once.
A handler with give_back stores who gets the ticket back (give_back_key); only after the comment is posted, a later
claim assigns the ticket to them (on the same retry schedule; the comment is never posted again)."""

import logging
import threading
from datetime import datetime, timedelta

import httpx
import psycopg
from fastapi import HTTPException

from knowledge_admin.jira import sync as jira_sync
from knowledge_admin.jira.client import JiraClient
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

POLL_SECONDS = 5
MAX_COMMENT = 32_000
TRUNCATED = '… (truncated; the full answer is in Knowledge Agent)'
RETRIES = (timedelta(minutes=1), timedelta(minutes=5))  # the waits after attempts 1 and 2; attempt 3 is the last
REFUSED = {400, 403, 404}  # Jira refused the comment: trying again cannot help
CONNECTION_GONE = 'The Jira connection that recorded this event no longer exists.'
DATABASE_UNAVAILABLE = 'The Knowledge Agent database was unavailable'
UNEXPECTED = 'Posting to Jira failed unexpectedly. See the repository-worker log.'
NOT_POSTED = 'Not assigned back: the comment was not posted.'

CLAIM = """SELECT n.id,n.project_id,n.body,n.jira_connection_id,n.jira_issue_id,n.jira_attempts,n.give_back_name,
    coalesce(a.name,n.agent_id,'Agent') AS agent_name
    FROM ka.notifications n LEFT JOIN ka.agents a ON a.id=n.agent_id
    WHERE n.jira_status='pending' AND n.jira_next_attempt_at<=now()
    ORDER BY n.jira_next_attempt_at,n.id LIMIT 1 FOR UPDATE OF n SKIP LOCKED"""
CLAIM_GIVE_BACK = """SELECT id,project_id,jira_connection_id,jira_issue_id,give_back_key,give_back_attempts
    FROM ka.notifications WHERE give_back_status='pending' AND give_back_next_attempt_at<=now()
    ORDER BY give_back_next_attempt_at,id LIMIT 1 FOR UPDATE SKIP LOCKED"""


class Failed(Exception):
    def __init__(self, message: str, final: bool):
        super().__init__(message)
        self.message, self.final = message, final


def comment_body(answer: str, agent_name: str, give_back_name: str | None = None) -> str:
    footer = f'\n\n— {agent_name} (Knowledge Agent)'
    if give_back_name:
        footer = f'\n\nAssigned back to {give_back_name}.' + footer
    if len(answer) + len(footer) > MAX_COMMENT:
        answer = answer[: MAX_COMMENT - len(footer) - len(TRUNCATED) - 1].rstrip() + '\n' + TRUNCATED
    return answer + footer


def posted_already(client: JiraClient, issue_id: str, body: str) -> dict | None:
    """Our comment when Jira already has it: an earlier attempt may have posted it without us recording it (a
    response we could not read, a timeout after Jira committed, a database error after the POST)."""
    _, comments = client.issue_with_comments(issue_id)
    for comment in comments:
        if comment['body'] == body:
            return {'id': comment['id'], 'created': datetime.fromisoformat(comment['created'])}
    return None


def jira_access(store: RepositoryStore, row: dict) -> tuple[dict, str]:
    """The connection's settings and token, when it is still the one that recorded the event."""
    try:
        with store.connect() as conn:
            current = conn.execute(
                "SELECT id FROM ka.jira_connections WHERE project_id=%s AND name='primary'", (row['project_id'],)
            ).fetchone()
        if current is None or current['id'] != row['jira_connection_id']:
            raise Failed(CONNECTION_GONE, final=True)
        return jira_sync.credentials(store, row['project_id'])
    except HTTPException as exc:  # 400: settings incomplete or disabled; 503: the token cannot be read
        raise Failed(str(exc.detail), final=exc.status_code == 400) from None
    except psycopg.Error:  # a transient database error: try again later
        raise Failed(DATABASE_UNAVAILABLE, final=False) from None


def post(store: RepositoryStore, row: dict, transport: httpx.BaseTransport | None) -> dict:
    """Post the reply, or on a retry first look for it on the ticket so it is posted once."""
    config, token = jira_access(store, row)
    body = comment_body(row['body'], row['agent_name'], row['give_back_name'])
    try:
        with JiraClient(config['base_url'], token, transport) as client:
            if row['jira_attempts'] >= 1 and (found := posted_already(client, row['jira_issue_id'], body)):
                return found
            return client.add_comment(row['jira_issue_id'], body)
    except HTTPException as exc:
        raise Failed(str(exc.detail), final=exc.status_code in REFUSED) from None


def give_back(store: RepositoryStore, row: dict, transport: httpx.BaseTransport | None) -> None:
    """Assign the ticket to the stored user: Data Center assigns by login name, looked up by the user's key."""
    config, token = jira_access(store, row)
    try:
        with JiraClient(config['base_url'], token, transport) as client:
            user = client.user_by_key(row['give_back_key'])
            client.assign(row['jira_issue_id'], user.get('name'))
    except HTTPException as exc:
        raise Failed(str(exc.detail), final=exc.status_code in REFUSED) from None


def give_back_once(conn, store: RepositoryStore, row: dict, transport: httpx.BaseTransport | None) -> None:
    attempt = row['give_back_attempts'] + 1
    try:
        give_back(store, row, transport)
    except Failed as exc:
        final = exc.final or attempt > len(RETRIES)
        conn.execute(
            """UPDATE ka.notifications SET give_back_status=%s,give_back_attempts=%s,give_back_error=%s,
            give_back_next_attempt_at=now()+%s WHERE id=%s""",
            (
                'failed' if final else 'pending',
                attempt,
                exc.message[:2000],
                None if final else RETRIES[attempt - 1],
                row['id'],
            ),
        )
        return
    except Exception:
        if conn.broken or conn.closed:
            raise
        logger.exception('Giving back the Jira ticket of notification %s failed', row['id'])
        conn.execute(
            """UPDATE ka.notifications SET give_back_status='failed',give_back_attempts=%s,give_back_error=%s,
            give_back_next_attempt_at=NULL WHERE id=%s""",
            (attempt, UNEXPECTED, row['id']),
        )
        return
    conn.execute(
        """UPDATE ka.notifications SET give_back_status='done',give_back_attempts=%s,give_back_error=NULL,
        give_back_next_attempt_at=NULL WHERE id=%s""",
        (attempt, row['id']),
    )


def run_once(store: RepositoryStore, transport: httpx.BaseTransport | None = None) -> bool:
    """Post one due reply, else give back one due ticket; False when nothing is due."""
    with store.connect() as conn:
        row = conn.execute(CLAIM).fetchone()
        if row is None:
            if (due := conn.execute(CLAIM_GIVE_BACK).fetchone()) is None:
                return False
            give_back_once(conn, store, due, transport)
            return True
        attempt = row['jira_attempts'] + 1
        try:
            comment = post(store, row, transport)
        except Failed as exc:
            if exc.final or attempt > len(RETRIES):
                fail(conn, row['id'], attempt, exc.message)
            else:
                conn.execute(
                    """UPDATE ka.notifications SET jira_attempts=%s,jira_error=%s,jira_next_attempt_at=now()+%s
                    WHERE id=%s""",
                    (attempt, exc.message[:2000], RETRIES[attempt - 1], row['id']),
                )
            return True
        except Exception:
            if conn.broken or conn.closed:
                raise
            logger.exception('Posting notification %s to Jira failed', row['id'])
            fail(conn, row['id'], attempt, UNEXPECTED)
            return True
        conn.execute(
            """UPDATE ka.notifications SET jira_status='posted',jira_attempts=%s,jira_comment_id=%s,
            jira_comment_created=%s,jira_next_attempt_at=NULL,jira_error=NULL,
            give_back_next_attempt_at=CASE WHEN give_back_status='pending' THEN now() END WHERE id=%s""",
            (attempt, comment['id'], comment['created'], row['id']),
        )
    return True


def fail(conn, notification_id: int, attempt: int, message: str) -> None:
    """The comment failed for good; a hand-back waiting for it fails with it (the ticket stays with the agent)."""
    conn.execute(
        """UPDATE ka.notifications SET jira_status='failed',jira_attempts=%s,jira_error=%s,jira_next_attempt_at=NULL,
        give_back_status=CASE WHEN give_back_status='pending' THEN 'failed' ELSE give_back_status END,
        give_back_error=CASE WHEN give_back_status='pending' THEN %s ELSE give_back_error END WHERE id=%s""",
        (attempt, message[:2000], NOT_POSTED, notification_id),
    )


def loop(store: RepositoryStore, stopped: threading.Event) -> None:
    while not stopped.is_set():
        try:
            while not stopped.is_set() and run_once(store):
                pass
        except psycopg.Error:
            print('Jira replies database unavailable; retrying', flush=True)
        except Exception:
            logger.exception('Jira reply loop failed; retrying')
        stopped.wait(POLL_SECONDS)
