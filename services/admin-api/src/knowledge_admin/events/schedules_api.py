"""Administration → Schedules: create, list, edit, pause, run and delete schedules. repository-worker
runs them (scheduler.py); this API only changes their rows."""

from datetime import UTC, datetime
from functools import partial
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.identity import request_actor
from knowledge_admin.db import audit as write_audit, storage, workspace
from knowledge_admin.jira.sync import DEFAULT_LIMIT
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.events.schedules import ScheduleError, describe, next_after, upcoming, validate

FIELDS = """id,name,target_kind,target_id,cron,timezone,enabled,next_run_at,
    coalesce(lease_expires_at > now(), false) AS running,last_run_at,last_finished_at,last_status,last_message,revision,params"""
NOT_FOUND = 'Schedule not found.'
STALE = 'Schedule changed. Reload and try again.'
RUNNING = 'This schedule is already running.'
NO_JIRA = 'Save Jira settings first.'
DUPLICATE = 'This target already has a schedule.'
JIRA_PULL = 'Jira auto pull'
PREVIEW_RUNS = 3
HISTORY = 20


class Timetable(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    cron: str = Field(min_length=1, max_length=100)
    timezone: str = Field(min_length=1, max_length=100)


class JiraPullParams(BaseModel):
    """What a jira.pull schedule fetches. On create a missing value is copied from the connection; on edit it is
    removed, so the pull falls back to the connection's last manual fetch (then 25 tickets)."""

    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    jql: str | None = Field(default=None, min_length=1, max_length=4000)
    limit: int | None = Field(default=None, ge=1, le=200, strict=True)


class ScheduleInput(Timetable):
    enabled: bool
    revision: int = Field(ge=1, strict=True)
    params: JiraPullParams | None = None


class NewSchedule(Timetable):
    target_kind: Literal['jira.pull']
    enabled: bool
    params: JiraPullParams = JiraPullParams()


def shown(row: dict) -> dict:
    return {**row, 'description': describe(row['cron'])}


def checked(value: Timetable) -> None:
    try:
        validate(value.cron, value.timezone)
    except ScheduleError as exc:
        raise HTTPException(400, str(exc)) from None


def first_run(value: Timetable) -> datetime | None:
    return next_after(value.cron, value.timezone, datetime.now(UTC)) if value.enabled else None


def jira_defaults(connection: dict) -> dict:
    return {'jql': connection['jql'], 'limit': connection['last_fetch_limit'] or DEFAULT_LIMIT}


def merged(params: dict, change: JiraPullParams) -> dict:
    return {**params, **change.model_dump(exclude_none=True)}


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    database = partial(storage, store, 'Schedule storage', message='Schedule storage is not configured')

    def audit(conn, project: UUID, request: Request, action: str, schedule_id: UUID, details: dict) -> None:
        actor = database().actor(conn, *request_actor(request))
        write_audit(conn, project, actor, action, 'schedule', schedule_id, details)

    def jira_connection(conn, project: UUID) -> dict | None:
        return conn.execute(
            """SELECT id,jql,last_fetch_limit FROM ka.jira_connections
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
        db = database()
        with workspace(db, app) as (conn, project):
            connection = jira_connection(conn, project)
            if connection is None:
                raise HTTPException(400, NO_JIRA)
            row = conn.execute(
                f"""INSERT INTO ka.schedules(project_id,name,target_kind,target_id,cron,timezone,enabled,next_run_at,params)
                VALUES (%s,%s,'jira.pull',%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id,target_kind,target_id) DO NOTHING
                RETURNING {FIELDS}""",
                (
                    project,
                    JIRA_PULL,
                    connection['id'],
                    value.cron,
                    value.timezone,
                    value.enabled,
                    first_run(value),
                    Jsonb(merged(jira_defaults(connection), value.params)),
                ),
            ).fetchone()
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
                """SELECT revision,run_requested,next_run_at,params FROM ka.schedules
                WHERE id=%s AND project_id=%s FOR UPDATE""",
                (schedule_id, project),
            ).fetchone()
            if current is None:
                raise HTTPException(404, NOT_FOUND)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE)
            next_run = current['next_run_at'] if current['run_requested'] else first_run(value)
            params = current['params'] if value.params is None else value.params.model_dump(exclude_none=True)
            row = conn.execute(
                f"""UPDATE ka.schedules SET cron=%s,timezone=%s,enabled=%s,next_run_at=%s,params=%s,
                revision=revision+1,updated_at=now() WHERE id=%s RETURNING {FIELDS}""",
                (value.cron, value.timezone, value.enabled, next_run, Jsonb(params), schedule_id),
            ).fetchone()
            audit(
                conn,
                project,
                request,
                'schedule.saved',
                schedule_id,
                {'cron': value.cron, 'timezone': value.timezone, 'enabled': value.enabled, **params},
            )
        return {'schedule': shown(row)}

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
