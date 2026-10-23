"""Managed Markdown documents: read, download and delete live documents, read a brand-new draft as the document it will
become and save a document's resource links. People never write live
content here: every save is a draft (documents/drafts.py), and Publish / Promote make it live. The lifecycle itself is
in documents/store.py."""

from functools import partial
import io
import logging
import re
import zipfile
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from knowledge_admin.identity import request_actor
from knowledge_admin.db import audit, storage, workspace
from knowledge_admin.documents import store as documents
from knowledge_admin.documents.store import LINK_COLUMNS, Provenance
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)


def _text(value: str, *, blank: bool = False, strip: bool = True) -> str:
    if '\x00' in value or (not blank and not value.strip()):
        raise ValueError('Enter nonempty text without null characters')
    return value.strip() if strip else value


def _tags(values: list[str]) -> list[str]:
    tags = list(dict.fromkeys(v.strip() for v in values))
    if any(not v or len(v) > 80 or '\x00' in v for v in tags):
        raise ValueError('Tags must contain 1–80 characters without null characters')
    return tags


# A document's fields with their limits and text rules, shared by the drafts' models: no null
# characters; nonblank except the description; stripped except the Markdown.
SLUG_PATTERN = r'^[a-z0-9]+(-[a-z0-9]+)*$'
Slug = Annotated[str, Field(min_length=1, max_length=120, pattern=SLUG_PATTERN), AfterValidator(_text)]
Title = Annotated[str, Field(min_length=1, max_length=240), AfterValidator(_text)]
Description = Annotated[str, Field(max_length=4000), AfterValidator(lambda value: _text(value, blank=True))]
Purpose = Annotated[str, Field(min_length=1, max_length=80), AfterValidator(_text)]
Audience = Literal['people', 'agents', 'both']
Tags = Annotated[list[str], Field(max_length=30), AfterValidator(_tags)]
Markdown = Annotated[
    str, Field(min_length=1, max_length=1000000), AfterValidator(lambda value: _text(value, strip=False))
]
ResourceIds = Annotated[list[UUID], Field(max_length=100), AfterValidator(lambda values: list(dict.fromkeys(values)))]
# The id-list field of each kind of resource a document links (LinksInput, drafts' resource_ids, document_details).
DOCUMENT_RESOURCE_FIELDS = tuple(LINK_COLUMNS)


class LinksInput(BaseModel):
    """A document's resource links (every kind; a missing kind means none) at the revision the page loaded."""

    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    code_project_ids: ResourceIds = Field(default_factory=list)
    service_ids: ResourceIds = Field(default_factory=list)
    server_ids: ResourceIds = Field(default_factory=list)
    deployment_ids: ResourceIds = Field(default_factory=list)
    environment_ids: ResourceIds = Field(default_factory=list)


def document_files(markdown: str) -> dict[str, str]:
    files = {'README.md': markdown}
    for number, source in enumerate(re.findall(r'^```d2\s*\n(.*?)^```\s*$', markdown, re.M | re.S), 1):
        files[f'architecture-{number}.d2'] = source
    return files


def document_slug(name: str) -> str:
    """A slug from a document name: lowercase ASCII letters and digits joined by '-', at most 120 characters
    ('' when the name has none)."""
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:120].rstrip('-')


