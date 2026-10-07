"""Claude Code agents (registrations: a name and a description) and their interactive documentation sessions."""
import asyncio
import json
import os
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
from knowledge_admin.claude_code import SYSTEM_PROMPT, close_idle, first_message
from knowledge_admin.data_root import data_dir, data_folder, data_path, documentation_folder
from knowledge_admin.documentation_generation import PROMPT_CHANGED, SAME_DOCUMENT_NAME
from knowledge_admin.documents import DocumentInput, document_slug, export_args, export_document, export_input, save_document
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.storage import storage_path

AGENT_FIELDS = 'id,name,description,runtime,enabled,revision'


class AgentInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=4000)
    revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator('name', 'description')
    @classmethod
    def no_nul(cls, value: str) -> str:
        if '\x00' in value:
            raise ValueError('Text cannot contain null characters')
        return value


NOT_CLAUDE = 'Select an enabled Claude model (Bedrock or Anthropic) from LLM Settings.'
CLOSED = 'This session is closed. Start a new session.'
NOTHING_TO_STOP = 'The agent is not working, so there is nothing to stop.'


class SessionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str = Field(min_length=1, max_length=100)
    model_id: UUID
    prompt_id: UUID
    prompt_version_id: UUID
    comments: str = Field(default='', max_length=20000)
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=20)
    folders: list[str] = Field(default_factory=list, max_length=20)
    server_ids: list[UUID] = Field(default_factory=list, max_length=50)
    notes: str = Field(default='', max_length=20000)
    document_name: str = Field(max_length=240)
    folder: str = Field(max_length=200)

    @model_validator(mode='after')
    def valid(self):
        if any('\x00' in text for text in (self.comments, self.notes, self.document_name)):
            raise ValueError('Text cannot contain null characters')
        self.document_name = self.document_name.strip()
        if not document_slug(self.document_name):
            raise ValueError('Use letters or digits in the document name')
        self.folder = documentation_folder(self.folder)
        self.folders = list(dict.fromkeys(data_folder(folder) for folder in self.folders))
        self.code_project_ids = list(dict.fromkeys(self.code_project_ids))
        self.server_ids = list(dict.fromkeys(self.server_ids))
        if not self.code_project_ids and not self.folders:
            raise ValueError('Add at least one code project or folder as context')
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
    refused = HTTPException(400, f'The folder {path} is a symbolic link or outside DATA_ROOT. Remove it from the context.')
    container = PurePosixPath(path)
    if not container.is_relative_to(data_dir()) or container == data_dir():
        raise refused
    try:
        storage_path('data', container.relative_to(data_dir()).as_posix())
    except HTTPException as exc:
        if exc.status_code == 503:
            raise
        raise refused from None


def session_context(conn, project: UUID, value: SessionInput) -> dict:
    """Working folders as container paths (code project folders in the chosen order, then DATA_ROOT folders), servers
    as text and the notes. Every folder must exist now, inside DATA_ROOT, without a symbolic link on the way."""
    code_root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
    if value.code_project_ids and (not code_root.is_absolute() or '..' in code_root.parts):
        raise HTTPException(503, 'CODE_ROOT must be an absolute mounted path')
    projects = {row['id']: row for row in conn.execute("""SELECT r.id,r.name,
        ARRAY(SELECT path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY path) AS folder_paths
        FROM ka.repositories r WHERE r.project_id=%s AND r.id=ANY(%s) AND r.archived_at IS NULL""",
        (project, value.code_project_ids)).fetchall()}
    if len(projects) != len(value.code_project_ids):
        raise HTTPException(400, 'Select available code projects.')
    candidates = []
    for project_id in value.code_project_ids:
        item = projects[project_id]
        if not item['folder_paths']:
            raise HTTPException(400, f"The code project {item['name']} has no folders.")
        candidates += [{'path': str(code_root / path), 'kind': 'code_project', 'name': item['name'],
                        'code_project_id': str(project_id)} for path in item['folder_paths']]
    candidates += [{'path': str(data_path(folder)), 'kind': 'folder', 'name': folder} for folder in value.folders]
    folders, seen = [], set()
    for item in candidates:
        if item['path'] in seen:
            continue
        seen.add(item['path'])
        if not Path(item['path']).is_dir():
            raise HTTPException(400, f"The folder {item['path']} does not exist. Remove it from the context.")
        inside_data_root(item['path'])
        folders.append(item)
    servers = conn.execute('''SELECT id,name,hostname,description FROM ka.servers WHERE project_id=%s AND id=ANY(%s)
        ORDER BY lower(name),id''', (project, value.server_ids)).fetchall()
    if len(servers) != len(value.server_ids):
        raise HTTPException(400, 'Select available servers.')
    return jsonable_encoder({'folders': folders, 'code_project_ids': value.code_project_ids, 'servers': servers,
                             'notes': value.notes})


