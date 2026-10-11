"""Server discovery: the server page's data, discovery runs (an ad-hoc agent session, purpose discovery, with SSH to
exactly one server, the inventory tools and the proposal tools of capabilities/discovery.py) and reviewing the
proposals they make. A run's transcript, messages, Stop and Close use the Generate documents session endpoints."""
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.access import request_actor
from knowledge_admin.generations_api import NO_MODEL, Selection
from knowledge_admin.knowledge_agents.config import load_model, model_fits
from knowledge_admin.knowledge_agents.ephemeral import ad_hoc_config
from knowledge_admin.knowledge_agents.run_resources import RunResourceError
from knowledge_admin.repositories import RepositoryStore

NOT_FOUND = 'Server not found.'
NOT_CONFIGURED = 'Set up SSH for this server first (SSH settings).'
NOT_ALLOWED = 'Turn on SSH allowed for this server first.'
PROPOSAL_TOOLS = ('propose_service', 'propose_document')
DISCOVERY_SYSTEM_PROMPT = '''You explore one server over SSH and propose what the inventory should record about it: the
services that run on it and documents about the server and its services. An administrator reviews every proposal.

The server: {name} (hostname {hostname}). Run commands with ssh_run and server_id {id}.

Read what the inventory already records before you explore (get_server_info_basic, get_server_services,
get_server_docs). Propose with propose_service and propose_document; when a proposal is refused, the result says why:
correct it and call again.'''
DISCOVERY_FINAL = 'When you finish, reply with a short report: what you found, what you proposed and what you could not check.'
RUNS = '''SELECT s.id,s.title,s.status,s.created_at,s.last_activity_at,m.name AS model_name,
    count(p.id) FILTER (WHERE p.status='pending')::int AS pending,
    count(p.id) FILTER (WHERE p.status='accepted')::int AS accepted,
    count(p.id) FILTER (WHERE p.status='rejected')::int AS rejected
    FROM ka.agent_sessions s LEFT JOIN ka.llm_models m ON m.id=s.model_id
    LEFT JOIN ka.discovery_proposals p ON p.session_id=s.id
    WHERE s.project_id=%s AND s.server_id=%s AND s.purpose='discovery'
    GROUP BY s.id,m.name ORDER BY s.created_at DESC,s.id LIMIT 50'''


class DiscoveryInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    model_id: UUID
    prompt: str = Field(min_length=1, max_length=100000)
    prompt_id: UUID | None = None


def readiness(server: dict) -> str | None:
    """Why a discovery run cannot start on this server, or None."""
    if not server['enabled'] or server['credential_id'] is None:
        return NOT_CONFIGURED
    if not server['ssh_allowed']:
        return NOT_ALLOWED
    return None


def discovery_config(conn, project: UUID, server: dict, model: dict, session_id: UUID) -> dict:
    """The configuration a run pins: the ad-hoc agent with SSH to exactly this server, its workspace and its run
    (which the proposal tools need), the proposal tools kept from subagents."""
    prompt = DISCOVERY_SYSTEM_PROMPT.format(name=server['name'], hostname=server['hostname'], id=server['id'])
    pinned = ad_hoc_config(conn, project, model=model, system_prompt=prompt, selection=Selection(server_ids=[server['id']]))
    pinned['experts'] = []  # ad_hoc_config pins every expert; each would reach its own servers
    pinned['project_id'] = str(project)
    pinned['discovery'] = {'session_id': str(session_id), 'server_id': str(server['id'])}
    for item in pinned['tools']['items']:
        if item['tool'] in PROPOSAL_TOOLS:
            item['subagent_allowed'] = False
    return pinned


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Server discovery storage is not configured.')
        return store

    def audit(conn, project, actor, action: str, resource_type: str, resource_id, details: dict) -> None:
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,%s,%s,%s)''', (project, actor, action, resource_type, resource_id, Jsonb(jsonable_encoder(details))))

    def server_row(conn, project, server_id: UUID, lock: bool = False) -> dict:
        row = conn.execute('SELECT id,name,hostname,enabled,ssh_allowed,credential_id FROM ka.servers WHERE id=%s AND project_id=%s'
                           + (' FOR SHARE' if lock else ''), (server_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return row

    @router.get('/servers/{server_id}')
    def server_page(server_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            access = server_row(conn, project, server_id)
            server = conn.execute('SELECT * FROM ka.server_assets WHERE id=%s', (server_id,)).fetchone()
            environments = conn.execute('''SELECT e.id,e.name FROM ka.environment_servers es JOIN ka.environments e
                ON e.id=es.environment_id WHERE es.server_id=%s ORDER BY lower(e.name)''', (server_id,)).fetchall()
            deployments = conn.execute('''SELECT d.id,d.service_id,s.name AS service_name,COALESCE(e.name,d.environment) AS environment,
                d.runtime,d.runtime_name,d.port,d.deployed_version FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id
                LEFT JOIN ka.environments e ON e.id=d.environment_id WHERE d.server_id=%s
                ORDER BY lower(s.name),environment,d.runtime_name''', (server_id,)).fetchall()
        reason = readiness(access)
        return {'server': server, 'environments': environments, 'deployments': deployments,
                'discovery': {'ready': reason is None, 'reason': reason}}

    @router.post('/servers/{server_id}/discovery', status_code=201)
    def start(server_id: UUID, value: DiscoveryInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            server = server_row(conn, project, server_id, lock=True)
            reason = readiness(server)
            if reason:
                raise HTTPException(400, reason)
            model = load_model(conn, value.model_id)
            if model is None or not model['enabled'] or not model_fits(model):
                raise HTTPException(400, NO_MODEL)
            actor = db.actor(conn, *request_actor(request))
            session_id = uuid4()
            try:
                pinned = discovery_config(conn, project, server, model, session_id)
            except RunResourceError as exc:
                raise HTTPException(400, str(exc)) from None
            snapshot = {'prompt_id': value.prompt_id, 'server_id': server_id}
            conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,purpose,server_id,
                prompt_snapshot,context_snapshot,agent_config,run_resources)
                VALUES (%s,%s,NULL,%s,%s,%s,'discovery',%s,%s,%s,%s,%s)""",
                (session_id, project, actor, model['id'], f"Discovery · {server['name']}", server_id,
                 Jsonb(jsonable_encoder(snapshot)), Jsonb({'server_id': str(server_id)}), Jsonb(jsonable_encoder(pinned)),
                 Jsonb(jsonable_encoder(pinned['run_resources']))))
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                         (session_id, actor, f'{value.prompt}\n\n{DISCOVERY_FINAL}'))
            audit(conn, project, actor, 'discovery.run.started', 'agent_session', session_id,
                  {'server_id': server_id, 'model_id': model['id'], 'prompt_id': value.prompt_id})
        return {'id': session_id}

    @router.get('/servers/{server_id}/discovery')
    def runs(server_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            server_row(conn, project, server_id)
            return {'runs': conn.execute(RUNS, (project, server_id)).fetchall()}

    return router
