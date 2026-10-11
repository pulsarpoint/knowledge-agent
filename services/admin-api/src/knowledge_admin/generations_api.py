"""Generate documents sessions (an ad-hoc native agent) and Test sessions: create, the form's live context, list,
detail, send, stop, close, the event stream and saving the draft as a document. Turns run in
agent-session-worker."""
import asyncio
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.session_text import FINAL_ANSWER, close_idle
from knowledge_admin.data_root import documentation_folder
from knowledge_admin.documents import SAME_DOCUMENT_NAME, DocumentInput, document_slug, export_args, export_document, export_input, save_document
from knowledge_admin.generate_documents import resource_context, subjects
from knowledge_admin.knowledge_agents.config import AD_HOC_NAME, load_model, model_fits
from knowledge_admin.knowledge_agents.ephemeral import ad_hoc_config
from knowledge_admin.knowledge_agents.run_resources import RunResourceError
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.run_record import tool_counts_through

NOTHING_CHOSEN = 'Choose at least one code project, collection, environment, service or server to document.'
NO_MODEL = 'Choose an enabled model.'
ONLY_DOCUMENTS = 'Only documentation sessions have a document.'
CLOSED = 'This session is closed. Start a new session.'
NOTHING_TO_STOP = 'The agent is not working, so there is nothing to stop.'


SELECTION = ('code_project_ids', 'collection_ids', 'environment_ids', 'service_ids', 'server_ids')


class Selection(BaseModel):
    """What a generation documents: each id once, in the order chosen."""
    model_config = ConfigDict(extra='forbid')
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=20)
    collection_ids: list[UUID] = Field(default_factory=list, max_length=50)
    environment_ids: list[UUID] = Field(default_factory=list, max_length=20)
    service_ids: list[UUID] = Field(default_factory=list, max_length=50)
    server_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def once(self):
        for name in SELECTION:
            setattr(self, name, list(dict.fromkeys(getattr(self, name))))
        return self


class SessionInput(Selection):
    """Docs → Generate documents → New: an ad-hoc native agent with this model and system prompt, the context the form
    generated (or the user edited), the prompt as its first message, and the document's name and folder."""
    model_id: UUID
    system_prompt: str = Field(max_length=20000)
    context: str = Field(default='', max_length=100000)
    prompt: str = Field(max_length=100000)
    prompt_id: UUID | None = None
    document_name: str = Field(max_length=240)
    folder: str = Field(max_length=200)

    @model_validator(mode='after')
    def valid(self):
        texts = ('system_prompt', 'context', 'prompt', 'document_name')
        if any('\x00' in getattr(self, name) for name in texts):
            raise ValueError('Text cannot contain null characters')
        for name in texts:
            setattr(self, name, getattr(self, name).strip())
        if not self.system_prompt:
            raise ValueError('Enter a system prompt')
        if not self.prompt:
            raise ValueError('Enter a prompt')
        if not document_slug(self.document_name):
            raise ValueError('Use letters or digits in the document name')
        self.folder = documentation_folder(self.folder)
        if not any(getattr(self, name) for name in SELECTION):
            raise ValueError(NOTHING_CHOSEN)
        return self


def run_folders(config: dict) -> list[dict]:
    """The folders of the run's code projects, each once (chosen first, then brought by environments and services)."""
    folders, seen = [], set()
    for item in config['code']['projects']:
        for path in item['folder_paths']:
            if path not in seen:
                seen.add(path)
                folders.append({'path': path, 'kind': 'code_project', 'name': item['name'], 'code_project_id': item['id']})
    return folders


class SaveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    update_existing: bool = Field(default=False, strict=True)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=20000)

    @field_validator('text')
    @classmethod
    def valid_text(cls, value: str) -> str:
        if '\x00' in value or not value.strip():
            raise ValueError('Enter a message without null characters')
        return value.strip()


def _select(extra: str = '') -> str:
    return f"""SELECT s.id,s.title,s.agent_id,coalesce(a.name, '{AD_HOC_NAME}') AS agent_name,m.name AS model_name,s.status,s.status_message,s.turns,
        s.input_tokens,s.output_tokens,s.cached_tokens,s.purpose,s.model_calls,s.reasoning_tokens,s.document_id,d.title AS document_title,s.folder,
        coalesce(s.lease_expires_at > now(), false) AS worker_active,
        (SELECT count(*) FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued,
        s.created_at,s.last_activity_at,s.closed_at{extra}
        FROM ka.agent_sessions s LEFT JOIN ka.agents a ON a.id=s.agent_id JOIN ka.llm_models m ON m.id=s.model_id
        LEFT JOIN ka.document_details d ON d.id=s.document_id"""


