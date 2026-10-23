"""Starting a session: the options a start panel needs, Preview (exactly what Start pins) and Start. One request shape for
every run; where it is started from is a label (started_from), and what it may use is pinned in its config. A saved agent
(agent_id) starts from its own configuration, which the request can only narrow (narrow)."""

import hashlib
import re
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg import errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_admin.data_root import documentation_folder
from knowledge_admin.servers.discovery import readiness, server_row
from knowledge_admin.documents.api import Tags, document_slug
from knowledge_admin.documents.store import INVENTORY_TAG, NAME_IN_USE

from knowledge_admin import access as access_rules
from knowledge_admin.knowledge_agents.config import (
    AD_HOC_AGENT,
    AD_HOC_NAME,
    entry as access_entry,
    get_agent_config,
    load_entries,
    load_model,
    model_fits,
)
from knowledge_admin.knowledge_agents.experts import offered, pin
from knowledge_admin.sessions.run_access import (
    agent_access,
    bring,
    built_for,
    check_picks,
    given,
    run_access,
)
from knowledge_admin.sessions.text import agent_system_prompt
from knowledge_admin.sessions.rows import NO_MODEL, Selection
from knowledge_admin.tools.selection import disabled_for, validate as valid_tools

REVISION_TAG = 'document-revision'
WRITE_TAG = 'documentation'
ASSISTANT_TAG = 'assistant'  # with the resource type's tag (server, service, environment): a chat prompt for it
ONE_START = 'Choose one starting point: a document, a new document, a server, a service or an environment.'
ID_OR_NEW = 'Give a document id or a new document.'
ONE_NEW = 'One new document per run.'
INVENTORY_FOR = 'An inventory document is written for one server, service or environment.'
RESOURCE_FOR = 'A new document is built for one server, service or environment.'
NOT_THE_START = 'The new document must be the one the run starts from.'
DOCUMENT_NOT_FOUND = 'Document not found'
DOCUMENT_FOREIGN_KEY = 'agent_sessions_target_document'
PICKS = (
    ('code_project', 'code_project_ids'),
    ('server', 'server_ids'),
    ('service', 'service_ids'),
    ('environment', 'environment_ids'),
)
# A chat on a resource: its tag (lists find its chats by it) and the table its id is in.
RESOURCE_TYPES = {'server': 'servers', 'service': 'services', 'environment': 'environments'}
RESOURCE_NOT_FOUND = {
    'server': 'Server not found.',
    'service': 'Service not found.',
    'environment': 'Environment not found.',
}
TAG = re.compile(r'^[a-z0-9_-]+(/[a-z0-9_-]+)*$')
TAG_LENGTH = 120
MAX_TAGS = 10
BAD_TAG = 'A tag is lowercase letters, digits, _ and -, in parts separated by /, at most 120 characters: {tag}'
TOO_MANY_TAGS = 'A run takes at most 10 tags.'
RESOURCE_TAG = 'A resource tag comes from where the run starts: {tag}'
NO_EXPERT = 'This expert is not available: {id}.'
OWN_MODEL = 'A saved agent uses its own model and prompt.'
NO_SSH = (
    'Server {name} is not given to this run: it has no usable SSH connection, so the agent cannot read it or run '
    'commands on it. {reason}'
)
AGENT_DISABLED = 'Agent is disabled.'
AGENT_NO_MODEL = 'Select an enabled model in this agent’s General tab.'
TITLE_LENGTH = 120


class NewDocument(BaseModel):
    """A document the run writes: its name (a slug comes from it), its documentation folder and its tags; with one
    server_id, service_id or environment_id it is built for that resource alone (a resource's New document page's
    Write with an agent); tagged inventory it must name one: the inventory document of that resource."""

    model_config = ConfigDict(extra='forbid')
    folder: str = Field(max_length=200)
    name: str = Field(max_length=240)
    tags: Tags = Field(default_factory=list)
    server_id: UUID | None = None
    service_id: UUID | None = None
    environment_id: UUID | None = None

    @model_validator(mode='after')
    def valid(self):
        if '\x00' in self.name:
            raise ValueError('Text cannot contain null characters')
        self.name = self.name.strip()
        if not document_slug(self.name):
            raise ValueError('Use letters or digits in the document name')
        self.folder = documentation_folder(self.folder)
        given = [kind for kind in RESOURCE_TYPES if getattr(self, f'{kind}_id')]
        if INVENTORY_TAG in self.tags and len(given) != 1:
            raise ValueError(INVENTORY_FOR)
        if len(given) > 1:
            raise ValueError(RESOURCE_FOR)
        return self

    def resource_for(self) -> tuple[str, UUID] | None:
        """(type, id) of the one resource the document is built for; None when it names none."""
        given = [(kind, getattr(self, f'{kind}_id')) for kind in RESOURCE_TYPES if getattr(self, f'{kind}_id')]
        return given[0] if given else None


