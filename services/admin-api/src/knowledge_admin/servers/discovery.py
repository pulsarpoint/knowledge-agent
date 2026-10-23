"""Server discovery: the server page's data and reviewing the proposals of discovery runs. A discovery run is an ordinary
run started from a server through /api/sessions (sessions/start.py: started_from.server_id, purpose resource); its
proposal tools are in capabilities/discovery.py. What it does is its prompt's text ("Server discovery")."""

from functools import partial
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_admin.identity import request_actor
from knowledge_admin.capabilities.discovery import RESOURCE_COLUMNS, RESOURCE_TABLES
from knowledge_admin.db import audit, storage, workspace
from knowledge_admin.documents import store as lifecycle
from knowledge_admin.documents.api import document_slug, export_conflict
from knowledge_admin.documents.drafts import DraftContent
from knowledge_admin.documents.store import Provenance
from knowledge_admin.inventory import store as inventory
from knowledge_admin.inventory.rules import Invalid
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


NO_RUN = 'Discovery run not found.'
RESOURCE_GONE = 'The server, service or environment this proposal was made for no longer exists. Reject this proposal.'
NO_PROPOSAL = 'Proposal not found.'
DECIDED = 'This proposal was already accepted or rejected.'
STALE = 'This proposal changed since you opened it. Review it again.'
DOCUMENT_CHANGED = 'This document changed since the proposal was made. Review it again.'
DOCUMENT_ADDED = 'A document titled {title} was added since this proposal was made. It is now shown as a new version. Review it again.'
SERVICE_GONE = 'The service {name} no longer exists. Reject this proposal.'


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


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
        if (
            conn.execute(
                'SELECT 1 FROM ka.documents WHERE project_id=%s AND folder=%s AND slug=%s', (project, folder, slug)
            ).fetchone()
            is None
            and export_conflict(conn, project, slug, folder) is None
        ):
            return slug
    raise HTTPException(409, 'Too many documents with this name. Rename one and try again.')


# The document folder roots of each resource type, in order: a root that is a document's export is skipped. A
# top-level services folder is reserved (documents_folder_check).
FOLDER_ROOTS = {
    'server': ('servers', 'server-documents'),
    'service': ('service-documents',),
    'environment': ('environments', 'environment-documents'),
}


def free_folder(conn, project, kind: str, entry_slug: str) -> str:
    """<root>/<entry> (servers/web-1, service-documents/nginx, environments/production), or <root>/<entry>-2, -3, ...
    when the folder itself cannot hold exports (a document is named like it, or a folder level is a document's
    export): the conflict does not depend on the slug. When a root itself is a document's export, the next root of
    FOLDER_ROOTS is used instead."""
    for root in FOLDER_ROOTS[kind]:
        for number in range(1, 100):
            folder = f'{root}/{entry_slug}' if number == 1 else f'{root}/{entry_slug}-{number}'
            if export_conflict(conn, project, 'probe-slug-0', folder) is None:
                return folder
    raise HTTPException(
        409, f"No folder is free for this {kind}'s documents. Rename a document or folder and try again."
    )


def run_resource(conn, row: dict) -> dict | None:
    """The resource a proposal's run started from ({type, id, name}, the name as it is now); None when it no longer
    exists."""
    session = conn.execute(
        "SELECT agent_config->'started_from' AS started_from FROM ka.agent_sessions WHERE id=%s", (row['session_id'],)
    ).fetchone()
    started = session['started_from'] if session else None
    resource = (
        conn.execute(f'SELECT id,name FROM {RESOURCE_TABLES[started["type"]]} WHERE id=%s', (started['id'],)).fetchone()
        if started and started.get('type') in RESOURCE_TABLES
        else None
    )
    return None if resource is None else {**resource, 'type': started['type']}


