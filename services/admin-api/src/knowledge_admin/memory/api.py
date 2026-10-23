"""Administration → Memory: search, read (with earlier versions), create, edit, restore, delete and re-embed memory
entries. Changes made here are a person's (the actor), record the same memory.* events as the agent tools and an audit
row. The content is embedded before the transaction; without a model an entry is saved without a vector."""

import json
from functools import partial
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.identity import request_actor
from knowledge_admin.db import audit as write_audit
from knowledge_admin.db import storage, workspace
from knowledge_admin.memory import store as memory
from knowledge_admin.memory.embedding import current_model, embed_text
from knowledge_admin.memory.search import not_embedded, search
from knowledge_admin.repositories import RepositoryStore
from knowledge_llm import embeddings

PAGE, MAX_PAGE, MAX_OFFSET = 25, 50, 100000
NOT_FOUND = 'Memory entry not found.'
STALE = 'This entry changed since you opened it.'
EXISTS = 'An entry named {name} already exists.'
NO_MODEL = 'Select an embedding model on the Memory page.'
NOT_EMBEDDING = 'Select an embedding model.'
BAD_METADATA = 'metadata must be a JSON object of text values.'


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(max_length=200)
    metadata: dict = Field(default_factory=dict)
    content: str = Field(max_length=20000)


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    embedding_model_id: UUID | None = None


class Edit(Draft):
    revision: int = Field(ge=1)


