"""The wake-up tools: schedule_wakeup, list_wakeups, cancel_wakeup. A wake-up is a message the agent queues to its own
session (config['session_id']) for later: an input with kind 'wakeup', not_before its due time and the session's
starting person as its actor. The session worker delivers it as the session's next turn once it is due
(knowledge_agents/session_worker.py). Only a session turn gets these tools (agents/runner.instructions_and_tools).
Replies are JSON."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import psycopg
from pydantic import Field

from knowledge_admin.capabilities.inventory import UNAVAILABLE as STORE_UNAVAILABLE
from knowledge_admin.capabilities.inventory import connect
from knowledge_admin.sessions.rows import CLOSED
from knowledge_admin.tools.registry import tool

MAX_PENDING = 3
EARLIEST, LATEST = timedelta(minutes=1), timedelta(days=30)
MAX_MINUTES = 43200  # LATEST in minutes
TOO_MANY = f'A session can have at most {MAX_PENDING} pending wake-ups.'
OUT_OF_RANGE = 'Choose a time between 1 minute and 30 days from now.'
EITHER = 'Give either in_minutes or at.'
NO_SESSION = 'Wake-ups need a session.'
NOT_PENDING = 'This wake-up is not pending in this session.'
UNAVAILABLE = 'Wake-ups are unavailable right now.'


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec='seconds')


def due_time(now: datetime, in_minutes: int | None, at: str | None) -> datetime | str:
    """When a wake-up is due: now + in_minutes, or `at` (an ISO time; without an offset it is UTC), 1 minute to 30 days
    from now; otherwise the refusal."""
    if (in_minutes is None) == (at is None):
        return EITHER
    if in_minutes is not None:
        if not 1 <= in_minutes <= MAX_MINUTES:  # before any date arithmetic: a huge value cannot overflow
            return OUT_OF_RANGE
        return now + timedelta(minutes=in_minutes)
    try:
        due = datetime.fromisoformat(at)
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        ahead = due - now
    except (ValueError, OverflowError):
        return OUT_OF_RANGE
    return due if EARLIEST <= ahead <= LATEST else OUT_OF_RANGE


def check_schedule_wakeup(
    access: dict, in_minutes: int | None = None, at: str | None = None, now: datetime | None = None, **_
) -> str | None:
    """Any run's access may schedule (its own session's turn); refused only for a time out of range or both/neither
    (due_time's refusal; the tool calls due_time directly, so the due time is computed once)."""
    due = due_time(now or datetime.now(UTC), in_minutes, at)
    return due if isinstance(due, str) else None


def check_list_wakeups(access: dict, **_) -> str | None:
    """Always allowed: a session's own wake-ups, not a resource of the run."""
    return None


def check_cancel_wakeup(access: dict, **_) -> str | None:
    """Always allowed (see check_list_wakeups); the tool cancels only a pending wake-up of its own session."""
    return None


def _reply(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _guarded(config: dict[str, Any], run) -> str:
    """run(conn, session_id) in one transaction, NO_SESSION outside a session, UNAVAILABLE when the database is."""
    session = config.get('session_id')
    if not session:
        return _reply({'refused': NO_SESSION})
    try:
        with connect() as conn:
            return _reply(run(conn, session))
    except ValueError as error:
        if str(error) != STORE_UNAVAILABLE:
            raise
        return _reply({'refused': UNAVAILABLE})
    except psycopg.OperationalError:
        return _reply({'refused': UNAVAILABLE})


def _schedule(config: dict[str, Any], message: str, due: datetime) -> str:
    def run(conn, session) -> dict:
        row = conn.execute(
            'SELECT status,actor_id FROM ka.agent_sessions WHERE id=%s FOR UPDATE', (session,)
        ).fetchone()
        if row is None:
            return {'refused': NO_SESSION}
        if row['status'] == 'closed':
            return {'refused': CLOSED}
        pending = conn.execute(
            """SELECT count(*) AS n FROM ka.agent_session_inputs
            WHERE session_id=%s AND kind='wakeup' AND status='queued'""",
            (session,),
        ).fetchone()['n']
        if pending >= MAX_PENDING:
            return {'refused': TOO_MANY}
        added = conn.execute(
            """INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text,kind,not_before)
            SELECT %(session)s,coalesce(max(seq),0)+1,%(actor)s,%(text)s,'wakeup',%(due)s
            FROM ka.agent_session_inputs WHERE session_id=%(session)s RETURNING id,not_before""",
            {'session': session, 'actor': row['actor_id'], 'text': message, 'due': due},
        ).fetchone()
        return {'id': added['id'], 'due_at': iso(added['not_before'])}

    return _guarded(config, run)


def _list(config: dict[str, Any]) -> str:
    def run(conn, session) -> dict:
        rows = conn.execute(
            """SELECT id,not_before,text FROM ka.agent_session_inputs
            WHERE session_id=%s AND kind='wakeup' AND status='queued' ORDER BY not_before,id""",
            (session,),
        ).fetchall()
        return {'wakeups': [{'id': r['id'], 'due_at': iso(r['not_before']), 'message': r['text']} for r in rows]}

    return _guarded(config, run)


def cancel_pending(conn, session_id, wakeup_id: int) -> bool:
    """Cancel a pending wake-up of this session (the tool and the session page): False when it is not pending there.
    Takes the session row lock first, as delivery does (session_worker.next_turn), so a cancel and a delivery of the same
    wake-up cannot both succeed."""
    conn.execute('SELECT 1 FROM ka.agent_sessions WHERE id=%s FOR UPDATE', (session_id,))
    row = conn.execute(
        """UPDATE ka.agent_session_inputs SET status='discarded'
        WHERE id=%s AND session_id=%s AND kind='wakeup' AND status='queued' RETURNING id""",
        (wakeup_id, session_id),
    ).fetchone()
    return row is not None


def _cancel(config: dict[str, Any], wakeup_id: int) -> str:
    def run(conn, session) -> dict:
        return {'cancelled': wakeup_id} if cancel_pending(conn, session, wakeup_id) else {'refused': NOT_PENDING}

    return _guarded(config, run)


@tool
async def schedule_wakeup(
    context,
    message: Annotated[
        str, Field(min_length=1, max_length=2000, description='What to remind yourself of when you wake up.')
    ],
    in_minutes: Annotated[
        int | None, Field(description='Wake up in this many minutes, 1-43200 (30 days). Give this or at.')
    ] = None,
    at: Annotated[
        str | None,
        Field(description='Wake up at this ISO time, such as 2026-10-22T15:30:00Z, 1 minute to 30 days ahead.'),
    ] = None,
) -> str:
    """Continue this session later: at the time you choose, this session gets a new turn with your message (it starts with "Scheduled wake-up"), with the whole conversation so far. Use it to look again at something slow, such as a deployment or a reply you are waiting for. A wake-up fires once; when woken you may schedule the next one. At most 3 pending wake-ups per session."""
    due = due_time(datetime.now(UTC), in_minutes, at)  # once; check_schedule_wakeup is this refusal for the matrix
    if isinstance(due, str):
        return _reply({'refused': due})
    return await asyncio.to_thread(_schedule, context['config'], message.replace('\x00', ''), due)


@tool
async def list_wakeups(context) -> str:
    """List this session's pending wake-ups: id, when each is due and its message."""
    if refusal := check_list_wakeups(context['config']['access']):
        return _reply({'refused': refusal})
    return await asyncio.to_thread(_list, context['config'])


@tool
async def cancel_wakeup(
    context, id: Annotated[int, Field(description='The wake-up id from schedule_wakeup or list_wakeups.')]
) -> str:
    """Cancel one of this session's pending wake-ups."""
    if refusal := check_cancel_wakeup(context['config']['access'], id=id):
        return _reply({'refused': refusal})
    return await asyncio.to_thread(_cancel, context['config'], id)
