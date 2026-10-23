"""Server discovery: the server page's data and whether a run can start from the server. A discovery run is an
ordinary run started from a server through /api/sessions (sessions/start.py: started_from.server_id, purpose
resource); what it does is its prompt's text ("Server discovery"), and it saves its findings as drafts."""

from functools import partial
from uuid import UUID

from fastapi import APIRouter, HTTPException

from knowledge_admin.db import storage, workspace
from knowledge_admin.inventory import store as inventory
from knowledge_admin.repositories import RepositoryStore

NOT_FOUND = 'Server not found.'
NOT_CONFIGURED = 'Set up SSH for this server first (SSH settings).'
NOT_ALLOWED = 'Turn on SSH allowed for this server first.'


def readiness(server: dict) -> str | None:
    """Why a run cannot start from this server, or None."""
    if not server['enabled'] or server['credential_id'] is None:
        return NOT_CONFIGURED
    if not server['ssh_allowed']:
        return NOT_ALLOWED
    return None


def server_row(conn, project, server_id: UUID, lock: bool = False) -> dict:
    row = conn.execute(
        'SELECT id,name,hostname,enabled,ssh_allowed,credential_id FROM ka.servers WHERE id=%s AND project_id=%s'
        + (' FOR SHARE' if lock else ''),
        (server_id, project),
    ).fetchone()
    if row is None:
        raise HTTPException(404, NOT_FOUND)
    return row


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    database = partial(storage, store, 'Server discovery storage')

    @router.get('/servers/{server_id}')
    def server_page(server_id: UUID) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            access = server_row(conn, project, server_id)
            server = inventory.get(conn, project, 'server', server_id)
            environments = [
                {'id': e['id'], 'name': e['name']}
                for e in inventory.list_environments(conn, project, server_id=server_id)
            ]
            deployments = conn.execute(
                """SELECT d.id,d.service_id,s.name AS service_name,e.name AS environment,
                d.runtime,d.runtime_name,d.port,d.deployed_version,d.status,d.revision FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id
                JOIN ka.environments e ON e.id=d.environment_id WHERE d.server_id=%s
                ORDER BY lower(s.name),environment,d.runtime_name""",
                (server_id,),
            ).fetchall()
        reason = readiness(access)
        return {
            'server': server,
            'environments': environments,
            'deployments': deployments,
            'ssh_ready': bool(access['enabled'] and access['credential_id'] is not None),
            'discovery': {'ready': reason is None, 'reason': reason},
        }

    return router
