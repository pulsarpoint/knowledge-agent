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
from knowledge_admin.knowledge_agents.config import CHOOSE_CLAUDE, ClaudeCodeTool, get_agent_config, model_fits, normalize_tools, per_run
from knowledge_admin.knowledge_agents.run_resources import options as run_resource_options


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


HISTORY_REFUSED = 'This agent has sessions or run history. Delete it with its history, or disable it to keep the history.'
WORKING_REFUSED = ('This agent is working right now. Stop its documentation sessions, runs and board tasks, '
                   'or wait for them to finish, then delete it.')
COORDINATOR_FIXED = 'The coordinator uses fixed resources.'
# Anything still in progress for the agent, including a board task whose team it is on.
WORKING = '''SELECT 1 FROM ka.agent_sessions_cc WHERE agent_id=%(a)s AND status IN ('starting','working')
    UNION ALL SELECT 1 FROM ka.operations_runs WHERE agent=%(a)s AND status='running'
    UNION ALL SELECT 1 FROM ka.agent_board_tasks t WHERE t.status IN ('queued','running')
        AND (t.agent_id=%(a)s OR EXISTS (SELECT 1 FROM ka.agent_board_members m WHERE m.task_id=t.id AND m.agent_id=%(a)s))
    LIMIT 1'''

# Board tasks assigned to the agent and every task below them; tasks of other agents above them stay.
BOARD_TASKS = '''WITH RECURSIVE tree AS (SELECT id FROM ka.agent_board_tasks WHERE agent_id=%s
    UNION SELECT t.id FROM ka.agent_board_tasks t JOIN tree ON t.parent_id=tree.id) SELECT id FROM tree'''


def agent_history(conn, agent_id) -> tuple[dict[str, int], list]:
    """What deleting the agent with its history removes, and its board subtree; saved documents are not part of it."""
    tasks = [row['id'] for row in conn.execute(BOARD_TASKS, (agent_id,))]
    counts = conn.execute('''SELECT
        (SELECT count(*) FROM ka.agent_sessions WHERE agent=%(agent)s AND is_test)::int AS test_chats,
        (SELECT count(*) FROM ka.agent_sessions WHERE agent=%(agent)s AND NOT is_test)::int AS chats,
        (SELECT count(*) FROM ka.agent_sessions_cc WHERE agent_id=%(agent)s)::int AS claude_code_sessions,
        (SELECT count(*) FROM ka.operations_runs WHERE agent=%(agent)s)::int AS runs,
        cardinality(%(tasks)s::uuid[]) AS board_tasks,
        (SELECT count(*) FROM ka.agent_board_members
            WHERE agent_id=%(agent)s OR task_id=ANY(%(tasks)s::uuid[]))::int AS board_memberships''',
        {'agent': agent_id, 'tasks': tasks}).fetchone()
    return counts, tasks


