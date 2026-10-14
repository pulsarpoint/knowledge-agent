"""Administration → Events → Handlers: create, list, edit, delete and Preview handlers. The event processor
(event_processor.py, in repository-worker) runs them; this API only changes their rows."""
from functools import partial
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.events import matching as event_matching, templates as event_templates
from knowledge_admin.access import request_actor
from knowledge_admin.db import audit as write_audit, storage, workspace
from knowledge_admin.repositories import RepositoryStore

NOT_FOUND = 'Handler not found.'
STALE = 'This handler was changed by someone else. Reload and try again.'
DUPLICATE = 'A handler with this name already exists.'
NO_EVENT = 'Event not found.'
NO_AGENT = 'Agent not found.'
PER_RUN = 'This agent gets its resources per run; handlers can only send to agents with fixed resources.'
SELECT = """SELECT h.id,h.name,h.enabled,h.type_pattern,h.conditions,h.action,h.emit,h.agent_id,a.name AS agent_name,h.prompt,
    h.revision,h.created_at,h.updated_at,(SELECT jsonb_build_object('status',r.status,'created_at',r.created_at)
        FROM ka.event_handler_runs r WHERE r.handler_id=h.id ORDER BY r.id DESC LIMIT 1) AS last_run
    FROM ka.event_handlers h LEFT JOIN ka.agents a ON a.id=h.agent_id"""


class Condition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: str = Field(max_length=200)
    op: str = Field(max_length=20)
    value: str | list[str] | None = None


