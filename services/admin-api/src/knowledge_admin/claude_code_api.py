"""Interactive Claude Code documentation sessions: create, list, detail, send, stop,
close, the event stream and saving the draft as a document."""
import asyncio
import json
import time
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from knowledge_llm import claude_model
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.claude_code import agent_system_prompt, close_idle
from knowledge_admin.data_root import data_dir, documentation_folder
from knowledge_admin.documents import SAME_DOCUMENT_NAME, DocumentInput, document_slug, export_args, export_document, export_input, save_document
from knowledge_admin.generate_documents import first_message, subjects
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.storage import storage_path

NOTHING_CHOSEN = 'Choose at least one code project, environment, service or server to document.'
NO_FOLDER = ('Claude Code needs at least one working folder: choose code projects, or environments or services with '
             'code projects, or give the agent code projects.')
AGENT_MODEL = 'The agent has no enabled Claude model. Choose one on its General tab.'
CLOSED = 'This session is closed. Start a new session.'
NOTHING_TO_STOP = 'The agent is not working, so there is nothing to stop.'


class SessionInput(BaseModel):
    """Docs → Generate documents → New. The agent runs with its own model, prompt, instructions, skills and tools."""
    model_config = ConfigDict(extra='forbid')
    agent_id: str = Field(min_length=1, max_length=100)
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=20)
    environment_ids: list[UUID] = Field(default_factory=list, max_length=20)
    service_ids: list[UUID] = Field(default_factory=list, max_length=50)
    server_ids: list[UUID] = Field(default_factory=list, max_length=50)
    instructions: str = Field(default='', max_length=20000)
    document_name: str = Field(max_length=240)
    folder: str = Field(max_length=200)

    @model_validator(mode='after')
    def valid(self):
        if '\x00' in self.instructions or '\x00' in self.document_name:
            raise ValueError('Text cannot contain null characters')
        self.instructions = self.instructions.strip()
        self.document_name = self.document_name.strip()
        if not document_slug(self.document_name):
            raise ValueError('Use letters or digits in the document name')
        self.folder = documentation_folder(self.folder)
        for name in ('code_project_ids', 'environment_ids', 'service_ids', 'server_ids'):
            setattr(self, name, list(dict.fromkeys(getattr(self, name))))
        if not (self.code_project_ids or self.environment_ids or self.service_ids or self.server_ids):
            raise ValueError(NOTHING_CHOSEN)
        return self


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
    return f"""SELECT s.id,s.title,s.agent_id,a.name AS agent_name,m.name AS model_name,s.status,s.status_message,s.turns,
        s.input_tokens,s.output_tokens,s.cached_tokens,s.cost_usd,s.document_id,d.title AS document_title,s.folder,
        coalesce(s.lease_expires_at > now(), false) AS worker_active,
        (SELECT count(*) FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued,
        s.created_at,s.last_activity_at,s.closed_at{extra}
        FROM ka.agent_sessions_cc s JOIN ka.agents a ON a.id=s.agent_id JOIN ka.llm_models m ON m.id=s.model_id
        LEFT JOIN ka.document_details d ON d.id=s.document_id"""


SUMMARY = _select()
DETAIL = _select(',s.document_name,s.draft,s.draft_turn,s.prompt_snapshot,s.context_snapshot,s.model_id')


