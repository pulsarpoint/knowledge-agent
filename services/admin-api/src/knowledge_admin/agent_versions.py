"""Saved specialist configurations: prepare a draft, test it, then activate it."""
import copy
import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.agents import get_agent_config
from knowledge_admin.knowledge_agents.config import CHOOSE_CLAUDE, model_fits


class ServerInstructions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    server_id: UUID
    instructions: str = Field(max_length=40000)


class InstructionChanges(BaseModel):
    model_config = ConfigDict(extra='forbid')
    system_prompt: str | None = Field(max_length=100000)
    instructions: str | None = Field(max_length=40000)
    server_instructions: list[ServerInstructions] = Field(max_length=200)


class TuningSuggestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    suggestion: str = Field(min_length=1)
    changes: InstructionChanges

    @field_validator('changes', mode='before')
    @classmethod
    def decode_changes(cls, value: Any) -> Any:
        # Bedrock structured output can JSON-encode this nested object.
        # Decode it once; InstructionChanges still validates every field.
        return json.loads(value) if isinstance(value, str) else value


class DraftInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=160)
    notes: str = Field(default='', max_length=20000)
    source_version_id: UUID | None = None
    source_review_id: UUID | None = None
    source_hash: str = Field(min_length=64, max_length=64)
    changes: InstructionChanges


# The configuration sections a per-run conversation narrows to its run resources.
RESOURCE_SECTIONS = ('code', 'documents', 'ssh', 'services')


class ActivateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_active_id: UUID | None


