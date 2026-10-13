"""Starting a session: the options a start panel needs, Preview (exactly what Start pins) and Start. One request shape for
every ad-hoc run; where it is started from is a label (started_from), and what it may use is pinned in its config. Saved
agents (agent_id) join in part 2."""
import hashlib
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_admin.knowledge_agents.config import load_model, model_fits
from knowledge_admin.knowledge_agents.ephemeral import ad_hoc_config
from knowledge_admin.knowledge_agents.experts import offered
from knowledge_admin.knowledge_agents.run_resources import RunResourceError, bring
from knowledge_admin.sessions.rows import NO_MODEL, Selection
from knowledge_admin.tools.catalog import CATALOG, DOCUMENT_EDIT_TOOLS, SUBAGENT, needs_met

REVISION_TAG = 'document-revision'
DOCUMENT_NOT_FOUND = 'Document not found'
DOCUMENT_FOREIGN_KEY = 'agent_sessions_target_document'
UNKNOWN_TOOL = 'Unknown tool: {names}.'
NEEDS = '{tool} needs {what}.'
REQUIRES = {'files': 'a code project or a collection', 'code': 'a code project', 'code_graph': 'a code project',
            'documents': 'a collection', 'ssh': 'at least one server', 'editable_document': 'a document to edit'}
NO_EXPERT = 'This expert is not available: {id}.'
NOT_SET_UP = 'SSH is not set up for this server.'
NOT_ALLOWED = 'SSH is not allowed for this server.'
NO_SAVED_AGENTS = 'Saved agents start from their Test tab for now.'
TITLE_LENGTH = 120


