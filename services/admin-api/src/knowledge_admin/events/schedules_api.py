"""Administration → Schedules: create, list, edit, pause, run and delete schedules. repository-worker
runs them (scheduler.py); this API only changes their rows. Targets: the Jira pull (one per connection) and Emit event
(each schedule its own target; a handler runs it by checking the event's subject equals the schedule's name, so names
are unique per project and a rename rewrites that condition on its handlers; the list shows the handlers that run it)."""

from datetime import UTC, datetime
from functools import partial
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.identity import request_actor
from knowledge_admin.db import audit as write_audit, storage, workspace
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.events.matching import type_matches
from knowledge_admin.events.schedule_event import TYPE as FIRED
from knowledge_admin.events.schedules import ScheduleError, describe, next_after, upcoming, validate

FIELDS = """id,name,target_kind,target_id,cron,timezone,enabled,next_run_at,
    coalesce(lease_expires_at > now(), false) AS running,last_run_at,last_finished_at,last_status,last_message,revision,params"""
NOT_FOUND = 'Schedule not found.'
STALE = 'Schedule changed. Reload and try again.'
RUNNING = 'This schedule is already running.'
NO_JIRA = 'Save Jira settings first.'
DUPLICATE = 'This target already has a schedule.'
JIRA_PULL = 'Jira auto pull'
NO_RENAME = 'The Jira pull schedule cannot be renamed.'
NO_NAME = 'Enter a name.'
NO_SETTINGS = 'Emit event schedules take no settings.'
TAKEN = 'A schedule with this name already exists.'
RESERVED = 'This name is reserved for the Jira pull schedule.'
PREVIEW_RUNS = 3
HISTORY = 20


