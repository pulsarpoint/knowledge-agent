"""What the Claude Code and native session workers share: claiming the sessions of one agent type with a lease,
delivering queued messages as turns, Stop, finishing a turn (draft, tokens and, for native sessions, history in one
transaction), the idle release and the serve loop. A worker only adds how it runs a turn."""
import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from knowledge_admin.agent_activity import redact
from knowledge_admin.claude_code import close_idle
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
END_STATUS = {'waiting': 'completed', 'stopped': 'stopped', 'failed': 'failed'}
CLOSED = object()
LOST = object()  # another worker holds the session now: stop without further writes
NO_USAGE = {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0, 'cost_usd': 0.0}


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
        return {scrub(key, secrets) if isinstance(key, str) else key: scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(item, secrets) for item in value]
    return value


def claim(store: RepositoryStore, worker_id: str, active: list[UUID], free: int, runtime: str) -> list[dict]:
    """Close idle sessions, renew this worker's leases, then claim sessions of this agent type (`runtime`) that need a
    worker: a queued message, or a turn left `working` by a worker that stopped (that turn is recorded as failed)."""
    with store.connect() as conn:
        close_idle(conn)
        conn.execute("""UPDATE ka.agent_sessions_cc SET lease_expires_at=now()+%s*interval '1 second'
            WHERE worker_id=%s AND id=ANY(%s::uuid[]) AND status<>'closed'""", (LEASE_SECONDS, worker_id, active))
        if free <= 0:
            return []
        rows = conn.execute("""WITH picked AS (
                SELECT s.id,s.status AS previous FROM ka.agent_sessions_cc s
                WHERE s.runtime=%s AND s.status<>'closed' AND (s.lease_expires_at IS NULL OR s.lease_expires_at<now())
                  AND NOT s.id=ANY(%s::uuid[])
                  AND (s.status='working' OR EXISTS (SELECT 1 FROM ka.agent_session_inputs i
                       WHERE i.session_id=s.id AND i.status='queued'))
                ORDER BY s.created_at,s.id LIMIT %s FOR UPDATE OF s SKIP LOCKED)
            UPDATE ka.agent_sessions_cc s SET worker_id=%s,lease_expires_at=now()+%s*interval '1 second',
                status=CASE WHEN s.status='working' THEN 'failed' ELSE s.status END,
                status_message=CASE WHEN s.status='working' THEN %s ELSE s.status_message END,
                stop_requested_at=CASE WHEN s.status='working' THEN NULL ELSE s.stop_requested_at END
            FROM picked WHERE s.id=picked.id
            RETURNING s.*,picked.previous,coalesce((SELECT a.name FROM ka.agents a WHERE a.id=s.agent_id), %s) AS agent_name,
                EXISTS (SELECT 1 FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued""",
            (runtime, active, free, worker_id, LEASE_SECONDS, INTERRUPTED, AD_HOC_NAME)).fetchall()
        for row in rows:
            if row['previous'] == 'working' and not row['queued']:  # failed now; it needs a worker only for a message
                conn.execute('UPDATE ka.agent_sessions_cc SET worker_id=NULL,lease_expires_at=NULL WHERE id=%s AND worker_id=%s',
                             (row['id'], worker_id))
            if row['previous'] == 'working' and row['turns'] > 0:  # comments delivered inside it stay delivered
                key = run_key(row['id'], row['turns'])
                for kind, payload in (('error', {'message': INTERRUPTED}), ('end', {'status': 'failed'})):
                    conn.execute("""INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,actor_id,payload)
                        SELECT %s,%s,coalesce(max(seq),0)+1,%s,%s,%s,%s FROM ka.agent_run_events WHERE run_key=%s""",
                        (row['project_id'], key, kind, row['agent_id'] or AD_HOC_AGENT, row['actor_id'], Jsonb(payload), key))
    return rows


