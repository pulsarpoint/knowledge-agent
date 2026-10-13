"""One way to start an ad-hoc agent session: /agent-sessions — the options a start panel needs, Preview (exactly what
Start pins), Start and the sessions about something. Kinds: revision (Ask agent on a document: the document tools
get_document, save_document and edit_document, the chosen resources and tools, experts and subagents only when chosen). Generate
documents and server discovery keep their own endpoints until they move here."""
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from psycopg import errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.capabilities.document_tools import outline
from knowledge_admin.sessions.rows import NO_MODEL, Selection, session_detail
from knowledge_admin.knowledge_agents.config import load_model, model_fits
from knowledge_admin.knowledge_agents.ephemeral import ad_hoc_config
from knowledge_admin.knowledge_agents.experts import offered
from knowledge_admin.knowledge_agents.run_resources import RunResourceError, bring
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.tools.catalog import CATALOG, DOCUMENT_EDIT_TOOLS, RUN_ONLY_NEEDS, SUBAGENT, needs_met

FIXED_TOOLS = ('get_document', *DOCUMENT_EDIT_TOOLS)
REVISION_TAG = 'document-revision'
DOCUMENT_NOT_FOUND = 'Document not found'
DOCUMENT_FOREIGN_KEY = 'agent_sessions_target_document'
UNKNOWN_TOOL = 'Unknown tool: {names}.'
NEEDS = '{tool} needs {what}.'
REQUIRES = {'files': 'a code project or a collection', 'code': 'a code project', 'code_graph': 'a code project',
            'documents': 'a collection', 'ssh': 'at least one server'}
NO_EXPERT = 'This expert is not available: {id}.'
NOT_SET_UP = 'SSH is not set up for this server.'
NOT_ALLOWED = 'SSH is not allowed for this server.'
TITLE_LENGTH = 120
DEFAULT_REVISION_PROMPT = '''You revise one technical document at a person's request. Read the parts of the document
you change before you change them, and check facts against the servers, code and documents you were given. Keep the
document's structure and voice; change only what the request asks for. Never include passwords, keys or tokens.'''
RULES = '''Rules:
- Read the document with get_document: without arguments for its outline, with section for one section, with start_line
  and end_line for lines.
- Change the document only with edit_document (one exact passage) or save_document (the complete text). Each save updates your
  draft; a person reviews it and decides whether it becomes the live version. Never say the change is live.
- When the request is unclear, ask in plain chat before you change the document.
- When you are done, say in a few sentences what you changed.'''
RESOURCE_NAMES = {'server_ids': ('servers', 'SELECT id,name FROM ka.servers WHERE id=ANY(%s) ORDER BY lower(name)'),
                  'service_ids': ('services', 'SELECT id,name FROM ka.services WHERE id=ANY(%s) ORDER BY lower(name)'),
                  'environment_ids': ('environments', 'SELECT id,name FROM ka.environments WHERE id=ANY(%s) ORDER BY lower(name)'),
                  'code_project_ids': ('code projects', 'SELECT id,name FROM ka.repositories WHERE id=ANY(%s) ORDER BY lower(name)')}
RUNS = '''SELECT s.id,s.title,s.status,s.created_at,s.last_activity_at,m.name AS model_name,p.email AS started_by,
    (SELECT x.id FROM ka.document_drafts x WHERE x.session_id=s.id AND x.status='open'
     ORDER BY x.created_at DESC,x.id DESC LIMIT 1) AS draft_id
    FROM ka.agent_sessions s JOIN ka.llm_models m ON m.id=s.model_id LEFT JOIN ka.principals p ON p.id=s.actor_id
    WHERE s.project_id=%s AND s.purpose='revision' AND s.target_document_id=%s
    ORDER BY s.status='closed',s.last_activity_at DESC,s.id LIMIT 100'''


class RevisionTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: UUID


