"""Document drafts: rows of ka.documents (is_draft) that revise a live document (draft_of) or are a brand-new
document, which a person promotes to the live version or discards (both delete the draft). Agents only ever write
drafts. Readers of live documents (document_details, live_documents, exports) never see drafts. The
lifecycle itself is documents/store.py; this module is the drafts API, a run's sources and document links."""

from functools import partial
import json
import logging
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from knowledge_admin.identity import request_actor
from knowledge_admin.db import audit, storage, workspace
from knowledge_admin.documents import store as lifecycle
from knowledge_admin.documents.api import (
    DOCUMENT_RESOURCE_FIELDS,
    Audience,
    Description,
    Markdown,
    Purpose,
    ResourceIds,
    Slug,
    Tags,
    Title,
)
from knowledge_admin.documents.store import CONTENT, DOCUMENT_NOT_FOUND, DRAFT_NOT_FOUND, NAME_IN_USE, Provenance
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

KEEPS_NAME = "A draft of a published document keeps its document's name."
RESOURCE_GONE = 'A selected resource is no longer available. Reload resources and update the selection.'
SAVED_MEANWHILE = 'Another document was saved with this name at the same time. Try again.'
NEW_DEFAULTS = {'description': '', 'purpose': 'explanation', 'audience': 'both', 'tags': []}


class DraftContent(BaseModel):
    """A draft's text, with a document's limits and text rules (documents/api.py)."""

    model_config = ConfigDict(extra='forbid')
    title: Title
    description: Description = ''
    purpose: Purpose = 'explanation'
    audience: Audience = 'both'
    tags: Tags = Field(default_factory=list)
    markdown: Markdown


class DraftUpdate(DraftContent):
    """A draft's text at its revision; a brand-new draft may also change its slug until it is published (left out:
    it keeps the current one)."""

    revision: int = Field(ge=1, strict=True)
    slug: Slug | None = None


class Resources(BaseModel):
    """The resources a brand-new document is built for."""

    model_config = ConfigDict(extra='forbid')
    code_project_ids: ResourceIds = Field(default_factory=list)
    service_ids: ResourceIds = Field(default_factory=list)
    server_ids: ResourceIds = Field(default_factory=list)
    deployment_ids: ResourceIds = Field(default_factory=list)
    environment_ids: ResourceIds = Field(default_factory=list)


class DraftCreate(BaseModel):
    """A draft of a document (document_id; the text defaults to the live version's; base_version: the version the
    person loaded, default the live one) or of a new document (slug, title and Markdown, optional
    resources it is built for; a new inventory document is one tagged inventory)."""

    model_config = ConfigDict(extra='forbid')
    document_id: UUID | None = None
    base_version: int | None = Field(default=None, ge=1, strict=True)
    slug: Slug | None = None
    title: Title | None = None
    description: Description | None = None
    purpose: Purpose | None = None
    audience: Audience | None = None
    tags: Tags | None = None
    markdown: Markdown | None = None
    resource_ids: Resources | None = None

    @model_validator(mode='after')
    def target(self):
        if (self.document_id is None) == (self.slug is None):
            raise ValueError('Give a document_id, or a slug for a new document.')
        if self.document_id is not None and self.resource_ids is not None:
            raise ValueError('A draft of a document keeps its linked resources.')
        if self.document_id is None and self.base_version is not None:
            raise ValueError('Only a draft of a document has a base version.')
        if self.document_id is None and (self.title is None or self.markdown is None):
            raise ValueError('A new document needs a title and Markdown.')
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


class Promotion(Decision):
    confirm_stale: bool = Field(default=False, strict=True)


