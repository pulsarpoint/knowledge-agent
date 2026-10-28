"""Administrator view of complete agent run records."""

import json
from collections import Counter
from functools import partial
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_admin.db import storage
from knowledge_admin.http_errors import validation_text
from knowledge_admin.sessions import explorer
from knowledge_admin.sessions.run_record import tool_counts

RUNS = """SELECT s.run_key, s.agent_id, s.payload->>'agent' AS agent, s.payload->>'kind' AS kind,
    s.payload->>'title' AS title, p.email AS "user", s.created_at AS started_at,
    (SELECT e.payload->>'status' FROM ka.agent_run_events e WHERE e.run_key=s.run_key AND e.kind='end'
        ORDER BY e.seq DESC LIMIT 1) AS status,
    (SELECT coalesce(sum((e.payload->>'total_tokens')::bigint),0) FROM ka.agent_run_events e
        WHERE e.run_key=s.run_key AND e.kind='usage') AS total_tokens,
    (SELECT coalesce(jsonb_agg(jsonb_build_object('name', t.name, 'calls', t.calls) ORDER BY t.calls DESC, t.name), '[]'::jsonb)
        FROM (SELECT e.payload->>'name' AS name, count(*)::int AS calls FROM ka.agent_run_events e
              WHERE e.run_key=s.run_key AND e.kind='tool_call' GROUP BY 1) t) AS tool_counts, s.id,
    NULL::text AS call_id, NULL::text AS caller
    FROM ka.agent_run_events s LEFT JOIN ka.principals p ON p.id=s.actor_id
    WHERE s.kind='start' AND s.project_id=%(project)s
      AND (%(agent_id)s::text IS NULL OR s.agent_id=%(agent_id)s)
      AND (%(kind)s::text IS NULL OR s.payload->>'kind'=%(kind)s)
      AND (%(user)s::text IS NULL OR strpos(lower(p.email), lower(%(user)s)) > 0)
      AND (%(key_prefix)s::text IS NULL OR starts_with(s.run_key, %(key_prefix)s))
      AND (%(has_output)s::boolean IS NULL OR %(has_output)s = EXISTS (SELECT 1 FROM ka.agent_sessions z
        WHERE z.id::text=split_part(s.run_key, ':', 2) AND z.output IS NOT NULL))
      AND (%(schedule_id)s::uuid IS NULL OR EXISTS (SELECT 1 FROM ka.agent_sessions z
        WHERE z.id::text=split_part(s.run_key, ':', 2) AND z.schedule_id=%(schedule_id)s))"""

EXPERT_CALLS = """SELECT i.run_key, i.agent_id, i.payload->'expert'->>'name' AS agent, 'expert_call' AS kind,
    left(i.payload->>'task', 200) AS title, p.email AS "user", i.created_at AS started_at,
    CASE WHEN r.id IS NULL THEN CASE WHEN EXISTS (SELECT 1 FROM ka.agent_run_events z
                                                   WHERE z.run_key=i.run_key AND z.kind='end') THEN 'stopped' ELSE 'working' END
         WHEN starts_with(r.payload->>'preview', 'Expert ' || (i.payload->'expert'->>'name') || ' failed: ') OR r.payload->>'is_error' = 'true' THEN 'failed'
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
      AND (%(user)s::text IS NULL OR strpos(lower(p.email), lower(%(user)s)) > 0)
      AND (%(key_prefix)s::text IS NULL OR starts_with(i.run_key, %(key_prefix)s))"""

# a run without an end event is still working; a cancelled run was stopped like one ended with Stop
MATCHING = """FROM ({rows}) r WHERE %(status)s::text IS NULL
    OR CASE WHEN r.status='cancelled' THEN 'stopped' ELSE coalesce(r.status, 'working') END=%(status)s"""
SORT_COLUMNS = {'started': 'r.started_at', 'tokens': 'r.total_tokens'}
PAGE_SIZES = (10, 20, 50, 100)  # the admin UI's table page sizes

NESTED_CALLS = """SELECT DISTINCT e.payload->>'call_id' AS call_id FROM ka.agent_run_events e
    WHERE e.run_key=%s AND e.kind='tool_call' AND e.payload->>'parent_call_id'=%s AND e.payload->>'call_id' IS NOT NULL
      AND e.agent_id=(SELECT i.agent_id FROM ka.agent_run_events i WHERE i.run_key=%s AND i.kind='input'
                      AND i.payload->>'parent_call_id'=%s AND i.payload ? 'expert' LIMIT 1)"""


class ExploreInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    method: str = Field(min_length=1, max_length=64)
    args: dict = Field(default_factory=dict)


def explored(conn, store, project, run_key: str) -> explorer.SessionRecord:
    """The explorer record of the run's whole session (every turn `cc:<session>:<n>`; a run outside a session alone).
    Run keys only, as the run record: whoever may read the run record reads its session's (admin, a record of that run
    key in this workspace; 404 otherwise). Redacted again with the session's pinned secrets, as the session Debug tab."""
    session_id, _ = explorer.session_of(run_key)
    secrets = explorer.session_secrets(conn, store, project, session_id)
    try:
        return explorer.SessionRecord.load(conn, project, run_key, secrets)
    except explorer.RunNotFound:
        raise HTTPException(404, 'Run record not found.') from None