SUMMARY = _select()
DETAIL = _select(',s.document_name,s.draft,s.draft_turn,s.prompt_snapshot,s.context_snapshot,s.model_id,s.run_resources,s.server_id')


def session_detail(conn, project: UUID, session_id: UUID) -> dict:
    row = conn.execute(DETAIL + ' WHERE s.id=%s AND s.project_id=%s', (session_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, 'Session not found.')
    row['inputs'] = conn.execute('''SELECT seq,text,status,turn,created_at FROM ka.agent_session_inputs
        WHERE session_id=%s ORDER BY seq''', (session_id,)).fetchall()
    row['tool_counts'], row['tool_counts_through_id'] = tool_counts_through(
        conn, [f'cc:{session_id}:{turn}' for turn in range(1, row['turns'] + 1)])
    if row['purpose'] in ('test', 'event'):  # its pinned configuration (credential references only) and its suggestions
        row['config'] = conn.execute('SELECT agent_config FROM ka.agent_sessions WHERE id=%s',
                                     (session_id,)).fetchone()['agent_config']
        row['reviews'] = conn.execute('''SELECT id,comment,suggestion,response_turn,created_at,context->'changes' AS changes
            FROM ka.agent_session_reviews WHERE session_id=%s ORDER BY created_at,id''', (session_id,)).fetchall()
    return row


DEFAULT_KEEPALIVE_SECONDS = 15.0
KEEPALIVE_SECONDS = DEFAULT_KEEPALIVE_SECONDS
HISTORY_LIMIT = 100  # events per page; the session page opens with the latest page
LIVE_UNAVAILABLE = 'Live session updates are not running.'


def stream_event(row: dict) -> dict:
    """One run event for the page; a tool result keeps its preview, the full output stays in the run record."""
    payload = dict(row['payload'])
    if row['kind'] == 'tool_result':
        payload.pop('output', None)
    return {'id': row['id'], 'turn': int(row['run_key'].rsplit(':', 1)[1]), 'seq': row['seq'], 'kind': row['kind'],
            'payload': payload, 'created_at': row['created_at']}


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/generations')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Generate documents storage is not configured.')
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

    @router.post('/context')
    def generation_context(value: Selection) -> dict:
        """The form's live context: what the selection says, as the ad-hoc agent will read it."""
        db = database()
        with db.connect() as conn:
            return {'text': resource_context(subjects(conn, db.project(conn, app_name), value))}

    @router.post('/sessions', status_code=201)
    def create_session(value: SessionInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            model = load_model(conn, value.model_id)
            if model is None or not model['enabled'] or not model_fits(model):
                raise HTTPException(400, NO_MODEL)
            described = subjects(conn, project, value)  # unknown ids: 400 naming the kind
            system_prompt = '\n\n'.join(part for part in (value.system_prompt, value.context) if part)
            try:
                pinned = ad_hoc_config(conn, project, model=model, system_prompt=system_prompt, selection=value)
            except RunResourceError as exc:
                raise HTTPException(400, str(exc)) from None
            context = {**described, 'folders': run_folders(pinned),
                       'code_project_ids': [item['id'] for item in described['code_projects']]}
            snapshot = {'system_prompt': value.system_prompt, 'context': value.context, 'prompt_id': value.prompt_id}
            session_id = uuid4()
            # The worker decrypts per turn: the pinned config holds credential references only.
            conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
                context_snapshot,document_name,folder,agent_config,run_resources)
                VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (session_id, project, actor, model['id'], value.document_name, Jsonb(jsonable_encoder(snapshot)),
                 Jsonb(context), value.document_name, value.folder, Jsonb(jsonable_encoder(pinned)),
                 Jsonb(jsonable_encoder(pinned['run_resources']))))
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                         (session_id, actor, f'{value.prompt}\n\n{FINAL_ANSWER}'))
            audit(conn, actor, 'generation.session.created', 'agent_session',
                  jsonable_encoder({'ephemeral': True, 'model_id': model['id'],
                                    'prompt_id': value.prompt_id}), session_id)
            return session_detail(conn, project, session_id)

    @router.get('/sessions')
    def sessions(response: Response, offset: int = Query(0, ge=0)) -> dict:
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            close_idle(conn)
            project = db.project(conn, app_name)
            return {'sessions': conn.execute(SUMMARY + " WHERE s.project_id=%s AND s.purpose='documents' ORDER BY s.created_at DESC,s.id LIMIT 50 OFFSET %s",
                                             (project, offset)).fetchall()}

    @router.get('/sessions/{session_id}')
    def session(session_id: UUID, response: Response) -> dict:
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            close_idle(conn)
            return session_detail(conn, db.project(conn, app_name), session_id)

    @router.post('/sessions/{session_id}/messages', status_code=202)
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

    @router.post('/sessions/{session_id}/stop', status_code=202)
    def stop(session_id: UUID, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            row = locked(conn, db.project(conn, app_name), session_id)
            if row['status'] != 'working':
                raise HTTPException(409, NOTHING_TO_STOP)
            actor = db.actor(conn, *request_actor(request))
            conn.execute('UPDATE ka.agent_sessions SET stop_requested_at=now() WHERE id=%s', (session_id,))
            discarded = discard(conn, session_id)
            audit(conn, actor, 'generation.session.stopped', 'agent_session', {'discarded': discarded}, session_id)
            return {'id': session_id, 'discarded': discarded}

    @router.post('/sessions/{session_id}/document', status_code=201)
    def save(session_id: UUID, value: SaveInput, request: Request) -> dict:
        """Save the current draft as a version of the session's document in its folder, then export it."""
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            session = conn.execute('SELECT * FROM ka.agent_sessions WHERE id=%s AND project_id=%s FOR UPDATE',
                                   (session_id, project)).fetchone()
            if session is None:
                raise HTTPException(404, 'Session not found.')
            if session['purpose'] != 'documents':
                raise HTTPException(400, ONLY_DOCUMENTS)
            if not (session['draft'] or '').strip():
                raise HTTPException(409, 'There is no draft yet. Wait for the agent to finish a turn.')
            actor = db.actor(conn, *request_actor(request))
            slug = document_slug(session['document_name'])
            existing = conn.execute('SELECT id,revision FROM ka.documents WHERE project_id=%s AND folder=%s AND slug=%s',
                                    (project, session['folder'], slug)).fetchone()
            if existing is not None and not value.update_existing:
                raise HTTPException(409, SAME_DOCUMENT_NAME)
            chosen = [UUID(item) for item in session['context_snapshot']['code_project_ids']]
            available = {row['id'] for row in conn.execute('''SELECT id FROM ka.repositories
                WHERE project_id=%s AND id=ANY(%s) AND archived_at IS NULL''', (project, chosen)).fetchall()}
            linked = [item for item in chosen if item in available]  # an archived project is no longer linked
            saved = save_document(conn, project, actor, DocumentInput(
                slug=slug, title=session['document_name'], purpose='code' if chosen else 'explanation',
                markdown=session['draft'], code_project_ids=linked,
                revision=existing['revision'] if existing is not None else None),
                existing['id'] if existing is not None else None, folder=session['folder'])
            conn.execute('UPDATE ka.agent_sessions SET document_id=%s,last_activity_at=now() WHERE id=%s',
                         (saved['id'], session_id))
            audit(conn, actor, 'generation.session.document_saved', 'agent_session',
                  {'document_id': str(saved['id']), 'revision': saved['revision']}, session_id)
            try:
                output, export_error = export_document(**export_args(export_input(conn, saved['id']))), None
            except (OSError, ValueError):
                output = None
                export_error = 'The document was saved. File export failed; retry Export to folder from the document page.'
        return jsonable_encoder({**saved, 'folder': session['folder'], 'output_folder': output, 'export_error': export_error})

    @router.post('/sessions/{session_id}/close')
    def close(session_id: UUID, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            row = locked(conn, db.project(conn, app_name), session_id)
            actor = db.actor(conn, *request_actor(request))
            if row['status'] != 'closed':
                conn.execute("""UPDATE ka.agent_sessions SET status='closed',closed_at=now(),status_message='Closed.'
                    WHERE id=%s""", (session_id,))
                discard(conn, session_id)
                audit(conn, actor, 'generation.session.closed', 'agent_session', {}, session_id)
            return {'id': session_id}

    @router.get('/sessions/{session_id}/events/history')
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

    @router.get('/sessions/{session_id}/events')
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
