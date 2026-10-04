"""JSON API for the React UI: read surface, chat, and sessions — every
route is backed by the RAG service through `RagClient` (a dumb HTTP
transport with zero domain logic); grouping, freshness, and response
shaping happen here."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, PlainTextResponse
from pydantic import BaseModel

from docs_wiki import audit, freshness, gaps, sessions, webapp
from docs_wiki.config import App, ServerConfig
from docs_wiki.rag_client import RagClient, RagError, RagNotFound
from docs_wiki.webapp import MAX_QUESTION


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"message": message}})


def _caller_email(request: Request) -> str:
    return (request.headers.get("x-forwarded-email")
            or request.headers.get("x-forwarded-user")
            or "unknown")


class ChatRequest(BaseModel):
    question: str = ""
    session: str | None = None
    sources: dict[str, bool] | None = None




def _rag_unavailable() -> JSONResponse:
    # rag_client is None whenever no RAG service URL was ever configured
    # (webapp.create_app leaves it unset rather than raising) — the read
    # routes below all depend on it, so this is the same "documentation
    # service unavailable" 502 JSON-error shape a RagError raised mid-call
    # would produce, not a 500 from an unguarded None dereference.
    return _error(502, "documentation service unavailable: rag not configured")


def _doc_freshness(target: App, meta: dict, cache: dict) -> str:
    """Freshness badge with a per-request memo keyed on the analyzed
    commits: sibling docs usually share shas, so the git calls collapse
    to one per distinct (service, sha) set per request."""
    commits = meta.get("analyzed_commits") or {}
    key = tuple(sorted(commits.items()))
    if key not in cache:
        per_service = freshness.freshness(target.app_root, commits)
        cache[key] = freshness.badge(per_service)
    return cache[key]


def build_api(apps: dict[str, App], server: ServerConfig,
              rag_client: RagClient | None,
              context_enabled: bool = False) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/apps")
    def list_apps(request: Request):
        return [{"name": name, "ask_enabled": True,
                 **({'context_enabled': True} if context_enabled else {}),
                 "ask_reason": None,
                 "sources": {"docs": rag_client is not None}}
                for name in apps]

    @router.get("/{app_name}/toc")
    def toc(app_name: str):
        target = apps.get(app_name)
        if target is None:
            return _error(404, "unknown app")
        if rag_client is None:
            return _rag_unavailable()
        try:
            catalog = rag_client.list_documents(app_name)
        except RagError as exc:
            return _error(502, f"documentation service unavailable: {exc}")
        cache: dict = {}
        groups: dict[str, list] = {}
        for entry in catalog:
            meta = entry.get("front_matter") or {}
            groups.setdefault(entry["quadrant"], []).append({
                "title": entry["title"], "path": entry["path"],
                "freshness": _doc_freshness(target, meta, cache)})
        return [{"group": webapp.GROUP_LABELS.get(q, q), "docs": docs}
                for q, docs in groups.items()]

    @router.get("/{app_name}/doc")
    def doc(app_name: str, path: str = ""):
        target = apps.get(app_name)
        if target is None:
            return _error(404, "unknown app")
        if not path:
            return _error(400, "path query parameter is required")
        if rag_client is None:
            return _rag_unavailable()
        try:
            row = rag_client.get_document(app_name, path)
        except RagNotFound:
            return _error(404, f"no such document: {path}")
        except RagError as exc:
            return _error(502, f"documentation service unavailable: {exc}")
        meta = row.get("front_matter") or {}
        return {"path": path, "title": row["title"],
                "markdown": row["markdown"],
                "front_matter": meta,
                "freshness": _doc_freshness(target, meta, {}),
                "headings": row["headings"]}

    @router.get("/{app_name}/search")
    def search(app_name: str, q: str = ""):
        target = apps.get(app_name)
        if target is None:
            return _error(404, "unknown app")
        if not q:
            return []
        if rag_client is None:
            return _rag_unavailable()
        try:
            # Phase-4's local search returned up to indexer.search's
            # limit=20; rag_client.search defaults to top=8, so this must
            # be passed explicitly to stay byte-compatible with the old
            # result-count ceiling.
            hits = rag_client.search(app_name, q, top=20)
        except RagError as exc:
            return _error(502, f"documentation service unavailable: {exc}")
        return [{"path": h["path"], "title": h["title"],
                "heading_path": h["heading_path"], "snippet": h["text"],
                "anchor": h.get("anchor")} for h in hits]

    @router.get("/{app_name}/gaps")
    def gaps_list(app_name: str):
        target = apps.get(app_name)
        if target is None:
            return _error(404, "unknown app")
        entries, skipped = gaps.load_gaps(webapp.app_state_dir(server, app_name))
        return {"gaps": entries, "skipped": skipped}

    @router.post("/{app_name}/chat")
    async def chat(app_name: str, body: ChatRequest, request: Request):
        target = apps.get(app_name)
        if target is None:
            return _error(404, "unknown app")
        question = body.question.strip()
        if not question:
            return _error(400, "Ask needs a question.")
        if len(question) > MAX_QUESTION:
            return _error(400, "Question too long (10,000 character limit).")
        if body.session is not None:
            state_dir = webapp.app_state_dir(server, app_name)
            if not sessions.KEY_RE.fullmatch(body.session) or not sessions.exists(state_dir, body.session):
                return _error(404, "unknown conversation")
        return PlainTextResponse("TODO")

    @router.get("/{app_name}/chats")
    def my_chats(app_name: str, request: Request):
        """The caller's own conversations, derived from the audit log
        (participation-based: every session this email has asked in),
        filtered to sessions that still exist — a user-deleted
        conversation drops out here while its audit rows remain visible
        to admins. Identity-less callers get an empty list and the UI
        falls back to its localStorage history."""
        if apps.get(app_name) is None:
            return _error(404, "unknown app")
        email = _caller_email(request)
        if email == "unknown":
            return {"sessions": []}
        state_dir = webapp.app_state_dir(server, app_name)
        live = [s for s in audit.sessions_for(state_dir, email)
                if sessions.exists(state_dir, s["session_key"])]
        return {"sessions": live[:50]}

    @router.get("/{app_name}/chats/{key}")
    async def get_chat(app_name: str, key: str):
        target = apps.get(app_name)
        if target is None:
            return _error(404, "unknown app")
        state_dir = webapp.app_state_dir(server, app_name)
        if not sessions.KEY_RE.fullmatch(key) or not sessions.exists(
                state_dir, key):
            return _error(404, "unknown conversation")
        messages = await sessions.transcript(state_dir, key)
        return {"session": key, "messages": messages}

    @router.delete("/{app_name}/chats/{key}")
    async def delete_chat(app_name: str, key: str):
        target = apps.get(app_name)
        if target is None:
            return _error(404, "unknown app")
        state_dir = webapp.app_state_dir(server, app_name)
        if not sessions.KEY_RE.fullmatch(key) or not sessions.exists(
                state_dir, key):
            return _error(404, "unknown conversation")
        await sessions.delete(state_dir, key)
        return Response(status_code=204)

    return router