class StartInput(BaseModel):
    """What a start panel sends. kind picks the target's shape (revision: a document)."""
    model_config = ConfigDict(extra='forbid')
    kind: Literal['revision']
    target: RevisionTarget
    instruction: str = Field(max_length=20000)
    model_id: UUID
    prompt_id: UUID | None = None
    system_prompt: str = Field(max_length=20000)
    resources: Selection = Field(default_factory=Selection)
    tools: list[str] = Field(default_factory=list, max_length=100)
    expert_ids: list[UUID] = Field(default_factory=list, max_length=50)
    subagents: bool = False

    @model_validator(mode='after')
    def valid(self):
        for name in ('instruction', 'system_prompt'):
            value = getattr(self, name)
            if '\x00' in value:
                raise ValueError('Text cannot contain null characters')
            setattr(self, name, value.strip())
        if not self.instruction:
            raise ValueError('Enter an instruction')
        if not self.system_prompt:
            raise ValueError('Enter a system prompt')
        self.tools = list(dict.fromkeys(self.tools))
        self.expert_ids = list(dict.fromkeys(self.expert_ids))
        return self


def choosable() -> list:
    """The tools a person can switch on for a run: not run-only, not "Answer only from documents", not run_subagent
    (its own switch)."""
    return [spec for spec in CATALOG.values()
            if spec.needs not in RUN_ONLY_NEEDS and spec.needs != 'grounded' and spec.name != SUBAGENT]


def server_reason(row: dict) -> str | None:
    if not row['enabled'] or row['credential_id'] is None:
        return NOT_SET_UP
    if not row['ssh_allowed']:
        return NOT_ALLOWED
    return None