class DocumentAccess(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    editable: bool = False


class Resources(Selection):
    documents: list[DocumentAccess] = Field(default_factory=list, max_length=20)


class StartedFrom(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: UUID


class StartInput(BaseModel):
    """What a start panel sends: the first message, the model and prompt, facts from where it starts (context), what
    the run may use (resources, tools, experts, subagents) and where it was started from (a label for lists)."""
    model_config = ConfigDict(extra='forbid')
    agent_id: str | None = None
    started_from: StartedFrom
    message: str = Field(max_length=20000)
    model_id: UUID
    prompt_id: UUID | None = None
    system_prompt: str = Field(max_length=20000)
    context: str = Field(default='', max_length=20000)
    resources: Resources = Field(default_factory=Resources)
    tools: list[str] = Field(default_factory=list, max_length=100)
    expert_ids: list[UUID] = Field(default_factory=list, max_length=50)
    subagents: bool = False

    @model_validator(mode='after')
    def valid(self):
        for name in ('message', 'system_prompt', 'context'):
            value = getattr(self, name)
            if '\x00' in value:
                raise ValueError('Text cannot contain null characters')
            setattr(self, name, value.strip())
        if not self.message:
            raise ValueError('Enter a message')
        if not self.system_prompt:
            raise ValueError('Enter a system prompt')
        self.tools = list(dict.fromkeys(self.tools))
        self.expert_ids = list(dict.fromkeys(self.expert_ids))
        merged: dict[UUID, bool] = {}
        for entry in self.resources.documents:  # one entry per document; editable wins
            merged[entry.id] = merged.get(entry.id, False) or entry.editable
        self.resources.documents = [DocumentAccess(id=key, editable=value) for key, value in merged.items()]
        return self


def choosable() -> list:
    """The tools a person can switch on for a run: not discovery-only, not "Answer only from documents", not
    run_subagent (its own switch)."""
    return [spec for spec in CATALOG.values() if spec.needs not in ('discovery', 'grounded') and spec.name != SUBAGENT]


def revise_prompt_id(project: UUID) -> UUID:
    """The seeded "Revise a document" prompt's id (migration 0100: md5(...)::uuid)."""
    return UUID(hashlib.md5(f'knowledge-agent-v3:prompts:document-revision:{project}'.encode()).hexdigest())


def server_reason(row: dict) -> str | None:
    if not row['enabled'] or row['credential_id'] is None:
        return NOT_SET_UP
    if not row['ssh_allowed']:
        return NOT_ALLOWED
    return None


def target_document(conn, project: UUID, document_id: UUID) -> dict:
    row = conn.execute('''SELECT d.id,d.title,d.code_project_ids,d.service_ids,d.server_ids,d.environment_ids
        FROM ka.document_details d WHERE d.id=%s AND d.project_id=%s''', (document_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return row


def document_access(conn, project: UUID, entries: list[DocumentAccess]) -> list[dict]:
    """The documents the run may read (and edit, when editable), with their current titles."""
    access = []
    for entry in entries:
        row = conn.execute('''SELECT d.id,v.title FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id
            WHERE d.id=%s AND d.project_id=%s''', (entry.id, project)).fetchone()
        if row is None:
            raise HTTPException(404, DOCUMENT_NOT_FOUND)
        access.append({'id': str(row['id']), 'title': row['title'], 'editable': entry.editable})
    return access


def build(conn, project: UUID, value: StartInput, session_id: UUID | None) -> tuple[dict, dict, dict]:
    """(pinned configuration, model, started-from document): preview and start share it, so they cannot differ."""
    if value.agent_id is not None:
        raise HTTPException(400, NO_SAVED_AGENTS)
    target = conn.execute('SELECT id FROM ka.documents WHERE id=%s AND project_id=%s',
                          (value.started_from.document_id, project)).fetchone()
    if target is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    model = load_model(conn, value.model_id)
    if model is None or not model['enabled'] or not model_fits(model):
        raise HTTPException(400, NO_MODEL)
    for row in conn.execute('SELECT id,name,enabled,ssh_allowed,credential_id FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
                            (project, value.resources.server_ids)).fetchall():
        reason = server_reason(row)
        if reason:
            raise HTTPException(400, f"Server {row['name']}: {reason}")
    names = {spec.name: spec for spec in choosable()}
    unknown = [name for name in value.tools if name not in names]
    if unknown:
        raise HTTPException(400, UNKNOWN_TOOL.format(names=', '.join(unknown)))
    system_prompt = value.system_prompt + (f'\n\n# Context\n\n{value.context}' if value.context else '')
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
    pinned['document_access'] = document_access(conn, project, value.resources.documents)
    unmet = [NEEDS.format(tool=name, what=REQUIRES[names[name].needs]) for name in value.tools
             if not needs_met(names[name].needs, pinned, None)]
    if unmet:
        raise HTTPException(400, ' '.join(unmet))
    chosen = set(value.tools)
    for item in pinned['tools']['items']:
        if item['tool'] == SUBAGENT:
            item['mode'] = 'on' if value.subagents else 'off'
        elif item['tool'] in CATALOG:
            item['mode'] = 'on' if item['tool'] in chosen else 'off'
            if item['tool'] in DOCUMENT_EDIT_TOOLS:
                item['subagent_allowed'] = False
    experts = {entry['id']: entry for entry in pinned['experts']}
    for expert_id in value.expert_ids:
        if str(expert_id) not in experts:
            raise HTTPException(400, NO_EXPERT.format(id=expert_id))
    pinned['experts'] = [experts[str(expert_id)] for expert_id in value.expert_ids]
    pinned['project_id'] = str(project)
    return jsonable_encoder(pinned), model, target


def options(conn, project: UUID, actor: UUID | None, document_id: UUID | None) -> dict:
    """What a start panel offers: models (the last one used first), prompts tagged for revision, resources (marked
    linked to the starting document), the choosable tools and experts."""
    document = target_document(conn, project, document_id) if document_id is not None else None

    def links(key: str) -> list:
        return (document[key] or []) if document else []
    models = [{'id': row['id'], 'name': row['name']} for row in conn.execute(
        'SELECT id,name,provider,api_format,model FROM ka.llm_models WHERE enabled ORDER BY lower(name),id').fetchall()
        if model_fits(row)]
    if document_id is not None:
        last = conn.execute('''SELECT model_id FROM ka.agent_sessions WHERE target_document_id=%s AND actor_id=%s
            ORDER BY created_at DESC LIMIT 1''', (document_id, actor)).fetchone()
    else:
        last = conn.execute('''SELECT model_id FROM ka.agent_sessions WHERE agent_id IS NULL AND actor_id=%s
            ORDER BY created_at DESC LIMIT 1''', (actor,)).fetchone()
    ids = [item['id'] for item in models]
    default_model = last['model_id'] if last and last['model_id'] in ids else (ids[0] if ids else None)
    prompts = conn.execute('''SELECT p.id,p.name,v.content FROM ka.documentation_prompts p
        JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
        WHERE p.project_id=%s AND p.enabled AND p.tags @> ARRAY[%s]::text[] ORDER BY lower(p.name),p.id''',
        (project, REVISION_TAG)).fetchall()
    seeded = revise_prompt_id(project)
    prompt_choice = seeded if any(row['id'] == seeded for row in prompts) else (prompts[0]['id'] if prompts else None)

    def listed(query: str, linked_ids) -> list[dict]:
        linked = {str(item) for item in linked_ids}
        return [{'id': row['id'], 'name': row['name'], 'linked': str(row['id']) in linked}
                for row in conn.execute(query, (project,)).fetchall()]
    servers = []
    linked_servers = {str(i) for i in links('server_ids')}
    for row in conn.execute('''SELECT id,name,enabled,ssh_allowed,credential_id FROM ka.servers WHERE project_id=%s
            ORDER BY lower(name),id''', (project,)).fetchall():
        reason = server_reason(row)
        servers.append({'id': row['id'], 'name': row['name'], 'linked': str(row['id']) in linked_servers,
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
                                  links('service_ids')), False),
        'environments': brings(listed('SELECT id,name FROM ka.environments WHERE project_id=%s ORDER BY lower(name),id',
                                      links('environment_ids')), True),
        'code_projects': listed('''SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL
            ORDER BY lower(name),id''', links('code_project_ids')),
        'collections': listed('''SELECT id,name FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL
            ORDER BY lower(name),id''', []),
        'documents': [{'id': document['id'], 'name': document['title'], 'linked': True, 'editable': True}] if document else [],
    }
    experts = [{'id': entry['id'], 'name': entry['name'], 'when_to_use': entry['when_to_use']} for entry in offered(conn, None)]
    return jsonable_encoder({
        'models': models, 'default_model_id': default_model, 'prompts': prompts, 'default_prompt_id': prompt_choice,
        'resources': resources,
        'tools': [{'name': spec.name, 'label': spec.label, 'summary': spec.summary, 'group': spec.group,
                   'needs': spec.needs, 'requires': REQUIRES.get(spec.needs) if spec.needs else None} for spec in choosable()],
        'experts': experts})
