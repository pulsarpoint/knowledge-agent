"""Starting a session: the options a start panel needs, Preview (exactly what Start pins) and Start. One request shape for
every run; where it is started from is a label (started_from), and what it may use is pinned in its config. A saved agent
(agent_id) starts from its own configuration, which the request can only narrow (narrow)."""

import hashlib
import re
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg import errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_admin.data_root import documentation_folder
from knowledge_admin.servers.discovery import readiness, server_row
from knowledge_admin.documents.api import NAME_IN_USE, document_slug

from knowledge_admin.knowledge_agents.config import (
    get_agent_config,
    load_model,
    model_fits,
    per_run,
    require_run_resources,
)
from knowledge_admin.knowledge_agents.ephemeral import ad_hoc_config, pin_services
from knowledge_admin.knowledge_agents.experts import offered, pin
from knowledge_admin.knowledge_agents.run_resources import (
    RunResourceError,
    RunResources,
    bring,
    options as run_options,
    resolve,
)
from knowledge_admin.sessions.text import agent_system_prompt
from knowledge_admin.sessions.rows import NO_MODEL, Selection
from knowledge_admin.tools.catalog import (
    CATALOG,
    DOCUMENT_EDIT_TOOLS,
    PROPOSAL_STARTS,
    PROPOSAL_TOOLS,
    SUBAGENT,
    needs_met,
)

REVISION_TAG = 'document-revision'
WRITE_TAG = 'documentation'
ASSISTANT_TAG = 'assistant'  # with the resource type's tag (server, service, environment): a chat prompt for it
ONE_START = 'Choose one starting point: a document, a new document, a server, a service or an environment.'
ID_OR_NEW = 'Give a document id or a new document.'
ONE_NEW = 'One new document per run.'
NOT_THE_START = 'The new document must be the one the run starts from.'
DOCUMENT_NOT_FOUND = 'Document not found'
DOCUMENT_FOREIGN_KEY = 'agent_sessions_target_document'
UNKNOWN_TOOL = 'Unknown tool: {names}.'
NEEDS = '{tool} needs {what}.'
REQUIRES = {
    'files': 'a code project or a collection',
    'code': 'a code project',
    'code_graph': 'a code project',
    'documents': 'a collection',
    'ssh': 'at least one server',
    'editable_document': 'a document to edit',
    'server_proposals': 'a run started from a server',
    'resource_proposals': 'a run started from a server, service or environment',
}
# A chat on a resource: its tag (lists find its chats by it) and the table its id is in.
RESOURCE_TYPES = {'server': 'servers', 'service': 'services', 'environment': 'environments'}
RESOURCE_NOT_FOUND = {'service': 'Service not found.', 'environment': 'Environment not found.'}
TAG = re.compile(r'^[a-z0-9_-]+(/[a-z0-9_-]+)*$')
TAG_LENGTH = 120
MAX_TAGS = 10
BAD_TAG = 'A tag is lowercase letters, digits, _ and -, in parts separated by /, at most 120 characters: {tag}'
TOO_MANY_TAGS = 'A run takes at most 10 tags.'
RESOURCE_TAG = 'A resource tag comes from where the run starts: {tag}'
NO_EXPERT = 'This expert is not available: {id}.'
NOT_SET_UP = 'SSH is not set up for this server.'
NOT_ALLOWED = 'SSH is not allowed for this server.'
NOT_ITS_TOOL = "{tool} is not one of this agent's tools."
NO_SUBAGENTS = 'This agent does not allow subagents.'
OWN_MODEL = 'A saved agent uses its own model and prompt.'
NO_ENVIRONMENTS = 'A saved agent takes no environments; choose its servers or services.'
NO_DOCUMENTS = 'A saved agent takes no documents yet.'
AGENT_NO_MODEL = 'Select an enabled model in this agent’s General tab.'
TITLE_LENGTH = 120


