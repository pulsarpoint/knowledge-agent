"""Blackbox retrieval API: collections, documents, hybrid search.

Serves spec §3 exactly. The `/collections/{name}/search` response shape is
`{path, title, heading_path, anchor, text, score}` on BOTH the hybrid
(BM25+vector RRF) path and the BM25-only fallback used when no embed
client is configured or the embedding endpoint fails -- Plan 2's
`RagClient` depends on that exact shape regardless of which path served
the hit. The one deliberate difference between the two paths is `text`
itself: hybrid hits carry the full chunk text (`retrieval.retrieve`),
while a BM25-only fallback hit carries the FTS5 snippet (highlighted,
truncated excerpt) instead, since `Store.search_bm25` never loads full
chunk text. Documented here in code per the plan; the README (Task 4)
documents it for API consumers.

Spec §3's `{"error": {"message"}}` 4xx contract is unconditional here --
unlike docs-wiki (which serves both a JSON `/api` and a server-rendered
SPA and so branches its validation handler on path prefix), this whole
app IS the API, so a body-validation failure (`RequestValidationError`,
e.g. a wrong-typed field or unparsable JSON) is rewritten to that shape
with no branching.
"""
from __future__ import annotations

import logging

import psycopg
from rag.workspace import Workspace
from rag.workspace_api import build_api as build_workspace_api
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from rag import anchors, retrieval
from rag.config import Collection, Config
from rag.embed_client import EmbedError
from rag.errors import error_response as _error
from rag.store import Store

logger = logging.getLogger("rag.api")



def _validation_message(exc: RequestValidationError) -> str:
    """Concise, single-line summary of a body-validation failure -- the
    API's 4xx contract is {"error": {"message": str}}, not FastAPI's own
    {"detail": [...]} list of structured error dicts. Copied from
    docs-wiki's `webapp._validation_message` (identical rule)."""
    errors = exc.errors()
    if not errors:
        return "invalid request"
    parts = []
    for err in errors:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        msg = err.get("msg", "invalid value")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


class SearchRequest(BaseModel):
    query: str = ""
    # Bounded because SQLite's LIMIT -1 means unlimited -- an unvalidated
    # negative top would dump the whole corpus on the BM25 path.
    top: int = Field(8, ge=1, le=50)


def _indexed(collection: Collection) -> bool:
    return Store.exists(collection.path, collection.db_dir)


def create_app(cfg: Config, embed=None,
              workspace: Workspace | None = None, workspace_key: str = "") -> FastAPI:
    # Local import: mcp_facade imports run_search/_indexed/etc. back from
    # this module at ITS module level, so importing it up top here would
    # be a circular import at load time -- deferring to call time lets
    # rag.api finish defining its own names first.
    from rag.mcp_facade import build_mcp

    collections = {c.name: c for c in cfg.collections}
    mcp = build_mcp(cfg, embed)
    # path="/" because we mount this sub-app at "/mcp" below; leaving the
    # default internal path ("/mcp") would make the externally reachable
    # endpoint "/mcp/mcp" instead of the "/mcp" the README/spec promise
    # (verified empirically -- see task-2-report.md).
    mcp_app = mcp.http_app(path="/")
    # The FastMCP lifespan caveat: the mounted app's session manager never
    # starts unless ITS lifespan runs as part of the outer app's lifespan.
    # Omitting this makes every MCP request fail with fastmcp's own
    # "Task group is not initialized" RuntimeError -- silently fatal until
    # the first real client connects.
    app = FastAPI(lifespan=mcp_app.lifespan)

    if workspace is not None:
        app.include_router(build_workspace_api(workspace, workspace_key))

        @app.exception_handler(psycopg.Error)
        async def workspace_database_error(request, exc):
            return _error(503, "RAG database unavailable")

        @app.exception_handler(OSError)
        async def workspace_file_error(request, exc):
            if isinstance(exc, FileNotFoundError):
                return _error(404, "Collection folder or file does not exist")
            return _error(400, "Source path is unavailable; check permissions and remove symbolic links")

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(request: Request,
                                   exc: RequestValidationError):
        # This whole app is the JSON API (no SPA/non-API routes to
        # exempt, unlike docs-wiki's webapp.py) so every route is bound
        # to the {"error": {"message": ...}} 4xx contract unconditionally.
        return _error(400, _validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _on_http_error(request: Request,
                             exc: StarletteHTTPException):
        # Unmatched paths (404) and wrong methods (405) otherwise leak
        # FastAPI's default {"detail": ...} shape. exc.headers carries
        # the router's Allow header on 405s -- keep it.
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"message": str(exc.detail)}},
            headers=exc.headers)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/collections")
    def list_collections():
        out = []
        for name, collection in collections.items():
            documents = 0
            if _indexed(collection):
                with Store.open(collection.path, collection.db_dir) as store:
                    documents = store.doc_count()
            out.append({"name": name, "indexed": _indexed(collection),
                        "documents": documents,
                        "retrieval": "hybrid" if embed is not None
                        else "bm25-only", "kind": collection.kind})
        return out

    @app.get("/collections/{name}/documents")
    def documents(name: str):
        collection = collections.get(name)
        if collection is None:
            return _error(404, "unknown collection")
        if not _indexed(collection):
            return []
        with Store.open(collection.path, collection.db_dir) as store:
            return _documents_catalog(store)

    @app.get("/collections/{name}/documents/{path:path}")
    def document(name: str, path: str):
        collection = collections.get(name)
        if collection is None:
            return _error(404, "unknown collection")
        if not _indexed(collection):
            return _error(404, f"no such document: {path}")
        with Store.open(collection.path, collection.db_dir) as store:
            doc = _document_or_none(store, path)
            if doc is None:
                return _error(404, f"no such document: {path}")
            return doc

    @app.post("/collections/{name}/search")
    def search(name: str, body: SearchRequest):
        collection = collections.get(name)
        if collection is None:
            return _error(404, "unknown collection")
        return run_search(collection, embed, body.query, body.top)

    # Mounted after every HTTP route above is registered -- Starlette
    # matches routes in registration order, so this ordering keeps the
    # explicit /collections/... routes taking precedence over the mount
    # (they don't overlap with /mcp anyway, but this is the documented,
    # deliberate order per the spec).
    app.mount("/mcp", mcp_app)

    return app