# {extra}: more columns; the only placeholder (no other braces in the SQL). A run's draft (session_id) reports origin
# 'agent', a person's 'manual'; published: it revises a live document (false: a brand-new draft), document_title its
# title (null for a brand-new draft), tags its tags (a draft's own); l is the live document a draft revises.
SUMMARY = """SELECT x.id,x.document_id,x.slug,x.title,
    CASE WHEN x.session_id IS NULL THEN 'manual' ELSE 'agent' END AS origin,x.session_id,s.title AS session_title,
    s.purpose AS session_purpose,p.email AS author,x.edited,x.revision,x.base_version,x.live_version,x.stale,
    x.document_id IS NOT NULL AS published,x.document_title,x.tags,x.created_at,x.updated_at{extra}
    FROM ka.document_drafts_view x LEFT JOIN ka.documents l ON l.id=x.document_id
    LEFT JOIN ka.agent_sessions s ON s.id=x.session_id LEFT JOIN ka.principals p ON p.id=x.created_by"""
DETAIL_EXTRA = """,x.description,x.purpose,x.audience,x.markdown,
    jsonb_build_object('code_project_ids',x.code_project_ids,'service_ids',x.service_ids,'server_ids',x.server_ids,
    'deployment_ids',x.deployment_ids,'environment_ids',x.environment_ids) AS resource_ids,
    l.markdown AS live_markdown"""
# GET /drafts filters: query parameter → the view's link array (fixed identifiers, never taken from a request).
RESOURCE_FILTERS = {
    'environment_id': 'environment_ids',
    'service_id': 'service_ids',
    'server_id': 'server_ids',
    'code_project_id': 'code_project_ids',
    'deployment_id': 'deployment_ids',
}


LINK_SELF = 'A document cannot depend on itself.'
LINK_TWICE = 'This document already depends on that document.'
LINK_NOT_FOUND = 'This link does not exist.'
LINKED = """SELECT d.id,d.title,d.slug,d.version FROM ka.document_links l JOIN ka.document_details d ON d.id=l.{other}
    WHERE l.{this}=%s AND l.kind='depends_on' ORDER BY lower(d.title),d.id"""


class LinkInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    to_document_id: UUID


def connections(conn, project: UUID, document_id: UUID) -> dict:
    """What a document depends on, what depends on it (links people add) and what its live version was built from."""
    if live_document(conn, project, document_id) is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return {
        'depends_on': conn.execute(
            LINKED.format(this='from_document_id', other='to_document_id'), (document_id,)
        ).fetchall(),
        'used_by': conn.execute(
            LINKED.format(this='to_document_id', other='from_document_id'), (document_id,)
        ).fetchall(),
        'built_from': sources_of(conn, document_id),
    }


def live_document(conn, project: UUID, document_id: UUID) -> dict | None:
    return conn.execute(
        'SELECT id FROM ka.documents WHERE id=%s AND project_id=%s AND NOT is_draft', (document_id, project)
    ).fetchone()


def project_draft(conn, project: UUID, draft_id: UUID) -> dict:
    """The draft's session and whether it revises a live document, 404 unless it is a draft of `project`."""
    row = conn.execute(
        'SELECT id,session_id,draft_of,slug FROM ka.documents WHERE id=%s AND project_id=%s AND is_draft',
        (draft_id, project),
    ).fetchone()
    if row is None:
        raise HTTPException(404, DRAFT_NOT_FOUND)
    return row


def sources_of(conn, document_id: UUID) -> list[dict]:
    """What a draft or a live document's current version was built from, the parent first; `deleted` when the source
    document is gone."""
    return conn.execute(
        """SELECT kind,source_document_id,source_title,source_version,source_document_id IS NULL AS deleted
        FROM ka.document_sources WHERE document_id=%s ORDER BY kind='parent' DESC,lower(source_title),source_title,id""",
        (document_id,),
    ).fetchall()


def live_content(conn, project: UUID, document_id: UUID) -> dict:
    row = conn.execute(
        """SELECT title,description,purpose,audience,tags,markdown FROM ka.documents
        WHERE id=%s AND project_id=%s AND NOT is_draft""",
        (document_id, project),
    ).fetchone()
    if row is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return dict(row)


