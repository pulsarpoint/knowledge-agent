"""Document drafts: unpublished versions of a document, or of a new document, that a person promotes to the live
version or discards. Agents only ever write drafts. Drafts live in ka.document_drafts, apart from
ka.document_versions, so version history, document_details, exports and folder statistics see live versions only."""
from functools import partial
import json
import logging
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.data_root import documentation_folder, generated_docs_folder
from knowledge_admin.db import audit, storage, workspace
from knowledge_admin.documents.api import (DOCUMENT_RESOURCE_FIELDS, Audience, Description, DocumentInput, Markdown, Purpose, ResourceIds, Slug, Tags,
                                       Title, export_args, export_document, export_input, save_document)
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

DRAFT_NOT_FOUND = 'Draft not found.'
DOCUMENT_NOT_FOUND = 'Document not found'
DRAFT_CHANGED = 'This draft changed. Reload it before saving; your text has not been overwritten.'
DRAFT_DECIDED = 'This draft was already promoted or discarded.'
STALE_BASE = 'STALE_BASE'
STALE_MESSAGE = ("This draft was based on version {base}; the live version is now {live}. "
                 "Promoting replaces version {live}'s text.")
NAME_TAKEN = ('A document named {slug} was created in this folder meanwhile. The draft is now a draft of it. '
              'Review it again.')
SAVED_MEANWHILE = 'Another document was saved with this name at the same time. Try again.'
EXPORT_FAILED = 'Saved. Writing the document file failed; use Export to folder to retry.'
CONTENT = ('title', 'description', 'purpose', 'audience', 'tags', 'markdown')
NEW_DEFAULTS = {'description': '', 'purpose': 'explanation', 'audience': 'both', 'tags': []}


class DraftContent(BaseModel):
    """A draft's text, with DocumentInput's limits and text rules."""
    model_config = ConfigDict(extra='forbid')
    title: Title
    description: Description = ''
    purpose: Purpose = 'explanation'
    audience: Audience = 'both'
    tags: Tags = Field(default_factory=list)
    markdown: Markdown


class DraftUpdate(DraftContent):
    revision: int = Field(ge=1, strict=True)


class Resources(BaseModel):
    """What a new document links on promote."""
    model_config = ConfigDict(extra='forbid')
    code_project_ids: ResourceIds = Field(default_factory=list)
    service_ids: ResourceIds = Field(default_factory=list)
    server_ids: ResourceIds = Field(default_factory=list)
    deployment_ids: ResourceIds = Field(default_factory=list)
    environment_ids: ResourceIds = Field(default_factory=list)


class DraftCreate(BaseModel):
    """A draft of a document (document_id; the text defaults to the live version's) or of a new document (slug, an
    optional folder, title and Markdown, optional resources)."""
    model_config = ConfigDict(extra='forbid')
    document_id: UUID | None = None
    slug: Slug | None = None
    folder: str | None = Field(default=None, max_length=200)
    title: Title | None = None
    description: Description | None = None
    purpose: Purpose | None = None
    audience: Audience | None = None
    tags: Tags | None = None
    markdown: Markdown | None = None
    resource_ids: Resources | None = None

    @field_validator('folder')
    @classmethod
    def valid_folder(cls, value: str | None) -> str | None:
        return None if value is None else documentation_folder(value)

    @model_validator(mode='after')
    def target(self):
        if (self.document_id is None) == (self.slug is None):
            raise ValueError('Give a document_id, or a slug for a new document.')
        if self.document_id is not None and (self.folder is not None or self.resource_ids is not None):
            raise ValueError('A draft of a document keeps its folder and linked resources.')
        if self.document_id is None and (self.title is None or self.markdown is None):
            raise ValueError('A new document needs a title and Markdown.')
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


class Promotion(Decision):
    confirm_stale: bool = Field(default=False, strict=True)


# {extra}: more columns; the only placeholder (no other braces in the SQL).
SUMMARY = '''SELECT x.id,x.document_id,x.slug,x.folder,x.title,x.origin,x.session_id,s.title AS session_title,s.purpose AS session_purpose,
    p.email AS author,x.edited,x.status,x.revision,x.base_version_id,bv.version AS base_version,lv.version AS live_version,
    (x.document_id IS NOT NULL AND x.status='open' AND lv.id IS DISTINCT FROM x.base_version_id) AS stale,
    x.created_at,x.updated_at{extra}
    FROM ka.document_drafts x LEFT JOIN ka.document_versions bv ON bv.id=x.base_version_id
    LEFT JOIN ka.documents d ON d.id=x.document_id LEFT JOIN ka.document_versions lv ON lv.id=d.current_version_id
    LEFT JOIN ka.agent_sessions s ON s.id=x.session_id LEFT JOIN ka.principals p ON p.id=x.created_by'''