def target_document(conn, project: UUID, document_id: UUID) -> dict:
    row = conn.execute('''SELECT d.id,d.title,d.folder,d.description,d.purpose,d.audience,d.code_project_ids,d.service_ids,
        d.server_ids,d.environment_ids,v.markdown FROM ka.document_details d JOIN ka.document_versions v ON v.id=d.current_version_id
        WHERE d.id=%s AND d.project_id=%s''', (document_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return row


def revision_context(conn, document: dict) -> str:
    """What the agent knows about its document before it reads it: the fields, linked resources and outline."""
    lines = ['# The document you revise', f"Title: {document['title']}",
             f"Folder: {document['folder'] or '(top folder)'}", f"Description: {document['description'] or '(none)'}",
             f"Purpose: {document['purpose']}", f"Audience: {document['audience']}"]
    for key, (label, query) in RESOURCE_NAMES.items():
        names = [row['name'] for row in conn.execute(query, (list(document[key] or []),)).fetchall()]
        if names:
            lines.append(f"Linked {label}: {', '.join(names)}")
    markdown = document['markdown']
    headings = '\n'.join(f"{h['line']}: {'#' * h['level']} {h['text']}" for h in outline(markdown)) or '(no headings)'
    lines += ['', f'Length: {len(markdown.splitlines())} lines, {len(markdown)} characters', '', 'Outline:', headings, '', RULES]
    return '\n'.join(lines)


def build(conn, project: UUID, value: StartInput, session_id: UUID | None) -> tuple[dict, dict, dict]:
    """(pinned configuration, model, document) for a revision run: preview and start share it, so they cannot differ."""
    document = target_document(conn, project, value.target.document_id)
    model = load_model(conn, value.model_id)
    if model is None or not model['enabled'] or not model_fits(model):
        raise HTTPException(400, NO_MODEL)
    for row in conn.execute('SELECT id,name,enabled,ssh_allowed,credential_id FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
                            (project, value.resources.server_ids)).fetchall():
        reason = server_reason(row)
        if reason:
            raise HTTPException(400, f"Server {row['name']}: {reason}")
    names = {spec.name: spec for spec in choosable()}
    unknown = [name for name in value.tools if name not in names and name not in FIXED_TOOLS]
    if unknown:
        raise HTTPException(400, UNKNOWN_TOOL.format(names=', '.join(unknown)))
    system_prompt = f'{value.system_prompt}\n\n{revision_context(conn, document)}'
    try:
        pinned = ad_hoc_config(conn, project, model=model, system_prompt=system_prompt, selection=value.resources)
    except RunResourceError as exc:
        raise HTTPException(400, str(exc)) from None
    keyed = [server for server in pinned['ssh']['servers'] if server.get('private_key_ref') is not None]
    if len(keyed) != len(pinned['ssh']['servers']):  # a server a service or environment brought in, without an SSH key
        pinned['ssh'] = {**pinned['ssh'], 'servers': keyed, 'enabled': pinned['ssh']['enabled'] and bool(keyed)}
        if 'server_ids' in pinned.get('run_resources', {}):
            kept = {str(server['id']) for server in keyed}
            pinned['run_resources'] = {**pinned['run_resources'],
                                       'server_ids': [i for i in pinned['run_resources']['server_ids'] if str(i) in kept]}
    unmet = [NEEDS.format(tool=name, what=REQUIRES[names[name].needs]) for name in value.tools
             if name in names and not needs_met(names[name].needs, pinned, None)]
    if unmet:
        raise HTTPException(400, ' '.join(unmet))
    chosen = set(value.tools)
    for item in pinned['tools']['items']:
        if item['tool'] in FIXED_TOOLS:
            item.update(mode='on', subagent_allowed=False)
        elif item['tool'] == SUBAGENT:
            item['mode'] = 'on' if value.subagents else 'off'
        elif item['tool'] in CATALOG:
            item['mode'] = 'on' if item['tool'] in chosen else 'off'
    experts = {entry['id']: entry for entry in pinned['experts']}
    for expert_id in value.expert_ids:
        if str(expert_id) not in experts:
            raise HTTPException(400, NO_EXPERT.format(id=expert_id))
    pinned['experts'] = [experts[str(expert_id)] for expert_id in value.expert_ids]
    pinned['project_id'] = str(project)
    pinned['revision_run'] = {'session_id': str(session_id) if session_id else None, 'document_id': str(document['id'])}
    return jsonable_encoder(pinned), model, document


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/agent-sessions')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Agent session storage is not configured.')
        return store

    @router.get('/options')
    def options(request: Request, kind: Literal['revision'] = Query(...), document_id: UUID = Query(...)) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            document = target_document(conn, project, document_id)
            subject, _ = request_actor(request)  # read-only: a GET does not create the principal
            actor = conn.execute('SELECT id FROM ka.principals WHERE issuer=%s AND subject=%s', (db.issuer, subject)).fetchone()
            actor = actor['id'] if actor else None
            models = [{'id': row['id'], 'name': row['name']} for row in conn.execute(
                'SELECT id,name,provider,api_format,model FROM ka.llm_models WHERE enabled ORDER BY lower(name),id').fetchall()
                if model_fits(row)]
            last = conn.execute('''SELECT model_id FROM ka.agent_sessions WHERE target_document_id=%s AND actor_id=%s
                ORDER BY created_at DESC LIMIT 1''', (document_id, actor)).fetchone()
            ids = [item['id'] for item in models]
            default_model = last['model_id'] if last and last['model_id'] in ids else (ids[0] if ids else None)
            prompts = conn.execute('''SELECT p.id,p.name,v.content FROM ka.documentation_prompts p
                JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
                WHERE p.project_id=%s AND p.enabled AND p.tags @> ARRAY[%s]::text[] ORDER BY lower(p.name),p.id''',
                (project, REVISION_TAG)).fetchall()

            def listed(query: str, linked_ids) -> list[dict]:
                linked = {str(item) for item in linked_ids or []}
                return [{'id': row['id'], 'name': row['name'], 'linked': str(row['id']) in linked}
                        for row in conn.execute(query, (project,)).fetchall()]
            servers = []
            for row in conn.execute('''SELECT id,name,enabled,ssh_allowed,credential_id FROM ka.servers WHERE project_id=%s
                    ORDER BY lower(name),id''', (project,)).fetchall():
                reason = server_reason(row)
                servers.append({'id': row['id'], 'name': row['name'], 'linked': str(row['id']) in {str(i) for i in document['server_ids'] or []},
                                'usable': reason is None, 'reason': reason})
            usable = {str(server['id']) for server in servers if server['usable']}

            def brings(items: list[dict], environments: bool) -> list[dict]:
                """Whether each service or environment brings a code project, and a server the run can use (as
                run_resources.resolve and build() narrow them), so the panel offers only tools Start accepts."""
                for entry in items:
                    found = bring(conn, project, [] if environments else [entry['id']], [entry['id']] if environments else [])
                    entry['brings_code'] = bool(found['code_project_ids'])
                    entry['brings_servers'] = any(item in usable for item in found['server_ids'])
                return items
            resources = {
                'servers': servers,
                'services': brings(listed('SELECT id,name FROM ka.services WHERE project_id=%s ORDER BY lower(name),id',
                                          document['service_ids']), False),
                'environments': brings(listed('SELECT id,name FROM ka.environments WHERE project_id=%s ORDER BY lower(name),id',
                                              document['environment_ids']), True),
                'code_projects': listed('''SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL
                    ORDER BY lower(name),id''', document['code_project_ids']),
                'collections': listed('''SELECT id,name FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL
                    ORDER BY lower(name),id''', []),
            }
            experts = [{'id': entry['id'], 'name': entry['name'], 'when_to_use': entry['when_to_use']} for entry in offered(conn, None)]
        return jsonable_encoder({
            'models': models, 'default_model_id': default_model, 'prompts': prompts,
            'default_prompt': {'id': None, 'name': 'Built-in', 'content': DEFAULT_REVISION_PROMPT},
            'resources': resources,
            'tools': [{'name': spec.name, 'label': spec.label, 'summary': spec.summary, 'group': spec.group,
                       'needs': spec.needs, 'requires': REQUIRES.get(spec.needs) if spec.needs else None} for spec in choosable()],
            'fixed_tools': [{'name': name, 'label': CATALOG[name].label} for name in FIXED_TOOLS],
            'experts': experts})

    @router.post('/preview')
    def preview(value: StartInput) -> dict:
        db = database()
        with db.connect() as conn:
            pinned, _, _ = build(conn, db.project(conn, app_name), value, None)
        return {'config': pinned, 'first_message': value.instruction}

    @router.post('', status_code=201)
    def start(value: StartInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            session_id = uuid4()
            pinned, model, document = build(conn, project, value, session_id)
            title = value.instruction.splitlines()[0][:TITLE_LENGTH]
            snapshot = {'system_prompt': value.system_prompt, 'prompt_id': value.prompt_id}
            try:
                conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,purpose,
                    target_document_id,prompt_snapshot,context_snapshot,agent_config,run_resources)
                    VALUES (%s,%s,NULL,%s,%s,%s,'revision',%s,%s,%s,%s,%s)""",
                    (session_id, project, actor, model['id'], title, document['id'], Jsonb(jsonable_encoder(snapshot)),
                     Jsonb({'document_id': str(document['id'])}), Jsonb(pinned), Jsonb(pinned['run_resources'])))
            except errors.ForeignKeyViolation as exc:  # the document was deleted after build() read it
                if exc.diag.constraint_name != DOCUMENT_FOREIGN_KEY:
                    raise
                raise HTTPException(404, DOCUMENT_NOT_FOUND) from None
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                         (session_id, actor, value.instruction))
            tools_off = sorted(item['tool'] for item in pinned['tools']['items']
                               if item['tool'] in CATALOG and item['mode'] != 'on' and CATALOG[item['tool']].needs not in RUN_ONLY_NEEDS)
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'agent_run.created','agent_session',%s,%s)''', (project, actor, session_id, Jsonb(jsonable_encoder({
                    'kind': value.kind, 'document_id': document['id'], 'model_id': model['id'], 'prompt_id': value.prompt_id,
                    'tools_off': tools_off, 'expert_ids': value.expert_ids, 'subagents': value.subagents}))))
            return session_detail(conn, project, session_id)

    @router.get('')
    def sessions(document_id: UUID = Query(...)) -> dict:
        db = database()
        with db.connect() as conn:
            return {'sessions': conn.execute(RUNS, (db.project(conn, app_name), document_id)).fetchall()}

    return router
