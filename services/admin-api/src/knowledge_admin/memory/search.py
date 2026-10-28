"""Memory search: full text (websearch_to_tsquery over name, content and metadata values) and vector distance among
entries embedded with the query's profile, merged by reciprocal rank. Without a query: the filtered
entries, newest first. Vectors of another profile (another model, maybe other dimensions) are never compared."""

import json
from uuid import UUID

from psycopg.types.json import Jsonb

from knowledge_admin.memory.store import ENTRY

RANK = 60


def _filters(project_id: UUID, metadata: dict | None, agent_id: str | None) -> tuple[str, list]:
    where, values = ['m.project_id=%s'], [project_id]
    if metadata:
        where.append('m.metadata @> %s')
        values.append(Jsonb(metadata))
    if agent_id:
        where.append('(m.created_by_agent=%s OR m.updated_by_agent=%s)')
        values += [agent_id, agent_id]
    return ' AND '.join(where), values


def search(
    conn,
    project_id: UUID,
    *,
    query: str | None = None,
    metadata: dict | None = None,
    agent_id: str | None = None,
    limit: int = 10,
    offset: int = 0,
    vector: list[float] | None = None,
    profile: str | None = None,
) -> list[dict]:
    filters, values = _filters(project_id, metadata, agent_id)
    if not query or not query.strip():
        rows = conn.execute(
            f'{ENTRY} WHERE {filters} ORDER BY m.updated_at DESC, m.id LIMIT %s OFFSET %s', (*values, limit, offset)
        ).fetchall()
        return [{**row, 'score': 0.0} for row in rows]
    depth = (offset + limit) * 3
    rankings = [
        conn.execute(
            f"""SELECT m.id FROM ka.memories m WHERE {filters}
            AND m.search_vector @@ websearch_to_tsquery('simple',%s)
            ORDER BY ts_rank_cd(m.search_vector, websearch_to_tsquery('simple',%s)) DESC, m.updated_at DESC, m.id
            LIMIT %s""",
            (*values, query, query, depth),
        ).fetchall()
    ]
    if vector is not None and profile:
        rankings.append(
            conn.execute(
                f"""SELECT m.id FROM ka.memories m WHERE {filters} AND m.embedding_profile=%s
                ORDER BY m.embedding <=> %s::public.vector, m.updated_at DESC, m.id LIMIT %s""",
                (*values, profile, json.dumps(vector), depth),
            ).fetchall()
        )
    scores: dict[UUID, float] = {}
    for ranking in rankings:
        for position, row in enumerate(ranking):
            scores[row['id']] = scores.get(row['id'], 0.0) + 1 / (RANK + position + 1)
    if not scores:
        return []
    rows = conn.execute(f'{ENTRY} WHERE m.id = ANY(%s)', (list(scores),)).fetchall()
    rows.sort(key=lambda row: (-scores[row['id']], -row['updated_at'].timestamp(), str(row['id'])))
    return [{**row, 'score': scores[row['id']]} for row in rows[offset : offset + limit]]


def count(
    conn,
    project_id: UUID,
    *,
    query: str | None = None,
    metadata: dict | None = None,
    agent_id: str | None = None,
    profile: str | None = None,
) -> int:
    """How many entries `search` can return across its pages: the filtered entries; with a query, those matching its
    text plus, with a vector (`profile`), every entry embedded with that profile (vector search ranks them all)."""
    filters, values = _filters(project_id, metadata, agent_id)
    if query and query.strip():
        matched = ["m.search_vector @@ websearch_to_tsquery('simple',%s)"]
        values.append(query)
        if profile:
            matched.append('m.embedding_profile=%s')
            values.append(profile)
        filters += f' AND ({" OR ".join(matched)})'
    return conn.execute(f'SELECT count(*)::int AS n FROM ka.memories m WHERE {filters}', values).fetchone()['n']


def not_embedded(conn, project_id: UUID, profile: str | None) -> int:
    """Entries without a vector of `profile` (all of them when there is no model)."""
    return conn.execute(
        'SELECT count(*) AS n FROM ka.memories WHERE project_id=%s AND (embedding_profile IS NULL OR embedding_profile IS DISTINCT FROM %s)',
        (project_id, profile),
    ).fetchone()['n']