STREAM_POLL_SECONDS = 1.0
DEFAULT_KEEPALIVE_SECONDS = 15.0
KEEPALIVE_SECONDS = DEFAULT_KEEPALIVE_SECONDS
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

    @router.post('/agents', status_code=201)
    def create_agent(value: AgentInput, request: Request) -> dict:
        if value.revision is not None:
            raise HTTPException(400, 'New agents cannot reuse a revision')
        db = database()
        agent_id = str(uuid4())
        try:
            with db.connect() as conn:
                actor = db.actor(conn, *request_actor(request))
                conn.execute('''INSERT INTO ka.agents(id,name,description,runtime,created_by,updated_by)
                    VALUES (%s,%s,%s,'claude_code',%s,%s)''', (agent_id, value.name, value.description, actor, actor))
                audit(conn, actor, 'agent.created', 'agent', {'agent_id': agent_id, 'runtime': 'claude_code'})
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'An agent with this name already exists.') from None
        return {'id': agent_id}

    @router.put('/agents/{agent_id}')
    def update_agent(agent_id: str, value: AgentInput, request: Request) -> dict:
        db = database()
        try:
            with db.connect() as conn:
                row = conn.execute('SELECT revision,runtime FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,)).fetchone()
                if row is None or row['runtime'] != 'claude_code':
                    raise HTTPException(404, 'Claude Code agent not found.')
                if row['revision'] != value.revision:
                    raise HTTPException(409, 'Agent changed. Reload before saving.')
                actor = db.actor(conn, *request_actor(request))
                conn.execute('UPDATE ka.agents SET name=%s,description=%s,revision=revision+1,updated_by=%s WHERE id=%s',
                             (value.name, value.description, actor, agent_id))
                audit(conn, actor, 'agent.updated', 'agent', {'agent_id': agent_id, 'runtime': 'claude_code'})
                return conn.execute(f'SELECT {AGENT_FIELDS} FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'An agent with this name already exists.') from None

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
            model = conn.execute('SELECT id,provider,model,api_format,enabled FROM ka.llm_models WHERE id=%s FOR SHARE',
                                 (value.model_id,)).fetchone()
            if model is None or not model['enabled'] or not claude_model(model['provider'], model['api_format'], model['model']):
                raise HTTPException(400, NOT_CLAUDE)
            prompt = conn.execute("""SELECT p.id,p.name,v.id AS version_id,v.version,v.content
                FROM ka.documentation_prompts p JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
                WHERE p.id=%s AND p.project_id=%s AND p.enabled""", (value.prompt_id, project)).fetchone()
            if prompt is None:
                raise HTTPException(400, 'Select an active documentation prompt.')
            if prompt['version_id'] != value.prompt_version_id:
                raise HTTPException(409, PROMPT_CHANGED)
            context = session_context(conn, project, value)
            snapshot = jsonable_encoder({**prompt, 'comments': value.comments, 'system_prompt': SYSTEM_PROMPT})
            session_id = uuid4()
            conn.execute("""INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
                context_snapshot,document_name,folder) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (session_id, project, agent['id'], actor, model['id'], value.document_name, Jsonb(snapshot), Jsonb(context),
                 value.document_name, value.folder))
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                         (session_id, actor, first_message(prompt['content'], value.comments, context)))
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
