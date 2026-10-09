"""Administrator view of complete agent run records."""
from collections import Counter

from fastapi import APIRouter, HTTPException, Query

from knowledge_admin.run_record import tool_counts

RUNS = """SELECT s.run_key, s.agent_id, s.payload->>'agent' AS agent, s.payload->>'kind' AS kind,
    s.payload->>'title' AS title, p.email AS "user", s.created_at AS started_at,
    (SELECT e.payload->>'status' FROM ka.agent_run_events e WHERE e.run_key=s.run_key AND e.kind='end'
        ORDER BY e.seq DESC LIMIT 1) AS status,
    (SELECT coalesce(sum((e.payload->>'total_tokens')::bigint),0) FROM ka.agent_run_events e
        WHERE e.run_key=s.run_key AND e.kind='usage') AS total_tokens,
    (SELECT coalesce(jsonb_agg(jsonb_build_object('name', t.name, 'calls', t.calls) ORDER BY t.calls DESC, t.name), '[]'::jsonb)
        FROM (SELECT e.payload->>'name' AS name, count(*)::int AS calls FROM ka.agent_run_events e
              WHERE e.run_key=s.run_key AND e.kind='tool_call' GROUP BY 1) t) AS tool_counts, s.id
    FROM ka.agent_run_events s LEFT JOIN ka.principals p ON p.id=s.actor_id
    WHERE s.kind='start' AND s.project_id=%(project)s
      AND (%(agent_id)s::text IS NULL OR s.agent_id=%(agent_id)s)
      AND (%(kind)s::text IS NULL OR s.payload->>'kind'=%(kind)s)
      AND (%(user)s::text IS NULL OR p.email=%(user)s)
      AND (%(key_prefix)s::text IS NULL OR starts_with(s.run_key, %(key_prefix)s))
      AND (%(before)s::bigint IS NULL OR s.id < %(before)s)
    ORDER BY s.id DESC LIMIT %(limit)s"""

EXPERT_CALLS = """SELECT i.run_key, i.agent_id, i.payload->'expert'->>'name' AS agent, 'expert_call' AS kind,
    left(i.payload->>'task', 200) AS title, p.email AS "user", i.created_at AS started_at,
    CASE WHEN r.id IS NULL THEN 'working'
         WHEN r.payload->>'preview' ~ '^Expert .+ failed: ' OR r.payload->>'is_error' = 'true' THEN 'failed'
         ELSE 'completed' END AS status,
    (SELECT coalesce(sum((e.payload->>'total_tokens')::bigint),0) FROM ka.agent_run_events e
        WHERE e.run_key=i.run_key AND e.kind='usage' AND e.payload->>'parent_call_id'=ANY(c.ids)) AS total_tokens,
    (SELECT coalesce(jsonb_agg(jsonb_build_object('name', t.name, 'calls', t.calls) ORDER BY t.calls DESC, t.name), '[]'::jsonb)
        FROM (SELECT e.payload->>'name' AS name, count(*)::int AS calls FROM ka.agent_run_events e
              WHERE e.run_key=i.run_key AND e.kind='tool_call' AND e.payload->>'parent_call_id'=ANY(c.ids)
              GROUP BY 1) t) AS tool_counts,
    i.id, i.payload->>'parent_call_id' AS call_id, s.payload->>'agent' AS caller
    FROM ka.agent_run_events i
    LEFT JOIN ka.agent_run_events s ON s.run_key=i.run_key AND s.project_id=i.project_id AND s.kind='start'
    LEFT JOIN ka.principals p ON p.id=coalesce(s.actor_id, i.actor_id)
    CROSS JOIN LATERAL (SELECT i.payload->>'parent_call_id' AS call_id) k
    CROSS JOIN LATERAL (SELECT array_cat(ARRAY[k.call_id], coalesce(array_agg(e.payload->>'call_id'), '{}')) AS ids
        FROM ka.agent_run_events e WHERE e.run_key=i.run_key AND e.kind='tool_call' AND e.agent_id=i.agent_id
          AND e.payload->>'parent_call_id'=k.call_id AND e.payload->>'call_id' IS NOT NULL) c
    LEFT JOIN LATERAL (SELECT e.id, e.payload FROM ka.agent_run_events e
        WHERE e.run_key=i.run_key AND e.kind='tool_result' AND e.payload->>'call_id'=i.payload->>'parent_call_id'
        ORDER BY e.seq DESC LIMIT 1) r ON true
    WHERE i.kind='input' AND i.agent_id=%(agent_id)s AND i.payload ? 'expert' AND i.project_id=%(project)s
      AND (%(user)s::text IS NULL OR p.email=%(user)s)
      AND (%(key_prefix)s::text IS NULL OR starts_with(i.run_key, %(key_prefix)s))
      AND (%(before)s::bigint IS NULL OR i.id < %(before)s)
    ORDER BY i.id DESC LIMIT %(limit)s"""

