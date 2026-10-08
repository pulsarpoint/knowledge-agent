"""Clone an agent; apply a clone's configuration back to its original. An agent has one configuration (0080)."""
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.access import request_actor
from knowledge_admin.knowledge_agents.config import changed_sections, fingerprint, get_agent_config
from knowledge_admin.profiles_api import WORKING

NAME_TAKEN = 'An agent with this name already exists.'
NAME_LIMIT = 100
NOT_A_CLONE = 'This agent is not a clone.'
APPLY_WORKING = ('The original agent is working right now. Wait for its documentation sessions and runs to finish, '
                 'then apply.')
ORIGINAL_CHANGED = 'The original changed; review again.'
# Everything an agent runs with on ka.agents. Name, description, enabled and cloned_from belong to the agent itself.
CONFIGURATION = ('when_to_use', 'model_id', 'runtime', 'claude_code_tools', 'resources', 'system_prompt',
                 'documents_enabled', 'documents_grounded', 'repositories_enabled', 'ssh_enabled', 'skills_enabled',
                 'mcp_enabled', 'services_enabled', 'all_collections', 'all_repositories')
SELECTIONS = (('agent_collections', 'collection_id'), ('agent_repositories', 'repository_id'),
              ('agent_services', 'service_id'))


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    fingerprint: str = Field(min_length=64, max_length=64)


def clone_name(conn, name: str) -> str:
    """"<name> (copy)", then "(copy 2)", "(copy 3)" … while taken; shortened to the 100-character name limit."""
    taken = {row['name'] for row in conn.execute('SELECT name FROM ka.agents')}
    number = 1
    while True:
        suffix = ' (copy)' if number == 1 else f' (copy {number})'
        candidate = name[:NAME_LIMIT - len(suffix)].rstrip() + suffix
        if candidate not in taken:
            return candidate
        number += 1


