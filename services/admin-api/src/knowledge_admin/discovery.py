"""Server discovery: the server page's data, discovery runs (an ad-hoc agent session, purpose discovery, with SSH to
exactly one server, the inventory tools and the proposal tools of capabilities/discovery.py) and reviewing the
proposals they make. A run's transcript, messages, Stop and Close use the Generate documents session endpoints."""
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_admin.access import request_actor
from knowledge_admin.documents import (DocumentInput, document_slug, export_args, export_conflict, export_document, export_input,
                                       save_document)
from knowledge_admin.sessions.rows import NO_MODEL, Selection
from knowledge_admin.knowledge_agents.config import load_model, model_fits
from knowledge_admin.knowledge_agents.ephemeral import ad_hoc_config
from knowledge_admin.knowledge_agents.run_resources import RunResourceError
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.service_api import save_service
from knowledge_admin.services import DeploymentInput, ServiceDependencyInput, ServiceInput

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


NO_RUN = 'Discovery run not found.'
NO_PROPOSAL = 'Proposal not found.'
DECIDED = 'This proposal was already accepted or rejected.'
STALE = 'This proposal changed since you opened it. Review it again.'
SERVICE_CHANGED = 'This service changed since the proposal was made. Review it again.'
SERVICE_ADDED = 'A service named {name} was added since this proposal was made. It is now shown as an update. Review it again.'
DOCUMENT_CHANGED = 'This document changed since the proposal was made. Review it again.'
DOCUMENT_ADDED = 'A document titled {title} was added since this proposal was made. It is now shown as a new version. Review it again.'
ACCEPT_SERVICE_FIRST = 'Accept the service first.'
SERVICE_GONE = 'The service {name} no longer exists. Reject this proposal.'
EXPORT_FAILED = 'The document was saved. File export failed; retry Export to folder from the document page.'
SERVICE_FIELDS = ('service_type', 'service_role', 'description', 'owner', 'operations_notes')
DEPLOYMENT_FIELDS = ('runtime', 'runtime_name', 'port', 'log_location', 'deployed_version')
ServiceField = Literal['service_type', 'service_role', 'description', 'owner', 'operations_notes']
CURRENT_SERVICE = '''SELECT id,name,revision,service_type,service_role,description,owner,operations_notes
    FROM ka.services WHERE id=%s'''
DEPLOYMENT_COLUMNS = 'id,server_id,environment,environment_id,runtime,runtime_name,port,log_location,deployed_version'


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


class Acceptance(Decision):
    fields: list[ServiceField] | None = None


def changes(proposed: dict, current: dict) -> list[str]:
    """The fields a service proposal would change: a proposed value that says something and differs."""
    return [field for field in SERVICE_FIELDS
            if proposed.get(field) not in (None, '', 'unclassified') and proposed[field] != current[field]]


def matching_deployment(conn, service_id, server_id, deployment: dict) -> dict | None:
    """The service's deployment a proposal updates: on this server in the same environment, with the same runtime name,
    or the only one there."""
    rows = conn.execute(f'''SELECT {DEPLOYMENT_COLUMNS} FROM ka.service_deployments WHERE service_id=%s AND server_id=%s
        AND (environment_id=%s OR (environment_id IS NULL AND %s::uuid IS NULL AND lower(environment)=lower(%s)))
        ORDER BY id''', (service_id, server_id, deployment['environment_id'], deployment['environment_id'],
                         deployment['environment'])).fetchall()
    same = [row for row in rows if row['runtime_name'] == deployment['runtime_name']]
    return same[0] if same else rows[0] if len(rows) == 1 else None


def first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    message = str(error['msg']).removeprefix('Value error, ')
    location = '.'.join(str(part) for part in error['loc'])
    return f'{location}: {message}' if location else message


def free_slug(conn, project, folder: str, base: str) -> str:
    """The first slug in the folder that no document has and whose export does not overlap another export."""
    base = base[:110].rstrip('-') or 'document'
    for number in range(1, 1000):
        slug = base if number == 1 else f'{base}-{number}'
        if conn.execute('SELECT 1 FROM ka.documents WHERE project_id=%s AND folder=%s AND slug=%s',
                        (project, folder, slug)).fetchone() is None and export_conflict(conn, project, slug, folder) is None:
            return slug
    raise HTTPException(409, 'Too many documents with this name. Rename one and try again.')


