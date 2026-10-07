"""Managed Markdown documents, inventory associations and immutable versions."""

import hashlib
import io
import json
import logging
import os
import re
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)


GitCommit = Annotated[str, Field(pattern=r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')]
SAME_DOCUMENT_NAME = 'A document with this name already exists in this folder. Confirm to save a new version.'


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    slug: str = Field(min_length=1, max_length=120, pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default='', max_length=4000)
    purpose: str = Field(default='explanation', min_length=1, max_length=80)
    audience: Literal['people', 'agents', 'both'] = 'both'
    tags: list[str] = Field(default_factory=list, max_length=30)
    markdown: str = Field(min_length=1, max_length=1000000)
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=100)
    service_ids: list[UUID] = Field(default_factory=list, max_length=100)
    server_ids: list[UUID] = Field(default_factory=list, max_length=100)
    deployment_ids: list[UUID] = Field(default_factory=list, max_length=100)
    environment_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_commits: dict[UUID, GitCommit] | None = Field(default=None, max_length=100)
    revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator('slug', 'title', 'description', 'purpose', 'markdown')
    @classmethod
    def valid_text(cls, value: str, info) -> str:
        if '\x00' in value or (info.field_name != 'description' and not value.strip()):
            raise ValueError('Enter nonempty text without null characters')
        return value if info.field_name == 'markdown' else value.strip()

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, values: list[str]) -> list[str]:
        tags = list(dict.fromkeys(v.strip() for v in values))
        if any(not v or len(v) > 80 or '\x00' in v for v in tags):
            raise ValueError('Tags must contain 1–80 characters without null characters')
        return tags

    @field_validator('code_project_ids', 'service_ids', 'server_ids', 'deployment_ids', 'environment_ids')
    @classmethod
    def unique_ids(cls, values: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(values))


def document_files(markdown: str) -> dict[str, str]:
    files = {'README.md': markdown}
    for number, source in enumerate(re.findall(r'^```d2\s*\n(.*?)^```\s*$', markdown, re.M | re.S), 1):
        files[f'architecture-{number}.d2'] = source
    return files


def document_slug(name: str) -> str:
    """A slug from a document name: lowercase ASCII letters and digits joined by '-', at most 120 characters
    ('' when the name has none)."""
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:120].rstrip('-')


def _output_root() -> Path:
    configured = os.environ.get('DOCUMENT_OUTPUT_ROOT', '')
    root = Path(configured)
    if not configured or not root.is_absolute():
        raise ValueError('DOCUMENT_OUTPUT_ROOT must be a mounted absolute path')
    return root.resolve(strict=True)


def export_location(slug: str, phase_metadata: dict | None = None, folder: str | None = None) -> tuple[Path, str]:
    """Folder and main file of a document's current export: [<folder>/]<slug>/README.md or services/<svc>/<phase>/<phase>.md."""
    root = _output_root()
    if phase_metadata is None:
        return (root / folder / slug if folder else root / slug), 'README.md'
    service_folder = re.sub(r'[^a-z0-9]+', '-', phase_metadata['service_name'].lower()).strip('-')[:40] or 'service'
    service_folder += '-' + str(UUID(phase_metadata['service_id']))[:8]
    phase = phase_metadata['phase']
    if phase not in ('overview', 'api', 'messaging'):
        raise ValueError('Unknown code documentation phase')
    return root / 'services' / service_folder / phase, f'{phase}.md'