class NewDocument(BaseModel):
    """A document the run writes: its name (a slug comes from it) and its documentation folder."""

    model_config = ConfigDict(extra='forbid')
    folder: str = Field(max_length=200)
    name: str = Field(max_length=240)

    @model_validator(mode='after')
    def valid(self):
        if '\x00' in self.name:
            raise ValueError('Text cannot contain null characters')
        self.name = self.name.strip()
        if not document_slug(self.name):
            raise ValueError('Use letters or digits in the document name')
        self.folder = documentation_folder(self.folder)
        return self


class DocumentAccess(BaseModel):
    """A document the run may read (and edit, when editable), or a new document it may write (always editable)."""

    model_config = ConfigDict(extra='forbid')
    id: UUID | None = None
    new: NewDocument | None = None
    editable: bool = False

    @model_validator(mode='after')
    def valid(self):
        if (self.id is None) == (self.new is None):
            raise ValueError(ID_OR_NEW)
        if self.new is not None:
            self.editable = True
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


def resource_tag(kind: str, resource_id) -> str:
    """The tag of a run started from a resource: servers/<id>, services/<id> or environments/<id>."""
    return f'{RESOURCE_TYPES[kind]}/{resource_id}'


class StartInput(BaseModel):
    """What a start panel sends: the first message, the model and prompt, facts from where it starts (context), what
    the run may use (resources, tools, experts, subagents) and where it was started from (a label for lists)."""

    model_config = ConfigDict(extra='forbid')
    agent_id: str | None = None
    started_from: StartedFrom | None = None
    message: str = Field(max_length=20000)
    model_id: UUID | None = None
    prompt_id: UUID | None = None
    system_prompt: str = Field(default='', max_length=20000)
    context: str = Field(default='', max_length=20000)
    resources: Resources = Field(default_factory=Resources)
    tools: list[str] = Field(default_factory=list, max_length=100)
    expert_ids: list[UUID] = Field(default_factory=list, max_length=50)
    subagents: bool = False
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
        if self.agent_id is None:
            if self.started_from is None:
                raise ValueError('Choose what the agent starts from.')
            if self.model_id is None:
                raise ValueError('Choose a model.')
            if not self.system_prompt:
                raise ValueError('Enter a system prompt')
        self.tools = list(dict.fromkeys(self.tools))
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
        merged: dict[UUID, bool] = {}
        new = [entry for entry in self.resources.documents if entry.new is not None]
        if len(new) > 1:
            raise ValueError(ONE_NEW)
        for entry in self.resources.documents:  # one entry per document; editable wins
            if entry.id is not None:
                merged[entry.id] = merged.get(entry.id, False) or entry.editable
        self.resources.documents = [DocumentAccess(id=key, editable=value) for key, value in merged.items()] + new
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


def choosable() -> list:
    """The tools a person can switch on for a run: not "Answer only from documents", not run_subagent (its own
    switch), not get_resource (on in every run with resources). The proposal tools work only in a run started from a
    resource whose request turns them on (their need says so)."""
    return [spec for spec in CATALOG.values() if spec.needs not in ('grounded', 'resources') and spec.name != SUBAGENT]


def revise_prompt_id(project: UUID) -> UUID:
    """The seeded "Revise a document" prompt's id (migration 0100: md5(...)::uuid)."""
    return UUID(hashlib.md5(f'knowledge-agent-v3:prompts:document-revision:{project}'.encode()).hexdigest())


def write_prompt_id(project: UUID) -> UUID:
    """The seeded "Write a document" prompt's id (md5(...)::uuid, as its migration seeds it)."""
    return UUID(hashlib.md5(f'knowledge-agent-v3:prompts:write-document:{project}'.encode()).hexdigest())


def assistant_prompt_id(project: UUID, kind: str) -> UUID:
    """The seeded "<Type> assistant" prompt's id of a resource type (migration 0106: md5(...)::uuid)."""
    return UUID(hashlib.md5(f'knowledge-agent-v3:prompts:{kind}-assistant:{project}'.encode()).hexdigest())


def proposals_offered(kind: str | None) -> tuple[str, ...]:
    """The proposal tools a run started from this resource type may ask for (none for other runs)."""
    return tuple(name for name in PROPOSAL_TOOLS if kind is not None and kind in PROPOSAL_STARTS[CATALOG[name].needs])