def build_api(app_name: str, store) -> APIRouter:
    router = APIRouter(prefix='/agent-runs')

    database = partial(storage, store, 'Agent storage')

    @router.get('')
    def runs(
        agent_id: str | None = None,
        kind: str | None = None,
        status: Literal['working', 'completed', 'failed', 'stopped', 'retrying'] | None = None,
        user: str | None = None,
        key_prefix: str | None = None,
        has_output: bool | None = None,
        schedule_id: UUID | None = None,
        sort: Literal['started', 'tokens'] = 'started',
        order: Literal['desc', 'asc'] = 'desc',
        page: int = Query(1, ge=1),
        page_size: int = 50,
    ):
        # an old link's page size (25 before the table's sizes) is taken as the nearest one offered
        page_size = min(PAGE_SIZES, key=lambda size: abs(size - page_size))
        # expert calls of this agent from other agents' runs list with its own runs
        rows = RUNS + (
            f' UNION ALL {EXPERT_CALLS}'
            if agent_id is not None and kind in (None, 'expert_call') and has_output is None and schedule_id is None
            else ''
        )
        matching = MATCHING.format(rows=rows)
        direction = order.upper()
        params = {
            'agent_id': agent_id,
            'kind': kind,
            'status': status,
            'user': user,
            'key_prefix': key_prefix,
            'has_output': has_output,
            'schedule_id': schedule_id,
            'limit': page_size,
            'offset': (page - 1) * page_size,
        }
        with database().connect() as conn:
            params['project'] = store.project(conn, app_name)
            total = conn.execute(f'SELECT count(*) AS n {matching}', params).fetchone()['n']
            rows = conn.execute(
                f"""SELECT r.* {matching} ORDER BY {SORT_COLUMNS[sort]} {direction}, r.id {direction}
                LIMIT %(limit)s OFFSET %(offset)s""",
                params,
            ).fetchall()
        for row in rows:
            if row['kind'] != 'expert_call':
                for key in ('id', 'call_id', 'caller'):
                    row.pop(key)
            row['total_tokens'] = int(row['total_tokens'])
        return {'runs': rows, 'total': total, 'page': page, 'page_size': page_size}

    @router.get('/{run_key}')
    def record(run_key: str, call: str | None = Query(None)):
        with database().connect() as conn:
            # the expert's own subagents belong to the call: their run_subagent calls are expert events
            ids = (
                [call]
                + [row['call_id'] for row in conn.execute(NESTED_CALLS, (run_key, call, run_key, call)).fetchall()]
                if call is not None
                else None
            )
            events = conn.execute(
                """SELECT e.seq,e.kind,e.agent_id,e.payload,e.created_at,p.email AS "user"
                FROM ka.agent_run_events e LEFT JOIN ka.principals p ON p.id=e.actor_id
                WHERE e.run_key=%s AND e.project_id=%s
                  AND (%s::text[] IS NULL OR e.payload->>'parent_call_id'=ANY(%s)
                       OR (e.kind IN ('tool_call','tool_result') AND e.payload->>'call_id'=%s))
                ORDER BY e.seq""",
                (run_key, store.project(conn, app_name), ids, ids, call),
            ).fetchall()
            if call is None:
                counts = tool_counts(conn, [run_key]) if events else []
        if call is not None:
            calls = Counter(
                e['payload'].get('name')
                for e in events
                if e['kind'] == 'tool_call' and e['payload'].get('parent_call_id') in ids
            )
            counts = [
                {'name': name, 'calls': n} for name, n in sorted(calls.items(), key=lambda item: (-item[1], item[0]))
            ]
        if not events:
            raise HTTPException(404, 'Run record not found.')
        return {
            'run_key': run_key,
            'agent_id': events[0]['agent_id'],
            'user': events[0]['user'],
            'tool_counts': counts,
            'events': [
                {key: event[key] for key in ('seq', 'kind', 'agent_id', 'payload', 'created_at')} for event in events
            ],
        }

    @router.get('/{run_key}/explore')
    def explore_methods(run_key: str):
        """The explorer's methods exactly as the session reviewer's model gets them (explorer.listing), with the
        run's session and turn (the Debug tab preselects it); 404 like the run record."""
        with database().connect() as conn:
            found = conn.execute(
                'SELECT 1 FROM ka.agent_run_events WHERE run_key=%s AND project_id=%s LIMIT 1',
                (run_key, store.project(conn, app_name)),
            ).fetchone()
        if found is None:
            raise HTTPException(404, 'Run record not found.')
        session_id, turn = explorer.session_of(run_key)
        return {'methods': explorer.listing(), 'session_id': session_id, 'turn': turn}

    @router.post('/{run_key}/explore')
    def explore(run_key: str, value: ExploreInput):
        """Run one explorer method on the run's session: its result and the size of the serialised result."""
        with database().connect() as conn:
            record = explored(conn, store, store.project(conn, app_name), run_key)
        try:
            result = explorer.call(record, value.method, value.args)
        except ValidationError as exc:
            raise HTTPException(400, validation_text(exc.errors())) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        size = len(json.dumps(result, ensure_ascii=False))
        return {'result': result, 'size_chars': size, 'approx_tokens': -(-size // 4)}

    return router