def build_api(app_name, store):
    router = APIRouter(prefix='/agents/{agent_id}/versions')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    def owner(conn, agent_id, *, lock=False):
        row = conn.execute('SELECT id,is_coordinator,runtime,active_config_version_id,next_config_version FROM ka.agents WHERE id=%s' + (' FOR UPDATE' if lock else ''), (agent_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Agent not found.')
        if row['is_coordinator']:
            raise HTTPException(409, 'Configuration versions are available for specialist agents.')
        return row

    def source(conn, agent_id, version_id, review_id):
        if version_id is not None and review_id is not None:
            raise HTTPException(422, 'Choose one draft source.')
        changes = {'system_prompt': None, 'instructions': None, 'server_instructions': []}
        suggestion = ''
        if review_id is not None:
            row = conn.execute('''SELECT r.context,r.suggestion,s.agent_version_id FROM ka.agent_session_reviews r
                JOIN ka.agent_sessions s ON s.id=r.session_id WHERE r.id=%s AND s.agent=%s''', (review_id,agent_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'Suggestion not found for this agent.')
            config = row['context']['agent_config']
            if 'run_resources' in config:
                # A per-run conversation pinned one run's resources: draft from the configuration it ran with, with
                # the narrowed resource lists replaced by the agent's own selection from the same source.
                try:
                    own = get_agent_config(agent_id, conninfo=store.conninfo, version_id=row['agent_version_id'],
                                           working_copy=row['agent_version_id'] is None)
                except (LookupError, ValueError) as exc:
                    raise HTTPException(409, str(exc)) from None
                config = {key: value for key, value in config.items() if key != 'run_resources'}
                config.update({section: own[section] for section in RESOURCE_SECTIONS})
            changes = row['context'].get('changes', changes)
            suggestion = row['suggestion']
        else:
            try:
                config = get_agent_config(agent_id, conninfo=store.conninfo, version_id=version_id, working_copy=version_id is None)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from None
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
        config = copy.deepcopy(config)
        config.pop('config_version', None)
        digest = hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return {'config': config, 'source_hash': digest, 'changes': changes, 'suggestion': suggestion}

    @router.get('')
    def listing(agent_id: str, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            row = owner(conn,agent_id)
            versions = conn.execute('''SELECT id,version,label,notes,created_at,published_at,source_review_id,
                CASE WHEN id=%s THEN 'active' WHEN published_at IS NULL THEN 'draft' ELSE 'archived' END AS status
                FROM ka.agent_versions WHERE agent=%s ORDER BY version DESC''', (row['active_config_version_id'],agent_id)).fetchall()
            return {'active_id':row['active_config_version_id'],'versions':versions}

    @router.get('/source')
    def draft_source(agent_id: str, response: Response, version_id: UUID | None = None, review_id: UUID | None = None):
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            owner(conn,agent_id)
            return source(conn,agent_id,version_id,review_id)

    @router.post('', status_code=201)
    def create(agent_id: str, value: DraftInput, request: Request):
        actor = request_actor(request)
        with database().connect() as conn:
            row = owner(conn,agent_id,lock=True)
            prepared = source(conn,agent_id,value.source_version_id,value.source_review_id)
            if prepared['source_hash'] != value.source_hash:
                raise HTTPException(409, 'The source configuration changed. Reload the draft before saving.')
            config = prepared['config']
            if value.changes.system_prompt is not None:
                if not value.changes.system_prompt.strip():
                    raise HTTPException(422, 'A system prompt is required.')
                config['system_prompt']['content'] = value.changes.system_prompt
            if value.changes.instructions is not None:
                config['instructions'] = value.changes.instructions
            servers = {server['id']: server for server in config['ssh']['servers']}
            for server in servers.values():
                if server['instructions_source'] == 'agent':
                    server['instructions'] = config['instructions']
            for change in value.changes.server_instructions:
                if str(change.server_id) not in servers:
                    raise HTTPException(422, 'Instructions refer to a server outside this configuration.')
                servers[str(change.server_id)]['instructions'] = change.instructions
                servers[str(change.server_id)]['instructions_source'] = 'agent_version'
            actor_id = store.actor(conn,*actor)
            number = row['next_config_version']
            if row['active_config_version_id'] is None:
                # Preserve current behavior before the first experimental draft is saved.
                baseline = get_agent_config(agent_id,conninfo=store.conninfo,working_copy=True)
                original = conn.execute('''INSERT INTO ka.agent_versions(agent,version,label,config,created_by,published_at)
                    VALUES (%s,%s,'Initial configuration',%s,%s,now()) RETURNING id''', (agent_id,number,Jsonb(baseline),actor_id)).fetchone()
                conn.execute('UPDATE ka.agents SET active_config_version_id=%s WHERE id=%s', (original['id'],agent_id))
                number += 1
            created = conn.execute('''INSERT INTO ka.agent_versions(agent,version,label,notes,config,created_by,source_review_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,version,label''',
                (agent_id,number,value.label,value.notes,Jsonb(config),actor_id,value.source_review_id)).fetchone()
            conn.execute('UPDATE ka.agents SET next_config_version=%s WHERE id=%s', (number+1,agent_id))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'agent_version.created','agent_version',%s,%s)''',
                (store.project(conn,app_name),actor_id,created['id'],Jsonb({'agent_id':agent_id,'version':number})))
            return created

    @router.post('/{version_id}/activate')
    def activate(agent_id: str, version_id: UUID, value: ActivateInput, request: Request):
        actor = request_actor(request)
        with database().connect() as conn:
            row = owner(conn,agent_id,lock=True)
            if row['active_config_version_id'] != value.expected_active_id:
                raise HTTPException(409, 'The active version changed. Reload versions before activating.')
            version = conn.execute('SELECT config FROM ka.agent_versions WHERE agent=%s AND id=%s', (agent_id,version_id)).fetchone()
            if version is None:
                raise HTTPException(404, 'Version not found for this agent.')
            config = version['config']
            if not config['system_prompt']['content'].strip() or config['model'] is None:
                raise HTTPException(409, 'A system prompt and model are required before activation.')
            if not model_fits(row['runtime'], config['model']):
                raise HTTPException(400, CHOOSE_CLAUDE if row['runtime'] == 'claude_code'
                                    else 'Choose an enabled model compatible with this agent.')
            conn.execute('UPDATE ka.agent_versions SET published_at=coalesce(published_at,now()) WHERE id=%s', (version_id,))
            conn.execute('UPDATE ka.agents SET active_config_version_id=%s WHERE id=%s', (version_id,agent_id))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'agent_version.activated','agent_version',%s,%s)''',
                (store.project(conn,app_name),store.actor(conn,*actor),version_id,Jsonb({'agent_id':agent_id})))
            return {'id':version_id}

    return router
