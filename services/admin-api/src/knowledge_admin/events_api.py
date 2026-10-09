"""Administration → Events: things that happened at a source (the scheduled Jira pull), that a handler emitted, or that
someone made by hand to test handlers. Handlers (event_handlers_api.py) and the event processor act on them."""
import json
import re
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from knowledge_admin.access import request_actor
from knowledge_admin.repositories import RepositoryStore

LISTED = 'id,source,type,subject,title,occurred_at,created_at,status,depth,parent_id,root_id,handler_id'
PAGE, MAX_PAGE, MAX_OFFSET, MAX_CHAIN = 50, 200, 100000, 200
NOT_FOUND = 'Event not found.'
SORTS = {'time': 'occurred_at', 'source': 'source', 'type': 'type', 'subject': 'subject', 'status': 'status'}
TYPE_PATTERN = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')
MAX_TEXT, MAX_PAYLOAD = 500, 100 * 1024
MADE_BY_HAND = "dedupe_key LIKE 'manual:%%'"


class NewEvent(BaseModel):
    """Loosely typed on purpose: check() gives every field its own message."""
    model_config = ConfigDict(extra='forbid')
    type: Any = None
    subject: Any = None
    title: Any = ''
    payload: Any = None
    source: Any = 'manual'


def has_nul(value: Any) -> bool:
    if isinstance(value, str):
        return '\x00' in value
    if isinstance(value, dict):
        return any(has_nul(key) or has_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(has_nul(item) for item in value)
    return False


def check(value: NewEvent) -> dict:
    """The event's fields, or a 400 naming the first bad one."""
    def bad(message: str):
        raise HTTPException(400, message)
    if not isinstance(value.type, str) or not TYPE_PATTERN.match(value.type) or has_nul(value.type):
        bad('Type must look like jira.ticket.created: lowercase words separated by dots.')
    subject = value.subject.strip() if isinstance(value.subject, str) else ''
    if not subject or len(subject) > MAX_TEXT or has_nul(subject):
        bad(f'Subject is required and at most {MAX_TEXT} characters.')
    title = value.title if value.title is not None else ''
    if not isinstance(title, str) or len(title) > MAX_TEXT or has_nul(title):
        bad(f'Title must be text of at most {MAX_TEXT} characters.')
    payload = {} if value.payload is None else value.payload
    if not isinstance(payload, dict):
        bad('Payload must be a JSON object.')
    if has_nul(payload):
        bad('Payload cannot contain the NUL character (\\u0000).')
    if len(json.dumps(payload).encode()) > MAX_PAYLOAD:
        bad('Payload is too large (100 KB at most).')
    if value.source not in ('manual', 'jira'):
        bad('Source must be manual or jira.')
    return {'type': value.type, 'subject': subject, 'title': title, 'payload': payload, 'source': value.source}


def like(text: str) -> str:
    """A case-insensitive substring pattern with LIKE wildcards escaped."""
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Event storage is not configured')
        return store

    def detail(conn, project: UUID, event_id: int) -> dict | None:
        row = conn.execute(f'SELECT {LISTED},source_id,dedupe_key,payload,({MADE_BY_HAND}) AS created_manually FROM ka.events WHERE id=%s AND project_id=%s',
                           (event_id, project)).fetchone()
        if row is None:
            return None
        row['parent'] = None if row['parent_id'] is None else conn.execute(
            f'SELECT {LISTED} FROM ka.events WHERE id=%s AND project_id=%s', (row['parent_id'], project)).fetchone()
        row['chain'] = conn.execute(f'SELECT {LISTED} FROM ka.events WHERE project_id=%s AND root_id=%s ORDER BY depth,id LIMIT %s',
                                    (project, row['root_id'], MAX_CHAIN)).fetchall()
        row['runs'] = conn.execute("""SELECT r.handler_id,r.handler_name,r.status,r.message,r.emitted_event_id,
            r.agent_session_id,coalesce(s.agent_id,t.agent_id) AS agent_id,r.created_at,
            CASE WHEN t.id IS NULL THEN NULL ELSE jsonb_build_object('id',t.id,'status',t.status,'attempts',t.attempts,
                'notification_id',t.notification_id) END AS agent_task
            FROM ka.event_handler_runs r LEFT JOIN ka.agent_sessions s ON s.id=r.agent_session_id
            LEFT JOIN ka.agent_tasks t ON t.id=r.agent_task_id WHERE r.event_id=%s ORDER BY r.id""", (event_id,)).fetchall()
        return row

    @router.post('/events', status_code=201)
    def create(value: NewEvent, request: Request) -> dict:
        fields = check(value)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            actor = db.actor(conn, *request_actor(request))
            with conn.transaction():
                event_id = conn.execute("""INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,occurred_at,status)
                    VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,now(),'pending') RETURNING id""",
                    (project, fields['source'], fields['type'], fields['subject'], fields['title'], Jsonb(fields['payload']),
                     f'manual:{uuid4()}')).fetchone()['id']
                conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                    VALUES (%s,%s,'event.created','event',NULL,%s)""", (project, actor,
                    Jsonb({'event_id': event_id, 'type': fields['type'], 'subject': fields['subject'], 'source': fields['source']})))
            return {'event': detail(conn, project, event_id)}

    @router.get('/events')
    def list_events(source: str | None = Query(None, max_length=50), type: str | None = Query(None, max_length=100),
                    status: Literal['pending', 'processed', 'failed'] | None = None, handler_id: UUID | None = None,
                    root_id: int | None = Query(None, ge=1), q: str | None = Query(None, max_length=200),
                    sort: Literal['time', 'source', 'type', 'subject', 'status'] = 'time',
                    order: Literal['asc', 'desc'] = 'desc', offset: int = Query(0, ge=0, le=MAX_OFFSET),
                    limit: int = Query(PAGE, ge=1, le=MAX_PAGE)) -> dict:
        db = database()
        with db.connect() as conn:
            where, values = ['project_id=%s'], [db.project(conn, app)]
            for column, value in (('source', source), ('type', type), ('status', status), ('handler_id', handler_id),
                                  ('root_id', root_id)):
                if value:
                    where.append(f'{column}=%s'); values.append(value)
            if q and q.strip():
                where.append("(subject ILIKE %s ESCAPE '\\' OR title ILIKE %s ESCAPE '\\')"); values += [like(q.strip())] * 2
            direction = 'ASC' if order == 'asc' else 'DESC'
            rows = conn.execute(f"""SELECT {LISTED} FROM ka.events WHERE {' AND '.join(where)}
                ORDER BY {SORTS[sort]} {direction},id {direction} LIMIT %s OFFSET %s""", (*values, limit + 1, offset)).fetchall()
        more = len(rows) > limit
        return {'events': rows[:limit], 'next_offset': offset + limit if more else None}

    @router.get('/events/facets')
    def facets() -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            distinct = lambda column: [row[column] for row in conn.execute(
                f'SELECT DISTINCT {column} FROM ka.events WHERE project_id=%s ORDER BY 1', (project,))]
            handlers = conn.execute('SELECT id,name FROM ka.event_handlers WHERE project_id=%s ORDER BY name', (project,)).fetchall()
            return {'sources': distinct('source'), 'types': distinct('type'), 'statuses': distinct('status'), 'handlers': handlers}

    @router.get('/events/{event_id}')
    def one(event_id: int) -> dict:
        db = database()
        with db.connect() as conn:
            row = detail(conn, db.project(conn, app), event_id)
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return {'event': row}

    return router
