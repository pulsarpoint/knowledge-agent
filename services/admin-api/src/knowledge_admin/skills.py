"""Administrative skill library; storing instructions does not execute them."""

from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.repositories import RepositoryStore

SUMMARY_FIELDS = 'id,name,description,enabled,revision,created_at,updated_at'
DETAIL_FIELDS = SUMMARY_FIELDS + ',instructions'


class SkillInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=64, pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')
    description: str = Field(min_length=1, max_length=1024)
    instructions: str = Field(min_length=1, max_length=100000)
    enabled: bool = Field(default=True, strict=True)
    revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator('name', 'description', mode='before')
    @classmethod
    def trim_metadata(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator('description', 'instructions')
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip() or '\x00' in value:
            raise ValueError('Enter nonempty text without null characters')
        return value


class SkillStatusInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(strict=True)
    revision: int = Field(ge=1, strict=True)


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/skills')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Skill storage is not configured')
        return store

    @router.get('')
    def list_skills(enabled: bool | None = None) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            rows = conn.execute(f'''SELECT {SUMMARY_FIELDS} FROM ka.agent_skills
                WHERE project_id=%s AND (%s::boolean IS NULL OR enabled=%s) ORDER BY name,id''',
                (project, enabled, enabled)).fetchall()
        return {'skills': rows}

    @router.get('/{skill_id}')
    def get_skill(skill_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            row = conn.execute(f'SELECT {DETAIL_FIELDS} FROM ka.agent_skills WHERE id=%s AND project_id=%s', (skill_id, project)).fetchone()
            if row is None:
                raise HTTPException(404, 'Skill not found')
        return row

    def save(value: SkillInput, request: Request, skill_id: UUID | None) -> dict:
        subject, email = request_actor(request)
        db = database()
        try:
            with db.connect() as conn:
                project = db.project(conn, app_name)
                actor = db.actor(conn, subject, email)
                if skill_id is None:
                    if value.revision is not None:
                        raise HTTPException(400, 'New skills cannot reuse a revision')
                    row = conn.execute(f'''INSERT INTO ka.agent_skills(project_id,name,description,instructions,enabled,created_by,updated_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING {DETAIL_FIELDS}''',
                        (project, value.name, value.description, value.instructions, value.enabled, actor, actor)).fetchone()
                    action = 'skill.created'
                else:
                    existing = conn.execute('SELECT revision,enabled FROM ka.agent_skills WHERE id=%s AND project_id=%s FOR UPDATE', (skill_id, project)).fetchone()
                    if existing is None:
                        raise HTTPException(404, 'Skill not found')
                    if value.revision != existing['revision']:
                        raise HTTPException(409, 'This skill changed since you opened it. Cancel and reopen it before saving')
                    row = conn.execute(f'''UPDATE ka.agent_skills SET name=%s,description=%s,instructions=%s,
                        enabled=%s,updated_by=%s,revision=revision+1 WHERE id=%s AND project_id=%s RETURNING {DETAIL_FIELDS}''',
                        (value.name, value.description, value.instructions,
                         value.enabled if 'enabled' in value.model_fields_set else existing['enabled'], actor, skill_id, project)).fetchone()
                    action = 'skill.updated'
                conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                    VALUES (%s,%s,%s,'skill',%s,%s)''', (project, actor, action, row['id'], Jsonb({'name': row['name'], 'revision': row['revision'], 'enabled': row['enabled']})))
            return row
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'A skill with this name already exists') from None

    @router.post('', status_code=201)
    def create_skill(value: SkillInput, request: Request) -> dict:
        return save(value, request, None)

    @router.put('/{skill_id}')
    def update_skill(skill_id: UUID, value: SkillInput, request: Request) -> dict:
        return save(value, request, skill_id)

    @router.patch('/{skill_id}/status')
    def set_status(skill_id: UUID, value: SkillStatusInput, request: Request) -> dict:
        subject, email = request_actor(request)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            row = conn.execute(f'SELECT {SUMMARY_FIELDS} FROM ka.agent_skills WHERE id=%s AND project_id=%s FOR UPDATE', (skill_id, project)).fetchone()
            if row is None:
                raise HTTPException(404, 'Skill not found')
            if row['revision'] != value.revision:
                raise HTTPException(409, 'This skill changed. Refresh the list before changing its status')
            if row['enabled'] == value.enabled:
                return row
            actor = db.actor(conn, subject, email)
            row = conn.execute(f'''UPDATE ka.agent_skills SET enabled=%s,updated_by=%s,revision=revision+1
                WHERE id=%s AND project_id=%s RETURNING {SUMMARY_FIELDS}''', (value.enabled, actor, skill_id, project)).fetchone()
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,%s,'skill',%s,%s)''', (project, actor, 'skill.enabled' if value.enabled else 'skill.disabled', skill_id,
                                                   Jsonb({'name': row['name'], 'revision': row['revision'], 'enabled': row['enabled']})))
        return row

    return router
