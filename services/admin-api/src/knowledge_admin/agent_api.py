"""Admin context discovery, conversation history, and TODO test endpoints."""
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter

from docs_wiki import search_api
from knowledge_admin import agent_tracing
from docs_wiki.config import state_root
from docs_wiki.workspace_client import WorkspaceRagClient


def build_agent_api(server, backoffice, app_name):
    rag = WorkspaceRagClient(str(backoffice.base_url), os.environ.get('RAG_BACKOFFICE_KEY', '')) if backoffice is not None else None
    agent_tracing.setup()
    router = APIRouter()
    router.include_router(search_api.build_api('/agents/search', state_root(server) / 'admin-agent-tests',
                                rag, audit_dir=state_root(server) / app_name, profiles=True))

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if rag is not None:
                rag.close()
    router.lifespan_context = lifespan
    return router