def document_named(conn, project: UUID, slug: str) -> dict | None:
    """The live document (id, version) a slug names, or None."""
    return conn.execute(
        'SELECT id,version FROM ka.documents WHERE project_id=%s AND NOT is_draft AND slug=%s', (project, slug)
    ).fetchone()


def draft_detail(conn, project: UUID, draft_id: UUID) -> dict:
    row = conn.execute(
        SUMMARY.format(extra=DETAIL_EXTRA) + ' WHERE x.id=%s AND x.project_id=%s', (draft_id, project)
    ).fetchone()
    if row is None:
        raise HTTPException(404, DRAFT_NOT_FOUND)
    row['sources'] = sources_of(conn, draft_id)
    return row


RESOURCE_QUERIES = {
    'code_project_ids': 'SELECT id FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL AND id=ANY(%s)',
    'service_ids': 'SELECT id FROM ka.services WHERE project_id=%s AND id=ANY(%s)',
    'server_ids': 'SELECT id FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
    'environment_ids': 'SELECT id FROM ka.environments WHERE project_id=%s AND id=ANY(%s)',
}


def available_resources(conn, project: UUID, wanted: dict) -> dict:
    """The chosen resources that still exist, as link fields: deleted entries and archived code projects drop
    out, and a deployment stays only with its service and server."""
    chosen = {key: [UUID(str(item)) for item in wanted.get(key, [])] for key in DOCUMENT_RESOURCE_FIELDS}
    result = {}
    for key, query in RESOURCE_QUERIES.items():
        found = {row['id'] for row in conn.execute(query, (project, chosen[key])).fetchall()}
        result[key] = [item for item in chosen[key] if item in found]
    deployments = conn.execute(
        'SELECT id,service_id,server_id FROM ka.service_deployments WHERE project_id=%s AND id=ANY(%s)',
        (project, chosen['deployment_ids']),
    ).fetchall()
    usable = {
        row['id']
        for row in deployments
        if row['service_id'] in result['service_ids'] and row['server_id'] in result['server_ids']
    }
    result['deployment_ids'] = [item for item in chosen['deployment_ids'] if item in usable]
    return result


def free_slug(conn, project, base: str) -> str:
    """The first slug from `base` (base, base-2, base-3, ...) that no document or brand-new draft of the project has."""
    base = base[:110].rstrip('-') or 'document'
    for number in range(1, 1000):
        slug = base if number == 1 else f'{base}-{number}'
        if not conn.execute('SELECT 1 FROM ka.documents WHERE project_id=%s AND slug=%s', (project, slug)).fetchone():
            return slug
    raise HTTPException(409, 'Too many documents with this name. Rename one and try again.')


# The session `s`'s last draft (agent_sessions.last_draft_id) as {id, document_id, status} (open, promoted, discarded
# or replaced; store.DRAFT_OUTCOME), for session rows and the live session state: a run has one once its agent saved
# a document with new_document, update_document or edit_document.
SESSION_DRAFT = lifecycle.DRAFT_OUTCOME.format(draft='s.last_draft_id') + ' AS document_draft'


def call_arguments(payload: dict) -> dict:
    """A recorded tool call's arguments. A native session records them as an object (its recorder parses the SDK's
    JSON string); a JSON string is parsed too. Anything else: no arguments."""
    value = payload.get('arguments')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


# The version of a live document that was live at a moment: the live row or an archive row, by when it went live.
VERSION_AT = """SELECT version,title FROM (
    SELECT version,title,version_created_at AS live_from FROM ka.documents WHERE id=%s AND NOT is_draft
    UNION ALL SELECT version,title,created_at FROM ka.document_archive WHERE document_id=%s) v
    WHERE live_from<=%s ORDER BY version DESC LIMIT 1"""