def export_conflict(conn, project: UUID, slug: str, folder: str | None, document_id: UUID | None = None) -> str | None:
    """Why this document's slug and folder would clash with another document or documentation folder, or None.

    A folderless document is <slug>/, a folder document <folder>/<slug>/. At every level a document's
    path is never also a documentation folder or a parent of one, in either direction. Folders and slugs are
    lowercase-only, so these exact comparisons also hold on case-insensitive filesystems. Live documents and brand-new
    drafts (rows with a slug) reserve their names."""
    target = f'{folder}/{slug}' if folder else slug
    # starts_with, not LIKE: '_' is a valid folder character and must match literally.
    if conn.execute(
        """SELECT 1 FROM ka.documents WHERE project_id=%s AND slug IS NOT NULL AND id IS DISTINCT FROM %s
        AND (folder=%s OR starts_with(folder, %s)) LIMIT 1""",
        (project, document_id, target, target + '/'),
    ).fetchone():
        if folder is None:
            return f'The slug {slug} is used by a documentation folder. Choose another slug.'
        return f'The slug {slug} is used by the documentation folder {target}. Choose another slug.'
    if folder is None:
        return None
    parts = folder.split('/')
    for depth in range(1, len(parts) + 1):
        parent, name = '/'.join(parts[: depth - 1]) or None, parts[depth - 1]
        if conn.execute(
            """SELECT 1 FROM ka.documents WHERE project_id=%s AND folder IS NOT DISTINCT FROM %s AND slug=%s
            AND id IS DISTINCT FROM %s LIMIT 1""",
            (project, parent, name, document_id),
        ).fetchone():
            if parent is None:
                return f'The folder {name} is used by the document {name}. Choose another folder.'
            return (
                f'The folder {parent}/{name} is used by the document {name} in the folder {parent}. '
                'Choose another folder.'
            )
    return None


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/documentation')

    database = partial(storage, store, 'Document storage', message='Document storage is not configured')

    @router.get('/resources')
    def resources() -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            return {
                'environments': conn.execute(
                    'SELECT id,name FROM ka.environments WHERE project_id=%s ORDER BY name', (project,)
                ).fetchall(),
                'code_projects': conn.execute(
                    'SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL ORDER BY lower(name),id',
                    (project,),
                ).fetchall(),
                'services': conn.execute(
                    'SELECT id,name FROM ka.services WHERE project_id=%s ORDER BY lower(name),id', (project,)
                ).fetchall(),
                'servers': conn.execute(
                    'SELECT id,name,hostname FROM ka.servers WHERE project_id=%s ORDER BY lower(name),id', (project,)
                ).fetchall(),
                'deployments': conn.execute(
                    """SELECT d.id,d.service_id,d.server_id,e.name AS environment,d.runtime_name,
                    s.name || ' / ' || h.name || ' / ' || e.name ||
                    CASE WHEN d.runtime_name='' THEN '' ELSE ' / ' || d.runtime_name END AS name
                    FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id
                    JOIN ka.servers h ON h.id=d.server_id JOIN ka.environments e ON e.id=d.environment_id
                    WHERE d.project_id=%s ORDER BY name,d.id""",
                    (project,),
                ).fetchall(),
            }

    @router.get('/documents')
    def list_documents(
        q: str = Query(default='', max_length=200),
        code_project_id: UUID | None = None,
        service_id: UUID | None = None,
        server_id: UUID | None = None,
        deployment_id: UUID | None = None,
        environment_id: UUID | None = None,
        has_drafts: bool = False,
        tag: str | None = Query(default=None, max_length=80),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        """Live documents, most recently updated first, 50 a page; `tag`: those with that tag (one tag, exact,
        case-insensitive)."""
        tag = (tag or '').strip() or None  # blank: no tag filter
        db = database()
        with workspace(db, app_name) as (conn, project):
            where = """project_id=%s AND (strpos(lower(title || ' ' || description || ' ' || slug),lower(%s))>0)
                AND (%s::uuid IS NULL OR %s=ANY(code_project_ids))
                AND (%s::uuid IS NULL OR %s=ANY(service_ids))
                AND (%s::uuid IS NULL OR %s=ANY(server_ids))
                AND (%s::uuid IS NULL OR %s=ANY(deployment_ids))
                AND (%s::uuid IS NULL OR %s=ANY(environment_ids))
                AND (NOT %s OR draft_count > 0)
                AND (%s::text IS NULL OR EXISTS (SELECT 1 FROM unnest(tags) t WHERE lower(t)=lower(%s)))"""
            params = (
                project,
                q.strip(),
                code_project_id,
                code_project_id,
                service_id,
                service_id,
                server_id,
                server_id,
                deployment_id,
                deployment_id,
                environment_id,
                environment_id,
                has_drafts,
                tag,
                tag,
            )
            rows = conn.execute(
                f"""SELECT d.* FROM ka.document_details d WHERE {where}
                ORDER BY updated_at DESC,id LIMIT 50 OFFSET %s""",
                (*params, offset),
            ).fetchall()
            total = conn.execute(f'SELECT count(*) AS n FROM ka.document_details d WHERE {where}', params).fetchone()[
                'n'
            ]
            return {'documents': rows, 'total': total}

    def live_document(conn, project: UUID, document_id: UUID) -> None:
        """404 unless `document_id` is a live document of the request's workspace (store reads take no project)."""
        if not conn.execute(
            'SELECT 1 FROM ka.live_documents WHERE id=%s AND project_id=%s', (document_id, project)
        ).fetchone():
            raise HTTPException(404, documents.DOCUMENT_NOT_FOUND)

    def linkable(conn, project: UUID, document_id: UUID) -> None:
        """404 unless `document_id` is a live document or a brand-new draft of the request's workspace (the rows that
        own their links; a draft of a document uses its document's)."""
        if not conn.execute(
            'SELECT 1 FROM ka.documents WHERE id=%s AND project_id=%s AND draft_of IS NULL', (document_id, project)
        ).fetchone():
            raise HTTPException(404, documents.DOCUMENT_NOT_FOUND)

    def brand_new_draft(conn, project: UUID, document_id: UUID) -> dict | None:
        """A brand-new draft read as the document it becomes when published (same id): no versions, no drafts."""
        row = conn.execute(
            """SELECT x.id,x.slug,x.folder,x.title,x.description,x.purpose,x.audience,x.tags,x.markdown,x.revision,
            true AS is_draft,false AS published,
            CASE WHEN x.session_id IS NULL THEN 'manual' ELSE 'agent' END AS origin,x.session_id,
            x.code_project_ids,x.service_ids,x.server_ids,x.deployment_ids,x.environment_ids,x.created_at,x.updated_at
            FROM ka.document_drafts_view x WHERE x.id=%s AND x.project_id=%s AND x.document_id IS NULL""",
            (document_id, project),
        ).fetchone()
        if row is not None:
            row.update(versions=[], drafts=[], resource_links=documents.resource_links(conn, document_id))
        return row

    @router.get('/documents/{document_id}')
    def get_document(document_id: UUID) -> dict:
        """A live document (is_draft false, published true) or a brand-new draft (is_draft true, published false); a
        draft of a live document is 404 here (it opens as a draft)."""
        db = database()
        with workspace(db, app_name) as (conn, project):
            row = conn.execute(
                """SELECT d.*,l.markdown,l.source_snapshot,false AS is_draft,true AS published
                FROM ka.document_details d JOIN ka.live_documents l ON l.id=d.id WHERE d.id=%s AND d.project_id=%s""",
                (document_id, project),
            ).fetchone()
            if row is None:
                row = brand_new_draft(conn, project, document_id)
                if row is None:
                    raise HTTPException(404, documents.DOCUMENT_NOT_FOUND)
                return row
            row['versions'] = documents.versions(conn, document_id)
            row['drafts'] = conn.execute(
                """SELECT x.id,x.title,CASE WHEN x.session_id IS NULL THEN 'manual' ELSE 'agent' END AS origin,
                x.session_id,p.email AS author,x.edited,x.base_version,x.stale,true AS published,x.created_at,
                x.updated_at FROM ka.document_drafts_view x LEFT JOIN ka.principals p ON p.id=x.created_by
                WHERE x.document_id=%s ORDER BY x.created_at DESC,x.id""",
                (document_id,),
            ).fetchall()
            row['resource_links'] = documents.resource_links(conn, document_id)
            return row

    def live_only(document: dict) -> dict:
        if document['is_draft']:  # a brand-new draft has no export and no bundle until it is published
            raise HTTPException(404, documents.DOCUMENT_NOT_FOUND)
        return document

    @router.get('/documents/{document_id}/resource-history')
    def resource_history(document_id: UUID) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            linkable(conn, project, document_id)
            return {'history': documents.resource_history(conn, document_id)}

    @router.get('/documents/{document_id}/bundle')
    def bundle(document_id: UUID) -> Response:
        document = live_only(get_document(document_id))
        markdown = document['markdown']
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
            folder = document['slug']
            for name, content in document_files(markdown).items():
                archive.writestr(f'{folder}/{name}', content)
        return Response(
            data.getvalue(),
            media_type='application/zip',
            headers={'Content-Disposition': f'attachment; filename="{document["slug"]}.zip"'},
        )

    @router.get('/documents/{document_id}/versions/{version}')
    def get_version(document_id: UUID, version: int) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            live_document(conn, project, document_id)
            row = documents.version(conn, document_id, version)
            if row is None:
                raise HTTPException(404, 'Document version not found')
            return row

    @router.put('/documents/{document_id}/links')
    def save_links(document_id: UUID, value: LinksInput, request: Request) -> dict:
        """Links of a live document or a brand-new draft, applied directly (they never go through drafts): new ones
        linked, unticked linked ones removed with history; unticking a built-for link is a 409 and nothing is written."""
        db = database()
        with workspace(db, app_name) as (conn, project):
            actor = db.actor(conn, *request_actor(request))
            row = conn.execute(
                """SELECT revision,is_draft FROM ka.documents WHERE id=%s AND project_id=%s AND draft_of IS NULL
                FOR UPDATE""",
                (document_id, project),
            ).fetchone()
            if row is None:
                raise HTTPException(404, documents.DOCUMENT_NOT_FOUND)
            if row['revision'] != value.revision:
                raise HTTPException(409, documents.DRAFT_CHANGED if row['is_draft'] else documents.DOCUMENT_CHANGED)
            wanted = {field: getattr(value, field) for field in LINK_COLUMNS}
            documents.build_snapshot(conn, project, wanted, None, None)  # the same checks a snapshot makes: 400
            saved = documents.save_links(conn, project, document_id, wanted, Provenance('person', actor))
            revision = conn.execute(
                'UPDATE ka.documents SET updated_by=%s,revision=revision+1 WHERE id=%s RETURNING revision',
                (actor, document_id),
            ).fetchone()['revision']
            audit(
                conn,
                project,
                actor,
                'document.links.updated',
                'document',
                document_id,
                {'links_added': saved['added'], 'links_removed': saved['removed']},
            )
            return {
                'id': document_id,
                'revision': revision,
                'resource_links': documents.resource_links(conn, document_id),
            }

    @router.delete('/documents/{document_id}')
    def delete_document(
        document_id: UUID, request: Request, revision: int = Query(ge=1), confirmed: bool = False
    ) -> dict:
        subject, email = request_actor(request)
        if not confirmed:
            raise HTTPException(400, 'Confirm document deletion before continuing')
        db = database()
        with workspace(db, app_name) as (conn, project):
            # The project first, as promote and create do: promote locks a draft, then the document; this delete locks
            # the document, then (cascade) its drafts. Without it the two could deadlock.
            conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
            row = conn.execute(
                'SELECT revision FROM ka.documents WHERE id=%s AND project_id=%s AND NOT is_draft FOR UPDATE',
                (document_id, project),
            ).fetchone()
            if row is None:
                raise HTTPException(404, 'Document not found')
            if row['revision'] != revision:
                raise HTTPException(409, 'This document changed. Cancel, reload, and try again.')
            actor = db.actor(conn, subject, email)
            conn.execute('DELETE FROM ka.documents WHERE id=%s', (document_id,))
            audit(conn, project, actor, 'document.deleted', 'document', document_id, {'revision': revision})
        return {'id': document_id}

    return router
