"""The event processor: every pending event goes once through the enabled handlers of its project. A thread in
repository-worker claims one event at a time (FOR UPDATE SKIP LOCKED, so several workers never take the same event),
runs each matching handler's action in a savepoint, records one run per handler and marks the event processed — or
failed when a handler failed. Emitted events enter as pending and are processed on a later pass. Nothing is retried:
an event that cannot be processed at all is logged and marked failed, so it never blocks the queue. Send to agent
starts an ordinary agent run (sessions.start.start_run; agent-session-worker runs it)."""

import logging
import threading

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from knowledge_admin.events import matching as event_matching, targets, templates as event_templates
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.sessions import start

logger = logging.getLogger(__name__)

POLL_SECONDS = 2
BATCH = 50
MAX_DEPTH = 5
TOO_DEEP = f'Chain too deep ({MAX_DEPTH} levels).'
NO_AGENT = 'Agent not found.'
DISABLED = targets.DISABLED
UNEXPECTED = 'The handler failed unexpectedly. See the repository-worker log.'

CLAIM = "SELECT * FROM ka.events WHERE status='pending' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
HANDLERS = 'SELECT * FROM ka.event_handlers WHERE project_id=%s AND enabled ORDER BY name,id'


def run_message(text: str) -> str:
    """A run's message as the database takes it: no NUL characters (a template can render one), at most 2000 characters."""
    return text.replace('\x00', '')[:2000]


class HandlerFailed(Exception):
    """A handler action that could not run; the message is recorded on its run."""


def emit(conn, store: RepositoryStore, handler: dict, event: dict) -> tuple[str, dict]:
    if event['depth'] >= MAX_DEPTH:
        raise HandlerFailed(TOO_DEEP)
    new = event_templates.render_emit(handler['emit'], event)
    key = f'handler:{handler["id"]}:{event["id"]}'
    row = conn.execute(
        """INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,
        occurred_at,parent_id,root_id,depth,handler_id) VALUES (%s,'handler',%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)
        ON CONFLICT (project_id,dedupe_key) DO NOTHING RETURNING id""",
        (
            event['project_id'],
            handler['id'],
            new['type'],
            new['subject'],
            new['title'],
            Jsonb(new['payload']),
            key,
            event['id'],
            event['root_id'],
            event['depth'] + 1,
            handler['id'],
        ),
    ).fetchone()
    if row is None:  # emitted before (a crash after the commit cannot happen, but a re-run must not duplicate)
        row = conn.execute(
            'SELECT id FROM ka.events WHERE project_id=%s AND dedupe_key=%s', (event['project_id'], key)
        ).fetchone()
    return f'Emitted {new["type"]}.', {'emitted_event_id': row['id']}


def send(conn, store: RepositoryStore, handler: dict, event: dict) -> tuple[str, dict]:
    """Start an ordinary run of the handler's agent (purpose event) through the sessions start: the saved agent with
    everything it has (every tool, every expert), the rendered prompt as its first message, the handler's
    creator as actor, titled by the event. The agent is the handler's own or the one mapped to the ticket's assignee
    (events/targets.py). One run per event and handler: processing the event again links the run it started before."""
    agent_id, refusal = targets.pick_agent(handler, event, targets.mapping_rows(conn, handler, event))
    if refusal:
        raise HandlerFailed(refusal)
    agent = conn.execute('SELECT name,enabled FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()
    if agent is None:
        raise HandlerFailed(NO_AGENT)
    if not agent['enabled']:
        raise HandlerFailed(DISABLED)
    prompt = event_templates.render_prompt(handler['prompt'], event)
    started = f'Started a run of {agent["name"]}.'
    row = conn.execute(
        'SELECT id FROM ka.agent_sessions WHERE event_id=%s AND handler_id=%s', (event['id'], handler['id'])
    ).fetchone()
    if row is not None:
        return started, {'agent_session_id': row['id']}
    try:
        value = start.StartInput(agent_id=agent_id, message=prompt)
        session_id = start.start_run(
            conn,
            event['project_id'],
            handler['created_by'],
            value,
            store.conninfo,
            purpose='event',
            event_id=event['id'],
            handler_id=handler['id'],
            title=f'{agent["name"]} · {event["type"]} {event["subject"]}',
        )
    except HTTPException as exc:
        raise HandlerFailed(str(exc.detail)) from None
    except ValidationError as exc:  # a rendered prompt longer than a message may be
        raise HandlerFailed('; '.join(error['msg'] for error in exc.errors())) from None
    return started, {'agent_session_id': session_id}


ACTIONS = {'emit': emit, 'agent': send}


def process(conn, store: RepositoryStore, event: dict) -> str:
    """Run every matching enabled handler on the claimed event inside the caller's transaction; returns its status."""
    failed = False
    for handler in conn.execute(HANDLERS, (event['project_id'],)).fetchall():
        links: dict = {}
        try:
            if not event_matching.matches(handler, event):
                continue
            with conn.transaction():  # a savepoint: a failing action leaves nothing behind
                message, links = ACTIONS[handler['action']](conn, store, handler, event)
            status = 'succeeded'
        except (HandlerFailed, event_templates.TemplateProblem) as exc:
            status, message = 'failed', str(exc)
        except Exception:
            if conn.broken or conn.closed:
                raise  # the connection is gone: the event stays pending and is retried after reconnect
            logger.exception('Handler %s failed on event %s', handler['id'], event['id'])
            status, message = 'failed', UNEXPECTED
        failed = failed or status == 'failed'
        conn.execute(
            """INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message,emitted_event_id,
            agent_session_id) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                event['id'],
                handler['id'],
                handler['name'],
                status,
                run_message(message),
                links.get('emitted_event_id'),
                links.get('agent_session_id'),
            ),
        )
    status = 'failed' if failed else 'processed'
    conn.execute('UPDATE ka.events SET status=%s,processed_at=now() WHERE id=%s', (status, event['id']))
    return status


def run_once(store: RepositoryStore) -> bool:
    with store.connect() as conn:
        event = conn.execute(CLAIM).fetchone()
        if event is None:
            return False
        try:
            with conn.transaction():  # a savepoint: the claim's lock outlives a failure inside it
                status = process(conn, store, event)
        except Exception:
            if conn.broken or conn.closed:
                raise  # the connection is gone: the event stays pending and is retried after reconnect
            logger.exception('Event %s could not be processed; marking it failed', event['id'])
            conn.execute("UPDATE ka.events SET status='failed',processed_at=now() WHERE id=%s", (event['id'],))
            status = 'failed'
    if status == 'failed':
        print(f'Event {event["id"]} ({event["type"]}): failed', flush=True)
    return True


def tick(store: RepositoryStore, stopped: threading.Event | None = None) -> int:
    """Process up to BATCH pending events, oldest first, until stopped; returns how many."""
    done = 0
    while done < BATCH and not (stopped and stopped.is_set()) and run_once(store):
        done += 1
    return done


def loop(store: RepositoryStore, stopped: threading.Event) -> None:
    while not stopped.is_set():
        try:
            tick(store, stopped)
        except psycopg.Error:
            print('Event processor database unavailable; retrying', flush=True)
        except Exception:
            logger.exception('Event processor loop failed; retrying')
        stopped.wait(POLL_SECONDS)
