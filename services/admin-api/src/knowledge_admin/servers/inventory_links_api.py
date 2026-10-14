"""Inventory → connections between entries (services, servers, environments, code projects) in ka.inventory_links, and
documents attached to entries in ka.inventory_documents. The System map draws and edits both; the Services page edits a
service's depends_on and built_from links through its own form too, so writes to those bump the services' revision."""

from functools import partial
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.access import request_actor
from knowledge_admin.db import audit as write_audit, storage, workspace
from knowledge_admin.events.templates import has_nul
from knowledge_admin.repositories import RepositoryStore

EntryKind = Literal['service', 'server', 'environment', 'code_project']
LinkKind = Literal['depends_on', 'built_from', 'connects_to']
ENTRY_TABLES = {
    'service': 'services',
    'server': 'servers',
    'environment': 'environments',
    'code_project': 'repositories',
}
ENTRY_LABELS = {'service': 'service', 'server': 'server', 'environment': 'environment', 'code_project': 'code project'}
ALLOWED = {
    'depends_on': ({'service'}, {'service'}),
    'built_from': ({'service'}, {'code_project'}),
    'connects_to': (set(ENTRY_TABLES), set(ENTRY_TABLES)),
}
WRONG_PAIR = {
    'depends_on': 'A service can only depend on another service.',
    'built_from': 'Only a service can be built from a code project.',
}
SERVICE_FORM_KINDS = ('depends_on', 'built_from')
SELF = 'An entry cannot connect to itself.'
DUPLICATE = 'This connection already exists.'
STALE = 'This connection was changed by someone else. Reload and try again.'
NO_LINK = 'Connection not found.'
MAX_ATTRIBUTES, MAX_KEY, MAX_VALUE = 20, 64, 500
LINK = """SELECT l.id,l.kind,l.description,l.attributes,l.revision,l.created_at,l.updated_at,
    jsonb_build_object('kind',l.from_kind,'id',l.from_id,'name',f.name) AS "from",
    jsonb_build_object('kind',l.to_kind,'id',l.to_id,'name',t.name) AS "to"
    FROM ka.inventory_links l JOIN ka.inventory_entries f ON f.id=l.from_id JOIN ka.inventory_entries t ON t.id=l.to_id"""


class Entry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: EntryKind
    id: UUID


NO_DOCUMENT = 'Document not found.'
NO_ATTACHMENT = 'Attachment not found.'
ATTACHED = 'This document is already attached.'
ATTACHMENT = """SELECT a.id,a.entry_kind,a.entry_id,a.document_id,d.slug,d.title,a.created_at
    FROM ka.inventory_documents a JOIN ka.document_details d ON d.id=a.document_id"""


class AttachmentDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    entry: Entry
    document_id: UUID


class LinkFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str = Field(default='', max_length=4000)
    attributes: dict[str, str] = Field(default_factory=dict)


class LinkDraft(LinkFields):
    start: Entry = Field(alias='from')
    end: Entry = Field(alias='to')
    kind: LinkKind


class LinkChange(LinkFields):
    revision: int = Field(ge=1, strict=True)


def check_fields(value: LinkFields) -> None:
    if has_nul(value.description):
        raise HTTPException(400, 'Remove the NUL character from Description.')
    if has_nul(value.attributes):
        raise HTTPException(400, 'Remove the NUL character from the attributes.')
    if len(value.attributes) > MAX_ATTRIBUTES:
        raise HTTPException(422, f'Use at most {MAX_ATTRIBUTES} attributes.')
    for key, text in value.attributes.items():
        if not key.strip() or len(key) > MAX_KEY:
            raise HTTPException(422, f'Attribute names are 1–{MAX_KEY} characters.')
        if len(text) > MAX_VALUE:
            raise HTTPException(422, f'Attribute values are at most {MAX_VALUE} characters.')


def lock_inventory(conn, project: UUID) -> None:
    """Services saves take this lock before rewriting a service's links; every link write takes it first too."""
    conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))