def start_server(conn, project: UUID, server_id: UUID) -> dict:
    """The server a run starts from: it exists in this workspace and SSH is set up and allowed for it."""
    server = server_row(conn, project, server_id, lock=True)
    reason = readiness(server)
    if reason:
        raise HTTPException(400, reason)
    return server


def start_resource(conn, project: UUID, kind: str, resource_id: UUID) -> dict:
    """The service or environment a run starts from: it exists in this workspace. No SSH is needed: what it brings
    that the run cannot reach is left out (inventory and documents still work)."""
    table = 'ka.services' if kind == 'service' else 'ka.environments'
    row = conn.execute(
        f'SELECT id,name FROM {table} WHERE id=%s AND project_id=%s FOR SHARE', (resource_id, project)
    ).fetchone()
    if row is None:
        raise HTTPException(404, RESOURCE_NOT_FOUND[kind])
    return row


def server_reason(row: dict) -> str | None:
    if not row['enabled'] or row['credential_id'] is None:
        return NOT_SET_UP
    if not row['ssh_allowed']:
        return NOT_ALLOWED
    return None


def target_document(conn, project: UUID, document_id: UUID) -> dict:
    row = conn.execute(
        """SELECT d.id,d.title,d.code_project_ids,d.service_ids,d.server_ids,d.environment_ids
        FROM ka.document_details d WHERE d.id=%s AND d.project_id=%s""",
        (document_id, project),
    ).fetchone()
    if row is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return row


def document_access(conn, project: UUID, entries: list[DocumentAccess]) -> list[dict]:
    """The documents the run may read (and edit, when editable), with their current titles, and the new document it
    may write (an id of its own, 'new:<uuid>', its name, folder and slug). A brand-new draft already holding the new
    document's folder and slug refuses the run (409): every save would fail. A live document of that name does not:
    the run drafts that document."""
    access = []
    for entry in entries:
        if entry.new is not None:
            slug = document_slug(entry.new.name)
            if conn.execute(
                """SELECT 1 FROM ka.documents WHERE project_id=%s AND is_draft AND draft_of IS NULL
                AND folder IS NOT DISTINCT FROM %s AND slug=%s""",
                (project, entry.new.folder, slug),
            ).fetchone():
                raise HTTPException(409, NAME_IN_USE.format(slug=slug))
            access.append(
                {
                    'id': f'new:{uuid4()}',
                    'title': entry.new.name,
                    'folder': entry.new.folder,
                    'slug': slug,
                    'editable': True,
                    'new': True,
                }
            )
            continue
        row = conn.execute(
            'SELECT id,title FROM ka.live_documents WHERE id=%s AND project_id=%s',
            (entry.id, project),
        ).fetchone()
        if row is None:
            raise HTTPException(404, DOCUMENT_NOT_FOUND)
        access.append({'id': str(row['id']), 'title': row['title'], 'editable': entry.editable})
    return access


