"""Admin-only repository management routes."""

from functools import partial
import json
import os
import shutil
import sqlite3
from uuid import UUID, uuid4

import httpx
import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from knowledge_admin.db import storage, workspace
from knowledge_admin.repositories import RepositoryInput, RepositoryStore
from knowledge_admin.access import request_actor
from knowledge_admin.data_root import host_path
from knowledge_admin.code_projects.paths import code_path, code_root
from knowledge_admin.code_projects.processing import index_directory, CodeProcessingError
from knowledge_admin.rag.files import RagFiles


class RemoveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    delete_files: bool = False


def overlaps(a: str, b: str) -> bool:
    """One path is the other, or contains it."""
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


def build_api(app_name: str, store: RepositoryStore | None, backoffice_client: httpx.Client | None = None) -> APIRouter:
    router = APIRouter()

    repository_store = partial(storage, store, 'Repository storage', message='Repository storage is not configured')

    @router.get('/repositories')
    def list_repositories():
        return {'repositories': repository_store().list(app_name)}

    @router.get('/code-projects')
    def list_code_projects():
        projects = repository_store().list(app_name)
        with repository_store().connect() as conn:
            for project in projects:
                project['processing'] = conn.execute(
                    """SELECT status,error,result FROM ka.jobs
                    WHERE repository_id=%s AND kind='code_index' ORDER BY created_at DESC,id DESC LIMIT 1""",
                    (project['id'],),
                ).fetchone()
                project['code_index'] = None
                try:
                    manifest = index_directory(project['id']) / 'manifest.json'
                    if manifest.is_file():
                        data = json.loads(manifest.read_text())
                        project['code_index'] = {
                            key: data[key]
                            for key in (
                                'status',
                                'source_hash',
                                'source_file_count',
                                'stats',
                                'unresolved_references',
                                'parse_error_files',
                                'processed_at',
                                'engine_version',
                                'missing_folders',
                            )
                        }
                except (CodeProcessingError, OSError, ValueError):
                    pass
        return {'projects': projects}

    @router.post('/code-projects/{repository_id}/process', status_code=202)
    def process_project(repository_id: UUID, request: Request):
        selected = repository_store()
        with workspace(selected, app_name) as (conn, project):
            repo = conn.execute(
                'SELECT id FROM ka.repositories WHERE id=%s AND project_id=%s AND archived_at IS NULL FOR UPDATE',
                (repository_id, project),
            ).fetchone()
            if repo is None:
                raise HTTPException(404, 'Code project not found')
            # Pull and processing jobs share the existing per-project queue lock.
            live = conn.execute(
                "SELECT id,kind FROM ka.jobs WHERE repository_id=%s AND status IN ('queued','running','waiting_input')",
                (repository_id,),
            ).fetchone()
            if live is not None:
                if live['kind'] != 'code_index':
                    raise HTTPException(409, 'Wait for the Git pull to finish, then generate the code graph.')
                job = live['id']
            else:
                job = uuid4()
            actor = selected.actor(conn, *request_actor(request))
            if live is None:
                conn.execute(
                    "INSERT INTO ka.jobs(id,project_id,repository_id,kind,requested_by,idempotency_key) VALUES (%s,%s,%s,'code_index',%s,%s)",
                    (job, project, repository_id, actor, str(job)),
                )
            return {'job_id': job}

    def rag_paths(project: UUID) -> list[tuple[str, str]]:
        """(collection, source_path) for every folder connected in RAG."""
        key = os.environ.get('RAG_BACKOFFICE_KEY')
        if backoffice_client is None or not key:
            raise HTTPException(
                503, 'RAG is not configured, so connected folders cannot be checked. Keep the files instead.'
            )
        listing = RagFiles(backoffice_client, key, project, None).request(
            'GET', 'collections', params={'status': 'false'}
        )
        return [(c['name'], p['source_path']) for c in listing.get('collections', []) for p in c.get('paths', [])]

    @router.post('/code-projects/{repository_id}/archive')
    def archive_project(repository_id: UUID, value: RemoveInput | None = None):
        """Remove the project everywhere and release its folders. With delete_files, also delete its folders
        under code/ and its code graph, unless another project maps them or a RAG collection indexes them."""
        value = value or RemoveInput()
        selected = repository_store()
        connected: list[tuple[str, str]] = []
        if value.delete_files:
            root = code_root()
            with selected.connect() as conn:
                project = selected.project(conn, app_name)
            connected = rag_paths(project)
        with workspace(selected, app_name) as (conn, project):
            repo = conn.execute(
                """SELECT id,clone_path FROM ka.repositories WHERE id=%s AND project_id=%s AND archived_at IS NULL
                FOR UPDATE""",
                (repository_id, project),
            ).fetchone()
            if repo is None:
                raise HTTPException(404, 'Code project not found')
            selected.require_idle(conn, repository_id)
            folders = {
                row['path']
                for row in conn.execute(
                    'SELECT path FROM ka.code_project_folders WHERE code_project_id=%s', (repository_id,)
                )
            }
            if repo['clone_path']:
                folders.add(repo['clone_path'])
            folders = sorted(folders)
            if value.delete_files:
                others = conn.execute(
                    """SELECT r.name,f.path FROM ka.code_project_folders f
                    JOIN ka.repositories r ON r.id=f.code_project_id
                    WHERE f.project_id=%s AND f.code_project_id<>%s AND r.archived_at IS NULL ORDER BY r.name,f.path""",
                    (project, repository_id),
                ).fetchall()
                for folder in folders:
                    for other in others:
                        if overlaps(folder, other['path']):
                            raise HTTPException(
                                409,
                                f'code/{folder} is also used by code project {other["name"]} '
                                f'(code/{other["path"]}). Nothing was removed; keep the files or unmap it there first.',
                            )
                    for collection, source in connected:
                        if overlaps(f'code/{folder}', source):
                            raise HTTPException(
                                409,
                                f'RAG collection {collection} indexes {source}. Nothing was removed; '
                                'remove that folder in RAG → Collections first or keep the files.',
                            )
            conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (repository_id,))
            conn.execute('DELETE FROM ka.code_project_folders WHERE code_project_id=%s', (repository_id,))
        deleted: list[str] = []
        errors: list[str] = []
        if value.delete_files:
            targets = []
            for folder in folders:
                try:
                    targets.append((f'code/{folder}', code_path(root, folder)))
                except HTTPException as error:
                    errors.append(f'code/{folder} could not be deleted: {error.detail}')
            try:
                targets.append((f'code-index/{repository_id}', index_directory(repository_id)))
            except CodeProcessingError as error:
                errors.append(f'code-index/{repository_id} could not be deleted: {error}')
            for label, target in targets:
                if not target.is_dir() or target.is_symlink():
                    continue
                try:
                    shutil.rmtree(target)
                    deleted.append(label)
                except OSError as error:
                    errors.append(f'{label} could not be deleted: {error.strerror or error}')
        return {'id': repository_id, 'deleted': deleted, 'errors': errors}

    @router.get('/code-projects/{repository_id}/graph')
    def view_code_graph(
        repository_id: UUID,
        file: str = Query(default='', max_length=1000),
        offset: int = Query(default=0, ge=0),
        source_hash: str | None = Query(default=None, pattern='^[a-f0-9]{64}$'),
    ):
        selected = repository_store()
        with workspace(selected, app_name) as (conn, project):
            if (
                conn.execute(
                    'SELECT id FROM ka.repositories WHERE id=%s AND project_id=%s AND archived_at IS NULL',
                    (repository_id, project),
                ).fetchone()
                is None
            ):
                raise HTTPException(404, 'Code project not found')
        try:
            directory = index_directory(repository_id)
            summary_path = directory / source_hash / 'summary.json' if source_hash else directory / 'manifest.json'
            summary = json.loads(summary_path.read_text())
        except (CodeProcessingError, OSError, ValueError):
            raise HTTPException(404, 'Generate the code graph first.') from None
        with sqlite3.connect(
            (directory / summary['source_hash'] / 'graph.sqlite').as_uri() + '?mode=ro', uri=True
        ) as db:
            db.row_factory = sqlite3.Row
            if file:
                nodes = db.execute(
                    """SELECT id,name AS label,kind,file_path,start_line,end_line FROM nodes
                    WHERE file_path=? AND kind<>'file' ORDER BY start_line,id LIMIT 201 OFFSET ?""",
                    (file, offset),
                ).fetchall()
                total = db.execute("SELECT count(*) FROM nodes WHERE file_path=? AND kind<>'file'", (file,)).fetchone()[
                    0
                ]
            else:
                nodes = db.execute(
                    """SELECT path AS id,path AS label,'file' AS kind,path AS file_path,1 AS start_line,1 AS end_line
                    FROM files ORDER BY path LIMIT 201 OFFSET ?""",
                    (offset,),
                ).fetchall()
                total = db.execute('SELECT count(*) FROM files').fetchone()[0]
            more = len(nodes) > 200
            nodes = [dict(row) for row in nodes[:200]]
            ids = [node['id'] for node in nodes]
            marks = ','.join('?' for _ in ids)
            if file:
                edges = (
                    db.execute(
                        f"""SELECT source,target,kind AS label,count(*) AS count FROM edges
                    WHERE source IN ({marks}) AND target IN ({marks}) AND kind<>'contains'
                    GROUP BY source,target,kind ORDER BY source,target,kind LIMIT 1501""",
                        [*ids, *ids],
                    ).fetchall()
                    if ids
                    else []
                )
            else:
                edges = (
                    db.execute(
                        f"""SELECT a.file_path AS source,b.file_path AS target,e.kind AS label,count(*) AS count
                    FROM edges e JOIN nodes a ON a.id=e.source JOIN nodes b ON b.id=e.target
                    WHERE a.file_path<>b.file_path AND e.kind<>'contains' AND a.file_path IN ({marks}) AND b.file_path IN ({marks})
                    GROUP BY a.file_path,b.file_path,e.kind ORDER BY a.file_path,b.file_path,e.kind LIMIT 1501""",
                        [*ids, *ids],
                    ).fetchall()
                    if ids
                    else []
                )
        return {
            'nodes': nodes,
            'edges': [dict(edge) for edge in edges[:1500]],
            'total': total,
            'next_offset': offset + 200 if more else None,
            'edges_truncated': len(edges) > 1500,
            'source_hash': summary['source_hash'],
            'file': file,
            'summary': {k: v for k, v in summary.items() if k not in ('files', 'graph_path')},
        }

    @router.get('/code/files')
    def list_code_files(
        path: str = Query(default='', max_length=1000), offset: int = Query(default=0, ge=0), folders_only: bool = False
    ):
        root = code_root()
        directory = code_path(root, path)
        if not directory.is_dir():
            raise HTTPException(404, 'Code folder not found.')
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    (
                        e
                        for e in iterator
                        if not folders_only or (e.is_dir(follow_symlinks=False) and e.name not in ('.git', '.ka-sync'))
                    ),
                    key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.casefold(), e.name),
                )
            files = [
                {
                    'name': e.name,
                    'path': f'{path}/{e.name}' if path else e.name,
                    'kind': 'symlink'
                    if e.is_symlink()
                    else 'folder'
                    if e.is_dir(follow_symlinks=False)
                    else 'file'
                    if e.is_file(follow_symlinks=False)
                    else 'other',
                    'size': e.stat(follow_symlinks=False).st_size,
                }
                for e in entries[offset : offset + 200]
            ]
        except OSError:
            raise HTTPException(503, 'Could not read this code folder. Check mount permissions and refresh.') from None
        return {
            'root': str(root),
            'host_path': host_path(str(root)),
            'path': path,
            'entries': files,
            'total': len(entries),
            'next_offset': offset + 200 if offset + 200 < len(entries) else None,
        }

    @router.get('/code/file')
    def read_code_file(path: str = Query(max_length=1000)):
        target = code_path(code_root(), path)
        if not target.is_file():
            raise HTTPException(404, 'Code file not found.')
        try:
            with target.open('rb') as source:
                data = source.read(512_001)
        except OSError:
            raise HTTPException(503, 'Could not read this code file.') from None
        if len(data) > 512_000:
            raise HTTPException(413, 'File preview is limited to 512 KB.')
        try:
            if b'\x00' in data:
                raise ValueError('binary')
            text = data.decode('utf-8')
        except ValueError:
            raise HTTPException(415, 'Preview is available for UTF-8 text files only.') from None
        return {'path': path, 'text': text}

    @router.post('/code-projects', status_code=201)
    @router.post('/repositories', status_code=201)
    def add_repository(value: RepositoryInput, request: Request):
        if request.url.path.endswith('/code-projects') and value.folder_paths is None:
            raise HTTPException(400, 'Configure at least one code project folder.')
        try:
            repository_id = repository_store().save(app_name, value, *request_actor(request))
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'This Git source or clone folder has already been added') from None
        return {'id': repository_id}

    @router.put('/code-projects/{repository_id}')
    @router.put('/repositories/{repository_id}')
    def edit_repository(repository_id: UUID, value: RepositoryInput, request: Request):
        if '/code-projects/' in request.url.path and (value.revision is None or value.folder_paths is None):
            raise HTTPException(400, 'Include the current revision and code project folders.')
        repository_store().save(app_name, value, *request_actor(request), repository_id=repository_id)
        return {'id': repository_id}

    @router.post('/code-projects/{repository_id}/pull', status_code=202)
    @router.post('/repositories/{repository_id}/pull', status_code=202)
    def pull_repository(repository_id: UUID, request: Request):
        job_id = repository_store().enqueue(app_name, repository_id, *request_actor(request))
        return {'job_id': job_id}

    return router
