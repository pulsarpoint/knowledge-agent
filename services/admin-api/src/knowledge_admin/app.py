"""Standalone admin application."""

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from knowledge_admin import http_errors
from knowledge_admin.config import Config, ServerConfig
from knowledge_admin.api import build_api
from knowledge_admin.sessions.hub import SessionHub
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.tools.fixture import sync_tools
from knowledge_admin.skills.store import startup_cleanup as skill_cleanup


def create_app(
    cfg: Config,
    *,
    server: ServerConfig,
    repository_store: RepositoryStore | None = None,
    jira_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        if repository_store is not None:  # ka.tools = tools/tools.json (the fixture is the only writer)
            await asyncio.to_thread(sync_tools, repository_store)
            await asyncio.to_thread(skill_cleanup, repository_store)
        hub = None
        if repository_store is not None:  # live session pages: one database listener per process
            hub = app.state.session_hub = SessionHub(repository_store.conninfo)
            await hub.start()
        try:
            yield
        finally:
            if hub is not None:
                await hub.stop()

    app = FastAPI(
        title='Knowledge Agent Administration', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    http_errors.install(app)

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    app.include_router(build_api({a.name: a for a in cfg.apps}, server, repository_store, jira_transport))
    if repository_store is not None:
        from knowledge_admin.sessions import run_record

        run_record.configure(repository_store, cfg.apps[0].name)
        from knowledge_admin.capabilities import inventory

        inventory.configure(repository_store)
    return app