def narrow(conn, project: UUID, agent_id: str, value: StartInput, conninfo: str) -> tuple[dict, dict]:
    """A saved agent's configuration for one run, narrowed by the request and never widened: its tools that are on
    (switch off only), a subset of its experts, subagents only if it allows them, and for a per-run agent a subset of
    its saved resources. Its model and system prompt are its own."""
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
    if value.resources.environment_ids:
        raise HTTPException(400, NO_ENVIRONMENTS)
    if value.resources.documents:
        raise HTTPException(400, NO_DOCUMENTS)
    resources = value.resources
    requested = (
        RunResources(
            code_project_ids=resources.code_project_ids,
            collection_ids=resources.collection_ids,
            server_ids=resources.server_ids,
            service_ids=resources.service_ids,
        )
        if any((resources.code_project_ids, resources.collection_ids, resources.server_ids, resources.service_ids))
        or per_run(config)
        else None
    )
    try:
        pinned = resolve(conn, project, config, requested)
        require_run_resources(pinned)
        if not pinned['enabled']:
            raise ValueError('Agent is disabled.')
        if model is None or not model['enabled'] or not model_fits(model):
            raise ValueError(AGENT_NO_MODEL)
    except RunResourceError as exc:
        raise HTTPException(400, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    pinned = pin(conn, pinned, agent_id)
    items = {item['tool']: item for item in pinned['tools']['items']}
    on = {name for name, item in items.items() if item['mode'] == 'on'}
    added = [name for name in value.tools if name not in on]
    if added:
        raise HTTPException(400, ' '.join(NOT_ITS_TOOL.format(tool=name) for name in added))
    if value.subagents and SUBAGENT not in on:
        raise HTTPException(400, NO_SUBAGENTS)
    controlled = {spec.name for spec in choosable()}  # what agent_options lists; any other tool keeps its pinned mode
    for name, item in items.items():
        if name == SUBAGENT:
            item['mode'] = 'on' if value.subagents else 'off'
        elif name in controlled and name in on and name not in value.tools:
            item['mode'] = 'off'
    usable = (
        [server for server in pinned['ssh']['servers'] if server['enabled'] and server['ssh_allowed']]
        if pinned['ssh']['enabled']
        else []
    )  # as get_ssh selects them at run time
    unmet = [
        NEEDS.format(tool=name, what=REQUIRES[CATALOG[name].needs])
        for name in value.tools
        if name in CATALOG
        and CATALOG[name].needs in REQUIRES
        and not needs_met(CATALOG[name].needs, pinned, usable if CATALOG[name].needs == 'ssh' else None)
    ]
    if unmet:
        raise HTTPException(400, ' '.join(unmet))
    experts = {entry['id']: entry for entry in pinned['experts']}
    for expert_id in value.expert_ids:
        if str(expert_id) not in experts:
            raise HTTPException(400, NO_EXPERT.format(id=expert_id))
    pinned['experts'] = [experts[str(expert_id)] for expert_id in value.expert_ids]
    if value.context:
        content = pinned['system_prompt']['content'] + f'\n\n# Context\n\n{value.context}'
        pinned['system_prompt'] = {**pinned['system_prompt'], 'content': content}
    pinned['project_id'] = str(project)
    return jsonable_encoder(pinned), model


def usable_tools(conn, project: UUID, agent_id: str, conninfo: str) -> list[str]:
    """The agent's tools that are on (as agent_options lists them) whose need its saved configuration meets, judged as
    narrow judges them (SSH: its enabled, SSH-allowed servers). What a start with everything the agent has on can
    send: a tool it could not use is left off rather than refused."""
    try:
        config = get_agent_config(agent_id, conninfo=conninfo)
        pinned = resolve(conn, project, config, None)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except RunResourceError as exc:
        raise HTTPException(400, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    on = {item['tool'] for item in pinned['tools']['items'] if item['mode'] == 'on'}
    usable = (
        [server for server in pinned['ssh']['servers'] if server['enabled'] and server['ssh_allowed']]
        if pinned['ssh']['enabled']
        else []
    )
    return [
        spec.name
        for spec in choosable()
        if spec.name in on
        and (spec.needs not in REQUIRES or needs_met(spec.needs, pinned, usable if spec.needs == 'ssh' else None))
    ]


def build(
    conn, project: UUID, value: StartInput, session_id: UUID | None, conninfo: str
) -> tuple[dict, dict, dict | None]:
    """(pinned configuration, model, where it starts: the document or the resource; None for a saved agent or a new
    document): preview and start share it, so they pin the same configuration except the random `new:` id of a new
    document. A run started from a resource records it as `started_from` ({type, id, name}) and is chosen for it
    (`chosen_resources`). A server run has SSH to it (added to the chosen servers when missing) and reads the services
    deployed on it (pinned without what they would bring); a service brings its code projects and the servers it is
    deployed on, an environment its services and servers (what the run cannot reach is left out). The proposal tools
    the request turns on are recorded as `proposal_tools`: their need is met only there."""
    if value.agent_id is not None:
        pinned, model = narrow(conn, project, value.agent_id, value, conninfo)
        return pinned, model, None
    server = origin = None
    selection = value.resources
    resource = value.resource()
    if resource is not None and resource[0] == 'server':
        server = origin = start_server(conn, project, resource[1])
        if server['id'] not in selection.server_ids:
            selection = selection.model_copy(update={'server_ids': [*selection.server_ids, server['id']]})
    elif resource is not None:
        kind, resource_id = resource
        origin = start_resource(conn, project, kind, resource_id)
        key = f'{kind}_ids'
        if origin['id'] not in getattr(selection, key):
            selection = selection.model_copy(update={key: [*getattr(selection, key), origin['id']]})
    started = value.started_from.new_document
    entries = list(value.resources.documents)
    new = next((entry.new for entry in entries if entry.new is not None), None)
    if new is not None and (started is None or new.model_dump() != started.model_dump()):
        raise HTTPException(400, NOT_THE_START)
    if started is not None and new is None:  # the new document it starts from is a resource it may write
        entries.append(DocumentAccess(new=started, editable=True))
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
    names = {spec.name: spec for spec in choosable()}
    unknown = [name for name in value.tools if name not in names]
    if unknown:
        raise HTTPException(400, UNKNOWN_TOOL.format(names=', '.join(unknown)))
    # a resource run's resource is named in the instructions (runner.get_prompt), not in its context
    system_prompt = value.system_prompt + (f'\n\n# Context\n\n{value.context}' if value.context else '')
    try:
        pinned = ad_hoc_config(conn, project, model=model, system_prompt=system_prompt, selection=selection)
    except RunResourceError as exc:
        raise HTTPException(400, str(exc)) from None
    if server is not None:
        deployed = conn.execute(
            'SELECT DISTINCT service_id FROM ka.service_deployments WHERE server_id=%s', (server['id'],)
        ).fetchall()
        pinned = pin_services(conn, project, pinned, [row['service_id'] for row in deployed])
    if resource is not None:
        pinned['started_from'] = {'type': resource[0], 'id': str(origin['id']), 'name': origin['name']}
        pinned['proposal_tools'] = [name for name in PROPOSAL_TOOLS if name in value.tools]
    keyed = [server for server in pinned['ssh']['servers'] if server.get('private_key_ref') is not None]
    if len(keyed) != len(pinned['ssh']['servers']):  # a server a service or environment brought in, without an SSH key
        pinned['ssh'] = {**pinned['ssh'], 'servers': keyed, 'enabled': pinned['ssh']['enabled'] and bool(keyed)}
        if 'server_ids' in pinned.get('run_resources', {}):
            kept = {str(server['id']) for server in keyed}
            pinned['run_resources'] = {
                **pinned['run_resources'],
                'server_ids': [i for i in pinned['run_resources']['server_ids'] if str(i) in kept],
            }
    pinned['document_access'] = document_access(conn, project, entries)
    unmet = [
        NEEDS.format(tool=name, what=REQUIRES[names[name].needs])
        for name in value.tools
        if not needs_met(names[name].needs, pinned, None)
    ]
    if unmet:
        raise HTTPException(400, ' '.join(unmet))
    chosen = set(value.tools)
    for item in pinned['tools']['items']:
        if item['tool'] == SUBAGENT:
            item['mode'] = 'on' if value.subagents else 'off'
        elif item['tool'] in CATALOG:
            item['mode'] = 'on' if item['tool'] in chosen else 'off'
            if item['tool'] in DOCUMENT_EDIT_TOOLS + PROPOSAL_TOOLS:
                item['subagent_allowed'] = False
    experts = {entry['id']: entry for entry in pinned['experts']}
    for expert_id in value.expert_ids:
        if str(expert_id) not in experts:
            raise HTTPException(400, NO_EXPERT.format(id=expert_id))
    pinned['experts'] = [experts[str(expert_id)] for expert_id in value.expert_ids]
    pinned['project_id'] = str(project)
    return jsonable_encoder(pinned), model, origin if origin is not None else target


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
    a saved agent → test, or `purpose` for a saved agent, as an event handler starts it with its event and handler),
    its tags (the request's and its resource's), its first input, and audit it. POST /api/sessions and the
    event processor share it. The title is the message's first line unless one is given (an event run's names its event)."""
    if value.agent_id is None and (purpose, event_id, handler_id, title) != (None, None, None, None):
        raise ValueError('Only a saved agent run takes a purpose, an event, a handler or a title.')
    session_id = uuid4()
    pinned, model, origin = build(conn, project, value, session_id, conninfo)
    title = (title or value.message.splitlines()[0])[:TITLE_LENGTH]
    resource = value.resource()
    # the proposal tools are offered only for runs started from a resource they work on: audited as off only there
    offered = proposals_offered(resource[0] if resource else None)
    tools_off = sorted(
        item['tool']
        for item in pinned['tools']['items']
        if item['tool'] in CATALOG
        and item['mode'] != 'on'
        and CATALOG[item['tool']].needs != 'resources'  # on whenever the run has resources
        and (item['tool'] not in PROPOSAL_TOOLS or item['tool'] in offered)
    )
    tags = value.run_tags()
    if value.agent_id is not None:
        snapshot = {'system_prompt': agent_system_prompt(pinned), 'context': value.context}
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,purpose,tags,
            target_document_id,prompt_snapshot,context_snapshot,agent_config,run_resources,event_id,handler_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s)""",
            (
                session_id,
                project,
                value.agent_id,
                actor,
                model['id'],
                title,
                purpose or 'test',
                tags,
                Jsonb(snapshot),
                Jsonb({'folders': [], 'code_project_ids': []}),
                Jsonb(pinned),
                Jsonb(pinned['run_resources']) if per_run(pinned) else None,
                event_id,
                handler_id,
            ),
        )
        details = {
            'agent_id': value.agent_id,
            'model_id': model['id'],
            'tools_off': tools_off,
            'expert_ids': [entry['id'] for entry in pinned['experts']],
            'subagents': value.subagents,
            'resources': pinned.get('run_resources'),
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
            server_id,target_document_id,prompt_snapshot,context_snapshot,agent_config,run_resources)
            VALUES (%s,%s,NULL,%s,%s,%s,'resource',%s,%s,NULL,%s,%s,%s,%s)""",
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
                Jsonb(pinned['run_resources']),
            ),
        )
        details = {
            f'{kind}_id': resource_id,
            'tags': tags,
            'model_id': model['id'],
            'prompt_id': value.prompt_id,
            'tools_off': tools_off,
            'expert_ids': value.expert_ids,
            'subagents': value.subagents,
        }
    elif value.started_from.new_document is not None:
        new = value.started_from.new_document.model_dump()
        snapshot = {'system_prompt': value.system_prompt, 'context': value.context, 'prompt_id': value.prompt_id}
        conn.execute(
            """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,purpose,tags,
            target_document_id,folder,document_name,prompt_snapshot,context_snapshot,agent_config,run_resources)
            VALUES (%s,%s,NULL,%s,%s,%s,'documents',%s,NULL,%s,%s,%s,%s,%s,%s)""",
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
                Jsonb(pinned['run_resources']),
            ),
        )
        details = {
            'new_document': new,
            'tags': tags,
            'model_id': model['id'],
            'prompt_id': value.prompt_id,
            'tools_off': tools_off,
            'expert_ids': value.expert_ids,
            'subagents': value.subagents,
        }
    else:
        document_id = value.started_from.document_id
        snapshot = {'system_prompt': value.system_prompt, 'context': value.context, 'prompt_id': value.prompt_id}
        try:
            conn.execute(
                """INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,purpose,tags,
                target_document_id,prompt_snapshot,context_snapshot,agent_config,run_resources)
                VALUES (%s,%s,NULL,%s,%s,%s,'revision',%s,%s,%s,%s,%s,%s)""",
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
                    Jsonb(pinned['run_resources']),
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
            'tools_off': tools_off,
            'expert_ids': value.expert_ids,
            'subagents': value.subagents,
        }
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
    seeded assistant prompt the first choice), resources (marked linked to the starting document or resource), the choosable
    tools (the proposal tools only where a resource run may ask for them; for a resource, none that edits a
    document) and experts."""
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
        """Whether each service or environment brings a code project, and a server the run can use (as
        run_resources.resolve and build() narrow them), so the panel offers only tools Start accepts."""
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
        'collections': listed(
            """SELECT id,name FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL
            ORDER BY lower(name),id""",
            [],
        ),
        'documents': [{'id': document['id'], 'name': document['title'], 'linked': True, 'editable': True}]
        if document
        else [],
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
            'tools': [
                {
                    'name': spec.name,
                    'label': spec.label,
                    'summary': spec.summary,
                    'group': spec.group,
                    'needs': spec.needs,
                    'requires': REQUIRES.get(spec.needs) if spec.needs else None,
                }
                for spec in choosable()
                if (spec.name in proposals_offered(kind) if spec.name in PROPOSAL_TOOLS else True)
                and (kind is None or spec.needs != 'editable_document')
            ],
            'experts': experts,
        }
    )


