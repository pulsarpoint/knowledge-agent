"""Versioned analysis and writing templates for documentation generation."""

from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.repositories import RepositoryStore


class PromptInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=4000)
    input_kind: Literal['code', 'server', 'service', 'mixed']
    enabled: bool = Field(default=True, strict=True)
    content: str = Field(min_length=1, max_length=100000)
    revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator('name', 'description', 'content')
    @classmethod
    def valid_text(cls, value: str, info) -> str:
        if '\x00' in value or (info.field_name != 'description' and not value.strip()):
            raise ValueError('Enter nonempty text without null characters')
        return value if info.field_name == 'content' else value.strip()


class PromptRevision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/documentation/prompts')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Prompt storage is not configured')
        return store

    def audit(conn, db, request, project, prompt_id, action, details):
        subject,email = request_actor(request)
        actor = db.actor(conn,subject,email)
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,'documentation_prompt',%s,%s)''', (project,actor,action,prompt_id,Jsonb(details)))
        return actor

    def locked_prompt(conn, project, prompt_id, revision):
        row = conn.execute('SELECT * FROM ka.documentation_prompts WHERE id=%s AND project_id=%s FOR UPDATE', (prompt_id,project)).fetchone()
        if row is None:
            raise HTTPException(404, 'Documentation prompt not found')
        if row['revision'] != revision:
            raise HTTPException(409, 'This prompt changed. Reload before continuing.')
        return row

    @router.get('')
    def list_prompts() -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn,app_name)
            rows = conn.execute('''SELECT p.*,v.version AS active_version,
                (SELECT count(*) FROM ka.documentation_prompt_versions WHERE prompt_id=p.id) AS version_count
                FROM ka.documentation_prompts p LEFT JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
                WHERE p.project_id=%s ORDER BY lower(p.name),p.id''', (project,)).fetchall()
            return {'prompts':rows}

    @router.get('/{prompt_id}')
    def get_prompt(prompt_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn,app_name)
            row = conn.execute('SELECT * FROM ka.documentation_prompts WHERE id=%s AND project_id=%s', (prompt_id,project)).fetchone()
            if row is None:
                raise HTTPException(404, 'Documentation prompt not found')
            row['versions'] = conn.execute('SELECT * FROM ka.documentation_prompt_versions WHERE prompt_id=%s ORDER BY version DESC', (prompt_id,)).fetchall()
            return row

    def save(value: PromptInput, request: Request, prompt_id: UUID | None) -> dict:
        db = database()
        request_actor(request)
        try:
            with db.connect() as conn:
                project = db.project(conn,app_name)
                if prompt_id is None:
                    if value.revision is not None:
                        raise HTTPException(400, 'New prompts cannot reuse a revision')
                    row = conn.execute('''INSERT INTO ka.documentation_prompts(project_id,name,description,input_kind,enabled)
                        VALUES (%s,%s,%s,%s,%s) RETURNING *''', (project,value.name,value.description,value.input_kind,value.enabled)).fetchone()
                    prompt_id = row['id']
                    revision = 1
                else:
                    row = locked_prompt(conn,project,prompt_id,value.revision)
                    revision = row['revision']+1
                    conn.execute('''UPDATE ka.documentation_prompts SET name=%s,description=%s,input_kind=%s,enabled=%s,revision=%s
                        WHERE id=%s''', (value.name,value.description,value.input_kind,value.enabled,revision,prompt_id))
                actor = audit(conn,db,request,project,prompt_id,'documentation_prompt.saved',{'revision':revision})
                latest = conn.execute('SELECT id,content FROM ka.documentation_prompt_versions WHERE prompt_id=%s ORDER BY version DESC LIMIT 1', (prompt_id,)).fetchone()
                # Metadata edits do not need another identical draft.
                version_id = latest['id'] if latest is not None else None
                if latest is None or latest['content'] != value.content:
                    version_id = conn.execute('''INSERT INTO ka.documentation_prompt_versions(prompt_id,version,content,created_by)
                        VALUES (%s,%s,%s,%s) RETURNING id''', (prompt_id,row['next_version'],value.content,actor)).fetchone()['id']
                    conn.execute('UPDATE ka.documentation_prompts SET next_version=next_version+1 WHERE id=%s', (prompt_id,))
                return {'id':prompt_id,'revision':revision,'version_id':version_id}
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'A documentation prompt with this name already exists') from None

    @router.post('', status_code=201)
    def create_prompt(value: PromptInput, request: Request) -> dict:
        return save(value,request,None)

    @router.put('/{prompt_id}')
    def update_prompt(prompt_id: UUID, value: PromptInput, request: Request) -> dict:
        return save(value,request,prompt_id)

    @router.post('/{prompt_id}/versions/{version_id}/activate')
    def activate(prompt_id: UUID, version_id: UUID, value: PromptRevision, request: Request) -> dict:
        request_actor(request)
        db = database()
        with db.connect() as conn:
            project = db.project(conn,app_name)
            prompt = locked_prompt(conn,project,prompt_id,value.revision)
            version = conn.execute('SELECT version FROM ka.documentation_prompt_versions WHERE id=%s AND prompt_id=%s', (version_id,prompt_id)).fetchone()
            if version is None:
                raise HTTPException(404, 'Prompt version not found')
            if prompt['active_version_id'] != version_id:
                conn.execute('UPDATE ka.documentation_prompt_versions SET activated_at=COALESCE(activated_at,now()) WHERE id=%s', (version_id,))
                conn.execute('UPDATE ka.documentation_prompts SET active_version_id=%s,revision=revision+1 WHERE id=%s', (version_id,prompt_id))
                audit(conn,db,request,project,prompt_id,'documentation_prompt.activated',{'version':version['version']})
            return {'id':prompt_id,'active_version_id':version_id}

    @router.delete('/{prompt_id}/versions/{version_id}')
    def delete_version(prompt_id: UUID, version_id: UUID, request: Request, revision: int = Query(ge=1), confirmed: bool = False) -> dict:
        request_actor(request)
        if not confirmed:
            raise HTTPException(400, 'Confirm prompt version deletion before continuing')
        db = database()
        with db.connect() as conn:
            project = db.project(conn,app_name)
            prompt = locked_prompt(conn,project,prompt_id,revision)
            if prompt['active_version_id'] == version_id:
                raise HTTPException(409, 'Activate another version before deleting the active version')
            row = conn.execute('DELETE FROM ka.documentation_prompt_versions WHERE id=%s AND prompt_id=%s RETURNING version', (version_id,prompt_id)).fetchone()
            if row is None:
                raise HTTPException(404, 'Prompt version not found')
            conn.execute('UPDATE ka.documentation_prompts SET revision=revision+1 WHERE id=%s', (prompt_id,))
            audit(conn,db,request,project,prompt_id,'documentation_prompt.version.deleted',{'version':row['version']})
            return {'id':version_id}

    @router.delete('/{prompt_id}')
    def delete_prompt(prompt_id: UUID, request: Request, revision: int = Query(ge=1), confirmed: bool = False) -> dict:
        request_actor(request)
        if not confirmed:
            raise HTTPException(400, 'Confirm prompt deletion before continuing')
        db = database()
        with db.connect() as conn:
            project = db.project(conn,app_name)
            locked_prompt(conn,project,prompt_id,revision)
            conn.execute('UPDATE ka.documentation_prompts SET active_version_id=NULL WHERE id=%s', (prompt_id,))
            conn.execute('DELETE FROM ka.documentation_prompts WHERE id=%s', (prompt_id,))
            audit(conn,db,request,project,prompt_id,'documentation_prompt.deleted',{'revision':revision})
            return {'id':prompt_id}

    return router
