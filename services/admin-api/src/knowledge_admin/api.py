"""Admin-only API for workspace management and cross-user activity."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from docs_wiki.config import App, ServerConfig
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin import activity, documents, documentation_workflows, documentation_generation, documentation_prompts, environments, agent_sessions, agent_versions, board, jira_api, repository_api, service_api, rag_api, skills, storage


def build_api(apps: dict[str, App], server: ServerConfig,
              backoffice_client: httpx.Client | None,
              repository_store: RepositoryStore | None = None,
              jira_transport: httpx.BaseTransport | None = None) -> APIRouter:
    if len(apps) != 1:
        raise ValueError("Administration requires exactly one configured workspace")
    app_name = next(iter(apps))

    def _is_admin(request: Request) -> bool:
        groups = request.headers.get("x-forwarded-groups", "")
        return server.admin_group in (
            g.strip() for g in groups.split(",") if g.strip())

    def require_admin(request: Request) -> None:
        # 403 before any 404 — non-admins must not learn administrative data.
        if not _is_admin(request):
            raise HTTPException(status_code=403,
                                detail="admin access required")

    router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
    router.include_router(storage.build_api())
    from knowledge_admin import agent_runs_api
    router.include_router(agent_runs_api.build_api(app_name, repository_store))
    router.include_router(activity.build_api(app_name, repository_store))
    router.include_router(board.build_api(app_name, repository_store))
    router.include_router(agent_versions.build_api(app_name, repository_store))
    router.include_router(agent_sessions.build_api(app_name, repository_store))
    router.include_router(repository_api.build_api(app_name, repository_store))
    from knowledge_admin import operations_api
    router.include_router(operations_api.build_api(app_name, repository_store))
    router.include_router(service_api.build_api(app_name, repository_store))
    router.include_router(environments.build_api(app_name, repository_store))
    router.include_router(skills.build_api(app_name, repository_store))
    router.include_router(documentation_workflows.build_api(app_name, repository_store))
    router.include_router(documents.build_api(app_name, repository_store, backoffice_client))
    router.include_router(documentation_generation.build_api(app_name, repository_store))
    router.include_router(documentation_prompts.build_api(app_name, repository_store))
    from knowledge_admin import llm_api, prompts_api, mcp_api, profiles_api, agent_wizard, reader_settings
    router.include_router(agent_wizard.build_api(app_name, repository_store))
    router.include_router(profiles_api.build_api(app_name, repository_store))
    router.include_router(reader_settings.build_api(app_name, repository_store))
    router.include_router(mcp_api.build_api(app_name, repository_store))
    router.include_router(llm_api.build_api(app_name, repository_store))
    router.include_router(prompts_api.build_api(app_name, repository_store))
    router.include_router(jira_api.build_api(app_name, repository_store, jira_transport, backoffice_client))

    router.include_router(rag_api.build_api(app_name, repository_store, backoffice_client))
    from knowledge_admin.agent_api import build_agent_api
    router.include_router(build_agent_api(server, backoffice_client, app_name))

    @router.get("/session")
    def admin_session():
        return {"is_admin": True}

    return router