NESTED_CALLS = """SELECT DISTINCT e.payload->>'call_id' AS call_id FROM ka.agent_run_events e
    WHERE e.run_key=%s AND e.kind='tool_call' AND e.payload->>'parent_call_id'=%s AND e.payload->>'call_id' IS NOT NULL
      AND e.agent_id=(SELECT i.agent_id FROM ka.agent_run_events i WHERE i.run_key=%s AND i.kind='input'
                      AND i.payload->>'parent_call_id'=%s AND i.payload ? 'expert' LIMIT 1)"""


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
            params = {'project': store.project(conn, app_name), 'agent_id': agent_id, 'kind': kind,
                'user': user, 'key_prefix': key_prefix, 'before': before, 'limit': limit + 1}
            rows = conn.execute(RUNS, params).fetchall()
            if agent_id is not None and kind in (None, 'expert_call'):
                rows = sorted(rows + conn.execute(EXPERT_CALLS, params).fetchall(), key=lambda row: row['id'], reverse=True)
        more = len(rows) > limit
        rows = rows[:limit]
        cursor = rows[-1]['id'] if more else None
        for row in rows:
            if row['kind'] != 'expert_call':
                row.pop('id')
            row['total_tokens'] = int(row['total_tokens'])
        return {'runs': rows, 'next': cursor}

    @router.get('/{run_key}')
    def record(run_key: str, call: str | None = Query(None)):
        with database().connect() as conn:
            # the expert's own subagents belong to the call: their run_subagent calls are expert events
            ids = [call] + [row['call_id'] for row in conn.execute(NESTED_CALLS, (run_key, call, run_key, call)).fetchall()] \
                if call is not None else None
            events = conn.execute('''SELECT e.seq,e.kind,e.agent_id,e.payload,e.created_at,p.email AS "user"
                FROM ka.agent_run_events e LEFT JOIN ka.principals p ON p.id=e.actor_id
                WHERE e.run_key=%s AND e.project_id=%s
                  AND (%s::text[] IS NULL OR e.payload->>'parent_call_id'=ANY(%s)
                       OR (e.kind IN ('tool_call','tool_result') AND e.payload->>'call_id'=%s))
                ORDER BY e.seq''', (run_key, store.project(conn, app_name), ids, ids, call)).fetchall()
            if call is None:
                counts = tool_counts(conn, [run_key]) if events else []
        if call is not None:
            calls = Counter(e['payload'].get('name') for e in events
                            if e['kind'] == 'tool_call' and e['payload'].get('parent_call_id') in ids)
            counts = [{'name': name, 'calls': n} for name, n in sorted(calls.items(), key=lambda item: (-item[1], item[0]))]
        if not events:
            raise HTTPException(404, 'Run record not found.')
        return {'run_key': run_key, 'agent_id': events[0]['agent_id'], 'user': events[0]['user'], 'tool_counts': counts,
                'events': [{key: event[key] for key in ('seq', 'kind', 'agent_id', 'payload', 'created_at')} for event in events]}

    return router
