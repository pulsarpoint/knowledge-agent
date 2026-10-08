"""Live session pages without polling. Postgres announces session changes on the channel `ka_sessions` (migration
0083's triggers, delivered on commit); one listener per API process receives them, and the hub reads the new rows once
and hands them to every subscriber of that session (the event stream of the Test tab and Generate documents)."""
import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import psycopg
from fastapi.encoders import jsonable_encoder
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

CHANNEL = 'ka_sessions'
QUEUE_SIZE = 500  # messages a subscriber may fall behind before it must resync (its queue has one more place, for resync)
PAGE = 500  # events per query
COALESCE_SECONDS = 0.05  # notices arriving together become one read
SWEEP_SECONDS = 30.0  # every watched session is read at least this often (a lost notice)
BACKOFF_START, BACKOFF_MAX = 1.0, 30.0
CONNECT_TIMEOUT = 5

STATE = """SELECT s.status,s.status_message,s.turns,s.input_tokens,s.output_tokens,s.cached_tokens,s.cost_usd,s.model_calls,s.reasoning_tokens,s.draft,
    s.draft_turn,s.document_id,coalesce(s.lease_expires_at > now(), false) AS worker_active,
    (SELECT count(*) FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued
    FROM ka.agent_sessions_cc s WHERE s.id=%s"""
EVENTS = """SELECT id,run_key,seq,kind,
    CASE WHEN kind='tool_result' THEN payload - 'output' ELSE payload END AS payload,created_at FROM ka.agent_run_events
    WHERE run_key=ANY(%s) AND id>%s ORDER BY id LIMIT %s"""

Message = tuple[str, Any]


class Subscription:
    """One page's view of a session: its queue of ('event', row), ('session', state), ('closed', None),
    ('unavailable', None) and ('resync', None) messages, the newest event id it was given and the last state sent."""

    def __init__(self, position: int):
        self.position = position
        self.queue: asyncio.Queue[Message] = asyncio.Queue(QUEUE_SIZE + 1)  # the extra place is for resync
        self.state: Any = None
        self.told_unavailable = False
        self.dropped = False

    async def get(self) -> Message:
        return await self.queue.get()


class Watched:
    """A session somebody watches: its subscribers and the newest event id read for it."""

    def __init__(self, read_to: int):
        self.subscribers: list[Subscription] = []
        self.read_to = read_to


