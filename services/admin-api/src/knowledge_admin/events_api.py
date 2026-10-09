"""Administration → Events: things that happened at a source (the scheduled Jira pull) or that a handler emitted.
Read-only here; handlers (event_handlers_api.py) and the event processor act on them."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from knowledge_admin.repositories import RepositoryStore

LISTED = 'id,source,type,subject,title,occurred_at,created_at,status,depth,parent_id,root_id,handler_id'
PAGE, MAX_PAGE, MAX_OFFSET, MAX_CHAIN = 50, 200, 100000, 200
NOT_FOUND = 'Event not found.'
SORTS = {'time': 'occurred_at', 'source': 'source', 'type': 'type', 'subject': 'subject', 'status': 'status'}


def like(text: str) -> str:
    """A case-insensitive substring pattern with LIKE wildcards escaped."""
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Event storage is not configured')
        return store

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
            project = db.project(conn, app)
            row = conn.execute(f'SELECT {LISTED},source_id,dedupe_key,payload FROM ka.events WHERE id=%s AND project_id=%s',
                               (event_id, project)).fetchone()
            if row is None:
                raise HTTPException(404, NOT_FOUND)
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
        return {'event': row}

    return router
