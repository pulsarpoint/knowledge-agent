"""Named agent profiles and their explicit knowledge and asset selections."""
from uuid import UUID
from typing import Literal

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.knowledge_agents.config import CHOOSE_CLAUDE, ClaudeCodeTool, get_agent_config, model_fits, normalize_tools, per_run, tool_items
from knowledge_admin.knowledge_agents.run_resources import options as run_resource_options
from knowledge_admin.tools.catalog import tools_summary
from knowledge_admin.tools_api import ensure_native_tools, flags, switch_on_defaults


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    revision: int = Field(ge=1, strict=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=4000)
    when_to_use: str = Field(max_length=4000)
    enabled: bool = Field(strict=True)
    all_collections: bool = Field(strict=True)
    all_repositories: bool = Field(strict=True)
    collection_ids: list[UUID] = Field(max_length=200)
    repository_ids: list[UUID] = Field(max_length=200)
    service_ids: list[UUID] = Field(max_length=100)
    skill_ids: list[UUID] | None = Field(default=None, max_length=30)
    documents_enabled: bool = Field(strict=True)
    documents_grounded: bool | None = Field(default=None, strict=True)
    repositories_enabled: bool = Field(strict=True)
    ssh_enabled: bool = Field(strict=True)
    skills_enabled: bool = Field(strict=True)
    mcp_enabled: bool = Field(strict=True)
    services_enabled: bool = Field(strict=True)
    runtime: Literal['openai', 'claude_code'] | None = None  # None keeps the agent type
    claude_code_tools: list[ClaudeCodeTool] | None = Field(default=None, max_length=8)  # None keeps the saved tools
    resources: Literal['fixed', 'per_run'] | None = None  # None keeps the setting

    @field_validator('collection_ids','repository_ids','service_ids','skill_ids')
    @classmethod
    def unique(cls, value):
        if value is not None and len(value) != len(set(value)):
            raise ValueError('Selections must be unique')
        return value


class DeleteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    confirmed: Literal[True]
    with_history: bool = Field(default=False, strict=True)


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


HISTORY_REFUSED = 'This agent has sessions or run history. Delete it with its history, or disable it to keep the history.'
WORKING_REFUSED = ('This agent is working right now. Stop its documentation sessions and runs, '
                   'or wait for them to finish, then delete it.')
# Anything still in progress for the agent.
WORKING = '''SELECT 1 FROM ka.agent_sessions_cc WHERE agent_id=%(a)s AND status IN ('starting','working')
    UNION ALL SELECT 1 FROM ka.operations_runs WHERE agent=%(a)s AND status='running'
    LIMIT 1'''


def agent_history(conn, agent_id) -> dict[str, int]:
    """What deleting the agent with its history removes; saved documents are not part of it."""
    return conn.execute('''SELECT
        (SELECT count(*) FROM ka.agent_sessions WHERE agent=%(agent)s AND is_test)::int AS test_chats,
        (SELECT count(*) FROM ka.agent_sessions WHERE agent=%(agent)s AND NOT is_test)::int AS chats,
        (SELECT count(*) FROM ka.agent_sessions_cc WHERE agent_id=%(agent)s)::int AS claude_code_sessions,
        (SELECT count(*) FROM ka.operations_runs WHERE agent=%(agent)s)::int AS runs''',
        {'agent': agent_id}).fetchone()


def delete_history(conn, agent_id) -> None:
    """Remove the agent's sessions, runs and their run records, children before their parents."""
    # Run records have no foreign keys; their keys are run:<run id> and cc:<session id>:<turn>.
    conn.execute('''DELETE FROM ka.agent_run_events WHERE run_key IN (
        SELECT 'run:' || id FROM ka.operations_runs WHERE agent=%s)
        OR (starts_with(run_key, 'cc:') AND split_part(run_key, ':', 2) IN (
            SELECT id::text FROM ka.agent_sessions_cc WHERE agent_id=%s))''', (agent_id, agent_id))
    conn.execute('''DELETE FROM ka.operations_commands WHERE run_id IN (
        SELECT id FROM ka.operations_runs WHERE agent=%s)''', (agent_id,))
    conn.execute('DELETE FROM ka.operations_runs WHERE agent=%s', (agent_id,))
    reviews = 'SELECT v.id FROM ka.agent_session_reviews v JOIN ka.agent_sessions s ON s.id=v.session_id WHERE s.agent=%s'
    conn.execute(f'DELETE FROM ka.agent_session_reviews WHERE id IN ({reviews})', (agent_id,))
    conn.execute('DELETE FROM ka.agent_sessions WHERE agent=%s', (agent_id,))
    conn.execute('DELETE FROM ka.agent_sessions_cc WHERE agent_id=%s', (agent_id,))  # queued inputs cascade


