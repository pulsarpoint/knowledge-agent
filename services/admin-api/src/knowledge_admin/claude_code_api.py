"""Claude Code agents (registrations: a name and a description) and their interactive documentation sessions."""
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.repositories import RepositoryStore

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

    return router
