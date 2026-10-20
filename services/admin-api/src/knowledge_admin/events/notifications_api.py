"""Administration → Notifications: what event-triggered agent runs produced — the first turn's answer, or why it failed.
One shared, read-only list for every user; the session worker writes it when an event run's first turn ends (rows from
before 0103 came from agent tasks, kept as read-only history).
A notification also says whether its answer was posted to Jira (events/jira_replies.py)."""

from functools import partial
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from knowledge_admin.db import storage
from knowledge_admin.events.api import like
from knowledge_admin.repositories import RepositoryStore

PAGE, MAX_PAGE, MAX_OFFSET, PREVIEW = 50, 200, 100000, 200
NOT_FOUND = 'Notification not found.'
LISTED = f"""n.id,n.created_at,n.kind,n.status,n.title,left(n.body,{PREVIEW}) AS preview,n.event_id,n.agent_id,
    coalesce(t.agent_name,a.name,n.agent_id) AS agent_name,n.session_id,n.jira_status,n.jira_issue_key,
    n.give_back_status"""
FROM = """FROM ka.notifications n LEFT JOIN ka.agent_tasks t ON t.id=n.agent_task_id
    LEFT JOIN ka.agents a ON a.id=n.agent_id"""


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    database = partial(storage, store, 'Notification storage', message='Notification storage is not configured')

    @router.get('/notifications')
    def list_notifications(
        status: Literal['succeeded', 'failed'] | None = None,
        q: str | None = Query(None, max_length=200),
        offset: int = Query(0, ge=0, le=MAX_OFFSET),
        limit: int = Query(PAGE, ge=1, le=MAX_PAGE),
    ) -> dict:
        db = database()
        with db.connect() as conn:
            where, values = ['n.project_id=%s'], [db.project(conn, app)]
            if status:
                where.append('n.status=%s')
                values.append(status)
            if q and q.strip():
                where.append("n.title ILIKE %s ESCAPE '\\'")
                values.append(like(q.strip()))
            rows = conn.execute(
                f"""SELECT {LISTED} {FROM} WHERE {' AND '.join(where)}
                ORDER BY n.id DESC LIMIT %s OFFSET %s""",
                (*values, limit + 1, offset),
            ).fetchall()
        more = len(rows) > limit
        return {'notifications': rows[:limit], 'next_offset': offset + limit if more else None}

    @router.get('/notifications/{notification_id}')
    def one(notification_id: int) -> dict:
        db = database()
        with db.connect() as conn:
            row = conn.execute(
                f"""SELECT {LISTED},n.body,n.handler_id,
                n.jira_comment_id,n.jira_attempts,n.jira_error,n.give_back_name,n.give_back_error,c.base_url AS jira_base_url,
                CASE WHEN e.id IS NULL THEN NULL ELSE jsonb_build_object('id',e.id,'type',e.type,'subject',e.subject) END AS event,
                CASE WHEN t.id IS NULL THEN NULL ELSE jsonb_build_object('id',t.id,'attempts',t.attempts,
                    'created_at',t.created_at,'finished_at',t.finished_at) END AS task
                {FROM} LEFT JOIN ka.events e ON e.id=n.event_id
                LEFT JOIN ka.jira_connections c ON c.id=n.jira_connection_id WHERE n.id=%s AND n.project_id=%s""",
                (notification_id, db.project(conn, app)),
            ).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return {'notification': row}

    return router