def build_api(app_name, store):
    router = APIRouter(prefix='/agents')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    def read(conn, agent_id):
        try:
            row = profile(conn, agent_id)
        except ModelUnavailable:
            raise HTTPException(404, 'Agent not found') from None
        row['collection_ids'] = [r['collection_id'] for r in conn.execute('SELECT a.collection_id FROM ka.agent_collections a JOIN ka.rag_collections c ON c.id=a.collection_id WHERE a.agent_id=%s AND c.deleted_at IS NULL ORDER BY a.collection_id', (agent_id,))]
        row['service_ids'] = [r['id'] for r in row['services']]
        row['skill_ids'] = [r['id'] for r in row['skills']]
        row['original'] = conn.execute('SELECT id,name FROM ka.agents WHERE id=%s', (row['cloned_from'],)).fetchone() if row['cloned_from'] else None
        return row

    def audit(conn, request, agent_id, action, **details):
        actor = store.actor(conn, *request_actor(request))
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
            VALUES (%s,%s,%s,'agent',%s)''', (store.project(conn, app_name), actor, action, Jsonb({'agent_id': agent_id, **details})))
        return actor

    @router.get('')
    def listing():
        with database().connect() as conn:
            rows = conn.execute('''SELECT a.*,m.name AS model_name,o.name AS original_name,
                (SELECT count(*) FROM ka.agent_repositories x JOIN ka.repositories r ON r.id=x.repository_id
                    WHERE x.agent_id=a.id AND r.archived_at IS NULL)::int AS code_count,
                (SELECT count(*) FROM ka.agent_collections x JOIN ka.rag_collections c ON c.id=x.collection_id
                    WHERE x.agent_id=a.id AND c.deleted_at IS NULL)::int AS collection_count,
                (SELECT count(*) FROM ka.operations_servers x JOIN ka.servers s ON s.id=x.server_id
                    LEFT JOIN ka.agent_server_connections c ON c.server_id=s.id AND c.agent_id=x.agent
                    WHERE x.agent=a.id AND s.ssh_allowed AND CASE WHEN c.server_id IS NULL THEN s.enabled
                        ELSE c.credential_id IS NOT NULL AND c.username IS NOT NULL END)::int AS server_count,
                (SELECT count(*) FROM ka.agent_services x WHERE x.agent_id=a.id)::int AS service_count
                FROM ka.agents a LEFT JOIN ka.llm_models m ON m.id=a.model_id
                LEFT JOIN ka.agents o ON o.id=a.cloned_from
                ORDER BY a.created_at,a.id''').fetchall()
            saved_tools: dict[str, list[dict]] = {}
            for tool in conn.execute('SELECT agent_id,tool,mode,usage FROM ka.agent_tools'):
                saved_tools.setdefault(tool['agent_id'], []).append(tool)
            agent_mcp: dict[str, list[dict]] = {}
            for server in conn.execute('''SELECT c.agent,m.id::text AS id,m.tools FROM ka.agent_configs c
                    JOIN ka.agent_config_mcp_servers s ON s.config_id=c.id JOIN ka.mcp_servers m ON m.id=s.mcp_server_id
                    WHERE c.server_id IS NULL AND m.enabled'''):
                agent_mcp.setdefault(server['agent'], []).append(server)
            for row in rows:
                row.pop('system_prompt')  # read with the agent, not in the list
                code_count, collection_count = row.pop('code_count'), row.pop('collection_count')
                limits = {'code': 'any' if row['all_repositories'] else code_count,
                          'documents': 'any' if row['all_collections'] else collection_count,
                          'servers': row.pop('server_count'), 'services': row.pop('service_count')}
                row['tools_summary'] = tools_summary({
                    **flags(row), 'claude_code_tools': row['claude_code_tools'],
                    'mcp': {'enabled': row['mcp_enabled'], 'servers': agent_mcp.get(row['id'], [])},
                    'tools': tool_items(saved_tools.get(row['id'], []))})
                enabled = {'code': row['repositories_enabled'], 'documents': row['documents_enabled'],
                           'servers': row['ssh_enabled'], 'services': row['services_enabled']}
                row['accepts'] = ([{'kind': kind, 'limit': limit} for kind, limit in limits.items() if enabled[kind]]
                                  if row['resources'] == 'per_run' else [])
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

    @router.get('/{agent_id}/run-resources')
    def run_resources(agent_id: str, response: Response):
        """What a caller may pass to one run of a per-run agent: the Test tab picker and Generate documents."""
        response.headers['Cache-Control'] = 'no-store'
        try:
            config = get_agent_config(agent_id, conninfo=database().conninfo)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        if not per_run(config):
            return {'resources': 'fixed'}
        with database().connect() as conn:
            return {'resources': 'per_run', **run_resource_options(conn, store.project(conn, app_name), config)}

    @router.get('/{agent_id}/options')
    def options(agent_id: str):
        with database().connect() as conn:
            read(conn, agent_id)
            project = store.project(conn, app_name)
            return {
                'collections': conn.execute('SELECT id,name,description FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name', (project,)).fetchall(),
                'repositories': conn.execute('SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL ORDER BY name', (project,)).fetchall(),
                'services': conn.execute('SELECT id,name,description FROM ka.services WHERE project_id=%s ORDER BY name', (project,)).fetchall(),
                'skills': conn.execute('SELECT id,name,description,enabled FROM ka.agent_skills WHERE project_id=%s ORDER BY name', (project,)).fetchall(),
            }

    @router.put('/{agent_id}')
    def update(agent_id: str, value: ProfileInput, request: Request):
        try:
            with database().connect() as conn:
                conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,))
                old = read(conn, agent_id)
                if old['revision'] != value.revision:
                    raise HTTPException(409, 'Agent changed. Reload before saving.')
                if value.all_collections and value.collection_ids or value.all_repositories and value.repository_ids:
                    raise HTTPException(400, 'Choose all resources or specific resources, not both.')
                runtime = value.runtime or old['runtime']
                resources = value.resources or old['resources']
                if runtime != old['runtime']:
                    if runtime == 'claude_code' and old['model_id'] is not None:
                        current = conn.execute('SELECT provider,api_format,model FROM ka.llm_models WHERE id=%s',
                                               (old['model_id'],)).fetchone()
                        if not model_fits('claude_code', current):
                            raise HTTPException(400, CHOOSE_CLAUDE)
                tools = normalize_tools(value.claude_code_tools) if value.claude_code_tools is not None else old['claude_code_tools']
                project = store.project(conn, app_name)
                switch = {**({'runtime': {'from': old['runtime'], 'to': runtime}} if runtime != old['runtime'] else {}),
                          **({'resources': {'from': old['resources'], 'to': resources}} if resources != old['resources'] else {})}
                actor = audit(conn, request, agent_id, 'agent.updated', **switch)
                if value.skill_ids is not None and set(value.skill_ids) != set(old['skill_ids']):
                    skills = conn.execute('SELECT id FROM ka.agent_skills WHERE project_id=%s AND enabled AND id=ANY(%s) FOR SHARE', (project, value.skill_ids)).fetchall()
                    if len(skills) != len(value.skill_ids):
                        raise HTTPException(400, 'Select enabled skills.')
                    conn.execute('DELETE FROM ka.skills_agents WHERE agent_id=%s', (agent_id,))
                    for skill_id in value.skill_ids:
                        conn.execute('INSERT INTO ka.skills_agents(agent_id,skill_id,project_id) VALUES (%s,%s,%s)', (agent_id,skill_id,project))
                    conn.execute('UPDATE ka.operations_settings SET revision=revision+1,updated_by=%s WHERE project_id=%s AND agent=%s', (actor,project,agent_id))
                for table, column, source, ids, condition in [
                    ('agent_collections','collection_id','rag_collections',value.collection_ids,' AND deleted_at IS NULL'),
                    ('agent_repositories','repository_id','repositories',value.repository_ids,' AND archived_at IS NULL'),
                    ('agent_services','service_id','services',value.service_ids,''),
                ]:
                    found = conn.execute(f'SELECT id FROM ka.{source} WHERE project_id=%s AND id=ANY(%s){condition} FOR SHARE', (project, ids)).fetchall()
                    if len(found) != len(ids):
                        raise HTTPException(400, 'A selected resource is unavailable. Reload the available options.')
                    conn.execute(f'DELETE FROM ka.{table} WHERE agent_id=%s', (agent_id,))
                    for resource_id in ids:
                        conn.execute(f'INSERT INTO ka.{table}(agent_id,{column}) VALUES (%s,%s)', (agent_id, resource_id))
                grounded = old['documents_grounded'] if value.documents_grounded is None else value.documents_grounded
                conn.execute('''UPDATE ka.agents SET name=%s,description=%s,when_to_use=%s,enabled=%s,all_collections=%s,
                    all_repositories=%s,documents_enabled=%s,documents_grounded=%s,repositories_enabled=%s,ssh_enabled=%s,skills_enabled=%s,mcp_enabled=%s,services_enabled=%s,runtime=%s,claude_code_tools=%s,resources=%s,revision=revision+1,updated_by=%s WHERE id=%s''',
                    (value.name,value.description,value.when_to_use,value.enabled,value.all_collections,value.all_repositories,value.documents_enabled,grounded,value.repositories_enabled,value.ssh_enabled,value.skills_enabled,value.mcp_enabled,value.services_enabled,runtime,tools,resources,actor,agent_id))
                now = flags({'runtime': runtime, 'repositories_enabled': value.repositories_enabled,
                             'documents_enabled': value.documents_enabled, 'documents_grounded': grounded,
                             'ssh_enabled': value.ssh_enabled})
                if runtime == 'openai' and old['runtime'] != 'openai':
                    ensure_native_tools(conn, agent_id, now)
                switch_on_defaults(conn, agent_id, flags(old), now)
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
            conn.execute('UPDATE ka.agents SET system_prompt=%s,revision=revision+1,updated_by=%s WHERE id=%s',
                         (value.system_prompt, actor, agent_id))
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
            credentials = conn.execute('DELETE FROM ka.agent_server_connections WHERE agent_id=%s RETURNING credential_id', (agent_id,)).fetchall()
            for table in ('agent_collections', 'agent_repositories', 'agent_services', 'skills_agents'):
                conn.execute(f'DELETE FROM ka.{table} WHERE agent_id=%s', (agent_id,))
            conn.execute('DELETE FROM ka.operations_servers WHERE agent=%s', (agent_id,))
            conn.execute('DELETE FROM ka.operations_settings WHERE agent=%s', (agent_id,))
            conn.execute('DELETE FROM ka.agent_configs WHERE agent=%s', (agent_id,))
            conn.execute('DELETE FROM ka.agents WHERE id=%s', (agent_id,))
            for row in credentials:
                credential = row['credential_id']
                if credential is not None:
                    conn.execute('''DELETE FROM ka.credentials WHERE id=%s
                        AND NOT EXISTS (SELECT 1 FROM ka.servers WHERE credential_id=%s)
                        AND NOT EXISTS (SELECT 1 FROM ka.agent_server_connections WHERE credential_id=%s)''',
                        (credential, credential, credential))
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
                selected = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s FOR SHARE', (value.model_id,)).fetchone()
                if selected is None or not selected['enabled'] or selected['api_format'] == 'embeddings':
                    raise HTTPException(400, 'Choose an enabled model compatible with this agent.')
                if not model_fits(old['runtime'], selected):
                    raise HTTPException(400, CHOOSE_CLAUDE if old['runtime'] == 'claude_code'
                                        else 'Choose an enabled model compatible with this agent.')
            actor = audit(conn, request, agent_id, 'agent.model.updated')
            conn.execute('UPDATE ka.agents SET model_id=%s,revision=revision+1,updated_by=%s WHERE id=%s', (value.model_id,actor,agent_id))
            conn.execute('UPDATE ka.llm_assignment_revision SET revision=revision+1,updated_by=%s', (actor,))
            return read(conn, agent_id)
    return router
