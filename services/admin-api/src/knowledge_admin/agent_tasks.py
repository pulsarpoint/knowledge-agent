"""Agent tasks: one agent call a Send-to-agent handler queued for an event (ka.agent_tasks, inserted by the event
processor). agent-session-worker runs them here: it claims due tasks (FOR UPDATE SKIP LOCKED, a 15-minute lease), runs
the agent once with the rendered prompt — no conversation, no streaming, no session — and writes the answer, or why it
failed, to ka.notifications. A failing call is tried 3 times in all (again 30 s, then 2 minutes later); a task whose
agent is gone, disabled, per-run or without a usable model fails at once. Only the worker that still holds a task's
lease may finish it, so a worker that lost the lease records nothing. The turn limit (10 minutes) is shorter than the
lease, so a running call never outlives it."""
import asyncio
import logging
import os
import socket
from typing import Any

import psycopg

from knowledge_admin.agent_sessions import NO_MODEL
from knowledge_admin.agents import agent, load_credentials
from knowledge_admin.event_handlers_api import PER_RUN
from knowledge_admin.knowledge_agents.config import get_agent_config, model_fits, per_run
from knowledge_admin.knowledge_agents.native_sessions import failure
from knowledge_admin.knowledge_agents.session_worker import clean_text
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

POLL_SECONDS = 2
LEASE_MINUTES = 15
TURN_SECONDS = 600
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = {1: 30, 2: 120}  # before the second and the third attempt
MAX_ERROR = 2000
MAX_BODY = 200_000
NO_AGENT = 'Agent not found.'
DISABLED = 'Agent is disabled.'
LOST = 'The worker stopped while running this task, and it has no attempts left.'

CLAIM = f"""UPDATE ka.agent_tasks t SET status='running', worker_id=%(worker)s,
    lease_expires_at=now() + interval '{LEASE_MINUTES} minutes', started_at=coalesce(t.started_at, now()),
    attempts=t.attempts + 1, claims=t.claims + 1
    FROM (SELECT id FROM ka.agent_tasks
          WHERE (status='queued' AND next_attempt_at <= now()) OR (status='running' AND lease_expires_at < now())
          ORDER BY next_attempt_at, id LIMIT %(limit)s FOR UPDATE SKIP LOCKED) due
    WHERE t.id=due.id RETURNING t.*"""
HELD = """SELECT id FROM ka.agent_tasks WHERE id=%s AND status='running' AND worker_id=%s AND lease_expires_at=%s
    FOR UPDATE"""


class NoRetry(Exception):
    """A task that trying again cannot fix; the message becomes its failed notification."""


def claim(store: RepositoryStore, worker_id: str, limit: int) -> list[dict[str, Any]]:
    """Up to `limit` due tasks — queued and due, or running with an expired lease — now running for this worker."""
    if limit < 1:
        return []
    with store.connect() as conn:
        return conn.execute(CLAIM, {'worker': worker_id, 'limit': limit}).fetchall()


def record_key(task: dict[str, Any]) -> str:
    """One run record per claim: a released attempt runs again under a new key (run events are unique per key and sequence)."""
    return f"task:{task['id']}:{task['claims']}"


