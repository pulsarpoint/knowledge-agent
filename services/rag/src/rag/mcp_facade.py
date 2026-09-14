"""MCP facade: kind-scoped retrieval tools over streamable HTTP.

Mounted at `/mcp` by `rag.api.create_app` alongside the existing HTTP
API. Every tool here is a thin wrapper over the SAME primitives the
HTTP routes use (`rag.api.run_search`, `rag.api._document_or_none`,
`rag.api._documents_catalog`, `rag.store.Store`, `rag.anchors`) -- there
is no second retrieval pipeline for MCP clients, only a second transport.

Search tools are registered dynamically, one per kind PRESENT in the
config (`search_docs` / `search_code` / `search_memory`), from a single
closure factory (`_make_search`) -- never copy-pasted per kind.
`read_doc` and `list_docs` are kind-independent and always registered.

"""
from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from rag import api
from rag.config import Collection, Config, VALID_KINDS
from rag.store import Store

logger = logging.getLogger("rag.mcp")

TOP_MIN, TOP_MAX = 1, 50

# Fixed name/description per kind -- VALID_KINDS bounds cfg.collections to
# exactly these three, so every kind present in a valid Config has an
# entry here.
_SEARCH_NAME = {"docs": "search_docs", "code": "search_code",
                "memory": "search_memory"}
_SEARCH_DESCRIPTION = {
    "docs": "Search the application documentation (how-to, reference, "
            "explanation). Returns excerpts with headings, anchors and "
            "relevance scores.",
    "code": "Search the extracted code catalog: error codes and "
            "constants, endpoints, config keys, log strings. Returns "
            "cited excerpts with file anchors.",
    "memory": "Search previously confirmed support solutions (symptom, "
              "cause, resolution, evidence).",
}

def _make_search(kind_collections: list[Collection], embed):
    """One closure body shared by every kind's search tool -- the per-kind
    tool is just this function bound to that kind's collection list and
    registered under a different name/description."""

    def search(query: str, top: int = 8) -> list[dict]:
        if not (TOP_MIN <= top <= TOP_MAX):
            raise ToolError(
                f"top must be between {TOP_MIN} and {TOP_MAX} (got {top})")
        stripped = query.strip()
        if not stripped:
            return []
        hits: list[dict] = []
        for collection in kind_collections:
            # api.run_search already returns [] for an unindexed
            # collection and an empty/whitespace query -- no special
            # casing needed here to "skip" it.
            for hit in api.run_search(collection, embed, stripped, top):
                hit = dict(hit)
                hit["collection"] = collection.name
                hits.append(hit)
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:top]

    return search


def build_mcp(cfg: Config, embed) -> FastMCP:
    mcp = FastMCP("rag", mask_error_details=True)

    collections_by_kind: dict[str, list[Collection]] = {}
    for collection in cfg.collections:
        collections_by_kind.setdefault(collection.kind, []).append(collection)

    for kind, kind_collections in collections_by_kind.items():
        mcp.tool(_make_search(kind_collections, embed),
                 name=_SEARCH_NAME[kind],
                 description=_SEARCH_DESCRIPTION[kind])

    collections_by_name = {c.name: c for c in cfg.collections}

    def read_doc(collection: str, path: str) -> dict:
        coll = collections_by_name.get(collection)
        if coll is None:
            raise ToolError(f"unknown collection: {collection}")
        if not api._indexed(coll):
            raise ToolError(f"no such document: {path}")
        with Store.open(coll.path, coll.db_dir) as store:
            doc = api._document_or_none(store, path)
            if doc is None:
                raise ToolError(f"no such document: {path}")
            return doc

    mcp.tool(read_doc, name="read_doc",
             description="Fetch one full document by collection and "
                         "path, with markdown and heading anchors.")

    def list_docs(kind: str) -> list[dict]:
        kind_collections = collections_by_kind.get(kind)
        if kind not in VALID_KINDS or not kind_collections:
            configured = ", ".join(sorted(collections_by_kind)) or "none"
            raise ToolError(
                f"no collections configured for kind {kind!r}; "
                f"configured kinds: {configured}")
        out: list[dict] = []
        for collection in kind_collections:
            if not api._indexed(collection):
                continue
            with Store.open(collection.path, collection.db_dir) as store:
                for entry in api._documents_catalog(store):
                    entry = dict(entry)
                    entry["collection"] = collection.name
                    out.append(entry)
        return out

    mcp.tool(list_docs, name="list_docs",
             description="List all documents of a kind (docs, code, "
                         "memory) across its collections.")


    return mcp
