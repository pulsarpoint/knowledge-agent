"""Named agent profiles and their explicit knowledge and asset selections."""

from functools import partial
from uuid import UUID
from typing import Literal

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import TYPES, is_content_editor
from knowledge_admin.capabilities.inventory import short
from knowledge_admin.identity import request_actor
from knowledge_admin.db import audit as write_audit, storage
from knowledge_admin.knowledge_agents.config import get_agent_config, model_fits, tool_items
from knowledge_admin.tools.catalog import tools_summary
from knowledge_admin.agents.tools_api import flags, subagents_on, switch_subagents


EXPERT_NEEDS_WHEN = 'Experts need When to use.'
AGENT = """SELECT id,name,description,when_to_use,enabled,model_id,system_prompt,documents_grounded,expert,cloned_from,
    revision,access,instructions,created_by,updated_by,created_at,updated_at FROM ka.agents WHERE id=%s"""


INSTRUCTIONS_LIMIT = 40000

# Every resource of the project an agent's access may reference, by type: what still exists (deleted collections and
# archived code projects are not listed).
RESOURCES = {
    'server': 'SELECT id,name,description FROM ka.servers WHERE project_id=%s ORDER BY lower(name),id',
    'service': 'SELECT id,name,description FROM ka.services WHERE project_id=%s ORDER BY lower(name),id',
    'environment': 'SELECT id,name,description FROM ka.environments WHERE project_id=%s ORDER BY lower(name),id',
    'code_project': """SELECT id,name,description FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL
        ORDER BY lower(name),id""",
    'collection': """SELECT id,name,description FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL
        ORDER BY lower(name),id""",
    'document': """SELECT id,title AS name,description FROM ka.live_documents WHERE project_id=%s
        ORDER BY lower(title),id""",
    'skill': 'SELECT id,name,description FROM ka.agent_skills WHERE project_id=%s ORDER BY lower(name),id',
    'mcp_server': 'SELECT id,name,description FROM ka.mcp_servers WHERE project_id=%s ORDER BY lower(name),id',
}


class AccessEntry(BaseModel):
    model_config = ConfigDict(extra='ignore')  # an entry as GET resolves it (name, description_short, missing) is fine
    type: Literal[TYPES]
    id: UUID
    editable: bool = Field(default=False, strict=True)  # documents only