def export_document(document: dict, *, phase_metadata: dict | None = None) -> str:
    """Materialize a saved version. PostgreSQL remains the source of truth."""
    root = _output_root()
    folder, main = export_location(document['slug'], phase_metadata, document.get('folder'))
    files = document_files(document['markdown'])
    files[main] = files.pop('README.md')
    if document.get('source_commits'):
        files['metadata.json'] = json.dumps({'source_commits': document['source_commits']}, indent=2) + '\n'
    if phase_metadata is not None:
        files['metadata.json'] = json.dumps(phase_metadata, ensure_ascii=False, indent=2) + '\n'
    for parent in reversed([folder, *folder.parents]):
        if parent == root or not parent.is_relative_to(root):
            continue
        if parent.is_symlink():
            raise ValueError('Document output folders cannot be symbolic links')
        parent.mkdir(exist_ok=True)
    for name, content in files.items():
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, delete=False) as temporary:
            staging = Path(temporary.name)
            try:
                temporary.write(content)
                temporary.flush()
                staging.replace(folder / name)
            finally:
                staging.unlink(missing_ok=True)
    for entry in folder.iterdir():
        if entry.is_file() and not entry.is_symlink() and entry.name not in files:
            entry.unlink()
    return str(folder)


def export_input(conn, document_id: UUID) -> dict | None:
    """Export input for a document's current version: slug, markdown, revision, source commits and phase metadata."""
    row = conn.execute('''SELECT d.slug, d.folder, d.revision, d.project_id, v.markdown, v.source_snapshot, v.generation_id
        FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s''', (document_id,)).fetchone()
    if row is None:
        return None
    phase_metadata = None
    generation_id = row['generation_id']
    if generation_id is None:  # A manual edit keeps the phase layout of the latest generated version.
        latest = conn.execute('SELECT generation_id FROM ka.document_versions WHERE document_id=%s AND generation_id IS NOT NULL ORDER BY version DESC LIMIT 1', (document_id,)).fetchone()
        generation_id = latest['generation_id'] if latest else None
    if generation_id is not None:
        job = conn.execute('SELECT result FROM ka.jobs WHERE id=%s AND project_id=%s', (generation_id, row['project_id'])).fetchone()
        if job is not None and job['result']:
            phase_metadata = job['result'].get('phase_metadata') or job['result'].get('overview_metadata')
    commits = {item['id']: item['git_commit'] for item in row['source_snapshot']['code_projects'] if item.get('git_commit')}
    if phase_metadata is not None:
        phase_metadata = {**phase_metadata, 'source_commits': commits}
    return {'slug': row['slug'], 'folder': row['folder'], 'markdown': row['markdown'], 'revision': row['revision'],
            'source_commits': commits, 'phase_metadata': phase_metadata}


def export_args(value: dict) -> dict:
    phase_metadata = value.pop('phase_metadata')
    return {'document': value, 'phase_metadata': phase_metadata}


def _real_folder(root: Path, folder: Path) -> bool:
    """A directory inside root reached without symbolic links."""
    return (folder.is_relative_to(root) and folder != root and folder.is_dir()
            and not any(p.is_symlink() for p in [folder, *folder.parents] if p.is_relative_to(root) and p != root))


def _remove_files(folder: Path, keep=lambda name: False) -> None:
    for entry in folder.iterdir():
        if entry.is_file() and not entry.is_symlink() and not keep(entry.name):
            entry.unlink()
    if not any(folder.iterdir()):
        folder.rmdir()


def remove_exported_folder(slug: str, folder: str | None = None) -> None:
    """Remove the regular files of a document's [<folder>/]<slug>/ folder; subdirectories stay, and the folder goes only if empty."""
    root = _output_root()
    parent = root / folder if folder else root
    target = parent / slug
    if target.parent == parent and _real_folder(root, target):
        _remove_files(target)


def remove_export(current: dict) -> None:
    """Remove a document's current export (see export_input); phase folders lose only the exporter's files."""
    phase_metadata = current['phase_metadata']
    if phase_metadata is None:
        remove_exported_folder(current['slug'], current.get('folder'))
        return
    root = _output_root()
    folder, main = export_location(current['slug'], phase_metadata)
    if _real_folder(root, folder):
        exporter = lambda name: name in (main, 'metadata.json') or re.fullmatch(r'architecture-\d+\.d2', name)
        _remove_files(folder, keep=lambda name: not exporter(name))


