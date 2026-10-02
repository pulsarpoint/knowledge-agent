"""Administrator view of Reader conversations stored as live agent sessions."""
from uuid import UUID

from fastapi import APIRouter, HTTPException


def build_api(app_name: str, store) -> APIRouter:
    router = APIRouter(prefix='/admin/audit')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    @router.get('/users')
    def users():
        with database().connect() as conn:
            rows = conn.execute('''SELECT p.email AS user_email,count(DISTINCT s.id) AS sessions,count(r.id) AS questions,
                max(coalesce(r.created_at,s.created_at)) AS last_ts FROM ka.agent_sessions s
                JOIN ka.principals p ON p.id=s.actor_id LEFT JOIN ka.operations_runs r ON r.session_id=s.id
                WHERE s.project_id=%s AND s.channel='reader' GROUP BY p.email ORDER BY last_ts DESC''',
                (store.project(conn, app_name),)).fetchall()
        return {'users': rows}

    @router.get('/users/{email}/sessions')
    def sessions(email: str):
        with database().connect() as conn:
            rows = conn.execute('''SELECT s.id::text AS session_key,s.title AS first_question,count(r.id) AS questions,
                min(coalesce(r.created_at,s.created_at)) AS first_ts,max(coalesce(r.created_at,s.created_at)) AS last_ts
                FROM ka.agent_sessions s JOIN ka.principals p ON p.id=s.actor_id
                LEFT JOIN ka.operations_runs r ON r.session_id=s.id
                WHERE s.project_id=%s AND s.channel='reader' AND p.email=%s
                GROUP BY s.id ORDER BY last_ts DESC''', (store.project(conn, app_name), email)).fetchall()
        return {'sessions': rows}

    @router.get('/session/{key}')
    def session(key: str):
        try:
            session_id = UUID(key)
        except ValueError:
            raise HTTPException(404, 'Unknown session') from None
        with database().connect() as conn:
            rows = conn.execute('''SELECT r.id::text AS id,r.created_at AS ts,p.email AS user_email,s.id::text AS session_key,
                s.agent AS agent_id,r.task AS question,r.answer,r.status,r.context->'grounding' AS grounding
                FROM ka.operations_runs r JOIN ka.agent_sessions s ON s.id=r.session_id
                JOIN ka.principals p ON p.id=r.actor_id
                WHERE s.id=%s AND s.project_id=%s AND s.channel='reader' ORDER BY r.created_at,r.id''',
                (session_id, store.project(conn, app_name))).fetchall()
        for row in rows:
            grounding = row.pop('grounding') or {}
            row['sources'] = grounding.get('sources', [])
            row['sources_used'] = ['docs'] if row['sources'] else []
            row['not_covered'] = bool(grounding.get('not_covered'))
            row['escalated'] = False
        return {'rows': rows}

    return router
