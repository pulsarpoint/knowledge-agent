"""/api/sessions: starting a run (options, preview, start), the run operations for every kind of run (detail, send,
stop, close, the event stream) and the lists."""
import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb

from knowledge_admin.access import request_actor
from knowledge_admin.agent_sessions import ReviewInput, review_session
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.session_text import close_idle, notify_closed_before_first_turn
from knowledge_admin.sessions import start
from knowledge_admin.sessions.rows import (CLOSED, NOTHING_TO_STOP, SUMMARY, MessageInput, SessionPurpose, session_detail,
                                           stream_event)

DEFAULT_KEEPALIVE_SECONDS = 15.0
KEEPALIVE_SECONDS = DEFAULT_KEEPALIVE_SECONDS
HISTORY_LIMIT = 100  # events per page; the session page opens with the latest page
LIVE_UNAVAILABLE = 'Live session updates are not running.'


LIST = SUMMARY.replace('FROM ka.agent_sessions s', ''',p.email AS started_by,
        (SELECT x.id FROM ka.document_drafts x WHERE x.session_id=s.id AND x.status='open'
         ORDER BY x.created_at DESC,x.id DESC LIMIT 1) AS draft_id
        FROM ka.agent_sessions s LEFT JOIN ka.principals p ON p.id=s.actor_id''', 1)
assert LIST != SUMMARY
PROPOSAL_COUNTS = ''.join(f""",(SELECT count(*) FROM ka.discovery_proposals dp WHERE dp.session_id=s.id
        AND dp.status='{status}')::int AS {status}""" for status in ('pending', 'accepted', 'rejected'))