def free_folder(conn, project, server_slug: str) -> str:
    """servers/<server>, or servers/<server>-2, -3, ... when the folder itself cannot hold exports (a document is
    named like it, or a folder level is a document's export): the conflict does not depend on the slug. When
    servers itself is a document's export, server-documents/<server> is used instead."""
    for root in ('servers', 'server-documents'):
        for number in range(1, 100):
            folder = f'{root}/{server_slug}' if number == 1 else f'{root}/{server_slug}-{number}'
            if export_conflict(conn, project, 'probe-slug-0', folder) is None:
                return folder
    raise HTTPException(409, 'No folder is free for this server\'s documents. Rename a document or folder and try again.')


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
                'ssh_ready': bool(access['enabled'] and access['credential_id'] is not None),
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

    def pending(conn, project, proposal_id: UUID, revision: int) -> dict:
        row = conn.execute('SELECT * FROM ka.discovery_proposals WHERE id=%s AND project_id=%s FOR UPDATE',
                           (proposal_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NO_PROPOSAL)
        if row['status'] != 'pending':
            raise HTTPException(409, DECIDED)
        if row['revision'] != revision:
            raise HTTPException(409, STALE)
        return row

    def convert(conn, proposal_id: UUID, message: str, **columns) -> None:
        """A "new" proposal whose entry was added meanwhile becomes an update / a new version; kept even though the
        request fails, so the administrator sees it on reload."""
        sets = ','.join(f'{name}=%s' for name in columns)
        conn.execute(f'UPDATE ka.discovery_proposals SET {sets},revision=revision+1 WHERE id=%s', (*columns.values(), proposal_id))
        conn.commit()
        raise HTTPException(409, message)

    def accept_service(conn, project, actor, row: dict, fields: list[str] | None) -> UUID:
        payload = row['payload']
        proposed = payload['deployment']
        deployment = DeploymentInput(server_id=row['server_id'], environment_id=proposed['environment_id'],
                                     environment=proposed['environment'], **{key: proposed[key] for key in DEPLOYMENT_FIELDS})
        dependencies = [UUID(item['id']) for item in payload['depends_on']]
        code_projects = [UUID(item['id']) for item in payload['built_from']]
        if row['service_id'] is None:
            clash = conn.execute('SELECT id,name,revision FROM ka.services WHERE project_id=%s AND lower(name)=lower(%s)',
                                 (project, payload['name'])).fetchone()
            if clash is not None:
                convert(conn, row['id'], SERVICE_ADDED.format(name=clash['name']), service_id=clash['id'],
                        payload=Jsonb({**payload, 'matched_revision': clash['revision']}))
            value = ServiceInput(name=payload['name'], **{field: payload[field] for field in SERVICE_FIELDS},
                                 repository_ids=code_projects, deployments=[deployment],
                                 dependencies=[ServiceDependencyInput(service_id=item) for item in dependencies])
            return save_service(conn, project, actor, value, None)['id']
        current = conn.execute(CURRENT_SERVICE, (row['service_id'],)).fetchone()
        if current is None:
            raise HTTPException(409, SERVICE_CHANGED)
        if current['revision'] != payload.get('matched_revision'):
            # Accepting another proposal can bump this service's revision (a dependency link); keep the new one so a
            # reload shows the current values and the next accept works.
            convert(conn, row['id'], SERVICE_CHANGED, payload=Jsonb({**payload, 'matched_revision': current['revision']}))
        changed = changes(payload, current)
        chosen = changed if fields is None else [field for field in fields if field in changed]
        deployments = [DeploymentInput(**item) for item in conn.execute(
            f'SELECT {DEPLOYMENT_COLUMNS} FROM ka.service_deployments WHERE service_id=%s ORDER BY id', (current['id'],))]
        match = matching_deployment(conn, current['id'], row['server_id'], proposed)
        if match is None:
            deployments.append(deployment)
        else:
            deployments = [item.model_copy(update={key: proposed[key] for key in DEPLOYMENT_FIELDS
                                                   if proposed[key] not in (None, '')}) if item.id == match['id'] else item
                           for item in deployments]
        repositories = [item['to_code_project_id'] for item in conn.execute('''SELECT l.to_code_project_id FROM ka.inventory_links l
            JOIN ka.repositories r ON r.id=l.to_code_project_id AND r.archived_at IS NULL
            WHERE l.kind='built_from' AND l.from_service_id=%s ORDER BY l.to_code_project_id''', (current['id'],))]
        links = {}
        if dependencies:
            links['dependencies'] = [ServiceDependencyInput(service_id=item['to_service_id'], description=item['description'])
                                     for item in conn.execute('''SELECT to_service_id,description FROM ka.inventory_links
                                         WHERE kind='depends_on' AND from_service_id=%s ORDER BY to_service_id''', (current['id'],))]
            known = {link.service_id for link in links['dependencies']}
            links['dependencies'] += [ServiceDependencyInput(service_id=item) for item in dependencies if item not in known]
        value = ServiceInput(name=current['name'],
                             **{field: payload[field] if field in chosen else current[field] for field in SERVICE_FIELDS},
                             repository_ids=list(dict.fromkeys([*repositories, *code_projects])),
                             deployments=deployments, revision=current['revision'], **links)
        return save_service(conn, project, actor, value, current['id'], keep_archived_links=True)['id']

    def accept_document(conn, project, actor, row: dict) -> tuple[UUID, str | None]:
        payload = row['payload']
        server = conn.execute('SELECT id,name FROM ka.servers WHERE id=%s', (row['server_id'],)).fetchone()
        if row['service_proposal_id'] is not None:
            parent = conn.execute('SELECT status,result_id,payload FROM ka.discovery_proposals WHERE id=%s',
                                  (row['service_proposal_id'],)).fetchone()
            if parent['status'] != 'accepted':
                raise HTTPException(400, ACCEPT_SERVICE_FIRST)
            service_id = parent['result_id']
            gone_name = parent['payload']['name']
        elif row['service_id'] is not None:
            service_id = row['service_id']
            gone_name = payload.get('about_service')
        elif payload.get('about_service'):
            raise HTTPException(400, SERVICE_GONE.format(name=payload['about_service']))
        else:
            service_id = None
        if service_id is not None and conn.execute('SELECT 1 FROM ka.services WHERE id=%s', (service_id,)).fetchone() is None:
            raise HTTPException(400, SERVICE_GONE.format(name=payload.get('about_service') or gone_name))
        column, entry = ('service_id', service_id) if service_id else ('server_id', server['id'])
        if row['document_id'] is None:
            clash = conn.execute(f'''SELECT d.id,d.revision FROM ka.inventory_documents a JOIN ka.document_details d ON d.id=a.document_id
                WHERE a.{column}=%s AND lower(d.title)=lower(%s) ORDER BY d.id LIMIT 1''', (entry, payload['title'])).fetchone()
            if clash is not None:
                convert(conn, row['id'], DOCUMENT_ADDED.format(title=payload['title']), document_id=clash['id'],
                        payload=Jsonb({**payload, 'matched_document_revision': clash['revision']}))
            name = conn.execute('SELECT name FROM ka.services WHERE id=%s', (service_id,)).fetchone()['name'] if service_id else server['name']
            folder = free_folder(conn, project, document_slug(server['name']) or 'server')
            slug = free_slug(conn, project, folder, document_slug(f"{name} {payload['title']}"))
            saved = save_document(conn, project, actor, DocumentInput(
                slug=slug, title=payload['title'], description=payload['description'], markdown=payload['markdown'],
                **({'service_ids': [service_id]} if service_id else {'server_ids': [server['id']]})),
                None, folder=folder, origin='generated')
            conn.execute(f'''INSERT INTO ka.inventory_documents(project_id,{column},document_id) VALUES (%s,%s,%s)
                ON CONFLICT DO NOTHING''', (project, entry, saved['id']))
        else:
            current = conn.execute('''SELECT slug,description,purpose,audience,tags,code_project_ids,service_ids,server_ids,
                deployment_ids,environment_ids,revision FROM ka.document_details WHERE id=%s''', (row['document_id'],)).fetchone()
            if current['revision'] != payload.get('matched_document_revision'):
                convert(conn, row['id'], DOCUMENT_CHANGED, payload=Jsonb({**payload, 'matched_document_revision': current['revision']}))
            saved = save_document(conn, project, actor, DocumentInput(
                slug=current['slug'], title=payload['title'], description=payload['description'] or current['description'],
                purpose=current['purpose'], audience=current['audience'], tags=current['tags'], markdown=payload['markdown'],
                code_project_ids=current['code_project_ids'], service_ids=current['service_ids'],
                server_ids=current['server_ids'], deployment_ids=current['deployment_ids'],
                environment_ids=current['environment_ids'], revision=current['revision']),
                row['document_id'], origin='generated')
        try:
            export_document(**export_args(export_input(conn, saved['id'])))
            export_error = None
        except (OSError, ValueError):
            export_error = EXPORT_FAILED
        return saved['id'], export_error

    def detail(conn, row: dict) -> dict:
        """A proposal as the review shows it: for a service update its current values, what would change and the
        deployment it updates; for a document the service it is about and what it would replace."""
        item = dict(row)
        payload = row['payload']
        if row['kind'] == 'service' and row['service_id'] is not None:
            current = conn.execute(CURRENT_SERVICE, (row['service_id'],)).fetchone()
            item['current'] = current
            item['changes'] = changes(payload, current) if current else []
            item['current_deployment'] = matching_deployment(conn, row['service_id'], row['server_id'], payload['deployment'])
        if row['kind'] == 'document':
            if row['service_id'] is not None:
                item['service_name'] = conn.execute('SELECT name FROM ka.services WHERE id=%s', (row['service_id'],)).fetchone()['name']
            if row['service_proposal_id'] is not None:
                parent = conn.execute('SELECT status,payload FROM ka.discovery_proposals WHERE id=%s',
                                      (row['service_proposal_id'],)).fetchone()
                item['service_name'] = parent['payload']['name']
                item['service_proposal_status'] = parent['status']
            if row['document_id'] is not None:
                item['current'] = conn.execute('SELECT id,title,version FROM ka.document_details WHERE id=%s',
                                               (row['document_id'],)).fetchone()
        return item

    @router.get('/discovery/{session_id}/proposals')
    def proposals(session_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            if conn.execute("SELECT 1 FROM ka.agent_sessions WHERE id=%s AND project_id=%s AND purpose='discovery'",
                            (session_id, project)).fetchone() is None:
                raise HTTPException(404, NO_RUN)
            rows = conn.execute('SELECT * FROM ka.discovery_proposals WHERE session_id=%s ORDER BY number', (session_id,)).fetchall()
            return jsonable_encoder({'proposals': [detail(conn, row) for row in rows]})

    @router.post('/discovery/proposals/{proposal_id}/accept')
    def accept(proposal_id: UUID, value: Acceptance, request: Request) -> dict:
        db = database()
        kind = None
        try:
            with db.connect() as conn:
                project = db.project(conn, app_name)
                conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
                actor = db.actor(conn, *request_actor(request))
                row = pending(conn, project, proposal_id, value.revision)
                kind = row['kind']
                export_error = None
                if row['kind'] == 'service':
                    result = accept_service(conn, project, actor, row, value.fields)
                else:
                    result, export_error = accept_document(conn, project, actor, row)
                updated = conn.execute('''UPDATE ka.discovery_proposals SET status='accepted',decided_by=%s,decided_at=now(),
                    result_id=%s,revision=revision+1 WHERE id=%s RETURNING *''', (actor, result, proposal_id)).fetchone()
                audit(conn, project, actor, 'discovery.proposal.accepted', 'discovery_proposal', proposal_id,
                      {'session_id': row['session_id'], 'number': row['number'], 'kind': row['kind'], 'result_id': result,
                       'fields': value.fields})
                return jsonable_encoder({'proposal': detail(conn, updated), 'result_id': result, 'export_error': export_error})
        except ValidationError as exc:
            raise HTTPException(400, first_error(exc)) from None
        except psycopg.errors.UniqueViolation:
            if kind == 'document':
                raise HTTPException(409, 'Another document was saved with this name at the same time. Try again.') from None
            raise HTTPException(409, 'A service with this name already exists in this application') from None

    @router.post('/discovery/proposals/{proposal_id}/reject')
    def reject(proposal_id: UUID, value: Decision, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
            actor = db.actor(conn, *request_actor(request))
            row = pending(conn, project, proposal_id, value.revision)
            updated = conn.execute('''UPDATE ka.discovery_proposals SET status='rejected',decided_by=%s,decided_at=now(),
                revision=revision+1 WHERE id=%s RETURNING *''', (actor, proposal_id)).fetchone()
            cascaded = conn.execute('''UPDATE ka.discovery_proposals SET status='rejected',decided_by=%s,decided_at=now(),
                revision=revision+1 WHERE service_proposal_id=%s AND status='pending' RETURNING id''', (actor, proposal_id)).fetchall()
            audit(conn, project, actor, 'discovery.proposal.rejected', 'discovery_proposal', proposal_id,
                  {'session_id': row['session_id'], 'number': row['number'], 'kind': row['kind'],
                   'rejected_documents': len(cascaded)})
            return jsonable_encoder({'proposal': detail(conn, updated), 'rejected_documents': len(cascaded)})

    return router
