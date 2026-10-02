"""Saved documentation workspaces and independent phase attempts."""
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.repositories import RepositoryStore


class WorkflowInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: Literal['service', 'environment']
    service_id: UUID | None = None
    environment_id: UUID | None = None
    model_id: UUID

    @model_validator(mode='after')
    def subject(self):
        if (self.scope == 'service' and (self.service_id is None or self.environment_id is not None)
            or self.scope == 'environment' and (self.environment_id is None or self.service_id is not None)):
            raise ValueError('Choose one service or one environment')
        return self


class DraftInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: dict
    previous: dict | None = None

    @model_validator(mode='after')
    def bounded(self):
        if len(json.dumps(self.content)) > 600000 or len(json.dumps(self.previous)) > 600000:
            raise ValueError('Workflow draft is too large')
        return self


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/documentation/workflows')

    def database():
        if store is None:
            raise HTTPException(503, 'Document storage is not configured')
        return store

    @router.get('')
    def listing(offset: int = Query(default=0, ge=0)):
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            return {'workflows': conn.execute('''SELECT id,name,scope,service_id,environment_id,created_at,updated_at,phases,graph_runs
                FROM ka.documentation_workflow_details WHERE project_id=%s ORDER BY updated_at DESC,id LIMIT 50 OFFSET %s''',
                (project,offset)).fetchall()}

    @router.post('', status_code=201)
    def create(value: WorkflowInput, request: Request):
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            table, subject = ('services',value.service_id) if value.scope == 'service' else ('environments',value.environment_id)
            entity = conn.execute(f'SELECT name FROM ka.{table} WHERE id=%s AND project_id=%s', (subject,project)).fetchone()
            if entity is None:
                raise HTTPException(404, 'Documentation subject not found')
            if conn.execute("SELECT 1 FROM ka.llm_models WHERE id=%s AND enabled AND api_format<>'embeddings'", (value.model_id,)).fetchone() is None:
                raise HTTPException(400, 'Select an available text model')
            workflow_id = uuid4()
            draft = {'form': {'value': {**jsonable_encoder(value), 'code_project_ids': [], 'server_ids': [], 'units': [],
                'instructions': '', 'update_existing': True}}}
            conn.execute('''INSERT INTO ka.documentation_workflows(id,project_id,name,scope,service_id,environment_id,drafts)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''', (workflow_id,project,entity['name'][:220]+' documentation',value.scope,value.service_id,value.environment_id,Jsonb(draft)))
            conn.execute("INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id) VALUES (%s,%s,'documentation.workflow_created','documentation_workflow',%s)", (project,actor,workflow_id))
            return {'id': workflow_id}

    @router.get('/{workflow_id}')
    def detail(workflow_id: UUID):
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            row = conn.execute('SELECT * FROM ka.documentation_workflow_details WHERE id=%s AND project_id=%s', (workflow_id,project)).fetchone()
            if row is None:
                raise HTTPException(404, 'Documentation workflow not found')
            return row

    @router.put('/{workflow_id}/drafts/{section}')
    def save_draft(workflow_id: UUID, section: Literal['form','overview','api','messaging'], value: DraftInput, request: Request):
        request_actor(request)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            row = conn.execute('SELECT drafts FROM ka.documentation_workflows WHERE id=%s AND project_id=%s FOR UPDATE', (workflow_id,project)).fetchone()
            if row is None:
                raise HTTPException(404, 'Documentation workflow not found')
            if row['drafts'].get(section) != value.previous:
                raise HTTPException(409, 'This draft changed in another page. Reload before editing it.')
            conn.execute('UPDATE ka.documentation_workflows SET drafts=jsonb_set(drafts,ARRAY[%s],%s),updated_at=now() WHERE id=%s',
                (section,Jsonb(value.content),workflow_id))
            return {'saved': True}

    return router
