"""Retained search tools. Execution endpoints do not invoke these tools."""
import asyncio
from dataclasses import dataclass, field
from agents import RunContextWrapper, function_tool
from docs_wiki.rag_client import RagClient, RagError, RagNotFound

@dataclass
class SearchContext:
    rag: RagClient
    collection: str
    excerpts: list[dict] = field(default_factory=list)
    last_search: list[dict] = field(default_factory=list)
    used: set[str] = field(default_factory=set)


@function_tool
async def search_docs(ctx: RunContextWrapper[SearchContext], query: str) -> str:
    """Search the documentation; returns numbered excerpts."""
    state = ctx.context
    try:
        chunks = await asyncio.to_thread(
            state.rag.search, state.collection, query)
    except RagError as exc:
        # Tool-level degradation: the model sees an error string
        # and the run continues (never crashes the stream) — the
        # RAG service being unreachable is not a 500 for the chat
        # turn. Clear last_search so a later NOT_COVERED gap can't
        # carry stale near-misses/telemetry from an earlier
        # successful search in the same turn.
        state.last_search = []
        return f"search_docs failed: {exc}"
    state.used.add("docs")
    state.last_search = chunks
    lines = []
    for chunk in chunks:
        entry = dict(chunk)
        entry["n"] = len(state.excerpts) + 1
        state.excerpts.append(entry)
        lines.append(f"[{entry['n']}] {entry['title']} — "
                     f"{entry['heading_path']} ({entry['path']})\n"
                     f"{entry['text']}")
    return "\n\n".join(lines) or "No matches in the documentation."


@function_tool
async def read_doc(ctx: RunContextWrapper[SearchContext], path: str) -> str:
    """Return the full markdown of one documented file by its path."""
    state = ctx.context
    try:
        doc = await asyncio.to_thread(
            state.rag.get_document, state.collection, path)
    except RagNotFound:
        return f"{path!r} is not part of this documentation."
    except RagError as exc:
        return f"read_doc failed: {exc}"
    state.used.add("docs")
    return doc["markdown"]


@function_tool
async def list_docs(ctx: RunContextWrapper[SearchContext]) -> str:
    """List every document: quadrant, path, title."""
    state = ctx.context
    try:
        rows = await asyncio.to_thread(
            state.rag.list_documents, state.collection)
    except RagError as exc:
        return f"list_docs failed: {exc}"
    state.used.add("docs")
    return "\n".join(f"{r['quadrant']}: {r['path']} — {r['title']}"
                     for r in rows) or "No documents indexed."

