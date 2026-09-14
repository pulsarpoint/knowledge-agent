"""FastAPI app factory: JSON API + SPA serving."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (http_exception_handler,
                                        request_validation_exception_handler)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from docs_wiki import tracing
from docs_wiki.code_client import CodeAgentClient
from docs_wiki.config import (App, Config, ServerConfig, resolve_code_agent,
                              resolve_rag, state_root)
from docs_wiki.rag_client import RagClient

_PACKAGE_DIR = Path(__file__).resolve().parent

_NO_UI_PAGE = """<!doctype html><meta charset="utf-8"><title>docs-wiki</title>
<body style="font-family: system-ui; max-width: 40rem; margin: 4rem auto;">
<h1>docs-wiki API is running</h1>
<p>There is no UI build to serve. The JSON API is available under
<code>/api</code>. To serve the web UI, build it
(<code>pnpm build</code> in <code>services/docs-wiki-ui</code>) and
point <code>[server] ui_dist</code> or <code>DOCS_WIKI_UI_DIST</code> at
the <code>build/client</code> folder — or run the deploy bundle in
<code>deploy/</code>.</p>"""


def _resolve_ui_dist(server) -> Path | None:
    """Spec §5 resolution order: explicit config/env → packaged ui/ →
    sibling repo build. A configured path without an index.html is
    treated as absent (informational page) rather than an error, so a
    half-finished build never takes the API down."""
    candidates = []
    if server.ui_dist:
        candidates.append(Path(server.ui_dist))
    candidates.append(_PACKAGE_DIR / "ui")
    candidates.append(_PACKAGE_DIR.parents[2] / "docs-wiki-ui" / "build" / "client")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


def app_state_dir(server: ServerConfig, app_name: str) -> Path:
    """Where one app's gaps/sessions live: `<state_root>/<app_name>`. The
    directory itself is created lazily by the first write (gaps.append_gap
    / sessions.db_path), not here."""
    return state_root(server) / app_name


MAX_QUESTION = 10_000

GROUP_LABELS = {
    "how-to": "How-to — do something",
    "reference": "Reference — look something up",
    "explanation": "Explanation — understand",
    "tutorials": "Tutorials — learn",
    "index": "Overview",
    "other": "Other",
}


def _validation_message(exc: RequestValidationError) -> str:
    """Concise, single-line summary of a body-validation failure — the
    `/api` 4xx contract is {"error": {"message": str}}, not FastAPI's own
    {"detail": [...]} list of structured error dicts."""
    errors = exc.errors()
    if not errors:
        return "invalid request"
    parts = []
    for err in errors:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        msg = err.get("msg", "invalid value")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def create_app(cfg: Config, server=None, rag_client=None, code_client=None) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(request: Request,
                                   exc: RequestValidationError):
        # Only the JSON API is bound to the {"error": {"message": ...}}
        # 4xx contract; any other route (there are none server-rendered
        # anymore, but the SPA catch-all and future non-API routes may
        # exist) keeps FastAPI's default 422 {"detail": [...]} shape.
        if not request.url.path.startswith("/api"):
            return await request_validation_exception_handler(request, exc)
        return JSONResponse(status_code=400,
                            content={"error": {
                                "message": _validation_message(exc)}})

    @app.exception_handler(StarletteHTTPException)
    async def _on_http_exception(request: Request,
                                 exc: StarletteHTTPException):
        if not request.url.path.startswith("/api"):
            return await http_exception_handler(request, exc)
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"message": str(exc.detail)}},
                            headers=exc.headers)

    apps: dict[str, App] = {a.name: a for a in cfg.apps}

    from docs_wiki import api as api_mod  # function-scope: api imports webapp

    server = server or ServerConfig(cors_origin=None,
                                    session_retention_days=90)
    tracing.setup(os.environ.get("DOCS_WIKI_PHOENIX_ENDPOINT"),
                  os.environ.get("DOCS_WIKI_PHOENIX_API_KEY"))
    if rag_client is None:
        rag_url = resolve_rag(cfg.rag)
        if rag_url:
            rag_client = RagClient(rag_url)
    if code_client is None:
        code_url = resolve_code_agent(cfg.code_agent)
        if code_url:
            code_client = CodeAgentClient(code_url)
    if server.cors_origin:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware,
                           allow_origins=[server.cors_origin],
                           allow_methods=["*"], allow_headers=["*"])
    workspace_key = os.environ.get('RAG_BACKOFFICE_KEY')
    workspace_rag = None
    if workspace_key and resolve_rag(cfg.rag):
        from docs_wiki.workspace_client import WorkspaceRagClient
        workspace_rag = WorkspaceRagClient(resolve_rag(cfg.rag), workspace_key)
        rag_client = workspace_rag
    reader_router = api_mod.build_api(
        apps, server, rag_client, code_client=code_client, context_enabled=workspace_rag is not None)
    if workspace_rag is not None:
        from docs_wiki.search_api import build_api as build_search_api
        reader_router.routes[:] = [route for route in reader_router.routes
            if route.path not in {'/api/{app_name}/chat', '/api/{app_name}/chats', '/api/{app_name}/chats/{key}', '/api/{app_name}/chat/{session_key}/escalate'}]
        for name in apps:
            app.include_router(build_search_api(f'/api/{name}', state_root(server) / 'search-conversations' / name,
                workspace_rag, code_client,
                audit_dir=state_root(server) / name))
    app.include_router(reader_router)

    from docs_wiki import sessions as sessions_mod
    # Prune every app's state, not just currently-configured ones: a
    # state_dir subfolder for an app dropped from config still holds real
    # session data until its own retention window elapses.
    root = state_root(server)
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if entry.is_dir():
                sessions_mod.prune(entry, server.session_retention_days)

    ui_dist = _resolve_ui_dist(server)
    assets_dir = (ui_dist / "assets") if ui_dist is not None else None

    @app.api_route("/{full_path:path}",
                   methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
                   include_in_schema=False)
    def spa(full_path: str, request: Request):
        if (full_path == "api" or full_path.startswith("api/")
                or "admin" in full_path.split("/")[:2]):
            # only reachable when no /api route matched: keep the API's
            # JSON error contract instead of serving HTML
            return JSONResponse(status_code=404,
                                content={"error": {"message": "not found"}})
        if request.method != "GET":
            # a SPA never POSTs/PUTs/etc. to itself; there is nothing
            # useful to serve, so non-GET falls to the same JSON 404
            # shape as an unknown /api route rather than index.html.
            return JSONResponse(status_code=404,
                                content={"error": {"message": "not found"}})
        if ui_dist is None:
            return HTMLResponse(_NO_UI_PAGE)
        if full_path:
            try:
                candidate = (ui_dist / full_path).resolve()
                valid = candidate.is_file() and candidate.is_relative_to(ui_dist)
            except (ValueError, OSError):
                # e.g. an embedded null byte (reachable via %00) makes
                # Path.resolve() raise instead of returning a path — any
                # such malformed candidate is just not a servable file,
                # same as a missing one: fall through to index.html rather
                # than let it become an unhandled 500.
                valid = False
            if valid:
                # Decide the cache header from where the RESOLVED file
                # actually lives, not the raw request path — a traversal
                # like `/assets/%2e%2e/index.html` resolves outside
                # assets_dir even though the raw path starts with
                # "assets/", and must not get the immutable header meant
                # for hashed build assets.
                if candidate.is_relative_to(assets_dir):
                    return FileResponse(candidate, headers={
                        "Cache-Control": "public, max-age=31536000, immutable"})
                return FileResponse(candidate)
        return FileResponse(ui_dist / "index.html",
                            headers={"Cache-Control": "no-cache"})

    return app
