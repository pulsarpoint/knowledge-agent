"""Project-scoped lexical/vector retrieval."""

import json
import re
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.workspace import Settings, folder_path
from rag.workspace_models import embed, embedding_profile


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    query: str = Field(min_length=1, max_length=4000)
    folder: str = "*"
    folders: list[str] | None = Field(default=None, min_length=1, max_length=200)
    mode: Literal["full_text", "embedding", "hybrid"] = "full_text"
    top: int = Field(8, ge=1, le=30)

    @model_validator(mode="after")
    def unambiguous_scope(self):
        if self.folders is not None and self.folder != "*":
            raise ValueError("Use either folder or folders")
        return self


def search(workspace, payload: SearchInput, transport=None):
    scopes = []
    scope_params = []
    for selector in dict.fromkeys(payload.folders if payload.folders is not None else [payload.folder]):
        recursive = selector.endswith(".*") or selector == "*"
        path = "" if selector == "*" else (selector[:-2] if recursive else selector).replace(".", "/")
        folder_path(path, root=True)
        scopes.append("(%s='' OR d.path=%s OR (%s AND starts_with(d.path,%s)))")
        scope_params.extend((path, path, recursive, path + "/"))
    with workspace.connect() as conn:
        config = workspace.settings(conn, payload.project_id)
        settings = Settings(**config["settings"])
        mode = payload.mode
        if mode == "embedding" and not settings.embedding_enabled:
            raise HTTPException(409, "Enable and configure embeddings in RAG Settings, then process the selected folders")
        if mode == "hybrid" and not settings.embedding_enabled:
            mode = "full_text"
        vector = None
        if mode != "full_text":
            model = workspace.embedding_model(conn, config['embedding_model_id'])
            vector = embed(model, [payload.query], transport)[0]
        # Keep one database snapshot across both rankings during concurrent publication.
        conn.commit()
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        base = """SELECT c.id,c.text,c.position,f.path,f.kind,f.content_hash,f.indexed_at,f.job_id,
                  d.path AS folder FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id
                  JOIN ka.rag_folders d ON d.id=f.folder_id"""
        scope = "c.project_id=%s AND (" + " OR ".join(scopes) + ")"
        params = (payload.project_id, *scope_params)
        rankings = []
        if mode != "embedding":
            rankings.append(conn.execute(base + " WHERE " + scope + " AND c.search_vector @@ websearch_to_tsquery('simple',%s) ORDER BY ts_rank_cd(c.search_vector,websearch_to_tsquery('simple',%s)) DESC,c.id LIMIT %s", (*params, payload.query, payload.query, payload.top * 3)).fetchall())
        if vector is not None:
            rankings.append(conn.execute(base + " JOIN ka.rag_vectors v ON v.project_id=c.project_id AND v.profile=f.embedding_profile AND v.input_hash=c.input_hash WHERE " + scope + " AND v.profile=%s ORDER BY v.embedding <=> %s::public.vector,c.id LIMIT %s", (*params, embedding_profile(model), json.dumps(vector), payload.top * 3)).fetchall())
    hits = {}
    for ranking in rankings:
        for position, row in enumerate(ranking):
            hit = hits.setdefault(row["id"], {**row, "folder": row["folder"].replace("/", "."), "score": 0.0})
            hit["score"] += 1 / (60 + position + 1)
    result = sorted(hits.values(), key=lambda row: (-row["score"], str(row["id"])))[:payload.top]
    return {"mode": mode, "folder": payload.folder, "results": [{**row, "citation": f"S{i + 1}"} for i, row in enumerate(result)]}