def load(store: RepositoryStore, task: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The agent's current configuration for one call — no experts (a task runs alone) and recorded in Agent runs as
    an event task — and its decrypted credentials. NoRetry: a problem that trying again cannot fix."""
    try:
        config = get_agent_config(task['agent_id'], conninfo=store.conninfo)
    except LookupError:
        raise NoRetry(NO_AGENT) from None
    except ValueError as exc:
        raise NoRetry(str(exc)) from None
    if per_run(config):
        raise NoRetry(PER_RUN)
    if not config['enabled']:
        raise NoRetry(DISABLED)
    model = config['model']
    if model is None or not model['enabled'] or not model_fits(model):
        raise NoRetry(NO_MODEL)
    config['experts'] = []
    config['record'] = {'run_key': record_key(task), 'project_id': str(task['project_id']), 'kind': 'event_task',
                        'title': task['title']}
    try:
        return config, load_credentials(store, config)
    except ValueError as exc:  # a disabled model, SSH server or MCP server, or a missing credential
        raise NoRetry(str(exc)) from None


def finish(store: RepositoryStore, task: dict[str, Any], outcome: str, text: str) -> bool:
    """End the attempt while this worker still holds the task (same worker and lease): `retry` queues it again after
    the backoff; `succeeded`/`failed` write the notification. False: the lease was lost and nothing is recorded."""
    text = text.replace('\x00', '')
    with store.connect() as conn:
        if conn.execute(HELD, (task['id'], task['worker_id'], task['lease_expires_at'])).fetchone() is None:
            return False
        if outcome == 'retry':
            conn.execute("""UPDATE ka.agent_tasks SET status='queued', worker_id=NULL, lease_expires_at=NULL, error=%s,
                next_attempt_at=now() + make_interval(secs => %s) WHERE id=%s""",
                (text[:MAX_ERROR], BACKOFF_SECONDS[task['attempts']], task['id']))
            return True
        notification = conn.execute("""INSERT INTO ka.notifications(project_id,kind,status,title,body,event_id,handler_id,
            agent_id,agent_task_id) VALUES (%s,'agent.response',%s,%s,%s,(SELECT id FROM ka.events WHERE id=%s),%s,%s,%s)
            RETURNING id""", (task['project_id'], outcome, task['title'], text[:MAX_BODY], task['event_id'],
                              task['handler_id'], task['agent_id'], task['id'])).fetchone()['id']
        conn.execute("""UPDATE ka.agent_tasks SET status=%s, worker_id=NULL, lease_expires_at=NULL, finished_at=now(),
            notification_id=%s, error=%s WHERE id=%s""",
            (outcome, notification, None if outcome == 'succeeded' else text[:MAX_ERROR], task['id']))
    return True


def release(store: RepositoryStore, task: dict[str, Any]) -> None:
    """Give a task back when the worker stops during an attempt: queued again, the attempt not counted."""
    with store.connect() as conn:
        conn.execute("""UPDATE ka.agent_tasks SET status='queued', attempts=attempts - 1, worker_id=NULL,
            lease_expires_at=NULL WHERE id=%s AND status='running' AND worker_id=%s AND lease_expires_at=%s""",
            (task['id'], task['worker_id'], task['lease_expires_at']))


NO_ANSWER = '(The agent returned no answer.)'


async def run_task(store: RepositoryStore, task: dict[str, Any], run_agent=agent) -> None:
    """Run one claimed task to the end of its attempt: a notification, or queued again for another attempt."""
    if task['attempts'] > MAX_ATTEMPTS:  # its lease ran out during the last attempt
        await asyncio.to_thread(finish, store, task, 'failed', LOST)
        return
    secrets: list[str] = []
    try:
        config, credentials = await asyncio.to_thread(load, store, task)
        secrets[:] = [value for value in credentials.values() if value]
        result = await run_agent(config, task['prompt'], credentials=credentials, stream=False,
                                 turn_timeout_seconds=TURN_SECONDS)
        answer = '' if result.final_output is None else clean_text(str(result.final_output), secrets)
        outcome, text = 'succeeded', answer if answer.strip() else NO_ANSWER
    except NoRetry as exc:
        outcome, text = 'failed', str(exc)
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(release, store, task))
        raise
    except Exception as exc:
        text = failure(exc, secrets)
        outcome = 'retry' if task['attempts'] < MAX_ATTEMPTS else 'failed'
        logger.warning('Agent task %s attempt %s failed (%s)', task['id'], task['attempts'], type(exc).__name__)
    if not await asyncio.to_thread(finish, store, task, outcome, text):
        logger.info('Agent task %s claim %s lost its lease; nothing recorded', task['id'], task['claims'])


async def guarded(store: RepositoryStore, task: dict[str, Any], run_agent) -> None:
    """run_task that never takes the loop down: a task that cannot be finished keeps its lease and is claimed again
    when the lease runs out."""
    try:
        await run_task(store, task, run_agent)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('Agent task %s could not be finished', task['id'])


def task_limit(limit: int | None) -> int:
    """The number of tasks run at once: `limit`, else AGENT_TASK_MAX (default 2); a whole number of at least 1."""
    try:
        value = int(os.environ.get('AGENT_TASK_MAX', '2')) if limit is None else int(limit)
    except ValueError:
        value = 0
    if value < 1:
        raise ValueError('AGENT_TASK_MAX must be a whole number of at least 1.')
    return value


async def serve(store: RepositoryStore, run_agent=agent, *, stopped: asyncio.Event, worker_id: str | None = None,
                limit: int | None = None, poll: float = POLL_SECONDS) -> None:
    """Run due tasks, AGENT_TASK_MAX of them at once (default 2), until stopped; running ones are then given back
    (also when serve itself is cancelled or fails)."""
    worker_id = worker_id or f'{socket.gethostname()}:{os.getpid()}:tasks'
    limit = task_limit(limit)
    running: set[asyncio.Task] = set()
    try:
        while not stopped.is_set():
            try:
                if len(running) < limit:
                    for task in await asyncio.to_thread(claim, store, worker_id, limit - len(running)):
                        job = asyncio.create_task(guarded(store, task, run_agent))
                        running.add(job)
                        job.add_done_callback(running.discard)
            except psycopg.Error as exc:
                logger.warning('Agent task runner database unavailable (%s); retrying', type(exc).__name__)
            except Exception:
                logger.exception('Agent task runner loop failed; retrying')
            try:
                await asyncio.wait_for(stopped.wait(), poll)
            except asyncio.TimeoutError:
                pass
    finally:
        await asyncio.sleep(0)  # let tasks claimed just now start, so cancelling them releases them
        for job in list(running):
            job.cancel()
        await asyncio.gather(*running, return_exceptions=True)
