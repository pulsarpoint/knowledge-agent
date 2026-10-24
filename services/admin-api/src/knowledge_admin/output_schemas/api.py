"""Administration → Output schemas: the library of JSON Schemas a run may be asked to answer with. A schema is checked
against the strict subset (rules.check_schema) before it is saved; every changed save is a new version. Changes write
an audit row."""

from functools import partial
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.db import audit as write_audit
from knowledge_admin.db import storage, workspace
from knowledge_admin.identity import request_actor
from knowledge_admin.output_schemas import store as schemas
from knowledge_admin.output_schemas.rules import check_schema, validate
from knowledge_admin.repositories import RepositoryStore

NOT_FOUND = 'Output schema not found.'
STALE = 'This output schema changed since you opened it.'
EXISTS = 'An output schema named {name} already exists.'
NOT_A_SCHEMA = 'Not an output schema: {problems}'


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120, pattern=r'\S')
    description: str = Field('', max_length=2000)
    schema_: Any = Field(alias='schema')


class Edit(Draft):
    revision: int = Field(ge=1)


class Check(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_: Any = Field(alias='schema')
    sample: Any = None


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()
    database = partial(storage, store, 'Output schema storage', message='Output schema storage is not configured')

    def accepted(value: Draft) -> dict:
        problems = check_schema(value.schema_)
        if problems:
            raise HTTPException(400, NOT_A_SCHEMA.format(problems='; '.join(problems)))
        return value.schema_

    def actor(conn, request: Request) -> UUID:
        return database().actor(conn, *request_actor(request))

    @router.get('/output-schemas')
    def list_schemas() -> dict:
        with workspace(database(), app) as (conn, project):
            return {'schemas': schemas.list_all(conn, project)}

    @router.post('/output-schemas/check')
    def check(value: Check) -> dict:
        problems = check_schema(value.schema_)
        sample = validate(value.schema_, value.sample) if 'sample' in value.model_fields_set and not problems else None
        return {'problems': problems, 'sample_errors': sample}

    @router.get('/output-schemas/{schema_id}')
    def one(schema_id: UUID) -> dict:
        with workspace(database(), app) as (conn, project):
            entry = schemas.get(conn, project, schema_id)
        if entry is None:
            raise HTTPException(404, NOT_FOUND)
        return {'schema': entry}

    @router.get('/output-schemas/{schema_id}/versions/{number}')
    def one_version(schema_id: UUID, number: int) -> dict:
        with workspace(database(), app) as (conn, project):
            found = schemas.version(conn, project, schema_id, number)
        if found is None:
            raise HTTPException(404, NOT_FOUND)
        return {'version': found}

    @router.post('/output-schemas', status_code=201)
    def create(value: Draft, request: Request) -> dict:
        schema = accepted(value)
        with workspace(database(), app) as (conn, project):
            who = actor(conn, request)
            try:
                entry = schemas.create(conn, project, value.name, value.description, schema, who)
            except schemas.Exists:
                raise HTTPException(409, EXISTS.format(name=value.name)) from None
            write_audit(conn, project, who, 'output_schema.created', 'output_schema', entry['id'], {'name': value.name})
            return {'schema': entry}

    @router.put('/output-schemas/{schema_id}')
    def edit(schema_id: UUID, value: Edit, request: Request) -> dict:
        schema = accepted(value)
        with workspace(database(), app) as (conn, project):
            who = actor(conn, request)
            try:
                entry = schemas.update(
                    conn, project, schema_id, value.name, value.description, schema, value.revision, who
                )
            except schemas.Missing:
                raise HTTPException(404, NOT_FOUND) from None
            except schemas.Stale:
                raise HTTPException(409, STALE) from None
            except schemas.Exists:
                raise HTTPException(409, EXISTS.format(name=value.name)) from None
            details = {'name': value.name, 'version': entry['current_version']}
            write_audit(conn, project, who, 'output_schema.updated', 'output_schema', schema_id, details)
            return {'schema': entry}

    @router.delete('/output-schemas/{schema_id}', status_code=204)
    def delete(schema_id: UUID, request: Request, revision: int = Query(ge=1)) -> Response:
        with workspace(database(), app) as (conn, project):
            who = actor(conn, request)
            entry = schemas.get(conn, project, schema_id)
            try:
                schemas.delete(conn, project, schema_id, revision)
            except schemas.Missing:
                raise HTTPException(404, NOT_FOUND) from None
            except schemas.Stale:
                raise HTTPException(409, STALE) from None
            except schemas.InUse as error:
                raise HTTPException(409, f'Used by: {error}.') from None
            write_audit(
                conn, project, who, 'output_schema.deleted', 'output_schema', schema_id, {'name': entry['name']}
            )
        return Response(status_code=204)

    return router