class SessionRunner:
    """One session in one asyncio task until the session closes, this worker loses it, or it has been idle for
    `idle_release` seconds. Subclasses implement run_turn (and disconnect, for what they keep between turns)."""

    def __init__(self, store: RepositoryStore, session: dict, *, poll: float = 1.0,
                 idle_release: float = IDLE_RELEASE_SECONDS, slot: int = 1):
        self.store, self.session = store, session
        self.id, self.worker_id, self.slot = session['id'], session['worker_id'], slot
        self.poll, self.idle_release = poll, idle_release
        self.secrets: list[str] = []

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
        """Deliver every queued message as the next turn, once; CLOSED when the session is closed, LOST when this worker
        no longer holds its lease."""
        with self.store.connect() as conn:
            row = conn.execute('''SELECT status,turns,(worker_id=%s AND lease_expires_at>now()) AS owned
                FROM ka.agent_sessions_cc WHERE id=%s FOR UPDATE''', (self.worker_id, self.id)).fetchone()
            if row is None or row['status'] == 'closed':
                return CLOSED
            if not row['owned']:
                return LOST
            number = row['turns'] + 1
            inputs = self._deliver(conn, number)
            if not inputs:
                return None
            conn.execute("""UPDATE ka.agent_sessions_cc SET status='working',status_message='',turns=%s,
                stop_requested_at=NULL,last_activity_at=now() WHERE id=%s""", (number, self.id))
            return number, '\n\n'.join(item['text'] for item in inputs)

    def queued_comments(self, number: int,
                        on_delivered: Callable[[Any, list[dict]], None] | None = None) -> list[str]:
        """Messages queued while turn `number` runs, marked delivered to that turn once; none when the session is closed,
        no longer this worker's (lease), not running that turn, or the user pressed Stop (a message sent after Stop stays
        queued and becomes the next turn). For delivery inside a running turn.
        `on_delivered(conn, inputs)` (their id and text) runs in the same transaction, such as recording them, so a
        message is never delivered without it."""
        with self.store.connect() as conn:
            row = conn.execute('''SELECT status,turns,stop_requested_at,(worker_id=%s AND lease_expires_at>now()) AS owned
                FROM ka.agent_sessions_cc WHERE id=%s FOR UPDATE''', (self.worker_id, self.id)).fetchone()
            if (row is None or row['status'] != 'working' or row['turns'] != number or not row['owned']
                    or row['stop_requested_at'] is not None):
                return []
            inputs = self._deliver(conn, number)
            if inputs:
                conn.execute('UPDATE ka.agent_sessions_cc SET last_activity_at=now() WHERE id=%s', (self.id,))
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
            conn.execute("""UPDATE ka.agent_session_inputs i SET turn=NULL,delivered_at=NULL,
                status=CASE WHEN s.stop_requested_at IS NOT NULL OR s.status='closed' THEN 'discarded' ELSE 'queued' END
                FROM ka.agent_sessions_cc s WHERE s.id=i.session_id AND i.session_id=%s AND i.id=ANY(%s)
                  AND i.status='delivered' AND i.turn=%s""", (self.id, ids, number))

    def _deliver(self, conn, number: int) -> list[dict]:
        """Mark every queued message of this session delivered to turn `number`; the caller holds the session row lock
        and has checked the lease."""
        inputs = conn.execute("""SELECT id,text FROM ka.agent_session_inputs WHERE session_id=%s AND status='queued'
            ORDER BY seq""", (self.id,)).fetchall()
        if inputs:
            conn.execute("""UPDATE ka.agent_session_inputs SET status='delivered',turn=%s,delivered_at=now()
                WHERE id=ANY(%s)""", (number, [item['id'] for item in inputs]))
        return inputs

    async def run_turn(self, number: int, text: str) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        return None

    def stop_requested(self) -> bool:
        with self.store.connect() as conn:
            row = conn.execute('''SELECT status,stop_requested_at,(worker_id=%s AND lease_expires_at>now()) AS owned
                FROM ka.agent_sessions_cc WHERE id=%s''', (self.worker_id, self.id)).fetchone()
        return row is None or row['status'] == 'closed' or row['stop_requested_at'] is not None or not row['owned']

    def finish_turn(self, number: int, status: str, message: str, *, draft: str, usage: dict[str, Any] | None,
                    claude_session_id: str | None = None, commit: Callable[[Any], None] | None = None) -> None:
        """Record the turn's outcome. The draft (already cleaned) replaces the previous one only after a successful turn
        with text; a Test session (purpose 'test') never gets a draft. `commit(conn)` runs in the same transaction,
        only for a successful turn this worker still owns: a lease lost after the turn completed leaves the comments
        delivered inside it delivered and its history uncommitted. A session closed during the turn still gets the commit, which is harmless: a closed session takes no
        more messages, so no turn reads that history again."""
        usage = usage or NO_USAGE
        draft = draft if status == 'waiting' and draft.strip() else None
        with self.store.connect() as conn:
            updated = conn.execute("""UPDATE ka.agent_sessions_cc SET
                status=CASE WHEN status='closed' THEN status ELSE %(status)s END,
                status_message=CASE WHEN status='closed' THEN status_message ELSE %(message)s END,
                draft=CASE WHEN purpose='test' THEN draft ELSE coalesce(%(draft)s::text, draft) END,
                draft_turn=CASE WHEN purpose='test' OR %(draft)s::text IS NULL THEN draft_turn ELSE %(turn)s END,
                claude_session_id=coalesce(%(cli)s::text, claude_session_id),
                input_tokens=input_tokens+%(input)s, output_tokens=output_tokens+%(output)s,
                cached_tokens=cached_tokens+%(cached)s, cost_usd=cost_usd+%(cost)s::numeric,
                stop_requested_at=NULL, last_activity_at=now() WHERE id=%(id)s AND worker_id=%(worker)s RETURNING id""",
                {'status': status, 'message': message, 'draft': draft, 'turn': number, 'cli': claude_session_id,
                 'input': usage['input_tokens'], 'output': usage['output_tokens'], 'cached': usage['cached_tokens'],
                 'cost': usage['cost_usd'], 'id': self.id, 'worker': self.worker_id}).fetchone()
            if updated is not None and status == 'waiting' and commit is not None:
                commit(conn)

    def release(self) -> None:
        with self.store.connect() as conn:
            conn.execute('UPDATE ka.agent_sessions_cc SET worker_id=NULL,lease_expires_at=NULL WHERE id=%s AND worker_id=%s',
                         (self.id, self.worker_id))