def record_run_sources(
    conn, project: UUID, draft_id: UUID, document_id: UUID | None, session_id: UUID, turn: int
) -> None:
    """Recompute a run draft's read sources: every document the run read with get_document in turns 1..turn, at the
    version live at its first read. The parent source (the document the draft revises, `document_id`) stays as
    create_draft wrote it. Reads only: no row of ka.projects or of a live document is locked."""
    conn.execute("DELETE FROM ka.document_sources WHERE document_id=%s AND kind='read'", (draft_id,))
    keys = [f'cc:{session_id}:{number}' for number in range(1, turn + 1)]
    calls = conn.execute(
        """SELECT payload,created_at FROM ka.agent_run_events WHERE run_key=ANY(%s) AND kind='tool_call'
        AND payload->>'name'='get_document' ORDER BY id""",
        (keys,),
    ).fetchall()
    for call in calls:
        arguments = call_arguments(call['payload'])
        try:
            wanted = UUID(str(arguments.get('document_id', '')))
        except ValueError:
            continue
        found = live_document(conn, project, wanted)
        if found is None or found['id'] == document_id:
            continue
        version = conn.execute(VERSION_AT, (found['id'], found['id'], call['created_at'])).fetchone()
        if version is None:  # read before the document existed
            continue
        conn.execute(
            """INSERT INTO ka.document_sources(project_id,document_id,kind,source_document_id,source_title,
            source_version) VALUES (%s,%s,'read',%s,%s,%s) ON CONFLICT DO NOTHING""",
            (project, draft_id, found['id'], version['title'], version['version']),
        )


