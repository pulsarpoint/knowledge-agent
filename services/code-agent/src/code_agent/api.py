"""Repository inventory, source browsing and placeholder code questions."""
from __future__ import annotations


from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from code_agent.config import Config
from code_agent.source_tools import build_api as source_tools_api
from knowledge_llm import ModelUnavailable, managed
from knowledge_llm.profiles import load_profile


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"message": message}})


def _validation_message(exc: RequestValidationError) -> str:
    """Concise, single-line summary of a body-validation failure -- the
    API's 4xx contract is {"error": {"message": str}}, not FastAPI's own
    {"detail": [...]} list of structured error dicts. Copied from rag's
    (and originally docs-wiki's) `_validation_message`."""
    errors = exc.errors()
    if not errors:
        return "invalid request"
    parts = []
    for err in errors:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        msg = err.get("msg", "invalid value")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


class AskRequest(BaseModel):
    agent_id: str = Field(default="code", min_length=1, max_length=100)
    question: str = Field(..., max_length=10_000)
    context: str | None = Field(None, max_length=4000)

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        # A length constraint alone (Field(min_length=1)) would accept a
        # whitespace-only question; strip-then-check is what the brief
        # means by "1..10000, stripped non-empty". Runs as an "after"
        # validator, so Field(max_length=10_000) has already rejected an
        # oversize body before this ever sees it.
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be empty")
        return stripped


import os
from pathlib import Path
from code_agent.inventory import repositories as inventory_repositories


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI()
    def available_repos():
        root = os.environ.get('CODE_ROOT')
        if root:
            return inventory_repositories(Path(root))
        return [(repo, repo.name) for repo in cfg.repos]

    app.include_router(source_tools_api(available_repos))

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(request: Request,
                                   exc: RequestValidationError):
        # This whole app is the JSON API, so every route is bound to the
        # {"error": {"message": ...}} 4xx contract unconditionally.
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

    @app.get("/repos")
    def list_repos():
        try:
            return [{"name": r.name, "label": label, "path_exists": r.path.is_dir(),
                     "backend": None} for r, label in available_repos()]
        except Exception:
            return _error(503, "Repository inventory unavailable")

    @app.post("/repos/{name}/ask")
    def ask(name: str, body: AskRequest):
        try:
            repo = next((repo for repo, _ in available_repos() if repo.name == name), None)
        except Exception:
            return _error(503, "Repository inventory unavailable")
        if repo is None:
            # No stream has started yet -- a plain 404 JSON error, same
            # shape as every other 4xx this service returns.
            return _error(404, "unknown repo")

        if managed():
            try:
                selected = load_profile(body.agent_id)
                if not selected['repositories_enabled'] or (not selected['all_repositories'] and name not in selected['repository_ids']):
                    return _error(403, 'Repository is outside this agent configuration.')
            except ModelUnavailable as exc:
                return _error(409, str(exc))
        return PlainTextResponse('TODO')

    return app
