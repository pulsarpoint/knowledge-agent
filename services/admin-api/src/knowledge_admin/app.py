"""Standalone admin application, including isolated agent tests."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import httpx
import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from knowledge_admin.config import Config, ServerConfig
from knowledge_admin.agent_sessions import expire_sessions
from knowledge_admin.api import build_api
from knowledge_admin.documents import backfill_exports
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)


async def backfill_document_exports(store: RepositoryStore | None) -> None:
    """Re-export documents that lack a current export; runs in the background and never blocks startup."""
    if store is None:
        return
    try:
        await asyncio.to_thread(backfill_exports, store)
    except Exception:  # noqa: BLE001 - a startup convenience must never take the service down
        logger.exception('Document export backfill failed')


def create_app(cfg: Config, *, server: ServerConfig,
               backoffice_client: httpx.Client | None = None,
               repository_store: RepositoryStore | None = None,
               jira_transport: httpx.BaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        async def expire_idle_sessions():
            while True:
                if repository_store is not None:
                    with suppress(psycopg.Error):
                        await asyncio.to_thread(expire_sessions, repository_store)
                await asyncio.sleep(60)
        expiry = asyncio.create_task(expire_idle_sessions())
        backfill = asyncio.create_task(backfill_document_exports(repository_store))
        try:
            yield
        finally:
            expiry.cancel()
            backfill.cancel()
            tasks = list(app.state.agent_runs)
            for task in tasks:
                task.cancel()
            await asyncio.gather(expiry, backfill, *tasks, return_exceptions=True)

    app = FastAPI(title="Knowledge Agent Administration", docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.agent_runs = set()

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"message": str(exc.detail)}})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Validation inputs may contain a GitHub token. Return messages only, never inputs/context.
        message = "; ".join(error["msg"] for error in exc.errors())
        return JSONResponse(status_code=400, content={"error": {"message": message}})

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc: psycopg.Error):
        return JSONResponse(status_code=503, content={"error": {"message": "Administration database unavailable"}})

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(build_api({a.name: a for a in cfg.apps}, server, backoffice_client, repository_store, jira_transport))
    if repository_store is not None:
        from knowledge_admin import run_record
        run_record.configure(repository_store, cfg.apps[0].name)
    return app
