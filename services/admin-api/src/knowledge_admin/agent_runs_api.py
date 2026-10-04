"""Administrator view of complete agent run records."""
from fastapi import APIRouter, HTTPException, Query

RUNS = """SELECT s.run_key, s.agent_id, s.payload->>'agent' AS agent, s.payload->>'kind' AS kind,
    s.payload->>'title' AS title, p.email AS "user", s.created_at AS started_at,
    (SELECT e.payload->>'status' FROM ka.agent_run_events e WHERE e.run_key=s.run_key AND e.kind='end'
        ORDER BY e.seq DESC LIMIT 1) AS status,
    (SELECT coalesce(sum((e.payload->>'total_tokens')::bigint),0) FROM ka.agent_run_events e
        WHERE e.run_key=s.run_key AND e.kind='usage') AS total_tokens, s.id
    FROM ka.agent_run_events s LEFT JOIN ka.principals p ON p.id=s.actor_id
    WHERE s.kind='start' AND s.project_id=%(project)s
      AND (%(agent_id)s::text IS NULL OR s.agent_id=%(agent_id)s)
      AND (%(kind)s::text IS NULL OR s.payload->>'kind'=%(kind)s)
      AND (%(user)s::text IS NULL OR p.email=%(user)s)
      AND (%(key_prefix)s::text IS NULL OR starts_with(s.run_key, %(key_prefix)s))
      AND (%(before)s::bigint IS NULL OR s.id < %(before)s)
    ORDER BY s.id DESC LIMIT %(limit)s"""


def build_api(app_name: str, store) -> APIRouter:
    router = APIRouter(prefix='/agent-runs')

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    @router.get('')
    def runs(agent_id: str | None = None, kind: str | None = None, user: str | None = None,
             key_prefix: str | None = None, before: int | None = None, limit: int = Query(50, ge=1, le=200)):
        with database().connect() as conn:
            rows = conn.execute(RUNS, {'project': store.project(conn, app_name), 'agent_id': agent_id, 'kind': kind,
                'user': user, 'key_prefix': key_prefix, 'before': before, 'limit': limit + 1}).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        cursor = rows[-1]['id'] if more else None
        for row in rows:
            row.pop('id')
            row['total_tokens'] = int(row['total_tokens'])
        return {'runs': rows, 'next': cursor}

    @router.get('/{run_key}')
    def record(run_key: str):
        with database().connect() as conn:
            events = conn.execute('''SELECT e.seq,e.kind,e.agent_id,e.payload,e.created_at,p.email AS "user"
                FROM ka.agent_run_events e LEFT JOIN ka.principals p ON p.id=e.actor_id
                WHERE e.run_key=%s AND e.project_id=%s ORDER BY e.seq''', (run_key, store.project(conn, app_name))).fetchall()
        if not events:
            raise HTTPException(404, 'Run record not found.')
        return {'run_key': run_key, 'agent_id': events[0]['agent_id'], 'user': events[0]['user'],
                'events': [{key: event[key] for key in ('seq', 'kind', 'agent_id', 'payload', 'created_at')} for event in events]}

    return router