def _documents_catalog(store: Store) -> list[dict]:
    """The `/collections/{name}/documents` catalog shape, shared with
    `mcp_facade.list_docs` (which adds a `collection` tag per entry).
    Uses `Store.list_docs_catalog` (never `list_docs`): a listing never
    renders `markdown`, so there is no reason to pull every doc's full
    body off disk just to discard it per row (review finding: 122x row
    bytes / 4.9x latency on a real corpus when this used `list_docs`)."""
    return [{"path": doc.path, "title": doc.title, "quadrant": doc.quadrant,
             "front_matter": doc.front_matter}
            for doc in store.list_docs_catalog()]


def _document_or_none(store: Store, path: str) -> dict | None:
    """The `/collections/{name}/documents/{path}` document shape (or None
    if not found), shared with `mcp_facade.read_doc`."""
    doc = store.get_doc(path)
    if doc is None:
        return None
    return {"path": path, "title": doc.title, "quadrant": doc.quadrant,
            "front_matter": doc.front_matter, "markdown": doc.markdown,
            "headings": anchors.headings(store, path)}


def run_search(collection: Collection, embed, query: str,
               top: int) -> list[dict]:
    """THE search pipeline: hybrid via `retrieval.retrieve` when an embed
    client is configured (EmbedError -> logged BM25 fallback), BM25-only
    otherwise, then `_attach` for anchors/scores. Shared verbatim by the
    HTTP `/collections/{name}/search` route and every MCP `search_<kind>`
    tool (`mcp_facade.py`) -- there is exactly one retrieval pipeline in
    this service, never a second copy for the MCP facade."""
    query = query.strip()
    if not query:
        return []
    if not _indexed(collection):
        return []
    with Store.open(collection.path, collection.db_dir) as store:
        if embed is not None:
            try:
                chunks = retrieval.retrieve(store, embed, query, top=top)
            except EmbedError as exc:
                # Embeddings missing/pending/unreachable: degrade to
                # BM25-only rather than 500 (spec §3, Global Constraints).
                logger.warning(
                    "embedding failed, degrading to BM25-only: %s", exc)
                chunks = _bm25_fallback(store, query, top)
        else:
            chunks = _bm25_fallback(store, query, top)
        _attach(store, chunks)
        return chunks


def _bm25_fallback(store: Store, query: str, top: int) -> list[dict]:
    """BM25-only hits, shaped like `retrieval.retrieve`'s output minus
    `rowid`. `text` here is the FTS5 snippet (a short, `<mark>`-highlighted
    excerpt around the match), NOT the full chunk text the hybrid path
    returns -- there is no full-text column in the query
    `Store.search_bm25` runs, and adding one would mean a second query per
    hit. `anchor` and `score` are filled in by `_attach`, same as the
    hybrid path."""
    hits = store.search_bm25(query, limit=top)
    return [{"path": hit.path, "title": hit.title,
             "heading_path": hit.heading_path, "text": hit.snippet}
            for hit in hits]


def _attach(store: Store, chunks: list[dict]) -> None:
    """Resolve anchor + a stable score field on every hit, in place, and
    drop `rowid` (an internal-only key `retrieval.retrieve` returns) so
    both paths converge on the exact same
    `{path, title, heading_path, anchor, text, score}` shape.

    `score` is a rank-derived proxy (`1 / (rank + 1)`), not a raw
    BM25/cosine value: neither `retrieval.retrieve` (which discards the
    RRF fusion scores once it sorts by them) nor `Store.search_bm25`
    (which never selects `bm25(chunks)` as a column) exposes a comparable
    native score, and the two paths' native scores are not on the same
    scale anyway. A monotonic rank-based score is enough for `RagClient`
    consumers to distinguish "better" from "worse" within one response.
    """
    cache: dict[str, dict[str, str]] = {}
    for rank, chunk in enumerate(chunks):
        path = chunk["path"]
        if path not in cache:
            cache[path] = anchors.heading_anchors(store, path)
        chunk["anchor"] = cache[path].get(chunk.get("heading_path"))
        chunk.setdefault("score", 1.0 / (rank + 1))
        chunk.pop("rowid", None)