DETAIL_EXTRA = ''',x.description,x.purpose,x.audience,x.tags,x.markdown,x.resource_ids,x.promoted_version_id,x.decided_at,
    lv.markdown AS live_markdown,lv.title AS document_title'''


LINK_SELF = 'A document cannot depend on itself.'
LINK_TWICE = 'This document already depends on that document.'
LINK_NOT_FOUND = 'This link does not exist.'
LINKED = '''SELECT d.id,d.title,d.slug,d.folder,d.version FROM ka.document_links l JOIN ka.document_details d ON d.id=l.{other}
    WHERE l.{this}=%s AND l.kind='depends_on' ORDER BY lower(d.title),d.id'''


class LinkInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    to_document_id: UUID


def connections(conn, project: UUID, document_id: UUID) -> dict:
    """What a document depends on, what depends on it (links people add) and what its live version was built from."""
    row = conn.execute('SELECT current_version_id FROM ka.documents WHERE id=%s AND project_id=%s',
                       (document_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return {'depends_on': conn.execute(LINKED.format(this='from_document_id', other='to_document_id'), (document_id,)).fetchall(),
            'used_by': conn.execute(LINKED.format(this='to_document_id', other='from_document_id'), (document_id,)).fetchall(),
            'built_from': sources_of(conn, 'version_id', row['current_version_id'])}


def sources_of(conn, column: Literal['draft_id', 'version_id'], owner: UUID) -> list[dict]:
    """What a draft or a live version was built from, the parent first; `deleted` when the source document is gone.
    `column` is fixed by the caller, never taken from a request."""
    if column not in ('draft_id', 'version_id'):
        raise ValueError(column)
    return conn.execute(f'''SELECT kind,source_document_id,source_version_id,source_title,source_version,
        source_document_id IS NULL AS deleted FROM ka.document_sources WHERE {column}=%s
        ORDER BY kind='parent' DESC,lower(source_title),source_title,id''', (owner,)).fetchall()


def add_parent_source(conn, project: UUID, draft_id: UUID, version_id: UUID | None) -> None:
    """The document a draft revises, at the version it started from."""
    if version_id is None:
        return
    conn.execute('''INSERT INTO ka.document_sources(project_id,draft_id,kind,source_document_id,source_version_id,
        source_title,source_version) SELECT %s,%s,'parent',document_id,id,title,version FROM ka.document_versions
        WHERE id=%s ON CONFLICT DO NOTHING''', (project, draft_id, version_id))


def live_content(conn, project: UUID, document_id: UUID) -> dict:
    row = conn.execute('''SELECT v.title,v.description,v.purpose,v.audience,v.tags,v.markdown FROM ka.documents d
        JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s AND d.project_id=%s''',
        (document_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return dict(row)


def document_named(conn, project: UUID, folder: str | None, slug: str) -> dict | None:
    """The document (id, current_version_id) a folder and slug name, or None."""
    return conn.execute('''SELECT id,current_version_id FROM ka.documents WHERE project_id=%s
        AND folder IS NOT DISTINCT FROM %s AND slug=%s''', (project, folder, slug)).fetchone()


def create_draft(conn, project: UUID, actor: UUID | None, content: dict, *, origin: str, document_id: UUID | None = None,
                 slug: str | None = None, folder: str | None = None, resources: dict | None = None,
                 session_id: UUID | None = None) -> UUID:
    """Insert an open draft (and, for a draft of a document, its parent source). A new document whose folder and slug
    already name a document becomes a draft of that document."""
    if document_id is None:
        existing = document_named(conn, project, folder, slug)
        if existing is not None:
            document_id = existing['id']
    base = None
    if document_id is not None:
        row = conn.execute('SELECT current_version_id FROM ka.documents WHERE id=%s AND project_id=%s',
                           (document_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, DOCUMENT_NOT_FOUND)
        base, slug, folder, resources = row['current_version_id'], None, None, {}
    draft_id = conn.execute('''INSERT INTO ka.document_drafts(project_id,document_id,slug,folder,resource_ids,
        base_version_id,title,description,purpose,audience,tags,markdown,origin,session_id,created_by,updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
        (project, document_id, slug, folder, Jsonb(resources or {}), base, content['title'], content['description'],
         content['purpose'], content['audience'], content['tags'], content['markdown'], origin, session_id, actor,
         actor)).fetchone()['id']
    add_parent_source(conn, project, draft_id, base)
    audit(conn, project, actor, 'document.draft.created', 'document_draft', draft_id,
          {'document_id': document_id, 'origin': origin, 'session_id': session_id})
    return draft_id


def draft_detail(conn, project: UUID, draft_id: UUID) -> dict:
    row = conn.execute(SUMMARY.format(extra=DETAIL_EXTRA) + ' WHERE x.id=%s AND x.project_id=%s',
                       (draft_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DRAFT_NOT_FOUND)
    row['sources'] = sources_of(conn, 'draft_id', draft_id)
    return row


def open_draft(conn, project: UUID, draft_id: UUID, revision: int) -> dict:
    """The draft locked for a change: 404 when missing, 409 when decided (checked first) or changed."""
    row = conn.execute('SELECT * FROM ka.document_drafts WHERE id=%s AND project_id=%s FOR UPDATE',
                       (draft_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DRAFT_NOT_FOUND)
    if row['status'] != 'open':
        raise HTTPException(409, DRAFT_DECIDED)
    if row['revision'] != revision:
        raise HTTPException(409, DRAFT_CHANGED)
    return row


RESOURCE_QUERIES = {
    'code_project_ids': 'SELECT id FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL AND id=ANY(%s)',
    'service_ids': 'SELECT id FROM ka.services WHERE project_id=%s AND id=ANY(%s)',
    'server_ids': 'SELECT id FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
    'environment_ids': 'SELECT id FROM ka.environments WHERE project_id=%s AND id=ANY(%s)',
}


def available_resources(conn, project: UUID, wanted: dict) -> dict:
    """The chosen resources that still exist, as DocumentInput fields: deleted entries and archived code projects drop
    out, and a deployment stays only with its service and server."""
    chosen = {key: [UUID(str(item)) for item in wanted.get(key, [])] for key in DOCUMENT_RESOURCE_FIELDS}
    result = {}
    for key, query in RESOURCE_QUERIES.items():
        found = {row['id'] for row in conn.execute(query, (project, chosen[key])).fetchall()}
        result[key] = [item for item in chosen[key] if item in found]
    deployments = conn.execute('SELECT id,service_id,server_id FROM ka.service_deployments WHERE project_id=%s AND id=ANY(%s)',
                               (project, chosen['deployment_ids'])).fetchall()
    usable = {row['id'] for row in deployments
              if row['service_id'] in result['service_ids'] and row['server_id'] in result['server_ids']}
    result['deployment_ids'] = [item for item in chosen['deployment_ids'] if item in usable]
    return result


def promote_draft(conn, project: UUID, actor: UUID, row: dict, confirm_stale: bool) -> dict:
    """Save the open, locked draft `row` as the live version (save_document), mark it promoted and copy its sources to
    the version. A stale draft needs `confirm_stale`; a new document whose name was taken meanwhile is converted into a
    draft of that document, committed, and refused."""
    origin = 'generated' if row['origin'] == 'agent' else 'manual'
    content = {key: row[key] for key in CONTENT}
    if row['document_id'] is not None:
        live = conn.execute('''SELECT current_version_id,version,slug,revision,code_project_ids,service_ids,server_ids,
            deployment_ids,environment_ids FROM ka.document_details WHERE id=%s''', (row['document_id'],)).fetchone()
        if live['current_version_id'] != row['base_version_id'] and not confirm_stale:
            base = conn.execute('SELECT version FROM ka.document_versions WHERE id=%s', (row['base_version_id'],)).fetchone()
            raise HTTPException(409, {'message': STALE_MESSAGE.format(base=base['version'] if base else '?', live=live['version']),
                                      'code': STALE_BASE})
        # The document's links that still exist: an archived code project drops out instead of refusing the save.
        linked = available_resources(conn, project, {key: live[key] for key in DOCUMENT_RESOURCE_FIELDS})
        saved = save_document(conn, project, actor, DocumentInput(
            slug=live['slug'], **content, **linked, revision=live['revision']), row['document_id'], origin=origin)
    else:
        clash = document_named(conn, project, row['folder'], row['slug'])
        if clash is not None:
            conn.execute('''UPDATE ka.document_drafts SET document_id=%s,base_version_id=%s,slug=NULL,folder=NULL,
                resource_ids='{}',revision=revision+1 WHERE id=%s''', (clash['id'], clash['current_version_id'], row['id']))
            conn.execute('DELETE FROM ka.document_sources WHERE draft_id=%s AND source_document_id=%s', (row['id'], clash['id']))
            add_parent_source(conn, project, row['id'], clash['current_version_id'])
            conn.commit()  # kept although the request fails, so the reload shows the converted draft
            raise HTTPException(409, NAME_TAKEN.format(slug=row['slug']))
        saved = save_document(conn, project, actor, DocumentInput(
            slug=row['slug'], **content, **available_resources(conn, project, row['resource_ids'])),
            None, folder=row['folder'], origin=origin)
    conn.execute('''UPDATE ka.document_drafts SET status='promoted',promoted_version_id=%s,document_id=%s,slug=NULL,
        folder=NULL,decided_by=%s,decided_at=now(),revision=revision+1 WHERE id=%s''',
        (saved['version_id'], saved['id'], actor, row['id']))
    conn.execute('''INSERT INTO ka.document_sources(project_id,version_id,kind,source_document_id,source_version_id,
        source_title,source_version) SELECT project_id,%s,kind,source_document_id,source_version_id,source_title,
        source_version FROM ka.document_sources WHERE draft_id=%s''', (saved['version_id'], row['id']))
    if row['session_id'] is not None:  # a new-document run records the document it wrote
        conn.execute("UPDATE ka.agent_sessions SET document_id=%s WHERE id=%s AND purpose='documents'",
                     (saved['id'], row['session_id']))
    audit(conn, project, actor, 'document.draft.promoted', 'document_draft', row['id'],
          {'document_id': saved['id'], 'version': saved['revision'], 'version_id': saved['version_id'],
           'stale_confirmed': confirm_stale})
    return saved


# The session `s`'s latest document draft of any status (open, promoted or discarded), for session rows and the live
# session state: a run has one once its agent saved a document with save_document or edit_document.
SESSION_DRAFT = """(SELECT jsonb_build_object('id',x.id,'document_id',x.document_id,'status',x.status)
    FROM ka.document_drafts x WHERE x.session_id=s.id ORDER BY x.created_at DESC,x.id DESC LIMIT 1) AS document_draft"""
DOCUMENT_TOOLS = ['get_document', 'read_document']


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


def export_key(path: str) -> tuple[str | None, str] | None:
    """(folder, slug) of a document export path [<GENERATED_DOCS_FOLDER>/][<folder>/]<slug>/README.md, else None."""
    parts = [part for part in path.strip().split('/') if part]
    try:
        prefix = generated_docs_folder().split('/')
    except ValueError:
        prefix = []
    if prefix and parts[:len(prefix)] == prefix and len(parts) > len(prefix) + 1:
        parts = parts[len(prefix):]
    if len(parts) < 2 or parts[-1] != 'README.md':
        return None
    return '/'.join(parts[:-2]) or None, parts[-2]


def record_run_sources(conn, project: UUID, draft_id: UUID, document_id: UUID | None, base_version_id: UUID | None,
                       session_id: UUID, turn: int) -> None:
    """Recompute a run draft's sources: its parent, and every document the run read with get_document or
    read_document (an export path) in turns 1..turn, at the version live at its first read. Reads only: no row of
    ka.documents or ka.projects is locked."""
    conn.execute('DELETE FROM ka.document_sources WHERE draft_id=%s', (draft_id,))
    if document_id is not None:
        add_parent_source(conn, project, draft_id, base_version_id)
    keys = [f'cc:{session_id}:{number}' for number in range(1, turn + 1)]
    calls = conn.execute('''SELECT payload,created_at FROM ka.agent_run_events WHERE run_key=ANY(%s) AND kind='tool_call'
        AND payload->>'name'=ANY(%s) ORDER BY id''', (keys, DOCUMENT_TOOLS)).fetchall()
    for call in calls:
        arguments = call_arguments(call['payload'])
        if call['payload']['name'] == 'get_document':
            try:
                wanted = UUID(str(arguments.get('document_id', '')))
            except ValueError:
                continue
            found = conn.execute('SELECT id FROM ka.documents WHERE id=%s AND project_id=%s', (wanted, project)).fetchone()
        else:
            key = export_key(str(arguments.get('path', '')))
            if key is None:
                continue
            found = conn.execute('''SELECT id FROM ka.documents WHERE project_id=%s AND folder IS NOT DISTINCT FROM %s
                AND slug=%s AND phase_metadata IS NULL''', (project, *key)).fetchone()
        if found is None or found['id'] == document_id:
            continue
        version = conn.execute('''SELECT id,version,title FROM ka.document_versions WHERE document_id=%s AND created_at<=%s
            ORDER BY version DESC LIMIT 1''', (found['id'], call['created_at'])).fetchone()
        if version is None:  # read before the document existed
            continue
        conn.execute('''INSERT INTO ka.document_sources(project_id,draft_id,kind,source_document_id,source_version_id,
            source_title,source_version) VALUES (%s,%s,'read',%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (project, draft_id, found['id'], version['id'], version['title'], version['version']))


def refresh_run_sources_safely(conn, session_id: UUID, turn: int) -> None:
    """A turn ended: recompute the sources of each of the run's working drafts from every tool call recorded so far
    (a read recorded after the save that preceded it). In a savepoint; a failure loses only this refresh."""
    try:
        with conn.transaction():
            drafts = conn.execute('''SELECT id,project_id,document_id,base_version_id FROM ka.document_drafts
                WHERE session_id=%s AND status='open' AND NOT edited ORDER BY created_at,id FOR UPDATE''',
                (session_id,)).fetchall()
            for draft in drafts:
                record_run_sources(conn, draft['project_id'], draft['id'], draft['document_id'], draft['base_version_id'],
                                   session_id, turn)
    except Exception:  # any failure, a bug included, rolls back only the savepoint
        logger.exception('Session %s turn %s: the draft sources were not refreshed', session_id, turn)


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/documentation')

    database = partial(storage, store, 'Document storage', message='Document storage is not configured')

    @router.post('/drafts', status_code=201)
    def create(value: DraftCreate, request: Request) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
            actor = db.actor(conn, *request_actor(request))
            given = value.model_dump(include=set(CONTENT), exclude_none=True)
            if value.document_id is not None:
                content = {**live_content(conn, project, value.document_id), **given}
            else:
                content = {**NEW_DEFAULTS, **given}
            resources = value.resource_ids.model_dump(mode='json') if value.resource_ids else {}
            draft_id = create_draft(conn, project, actor, content, origin='manual', document_id=value.document_id,
                                    slug=value.slug, folder=value.folder, resources=resources)
            return jsonable_encoder(draft_detail(conn, project, draft_id))

    @router.get('/drafts/{draft_id}')
    def get_draft(draft_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            return jsonable_encoder(draft_detail(conn, db.project(conn, app_name), draft_id))

    @router.put('/drafts/{draft_id}')
    def update(draft_id: UUID, value: DraftUpdate, request: Request) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            actor = db.actor(conn, *request_actor(request))
            row = open_draft(conn, project, draft_id, value.revision)
            conn.execute('''UPDATE ka.document_drafts SET title=%s,description=%s,purpose=%s,audience=%s,tags=%s,
                markdown=%s,edited=true,updated_by=%s,revision=revision+1 WHERE id=%s''',
                (value.title, value.description, value.purpose, value.audience, value.tags, value.markdown, actor, draft_id))
            audit(conn, project, actor, 'document.draft.updated', 'document_draft', draft_id, {'revision': row['revision'] + 1})
            return jsonable_encoder(draft_detail(conn, project, draft_id))

    @router.post('/drafts/{draft_id}/discard')
    def discard(draft_id: UUID, value: Decision, request: Request) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            actor = db.actor(conn, *request_actor(request))
            open_draft(conn, project, draft_id, value.revision)
            conn.execute('''UPDATE ka.document_drafts SET status='discarded',decided_by=%s,decided_at=now(),
                revision=revision+1 WHERE id=%s''', (actor, draft_id))
            audit(conn, project, actor, 'document.draft.discarded', 'document_draft', draft_id, {})
            return jsonable_encoder(draft_detail(conn, project, draft_id))

    @router.post('/drafts/{draft_id}/promote')
    def promote(draft_id: UUID, value: Promotion, request: Request) -> dict:
        db = database()
        try:
            with workspace(db, app_name) as (conn, project):
                conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
                actor = db.actor(conn, *request_actor(request))
                # The run's session before the draft, the order a run's turn locks them in.
                run = conn.execute('SELECT session_id FROM ka.document_drafts WHERE id=%s AND project_id=%s',
                                   (draft_id, project)).fetchone()
                if run is not None and run['session_id'] is not None:
                    conn.execute('SELECT id FROM ka.agent_sessions WHERE id=%s FOR UPDATE', (run['session_id'],))
                row = open_draft(conn, project, draft_id, value.revision)
                saved = promote_draft(conn, project, actor, row, value.confirm_stale)
                detail = draft_detail(conn, project, draft_id)
        except ValidationError as exc:
            raise HTTPException(400, '; '.join(error['msg'] for error in exc.errors())) from None
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, SAVED_MEANWHILE) from None
        try:  # after the commit, as the editor's save: a failed export never undoes the version
            with db.connect() as conn:
                current = export_input(conn, saved['id'])
            export_document(**export_args(current))
            export_error = None
        except (OSError, ValueError):
            export_error = EXPORT_FAILED
        return jsonable_encoder({'draft': detail, 'document_id': saved['id'], 'version_id': saved['version_id'],
                                 'revision': saved['revision'], 'export_error': export_error})

    @router.get('/drafts')
    def drafts(new: bool = False) -> dict:
        """Open drafts, newest first; new=true: only drafts of new documents (the Documents page's New drafts)."""
        db = database()
        with db.connect() as conn:
            rows = conn.execute(SUMMARY.format(extra='') + """ WHERE x.project_id=%s AND x.status='open'
                AND (NOT %s OR x.document_id IS NULL) ORDER BY x.updated_at DESC,x.id""",
                (db.project(conn, app_name), new)).fetchall()
            return jsonable_encoder({'drafts': rows})

    @router.get('/documents/{document_id}/drafts')
    def drafts_of_document(document_id: UUID) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            if conn.execute('SELECT 1 FROM ka.documents WHERE id=%s AND project_id=%s', (document_id, project)).fetchone() is None:
                raise HTTPException(404, DOCUMENT_NOT_FOUND)
            rows = conn.execute(SUMMARY.format(extra='') + """ WHERE x.document_id=%s AND x.status='open'
                ORDER BY x.updated_at DESC,x.id""", (document_id,)).fetchall()
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
            found = conn.execute('SELECT count(*) AS n FROM ka.documents WHERE project_id=%s AND id=ANY(%s)',
                                 (project, [document_id, value.to_document_id])).fetchone()['n']
            if found != 2:
                raise HTTPException(404, DOCUMENT_NOT_FOUND)
            try:
                added = conn.execute('''INSERT INTO ka.document_links(project_id,from_document_id,to_document_id,created_by)
                    VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id''',
                    (project, document_id, value.to_document_id, actor)).fetchone()
            except psycopg.errors.ForeignKeyViolation:  # either document was deleted after the check above
                raise HTTPException(404, DOCUMENT_NOT_FOUND) from None
            if added is None:
                raise HTTPException(409, LINK_TWICE)
            audit(conn, project, actor, 'document.link.added', 'document', document_id,
                  {'to_document_id': value.to_document_id, 'kind': 'depends_on'})
            return jsonable_encoder(connections(conn, project, document_id))

    @router.delete('/documents/{document_id}/links/{to_document_id}')
    def remove_link(document_id: UUID, to_document_id: UUID, request: Request) -> dict:
        db = database()
        with workspace(db, app_name) as (conn, project):
            actor = db.actor(conn, *request_actor(request))
            removed = conn.execute('''DELETE FROM ka.document_links WHERE project_id=%s AND from_document_id=%s
                AND to_document_id=%s AND kind='depends_on' RETURNING id''', (project, document_id, to_document_id)).fetchone()
            if removed is None:
                raise HTTPException(404, LINK_NOT_FOUND)
            audit(conn, project, actor, 'document.link.removed', 'document', document_id,
                  {'to_document_id': to_document_id, 'kind': 'depends_on'})
            return jsonable_encoder(connections(conn, project, document_id))

    return router