async def serve(store: RepositoryStore, start: Callable[[dict, int], SessionRunner], *, runtime: str, label: str,
                stopped: asyncio.Event, worker_id: str, limit: int, poll: float,
                reclaim: Callable[[Any, int], None] | None = None) -> None:
    """Claim sessions of `runtime` and run one task per session until `stopped` is set; leases are renewed every poll.
    Each running session holds one of `limit` slots until its task ends; `start(session, slot)` makes its runner.
    `reclaim(session_id, slot)` clears a slot before it is used again (Claude Code: the slot user's processes and the
    session HOME); a slot it cannot clear stays out of use."""
    active: dict[UUID, asyncio.Task] = {}
    slots: dict[UUID, int] = {}
    free_slots = list(range(1, limit + 1))
    try:
        while not stopped.is_set():
            for session_id, task in list(active.items()):
                if task.done():
                    del active[session_id]
                    slot = slots.pop(session_id)
                    try:  # nothing of this session stays readable by the next session in this slot
                        if reclaim is not None:
                            await asyncio.to_thread(reclaim, session_id, slot)
                    except Exception as exc:
                        logger.warning('%s slot %s is kept out of use: it could not be cleared (%s)',
                                       label, slot, type(exc).__name__)
                    else:
                        free_slots.append(slot)
                    if not task.cancelled() and task.exception() is not None:
                        logger.warning('%s session %s ended with %s', label, session_id, type(task.exception()).__name__)
            try:
                claimed = await asyncio.to_thread(claim, store, worker_id, list(active), len(free_slots), runtime)
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
