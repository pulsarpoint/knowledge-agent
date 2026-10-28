"""What the session workers share: claiming sessions with a lease,
delivering queued messages as turns, Stop, finishing a turn (draft, tokens and, for native sessions, history in one
transaction), the idle release and the serve loop. A worker only adds how it runs a turn."""

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from knowledge_admin.sessions.activity import redact
from knowledge_admin.documents.drafts import refresh_run_sources_safely
from knowledge_admin.sessions.text import close_idle, notify_event_run
from knowledge_admin.knowledge_agents.config import AD_HOC_AGENT, AD_HOC_NAME
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

PREVIEW_LINES = 20
MAX_PREVIEW_CHARS = 2000
MAX_TOOL_OUTPUT = 64 * 1024
TOOL_TRUNCATED = '… [truncated]'
LEASE_SECONDS = 60
IDLE_RELEASE_SECONDS = 300.0
STOPPED = 'Stopped. Send a message to continue.'
INTERRUPTED = 'The worker restarted during this turn. Send a message to continue.'
INTERRUPTED_REASON = 'the worker restarted during the turn'  # the Jira failure comment's reason
END_STATUS = {'waiting': 'completed', 'stopped': 'stopped', 'failed': 'failed'}
RETRYING_END = 'retrying'  # the end event of a failed attempt that is retried as the same turn
CLOSED = object()
LOST = object()  # another worker holds the session now: stop without further writes
NO_USAGE = {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0}


def run_key(session_id, turn: int) -> str:
    """The run record of one session turn, for both agent types: the session page, its history, Activity → Agent runs
    and deleting an agent with its history read these keys."""
    return f'cc:{session_id}:{turn}'


def clean_text(text: str, secrets: Iterable[str]) -> str:
    """Text to store: without U+0000 (Postgres rejects it in text and jsonb), removed first so it cannot split a key,
    then redacted."""
    return redact(text.replace('\x00', ''), [secret for secret in secrets if secret])


