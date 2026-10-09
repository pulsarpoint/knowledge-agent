"""Administration → Events: things that happened at a source (today the scheduled Jira pull). Read-only; handling
comes later."""
from fastapi import APIRouter, HTTPException, Query

from knowledge_admin.repositories import RepositoryStore

LISTED = 'id,source,type,subject,title,occurred_at,created_at'
PAGE, MAX_PAGE = 50, 200
NOT_FOUND = 'Event not found.'


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
                    q: str | None = Query(None, max_length=200), before: int | None = Query(None, ge=1),
                    limit: int = Query(PAGE, ge=1, le=MAX_PAGE)) -> dict:
        db = database()
        with db.connect() as conn:
            where, values = ['project_id=%s'], [db.project(conn, app)]
            if source:
                where.append('source=%s'); values.append(source)
            if type:
                where.append('type=%s'); values.append(type)
            if q and q.strip():
                where.append("(subject ILIKE %s ESCAPE '\\' OR title ILIKE %s ESCAPE '\\')"); values += [like(q.strip())] * 2
            if before:
                where.append('id<%s'); values.append(before)
            rows = conn.execute(f"SELECT {LISTED} FROM ka.events WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s",
                                (*values, limit + 1)).fetchall()
        more = len(rows) > limit
        return {'events': rows[:limit], 'next_before': rows[limit - 1]['id'] if more else None}

    @router.get('/events/facets')
    def facets() -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            sources = [row['source'] for row in conn.execute('SELECT DISTINCT source FROM ka.events WHERE project_id=%s ORDER BY 1', (project,))]
            types = [row['type'] for row in conn.execute('SELECT DISTINCT type FROM ka.events WHERE project_id=%s ORDER BY 1', (project,))]
        return {'sources': sources, 'types': types}

    @router.get('/events/{event_id}')
    def one(event_id: int) -> dict:
        db = database()
        with db.connect() as conn:
            row = conn.execute(f'SELECT {LISTED},source_id,dedupe_key,payload FROM ka.events WHERE id=%s AND project_id=%s',
                               (event_id, db.project(conn, app))).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return {'event': row}

    return router
