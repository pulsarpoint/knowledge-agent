"""Standalone admin application."""

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from knowledge_admin.config import Config, ServerConfig
from knowledge_admin.api import build_api
from knowledge_admin.documents import backfill_exports
from knowledge_admin.live.session_hub import SessionHub
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
        backfill = asyncio.create_task(backfill_document_exports(repository_store))
        hub = None
        if repository_store is not None:  # live session pages: one database listener per process
            hub = app.state.session_hub = SessionHub(repository_store.conninfo)
            await hub.start()
        try:
            yield
        finally:
            backfill.cancel()
            await asyncio.gather(backfill, return_exceptions=True)
            if hub is not None:
                await hub.stop()

    app = FastAPI(title="Knowledge Agent Administration", docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)

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
        from knowledge_admin.capabilities import inventory
        inventory.configure(repository_store)
    return app