def backfill_exports(store: RepositoryStore) -> int:
    """Write the current export of every document that lacks one (e.g. after upgrading from <slug>/v<n>/ exports).

    Idempotent: documents whose main export file exists are left untouched. Failures are logged, never raised.
    """
    try:
        _output_root()
        with store.connect() as conn:
            ids = [row['id'] for row in conn.execute('SELECT id FROM ka.documents ORDER BY id').fetchall()]
    except (OSError, ValueError, psycopg.Error) as error:
        logger.warning('Skipping the document export backfill: %s', error)
        return 0
    written = 0
    for document_id in ids:
        try:
            with store.connect() as conn:
                current = export_input(conn, document_id)
            if current is None:
                continue
            folder, main = export_location(current['slug'], current['phase_metadata'], current['folder'])
            if (folder / main).is_file():
                continue
            export_document(**export_args(current))
            written += 1
        except (OSError, ValueError, KeyError, psycopg.Error) as error:
            logger.warning('Could not export document %s: %s', document_id, error)
    if written:
        logger.info('Exported the current version of %d documents', written)
    return written


def export_conflict(conn, project: UUID, slug: str, folder: str | None, document_id: UUID | None = None) -> str | None:
    """Why this document's export folder would overlap another export of GENERATED_DOCS_FOLDER, or None.

    A folderless document exports to <slug>/, a folder document to <folder>/<slug>/. At every level a document's
    export folder is never also a documentation folder or a parent of one, in either direction. Folders and slugs are
    lowercase-only, so these exact comparisons also hold on case-insensitive filesystems."""
    target = f'{folder}/{slug}' if folder else slug
    # starts_with, not LIKE: '_' is a valid folder character and must match literally.
    if conn.execute('''SELECT 1 FROM ka.documents WHERE project_id=%s AND id IS DISTINCT FROM %s
        AND (folder=%s OR starts_with(folder, %s)) LIMIT 1''', (project, document_id, target, target + '/')).fetchone():
        if folder is None:
            return f'The slug {slug} is used by a documentation folder. Choose another slug.'
        return f'The slug {slug} is used by the documentation folder {target}. Choose another slug.'
    if folder is None:
        return None
    parts = folder.split('/')
    for depth in range(1, len(parts) + 1):
        parent, name = '/'.join(parts[:depth - 1]) or None, parts[depth - 1]
        if conn.execute('''SELECT 1 FROM ka.documents WHERE project_id=%s AND folder IS NOT DISTINCT FROM %s AND slug=%s
            AND id IS DISTINCT FROM %s LIMIT 1''', (project, parent, name, document_id)).fetchone():
            if parent is None:
                return f'The folder {name} holds the export of the document {name}. Choose another folder.'
            return (f'The folder {parent}/{name} holds the export of the document {name} in the folder {parent}. '
                    'Choose another folder.')
    return None