def copy_configuration(conn, source_id: str, target_id: str, actor_id) -> None:
    """Make the target run exactly what the source runs: every configuration column, selection, settings row,
    per-server override, agent SSH connection and tool. Saved credentials are referenced, never duplicated. Bumps the
    target's revisions."""
    columns = ','.join(f'{column}=s.{column}' for column in CONFIGURATION)
    conn.execute(f'''UPDATE ka.agents t SET {columns},revision=t.revision+1,updated_by=%s
        FROM ka.agents s WHERE s.id=%s AND t.id=%s''', (actor_id, source_id, target_id))
    for table, column in SELECTIONS:
        conn.execute(f'DELETE FROM ka.{table} WHERE agent_id=%s', (target_id,))
        conn.execute(f'INSERT INTO ka.{table}(agent_id,{column}) SELECT %s,{column} FROM ka.{table} WHERE agent_id=%s',
                     (target_id, source_id))
    conn.execute('DELETE FROM ka.skills_agents WHERE agent_id=%s', (target_id,))
    conn.execute('''INSERT INTO ka.skills_agents(agent_id,skill_id,project_id)
        SELECT %s,skill_id,project_id FROM ka.skills_agents WHERE agent_id=%s''', (target_id, source_id))
    conn.execute('DELETE FROM ka.agent_tools WHERE agent_id=%s', (target_id,))
    conn.execute('''INSERT INTO ka.agent_tools(agent_id,tool,mode,usage,subagent_allowed)
        SELECT %s,tool,mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s''', (target_id, source_id))
    conn.execute('DELETE FROM ka.agent_configs WHERE agent=%s', (target_id,))  # their skill and MCP rows cascade
    conn.execute('DELETE FROM ka.operations_servers WHERE agent=%s', (target_id,))
    replaced = conn.execute('DELETE FROM ka.agent_server_connections WHERE agent_id=%s RETURNING credential_id',
                            (target_id,)).fetchall()
    conn.execute('''INSERT INTO ka.operations_settings(project_id,agent,updated_by)
        SELECT project_id,%s,%s FROM ka.operations_settings WHERE agent=%s
        ON CONFLICT (project_id,agent) DO UPDATE SET revision=ka.operations_settings.revision+1,
            updated_by=EXCLUDED.updated_by''', (target_id, actor_id, source_id))
    conn.execute('''DELETE FROM ka.operations_settings t WHERE t.agent=%s AND NOT EXISTS (
        SELECT 1 FROM ka.operations_settings s WHERE s.agent=%s AND s.project_id=t.project_id)''', (target_id, source_id))
    conn.execute('''INSERT INTO ka.operations_servers(project_id,agent,server_id)
        SELECT project_id,%s,server_id FROM ka.operations_servers WHERE agent=%s''', (target_id, source_id))
    conn.execute('''INSERT INTO ka.agent_server_connections(agent_id,server_id,project_id,username,port,allow_sudo,
            known_hosts,credential_id,credential_kind,updated_by)
        SELECT %s,server_id,project_id,username,port,allow_sudo,known_hosts,credential_id,credential_kind,%s
        FROM ka.agent_server_connections WHERE agent_id=%s''', (target_id, actor_id, source_id))
    for row in conn.execute('''SELECT id,project_id,server_id,instructions,inherit_skills,inherit_mcp_servers
            FROM ka.agent_configs WHERE agent=%s''', (source_id,)).fetchall():
        copied = conn.execute('''INSERT INTO ka.agent_configs(project_id,agent,server_id,instructions,inherit_skills,
                inherit_mcp_servers,updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
            (row['project_id'], target_id, row['server_id'], row['instructions'], row['inherit_skills'],
             row['inherit_mcp_servers'], actor_id)).fetchone()['id']
        for table, column in (('agent_config_skills', 'skill_id'), ('agent_config_mcp_servers', 'mcp_server_id')):
            conn.execute(f'''INSERT INTO ka.{table}(config_id,project_id,{column})
                SELECT %s,project_id,{column} FROM ka.{table} WHERE config_id=%s''', (copied, row['id']))
    for credential in {row['credential_id'] for row in replaced} - {None}:  # a key nothing uses any more (as delete)
        conn.execute('''DELETE FROM ka.credentials WHERE id=%s
            AND NOT EXISTS (SELECT 1 FROM ka.servers WHERE credential_id=%s)
            AND NOT EXISTS (SELECT 1 FROM ka.agent_server_connections WHERE credential_id=%s)''',
            (credential, credential, credential))


def clone(conn, source_id: str, actor_id) -> dict[str, str]:
    """A new agent with the source's configuration and on/off switch, named after it. History is not copied."""
    source = conn.execute('SELECT name,description,enabled,system_prompt FROM ka.agents WHERE id=%s FOR UPDATE',
                          (source_id,)).fetchone()
    if source is None:
        raise LookupError('Agent not found')
    created = {'id': str(uuid4()), 'name': clone_name(conn, source['name'])}
    conn.execute('''INSERT INTO ka.agents(id,name,description,enabled,system_prompt,cloned_from,created_by,updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
        (created['id'], created['name'], source['description'], source['enabled'], source['system_prompt'], source_id,
         actor_id, actor_id))
    copy_configuration(conn, source_id, created['id'], actor_id)
    return created


def build_api(app_name, store):
    router = APIRouter(prefix='/agents/{agent_id}')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    def audit(conn, actor, action: str, details: dict) -> None:
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
            VALUES (%s,%s,%s,'agent',%s)''', (store.project(conn, app_name), actor, action, Jsonb(details)))

    @router.post('/clone', status_code=201)
    def clone_agent(agent_id: str, request: Request):
        try:
            with database().connect() as conn:
                actor = store.actor(conn, *request_actor(request))
                try:
                    created = clone(conn, agent_id, actor)
                except LookupError:
                    raise HTTPException(404, 'Agent not found') from None
                audit(conn, actor, 'agent.cloned', {'agent_id': created['id'], 'cloned_from': agent_id})
                return created
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, NAME_TAKEN) from None

    def config(agent_id: str) -> dict:
        try:
            return get_agent_config(agent_id, conninfo=store.conninfo)
        except LookupError:
            raise HTTPException(404, 'Agent not found') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    def original_of(conn, agent_id: str) -> dict:
        row = conn.execute('''SELECT o.id,o.name,o.revision FROM ka.agents c LEFT JOIN ka.agents o ON o.id=c.cloned_from
            WHERE c.id=%s''', (agent_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Agent not found')
        if row['id'] is None:  # never a clone, or its original was deleted
            raise HTTPException(404, NOT_A_CLONE)
        return row

    @router.get('/apply')
    def preview(agent_id: str, response: Response):
        """What Apply would replace on the original, and the fingerprint the dialog sends back."""
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            original = original_of(conn, agent_id)
        target = config(original['id'])
        return {'original': original, 'fingerprint': fingerprint(target),
                'changes': changed_sections(config(agent_id), target)}

    @router.post('/apply')
    def apply(agent_id: str, value: ApplyInput, request: Request):
        """Copy the clone's configuration onto its original in one transaction (not its name, description, on/off
        switch or cloned_from)."""
        with database().connect() as conn:
            original = original_of(conn, agent_id)
            for row_id in sorted((agent_id, original['id'])):
                conn.execute('SELECT id FROM ka.agents WHERE id=%s FOR UPDATE', (row_id,))
            original = original_of(conn, agent_id)  # again, under the locks
            if conn.execute(WORKING, {'a': original['id']}).fetchone():
                raise HTTPException(409, APPLY_WORKING)
            # The fingerprint is read on a separate connection, after the locks: writers of the original wait on them.
            if original['revision'] != value.revision or fingerprint(config(original['id'])) != value.fingerprint:
                raise HTTPException(409, ORIGINAL_CHANGED)
            actor = store.actor(conn, *request_actor(request))
            copy_configuration(conn, agent_id, original['id'], actor)
            audit(conn, actor, 'agent.config.applied', {'agent_id': original['id'], 'clone_id': agent_id})
            return conn.execute('SELECT id,revision FROM ka.agents WHERE id=%s', (original['id'],)).fetchone()

    return router
