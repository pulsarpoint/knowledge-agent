"""Admin-only API for workspace management and cross-user activity."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from knowledge_admin.config import App, ServerConfig
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin import storage
from knowledge_admin.agents import skills
from knowledge_admin.documents import (
    api as documents,
    drafts as document_drafts,
    folders as documentation_folders,
    prompts as documentation_prompts,
)
from knowledge_admin.servers import discovery, environments, inventory_links_api, map_api, service_api
from knowledge_admin.code_projects import api as repository_api
from knowledge_admin.events import (
    api as events_api,
    handlers_api as event_handlers_api,
    notifications_api,
    schedules_api,
)
from knowledge_admin.jira import api as jira_api
from knowledge_admin.memory import api as memory_api
from knowledge_admin.rag import api as rag_api
from knowledge_admin.sessions import api as sessions_api


def build_api(
    apps: dict[str, App],
    server: ServerConfig,
    backoffice_client: httpx.Client | None,
    repository_store: RepositoryStore | None = None,
    jira_transport: httpx.BaseTransport | None = None,
) -> APIRouter:
    if len(apps) != 1:
        raise ValueError('Administration requires exactly one configured workspace')
    app_name = next(iter(apps))

    def _is_admin(request: Request) -> bool:
        groups = request.headers.get('x-forwarded-groups', '')
        return server.admin_group in (g.strip() for g in groups.split(',') if g.strip())

    def require_admin(request: Request) -> None:
        # 403 before any 404 — non-admins must not learn administrative data.
        if not _is_admin(request):
            raise HTTPException(status_code=403, detail='admin access required')

    router = APIRouter(prefix='/api', dependencies=[Depends(require_admin)])
    router.include_router(storage.build_api())
    from knowledge_admin.sessions import runs_api as agent_runs_api

    router.include_router(agent_runs_api.build_api(app_name, repository_store))
    router.include_router(sessions_api.build_api(app_name, repository_store))
    router.include_router(discovery.build_api(app_name, repository_store))
    router.include_router(repository_api.build_api(app_name, repository_store, backoffice_client))
    from knowledge_admin.servers import operations_api

    router.include_router(operations_api.build_api(app_name, repository_store))
    router.include_router(service_api.build_api(app_name, repository_store))
    router.include_router(inventory_links_api.build_api(app_name, repository_store))
    router.include_router(map_api.build_api(app_name, repository_store))
    router.include_router(environments.build_api(app_name, repository_store))
    router.include_router(skills.build_api(app_name, repository_store))
    router.include_router(documents.build_api(app_name, repository_store))
    router.include_router(document_drafts.build_api(app_name, repository_store))
    router.include_router(documentation_prompts.build_api(app_name, repository_store))
    router.include_router(documentation_folders.build_api(app_name, repository_store))
    from knowledge_admin.llm import api as llm_api, mcp_api
    from knowledge_admin.agents import (
        profiles_api,
        wizard as agent_wizard,
        clones as agent_clones,
        reviews as agent_reviews,
    )

    router.include_router(agent_wizard.build_api(app_name, repository_store))
    router.include_router(profiles_api.build_api(app_name, repository_store))
    router.include_router(agent_clones.build_api(app_name, repository_store))
    router.include_router(agent_reviews.build_api(app_name, repository_store))
    router.include_router(mcp_api.build_api(app_name, repository_store))
    router.include_router(llm_api.build_api(app_name, repository_store))
    router.include_router(jira_api.build_api(app_name, repository_store, jira_transport, backoffice_client))
    router.include_router(schedules_api.build_api(app_name, repository_store))
    router.include_router(events_api.build_api(app_name, repository_store))
    router.include_router(event_handlers_api.build_api(app_name, repository_store))
    router.include_router(notifications_api.build_api(app_name, repository_store))
    router.include_router(memory_api.build_api(app_name, repository_store))

    router.include_router(rag_api.build_api(app_name, repository_store, backoffice_client))
    from knowledge_admin.sessions import tracing as agent_tracing

    agent_tracing.setup()

    @router.get('/session')
    def admin_session():
        return {'is_admin': True}

    return router