class Timetable(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    cron: str = Field(min_length=1, max_length=100)
    timezone: str = Field(min_length=1, max_length=100)


class JiraPullParams(BaseModel):
    """What a jira.pull schedule fetches. Only given values are stored; a missing one falls back, at each pull, to the
    connection's last manual fetch (then 25 tickets)."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    jql: str | None = Field(default=None, min_length=1, max_length=4000)
    limit: int | None = Field(default=None, ge=1, le=200, strict=True)


class ScheduleInput(Timetable):
    enabled: bool
    revision: int = Field(ge=1, strict=True)
    params: JiraPullParams | None = None
    name: str | None = Field(default=None, max_length=200)  # stripped; a blank one is refused with NO_NAME


class NewSchedule(Timetable):
    target_kind: Literal['jira.pull', 'event.emit']
    enabled: bool
    params: JiraPullParams = JiraPullParams()
    name: str | None = Field(default=None, max_length=200)  # stripped; a blank one is refused with NO_NAME


def shown(row: dict) -> dict:
    return {**row, 'description': describe(row['cron'])}


def kind_checked(target_kind: str, value: NewSchedule | ScheduleInput) -> None:
    """The Jira pull schedule keeps its fixed name; an emit schedule has nothing to set but its name and timing."""
    if target_kind == 'jira.pull' and value.name is not None:
        raise HTTPException(400, NO_RENAME)
    if target_kind == 'event.emit' and value.params is not None and value.params.model_dump(exclude_none=True):
        raise HTTPException(400, NO_SETTINGS)
    if target_kind == 'event.emit' and value.name == '':
        raise HTTPException(400, NO_NAME)
    if target_kind == 'event.emit' and value.name and value.name.casefold() == JIRA_PULL.casefold():
        raise HTTPException(400, RESERVED)  # the Jira pull cannot be renamed, so its name stays free for it


def subject_is(name: str) -> dict:
    return {'field': 'subject', 'op': 'equals', 'value': name}


def bound(handler: dict, name: str) -> bool:
    """A handler runs an emit schedule when its type pattern takes schedule.fired and it checks the subject equals the
    schedule's name."""
    return type_matches(handler['type_pattern'], FIRED) and subject_is(name) in handler['conditions']


def renamed(conditions: list[dict], old: str, new: str) -> list[dict]:
    return [subject_is(new) if condition == subject_is(old) else condition for condition in conditions]


def checked(value: Timetable) -> None:
    try:
        validate(value.cron, value.timezone)
    except ScheduleError as exc:
        raise HTTPException(400, str(exc)) from None


def first_run(value: Timetable) -> datetime | None:
    return next_after(value.cron, value.timezone, datetime.now(UTC)) if value.enabled else None


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    database = partial(storage, store, 'Schedule storage', message='Schedule storage is not configured')

    def audit(conn, project: UUID, request: Request, action: str, schedule_id: UUID, details: dict) -> None:
        actor = database().actor(conn, *request_actor(request))
        write_audit(conn, project, actor, action, 'schedule', schedule_id, details)

    def rebind(conn, project: UUID, old: str, new: str) -> int:
        """Point the handlers bound to the old name at the new one; how many changed."""
        handlers = conn.execute(
            'SELECT id,type_pattern,conditions FROM ka.event_handlers WHERE project_id=%s ORDER BY id FOR UPDATE',
            (project,),
        ).fetchall()
        changed = [h for h in handlers if bound(h, old)]
        for h in changed:
            conn.execute(
                """UPDATE ka.event_handlers SET conditions=%s,revision=revision+1,updated_at=now() WHERE id=%s""",
                (Jsonb(renamed(h['conditions'], old, new)), h['id']),
            )
        return len(changed)

    def jira_connection(conn, project: UUID) -> dict | None:
        return conn.execute(
            """SELECT id FROM ka.jira_connections
            WHERE project_id=%s AND name='primary'""",
            (project,),
        ).fetchone()

    @router.get('/schedules')
    def list_schedules() -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            rows = conn.execute(
                f'SELECT {FIELDS} FROM ka.schedules WHERE project_id=%s ORDER BY name,created_at', (project,)
            ).fetchall()
            handlers = conn.execute(
                'SELECT id,name,enabled,type_pattern,conditions FROM ka.event_handlers WHERE project_id=%s ORDER BY name,id',
                (project,),
            ).fetchall()
        for row in rows:
            if row['target_kind'] == 'event.emit':
                row['handlers'] = [
                    {'id': h['id'], 'name': h['name'], 'enabled': h['enabled']}
                    for h in handlers
                    if bound(h, row['name'])
                ]
        return {'schedules': [shown(row) for row in rows]}

    @router.post('/schedules/preview')
    def preview(value: Timetable) -> dict:
        checked(value)
        return {'runs': upcoming(value.cron, value.timezone, datetime.now(UTC), PREVIEW_RUNS)}

    @router.get('/schedules/{schedule_id}/runs')
    def runs(schedule_id: UUID) -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            if (
                conn.execute(
                    'SELECT 1 FROM ka.schedules WHERE id=%s AND project_id=%s', (schedule_id, project)
                ).fetchone()
                is None
            ):
                raise HTTPException(404, NOT_FOUND)
            rows = conn.execute(
                """SELECT id,trigger,started_at,finished_at,status,message FROM ka.schedule_runs
                WHERE schedule_id=%s ORDER BY id DESC LIMIT %s""",
                (schedule_id, HISTORY),
            ).fetchall()
        return {'runs': rows}

    @router.post('/schedules', status_code=201)
    def create(value: NewSchedule, request: Request) -> dict:
        checked(value)
        kind_checked(value.target_kind, value)
        if value.target_kind == 'event.emit' and value.name is None:
            raise HTTPException(400, NO_NAME)
        db = database()
        with workspace(db, app) as (conn, project):
            schedule_id = uuid4()
            if value.target_kind == 'jira.pull':
                connection = jira_connection(conn, project)
                if connection is None:
                    raise HTTPException(400, NO_JIRA)
                name, target_id = JIRA_PULL, connection['id']
            else:
                name, target_id = value.name, schedule_id  # an emit schedule is its own target
            try:
                row = conn.execute(
                    f"""INSERT INTO ka.schedules(id,project_id,name,target_kind,target_id,cron,timezone,enabled,next_run_at,params)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id,target_kind,target_id) DO NOTHING
                    RETURNING {FIELDS}""",
                    (
                        schedule_id,
                        project,
                        name,
                        value.target_kind,
                        target_id,
                        value.cron,
                        value.timezone,
                        value.enabled,
                        first_run(value),
                        Jsonb(value.params.model_dump(exclude_none=True)),
                    ),
                ).fetchone()
            except psycopg.errors.UniqueViolation:  # the target conflict is skipped above, so this is the name
                raise HTTPException(409, TAKEN) from None
            if row is None:
                raise HTTPException(409, DUPLICATE)
            audit(
                conn,
                project,
                request,
                'schedule.created',
                row['id'],
                {
                    'target_kind': value.target_kind,
                    'name': row['name'],
                    'cron': value.cron,
                    'timezone': value.timezone,
                    'enabled': value.enabled,
                    **row['params'],
                },
            )
        return {'schedule': shown(row)}

    @router.delete('/schedules/{schedule_id}', status_code=204)
    def delete(schedule_id: UUID, request: Request) -> Response:
        db = database()
        with workspace(db, app) as (conn, project):
            row = conn.execute(
                'DELETE FROM ka.schedules WHERE id=%s AND project_id=%s RETURNING name,target_kind',
                (schedule_id, project),
            ).fetchone()
            if row is None:
                raise HTTPException(404, NOT_FOUND)
            audit(
                conn,
                project,
                request,
                'schedule.deleted',
                schedule_id,
                {'name': row['name'], 'target_kind': row['target_kind']},
            )
        return Response(status_code=204)

    @router.put('/schedules/{schedule_id}')
    def save(schedule_id: UUID, value: ScheduleInput, request: Request) -> dict:
        checked(value)
        db = database()
        with workspace(db, app) as (conn, project):
            current = conn.execute(
                """SELECT revision,run_requested,next_run_at,params,target_kind,name FROM ka.schedules
                WHERE id=%s AND project_id=%s FOR UPDATE""",
                (schedule_id, project),
            ).fetchone()
            if current is None:
                raise HTTPException(404, NOT_FOUND)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE)
            kind_checked(current['target_kind'], value)
            next_run = current['next_run_at'] if current['run_requested'] else first_run(value)
            params = current['params'] if value.params is None else value.params.model_dump(exclude_none=True)
            name = value.name or current['name']
            try:
                row = conn.execute(
                    f"""UPDATE ka.schedules SET name=%s,cron=%s,timezone=%s,enabled=%s,next_run_at=%s,params=%s,
                    revision=revision+1,updated_at=now() WHERE id=%s RETURNING {FIELDS}""",
                    (name, value.cron, value.timezone, value.enabled, next_run, Jsonb(params), schedule_id),
                ).fetchone()
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, TAKEN) from None
            # older events keep the old subject; the handlers follow the new name
            moved = rebind(conn, project, current['name'], name) if name != current['name'] else 0
            audit(
                conn,
                project,
                request,
                'schedule.saved',
                schedule_id,
                {
                    'cron': value.cron,
                    'timezone': value.timezone,
                    'enabled': value.enabled,
                    **({'name': name} if value.name is not None else {}),
                    **({'handlers_updated': moved} if moved else {}),
                    **params,
                },
            )
        return {'schedule': shown(row), 'handlers_updated': moved}

    @router.post('/schedules/{schedule_id}/run')
    def run_now(schedule_id: UUID, request: Request) -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            current = conn.execute(
                """SELECT coalesce(lease_expires_at > now(), false) AS running FROM ka.schedules
                WHERE id=%s AND project_id=%s FOR UPDATE""",
                (schedule_id, project),
            ).fetchone()
            if current is None:
                raise HTTPException(404, NOT_FOUND)
            if current['running']:
                raise HTTPException(409, RUNNING)
            row = conn.execute(
                f"""UPDATE ka.schedules SET run_requested=true,next_run_at=now(),updated_at=now()
                WHERE id=%s RETURNING {FIELDS}""",
                (schedule_id,),
            ).fetchone()
            audit(conn, project, request, 'schedule.run_requested', schedule_id, {})
        return {'schedule': shown(row)}

    return router
