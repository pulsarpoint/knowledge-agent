"""Draft system prompts and explicit, atomic activation per agent."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.repositories import RepositoryStore

AgentName = str


class DraftInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=40000)

    @field_validator('title', 'content')
    @classmethod
    def valid_text(cls, value: str) -> str:
        if not value.strip() or '\x00' in value:
            raise ValueError('Enter nonempty text without null characters')
        return value.strip()


class DraftUpdate(DraftInput):
    revision: int = Field(ge=1, strict=True)


class ActivateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    prompt_revision: int = Field(ge=1, strict=True)


class DeleteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    prompt_revision: int = Field(ge=1, strict=True)
    confirmed: bool = Field(default=False, strict=True)


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/llm/prompts')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Prompt storage is not configured')
        return store

    def record(conn, db, request, action, row):
        subject, email = request_actor(request)
        actor = db.actor(conn, subject, email)
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,'agent_prompt',%s,%s)''',
            (db.project(conn, app_name), actor, action, row['id'],
             Jsonb({'agent': row['agent'], 'version': row['version']})))
        return actor

    @router.get('')
    def list_prompts():
        with database().connect() as conn:
            return {
                'agents': conn.execute('SELECT * FROM ka.agent_prompts ORDER BY agent').fetchall(),
                'versions': conn.execute('SELECT * FROM ka.agent_prompt_version_details ORDER BY agent,version DESC').fetchall(),
            }

    @router.post('/{agent}/versions', status_code=201)
    def create_draft(agent: AgentName, value: DraftInput, request: Request):
        db = database()
        with db.connect() as conn:
            number = conn.execute('''UPDATE ka.agent_prompts SET next_version=next_version+1
                WHERE agent=%s RETURNING next_version-1 AS number''', (agent,)).fetchone()
            if number is None:
                raise HTTPException(404, 'Agent not found')
            number = number['number']
            subject, email = request_actor(request)
            actor = db.actor(conn, subject, email)
            row = conn.execute('''INSERT INTO ka.agent_prompt_versions(agent,version,title,content,created_by,updated_by)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING *''', (agent, number, value.title, value.content, actor, actor)).fetchone()
            record(conn, db, request, 'prompt.draft.created', row)
            return conn.execute('SELECT * FROM ka.agent_prompt_version_details WHERE id=%s', (row['id'],)).fetchone()

    @router.put('/{agent}/versions/{version_id}')
    def update_draft(agent: AgentName, version_id: UUID, value: DraftUpdate, request: Request):
        db = database()
        with db.connect() as conn:
            conn.execute('SELECT agent FROM ka.agent_prompts WHERE agent=%s FOR UPDATE', (agent,)).fetchone()
            row = conn.execute('SELECT * FROM ka.agent_prompt_versions WHERE agent=%s AND id=%s FOR UPDATE', (agent, version_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'Prompt version not found')
            if row['published_at'] is not None:
                raise HTTPException(409, 'Published versions are read-only. Create a new draft to edit them.')
            if row['revision'] != value.revision:
                raise HTTPException(409, 'This draft changed. Reload before saving.')
            actor = record(conn, db, request, 'prompt.draft.updated', row)
            conn.execute('''UPDATE ka.agent_prompt_versions SET title=%s,content=%s,revision=revision+1,updated_by=%s
                WHERE id=%s''', (value.title, value.content, actor, version_id))
            return conn.execute('SELECT * FROM ka.agent_prompt_version_details WHERE id=%s', (version_id,)).fetchone()

    @router.delete('/{agent}/versions/{version_id}', status_code=204)
    def delete_version(agent: AgentName, version_id: UUID, value: DeleteInput, request: Request) -> Response:
        if not value.confirmed:
            raise HTTPException(400, 'Confirm prompt version deletion before continuing')
        db = database()
        with db.connect() as conn:
            # Match activation's lock order so activation and deletion cannot both succeed.
            prompt = conn.execute('SELECT * FROM ka.agent_prompts WHERE agent=%s FOR UPDATE', (agent,)).fetchone()
            row = conn.execute('SELECT * FROM ka.agent_prompt_versions WHERE agent=%s AND id=%s FOR UPDATE', (agent, version_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'Prompt version not found')
            if prompt['active_version_id'] == version_id:
                raise HTTPException(409, 'The active prompt version cannot be deleted. Activate another version first.')
            if prompt['revision'] != value.prompt_revision or row['revision'] != value.revision:
                raise HTTPException(409, 'The prompt or version changed. Cancel, reload, and try again.')
            record(conn, db, request, 'prompt.deleted', row)
            conn.execute('DELETE FROM ka.agent_prompt_versions WHERE id=%s', (version_id,))
        return Response(status_code=204)

    @router.post('/{agent}/versions/{version_id}/activate')
    def activate(agent: AgentName, version_id: UUID, value: ActivateInput, request: Request):
        db = database()
        with db.connect() as conn:
            prompt = conn.execute('SELECT * FROM ka.agent_prompts WHERE agent=%s FOR UPDATE', (agent,)).fetchone()
            row = conn.execute('SELECT * FROM ka.agent_prompt_versions WHERE agent=%s AND id=%s FOR UPDATE', (agent, version_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'Prompt version not found')
            if prompt['revision'] != value.prompt_revision or row['revision'] != value.revision:
                raise HTTPException(409, 'The prompt or draft changed. Reload before activating.')
            if prompt['active_version_id'] == version_id:
                return conn.execute('SELECT * FROM ka.agent_prompt_version_details WHERE id=%s', (version_id,)).fetchone()
            actor = record(conn, db, request, 'prompt.activated', row)
            if row['published_at'] is None:
                conn.execute('UPDATE ka.agent_prompt_versions SET published_at=now(),updated_by=%s WHERE id=%s', (actor, version_id))
            conn.execute('''UPDATE ka.agent_prompts SET active_version_id=%s,revision=revision+1,updated_by=%s
                WHERE agent=%s''', (version_id, actor, agent))
            return conn.execute('SELECT * FROM ka.agent_prompt_version_details WHERE id=%s', (version_id,)).fetchone()

    return router