LIST_WITH_PROPOSALS = LIST.replace(',p.email AS started_by', ',p.email AS started_by' + PROPOSAL_COUNTS, 1)
assert LIST_WITH_PROPOSALS != LIST


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/sessions')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Agent session storage is not configured.')
        return store

    def audit(conn, actor, action: str, resource_type: str, details: dict, resource_id=None) -> None:
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,%s,%s,%s)''', (store.project(conn, app_name), actor, action, resource_type, resource_id, Jsonb(details)))

    def locked(conn, project, session_id: UUID) -> dict:
        row = conn.execute('SELECT id,status FROM ka.agent_sessions WHERE id=%s AND project_id=%s FOR UPDATE',
                           (session_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, 'Session not found.')
        return row

    def discard(conn, session_id: UUID) -> int:
        return conn.execute("UPDATE ka.agent_session_inputs SET status='discarded' WHERE session_id=%s AND status='queued'",
                            (session_id,)).rowcount

    @router.get('')
    def sessions(response: Response, purpose: SessionPurpose | None = None, document_id: UUID | None = None,
                 agent_id: str | None = None, server_id: UUID | None = None, offset: int = Query(0, ge=0)) -> dict:
        """Runs, newest first: those started from a document (open first, then by activity), of one purpose, an
        agent's Test and event sessions, or the runs started from a server (with their proposal counts)."""
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            close_idle(conn)
            where, values = ['s.project_id=%s'], [db.project(conn, app_name)]
            if document_id is not None:
                where.append('s.target_document_id=%s'); values.append(document_id)
            if purpose is not None:
                where.append('s.purpose=%s'); values.append(purpose)
            if agent_id is not None:
                where.append("s.agent_id=%s AND s.purpose IN ('test','event')"); values.append(agent_id)
            if server_id is not None:
                where.append("s.server_id=%s AND s.purpose='discovery'"); values.append(server_id)
            query = LIST_WITH_PROPOSALS if server_id is not None else LIST
            order = "s.status='closed',s.last_activity_at DESC,s.id" if document_id else 's.created_at DESC,s.id'
            return {'sessions': conn.execute(f"{query} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT %s OFFSET %s",
                                             (*values, 100 if document_id or agent_id else 50, offset)).fetchall()}

    @router.get('/options')
    def options(request: Request, document_id: UUID | None = Query(None), agent_id: str | None = Query(None),
                new_document: bool = Query(False), server_id: UUID | None = Query(None)) -> dict:
        db = database()
        starts = sum((document_id is not None, new_document, server_id is not None))
        if starts > 1 or (agent_id is not None and (new_document or server_id is not None)):
            raise HTTPException(400, start.ONE_START)
        if agent_id is not None and document_id is not None:
            raise HTTPException(400, 'Choose a document or an agent.')
        with db.connect() as conn:
            if agent_id is not None:
                return start.agent_options(conn, db.project(conn, app_name), agent_id, db.conninfo)
            subject, _ = request_actor(request)  # read-only: a GET does not create the principal
            actor = conn.execute('SELECT id FROM ka.principals WHERE issuer=%s AND subject=%s', (db.issuer, subject)).fetchone()
            return start.options(conn, db.project(conn, app_name), actor['id'] if actor else None, document_id, new_document,
                                 server_id)

    @router.post('/preview')
    def preview(value: start.StartInput) -> dict:
        db = database()
        with db.connect() as conn:
            pinned, _, _ = start.build(conn, db.project(conn, app_name), value, None, db.conninfo)
        return {'config': pinned, 'first_message': value.message}

    @router.post('', status_code=201)
    def start_session(value: start.StartInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            session_id = start.start_run(conn, project, actor, value, db.conninfo)
            return session_detail(conn, project, session_id)

    @router.post('/{session_id}/review')
    async def review(session_id: UUID, value: ReviewInput, request: Request) -> dict:
        """Suggest improvements for a Test or event session (a tuning review; nothing is applied)."""
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
        return await review_session(db, project, session_id, value, request_actor(request))

    @router.get('/{session_id}')
    def session(session_id: UUID, response: Response) -> dict:
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            close_idle(conn)
            return session_detail(conn, db.project(conn, app_name), session_id)

    @router.post('/{session_id}/messages', status_code=202)
    def send(session_id: UUID, value: MessageInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            close_idle(conn)
            row = locked(conn, db.project(conn, app_name), session_id)
            if row['status'] == 'closed':
                raise HTTPException(409, CLOSED)
            actor = db.actor(conn, *request_actor(request))
            seq = conn.execute('SELECT coalesce(max(seq),0)+1 AS seq FROM ka.agent_session_inputs WHERE session_id=%s',
                               (session_id,)).fetchone()['seq']
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,%s,%s,%s)',
                         (session_id, seq, actor, value.text))
            conn.execute('UPDATE ka.agent_sessions SET last_activity_at=now() WHERE id=%s', (session_id,))
            return {'seq': seq, 'status': 'queued', 'working': row['status'] == 'working'}

    @router.post('/{session_id}/stop', status_code=202)
    def stop(session_id: UUID, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))  # before the session: promote's lock order
            row = locked(conn, project, session_id)
            if row['status'] != 'working':
                raise HTTPException(409, NOTHING_TO_STOP)
            conn.execute('UPDATE ka.agent_sessions SET stop_requested_at=now() WHERE id=%s', (session_id,))
            discarded = discard(conn, session_id)
            audit(conn, actor, 'generation.session.stopped', 'agent_session', {'discarded': discarded}, session_id)
            return {'id': session_id, 'discarded': discarded}

    @router.post('/{session_id}/close')
    def close(session_id: UUID, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))  # before the session: promote's lock order
            row = locked(conn, project, session_id)
            if row['status'] != 'closed':
                conn.execute("""UPDATE ka.agent_sessions SET status='closed',closed_at=now(),status_message='Closed.'
                    WHERE id=%s""", (session_id,))
                discard(conn, session_id)
                notify_closed_before_first_turn(conn, session_id)
                audit(conn, actor, 'generation.session.closed', 'agent_session', {}, session_id)
            return {'id': session_id}

    @router.get('/{session_id}/events/history')
    def history(session_id: UUID, response: Response, before: int | None = Query(None, ge=1),
                limit: int = Query(HISTORY_LIMIT, ge=1, le=500)) -> dict:
        """A page of the session's events, oldest first: the latest page, or the page before an event id. Each event is
        what the stream sends (a tool result keeps its preview; the full output stays in the run record)."""
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            row = conn.execute('SELECT turns FROM ka.agent_sessions WHERE id=%s AND project_id=%s',
                               (session_id, db.project(conn, app_name))).fetchone()
            if row is None:
                raise HTTPException(404, 'Session not found.')
            keys = [f'cc:{session_id}:{turn}' for turn in range(1, row['turns'] + 1)]
            rows = conn.execute('''SELECT id,run_key,seq,kind,payload,created_at FROM ka.agent_run_events
                WHERE run_key=ANY(%s) AND (%s::bigint IS NULL OR id<%s) ORDER BY id DESC LIMIT %s''',
                (keys, before, before, limit + 1)).fetchall()
        return jsonable_encoder({'events': [stream_event(item) for item in reversed(rows[:limit])],
                                 'has_more': len(rows) > limit})

    @router.get('/{session_id}/events')
    async def events(session_id: UUID, request: Request, after: int = Query(0, ge=0)):
        """The session's live stream: run events after Last-Event-ID (or `after`), the state when it changes, `closed`
        at the end, `unavailable` once while the database cannot be reached, `: keepalive` after KEEPALIVE_SECONDS of
        quiet, and `retry: 1000` ending a stream that fell behind (the browser reconnects from its last id). The
        process's SessionHub reads the database; the stream holds no connection."""
        db = database()
        with db.connect() as conn:
            if conn.execute('SELECT 1 FROM ka.agent_sessions WHERE id=%s AND project_id=%s',
                            (session_id, db.project(conn, app_name))).fetchone() is None:
                raise HTTPException(404, 'Session not found.')
        hub = getattr(request.app.state, 'session_hub', None)
        if hub is None:
            raise HTTPException(503, LIVE_UNAVAILABLE)
        last = request.headers.get('last-event-id', '')
        start_after = int(last) if last.isdigit() else after

        async def stream():
            # Leaving the page cancels this generator, which unsubscribes only this page.
            async with hub.subscribe(session_id, start_after) as subscription:
                while True:
                    try:
                        kind, value = await asyncio.wait_for(subscription.get(), KEEPALIVE_SECONDS)
                    except TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ': keepalive\n\n'
                        continue
                    if kind == 'event':
                        yield (f"id: {value['id']}\nevent: run_event\n"
                               f'data: {json.dumps(jsonable_encoder(stream_event(value)))}\n\n')
                    elif kind == 'session':
                        yield f'event: session\ndata: {json.dumps(value)}\n\n'
                    elif kind == 'unavailable':
                        yield 'event: unavailable\ndata: {}\n\n'
                    elif kind == 'resync':
                        yield 'retry: 1000\n\n'
                        return
                    else:  # closed
                        yield 'event: closed\ndata: {}\n\n'
                        return

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    return router