def session_detail(conn, project: UUID, session_id: UUID) -> dict:
    row = conn.execute(DETAIL + ' WHERE s.id=%s AND s.project_id=%s', (session_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, 'Session not found.')
    row['inputs'] = conn.execute('''SELECT seq,text,status,turn,created_at FROM ka.agent_session_inputs
        WHERE session_id=%s ORDER BY seq''', (session_id,)).fetchall()
    return row


def inside_data_root(path: str) -> None:
    """The storage rule for a working folder: inside DATA_ROOT, and no part of it is a symbolic link."""
    refused = HTTPException(400, f"The folder {path} is a symbolic link or outside DATA_ROOT. Check the code project's folders.")
    container = PurePosixPath(path)
    if not container.is_relative_to(data_dir()) or container == data_dir():
        raise refused
    try:
        storage_path('data', container.relative_to(data_dir()).as_posix())
    except HTTPException as exc:
        if exc.status_code == 503:
            raise
        raise refused from None


def session_context(conn, project: UUID, value: SessionInput, agent_projects: list[dict]) -> dict:
    """What to document (generate_documents.subjects) plus the working folders: the chosen code projects, the code
    projects of the chosen environments and services, then the agent's own code projects, each folder once. Every
    folder must exist now, inside DATA_ROOT, without a symbolic link on the way."""
    context = subjects(conn, project, value)
    candidates = [(path, item, False) for item in context['code_projects'] for path in item['paths']]
    candidates += [(path, item, False) for group in ('environments', 'services') for subject in context[group]
                   for item in subject['code_projects'] for path in item['paths']]
    candidates += [(path, {'id': str(item['id']), 'name': item['name']}, True)
                   for item in agent_projects for path in item['folder_paths']]
    folders, seen = [], set()
    for path, item, from_agent in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not Path(path).is_dir():
            raise HTTPException(400, f"The folder {path} of the agent's code project {item['name']} does not exist. "
                                     'Remove the project from the agent or check its folders.' if from_agent else
                                f"The folder {path} of the code project {item['name']} does not exist. "
                                "Check the code project's folders.")
        inside_data_root(path)
        folders.append({'path': path, 'kind': 'code_project', 'name': item['name'], 'code_project_id': item['id']})
    if not folders:
        raise HTTPException(400, NO_FOLDER)
    return {**context, 'folders': folders, 'code_project_ids': [item['id'] for item in context['code_projects']]}


STREAM_POLL_SECONDS = 1.0
DEFAULT_KEEPALIVE_SECONDS = 15.0
KEEPALIVE_SECONDS = DEFAULT_KEEPALIVE_SECONDS
HISTORY_LIMIT = 100  # events per page; the session page opens with the latest page
STATE = """SELECT s.status,s.status_message,s.turns,s.input_tokens,s.output_tokens,s.cached_tokens,s.cost_usd,s.draft,
    s.draft_turn,s.document_id,coalesce(s.lease_expires_at > now(), false) AS worker_active,
    (SELECT count(*) FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued
    FROM ka.agent_sessions_cc s WHERE s.id=%s"""


def stream_event(row: dict) -> dict:
    """One run event for the page; a tool result keeps its preview, the full output stays in the run record."""
    payload = dict(row['payload'])
    if row['kind'] == 'tool_result':
        payload.pop('output', None)
    return {'id': row['id'], 'turn': int(row['run_key'].rsplit(':', 1)[1]), 'seq': row['seq'], 'kind': row['kind'],
            'payload': payload, 'created_at': row['created_at']}


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/claude-code')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Claude Code storage is not configured.')
        return store

    def audit(conn, actor, action: str, resource_type: str, details: dict, resource_id=None) -> None:
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,%s,%s,%s)''', (store.project(conn, app_name), actor, action, resource_type, resource_id, Jsonb(details)))

    def locked(conn, project, session_id: UUID) -> dict:
        row = conn.execute('SELECT id,status FROM ka.agent_sessions_cc WHERE id=%s AND project_id=%s FOR UPDATE',
                           (session_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, 'Session not found.')
        return row

    def discard(conn, session_id: UUID) -> int:
        return conn.execute("UPDATE ka.agent_session_inputs SET status='discarded' WHERE session_id=%s AND status='queued'",
                            (session_id,)).rowcount

    @router.post('/sessions', status_code=201)
    def create_session(value: SessionInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            agent = conn.execute('SELECT id,enabled,runtime FROM ka.agents WHERE id=%s FOR SHARE', (value.agent_id,)).fetchone()
            if agent is None or agent['runtime'] != 'claude_code' or not agent['enabled']:
                raise HTTPException(400, 'Select an enabled Claude Code agent.')
            try:  # the agent's saved config: its active version, or its working settings before the first version
                config = get_agent_config(agent['id'], conninfo=db.conninfo)
            except (LookupError, ValueError) as exc:
                raise HTTPException(400, str(exc)) from None
            model_id = (config['model'] or {}).get('id')  # the agent's own model; the form has none
            model = conn.execute('SELECT id,provider,model,api_format,enabled FROM ka.llm_models WHERE id=%s FOR SHARE',
                                 (model_id,)).fetchone() if model_id else None
            if model is None or not model['enabled'] or not claude_model(model['provider'], model['api_format'], model['model']):
                raise HTTPException(400, AGENT_MODEL)
            context = session_context(conn, project, value, config['code']['projects'] if config['code']['enabled'] else [])
            snapshot = jsonable_encoder({'system_prompt': agent_system_prompt(config),
                                         'claude_code_tools': config['claude_code_tools'],
                                         'agent_version': config.get('config_version')})
            session_id = uuid4()
            conn.execute("""INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
                context_snapshot,document_name,folder) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (session_id, project, agent['id'], actor, model['id'], value.document_name, Jsonb(snapshot), Jsonb(context),
                 value.document_name, value.folder))
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                         (session_id, actor, first_message(context)))
            audit(conn, actor, 'claude_code.session.created', 'agent_session', {'agent_id': agent['id']}, session_id)
            return session_detail(conn, project, session_id)

    @router.get('/sessions')
    def sessions(response: Response, offset: int = Query(0, ge=0)) -> dict:
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            close_idle(conn)
            project = db.project(conn, app_name)
            return {'sessions': conn.execute(SUMMARY + ' WHERE s.project_id=%s ORDER BY s.created_at DESC,s.id LIMIT 50 OFFSET %s',
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
            conn.execute('UPDATE ka.agent_sessions_cc SET last_activity_at=now() WHERE id=%s', (session_id,))
            return {'seq': seq, 'status': 'queued', 'working': row['status'] == 'working'}

    @router.post('/sessions/{session_id}/stop', status_code=202)
    def stop(session_id: UUID, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            row = locked(conn, db.project(conn, app_name), session_id)
            if row['status'] != 'working':
                raise HTTPException(409, NOTHING_TO_STOP)
            actor = db.actor(conn, *request_actor(request))
            conn.execute('UPDATE ka.agent_sessions_cc SET stop_requested_at=now() WHERE id=%s', (session_id,))
            discarded = discard(conn, session_id)
            audit(conn, actor, 'claude_code.session.stopped', 'agent_session', {'discarded': discarded}, session_id)
            return {'id': session_id, 'discarded': discarded}

    @router.post('/sessions/{session_id}/document', status_code=201)
    def save(session_id: UUID, value: SaveInput, request: Request) -> dict:
        """Save the current draft as a version of the session's document in its folder, then export it."""
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            session = conn.execute('SELECT * FROM ka.agent_sessions_cc WHERE id=%s AND project_id=%s FOR UPDATE',
                                   (session_id, project)).fetchone()
            if session is None:
                raise HTTPException(404, 'Session not found.')
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
            conn.execute('UPDATE ka.agent_sessions_cc SET document_id=%s,last_activity_at=now() WHERE id=%s',
                         (saved['id'], session_id))
            audit(conn, actor, 'claude_code.session.document_saved', 'agent_session',
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
                conn.execute("""UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now(),status_message='Closed.'
                    WHERE id=%s""", (session_id,))
                discard(conn, session_id)
                audit(conn, actor, 'claude_code.session.closed', 'agent_session', {}, session_id)
            return {'id': session_id}

    @router.get('/sessions/{session_id}/events/history')
    def history(session_id: UUID, response: Response, before: int | None = Query(None, ge=1),
                limit: int = Query(HISTORY_LIMIT, ge=1, le=500)) -> dict:
        """A page of the session's events, oldest first: the latest page, or the page before an event id. Each event is
        what the stream sends (a tool result keeps its preview; the full output stays in the run record)."""
        response.headers['Cache-Control'] = 'no-store'
        db = database()
        with db.connect() as conn:
            row = conn.execute('SELECT turns FROM ka.agent_sessions_cc WHERE id=%s AND project_id=%s',
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
        db = database()
        with db.connect() as conn:
            if conn.execute('SELECT 1 FROM ka.agent_sessions_cc WHERE id=%s AND project_id=%s',
                            (session_id, db.project(conn, app_name))).fetchone() is None:
                raise HTTPException(404, 'Session not found.')
        last = request.headers.get('last-event-id', '')
        start_after = int(last) if last.isdigit() else after

        async def stream():
            # Poll by event id over this session's turn keys (cc:<session>:<turn>) and the session row; leaving the
            # page closes only this subscriber.
            position, state, quiet_since = start_after, None, time.monotonic()
            try:
                async with await psycopg.AsyncConnection.connect(db.conninfo, autocommit=True, row_factory=dict_row,
                                                                 connect_timeout=5) as conn:
                    while True:
                        current = await (await conn.execute(STATE, (session_id,))).fetchone()
                        if current is None:  # the session was removed
                            yield 'event: closed\ndata: {}\n\n'
                            return
                        keys = [f'cc:{session_id}:{turn}' for turn in range(1, current['turns'] + 1)]
                        rows = await (await conn.execute('''SELECT id,run_key,seq,kind,payload,created_at
                            FROM ka.agent_run_events WHERE run_key=ANY(%s) AND id>%s ORDER BY id LIMIT 500''',
                            (keys, position))).fetchall()
                        for row in rows:
                            position, quiet_since = row['id'], time.monotonic()
                            yield f'id: {position}\nevent: run_event\ndata: {json.dumps(jsonable_encoder(stream_event(row)))}\n\n'
                        if len(rows) == 500:
                            continue
                        public = jsonable_encoder(current)
                        if public != state:
                            state, quiet_since = public, time.monotonic()
                            yield f'event: session\ndata: {json.dumps(public)}\n\n'
                        if current['status'] == 'closed':
                            yield 'event: closed\ndata: {}\n\n'
                            return
                        if await request.is_disconnected():
                            return
                        if time.monotonic() - quiet_since >= KEEPALIVE_SECONDS:
                            quiet_since = time.monotonic()
                            yield ': keepalive\n\n'
                        await asyncio.sleep(STREAM_POLL_SECONDS)
            except psycopg.Error:
                yield 'event: unavailable\ndata: {}\n\n'

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    return router