def find_entry(conn, project: UUID, entry: Entry) -> None:
    archived = ' AND archived_at IS NULL' if entry.kind == 'code_project' else ''
    found = conn.execute(
        f'SELECT 1 FROM ka.{ENTRY_TABLES[entry.kind]} WHERE id=%s AND project_id=%s{archived} FOR SHARE',
        (entry.id, project),
    ).fetchone()
    if found is None:
        raise HTTPException(404, f'That {ENTRY_LABELS[entry.kind]} was not found.')


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    database = partial(storage, store, 'Inventory storage', message='Inventory storage is not configured')

    def audit(
        conn, project: UUID, request: Request, action: str, resource_type: str, resource_id: UUID, details: dict
    ) -> None:
        actor = database().actor(conn, *request_actor(request))
        write_audit(conn, project, actor, action, resource_type, resource_id, details)

    def link_row(conn, project: UUID, link_id: UUID) -> dict:
        row = conn.execute(LINK + ' WHERE l.id=%s AND l.project_id=%s', (link_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NO_LINK)
        return row

    def locked(conn, project: UUID, link_id: UUID) -> dict:
        row = conn.execute(
            """SELECT kind,revision,from_kind,from_id,to_kind,to_id FROM ka.inventory_links
            WHERE id=%s AND project_id=%s FOR UPDATE""",
            (link_id, project),
        ).fetchone()
        if row is None:
            raise HTTPException(404, NO_LINK)
        return row

    def touch_services(conn, kind: str, ends: list[tuple[str, UUID]]) -> None:
        """An open Services form holds the service's revision; changing a link that form shows makes it stale."""
        if kind in SERVICE_FORM_KINDS:
            conn.execute(
                'UPDATE ka.services SET revision=revision+1 WHERE id=ANY(%s)',
                ([entry_id for entry_kind, entry_id in ends if entry_kind == 'service'],),
            )

    @router.get('/inventory/links')
    def links(entry_kind: EntryKind | None = None, entry_id: UUID | None = None) -> dict:
        if (entry_kind is None) != (entry_id is None):
            raise HTTPException(400, 'Give both entry_kind and entry_id, or neither.')
        db = database()
        with workspace(db, app) as (conn, project):
            rows = conn.execute(
                LINK
                + """ WHERE l.project_id=%s AND (%s::uuid IS NULL OR l.from_id=%s OR l.to_id=%s)
                ORDER BY l.kind,lower(f.name),lower(t.name),l.id""",
                (project, entry_id, entry_id, entry_id),
            ).fetchall()
        return {'links': rows}

    @router.post('/inventory/links', status_code=201)
    def create(value: LinkDraft, request: Request) -> dict:
        check_fields(value)
        starts, ends = ALLOWED[value.kind]
        if value.start.kind not in starts or value.end.kind not in ends:
            raise HTTPException(422, WRONG_PAIR[value.kind])
        if value.start.id == value.end.id:
            raise HTTPException(422, SELF)
        db = database()
        with workspace(db, app) as (conn, project):
            lock_inventory(conn, project)
            find_entry(conn, project, value.start)
            find_entry(conn, project, value.end)
            actor = db.actor(conn, *request_actor(request))
            try:
                with conn.transaction():
                    link_id = conn.execute(
                        f"""INSERT INTO ka.inventory_links(project_id,from_{value.start.kind}_id,
                        to_{value.end.kind}_id,kind,description,attributes,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (
                            project,
                            value.start.id,
                            value.end.id,
                            value.kind,
                            value.description,
                            Jsonb(value.attributes),
                            actor,
                        ),
                    ).fetchone()['id']
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, DUPLICATE) from None
            touch_services(conn, value.kind, [(value.start.kind, value.start.id), (value.end.kind, value.end.id)])
            audit(
                conn,
                project,
                request,
                'inventory.link.created',
                'inventory_link',
                link_id,
                {
                    'kind': value.kind,
                    'from': f'{value.start.kind}:{value.start.id}',
                    'to': f'{value.end.kind}:{value.end.id}',
                },
            )
            return {'link': link_row(conn, project, link_id)}

    @router.put('/inventory/links/{link_id}')
    def save(link_id: UUID, value: LinkChange, request: Request) -> dict:
        check_fields(value)
        db = database()
        with workspace(db, app) as (conn, project):
            lock_inventory(conn, project)
            current = locked(conn, project, link_id)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE)
            conn.execute(
                'UPDATE ka.inventory_links SET description=%s,attributes=%s,revision=revision+1 WHERE id=%s',
                (value.description, Jsonb(value.attributes), link_id),
            )
            touch_services(
                conn,
                current['kind'],
                [(current['from_kind'], current['from_id']), (current['to_kind'], current['to_id'])],
            )
            audit(
                conn, project, request, 'inventory.link.updated', 'inventory_link', link_id, {'kind': current['kind']}
            )
            return {'link': link_row(conn, project, link_id)}

    @router.delete('/inventory/links/{link_id}', status_code=204)
    def delete(link_id: UUID, request: Request) -> Response:
        db = database()
        with workspace(db, app) as (conn, project):
            lock_inventory(conn, project)
            current = locked(conn, project, link_id)
            conn.execute('DELETE FROM ka.inventory_links WHERE id=%s', (link_id,))
            touch_services(
                conn,
                current['kind'],
                [(current['from_kind'], current['from_id']), (current['to_kind'], current['to_id'])],
            )
            audit(
                conn,
                project,
                request,
                'inventory.link.deleted',
                'inventory_link',
                link_id,
                {
                    'kind': current['kind'],
                    'from': f'{current["from_kind"]}:{current["from_id"]}',
                    'to': f'{current["to_kind"]}:{current["to_id"]}',
                },
            )
        return Response(status_code=204)

    @router.get('/inventory/documents')
    def attachments(entry_kind: EntryKind, entry_id: UUID) -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            rows = conn.execute(
                ATTACHMENT + ' WHERE a.project_id=%s AND a.entry_id=%s ORDER BY lower(d.title),a.id',
                (project, entry_id),
            ).fetchall()
        return {'documents': rows}

    @router.post('/inventory/documents', status_code=201)
    def attach(value: AttachmentDraft, request: Request) -> dict:
        db = database()
        with workspace(db, app) as (conn, project):
            lock_inventory(conn, project)
            find_entry(conn, project, value.entry)
            if (
                conn.execute(
                    'SELECT 1 FROM ka.documents WHERE id=%s AND project_id=%s AND NOT is_draft FOR SHARE',
                    (value.document_id, project),
                ).fetchone()
                is None
            ):
                raise HTTPException(404, NO_DOCUMENT)
            try:
                with conn.transaction():
                    attachment = conn.execute(
                        f"""INSERT INTO ka.inventory_documents(project_id,{value.entry.kind}_id,document_id)
                        VALUES (%s,%s,%s) RETURNING id""",
                        (project, value.entry.id, value.document_id),
                    ).fetchone()['id']
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, ATTACHED) from None
            audit(
                conn,
                project,
                request,
                'inventory.document.attached',
                value.entry.kind,
                value.entry.id,
                {'document_id': str(value.document_id)},
            )
            return {'attachment': conn.execute(ATTACHMENT + ' WHERE a.id=%s', (attachment,)).fetchone()}

    @router.delete('/inventory/documents/{attachment_id}', status_code=204)
    def detach(attachment_id: UUID, request: Request) -> Response:
        db = database()
        with workspace(db, app) as (conn, project):
            lock_inventory(conn, project)
            removed = conn.execute(
                """DELETE FROM ka.inventory_documents WHERE id=%s AND project_id=%s
                RETURNING entry_kind,entry_id,document_id""",
                (attachment_id, project),
            ).fetchone()
            if removed is None:
                raise HTTPException(404, NO_ATTACHMENT)
            audit(
                conn,
                project,
                request,
                'inventory.document.detached',
                removed['entry_kind'],
                removed['entry_id'],
                {'document_id': str(removed['document_id'])},
            )
        return Response(status_code=204)

    return router