class Restore(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    current_revision: int = Field(ge=1)


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()
    database = partial(storage, store, 'Memory storage', message='Memory storage is not configured')

    def embedded(text: str) -> tuple[list[float] | None, str | None]:
        db = database()
        with db.connect() as conn:
            model = current_model(conn, db.project(conn, app))
        return embed_text(model, text)

    def author(conn, request: Request) -> memory.Author:
        return memory.Author(actor_id=database().actor(conn, *request_actor(request)))

    def audit(conn, project: UUID, who: memory.Author, action: str, entry_id: UUID, name: str) -> None:
        write_audit(conn, project, who.actor_id, action, 'memory', entry_id, {'name': name})

    @router.get('/memory/settings')
    def read_settings() -> dict:
        db = database()
        with db.connect() as conn:
            row = conn.execute(
                'SELECT embedding_model_id FROM ka.memory_settings WHERE project_id=%s', (db.project(conn, app),)
            ).fetchone()
        return {'embedding_model_id': str(row['embedding_model_id']) if row and row['embedding_model_id'] else None}

    @router.put('/memory/settings')
    def save_settings(payload: Settings) -> dict:
        db = database()
        with db.connect() as conn:
            if (
                payload.embedding_model_id is not None
                and not conn.execute(
                    "SELECT 1 FROM ka.llm_models WHERE id=%s AND api_format='embeddings'", (payload.embedding_model_id,)
                ).fetchone()
            ):
                raise HTTPException(400, NOT_EMBEDDING)
            conn.execute(
                """INSERT INTO ka.memory_settings(project_id,embedding_model_id) VALUES (%s,%s)
                ON CONFLICT (project_id) DO UPDATE SET embedding_model_id=EXCLUDED.embedding_model_id""",
                (db.project(conn, app), payload.embedding_model_id),
            )
        return read_settings()

    @router.get('/memories')
    def list_memories(
        query: str | None = Query(None, max_length=500),
        metadata: str | None = Query(None, max_length=10000),
        agent: str | None = Query(None, max_length=100),
        offset: int = Query(0, ge=0, le=MAX_OFFSET),
        limit: int = Query(PAGE, ge=1, le=MAX_PAGE),
    ) -> dict:
        filters = None
        if metadata:
            try:
                filters = json.loads(metadata)
            except ValueError:
                raise HTTPException(400, BAD_METADATA) from None
            if not isinstance(filters, dict) or not all(isinstance(v, str) for v in filters.values()):
                raise HTTPException(400, BAD_METADATA)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            model = current_model(conn, project)
        profile = embeddings.embedding_profile(model) if model else None
        vector = embed_text(model, query)[0] if model and query and query.strip() else None
        with db.connect() as conn:
            rows = search(
                conn,
                project,
                query=query,
                metadata=filters,
                agent_id=agent,
                limit=limit + 1,
                offset=offset,
                vector=vector,
                profile=profile,
            )
            missing = not_embedded(conn, project, profile)
        return {
            'memories': rows[:limit],
            'next_offset': offset + limit if len(rows) > limit else None,
            'not_embedded': missing,
            'embedding_model': model is not None,
            'current_profile': profile,
        }

    @router.get('/memories/agents')
    def agents() -> dict:
        db = database()
        with db.connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT a.id,a.name FROM ka.memories m JOIN ka.agents a
                ON a.id IN (m.created_by_agent,m.updated_by_agent) WHERE m.project_id=%s ORDER BY a.name,a.id""",
                (db.project(conn, app),),
            ).fetchall()
        return {'agents': rows}

    @router.post('/memories/re-embed')
    def reembed() -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
        try:
            return memory.reembed(db, project)
        except memory.Missing:
            raise HTTPException(409, NO_MODEL) from None

    @router.get('/memories/{memory_id}')
    def one(memory_id: UUID) -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            entry = memory.get(conn, project, memory_id=memory_id)
            if entry is None:
                raise HTTPException(404, NOT_FOUND)
            model = current_model(conn, project)
            profile = embeddings.embedding_profile(model) if model else None
            return {'memory': entry, 'versions': memory.versions(conn, memory_id), 'current_profile': profile}

    @router.post('/memories', status_code=201)
    def create(value: Draft, request: Request) -> dict:
        try:
            memory.validate(value.name, value.metadata, value.content)
        except memory.Invalid as error:
            raise HTTPException(400, str(error)) from None
        vector, profile = embedded(value.content)
        db = database()
        with workspace(db, app) as (conn, project):
            who = author(conn, request)
            try:
                entry = memory.create(conn, project, value.name, value.metadata, value.content, who, vector, profile)
            except memory.Exists:
                raise HTTPException(409, EXISTS.format(name=value.name)) from None
            audit(conn, project, who, 'memory.created', entry['id'], entry['name'])
            return {'memory': entry}

    @router.put('/memories/{memory_id}')
    def edit(memory_id: UUID, value: Edit, request: Request) -> dict:
        try:
            memory.validate(value.name, value.metadata, value.content)
        except memory.Invalid as error:
            raise HTTPException(400, str(error)) from None
        vector, profile = embedded(value.content)
        db = database()
        with workspace(db, app) as (conn, project):
            who = author(conn, request)
            try:
                entry = memory.update(
                    conn,
                    project,
                    memory_id,
                    value.name,
                    value.metadata,
                    value.content,
                    value.revision,
                    who,
                    vector,
                    profile,
                )
            except memory.Missing:
                raise HTTPException(404, NOT_FOUND) from None
            except memory.Stale:
                raise HTTPException(409, STALE) from None
            except memory.Exists:
                raise HTTPException(409, EXISTS.format(name=value.name)) from None
            audit(conn, project, who, 'memory.updated', memory_id, entry['name'])
            return {'memory': entry}

    @router.post('/memories/{memory_id}/restore')
    def restore(memory_id: UUID, value: Restore, request: Request) -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            old = memory.version(conn, memory_id, value.revision)
        if old is None:
            raise HTTPException(404, NOT_FOUND)
        vector, profile = embedded(old['content'])
        with workspace(db, app) as (conn, project):
            who = author(conn, request)
            try:
                entry = memory.restore(
                    conn, project, memory_id, value.revision, value.current_revision, who, vector, profile
                )
            except memory.Missing:
                raise HTTPException(404, NOT_FOUND) from None
            except memory.Stale:
                raise HTTPException(409, STALE) from None
            except memory.Exists:
                raise HTTPException(409, EXISTS.format(name=old['name'])) from None
            audit(conn, project, who, 'memory.restored', memory_id, entry['name'])
            return {'memory': entry}

    @router.delete('/memories/{memory_id}', status_code=204)
    def delete(memory_id: UUID, request: Request, revision: int = Query(ge=1)) -> Response:
        db = database()
        with workspace(db, app) as (conn, project):
            who = author(conn, request)
            entry = memory.get(conn, project, memory_id=memory_id)
            try:
                memory.delete(conn, project, memory_id, revision, who)
            except memory.Missing:
                raise HTTPException(404, NOT_FOUND) from None
            except memory.Stale:
                raise HTTPException(409, STALE) from None
            audit(conn, project, who, 'memory.deleted', memory_id, entry['name'])
        return Response(status_code=204)

    return router