class EmitTemplates(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: str = Field(default='', max_length=200)
    subject: str = Field(default='', max_length=2000)
    title: str = Field(default='', max_length=2000)
    payload: str = Field(default='', max_length=20000)


class HandlerDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(max_length=120)
    enabled: bool = True
    type_pattern: str = Field(max_length=200)
    conditions: list[Condition] = Field(default_factory=list, max_length=20)
    action: Literal['emit', 'agent']
    emit: EmitTemplates | None = None
    agent_id: str | None = Field(default=None, max_length=200)
    prompt: str | None = Field(default=None, max_length=20000)


class HandlerInput(HandlerDraft):
    revision: int = Field(ge=1, strict=True)


class PreviewDraft(HandlerDraft):
    name: str = Field(default='', max_length=120)  # Preview works before the handler has a name


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    handler: PreviewDraft
    event_id: int = Field(ge=1)


def bad(message: str) -> HTTPException:
    return HTTPException(400, message)


def refuse_nul(draft: HandlerDraft) -> None:
    """PostgreSQL text cannot hold NUL: say which field has one instead of failing on the insert."""
    emit = draft.emit or EmitTemplates()
    fields = (('Name', draft.name), ('Event type', draft.type_pattern),
              ('Conditions', [condition.model_dump() for condition in draft.conditions]),
              ('New event type', emit.type), ('Subject', emit.subject), ('Title', emit.title), ('Payload', emit.payload),
              ('Agent', draft.agent_id), ('Prompt', draft.prompt))
    for label, value in fields:
        if event_templates.has_nul(value):
            raise bad(f'Remove the NUL character from {label}.')


def stored(conn, draft: HandlerDraft, *, named: bool = True) -> dict:
    """The handler's columns, validated: 400 with a message for the form when something is wrong."""
    refuse_nul(draft)
    name = draft.name.strip()
    if named and not name:
        raise bad('Enter a name.')
    try:
        event_matching.check_pattern(draft.type_pattern.strip())
        conditions = event_matching.check_conditions([condition.model_dump() for condition in draft.conditions])
    except event_matching.ConditionError as exc:
        raise bad(str(exc)) from None
    values = {'name': name, 'enabled': draft.enabled, 'type_pattern': draft.type_pattern.strip(), 'conditions': conditions,
              'action': draft.action, 'emit': None, 'agent_id': None, 'prompt': None}
    try:
        if draft.action == 'emit':
            emit = (draft.emit or EmitTemplates()).model_dump()
            event_templates.check_emit(emit)
            values['emit'] = emit
        else:
            if not draft.agent_id:
                raise bad('Choose an agent.')
            event_templates.check_prompt(draft.prompt or '')
            values.update(agent_id=draft.agent_id, prompt=draft.prompt)
    except event_templates.TemplateProblem as exc:
        raise bad(str(exc)) from None
    return values


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    database = partial(storage, store, 'Event storage', message='Event storage is not configured')

    def agent_usable(conn, agent_id: str) -> None:
        agent = conn.execute('SELECT resources FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()
        if agent is None:
            raise bad(NO_AGENT)
        if agent['resources'] != 'fixed':
            raise bad(PER_RUN)

    def one(conn, project: UUID, handler_id: UUID) -> dict:
        row = conn.execute(SELECT + ' WHERE h.id=%s AND h.project_id=%s', (handler_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return row

    def audit(conn, project: UUID, request: Request, action: str, handler_id: UUID, details: dict) -> UUID:
        actor = database().actor(conn, *request_actor(request))
        write_audit(conn, project, actor, action, 'event_handler', handler_id, details)
        return actor

    @router.get('/event-handlers')
    def list_handlers() -> dict:
        db = database()
        with db.connect() as conn:
            rows = conn.execute(SELECT + ' WHERE h.project_id=%s ORDER BY h.name', (db.project(conn, app),)).fetchall()
        return {'handlers': rows}

    @router.get('/event-handlers/agents')
    def agents() -> dict:
        """Agents a handler can send to: enabled, with fixed resources."""
        db = database()
        with db.connect() as conn:
            return {'agents': conn.execute("SELECT id,name FROM ka.agents WHERE enabled AND resources='fixed' ORDER BY name,id").fetchall()}

    @router.post('/event-handlers/preview')
    def preview(value: PreviewInput) -> dict:
        db = database()
        with db.connect() as conn:
            handler = stored(conn, value.handler, named=False)
            event = conn.execute('SELECT * FROM ka.events WHERE id=%s AND project_id=%s',
                                 (value.event_id, db.project(conn, app))).fetchone()
        if event is None:
            raise HTTPException(404, NO_EVENT)
        try:
            output = (event_templates.render_emit(handler['emit'], event) if handler['action'] == 'emit'
                      else {'prompt': event_templates.render_prompt(handler['prompt'], event)})
        except event_templates.TemplateProblem as exc:
            output = {'error': str(exc)}
        reason = event_matching.explain(handler, event)
        return {'matches': reason is None, 'reason': reason, 'output': output}

    @router.post('/event-handlers', status_code=201)
    def create(value: HandlerDraft, request: Request) -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            values = stored(conn, value)
            if values['agent_id']:
                agent_usable(conn, values['agent_id'])
            actor = db.actor(conn, *request_actor(request))
            try:
                with conn.transaction():
                    handler_id = conn.execute("""INSERT INTO ka.event_handlers(project_id,name,enabled,type_pattern,conditions,
                        action,emit,agent_id,prompt,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (project, values['name'], values['enabled'], values['type_pattern'], Jsonb(values['conditions']),
                         values['action'], Jsonb(values['emit']) if values['emit'] else None, values['agent_id'],
                         values['prompt'], actor)).fetchone()['id']
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, DUPLICATE) from None
            audit(conn, project, request, 'event_handler.created', handler_id, {'name': values['name'], 'action': values['action']})
            return {'handler': one(conn, project, handler_id)}

    @router.put('/event-handlers/{handler_id}')
    def save(handler_id: UUID, value: HandlerInput, request: Request) -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            current = conn.execute('SELECT revision,enabled,agent_id FROM ka.event_handlers WHERE id=%s AND project_id=%s FOR UPDATE',
                                   (handler_id, project)).fetchone()
            if current is None:
                raise HTTPException(404, NOT_FOUND)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE)
            values = stored(conn, value)
            # Only a new agent or turning the handler on checks the agent: a handler whose agent was deleted or
            # switched to per-run resources can still be turned off and edited.
            if values['agent_id'] and (values['agent_id'] != current['agent_id'] or (values['enabled'] and not current['enabled'])):
                agent_usable(conn, values['agent_id'])
            try:
                with conn.transaction():
                    conn.execute("""UPDATE ka.event_handlers SET name=%s,enabled=%s,type_pattern=%s,conditions=%s,action=%s,
                        emit=%s,agent_id=%s,prompt=%s,revision=revision+1,updated_at=now() WHERE id=%s""",
                        (values['name'], values['enabled'], values['type_pattern'], Jsonb(values['conditions']),
                         values['action'], Jsonb(values['emit']) if values['emit'] else None, values['agent_id'],
                         values['prompt'], handler_id))
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, DUPLICATE) from None
            audit(conn, project, request, 'event_handler.updated', handler_id,
                  {'name': values['name'], 'action': values['action'], 'enabled': values['enabled']})
            return {'handler': one(conn, project, handler_id)}

    @router.delete('/event-handlers/{handler_id}', status_code=204)
    def delete(handler_id: UUID, request: Request) -> Response:
        db = database()
        with workspace(db, app) as (conn, project):
            row = conn.execute('DELETE FROM ka.event_handlers WHERE id=%s AND project_id=%s RETURNING name',
                               (handler_id, project)).fetchone()
            if row is None:
                raise HTTPException(404, NOT_FOUND)
            audit(conn, project, request, 'event_handler.deleted', handler_id, {'name': row['name']})
        return Response(status_code=204)

    return router