def draft_published(conn, row: dict) -> bool:
    """Whether the draft an accepted document proposal made went live: published (a brand-new draft keeps its id as
    the document) or promoted. False while it is open and once it was discarded or replaced by another draft's promote.
    result_id is the draft's id (draft_id is cleared when the draft row is deleted); a proposal accepted before drafts
    (0105) wrote the live document directly, so a result with no draft outcome is live."""
    outcome = lifecycle.draft_outcome(conn, row['result_id'])
    return outcome is None or outcome['status'] == 'promoted'


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
                d.runtime,d.runtime_name,d.port,d.deployed_version FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id
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

    def pending(conn, project, proposal_id: UUID, revision: int) -> dict:
        row = conn.execute(
            'SELECT * FROM ka.discovery_proposals WHERE id=%s AND project_id=%s FOR UPDATE', (proposal_id, project)
        ).fetchone()
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
        conn.execute(
            f'UPDATE ka.discovery_proposals SET {sets},revision=revision+1 WHERE id=%s',
            (*columns.values(), proposal_id),
        )
        conn.commit()
        raise HTTPException(409, message)

    def accept_document(conn, project, actor, row: dict) -> UUID:
        """The draft accepting a document proposal makes: a draft of the live document it updates, or a brand-new
        draft (built for the service it is about, else the run's server, service or environment, in that resource's
        folder), tagged inventory when that resource has no document tagged inventory built for it (live or brand-new
        draft), else an ordinary document beside it. Discovery never changes an inventory document nor tags an
        existing document: a title matching an inventory document is a new document, and a matched document tagged
        inventory since gets a new document, not a draft of it."""
        payload = row['payload']
        prov = Provenance('discovery', actor, row['session_id'])
        if row['service_id'] is not None:
            service_id = row['service_id']
            gone_name = payload.get('about_service')
        elif payload.get('about_service'):
            raise HTTPException(400, SERVICE_GONE.format(name=payload['about_service']))
        else:
            service_id = None
        if (
            service_id is not None
            and conn.execute('SELECT 1 FROM ka.services WHERE id=%s', (service_id,)).fetchone() is None
        ):
            raise HTTPException(400, SERVICE_GONE.format(name=payload.get('about_service') or gone_name))
        # The document the proposal updates, unless it was tagged inventory since (FOR SHARE: a concurrent promote
        # waits for this accept, so the tags read here hold until the draft is made).
        updates = row['document_id']
        matched = (
            None
            if updates is None
            else conn.execute(
                'SELECT tags FROM ka.documents WHERE id=%s AND NOT is_draft FOR SHARE', (updates,)
            ).fetchone()
        )
        if matched is not None and lifecycle.has_inventory_tag(matched['tags']):
            updates = None
        if updates is None:
            # A document about a service needs only the service; the run's resource, when it still exists, picks the
            # folder.
            resource = run_resource(conn, row)
            if resource is None and service_id is None:
                raise HTTPException(400, RESOURCE_GONE)
            column, entry = (
                ('service_id', service_id) if service_id else (RESOURCE_COLUMNS[resource['type']], resource['id'])
            )
            clash = conn.execute(
                f"""SELECT d.id,d.revision FROM ka.document_resources a JOIN ka.document_details d ON d.id=a.document_id
                WHERE a.{column}=%s AND a.removed_at IS NULL AND NOT {lifecycle.INVENTORY_TAGGED} AND lower(d.title)=lower(%s)
                ORDER BY d.id LIMIT 1""",
                (entry, lifecycle.INVENTORY_TAG, payload['title']),
            ).fetchone()
            if clash is not None:
                convert(
                    conn,
                    row['id'],
                    DOCUMENT_ADDED.format(title=payload['title']),
                    document_id=clash['id'],
                    payload=Jsonb({**payload, 'matched_document_revision': clash['revision']}),
                )
            name = (
                conn.execute('SELECT name FROM ka.services WHERE id=%s', (service_id,)).fetchone()['name']
                if service_id
                else resource['name']
            )
            if resource is None:
                resource = {'type': 'service', 'id': service_id, 'name': name}
            # The export lock a promote takes, so the free name below cannot race a Publish of another draft.
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (f'{project}:document-export',))
            kind = resource['type']
            folder = free_folder(conn, project, kind, document_slug(resource['name']) or kind)
            slug = free_slug(conn, project, folder, document_slug(f'{name} {payload["title"]}'))
            built_for = ('service', service_id) if service_id else (kind, resource['id'])
            first = lifecycle.inventory_document(conn, project, *built_for) is None
            content = DraftContent(
                title=payload['title'],
                description=payload['description'],
                tags=[lifecycle.INVENTORY_TAG] if first else [],
                markdown=payload['markdown'],
            )
            return lifecycle.create_draft(
                conn,
                project,
                prov,
                content.model_dump(),
                origin='generated',
                slug=slug,
                folder=folder,
                links={f'{built_for[0]}_ids': [built_for[1]]},
            )
        current = conn.execute(
            'SELECT title,description,purpose,audience,tags,revision FROM ka.document_details WHERE id=%s',
            (updates,),
        ).fetchone()
        if current['revision'] != payload.get('matched_document_revision'):
            convert(
                conn,
                row['id'],
                DOCUMENT_CHANGED,
                payload=Jsonb({**payload, 'matched_document_revision': current['revision']}),
            )
        content = DraftContent(
            title=payload['title'],
            description=payload['description'] or current['description'],
            purpose=current['purpose'],
            audience=current['audience'],
            tags=current['tags'],
            markdown=payload['markdown'],
        )
        return lifecycle.create_draft(
            conn, project, prov, content.model_dump(), origin='generated', document_id=updates
        )

    def detail(conn, row: dict) -> dict:
        """A proposal as the review shows it: the service it is about, what it would replace and, once accepted,
        whether its draft went live (draft_published)."""
        item = dict(row)
        item['draft_published'] = (
            row['kind'] == 'document' and row['status'] == 'accepted' and draft_published(conn, row)
        )
        if row['kind'] == 'document':
            if row['service_id'] is not None:
                item['service_name'] = conn.execute(
                    'SELECT name FROM ka.services WHERE id=%s', (row['service_id'],)
                ).fetchone()['name']
            if row['document_id'] is not None:
                item['current'] = conn.execute(
                    'SELECT id,title,version FROM ka.document_details WHERE id=%s', (row['document_id'],)
                ).fetchone()
        return item

    @router.get('/discovery/proposals')
    def resource_proposals(
        tag: str = Query(min_length=1, max_length=120), status: Literal['pending', 'accepted', 'rejected'] | None = None
    ) -> dict:
        """The proposals of the runs tagged with a resource (servers/<id>, services/<id>, environments/<id>), of one
        status or all, grouped by run, newest run first; a run without such proposals is left out."""
        db = database()
        with workspace(db, app_name) as (conn, project):
            rows = conn.execute(
                """SELECT p.*,s.title AS run_title,s.created_at AS run_started_at,a.email AS run_started_by
                FROM ka.discovery_proposals p JOIN ka.agent_sessions s ON s.id=p.session_id
                LEFT JOIN ka.principals a ON a.id=s.actor_id
                WHERE s.project_id=%s AND s.tags @> ARRAY[%s]::text[] AND (%s::text IS NULL OR p.status=%s)
                ORDER BY s.created_at DESC,s.id,p.number""",
                (project, tag, status, status),
            ).fetchall()
            runs: dict = {}
            for row in rows:
                proposal = dict(row)
                run = {
                    'session_id': row['session_id'],
                    'title': proposal.pop('run_title'),
                    'started_at': proposal.pop('run_started_at'),
                    'started_by': proposal.pop('run_started_by'),
                    'proposals': [],
                }
                runs.setdefault(row['session_id'], run)['proposals'].append(detail(conn, proposal))
            return jsonable_encoder({'runs': list(runs.values())})

    @router.get('/discovery/{session_id}/proposals')
    def proposals(session_id: UUID) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            if (
                conn.execute(
                    "SELECT 1 FROM ka.agent_sessions WHERE id=%s AND project_id=%s AND purpose='resource'",
                    (session_id, project),
                ).fetchone()
                is None
            ):
                raise HTTPException(404, NO_RUN)
            rows = conn.execute(
                'SELECT * FROM ka.discovery_proposals WHERE session_id=%s ORDER BY number', (session_id,)
            ).fetchall()
            return jsonable_encoder({'proposals': [detail(conn, row) for row in rows]})

    @router.post('/discovery/proposals/{proposal_id}/accept')
    def accept(proposal_id: UUID, value: Decision, request: Request) -> dict:
        db = database()
        try:
            with workspace(db, app_name) as (conn, project):
                conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
                actor = db.actor(conn, *request_actor(request))
                row = pending(conn, project, proposal_id, value.revision)
                result = draft = accept_document(conn, project, actor, row)
                updated = conn.execute(
                    """UPDATE ka.discovery_proposals SET status='accepted',decided_by=%s,decided_at=now(),
                    result_id=%s,draft_id=%s,revision=revision+1 WHERE id=%s RETURNING *""",
                    (actor, result, draft, proposal_id),
                ).fetchone()
                audit(
                    conn,
                    project,
                    actor,
                    'discovery.proposal.accepted',
                    'discovery_proposal',
                    proposal_id,
                    {
                        'session_id': row['session_id'],
                        'number': row['number'],
                        'kind': row['kind'],
                        'result_id': result,
                    },
                )
                return jsonable_encoder({'proposal': detail(conn, updated), 'result_id': result})
        except ValidationError as exc:
            raise HTTPException(400, first_error(exc)) from None
        except Invalid as exc:
            raise HTTPException(400, str(exc)) from None
        except (inventory.NotFound, inventory.Conflict) as exc:
            raise HTTPException(409, str(exc)) from None
        except psycopg.errors.UniqueViolation:  # inventory writes raise inventory.Conflict instead
            raise HTTPException(409, 'Another document was saved with this name at the same time. Try again.') from None

    @router.post('/discovery/proposals/{proposal_id}/reject')
    def reject(proposal_id: UUID, value: Decision, request: Request) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
            actor = db.actor(conn, *request_actor(request))
            row = pending(conn, project, proposal_id, value.revision)
            updated = conn.execute(
                """UPDATE ka.discovery_proposals SET status='rejected',decided_by=%s,decided_at=now(),
                revision=revision+1 WHERE id=%s RETURNING *""",
                (actor, proposal_id),
            ).fetchone()
            audit(
                conn,
                project,
                actor,
                'discovery.proposal.rejected',
                'discovery_proposal',
                proposal_id,
                {
                    'session_id': row['session_id'],
                    'number': row['number'],
                    'kind': row['kind'],
                },
            )
            return jsonable_encoder({'proposal': detail(conn, updated)})

    return router