def refresh_run_sources_safely(conn, session_id: UUID, turn: int) -> None:
    """A turn ended: recompute the sources of each of the run's working drafts from every tool call recorded so far
    (a read recorded after the save that preceded it). In a savepoint; a failure loses only this refresh."""
    try:
        with conn.transaction():
            drafts = conn.execute(
                """SELECT id,project_id,draft_of FROM ka.documents WHERE is_draft AND session_id=%s AND NOT edited
                ORDER BY created_at,id FOR UPDATE""",
                (session_id,),
            ).fetchall()
            for draft in drafts:
                record_run_sources(conn, draft['project_id'], draft['id'], draft['draft_of'], session_id, turn)
    except Exception:  # any failure, a bug included, rolls back only the savepoint
        logger.exception('Session %s turn %s: the draft sources were not refreshed', session_id, turn)


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/documentation')

    database = partial(storage, store, 'Document storage', message='Document storage is not configured')

    @router.post('/drafts', status_code=201)
    def create(value: DraftCreate, request: Request) -> dict:
        db = database()
        try:
            with workspace(db, app_name) as (conn, project):
                conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
                actor = db.actor(conn, *request_actor(request))
                given = value.model_dump(include=set(CONTENT), exclude_none=True)
                if value.document_id is not None:
                    content = {**live_content(conn, project, value.document_id), **given}
                elif document_named(conn, project, value.slug) is not None:
                    # A person names a new document; a live document's name is taken (a run's save drafts it).
                    raise HTTPException(409, NAME_IN_USE.format(slug=value.slug))
                else:
                    content = {**NEW_DEFAULTS, **given}
                draft_id = lifecycle.create_draft(
                    conn,
                    project,
                    Provenance('person', actor),
                    content,
                    origin='manual',
                    document_id=value.document_id,
                    slug=value.slug,
                    links=value.resource_ids.model_dump() if value.resource_ids else None,
                    base_version=value.base_version,
                )
                return jsonable_encoder(draft_detail(conn, project, draft_id))
        except psycopg.errors.UniqueViolation:  # a brand-new draft already holds this slug
            raise HTTPException(409, NAME_IN_USE.format(slug=value.slug)) from None
        except psycopg.errors.ForeignKeyViolation:  # a chosen resource is gone or not this workspace's
            raise HTTPException(400, RESOURCE_GONE) from None

    @router.get('/drafts/{draft_id}')
    def get_draft(draft_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            return jsonable_encoder(draft_detail(conn, db.project(conn, app_name), draft_id))

    @router.put('/drafts/{draft_id}')
    def update(draft_id: UUID, value: DraftUpdate, request: Request) -> dict:
        db = database()
        slug = None
        try:
            with workspace(db, app_name) as (conn, project):
                actor = db.actor(conn, *request_actor(request))
                row = project_draft(conn, project, draft_id)
                if value.slug is not None and row['draft_of'] is not None:
                    raise HTTPException(400, KEEPS_NAME)
                slug = value.slug or row['slug']
                revision = lifecycle.update_draft(
                    conn,
                    draft_id,
                    actor,
                    value.model_dump(include=set(CONTENT)),
                    revision=value.revision,
                    edited=True,
                )
                details = {'revision': revision}
                if slug != row['slug']:
                    conn.execute('UPDATE ka.documents SET slug=%s WHERE id=%s', (slug, draft_id))
                    details.update(slug=slug)
                audit(conn, project, actor, 'document.draft.updated', 'document_draft', draft_id, details)
                return jsonable_encoder(draft_detail(conn, project, draft_id))
        except psycopg.errors.UniqueViolation:  # a live document or another brand-new draft holds this name
            raise HTTPException(409, NAME_IN_USE.format(slug=slug)) from None

    def lock_for_decision(conn, project: UUID, draft_id: UUID) -> dict:
        """The project, then the draft's run (the order a run's turn locks them in); the caller then locks the draft."""
        conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
        row = project_draft(conn, project, draft_id)
        if row['session_id'] is not None:
            conn.execute('SELECT id FROM ka.agent_sessions WHERE id=%s FOR UPDATE', (row['session_id'],))
        return row

    @router.post('/drafts/{draft_id}/discard')
    def discard(draft_id: UUID, value: Decision, request: Request) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            lock_for_decision(conn, project, draft_id)
            actor = db.actor(conn, *request_actor(request))
            lifecycle.discard_draft(conn, project, actor, draft_id, revision=value.revision)
            return {}

    @router.post('/drafts/{draft_id}/promote')
    def promote(draft_id: UUID, value: Promotion, request: Request) -> dict:
        db = database()
        try:
            with workspace(db, app_name) as (conn, project):
                row = lock_for_decision(conn, project, draft_id)
                actor = db.actor(conn, *request_actor(request))
                result = lifecycle.promote_draft(
                    conn, project, actor, draft_id, revision=value.revision, confirm_stale=value.confirm_stale
                )
                if row['session_id'] is not None:  # a new-document run records the document it wrote
                    conn.execute(
                        "UPDATE ka.agent_sessions SET document_id=%s WHERE id=%s AND purpose='documents'",
                        (result['document_id'], row['session_id']),
                    )
        except ValidationError as exc:
            raise HTTPException(400, '; '.join(error['msg'] for error in exc.errors())) from None
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, SAVED_MEANWHILE) from None
        return jsonable_encoder(
            {
                'document_id': result['document_id'],
                'version': result['version'],
                'revision': result['revision'],
                'deleted_drafts': result['deleted_drafts'],
            }
        )

    @router.get('/drafts')
    def drafts(
        new: bool = False,
        environment_id: UUID | None = None,
        service_id: UUID | None = None,
        server_id: UUID | None = None,
        code_project_id: UUID | None = None,
        deployment_id: UUID | None = None,
        tag: str | None = Query(default=None, max_length=80),
    ) -> dict:
        """Drafts, newest first; new=true: only brand-new documents (the Documents page's New drafts). A resource
        filter keeps the drafts linked to it: brand-new drafts by their own links, drafts of a document by its links.
        `tag`: the drafts with that tag (one tag, exact, case-insensitive)."""
        db = database()
        given = {
            'environment_id': environment_id,
            'service_id': service_id,
            'server_id': server_id,
            'code_project_id': code_project_id,
            'deployment_id': deployment_id,
        }
        where, values = ['x.project_id=%s', '(NOT %s OR x.document_id IS NULL)'], [new]
        for name, resource_id in given.items():
            if resource_id is not None:
                where.append(f'%s=ANY(x.{RESOURCE_FILTERS[name]})')
                values.append(resource_id)
        tag = (tag or '').strip() or None  # blank: no tag filter
        if tag is not None:
            where.append('EXISTS (SELECT 1 FROM unnest(x.tags) t WHERE lower(t)=lower(%s))')
            values.append(tag)
        with db.connect() as conn:
            rows = conn.execute(
                SUMMARY.format(extra='') + f' WHERE {" AND ".join(where)} ORDER BY x.created_at DESC,x.id',
                (db.project(conn, app_name), *values),
            ).fetchall()
            return jsonable_encoder({'drafts': rows})

    @router.get('/documents/{document_id}/drafts')
    def drafts_of_document(document_id: UUID) -> dict:
        """The document's drafts, newest first."""
        db = database()
        with workspace(db, app_name) as (conn, project):
            if live_document(conn, project, document_id) is None:
                raise HTTPException(404, DOCUMENT_NOT_FOUND)
            rows = conn.execute(
                SUMMARY.format(extra='') + ' WHERE x.document_id=%s ORDER BY x.created_at DESC,x.id',
                (document_id,),
            ).fetchall()
            return jsonable_encoder({'drafts': rows})

    @router.get('/documents/{document_id}/connections')
    def document_connections(document_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            return jsonable_encoder(connections(conn, db.project(conn, app_name), document_id))

    @router.post('/documents/{document_id}/links', status_code=201)
    def add_link(document_id: UUID, value: LinkInput, request: Request) -> dict:
        if value.to_document_id == document_id:
            raise HTTPException(400, LINK_SELF)
        db = database()
        with workspace(db, app_name) as (conn, project):
            actor = db.actor(conn, *request_actor(request))
            found = conn.execute(
                'SELECT count(*) AS n FROM ka.documents WHERE project_id=%s AND NOT is_draft AND id=ANY(%s)',
                (project, [document_id, value.to_document_id]),
            ).fetchone()['n']
            if found != 2:
                raise HTTPException(404, DOCUMENT_NOT_FOUND)
            try:
                added = conn.execute(
                    """INSERT INTO ka.document_links(project_id,from_document_id,to_document_id,created_by)
                    VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                    (project, document_id, value.to_document_id, actor),
                ).fetchone()
            except psycopg.errors.ForeignKeyViolation:  # either document was deleted after the check above
                raise HTTPException(404, DOCUMENT_NOT_FOUND) from None
            if added is None:
                raise HTTPException(409, LINK_TWICE)
            audit(
                conn,
                project,
                actor,
                'document.link.added',
                'document',
                document_id,
                {'to_document_id': value.to_document_id, 'kind': 'depends_on'},
            )
            return jsonable_encoder(connections(conn, project, document_id))

    @router.delete('/documents/{document_id}/links/{to_document_id}')
    def remove_link(document_id: UUID, to_document_id: UUID, request: Request) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            actor = db.actor(conn, *request_actor(request))
            removed = conn.execute(
                """DELETE FROM ka.document_links WHERE project_id=%s AND from_document_id=%s
                AND to_document_id=%s AND kind='depends_on' RETURNING id""",
                (project, document_id, to_document_id),
            ).fetchone()
            if removed is None:
                raise HTTPException(404, LINK_NOT_FOUND)
            audit(
                conn,
                project,
                actor,
                'document.link.removed',
                'document',
                document_id,
                {'to_document_id': to_document_id, 'kind': 'depends_on'},
            )
            return jsonable_encoder(connections(conn, project, document_id))

    return router