class Roles(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content_editor: bool = Field(default=False, strict=True)


class AccessInput(BaseModel):
    """An agent's access object: references to the project's resources and its roles."""

    model_config = ConfigDict(extra='forbid')
    resources: list[AccessEntry] = Field(max_length=1000)
    roles: Roles


def project_resources(conn, project) -> dict[str, list[dict]]:
    """Every resource of the project by type, in TYPES order: {type, id, name, description_short}."""
    return {
        type_: [
            {'type': type_, 'id': str(row['id']), 'name': row['name'], 'description_short': short(row['description'])}
            for row in conn.execute(RESOURCES[type_], (project,))
        ]
        for type_ in TYPES
    }


def _index(found: dict[str, list[dict]]) -> dict[tuple[str, str], dict]:
    return {(item['type'], item['id']): item for items in found.values() for item in items}


def resolve(access: dict, found: dict[str, list[dict]]) -> dict:
    """The access object as the editor shows it: each reference with its name and short description (a document with
    editable); a reference to a resource that no longer exists is marked missing."""
    index = _index(found)
    resources = []
    for ref in access.get('resources', []):
        key = (ref['type'], str(ref['id']))
        entry = (
            dict(index[key]) if key in index else {'type': key[0], 'id': key[1], 'name': '', 'description_short': ''}
        )
        if key not in index:
            entry['missing'] = True
        if ref['type'] == 'document':
            entry['editable'] = bool(ref.get('editable'))
        resources.append(entry)
    return {'resources': resources, 'roles': {'content_editor': is_content_editor(access)}}


def references(value: AccessInput, found: dict[str, list[dict]]) -> dict:
    """The access object to store: each reference once, only to a resource of the project that exists (others are
    dropped); editable is kept for documents only."""
    index = _index(found)
    resources: dict[tuple[str, str], dict] = {}
    for ref in value.resources:
        key = (ref.type, str(ref.id))
        if key in index and key not in resources:
            resources[key] = {
                'type': ref.type,
                'id': key[1],
                **({'editable': ref.editable} if ref.type == 'document' else {}),
            }
    return {'resources': list(resources.values()), 'roles': {'content_editor': value.roles.content_editor}}


def counts(access: dict, found: dict[str, list[dict]]) -> dict[str, int]:
    """How many resources of each type the access references that still exist."""
    index = _index(found)
    held = [ref['type'] for ref in access.get('resources', []) if (ref['type'], str(ref['id'])) in index]
    return {type_: held.count(type_) for type_ in TYPES}


def valid_instructions(value: str) -> str:
    if '\x00' in value:
        raise ValueError('Instructions cannot contain null characters')
    return value


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    revision: int = Field(ge=1, strict=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=4000)
    when_to_use: str = Field(max_length=4000)
    enabled: bool = Field(strict=True)
    documents_grounded: bool | None = Field(default=None, strict=True)
    access: AccessInput
    instructions: str = Field(max_length=INSTRUCTIONS_LIMIT)
    subagents: bool | None = Field(default=None, strict=True)  # May start subagents; None keeps the switch
    expert: bool | None = Field(default=None, strict=True)  # Offer as an expert; None keeps the switch
    model_id: UUID | None = None  # Left out keeps the model; null clears it

    @field_validator('instructions')
    @classmethod
    def no_nulls(cls, value: str) -> str:
        return valid_instructions(value)


class DeleteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    confirmed: Literal[True]
    with_history: bool = Field(default=False, strict=True)


def check_model(conn, model_id) -> None:
    """Refuse a model an agent cannot run: missing, disabled, embeddings, or not a Responses or Claude connection."""
    selected = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s FOR SHARE', (model_id,)).fetchone()
    if selected is None or not selected['enabled'] or selected['api_format'] == 'embeddings':
        raise HTTPException(400, 'Choose an enabled model compatible with this agent.')
    if not model_fits(selected):
        raise HTTPException(400, 'Choose an enabled model compatible with this agent.')


class ModelInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    model_id: UUID | None


PROMPT_LIMIT = 100000
PROMPT_STALE = 'Agent changed. Reload before saving.'


class PromptInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    system_prompt: str = Field(min_length=1, max_length=PROMPT_LIMIT)

    @field_validator('system_prompt')
    @classmethod
    def valid_text(cls, value: str) -> str:
        if not value.strip() or '\x00' in value:
            raise ValueError('Enter a system prompt without null characters')
        return value


HISTORY_REFUSED = (
    'This agent has sessions or run history. Delete it with its history, or disable it to keep the history.'
)
WORKING_REFUSED = (
    'This agent is working right now. Stop its documentation sessions and runs, '
    'or wait for them to finish, then delete it.'
)
# Anything still in progress for the agent: Test and documentation sessions run in the session workers.
WORKING = """SELECT 1 FROM ka.agent_sessions WHERE agent_id=%(a)s AND status IN ('starting','working') LIMIT 1"""


def agent_history(conn, agent_id) -> dict[str, int]:
    """What deleting the agent with its history removes (test_chats: Test and event sessions); saved documents are not part of it. `run_records` are the
    records of Test chats from before migration 0081 (run:<id> keys)."""
    return conn.execute(
        """SELECT
        (SELECT count(*) FROM ka.agent_sessions WHERE agent_id=%(agent)s AND purpose IN ('test','event'))::int AS test_chats,
        (SELECT count(*) FROM ka.agent_sessions WHERE agent_id=%(agent)s AND purpose='documents')::int
            AS documentation_sessions,
        (SELECT count(*) FROM ka.agent_run_events WHERE agent_id=%(agent)s AND kind='start'
            AND starts_with(run_key, 'run:'))::int AS run_records""",
        {'agent': agent_id},
    ).fetchone()


def delete_history(conn, agent_id) -> None:
    """Remove the agent's sessions and run records. Run records have no foreign keys: run:<id> (old Test chats) belong
    to the agent by agent_id, cc:<session>:<turn> by their session; inputs and suggestions cascade with the session."""
    conn.execute(
        """DELETE FROM ka.agent_run_events WHERE (starts_with(run_key, 'run:') AND agent_id=%s)
        OR (starts_with(run_key, 'cc:') AND split_part(run_key, ':', 2) IN (
            SELECT id::text FROM ka.agent_sessions WHERE agent_id=%s))""",
        (agent_id, agent_id),
    )
    conn.execute('DELETE FROM ka.agent_sessions WHERE agent_id=%s', (agent_id,))


def build_api(app_name, store):
    router = APIRouter(prefix='/agents')

    database = partial(storage, store, 'Agent storage')

    def read(conn, agent_id):
        row = conn.execute(AGENT, (agent_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Agent not found')
        row['access'] = resolve(row['access'], project_resources(conn, store.project(conn, app_name)))
        row['original'] = (
            conn.execute('SELECT id,name FROM ka.agents WHERE id=%s', (row['cloned_from'],)).fetchone()
            if row['cloned_from']
            else None
        )
        row['subagents'] = subagents_on(conn, agent_id)
        return row

    def audit(conn, request, agent_id, action, **details):
        actor = store.actor(conn, *request_actor(request))
        write_audit(
            conn, store.project(conn, app_name), actor, action, 'agent', None, {'agent_id': agent_id, **details}
        )
        return actor

    @router.get('')
    def listing():
        with database().connect() as conn:
            rows = conn.execute("""SELECT a.*,m.name AS model_name,o.name AS original_name
                FROM ka.agents a LEFT JOIN ka.llm_models m ON m.id=a.model_id
                LEFT JOIN ka.agents o ON o.id=a.cloned_from
                ORDER BY a.created_at,a.id""").fetchall()
            project = store.project(conn, app_name)
            found = project_resources(conn, project)
            index = _index(found)
            saved_tools: dict[str, list[dict]] = {}
            for tool in conn.execute('SELECT agent_id,tool,mode,usage FROM ka.agent_tools'):
                saved_tools.setdefault(tool['agent_id'], []).append(tool)
            mcp_servers = {
                row['id']: row
                for row in conn.execute(
                    'SELECT id::text AS id,enabled,tools FROM ka.mcp_servers WHERE project_id=%s', (project,)
                )
            }
            for row in rows:
                row.pop('system_prompt')  # read with the agent, not in the list
                access = row['access']
                existing = {**access, 'resources': [r for r in access['resources'] if (r['type'], r['id']) in index]}
                row['counts'] = counts(access, found)
                row['tools_summary'] = tools_summary(
                    {
                        **flags(existing, row['documents_grounded']),
                        'mcp': {
                            'enabled': True,
                            'servers': [
                                mcp_servers[ref['id']]
                                for ref in access['resources']
                                if ref['type'] == 'mcp_server' and ref['id'] in mcp_servers
                            ],
                        },
                        'tools': tool_items(saved_tools.get(row['id'], [])),
                    }
                )
            return {'agents': rows}

    @router.get('/{agent_id}')
    def detail(agent_id: str):
        with database().connect() as conn:
            return read(conn, agent_id)

    @router.get('/{agent_id}/config')
    def configuration(agent_id: str, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        try:
            return get_agent_config(agent_id, conninfo=database().conninfo)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('/{agent_id}/options')
    def options(agent_id: str):
        """Every resource of the project by type, for the Resources picker."""
        with database().connect() as conn:
            if conn.execute('SELECT 1 FROM ka.agents WHERE id=%s', (agent_id,)).fetchone() is None:
                raise HTTPException(404, 'Agent not found')
            return project_resources(conn, store.project(conn, app_name))

    @router.put('/{agent_id}')
    def update(agent_id: str, value: ProfileInput, request: Request):
        try:
            with database().connect() as conn:
                if 'model_id' in value.model_fields_set:  # same lock order as Save model
                    conn.execute('SELECT singleton FROM ka.llm_assignment_revision FOR UPDATE')
                conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,))
                old = conn.execute(AGENT, (agent_id,)).fetchone()
                if old is None:
                    raise HTTPException(404, 'Agent not found')
                if old['revision'] != value.revision:
                    raise HTTPException(409, 'Agent changed. Reload before saving.')
                expert = old['expert'] if value.expert is None else value.expert
                if expert and not value.when_to_use.strip():
                    raise HTTPException(400, EXPERT_NEEDS_WHEN)
                model_id = str(value.model_id) if value.model_id else None
                new_model = 'model_id' in value.model_fields_set and model_id != (
                    str(old['model_id']) if old['model_id'] else None
                )
                if not new_model:
                    model_id = old['model_id']
                if new_model and model_id is not None:
                    check_model(conn, model_id)
                access = references(value.access, project_resources(conn, store.project(conn, app_name)))
                switch = {
                    **(
                        {'model_id': {'from': old['model_id'] and str(old['model_id']), 'to': model_id}}
                        if new_model
                        else {}
                    ),
                    **({'expert': expert} if expert != old['expert'] else {}),
                }
                actor = audit(conn, request, agent_id, 'agent.updated', **switch)
                grounded = old['documents_grounded'] if value.documents_grounded is None else value.documents_grounded
                conn.execute(
                    """UPDATE ka.agents SET name=%s,description=%s,when_to_use=%s,enabled=%s,documents_grounded=%s,
                    access=%s,instructions=%s,model_id=%s,expert=%s,revision=revision+1,updated_by=%s WHERE id=%s""",
                    (
                        value.name,
                        value.description,
                        value.when_to_use,
                        value.enabled,
                        grounded,
                        Jsonb(access),
                        value.instructions,
                        model_id,
                        expert,
                        actor,
                        agent_id,
                    ),
                )
                if new_model:
                    conn.execute('UPDATE ka.llm_assignment_revision SET revision=revision+1,updated_by=%s', (actor,))
                if value.subagents is not None and value.subagents != subagents_on(conn, agent_id):
                    switch_subagents(conn, agent_id, value.subagents)  # the UPDATE above bumped the revision
                return read(conn, agent_id)
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'An agent with this name already exists.') from None

    @router.put('/{agent_id}/prompt')
    def prompt(agent_id: str, value: PromptInput, request: Request):
        """The agent's system prompt, saved with its revision: what new conversations and runs use."""
        with database().connect() as conn:
            conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,))
            old = read(conn, agent_id)
            if old['revision'] != value.revision:
                raise HTTPException(409, PROMPT_STALE)
            actor = audit(conn, request, agent_id, 'agent.prompt.updated')
            conn.execute(
                'UPDATE ka.agents SET system_prompt=%s,revision=revision+1,updated_by=%s WHERE id=%s',
                (value.system_prompt, actor, agent_id),
            )
            return read(conn, agent_id)

    @router.get('/{agent_id}/history')
    def history(agent_id: str):
        with database().connect() as conn:
            read(conn, agent_id)
            return agent_history(conn, agent_id)

    @router.delete('/{agent_id}', status_code=204)
    def delete(agent_id: str, value: DeleteInput, request: Request):
        with database().connect() as conn:
            conn.execute('SELECT singleton FROM ka.llm_assignment_revision FOR UPDATE')
            conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,))
            old = read(conn, agent_id)
            if old['revision'] != value.revision:
                raise HTTPException(409, 'Agent changed. Reload before deleting.')
            if conn.execute(WORKING, {'a': agent_id}).fetchone():
                raise HTTPException(409, WORKING_REFUSED)
            history = agent_history(conn, agent_id)
            if any(history.values()):
                if not value.with_history:
                    raise HTTPException(409, HISTORY_REFUSED)
                delete_history(conn, agent_id)
            audit(conn, request, agent_id, 'agent.deleted', **({'history': history} if any(history.values()) else {}))
            conn.execute('DELETE FROM ka.agent_tools WHERE tool=%s', (f'expert:{agent_id}',))
            conn.execute('DELETE FROM ka.agents WHERE id=%s', (agent_id,))
            conn.execute('UPDATE ka.llm_assignment_revision SET revision=revision+1')
        return Response(status_code=204)

    @router.put('/{agent_id}/model')
    def model(agent_id: str, value: ModelInput, request: Request):
        with database().connect() as conn:
            conn.execute('SELECT singleton FROM ka.llm_assignment_revision FOR UPDATE')
            conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,))
            old = read(conn, agent_id)
            if old['revision'] != value.revision:
                raise HTTPException(409, 'Agent changed. Refresh before saving its model.')
            if value.model_id is not None:
                check_model(conn, value.model_id)
            actor = audit(conn, request, agent_id, 'agent.model.updated')
            conn.execute(
                'UPDATE ka.agents SET model_id=%s,revision=revision+1,updated_by=%s WHERE id=%s',
                (value.model_id, actor, agent_id),
            )
            conn.execute('UPDATE ka.llm_assignment_revision SET revision=revision+1,updated_by=%s', (actor,))
            return read(conn, agent_id)

    return router