class SessionHub:
    """One per API process (started in the app lifespan). `subscribe` gives a page its messages; a notice marks the
    session dirty and one reader task reads each dirty session once."""

    def __init__(self, conninfo: str, *, sweep_seconds: float = SWEEP_SECONDS, backoff_start: float = BACKOFF_START,
                 backoff_max: float = BACKOFF_MAX, coalesce_seconds: float = COALESCE_SECONDS):
        self.conninfo = conninfo
        self.sweep_seconds, self.coalesce_seconds = sweep_seconds, coalesce_seconds
        self.backoff_start, self.backoff_max = backoff_start, backoff_max
        self._watched: dict[UUID, Watched] = {}
        self._dirty: set[UUID] = set()
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.listening = asyncio.Event()  # set while LISTEN is active
        self.listener_pid: int | None = None
        self.reads = 0  # per-session reads so far (tests and diagnostics)

    async def start(self) -> None:
        """Start the listener and the reader; returns at once (the database may still be unreachable)."""
        self._tasks = [asyncio.create_task(self._listen(), name='session-hub-listener'),
                       asyncio.create_task(self._read_loop(), name='session-hub-reader')]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    @asynccontextmanager
    async def subscribe(self, session_id: UUID, after_id: int) -> AsyncIterator[Subscription]:
        """Watch a session from event id `after_id`: the hub's first read gives the events after it and the state."""
        subscription = Subscription(after_id)
        watched = self._watched.get(session_id)
        if watched is None:
            watched = self._watched[session_id] = Watched(after_id)
        watched.subscribers.append(subscription)
        self._mark([session_id])
        try:
            yield subscription
        finally:
            self._leave(session_id, subscription)

    def subscribers(self, session_id: UUID) -> int:
        watched = self._watched.get(session_id)
        return len(watched.subscribers) if watched else 0

    def _leave(self, session_id: UUID, subscription: Subscription) -> None:
        """Remove a subscriber; a session nobody watches any more is forgotten."""
        watched = self._watched.get(session_id)
        if watched is not None and subscription in watched.subscribers:
            watched.subscribers.remove(subscription)
            if not watched.subscribers:
                del self._watched[session_id]
                self._dirty.discard(session_id)

    def _mark(self, session_ids: Iterable[UUID]) -> None:
        """Mark watched sessions for the reader (unwatched ones are ignored)."""
        self._dirty.update(session_id for session_id in session_ids if session_id in self._watched)
        if self._dirty:
            self._wake.set()

    def _notice(self, payload: str) -> None:
        """Mark the session a notice names; a malformed notice is ignored (and never ends the listener)."""
        session_id = _session_of(payload)
        if session_id is None:  # never log the payload: it is not ours
            logger.warning('Ignoring a malformed %s notice', CHANNEL)
            return
        self._mark([session_id])

    def _put(self, session_id: UUID, subscription: Subscription, message: Message) -> bool:
        """Queue a message. A subscriber whose queue is full gets resync instead and is dropped: its stream ends and
        the browser reconnects from its last event id."""
        if subscription.dropped:
            return False
        if subscription.queue.qsize() >= QUEUE_SIZE:
            subscription.dropped = True
            subscription.queue.put_nowait(('resync', None))
            self._leave(session_id, subscription)
            return False
        subscription.queue.put_nowait(message)
        return True

    def _outage(self) -> None:
        """The database cannot be reached: every subscriber hears it once; everything watched is read on recovery."""
        self._dirty.update(self._watched)
        for session_id, watched in list(self._watched.items()):
            for subscription in list(watched.subscribers):
                if not subscription.told_unavailable:
                    subscription.told_unavailable = True
                    self._put(session_id, subscription, ('unavailable', None))

    def _recovered(self) -> None:
        """Connected again: a later outage is told again, and every watched session is read."""
        for watched in self._watched.values():
            for subscription in watched.subscribers:
                subscription.told_unavailable = False
        self._mark(list(self._watched))

    async def _listener_connection(self) -> psycopg.AsyncConnection:
        """Open the listener's connection (tests wrap it)."""
        return await psycopg.AsyncConnection.connect(self.conninfo, autocommit=True, connect_timeout=CONNECT_TIMEOUT)

    async def _listen(self) -> None:
        """LISTEN on one connection. A lost connection is reopened after `backoff_start`; a connect that fails is an
        outage, retried with backoff doubling to `backoff_max`. Once listening again every watched session is read.
        Every `sweep_seconds` the connection is checked and every watched session read anyway (a lost notice)."""
        delay = self.backoff_start
        while True:
            try:
                async with await self._listener_connection() as conn:
                    await conn.execute(f'LISTEN {CHANNEL}')
                    self.listener_pid, delay = conn.info.backend_pid, self.backoff_start
                    self.listening.set()
                    self._recovered()
                    while True:
                        async for notice in conn.notifies(timeout=self.sweep_seconds):
                            self._notice(notice.payload)
                        await asyncio.wait_for(conn.execute('SELECT 1'), CONNECT_TIMEOUT)  # a dead connection raises
                        self._mark(list(self._watched))
            except Exception as exc:  # noqa: BLE001 - the listener must outlive any error; only the class name is logged
                lost = self.listening.is_set()
                self.listening.clear()
                self.listener_pid = None
                if lost:
                    logger.warning('Session hub listener lost its connection (%s); reconnecting', type(exc).__name__)
                else:
                    logger.warning('Session hub cannot reach the database (%s); retrying in %.1f s',
                                   type(exc).__name__, delay)
                    self._outage()
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.backoff_max)

    async def _read_loop(self) -> None:
        """Drain dirty sessions; a burst of notices (within coalesce_seconds) becomes one read per session. A failed
        read is an outage: every watched session (the rest of the batch with them) stays dirty and is retried with
        backoff, at once when there is news (a notice, or the listener connected again)."""
        delay, failing = self.backoff_start, False
        while True:
            await self._wake.wait()
            await asyncio.sleep(self.coalesce_seconds)
            self._wake.clear()
            batch, self._dirty = self._dirty, set()
            try:
                await self._read_all(batch)
            except Exception as exc:  # noqa: BLE001 - the hub must outlive any failed read
                # only the class name, never the traceback: a psycopg message can echo connection details
                logger.warning('Session hub cannot read sessions (%s); retrying in %.1f s', type(exc).__name__, delay)
                failing = True
                self._outage()
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), delay)
                except TimeoutError:
                    pass
                delay = min(delay * 2, self.backoff_max)
                self._wake.set()
            else:
                if failing:
                    failing, delay = False, self.backoff_start
                    self._recovered()

    async def _read_all(self, batch: set[UUID]) -> None:
        """Read the dirty sessions over one short-lived connection."""
        if not batch:
            return
        async with await psycopg.AsyncConnection.connect(self.conninfo, autocommit=True, row_factory=dict_row,
                                                         connect_timeout=CONNECT_TIMEOUT) as conn:
            for session_id in batch:
                await self._read(conn, session_id)

    async def _read(self, conn, session_id: UUID) -> None:
        """Read one watched session once: the state, then the events after the furthest-behind subscriber (PAGE at a
        time) over its turn keys (cc:<session>:<turn>). Each subscriber present when the read starts gets only events
        after its own position, then the state if it changed; a page that subscribes during the read was marked dirty
        and gets its own read. A missing row or a closed session ends every present subscription."""
        watched = self._watched.get(session_id)
        if watched is None:
            return
        self.reads += 1
        present = list(watched.subscribers)
        state = await (await conn.execute(STATE, (session_id,))).fetchone()
        if state is None:  # the session was removed
            self._close(session_id, present)
            return
        keys = [f'cc:{session_id}:{turn}' for turn in range(1, state['turns'] + 1)]
        after = min([subscription.position for subscription in present] + [watched.read_to])
        while keys:
            rows = await (await conn.execute(EVENTS, (keys, after, PAGE))).fetchall()
            for row in rows:
                for subscription in present:
                    if row['id'] > subscription.position and self._put(session_id, subscription, ('event', row)):
                        subscription.position = row['id']
            if rows:
                after = rows[-1]['id']
                watched.read_to = max(watched.read_to, after)
            present = [subscription for subscription in present if not subscription.dropped]
            if not present:  # every subscriber fell behind and was dropped
                return
            if len(rows) < PAGE:
                break
        public = jsonable_encoder(state)
        for subscription in present:
            if public != subscription.state and self._put(session_id, subscription, ('session', public)):
                subscription.state = public
        if state['status'] == 'closed':
            self._close(session_id, present)

    def _close(self, session_id: UUID, subscribers: list[Subscription]) -> None:
        for subscription in subscribers:
            self._put(session_id, subscription, ('closed', None))
            self._leave(session_id, subscription)


def _session_of(payload: str) -> UUID | None:
    """The session id of a notice payload {"s": "<uuid>", ...}, or None when the payload is not one."""
    try:
        notice = json.loads(payload)
    except ValueError:
        return None
    session = notice.get('s') if isinstance(notice, dict) else None
    if not isinstance(session, str):
        return None
    try:
        return UUID(session)
    except ValueError:
        return None
