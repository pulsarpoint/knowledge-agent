"""The event processor: every pending event goes once through the enabled handlers of its project. A thread in
repository-worker claims one event at a time (FOR UPDATE SKIP LOCKED, so several workers never take the same event),
runs each matching handler's action in a savepoint, records one run per handler and marks the event processed — or
failed when a handler failed. Emitted events enter as pending and are processed on a later pass. Nothing is retried."""
import logging
import threading

import psycopg
from psycopg.types.json import Jsonb

from knowledge_admin import event_matching, event_templates
from knowledge_admin.agent_sessions import SessionRefused, start_session
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

POLL_SECONDS = 2
BATCH = 50
MAX_DEPTH = 5
TOO_DEEP = f'Chain too deep ({MAX_DEPTH} levels).'
NO_AGENT = 'Agent not found.'
UNEXPECTED = 'The handler failed unexpectedly. See the repository-worker log.'

CLAIM = "SELECT * FROM ka.events WHERE status='pending' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
HANDLERS = 'SELECT * FROM ka.event_handlers WHERE project_id=%s AND enabled ORDER BY name,id'


class HandlerFailed(Exception):
    """A handler action that could not run; the message is recorded on its run."""


def emit(conn, store: RepositoryStore, handler: dict, event: dict) -> tuple[str, dict]:
    if event['depth'] >= MAX_DEPTH:
        raise HandlerFailed(TOO_DEEP)
    new = event_templates.render_emit(handler['emit'], event)
    key = f"handler:{handler['id']}:{event['id']}"
    row = conn.execute("""INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,
        occurred_at,parent_id,root_id,depth,handler_id) VALUES (%s,'handler',%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)
        ON CONFLICT (project_id,dedupe_key) DO NOTHING RETURNING id""",
        (event['project_id'], handler['id'], new['type'], new['subject'], new['title'], Jsonb(new['payload']), key,
         event['id'], event['root_id'], event['depth'] + 1, handler['id'])).fetchone()
    if row is None:  # emitted before (a crash after the commit cannot happen, but a re-run must not duplicate)
        row = conn.execute('SELECT id FROM ka.events WHERE project_id=%s AND dedupe_key=%s', (event['project_id'], key)).fetchone()
    return f"Emitted {new['type']}.", {'emitted_event_id': row['id']}


def send(conn, store: RepositoryStore, handler: dict, event: dict) -> tuple[str, dict]:
    agent = conn.execute('SELECT name FROM ka.agents WHERE id=%s', (handler['agent_id'],)).fetchone()
    if agent is None:
        raise HandlerFailed(NO_AGENT)
    prompt = event_templates.render_prompt(handler['prompt'], event)
    try:
        session = start_session(conn, event['project_id'], handler['agent_id'], handler['created_by'], prompt,
                                purpose='event', title=f"{event['type']} {event['subject']}", conninfo=store.conninfo)
    except SessionRefused as exc:
        raise HandlerFailed(str(exc)) from None
    return f"Started a session with {agent['name']}.", {'agent_session_id': session}


ACTIONS = {'emit': emit, 'agent': send}


def process(conn, store: RepositoryStore, event: dict) -> str:
    """Run every matching enabled handler on the claimed event inside the caller's transaction; returns its status."""
    failed = False
    for handler in conn.execute(HANDLERS, (event['project_id'],)).fetchall():
        if not event_matching.matches(handler, event):
            continue
        links: dict = {}
        try:
            with conn.transaction():  # a savepoint: a failing action leaves nothing behind
                message, links = ACTIONS[handler['action']](conn, store, handler, event)
            status = 'succeeded'
        except (HandlerFailed, event_templates.TemplateProblem) as exc:
            status, message = 'failed', str(exc)
        except psycopg.OperationalError:
            raise  # the connection is gone: the event stays pending
        except Exception:
            logger.exception('Handler %s failed on event %s', handler['id'], event['id'])
            status, message = 'failed', UNEXPECTED
        failed = failed or status == 'failed'
        conn.execute("""INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message,emitted_event_id,
            agent_session_id) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (event['id'], handler['id'], handler['name'], status, message[:2000],
             links.get('emitted_event_id'), links.get('agent_session_id')))
    status = 'failed' if failed else 'processed'
    conn.execute('UPDATE ka.events SET status=%s,processed_at=now() WHERE id=%s', (status, event['id']))
    return status


def run_once(store: RepositoryStore) -> bool:
    with store.connect() as conn:
        event = conn.execute(CLAIM).fetchone()
        if event is None:
            return False
        status = process(conn, store, event)
    if status == 'failed':
        print(f"Event {event['id']} ({event['type']}): failed", flush=True)
    return True


def tick(store: RepositoryStore) -> int:
    """Process up to BATCH pending events, oldest first; returns how many."""
    done = 0
    while done < BATCH and run_once(store):
        done += 1
    return done


def loop(store: RepositoryStore, stopped: threading.Event) -> None:
    while not stopped.is_set():
        try:
            tick(store)
        except psycopg.Error:
            print('Event processor database unavailable; retrying', flush=True)
        except Exception:
            logger.exception('Event processor loop failed; retrying')
        stopped.wait(POLL_SECONDS)