def capped(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + TOOL_TRUNCATED


def scrub(value: Any, secrets: list[str]) -> Any:
    """clean_text for every string (and string key) of a JSON-like value."""
    if isinstance(value, str):
        return clean_text(value, secrets)
    if isinstance(value, dict):
        return {
            scrub(key, secrets) if isinstance(key, str) else key: scrub(item, secrets) for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub(item, secrets) for item in value]
    return value


DUE = 'i.not_before IS NULL OR i.not_before<=now()'  # a queued input that may be delivered now (a wake-up once due)
WAITING = "s.status='retrying' AND coalesce(s.retry_at>now(), false)"  # a run waiting for its retry: nothing is delivered before retry_at
WAKEUP = 'Scheduled wake-up (set {set_at}): {message}'


def turn_text(item: dict) -> str:
    """An input as the turn reads it: a person's message as sent; a wake-up says when the agent set it."""
    if item['kind'] != 'wakeup':
        return item['text']
    return WAKEUP.format(set_at=item['created_at'].astimezone(UTC).isoformat(timespec='seconds'), message=item['text'])


def claim(store: RepositoryStore, worker_id: str, active: list[UUID], free: int) -> list[dict]:
    """Close idle sessions, renew this worker's leases, then claim sessions that need a worker: a queued message or
    a due wake-up (a future one does not count), or a turn left `working` by a worker that stopped (that turn is
    recorded as failed)."""
    with store.connect() as conn:
        close_idle(conn)
        conn.execute(
            """UPDATE ka.agent_sessions SET lease_expires_at=now()+%s*interval '1 second'
            WHERE worker_id=%s AND id=ANY(%s::uuid[]) AND status<>'closed'""",
            (LEASE_SECONDS, worker_id, active),
        )
        if free <= 0:
            return []
        rows = conn.execute(
            f"""WITH picked AS (
                SELECT s.id,s.status AS previous FROM ka.agent_sessions s
                WHERE s.status<>'closed' AND (s.lease_expires_at IS NULL OR s.lease_expires_at<now())
                  AND NOT s.id=ANY(%s::uuid[])
                  AND (s.status='working' OR (NOT ({WAITING}) AND EXISTS (SELECT 1 FROM ka.agent_session_inputs i
                       WHERE i.session_id=s.id AND i.status='queued' AND ({DUE}))))
                ORDER BY s.created_at,s.id LIMIT %s FOR UPDATE OF s SKIP LOCKED)
            UPDATE ka.agent_sessions s SET worker_id=%s,lease_expires_at=now()+%s*interval '1 second',
                status=CASE WHEN s.status='working' THEN 'failed' ELSE s.status END,
                status_message=CASE WHEN s.status='working' THEN %s ELSE s.status_message END,
                stop_requested_at=CASE WHEN s.status='working' THEN NULL ELSE s.stop_requested_at END
            FROM picked WHERE s.id=picked.id
            RETURNING s.*,picked.previous,coalesce((SELECT a.name FROM ka.agents a WHERE a.id=s.agent_id), %s) AS agent_name,
                EXISTS (SELECT 1 FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued'
                  AND ({DUE})) AS queued""",
            (active, free, worker_id, LEASE_SECONDS, INTERRUPTED, AD_HOC_NAME),
        ).fetchall()
        for row in rows:
            if row['previous'] == 'working' and not row['queued']:  # failed now; it needs a worker only for a message
                conn.execute(
                    'UPDATE ka.agent_sessions SET worker_id=NULL,lease_expires_at=NULL WHERE id=%s AND worker_id=%s',
                    (row['id'], worker_id),
                )
            if row['previous'] == 'working' and row['turns'] > 0:  # comments delivered inside it stay delivered
                notify_event_run(conn, row['id'], row['turns'], 'failed', INTERRUPTED, reason=INTERRUPTED_REASON)
                append_run_events(conn, row['id'], [('error', {'message': INTERRUPTED}), ('end', {'status': 'failed'})])
    return rows


def append_run_events(conn, session_id, items: list[tuple[str, dict]]) -> None:
    """Events added to the record of the session's latest turn after its last one, outside a running turn: a turn the
    worker lost, or a retry that Stop or Close ended (its record ends with a final `end`)."""
    for kind, payload in items:
        conn.execute(
            """INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
            SELECT s.project_id,k.run_key,
                coalesce((SELECT max(e.seq) FROM ka.agent_run_events e WHERE e.run_key=k.run_key),0)+1,
                %s,coalesce(s.agent_id,%s),s.actor_id,%s
            FROM ka.agent_sessions s CROSS JOIN LATERAL (SELECT 'cc:'||s.id||':'||s.turns AS run_key) k
            WHERE s.id=%s AND s.turns>0""",
            (kind, AD_HOC_AGENT, Jsonb(payload), session_id),
        )


class SessionRunner:
    """One session in one asyncio task until the session closes, this worker loses it, or it has been idle for
    `idle_release` seconds. Subclasses implement run_turn (and disconnect, for what they keep between turns)."""

    def __init__(
        self,
        store: RepositoryStore,
        session: dict,
        *,
        poll: float = 1.0,
        idle_release: float = IDLE_RELEASE_SECONDS,
        slot: int = 1,
    ):
        self.store, self.session = store, session
        self.id, self.worker_id, self.slot = session['id'], session['worker_id'], slot
        self.poll, self.idle_release = poll, idle_release
        self.secrets: list[str] = []
        self.retrying = False  # the turn being run is a retry of the latest turn (the same number)
        self.retries = 0  # automatic retries of that turn before this attempt

    async def run(self) -> None:
        idle_since = time.monotonic()
        try:
            while True:
                turn = await asyncio.to_thread(self.next_turn)
                if turn is CLOSED or turn is LOST:
                    return
                if turn is None:
                    if time.monotonic() - idle_since >= self.idle_release:
                        return
                    await asyncio.sleep(self.poll)
                    continue
                await self.run_turn(*turn)
                idle_since = time.monotonic()
        finally:
            await self.disconnect()
            self.secrets.clear()
            with suppress(psycopg.Error):
                await asyncio.to_thread(self.release)

    def next_turn(self):
        """Deliver every queued message and due wake-up as the next turn, once; CLOSED when the session is closed, LOST
        when this worker no longer holds its lease. A session that is `retrying` runs its latest turn again: the
        same number (its run events continue that turn's record), with the automatic retries counted so far; any other
        turn starts with none."""
        with self.store.connect() as conn:
            row = conn.execute(
                f"""SELECT status,turns,retries,(worker_id=%s AND lease_expires_at>now()) AS owned,
                ({WAITING}) AS waiting FROM ka.agent_sessions s WHERE id=%s FOR UPDATE""",
                (self.worker_id, self.id),
            ).fetchone()
            if row is None or row['status'] == 'closed':
                return CLOSED
            if not row['owned']:
                return LOST
            if row['waiting']:
                return None
            retrying = row['status'] == 'retrying' and row['turns'] > 0
            number = row['turns'] if retrying else row['turns'] + 1
            inputs = self._deliver(conn, number, wakeups=True)
            if not inputs:
                return None
            self.retrying, self.retries = retrying, row['retries'] if retrying else 0
            conn.execute(
                """UPDATE ka.agent_sessions SET status='working',status_message='',turns=%s,retries=%s,retry_at=NULL,
                stop_requested_at=NULL,last_activity_at=now() WHERE id=%s""",
                (number, self.retries, self.id),
            )
            return number, '\n\n'.join(turn_text(item) for item in inputs)

    def queued_comments(self, number: int, on_delivered: Callable[[Any, list[dict]], None] | None = None) -> list[str]:
        """Messages queued while turn `number` runs, marked delivered to that turn once; none when the session is closed,
        no longer this worker's (lease), not running that turn, or the user pressed Stop (a message sent after Stop stays
        queued and becomes the next turn). For delivery inside a running turn.
        `on_delivered(conn, inputs)` (their id and text) runs in the same transaction, such as recording them, so a
        message is never delivered without it. Wake-ups are never comments: a due one waits for the next turn."""
        with self.store.connect() as conn:
            row = conn.execute(
                """SELECT status,turns,stop_requested_at,(worker_id=%s AND lease_expires_at>now()) AS owned
                FROM ka.agent_sessions WHERE id=%s FOR UPDATE""",
                (self.worker_id, self.id),
            ).fetchone()
            if (
                row is None
                or row['status'] != 'working'
                or row['turns'] != number
                or not row['owned']
                or row['stop_requested_at'] is not None
            ):
                return []
            inputs = self._deliver(conn, number, wakeups=False)
            if inputs:
                conn.execute('UPDATE ka.agent_sessions SET last_activity_at=now() WHERE id=%s', (self.id,))
                if on_delivered is not None:
                    on_delivered(conn, inputs)
            return [item['text'] for item in inputs]

    def return_comments(self, number: int, ids: list[int]) -> None:
        """Comments delivered inside turn `number`, which did not complete: discarded when the user pressed Stop (as Stop
        discards queued messages) or the session is closed, otherwise queued again, so the next turn receives them.
        No lease check, on purpose: the comments belong to this turn, whichever worker holds the session now."""
        if not ids:
            return
        with self.store.connect() as conn:
            conn.execute(
                """UPDATE ka.agent_session_inputs i SET turn=NULL,delivered_at=NULL,
                status=CASE WHEN s.stop_requested_at IS NOT NULL OR s.status='closed' THEN 'discarded' ELSE 'queued' END
                FROM ka.agent_sessions s WHERE s.id=i.session_id AND i.session_id=%s AND i.id=ANY(%s)
                  AND i.status='delivered' AND i.turn=%s""",
                (self.id, ids, number),
            )

    def _deliver(self, conn, number: int, *, wakeups: bool) -> list[dict]:
        """Mark every queued message of this session (and, with `wakeups`, every due wake-up) delivered to turn
        `number`; the caller holds the session row lock and has checked the lease."""
        inputs = conn.execute(
            f"""SELECT id,text,kind,created_at FROM ka.agent_session_inputs i WHERE session_id=%s AND status='queued'
            AND ({DUE}) AND (%s OR kind='message') ORDER BY seq""",
            (self.id, wakeups),
        ).fetchall()
        if inputs:
            conn.execute(
                """UPDATE ka.agent_session_inputs SET status='delivered',turn=%s,delivered_at=now()
                WHERE id=ANY(%s)""",
                (number, [item['id'] for item in inputs]),
            )
        return inputs

    async def run_turn(self, number: int, text: str) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        return None

    def stop_pressed(self) -> bool:
        """Whether a person pressed Stop during the turn (a failed turn then ends stopped: no retry, no failure comment)."""
        with self.store.connect() as conn:
            row = conn.execute('SELECT stop_requested_at FROM ka.agent_sessions WHERE id=%s', (self.id,)).fetchone()
        return row is not None and row['stop_requested_at'] is not None

    def stop_requested(self) -> bool:
        with self.store.connect() as conn:
            row = conn.execute(
                """SELECT status,stop_requested_at,(worker_id=%s AND lease_expires_at>now()) AS owned
                FROM ka.agent_sessions WHERE id=%s""",
                (self.worker_id, self.id),
            ).fetchone()
        return row is None or row['status'] == 'closed' or row['stop_requested_at'] is not None or not row['owned']

    def finish_turn(
        self,
        number: int,
        status: str,
        message: str,
        *,
        usage: dict[str, Any] | None,
        commit: Callable[[Any], None] | None = None,
        answer: str = '',
        reason: str = '',
        retry_at: datetime | None = None,
        retry_status: str = '',
    ) -> None:
        """Record the turn's outcome; an event run's first turn also writes its notification (`answer` when it
        finished, else `message`; `reason`: why it failed, in a few words, for a Jira failure comment). A failed turn
        with `retry_at` is retried instead, unless the session was closed or Stop was pressed meanwhile: the session
        waits as `retrying` until `retry_at` with `retry_status` as its message and one more retry counted, the turn's
        inputs (and the messages queued meanwhile) are queued again for `retry_at`, and nothing is notified yet. A
        failed turn during which Stop was pressed ends `stopped` (no retry and no Jira failure comment).
        `commit(conn)` runs in the same transaction, only for a successful turn this worker still owns: a lease lost after the turn completed leaves the comments
        delivered inside it delivered and its history uncommitted. A session closed during the turn still gets the commit, which is harmless: a closed session takes no
        more messages, so no turn reads that history again. Drafts the document tools saved get their
        sources refreshed."""
        usage = usage or NO_USAGE
        retry = status == 'failed' and retry_at is not None
        with self.store.connect() as conn:
            updated = conn.execute(
                """UPDATE ka.agent_sessions SET
                status=CASE WHEN status='closed' THEN status
                    WHEN %(status)s='failed' AND stop_requested_at IS NOT NULL THEN 'stopped'
                    WHEN %(retry)s THEN 'retrying' ELSE %(status)s END,
                status_message=CASE WHEN status='closed' THEN status_message
                    WHEN %(status)s='failed' AND stop_requested_at IS NOT NULL THEN %(stopped)s
                    WHEN %(retry)s THEN %(retry_status)s ELSE %(message)s END,
                retries=CASE WHEN status<>'closed' AND %(retry)s AND stop_requested_at IS NULL THEN retries+1
                    WHEN %(status)s='failed' AND stop_requested_at IS NOT NULL THEN 0 ELSE retries END,
                retry_at=CASE WHEN status<>'closed' AND %(retry)s AND stop_requested_at IS NULL THEN %(retry_at)s::timestamptz END,
                input_tokens=input_tokens+%(input)s, output_tokens=output_tokens+%(output)s,
                cached_tokens=cached_tokens+%(cached)s,
                stop_requested_at=NULL, last_activity_at=now() WHERE id=%(id)s AND worker_id=%(worker)s
                RETURNING status""",
                {
                    'status': status,
                    'message': message,
                    'retry': retry,
                    'retry_status': retry_status,
                    'retry_at': retry_at,
                    'stopped': STOPPED,
                    'input': usage['input_tokens'],
                    'output': usage['output_tokens'],
                    'cached': usage['cached_tokens'],
                    'id': self.id,
                    'worker': self.worker_id,
                },
            ).fetchone()
            if updated is not None and status == 'waiting' and commit is not None:
                commit(conn)
            if updated is not None and updated['status'] == 'retrying':
                self._queue_again(conn, number, retry_at)
            elif updated is not None and updated['status'] == 'stopped' and status == 'failed':
                notify_event_run(conn, self.id, number, 'stopped', STOPPED)
            elif updated is not None:
                notify_event_run(
                    conn, self.id, number, status, answer if status == 'waiting' else message, reason=reason
                )
            if updated is not None:
                refresh_run_sources_safely(conn, self.id, number)  # drafts the tools saved; reads may come later

    def _queue_again(self, conn, number: int, at: datetime) -> None:
        """A turn to retry: its inputs (comments delivered inside it too) and the messages queued since, queued for
        `at`; delivered together as the same turn then (next_turn), or at once when a person sends a message."""
        conn.execute(
            """UPDATE ka.agent_session_inputs SET status='queued',turn=NULL,delivered_at=NULL,not_before=%s
            WHERE session_id=%s AND ((status='delivered' AND turn=%s) OR (status='queued' AND kind='message'))""",
            (at, self.id, number),
        )

    def release(self) -> None:
        with self.store.connect() as conn:
            conn.execute(
                'UPDATE ka.agent_sessions SET worker_id=NULL,lease_expires_at=NULL WHERE id=%s AND worker_id=%s',
                (self.id, self.worker_id),
            )


async def serve(
    store: RepositoryStore,
    start: Callable[[dict, int], SessionRunner],
    *,
    label: str,
    stopped: asyncio.Event,
    worker_id: str,
    limit: int,
    poll: float,
) -> None:
    """Claim sessions and run one task per session until `stopped` is set; leases are renewed every poll.
    Each running session holds one of `limit` slots until its task ends; `start(session, slot)` makes its runner."""
    active: dict[UUID, asyncio.Task] = {}
    slots: dict[UUID, int] = {}
    free_slots = list(range(1, limit + 1))
    try:
        while not stopped.is_set():
            for session_id, task in list(active.items()):
                if task.done():
                    del active[session_id]
                    free_slots.append(slots.pop(session_id))
                    if not task.cancelled() and task.exception() is not None:
                        logger.warning(
                            '%s session %s ended with %s', label, session_id, type(task.exception()).__name__
                        )
            try:
                claimed = await asyncio.to_thread(claim, store, worker_id, list(active), len(free_slots))
            except psycopg.Error as exc:
                logger.warning('%s worker database unavailable (%s); retrying', label, type(exc).__name__)
                claimed = []
            for session in claimed:
                if session['previous'] == 'working' and not session['queued']:
                    continue  # its interrupted turn is recorded; claim released it until a message arrives
                slots[session['id']] = free_slots.pop(0)
                active[session['id']] = asyncio.create_task(start(session, slots[session['id']]).run())
            with suppress(TimeoutError):
                await asyncio.wait_for(stopped.wait(), poll)
    finally:
        for task in active.values():
            task.cancel()
        await asyncio.gather(*active.values(), return_exceptions=True)
