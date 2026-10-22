"""Environment inventory and explicit service/server membership."""

from functools import partial
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.identity import request_actor
from knowledge_admin.db import storage, workspace
from knowledge_admin.inventory import assets
from knowledge_admin.repositories import RepositoryStore


class EnvironmentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=4000)
    service_ids: list[UUID] = Field(default_factory=list, max_length=100)
    server_ids: list[UUID] = Field(default_factory=list, max_length=100)
    revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator('name', 'description')
    @classmethod
    def valid_text(cls, value: str) -> str:
        if '\x00' in value:
            raise ValueError('Environment text cannot contain null characters')
        return value

    @field_validator('service_ids', 'server_ids')
    @classmethod
    def unique_members(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError('Select each asset only once')
        return value


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    database = partial(storage, store, 'Environment storage', message='Environment storage is not configured')

    @router.get('/environments')
    def list_environments() -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project_id):
            rows = assets.environment_assets(conn, project_id)
        return {'environments': rows}

    @router.get('/environments/{environment_id}')
    def get_environment(environment_id: UUID) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project_id):
            environment = next(iter(assets.environment_assets(conn, project_id, environment_id)), None)
            if environment is None:
                raise HTTPException(404, 'Environment not found')
        return environment

    def save(value: EnvironmentInput, request: Request, environment_id: UUID | None) -> dict:
        db = database()
        subject, email = request_actor(request)
        try:
            with workspace(db, app_name) as (conn, project_id):
                conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project_id,))
                actor_id = db.actor(conn, subject, email)
                if environment_id is None:
                    if value.revision is not None:
                        raise HTTPException(400, 'New environments cannot reuse revisions')
                    environment_id = uuid4()
                    action = 'environment.created'
                else:
                    current = conn.execute(
                        'SELECT revision FROM ka.environments WHERE id=%s AND project_id=%s FOR UPDATE',
                        (environment_id, project_id),
                    ).fetchone()
                    if current is None:
                        raise HTTPException(404, 'Environment not found')
                    if value.revision != current['revision']:
                        raise HTTPException(409, 'This environment changed. Cancel and reopen the form before saving')
                    action = 'environment.updated'
                services = conn.execute(
                    'SELECT id FROM ka.services WHERE project_id=%s AND id=ANY(%s) FOR SHARE',
                    (project_id, value.service_ids),
                ).fetchall()
                if len(services) != len(value.service_ids):
                    raise HTTPException(400, 'Choose existing services from this workspace')
                servers = conn.execute(
                    'SELECT id FROM ka.servers WHERE project_id=%s AND id=ANY(%s) FOR SHARE',
                    (project_id, value.server_ids),
                ).fetchall()
                if len(servers) != len(value.server_ids):
                    raise HTTPException(400, 'Choose existing servers from this workspace')
                previous_services = {
                    row['service_id']
                    for row in conn.execute(
                        'SELECT service_id FROM ka.environment_services WHERE environment_id=%s', (environment_id,)
                    )
                }
                if (
                    conn.execute(
                        """SELECT 1 FROM ka.service_deployments WHERE environment_id=%s
                    AND (NOT (service_id=ANY(%s)) OR NOT (server_id=ANY(%s))) LIMIT 1""",
                        (environment_id, value.service_ids, value.server_ids),
                    ).fetchone()
                    is not None
                ):
                    raise HTTPException(
                        400,
                        'This environment has deployments using these services or servers. Update the deployments in Services before removing their membership',
                    )
                conn.execute(
                    """INSERT INTO ka.environments(id,project_id,name,description,created_by)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET
                    name=EXCLUDED.name,description=EXCLUDED.description,revision=environments.revision+1""",
                    (environment_id, project_id, value.name, value.description, actor_id),
                )
                conn.execute('DELETE FROM ka.environment_services WHERE environment_id=%s', (environment_id,))
                for service_id in value.service_ids:
                    conn.execute(
                        'INSERT INTO ka.environment_services(project_id,environment_id,service_id) VALUES (%s,%s,%s)',
                        (project_id, environment_id, service_id),
                    )
                conn.execute('DELETE FROM ka.environment_servers WHERE environment_id=%s', (environment_id,))
                for server_id in value.server_ids:
                    conn.execute(
                        'INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                        (project_id, environment_id, server_id),
                    )
                changed_services = previous_services ^ set(value.service_ids)
                conn.execute('UPDATE ka.services SET revision=revision+1 WHERE id=ANY(%s)', (list(changed_services),))
                [environment] = assets.environment_assets(conn, project_id, environment_id)
                conn.execute(
                    """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                    VALUES (%s,%s,%s,'environment',%s,%s)""",
                    (
                        project_id,
                        actor_id,
                        action,
                        environment_id,
                        Jsonb(
                            {
                                'name': value.name,
                                'revision': environment['revision'],
                                'service_ids': [str(i) for i in value.service_ids],
                                'server_ids': [str(i) for i in value.server_ids],
                            }
                        ),
                    ),
                )
            return environment
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'An environment with this name already exists') from None

    @router.post('/environments', status_code=201)
    def add_environment(value: EnvironmentInput, request: Request) -> dict:
        return save(value, request, None)

    @router.put('/environments/{environment_id}')
    def edit_environment(environment_id: UUID, value: EnvironmentInput, request: Request) -> dict:
        return save(value, request, environment_id)

    @router.delete('/environments/{environment_id}')
    def delete_environment(
        environment_id: UUID, request: Request, revision: int = Query(ge=1), confirmed: bool = False
    ) -> dict:
        if not confirmed:
            raise HTTPException(400, 'Confirm environment deletion before continuing')
        db = database()
        subject, email = request_actor(request)
        with workspace(db, app_name) as (conn, project_id):
            conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project_id,))
            current = conn.execute(
                'SELECT name,revision FROM ka.environments WHERE id=%s AND project_id=%s FOR UPDATE',
                (environment_id, project_id),
            ).fetchone()
            if current is None:
                raise HTTPException(404, 'Environment not found')
            if revision != current['revision']:
                raise HTTPException(409, 'This environment changed. Cancel, refresh, and try again')
            if conn.execute(
                'SELECT 1 FROM ka.service_deployments WHERE environment_id=%s LIMIT 1', (environment_id,)
            ).fetchone():
                raise HTTPException(409, 'This environment has deployments. Remove those deployments first')
            actor_id = db.actor(conn, subject, email)
            conn.execute(
                """UPDATE ka.services SET revision=revision+1 WHERE id IN
                (SELECT service_id FROM ka.environment_services WHERE environment_id=%s)""",
                (environment_id,),
            )
            conn.execute('DELETE FROM ka.environments WHERE id=%s', (environment_id,))
            conn.execute(
                """INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'environment.deleted','environment',%s,%s)""",
                (project_id, actor_id, environment_id, Jsonb(current)),
            )
        return {'id': environment_id}

    return router