def save_document(conn, project: UUID, actor: UUID | None, value: DocumentInput, document_id: UUID | None = None,
                  folder: str | None = None) -> dict:
    """Save a complete immutable version within the caller's transaction.

    `folder` places a new document in a documentation folder; an existing document keeps its own folder."""
    # Serialize saves per project so the folder / folder-less export check below cannot race another save.
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (f'{project}:document-export',))
    if document_id is None:
        if value.revision is not None:
            raise HTTPException(400, 'New documents cannot reuse a revision')
        document_id = uuid4()
        revision,base = 1,None
    else:
        previous = conn.execute('SELECT revision,current_version_id,folder FROM ka.documents WHERE id=%s AND project_id=%s FOR UPDATE', (document_id,project)).fetchone()
        if previous is None:
            raise HTTPException(404, 'Document not found')
        if previous['revision'] != value.revision:
            raise HTTPException(409, 'This document changed. Reload it before saving; your text has not been overwritten.')
        revision,base = previous['revision']+1,previous['current_version_id']
        folder = previous['folder']
    conflict = export_conflict(conn, project, value.slug, folder, document_id)
    if conflict:
        raise HTTPException(409, conflict)
    # Select only public inventory columns; credentials never enter document history.
    sources = {
        'environments': conn.execute('SELECT id,name FROM ka.environments WHERE project_id=%s AND id=ANY(%s) ORDER BY id FOR SHARE', (project,value.environment_ids)).fetchall(),
        'code_projects': conn.execute('SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL AND id=ANY(%s) ORDER BY id FOR SHARE', (project,value.code_project_ids)).fetchall(),
        'services': conn.execute('SELECT id,name FROM ka.services WHERE project_id=%s AND id=ANY(%s) ORDER BY id FOR SHARE', (project,value.service_ids)).fetchall(),
        'servers': conn.execute('SELECT id,name,hostname FROM ka.servers WHERE project_id=%s AND id=ANY(%s) ORDER BY id FOR SHARE', (project,value.server_ids)).fetchall(),
        'deployments': conn.execute('''SELECT d.id,d.service_id,d.server_id,COALESCE(e.name,d.environment) AS environment,d.runtime_name,
            s.name || ' / ' || h.name || ' / ' || COALESCE(e.name,d.environment) AS name
            FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id JOIN ka.servers h ON h.id=d.server_id
            LEFT JOIN ka.environments e ON e.id=d.environment_id
            WHERE d.project_id=%s AND d.id=ANY(%s) ORDER BY d.id FOR SHARE OF d''', (project,value.deployment_ids)).fetchall(),
    }
    if any(len(sources[key]) != len(ids) for key,ids in (
        ('code_projects',value.code_project_ids), ('services',value.service_ids),
        ('servers',value.server_ids), ('deployments',value.deployment_ids), ('environments',value.environment_ids))):
        raise HTTPException(400, 'A selected resource is no longer available. Reload resources and update the selection.')
    if any(row['service_id'] not in value.service_ids or row['server_id'] not in value.server_ids for row in sources['deployments']):
        raise HTTPException(400, 'Select the service and server belonging to each selected deployment')
    snapshot = {key:[{k:str(v) if isinstance(v,UUID) else v for k,v in row.items()} for row in rows] for key,rows in sources.items()}
    if value.source_commits is not None:
        if not set(value.source_commits) <= set(value.code_project_ids):
            raise HTTPException(400, 'Source commits must belong to the selected code projects')
        source_commits = {str(key): commit for key, commit in value.source_commits.items()}
    elif base is not None:
        previous_sources = conn.execute('SELECT source_snapshot FROM ka.document_versions WHERE id=%s', (base,)).fetchone()['source_snapshot']
        source_commits = {row['id']: row['git_commit'] for row in previous_sources['code_projects'] if row.get('git_commit')}
    else:
        source_commits = {}
    for row in snapshot['code_projects']:
        if row['id'] in source_commits:
            row['git_commit'] = source_commits[row['id']]
    version_id = uuid4()
    conn.execute('''INSERT INTO ka.documents(id,project_id,slug,folder,current_version_id,revision) VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (id) DO UPDATE SET slug=EXCLUDED.slug,current_version_id=EXCLUDED.current_version_id,revision=EXCLUDED.revision''',
        (document_id,project,value.slug,folder,version_id,revision))
    conn.execute('''INSERT INTO ka.document_versions(id,document_id,project_id,version,title,description,purpose,audience,
        tags,markdown,content_hash,source_snapshot,base_version_id,origin,created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (version_id,document_id,project,revision,value.title,value.description,value.purpose,value.audience,
         value.tags,value.markdown,hashlib.sha256(value.markdown.encode('utf-8')).hexdigest(),Jsonb(snapshot),base,'manual',actor))
    # Fixed relation names, never identifiers supplied by an HTTP request.
    for table,column,ids in (
        ('document_code_projects','code_project_id',value.code_project_ids),
        ('document_services','service_id',value.service_ids),
        ('document_servers','server_id',value.server_ids),
        ('document_deployments','deployment_id',value.deployment_ids),
        ('document_environments','environment_id',value.environment_ids)):
        conn.execute(f'DELETE FROM ka.{table} WHERE document_id=%s', (document_id,))
        for resource_id in ids:
            conn.execute(f'INSERT INTO ka.{table}(document_id,project_id,{column}) VALUES (%s,%s,%s)', (document_id,project,resource_id))
    conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
        VALUES (%s,%s,%s,'document',%s,%s)''', (project,actor,'document.created' if base is None else 'document.updated',
        document_id,Jsonb({'version':revision,'version_id':str(version_id)})))
    return {'id':document_id, 'revision':revision, 'version_id':version_id}


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/documentation')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Document storage is not configured')
        return store

    @router.get('/resources')
    def resources() -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            return {
                'environments': conn.execute('SELECT id,name FROM ka.environments WHERE project_id=%s ORDER BY name', (project,)).fetchall(),
                'code_projects': conn.execute('SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL ORDER BY lower(name),id', (project,)).fetchall(),
                'services': conn.execute('SELECT id,name FROM ka.services WHERE project_id=%s ORDER BY lower(name),id', (project,)).fetchall(),
                'servers': conn.execute('SELECT id,name,hostname FROM ka.servers WHERE project_id=%s ORDER BY lower(name),id', (project,)).fetchall(),
                'deployments': conn.execute('''SELECT d.id,d.service_id,d.server_id,COALESCE(e.name,d.environment) AS environment,d.runtime_name,
                    s.name || ' / ' || h.name || ' / ' || COALESCE(e.name,d.environment) ||
                    CASE WHEN d.runtime_name='' THEN '' ELSE ' / ' || d.runtime_name END AS name
                    FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id
                    JOIN ka.servers h ON h.id=d.server_id LEFT JOIN ka.environments e ON e.id=d.environment_id
                    WHERE d.project_id=%s ORDER BY name,d.id''', (project,)).fetchall(),
            }

    @router.get('/documents')
    def list_documents(q: str = Query(default='', max_length=200), code_project_id: UUID | None = None,
                       service_id: UUID | None = None, server_id: UUID | None = None,
                       deployment_id: UUID | None = None, environment_id: UUID | None = None, offset: int = Query(default=0, ge=0)) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            where = '''project_id=%s AND (strpos(lower(title || ' ' || description || ' ' || slug),lower(%s))>0)
                AND (%s::uuid IS NULL OR %s=ANY(code_project_ids))
                AND (%s::uuid IS NULL OR %s=ANY(service_ids))
                AND (%s::uuid IS NULL OR %s=ANY(server_ids))
                AND (%s::uuid IS NULL OR %s=ANY(deployment_ids))
                AND (%s::uuid IS NULL OR %s=ANY(environment_ids))'''
            params = (project,q.strip(),code_project_id,code_project_id,service_id,service_id,server_id,server_id,deployment_id,deployment_id,environment_id,environment_id)
            rows = conn.execute(f'SELECT * FROM ka.document_details WHERE {where} ORDER BY updated_at DESC,id LIMIT 50 OFFSET %s', (*params,offset)).fetchall()
            total = conn.execute(f'SELECT count(*) AS n FROM ka.document_details WHERE {where}', params).fetchone()['n']
            return {'documents': rows, 'total': total}

    @router.get('/documents/{document_id}')
    def get_document(document_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            row = conn.execute('SELECT * FROM ka.document_details WHERE id=%s AND project_id=%s', (document_id,project)).fetchone()
            if row is None:
                raise HTTPException(404, 'Document not found')
            row['current_version'] = conn.execute('SELECT * FROM ka.document_versions WHERE id=%s', (row['current_version_id'],)).fetchone()
            row['versions'] = conn.execute('''SELECT id,version,title,origin,base_version_id,content_hash,created_at
                FROM ka.document_versions WHERE document_id=%s ORDER BY version DESC''', (document_id,)).fetchall()
            return row

    @router.get('/documents/{document_id}/bundle')
    def bundle(document_id: UUID) -> Response:
        document = get_document(document_id)
        markdown = document['current_version']['markdown']
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
            folder = document['slug']
            for name, content in document_files(markdown).items():
                archive.writestr(f'{folder}/{name}', content)
        return Response(data.getvalue(), media_type='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="{document["slug"]}.zip"'})

    @router.post('/documents/{document_id}/export')
    def export(document_id: UUID, request: Request) -> dict:
        request_actor(request)
        document = get_document(document_id)
        try:
            with database().connect() as conn:
                folder = export_document(**export_args(export_input(conn, document['id'])))
        except (ValueError, OSError):
            raise HTTPException(503, 'Could not export the document. Check the DOCUMENT_OUTPUT_ROOT mount and write permissions.') from None
        return {'folder': folder}

    @router.get('/documents/{document_id}/versions/{version_id}')
    def get_version(document_id: UUID, version_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            row = conn.execute('SELECT * FROM ka.document_versions WHERE id=%s AND document_id=%s AND project_id=%s', (version_id,document_id,project)).fetchone()
            if row is None:
                raise HTTPException(404, 'Document version not found')
            return row

    def save(value: DocumentInput, request: Request, document_id: UUID | None) -> dict:
        db = database()
        subject,email = request_actor(request)
        try:
            with db.connect() as conn:
                project = db.project(conn, app_name)
                actor = db.actor(conn,subject,email)
                old_slug = old_folder = None
                if document_id is not None:
                    old = conn.execute('SELECT slug,folder FROM ka.documents WHERE id=%s AND project_id=%s', (document_id, project)).fetchone()
                    if old:
                        old_slug, old_folder = old['slug'], old['folder']
                saved = save_document(conn,project,actor,value,document_id)
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'A document with this slug already exists') from None
        try:
            with db.connect() as conn:
                current = export_input(conn, saved['id'])
            if current is not None:
                phase = current['phase_metadata'] is not None
                export_document(**export_args(current))
                if old_slug is not None and old_slug != current['slug'] and not phase:
                    remove_exported_folder(old_slug, old_folder)
        except (OSError, ValueError):
            saved = {**saved, 'export_error': 'Saved. Writing the document file failed; use Export to folder to retry.'}
        return saved

    @router.post('/documents', status_code=201)
    def create_document(value: DocumentInput, request: Request) -> dict:
        return save(value,request,None)

    @router.put('/documents/{document_id}')
    def update_document(document_id: UUID, value: DocumentInput, request: Request) -> dict:
        return save(value,request,document_id)

    @router.delete('/documents/{document_id}')
    def delete_document(document_id: UUID, request: Request, revision: int = Query(ge=1), confirmed: bool = False) -> dict:
        subject,email = request_actor(request)
        if not confirmed:
            raise HTTPException(400, 'Confirm document deletion before continuing')
        db = database()
        with db.connect() as conn:
            project = db.project(conn,app_name)
            row = conn.execute('SELECT revision FROM ka.documents WHERE id=%s AND project_id=%s FOR UPDATE', (document_id,project)).fetchone()
            if row is None:
                raise HTTPException(404, 'Document not found')
            if row['revision'] != revision:
                raise HTTPException(409, 'This document changed. Cancel, reload, and try again.')
            actor = db.actor(conn,subject,email)
            current = export_input(conn, document_id)
            conn.execute('DELETE FROM ka.documents WHERE id=%s', (document_id,))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'document.deleted','document',%s,%s)''', (project,actor,document_id,Jsonb({'revision':revision})))
        # After the commit: the export goes so a Refresh drops it from search; a failure never fails the delete.
        if current is not None:
            try:
                remove_export(current)
            except (OSError, ValueError) as error:
                logger.warning('Could not remove the export of deleted document %s: %s', document_id, error)
        return {'id':document_id}

    return router