class DocumentAccess(BaseModel):
    """A document the run may use (read and draft), or a new document it writes."""

    model_config = ConfigDict(extra='forbid')
    id: UUID | None = None
    new: NewDocument | None = None

    @model_validator(mode='after')
    def valid(self):
        if (self.id is None) == (self.new is None):
            raise ValueError(ID_OR_NEW)
        return self


class Resources(Selection):
    documents: list[DocumentAccess] = Field(default_factory=list, max_length=20)


class StartedFrom(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: UUID | None = None
    new_document: NewDocument | None = None
    server_id: UUID | None = None
    service_id: UUID | None = None
    environment_id: UUID | None = None

    @model_validator(mode='after')
    def valid(self):
        starts = (self.document_id, self.new_document, self.server_id, self.service_id, self.environment_id)
        if sum(item is not None for item in starts) != 1:
            raise ValueError(ONE_START)
        return self

    def resource(self) -> tuple[str, UUID] | None:
        """(type, id) of the server, service or environment the run starts from; None for a document start."""
        for kind in RESOURCE_TYPES:
            value = getattr(self, f'{kind}_id')
            if value is not None:
                return kind, value
        return None


CHAT_PURPOSE = 'Only a saved agent chat takes a purpose.'


def resource_tag(kind: str, resource_id) -> str:
    """The tag of a run started from a resource: servers/<id>, services/<id> or environments/<id>."""
    return f'{RESOURCE_TYPES[kind]}/{resource_id}'


class StartInput(BaseModel):
    """What a start panel sends: the first message, the model and prompt, facts from where it starts (context), what
    the run may use (resources, experts; every catalog tool but those it switches off, disabled_tools; every run may
    draft documents and inventory within its access) and where it was started from (a label for lists). A saved agent's
    run is a Test session unless purpose is chat (Run agent on the Agents page)."""

    model_config = ConfigDict(extra='forbid')
    agent_id: str | None = None
    started_from: StartedFrom | None = None
    purpose: Literal['chat'] | None = None
    message: str = Field(max_length=20000)
    model_id: UUID | None = None
    prompt_id: UUID | None = None
    system_prompt: str = Field(default='', max_length=20000)
    context: str = Field(default='', max_length=20000)
    resources: Resources = Field(default_factory=Resources)
    expert_ids: list[UUID] | None = Field(default=None, max_length=50)  # None: every expert offered
    disabled_tools: list[str] = Field(default_factory=list, max_length=200)  # catalog tools switched off (more only)
    tags: list[str] = Field(default_factory=list, max_length=100)  # at most MAX_TAGS with the resource tag (valid)

    @model_validator(mode='after')
    def valid(self):
        for name in ('message', 'system_prompt', 'context'):
            value = getattr(self, name)
            if '\x00' in value:
                raise ValueError('Text cannot contain null characters')
            setattr(self, name, value.strip())
        if not self.message:
            raise ValueError('Enter a message')
        if self.purpose is not None and (self.agent_id is None or self.started_from is not None):
            raise ValueError(CHAT_PURPOSE)
        if self.agent_id is None:
            if self.started_from is None:
                raise ValueError('Choose what the agent starts from.')
            if self.model_id is None:
                raise ValueError('Choose a model.')
            if not self.system_prompt:
                raise ValueError('Enter a system prompt')
        if self.expert_ids is not None:
            self.expert_ids = list(dict.fromkeys(self.expert_ids))
        self.tags = list(dict.fromkeys(tag.strip() for tag in self.tags))
        own = self.resource_tag()
        for tag in self.tags:
            if len(tag) > TAG_LENGTH or not TAG.match(tag):
                raise ValueError(BAD_TAG.format(tag=tag[:TAG_LENGTH]))
            if tag != own and tag.split('/', 1)[0] in RESOURCE_TYPES.values():
                raise ValueError(RESOURCE_TAG.format(tag=tag))
        if len(self.run_tags()) > MAX_TAGS:
            raise ValueError(TOO_MANY_TAGS)
        new = [entry for entry in self.resources.documents if entry.new is not None]
        if len(new) > 1:
            raise ValueError(ONE_NEW)
        given = dict.fromkeys(entry.id for entry in self.resources.documents if entry.id is not None)  # each once
        self.resources.documents = [DocumentAccess(id=key) for key in given] + new
        return self

    def resource(self) -> tuple[str, UUID] | None:
        """The resource an ad-hoc run starts from (a saved agent starts from its own configuration)."""
        if self.agent_id is not None or self.started_from is None:
            return None
        return self.started_from.resource()

    def resource_tag(self) -> str | None:
        found = self.resource()
        return resource_tag(*found) if found else None

    def run_tags(self) -> list[str]:
        """The run's tags: the request's and the tag of the resource it starts from."""
        own = self.resource_tag()
        return [*self.tags, *([own] if own and own not in self.tags else [])]


def revise_prompt_id(project: UUID) -> UUID:
    """The seeded "Revise a document" prompt's id (migration 0100: md5(...)::uuid)."""
    return UUID(hashlib.md5(f'knowledge-agent-v3:prompts:document-revision:{project}'.encode()).hexdigest())


def write_prompt_id(project: UUID) -> UUID:
    """The seeded "Write a document" prompt's id (md5(...)::uuid, as its migration seeds it)."""
    return UUID(hashlib.md5(f'knowledge-agent-v3:prompts:write-document:{project}'.encode()).hexdigest())


def assistant_prompt_id(project: UUID, kind: str) -> UUID:
    """The seeded "<Type> assistant" prompt's id of a resource type (migration 0106: md5(...)::uuid)."""
    return UUID(hashlib.md5(f'knowledge-agent-v3:prompts:{kind}-assistant:{project}'.encode()).hexdigest())


def start_server(conn, project: UUID, server_id: UUID) -> dict:
    """The server a run starts from: it exists in this workspace and SSH is set up and allowed for it."""
    server = server_row(conn, project, server_id, lock=True)
    reason = readiness(server)
    if reason:
        raise HTTPException(400, reason)
    return server


def start_resource(conn, project: UUID, kind: str, resource_id: UUID) -> dict:
    """The service or environment a run starts from: it exists in this workspace. No SSH is needed: a server it brings
    that the run cannot connect to is listed and read, and SSH skips it (inventory and documents still work)."""
    table = 'ka.services' if kind == 'service' else 'ka.environments'
    row = conn.execute(
        f'SELECT id,name FROM {table} WHERE id=%s AND project_id=%s FOR SHARE', (resource_id, project)
    ).fetchone()
    if row is None:
        raise HTTPException(404, RESOURCE_NOT_FOUND[kind])
    return row


def resource_owner(conn, project: UUID, kind: str, resource_id: UUID) -> None:
    """The resource a new-document run's document is built for: it exists in this workspace (404)."""
    if not conn.execute(
        f'SELECT 1 FROM ka.{RESOURCE_TYPES[kind]} WHERE id=%s AND project_id=%s', (resource_id, project)
    ).fetchone():
        raise HTTPException(404, RESOURCE_NOT_FOUND[kind])


def server_reason(row: dict) -> str | None:
    """Why a run cannot use this server row over SSH (access.ssh_reason; its credential stands for its key)."""
    return access_rules.ssh_reason({**row, 'private_key_ref': row['credential_id']})


def target_document(conn, project: UUID, document_id: UUID) -> dict:
    row = conn.execute(
        """SELECT d.id,d.title,d.code_project_ids,d.service_ids,d.server_ids,d.environment_ids
        FROM ka.document_details d WHERE d.id=%s AND d.project_id=%s""",
        (document_id, project),
    ).fetchone()
    if row is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return row


def document_access(conn, project: UUID, entries: list[DocumentAccess], built: list[list[str]]) -> list[dict]:
    """The access entries of the documents the run may use, with their current titles, and
    of the new document it may write (an id of its own, 'new:<uuid>', its name, folder, slug, tags and what it is
    built for: built). A brand-new draft already holding the new
    document's folder and slug refuses the run (409): every save would fail. A live document of that name does not:
    the run drafts that document (an inventory document's run is refused: it never drafts another document)."""
    access = []
    for entry in entries:
        if entry.new is not None:
            slug = document_slug(entry.new.name)
            # an inventory document never drafts the live document of that name: it is refused like a brand-new draft
            if conn.execute(
                """SELECT 1 FROM ka.documents WHERE project_id=%s
                AND (draft_of IS NULL AND is_draft OR %s AND NOT is_draft)
                AND folder IS NOT DISTINCT FROM %s AND slug=%s""",
                (project, INVENTORY_TAG in entry.new.tags, entry.new.folder, slug),
            ).fetchone():
                raise HTTPException(409, NAME_IN_USE.format(slug=slug))
            access.append(
                access_entry(
                    'document',
                    {'id': f'new:{uuid4()}', 'name': entry.new.name},
                    new=True,
                    folder=entry.new.folder,
                    slug=slug,
                    tags=entry.new.tags,
                    built_for=built,
                )
            )
            continue
        row = conn.execute(
            'SELECT id,title FROM ka.live_documents WHERE id=%s AND project_id=%s',
            (entry.id, project),
        ).fetchone()
        if row is None:
            raise HTTPException(404, DOCUMENT_NOT_FOUND)
        access.append(access_entry('document', {'id': row['id'], 'name': row['title']}))
    return access


def narrow(conn, project: UUID, agent_id: str, value: StartInput, conninfo: str) -> tuple[dict, dict]:
    """A saved agent's configuration for one run, narrowed by the request and never widened: its access object or the
    resources the request picked out of it (access.narrow: a pick outside it is refused), a subset of its experts, its
    off tools and the request's (never one back on: disabled_for). Its model and system prompt are its own;
    its access object is the run's (agent_access)."""
    try:
        config = get_agent_config(agent_id, conninfo=conninfo)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    model = config['model']
    if (
        (value.model_id is not None and (model is None or str(value.model_id) != str(model['id'])))
        or (value.system_prompt and value.system_prompt != config['system_prompt']['content'])
        or value.prompt_id is not None
    ):
        raise HTTPException(400, OWN_MODEL)
    if not config['enabled']:
        raise HTTPException(409, AGENT_DISABLED)
    if model is None or not model['enabled'] or not model_fits(model):
        raise HTTPException(409, AGENT_NO_MODEL)
    resources = value.resources
    picked = [{'type': kind, 'id': str(i)} for kind, key in PICKS for i in getattr(resources, key)]
    picked += [{'type': 'document', 'id': str(entry.id or 'new')} for entry in resources.documents]
    try:
        access = agent_access(
            conn,
            project,
            config,
            picked or None,  # nothing picked: everything it has
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    pinned = pin(conn, config, agent_id)
    pinned['experts'] = chosen_experts(pinned['experts'], value.expert_ids)
    if value.context:
        content = pinned['system_prompt']['content'] + f'\n\n# Context\n\n{value.context}'
        pinned['system_prompt'] = {**pinned['system_prompt'], 'content': content}
    pinned['project_id'] = str(project)
    pinned['access'] = access
    pinned['disabled_tools'] = sorted(disabled_for(config['disabled_tools'], off_tools(value)))
    return jsonable_encoder(pinned), model


def off_tools(value: StartInput) -> list[str]:
    """The catalog tools the request switches off (400: a name that is not a catalog tool)."""
    try:
        return valid_tools(value.disabled_tools)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def chosen_experts(offered: list[dict], expert_ids: list[UUID] | None) -> list[dict]:
    """The experts a run may call: every offered one, or those the request names (each must be offered)."""
    if expert_ids is None:
        return offered
    experts = {entry['id']: entry for entry in offered}
    for expert_id in expert_ids:
        if str(expert_id) not in experts:
            raise HTTPException(400, NO_EXPERT.format(id=expert_id))
    return [experts[str(expert_id)] for expert_id in expert_ids]


def _with(selection: Resources, kind: str, resource_id) -> Resources:
    """The selection with this server, service or environment picked too."""
    key = f'{kind}_ids'
    ids = getattr(selection, key)
    if str(resource_id) in {str(item) for item in ids}:
        return selection
    return selection.model_copy(update={key: [*ids, resource_id]})


def build(
    conn, project: UUID, value: StartInput, session_id: UUID | None, conninfo: str
) -> tuple[dict, dict, dict | None]:
    """(pinned configuration, model, where it starts: the document or the resource; None for a saved agent or a new
    document): preview and start share it, so they pin the same configuration except the random `new:` id of a new
    document. A saved agent's run narrows its agent's access (narrow); an ad-hoc run (the user's model and prompt, no
    agents row) holds what it was picked, each checked (check_picks), and is built the same way (run_access): what it
    was given brings its children. A run started from a resource records it as `started_from` ({type, id, name}) and
    is given it (a server run only with SSH to it); the resource an inventory document is about is given to its run
    too (a server only with SSH to it: else the run starts without it and says so)."""
    if value.agent_id is not None:
        pinned, model = narrow(conn, project, value.agent_id, value, conninfo)
        return pinned, model, None
    origin = None
    selection = value.resources
    resource = value.resource()
    if resource is not None:
        kind, resource_id = resource
        origin = (
            start_server(conn, project, resource_id) if kind == 'server' else start_resource(conn, project, *resource)
        )
        selection = _with(selection, kind, origin['id'])
    started = value.started_from.new_document
    entries = list(value.resources.documents)
    new = next((entry.new for entry in entries if entry.new is not None), None)
    if new is not None and (started is None or new.model_dump() != started.model_dump()):
        raise HTTPException(400, NOT_THE_START)
    if started is not None and new is None:  # the new document it starts from is a resource it may write
        entries.append(DocumentAccess(new=started))
    owner = started.resource_for() if started is not None else None
    notes = []
    if owner is not None:  # the resource it writes about is read as a chat on it reads it
        resource_owner(conn, project, *owner)
        kind, resource_id = owner
        found = server_row(conn, project, resource_id, lock=True) if kind == 'server' else None
        reason = readiness(found) if found is not None else None
        if reason:  # a run is given a server only with SSH to it: it starts without it
            notes.append(NO_SSH.format(name=found['name'], reason=reason))
        else:
            selection = _with(selection, kind, resource_id)
    target = None
    if value.started_from.document_id is not None:
        target = conn.execute(
            'SELECT id FROM ka.live_documents WHERE id=%s AND project_id=%s', (value.started_from.document_id, project)
        ).fetchone()
        if target is None:
            raise HTTPException(404, DOCUMENT_NOT_FOUND)
    model = load_model(conn, value.model_id)
    if model is None or not model['enabled'] or not model_fits(model):
        raise HTTPException(400, NO_MODEL)
    for row in conn.execute(
        'SELECT id,name,enabled,ssh_allowed,credential_id FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
        (project, selection.server_ids),
    ).fetchall():
        reason = server_reason(row)
        if reason:
            raise HTTPException(400, f'Server {row["name"]}: {reason}')
    picked = [{'type': kind, 'id': str(i)} for kind, key in PICKS for i in getattr(selection, key)]
    try:
        check_picks(conn, project, picked)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    # a new document is built for what the person chose; one naming a resource (an inventory document) for it alone
    documents = document_access(
        conn, project, entries, [[owner[0], str(owner[1])]] if owner is not None else built_for(picked)
    )
    # a resource run's resource is named in the instructions (runner.get_prompt), not in its context
    system_prompt = value.system_prompt + (f'\n\n# Context\n\n{value.context}' if value.context else '')
    for entry in documents:
        if entry.get('new'):  # a document run names its document in its context; a new document's id is made here
            context = f'Document: {entry["name"]} (id {entry["id"]})'
            system_prompt += f'\n\n{context}' if '\n# Context\n' in system_prompt else f'\n\n# Context\n\n{context}'
    pinned = pin(
        conn,
        {
            'id': AD_HOC_AGENT,
            'name': AD_HOC_NAME,
            'description': '',
            'when_to_use': '',
            'enabled': True,
            'revision': 0,
            'model': model,
            'system_prompt': {'content': system_prompt},
            'instructions': '',
            'settings_revision': None,
        },
        None,
    )
    if resource is not None:
        pinned['started_from'] = {'type': resource[0], 'id': str(origin['id']), 'name': origin['name']}
    if notes:
        pinned['start_notes'] = notes
    pinned['experts'] = chosen_experts(pinned['experts'], value.expert_ids)
    pinned['project_id'] = str(project)
    pinned['disabled_tools'] = sorted(disabled_for((), off_tools(value)))
    # what the run holds: what it was picked, loaded, and its documents
    ids = {type_: [pick['id'] for pick in picked if pick['type'] == type_] for type_, _ in PICKS}
    holds = load_entries(conn, project, ids, documents)
    try:
        access = run_access(
            conn,
            project,
            holds,
            [*picked, *({'type': 'document', 'id': entry['id']} for entry in documents)],
            own=owner or resource,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    pinned = jsonable_encoder(pinned)
    pinned['access'] = jsonable_encoder(access)
    return pinned, model, origin if origin is not None else target


def start_run(
    conn,
    project: UUID,
    actor: UUID,
    value: StartInput,
    conninfo: str,
    *,
    purpose: str | None = None,
    event_id: int | None = None,
    handler_id: UUID | None = None,
    title: str | None = None,
) -> UUID:
    """Start a run: build its configuration, insert the session (purpose by where it starts: a document → revision,
    a new document → documents, a server, service or environment → resource (tagged with it; server_id for a server),
    a saved agent → test or the request's purpose (chat), or `purpose` for a saved agent, as an event handler starts it
    with its event and handler),
    its tags (the request's and its resource's), its first input, and audit it. POST /api/sessions and the
    event processor share it. The title is the message's first line unless one is given (an event run's names its event)."""
    if value.agent_id is None and (purpose, event_id, handler_id, title) != (None, None, None, None):
        raise ValueError('Only a saved agent run takes a purpose, an event, a handler or a title.')
    session_id = uuid4()
    pinned, model, origin = build(conn, project, value, session_id, conninfo)
    title = (title or value.message.splitlines()[0])[:TITLE_LENGTH]
    resource = value.resource()
    tags = value.run_tags()
    if value.agent_id is not None:
        snapshot = {'system_prompt': agent_system_prompt(pinned), 'context': value.context}
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,purpose,tags,
            target_document_id,prompt_snapshot,context_snapshot,agent_config,event_id,handler_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,'{}',%s,%s,%s)""",
            (
                session_id,
                project,
                value.agent_id,
                actor,
                model['id'],
                title,
                purpose or value.purpose or 'test',
                tags,
                Jsonb(snapshot),
                Jsonb(pinned),
                event_id,
                handler_id,
            ),
        )
        details = {
            'agent_id': value.agent_id,
            'model_id': model['id'],
            'expert_ids': [entry['id'] for entry in pinned['experts']],
            'resources': [{'type': item['type'], 'id': item['id']} for item in given(pinned['access'])],
            'tags': tags,
        }
    elif resource is not None:
        kind, resource_id = resource
        snapshot = {
            'system_prompt': value.system_prompt,
            'context': value.context,
            'prompt_id': value.prompt_id,
        }
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,purpose,tags,
            server_id,target_document_id,prompt_snapshot,context_snapshot,agent_config)
            VALUES (%s,%s,NULL,%s,%s,%s,'resource',%s,%s,NULL,%s,%s,%s)""",
            (
                session_id,
                project,
                actor,
                model['id'],
                title,
                tags,
                resource_id if kind == 'server' else None,
                Jsonb(jsonable_encoder(snapshot)),
                Jsonb({f'{kind}_id': str(resource_id)}),
                Jsonb(pinned),
            ),
        )
        details = {
            f'{kind}_id': resource_id,
            'tags': tags,
            'model_id': model['id'],
            'prompt_id': value.prompt_id,
            'expert_ids': [entry['id'] for entry in pinned['experts']],
        }
    elif value.started_from.new_document is not None:
        new = value.started_from.new_document.model_dump(mode='json', exclude_defaults=True)
        snapshot = {'system_prompt': value.system_prompt, 'context': value.context, 'prompt_id': value.prompt_id}
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,purpose,tags,
            target_document_id,folder,document_name,prompt_snapshot,context_snapshot,agent_config)
            VALUES (%s,%s,NULL,%s,%s,%s,'documents',%s,NULL,%s,%s,%s,%s,%s)""",
            (
                session_id,
                project,
                actor,
                model['id'],
                title,
                tags,
                new['folder'],
                new['name'],
                Jsonb(jsonable_encoder(snapshot)),
                Jsonb({'new_document': new}),
                Jsonb(pinned),
            ),
        )
        details = {
            'new_document': new,
            'tags': tags,
            'model_id': model['id'],
            'prompt_id': value.prompt_id,
            'expert_ids': [entry['id'] for entry in pinned['experts']],
        }
    else:
        document_id = value.started_from.document_id
        snapshot = {'system_prompt': value.system_prompt, 'context': value.context, 'prompt_id': value.prompt_id}
        try:
            conn.execute(
                """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,purpose,tags,
                target_document_id,prompt_snapshot,context_snapshot,agent_config)
                VALUES (%s,%s,NULL,%s,%s,%s,'revision',%s,%s,%s,%s,%s)""",
                (
                    session_id,
                    project,
                    actor,
                    model['id'],
                    title,
                    tags,
                    document_id,
                    Jsonb(jsonable_encoder(snapshot)),
                    Jsonb({'document_id': str(document_id)}),
                    Jsonb(pinned),
                ),
            )
        except errors.ForeignKeyViolation as exc:  # the document was deleted after build() read it
            if exc.diag.constraint_name != DOCUMENT_FOREIGN_KEY:
                raise
            raise HTTPException(404, DOCUMENT_NOT_FOUND) from None
        details = {
            'document_id': document_id,
            'tags': tags,
            'model_id': model['id'],
            'prompt_id': value.prompt_id,
            'expert_ids': [entry['id'] for entry in pinned['experts']],
        }
    conn.execute('UPDATE ka.agent_sessions SET access=%s WHERE id=%s', (Jsonb(pinned['access']), session_id))
    conn.execute(
        'INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
        (session_id, actor, value.message),
    )
    conn.execute(
        """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
        VALUES (%s,%s,'agent_run.created','agent_session',%s,%s)""",
        (project, actor, session_id, Jsonb(jsonable_encoder(details))),
    )
    return session_id


def options(
    conn,
    project: UUID,
    actor: UUID | None,
    document_id: UUID | None,
    new_document: bool = False,
    server_id: UUID | None = None,
    service_id: UUID | None = None,
    environment_id: UUID | None = None,
) -> dict:
    """What a start panel offers: models (the last one used first), prompts tagged for revision (for a new document,
    tagged documentation; for a server, service or environment, the prompts tagged assistant and with its type, its
    seeded assistant prompt the first choice), resources (marked linked to the starting document or resource) and
    experts."""
    document = target_document(conn, project, document_id) if document_id is not None else None
    server = server_row(conn, project, server_id) if server_id is not None else None
    started = (
        ('server', server_id)
        if server_id is not None
        else ('service', service_id)
        if service_id is not None
        else ('environment', environment_id)
        if environment_id is not None
        else None
    )
    kind = started[0] if started else None
    if kind in ('service', 'environment'):
        start_resource(conn, project, kind, started[1])  # 404 when it is not in this workspace

    def links(key: str) -> list:
        return (document[key] or []) if document else []

    models = [
        {'id': row['id'], 'name': row['name']}
        for row in conn.execute(
            'SELECT id,name,provider,api_format,model FROM ka.llm_models WHERE enabled ORDER BY lower(name),id'
        ).fetchall()
        if model_fits(row)
    ]
    if document_id is not None:
        last = conn.execute(
            """SELECT model_id FROM ka.agent_sessions WHERE target_document_id=%s AND actor_id=%s
            ORDER BY created_at DESC LIMIT 1""",
            (document_id, actor),
        ).fetchone()
    elif started is not None:
        last = conn.execute(
            """SELECT model_id FROM ka.agent_sessions WHERE tags @> ARRAY[%s]::text[] AND actor_id=%s
            ORDER BY created_at DESC LIMIT 1""",
            (resource_tag(*started), actor),
        ).fetchone()
    else:
        last = conn.execute(
            """SELECT model_id FROM ka.agent_sessions WHERE agent_id IS NULL AND actor_id=%s
            ORDER BY created_at DESC LIMIT 1""",
            (actor,),
        ).fetchone()
    ids = [item['id'] for item in models]
    default_model = last['model_id'] if last and last['model_id'] in ids else (ids[0] if ids else None)
    if kind is not None:
        prompts = conn.execute(
            """SELECT p.id,p.name,v.content FROM ka.documentation_prompts p
            JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
            WHERE p.project_id=%s AND p.enabled AND p.tags @> ARRAY[%s,%s]::text[] ORDER BY lower(p.name),p.id""",
            (project, ASSISTANT_TAG, kind),
        ).fetchall()
    else:
        prompts = conn.execute(
            """SELECT p.id,p.name,v.content FROM ka.documentation_prompts p
            JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
            WHERE p.project_id=%s AND p.enabled AND p.tags @> ARRAY[%s]::text[] ORDER BY lower(p.name),p.id""",
            (project, WRITE_TAG if new_document else REVISION_TAG),
        ).fetchall()
    seeded = (
        assistant_prompt_id(project, kind)
        if kind
        else write_prompt_id(project)
        if new_document
        else revise_prompt_id(project)
    )
    prompt_choice = seeded if any(row['id'] == seeded for row in prompts) else (prompts[0]['id'] if prompts else None)

    def listed(query: str, linked_ids) -> list[dict]:
        linked = {str(item) for item in linked_ids}
        return [
            {'id': row['id'], 'name': row['name'], 'linked': str(row['id']) in linked}
            for row in conn.execute(query, (project,)).fetchall()
        ]

    servers = []
    linked_servers = {str(i) for i in links('server_ids')} | ({str(server['id'])} if server else set())
    linked_services = {str(i) for i in links('service_ids')} | ({str(service_id)} if service_id else set())
    linked_environments = {str(i) for i in links('environment_ids')} | (
        {str(environment_id)} if environment_id else set()
    )
    for row in conn.execute(
        """SELECT id,name,enabled,ssh_allowed,credential_id FROM ka.servers WHERE project_id=%s
            ORDER BY lower(name),id""",
        (project,),
    ).fetchall():
        reason = readiness(row) if server and row['id'] == server['id'] else server_reason(row)
        servers.append(
            {
                'id': row['id'],
                'name': row['name'],
                'linked': str(row['id']) in linked_servers,
                'usable': reason is None,
                'reason': reason,
            }
        )
    usable = {str(server['id']) for server in servers if server['usable']}

    def brings(items: list[dict], environments: bool) -> list[dict]:
        """Whether each service or environment brings a code project, and a server the run can connect to, so the
        panel can say what each brings. The run holds every server it brings (run_access); one it cannot connect to
        is listed and read, and SSH skips it (access.ssh_reason)."""
        for entry in items:
            found = bring(conn, project, [] if environments else [entry['id']], [entry['id']] if environments else [])
            entry['brings_code'] = bool(found['code_project_ids'])
            entry['brings_servers'] = any(item in usable for item in found['server_ids'])
        return items

    resources = {
        'servers': servers,
        'services': brings(
            listed('SELECT id,name FROM ka.services WHERE project_id=%s ORDER BY lower(name),id', linked_services),
            False,
        ),
        'environments': brings(
            listed(
                'SELECT id,name FROM ka.environments WHERE project_id=%s ORDER BY lower(name),id',
                linked_environments,
            ),
            True,
        ),
        'code_projects': listed(
            """SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL
            ORDER BY lower(name),id""",
            links('code_project_ids'),
        ),
        'documents': [{'id': document['id'], 'name': document['title'], 'linked': True}] if document else [],
    }
    experts = [
        {'id': entry['id'], 'name': entry['name'], 'when_to_use': entry['when_to_use']} for entry in offered(conn, None)
    ]
    return jsonable_encoder(
        {
            'models': models,
            'default_model_id': default_model,
            'prompts': prompts,
            'default_prompt_id': prompt_choice,
            'resources': resources,
            'experts': experts,
        }
    )


def agent_options(conn, project: UUID, agent_id: str, conninfo: str) -> dict:
    """What a start panel offers for a saved agent: only what the request may narrow. Its own model and prompt (no
    choice), what its access object holds (a run may pick a subset) and its offered experts."""
    try:
        config = get_agent_config(agent_id, conninfo=conninfo)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    model = config['model']
    fits = model is not None and model['enabled'] and model_fits(model)
    holds = config['access']['resources']
    listed = lambda type_: [item for item in holds if item['type'] == type_]
    servers = {item['id'] for item in listed('server')}
    projects = {item['id'] for item in listed('code_project')}

    def brings(service_id) -> dict:
        """What one service brings to a run of this agent: code projects and usable servers it holds."""
        found = bring(conn, project, [service_id])
        return {
            'brings_code': any(str(item) in projects for item in found['code_project_ids']),
            'brings_servers': any(str(item) in servers for item in found['server_ids']),
        }

    def server_state(server_id: str) -> dict:
        """Whether a run can use the server over SSH now (a server without it is still given)."""
        server = next(item for item in holds if item['type'] == 'server' and item['id'] == server_id)
        reason = access_rules.ssh_reason(server)
        return {'usable': reason is None, 'reason': reason}

    def plain(type_: str) -> list[dict]:
        return [{'id': item['id'], 'name': item['name'], 'linked': False} for item in listed(type_)]

    resources = {
        'servers': [{**item, **server_state(item['id'])} for item in plain('server')],
        'services': [{**item, **brings(item['id'])} for item in plain('service')],
        'environments': plain('environment'),
        'code_projects': plain('code_project'),
        'documents': [{'id': item['id'], 'name': item['name'], 'linked': False} for item in listed('document')],
    }
    return jsonable_encoder(
        {
            'agent': {
                'id': config['id'],
                'name': config['name'],
                'revision': config['revision'],
                'model': {'id': model['id'], 'name': model['name']} if model else None,
                'system_prompt': config['system_prompt']['content'],
                # off in every run of it (a run's settings show them locked)
                'disabled_tools': config['disabled_tools'],
            },
            'models': [{'id': model['id'], 'name': model['name']}] if fits else [],
            'default_model_id': model['id'] if fits else None,
            'prompts': [],
            'default_prompt_id': None,
            'resources': resources,
            'experts': [
                {'id': entry['id'], 'name': entry['name'], 'when_to_use': entry['when_to_use']}
                for entry in offered(conn, agent_id)
            ],
        }
    )