def delete_history(conn, agent_id, tasks) -> None:
    """Remove the agent's sessions, runs, board subtree (tasks) and their run records, children before their parents."""
    # Run records have no foreign keys; their keys are run:<run id>, cc:<session id>:<turn> and board:<task id>.
    conn.execute('''DELETE FROM ka.agent_run_events WHERE run_key IN (
        SELECT 'run:' || id FROM ka.operations_runs WHERE agent=%s
        UNION ALL SELECT 'board:' || id FROM unnest(%s::uuid[]) AS id)
        OR (starts_with(run_key, 'cc:') AND split_part(run_key, ':', 2) IN (
            SELECT id::text FROM ka.agent_sessions_cc WHERE agent_id=%s))''', (agent_id, tasks, agent_id))
    conn.execute('''DELETE FROM ka.operations_commands WHERE run_id IN (
        SELECT id FROM ka.operations_runs WHERE agent=%s)''', (agent_id,))
    conn.execute('DELETE FROM ka.operations_runs WHERE agent=%s', (agent_id,))
    reviews = 'SELECT v.id FROM ka.agent_session_reviews v JOIN ka.agent_sessions s ON s.id=v.session_id WHERE s.agent=%s'
    conn.execute(f'UPDATE ka.agent_versions SET source_review_id=NULL WHERE source_review_id IN ({reviews})', (agent_id,))
    conn.execute(f'DELETE FROM ka.agent_session_reviews WHERE id IN ({reviews})', (agent_id,))
    conn.execute('DELETE FROM ka.agent_sessions WHERE agent=%s', (agent_id,))
    conn.execute('DELETE FROM ka.agent_sessions_cc WHERE agent_id=%s', (agent_id,))  # queued inputs cascade
    conn.execute('DELETE FROM ka.agent_board_members WHERE agent_id=%s OR task_id=ANY(%s)', (agent_id, tasks))
    # One statement: the parent_id references are checked once it has removed the whole subtree.
    conn.execute('DELETE FROM ka.agent_board_tasks WHERE id=ANY(%s)', (tasks,))


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
        row['active_prompt'] = conn.execute('''SELECT v.id,v.title,v.version FROM ka.agent_prompts p
            JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id WHERE p.agent=%s''', (agent_id,)).fetchone()
        return row

    def audit(conn, request, agent_id, action, **details):
        actor = store.actor(conn, *request_actor(request))
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
            VALUES (%s,%s,%s,'agent',%s)''', (store.project(conn, app_name), actor, action, Jsonb({'agent_id': agent_id, **details})))
        return actor

    @router.get('')
    def listing():
        with database().connect() as conn:
            rows = conn.execute('''SELECT a.*,m.name AS model_name,v.title AS prompt_title,v.version AS prompt_version,
                saved.config AS version_config,saved.version AS config_version
                FROM ka.agents a LEFT JOIN ka.llm_models m ON m.id=a.model_id
                LEFT JOIN ka.agent_prompts p ON p.agent=a.id LEFT JOIN ka.agent_prompt_versions v ON v.id=p.active_version_id
                LEFT JOIN ka.agent_versions saved ON saved.id=a.active_config_version_id
                ORDER BY a.is_coordinator DESC,a.created_at,a.id''').fetchall()
            for row in rows:
                config = row.pop('version_config')
                if config is not None:
                    row['resources'] = config.get('resources', 'fixed')  # versions saved before 0075 are fixed
                    row['claude_code_tools'] = config.get('claude_code_tools', row['claude_code_tools'])
                    for key in ('name','description','when_to_use'):
                        row[key] = config[key]
                    row['model_id'] = config['model']['id'] if config['model'] else None
                    row['model_name'] = config['model']['name'] if config['model'] else None
                    row['prompt_title'] = config['system_prompt']['title']
                    row['prompt_version'] = config['system_prompt']['version']
                    for flag, capability in (('documents_enabled','documents'),('repositories_enabled','code'),('ssh_enabled','ssh'),('skills_enabled','skills'),('mcp_enabled','mcp'),('services_enabled','services')):
                        row[flag] = config[capability]['enabled']
            return {'agents': rows}

    @router.get('/{agent_id}')
    def detail(agent_id: str):
        with database().connect() as conn:
            return read(conn, agent_id)

    @router.get('/{agent_id}/config')
    def configuration(agent_id: str, response: Response, version_id: UUID | None = None):
        response.headers['Cache-Control'] = 'no-store'
        try:
            return get_agent_config(agent_id, conninfo=database().conninfo, version_id=version_id)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('/{agent_id}/run-resources')
    def run_resources(agent_id: str, response: Response, version_id: UUID | None = None):
        """What a caller may pass to one run of a per-run agent: the Test tab picker and Generate documents."""
        response.headers['Cache-Control'] = 'no-store'
        try:
            config = get_agent_config(agent_id, conninfo=database().conninfo, version_id=version_id)
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
                if old['is_coordinator'] and not value.enabled:
                    raise HTTPException(400, 'The coordinator is always enabled and cannot be disabled.')
                if old['is_coordinator'] and any((value.documents_enabled,value.documents_grounded,value.repositories_enabled,value.ssh_enabled,
                        value.skills_enabled,value.mcp_enabled,value.services_enabled,value.collection_ids,value.repository_ids,
                        value.service_ids,value.skill_ids)):
                    raise HTTPException(400, 'Configure resource access on the specialists the coordinator delegates to.')
                if value.all_collections and value.collection_ids or value.all_repositories and value.repository_ids:
                    raise HTTPException(400, 'Choose all resources or specific resources, not both.')
                runtime = value.runtime or old['runtime']
                resources = value.resources or old['resources']
                if old['is_coordinator'] and resources != 'fixed':
                    raise HTTPException(400, COORDINATOR_FIXED)
                if runtime != old['runtime']:
                    if old['is_coordinator']:
                        raise HTTPException(400, 'The coordinator runs on the native runtime.')
                    if runtime == 'claude_code' and old['model_id'] is not None:
                        current = conn.execute('SELECT provider,api_format,model FROM ka.llm_models WHERE id=%s',
                                               (old['model_id'],)).fetchone()
                        if not model_fits('claude_code', current):
                            raise HTTPException(400, CHOOSE_CLAUDE)
                    if runtime == 'claude_code':
                        # The active version runs with its own stored model.
                        active = conn.execute('''SELECT v.config->'model' AS model FROM ka.agents a
                            JOIN ka.agent_versions v ON v.id=a.active_config_version_id WHERE a.id=%s''', (agent_id,)).fetchone()
                        if active is not None and active['model'] is not None and not model_fits('claude_code', active['model']):
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
                return read(conn, agent_id)
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'An agent with this name already exists.') from None

    @router.get('/{agent_id}/history')
    def history(agent_id: str):
        with database().connect() as conn:
            read(conn, agent_id)
            return agent_history(conn, agent_id)[0]

    @router.delete('/{agent_id}', status_code=204)
    def delete(agent_id: str, value: DeleteInput, request: Request):
        with database().connect() as conn:
            conn.execute('SELECT singleton FROM ka.llm_assignment_revision FOR UPDATE')
            conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (agent_id,))
            old = read(conn, agent_id)
            if old['is_coordinator']:
                raise HTTPException(409, 'The coordinator cannot be removed.')
            if old['revision'] != value.revision:
                raise HTTPException(409, 'Agent changed. Reload before deleting.')
            if conn.execute(WORKING, {'a': agent_id}).fetchone():
                raise HTTPException(409, WORKING_REFUSED)
            history, tasks = agent_history(conn, agent_id)
            if any(history.values()):
                if not value.with_history:
                    raise HTTPException(409, HISTORY_REFUSED)
                delete_history(conn, agent_id, tasks)
            conn.execute('SELECT agent FROM ka.agent_prompts WHERE agent=%s FOR UPDATE', (agent_id,))
            audit(conn, request, agent_id, 'agent.deleted', **({'history': history} if any(history.values()) else {}))
            credentials = conn.execute('DELETE FROM ka.agent_server_connections WHERE agent_id=%s RETURNING credential_id', (agent_id,)).fetchall()
            for table in ('agent_collections', 'agent_repositories', 'agent_services', 'skills_agents'):
                conn.execute(f'DELETE FROM ka.{table} WHERE agent_id=%s', (agent_id,))
            conn.execute('DELETE FROM ka.operations_servers WHERE agent=%s', (agent_id,))
            conn.execute('DELETE FROM ka.operations_settings WHERE agent=%s', (agent_id,))
            conn.execute('DELETE FROM ka.agent_configs WHERE agent=%s', (agent_id,))
            # Both sides of the required-prompt relationship are deferred.
            conn.execute('DELETE FROM ka.agent_prompts WHERE agent=%s', (agent_id,))
            conn.execute('DELETE FROM ka.agent_prompt_versions WHERE agent=%s', (agent_id,))
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
                if not model_fits(old['runtime'], selected, coordinator=old['is_coordinator']):
                    raise HTTPException(400, CHOOSE_CLAUDE if old['runtime'] == 'claude_code'
                                        else 'Choose an enabled model compatible with this agent.')
            actor = audit(conn, request, agent_id, 'agent.model.updated')
            conn.execute('UPDATE ka.agents SET model_id=%s,revision=revision+1,updated_by=%s WHERE id=%s', (value.model_id,actor,agent_id))
            conn.execute('UPDATE ka.llm_assignment_revision SET revision=revision+1,updated_by=%s', (actor,))
            return read(conn, agent_id)
    return router