def agent_options(conn, project: UUID, agent_id: str, conninfo: str) -> dict:
    """What a start panel offers for a saved agent: only what the request may narrow. Its own model and prompt (no
    choice), its saved selection (a per-run agent's usable resources; none for a fixed agent), its tools that are on and
    its offered experts."""
    try:
        config = get_agent_config(agent_id, conninfo=conninfo)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    model = config['model']
    fits = model is not None and model['enabled'] and model_fits(model)
    fixed = not per_run(config)
    saved = {} if fixed else run_options(conn, project, config)
    listed = lambda kind: [] if saved.get(kind) is None else saved[kind]  # noqa: E731
    on = {item['tool'] for item in config['tools']['items'] if item['mode'] == 'on'}
    servers = {str(item['id']) for item in listed('servers')}
    projects = {str(item['id']) for item in listed('code_projects')}

    def brings(service_id) -> dict:
        """What one service brings to a run of this agent, as resolve() gives it: code projects and usable servers
        inside its selection."""
        found = bring(conn, project, [service_id])
        return {
            'brings_code': any(str(item) in projects for item in found['code_project_ids']),
            'brings_servers': any(str(item) in servers for item in found['server_ids']),
        }

    resources = {
        'servers': [
            {'id': item['id'], 'name': item['name'], 'linked': False, 'usable': True, 'reason': None}
            for item in listed('servers')
        ],
        'services': [
            {'id': item['id'], 'name': item['name'], 'linked': False, **brings(item['id'])}
            for item in listed('services')
        ],
        'environments': [],
        'code_projects': [
            {'id': item['id'], 'name': item['name'], 'linked': False} for item in listed('code_projects')
        ],
        'collections': [{'id': item['id'], 'name': item['name'], 'linked': False} for item in listed('collections')],
        'documents': [],
    }
    return jsonable_encoder(
        {
            'agent': {
                'id': config['id'],
                'name': config['name'],
                'revision': config['revision'],
                'model': {'id': model['id'], 'name': model['name']} if model else None,
                'system_prompt': config['system_prompt']['content'],
                'resources_fixed': fixed,
                'subagents_allowed': SUBAGENT in on,
            },
            'models': [{'id': model['id'], 'name': model['name']}] if fits else [],
            'default_model_id': model['id'] if fits else None,
            'prompts': [],
            'default_prompt_id': None,
            'resources': resources,
            'tools': [
                {
                    'name': spec.name,
                    'label': spec.label,
                    'summary': spec.summary,
                    'group': spec.group,
                    'needs': spec.needs,
                    'requires': REQUIRES.get(spec.needs) if spec.needs else None,
                }
                for spec in choosable()
                if spec.name in on
            ],
            'experts': [
                {'id': entry['id'], 'name': entry['name'], 'when_to_use': entry['when_to_use']}
                for entry in offered(conn, agent_id)
            ],
        }
    )
