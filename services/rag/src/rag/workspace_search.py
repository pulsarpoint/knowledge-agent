"""Project-scoped lexical/vector retrieval over named collections."""

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from rag.workspace_models import embed, embedding_profile


CollectionName = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=60)]


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    query: str = Field(min_length=1, max_length=4000)
    collections: list[CollectionName] | None = Field(default=None, max_length=200)
    mode: Literal["full_text", "embedding", "hybrid"] | None = None
    top: int = Field(8, ge=1, le=30)


BASE = """SELECT c.id,c.text,c.position,f.path,f.content_hash,f.indexed_at,f.job_id,k.name AS collection
          FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id JOIN ka.rag_collections k ON k.id=f.collection_id"""


def search(workspace, payload: SearchInput, transport=None):
    with workspace.connect() as conn:
        config = workspace.settings(conn, payload.project_id)
        rows = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name",
                            (payload.project_id,)).fetchall()
        if payload.collections is not None:
            known = {row["name"] for row in rows}
            for name in payload.collections:
                if name not in known:
                    raise HTTPException(404, f"Unknown collection: {name}")
            rows = [row for row in rows if row["name"] in set(payload.collections)]
        try:
            model = workspace.embedding_model(conn, config["embedding_model_id"]) if config["embedding_model_id"] else None
        except HTTPException:
            model = None
        text_ids, vector_ids, fallback = [], [], []
        for row in rows:
            mode = payload.mode or row["search_mode"]
            if mode in ("full_text", "hybrid"):
                text_ids.append(row["id"])
            if mode in ("embedding", "hybrid"):
                if row["embeddings_enabled"] and model is not None:
                    vector_ids.append(row["id"])
                else:
                    fallback.append(row["name"])
                    if mode == "embedding":
                        text_ids.append(row["id"])
        vector = embed(model, [payload.query], transport)[0] if vector_ids else None
        conn.commit()
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        rankings = []
        if text_ids:
            rankings.append(conn.execute(BASE + """ WHERE c.project_id=%s AND f.collection_id=ANY(%s)
                AND c.search_vector @@ websearch_to_tsquery('simple',%s)
                ORDER BY ts_rank_cd(c.search_vector,websearch_to_tsquery('simple',%s)) DESC,c.id LIMIT %s""",
                (payload.project_id, text_ids, payload.query, payload.query, payload.top * 3)).fetchall())
        if vector is not None:
            rankings.append(conn.execute(BASE + """ JOIN ka.rag_vectors v ON v.project_id=c.project_id AND v.profile=f.embedding_profile
                AND v.input_hash=c.input_hash WHERE c.project_id=%s AND f.collection_id=ANY(%s) AND v.profile=%s
                ORDER BY v.embedding <=> %s::public.vector,c.id LIMIT %s""",
                (payload.project_id, vector_ids, embedding_profile(model), json.dumps(vector), payload.top * 3)).fetchall())
    hits = {}
    for ranking in rankings:
        for position, row in enumerate(ranking):
            hit = hits.setdefault(row["id"], {**row, "score": 0.0})
            hit["score"] += 1 / (60 + position + 1)
    result = sorted(hits.values(), key=lambda row: (-row["score"], str(row["id"])))[:payload.top]
    return {"collections": [row["name"] for row in rows], "fallback": fallback,
            "results": [{**row, "citation": f"S{i + 1}"} for i, row in enumerate(result)]}
