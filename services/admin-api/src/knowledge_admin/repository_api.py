"""Admin-only repository management routes."""

import json
import os
import sqlite3
from uuid import UUID, uuid4

import httpx
import psycopg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.repositories import RepositoryInput, RepositoryStore
from knowledge_admin.access import request_actor
from knowledge_admin.code_paths import code_path, code_root
from knowledge_admin.code_project_search import default_collection, reconcile
from knowledge_admin.code_processing import index_directory, CodeProcessingError


class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    enabled: bool
    collection: str | None = Field(default=None, max_length=60, pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')


def build_api(app_name: str, store: RepositoryStore | None, backoffice_client: httpx.Client | None = None) -> APIRouter:
    router = APIRouter()

    def repository_store() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, "Repository storage is not configured")
        return store

    def refresh_search(repository_id: UUID, request: Request, *, release: bool = False) -> str | None:
        selected = repository_store()
        with selected.connect() as conn:
            actor = selected.actor(conn, *request_actor(request))
        return reconcile(selected, app_name, backoffice_client, repository_id, actor, release=release)

    @router.get("/repositories")
    def list_repositories():
        return {"repositories": repository_store().list(app_name)}

    @router.get('/code-projects')
    def list_code_projects():
        projects = repository_store().list(app_name)
        with repository_store().connect() as conn:
            for project in projects:
                project['processing'] = conn.execute('''SELECT status,error,result FROM ka.jobs
                    WHERE repository_id=%s AND kind='code_index' ORDER BY created_at DESC,id DESC LIMIT 1''', (project['id'],)).fetchone()
                project['code_index'] = None
                try:
                    manifest = index_directory(project['id']) / 'manifest.json'
                    if manifest.is_file():
                        data = json.loads(manifest.read_text())
                        project['code_index'] = {key: data[key] for key in ('status','source_hash','source_file_count',
                            'stats','unresolved_references','parse_error_files','processed_at','engine_version','missing_folders')}
                except (CodeProcessingError, OSError, ValueError):
                    pass
        return {'projects': projects}

    @router.post('/code-projects/{repository_id}/process', status_code=202)
    def process_project(repository_id: UUID, request: Request, workflow_id: UUID | None = None):
        selected = repository_store()
        with selected.connect() as conn:
            project = selected.project(conn, app_name)
            repo = conn.execute('SELECT id FROM ka.repositories WHERE id=%s AND project_id=%s AND archived_at IS NULL FOR UPDATE',
                (repository_id,project)).fetchone()
            if repo is None:
                raise HTTPException(404, 'Code project not found')
            if workflow_id is not None and conn.execute('''SELECT 1 FROM ka.documentation_workflows w
                JOIN ka.service_repositories r ON r.service_id=w.service_id
                WHERE w.id=%s AND w.project_id=%s AND r.repository_id=%s''', (workflow_id,project,repository_id)).fetchone() is None:
                raise HTTPException(400, 'Select a code project linked to this workflow service')
            # Pull and processing jobs share the existing per-project queue lock.
            live = conn.execute("SELECT id,kind FROM ka.jobs WHERE repository_id=%s AND status IN ('queued','running','waiting_input')", (repository_id,)).fetchone()
            if live is not None:
                if live['kind'] != 'code_index':
                    raise HTTPException(409, 'Wait for the Git pull to finish, then generate the code graph.')
                job = live['id']
            else:
                job = uuid4()
            actor = selected.actor(conn, *request_actor(request))
            if live is None:
                conn.execute("INSERT INTO ka.jobs(id,project_id,repository_id,kind,requested_by,idempotency_key) VALUES (%s,%s,%s,'code_index',%s,%s)",
                    (job,project,repository_id,actor,str(job)))
            if workflow_id is not None:
                conn.execute('INSERT INTO ka.documentation_workflow_jobs(workflow_id,job_id,project_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', (workflow_id,job,project))
                conn.execute('UPDATE ka.documentation_workflows SET updated_at=now() WHERE id=%s', (workflow_id,))
            return {'job_id': job}

    @router.put('/code-projects/{repository_id}/search')
    def set_search(repository_id: UUID, value: SearchInput, request: Request):
        """Searchable on adds every mapped folder to the collection (default: the project's slug); off removes them."""
        selected = repository_store()
        with selected.connect() as conn:
            project = selected.project(conn, app_name)
            repo = conn.execute('''SELECT id,name FROM ka.repositories WHERE id=%s AND project_id=%s AND archived_at IS NULL
                FOR UPDATE''', (repository_id, project)).fetchone()
            if repo is None:
                raise HTTPException(404, 'Code project not found')
            collection = (value.collection or default_collection(repo['name'], repository_id)) if value.enabled else None
            conn.execute('UPDATE ka.repositories SET search_collection=%s,search_error=NULL WHERE id=%s', (collection, repository_id))
        error = refresh_search(repository_id, request, release=True)
        return {'searchable': collection is not None, 'collection': collection, 'search_error': error}

    @router.post('/code-projects/{repository_id}/archive')
    def archive_project(repository_id: UUID, request: Request):
        """Hide the project everywhere; a searchable project's folders leave every collection."""
        selected = repository_store()
        with selected.connect() as conn:
            project = selected.project(conn, app_name)
            repo = conn.execute('''SELECT id,search_collection FROM ka.repositories WHERE id=%s AND project_id=%s
                AND archived_at IS NULL FOR UPDATE''', (repository_id, project)).fetchone()
            if repo is None:
                raise HTTPException(404, 'Code project not found')
            selected.require_idle(conn, repository_id)
            conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (repository_id,))
        error = refresh_search(repository_id, request, release=repo['search_collection'] is not None)
        return {'id': repository_id, 'search_error': error}

    @router.get('/code-projects/{repository_id}/graph')
    def view_code_graph(repository_id: UUID, file: str = Query(default='', max_length=1000),
                        offset: int = Query(default=0, ge=0), source_hash: str | None = Query(default=None, pattern='^[a-f0-9]{64}$')):
        selected = repository_store()
        with selected.connect() as conn:
            project = selected.project(conn, app_name)
            if conn.execute('SELECT id FROM ka.repositories WHERE id=%s AND project_id=%s AND archived_at IS NULL', (repository_id,project)).fetchone() is None:
                raise HTTPException(404, 'Code project not found')
        try:
            directory = index_directory(repository_id)
            summary_path = directory / source_hash / 'summary.json' if source_hash else directory / 'manifest.json'
            summary = json.loads(summary_path.read_text())
        except (CodeProcessingError, OSError, ValueError):
            raise HTTPException(404, 'Generate the code graph first.') from None
        with sqlite3.connect((directory / summary['source_hash'] / 'graph.sqlite').as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            if file:
                nodes = db.execute('''SELECT id,name AS label,kind,file_path,start_line,end_line FROM nodes
                    WHERE file_path=? AND kind<>'file' ORDER BY start_line,id LIMIT 201 OFFSET ?''', (file,offset)).fetchall()
                total = db.execute("SELECT count(*) FROM nodes WHERE file_path=? AND kind<>'file'", (file,)).fetchone()[0]
            else:
                nodes = db.execute('''SELECT path AS id,path AS label,'file' AS kind,path AS file_path,1 AS start_line,1 AS end_line
                    FROM files ORDER BY path LIMIT 201 OFFSET ?''', (offset,)).fetchall()
                total = db.execute('SELECT count(*) FROM files').fetchone()[0]
            more = len(nodes) > 200
            nodes = [dict(row) for row in nodes[:200]]
            ids = [node['id'] for node in nodes]
            marks = ','.join('?' for _ in ids)
            if file:
                edges = db.execute(f'''SELECT source,target,kind AS label,count(*) AS count FROM edges
                    WHERE source IN ({marks}) AND target IN ({marks}) AND kind<>'contains'
                    GROUP BY source,target,kind ORDER BY source,target,kind LIMIT 1501''', [*ids,*ids]).fetchall() if ids else []
            else:
                edges = db.execute(f'''SELECT a.file_path AS source,b.file_path AS target,e.kind AS label,count(*) AS count
                    FROM edges e JOIN nodes a ON a.id=e.source JOIN nodes b ON b.id=e.target
                    WHERE a.file_path<>b.file_path AND e.kind<>'contains' AND a.file_path IN ({marks}) AND b.file_path IN ({marks})
                    GROUP BY a.file_path,b.file_path,e.kind ORDER BY a.file_path,b.file_path,e.kind LIMIT 1501''', [*ids,*ids]).fetchall() if ids else []
        return {'nodes': nodes, 'edges': [dict(edge) for edge in edges[:1500]], 'total': total,
            'next_offset': offset+200 if more else None, 'edges_truncated': len(edges)>1500,
            'source_hash': summary['source_hash'], 'file': file,
            'summary': {k: v for k, v in summary.items() if k not in ('files','graph_path')}}

    @router.get('/code/files')
    def list_code_files(path: str = Query(default='', max_length=1000), offset: int = Query(default=0, ge=0), folders_only: bool = False):
        root = code_root()
        directory = code_path(root, path)
        if not directory.is_dir():
            raise HTTPException(404, 'Code folder not found.')
        try:
            with os.scandir(directory) as iterator:
                entries = sorted((e for e in iterator if not folders_only or
                    (e.is_dir(follow_symlinks=False) and e.name not in ('.git', '.ka-sync'))),
                    key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.casefold(), e.name))
            files = [{'name': e.name, 'path': f'{path}/{e.name}' if path else e.name,
                'kind': 'symlink' if e.is_symlink() else 'folder' if e.is_dir(follow_symlinks=False) else 'file' if e.is_file(follow_symlinks=False) else 'other',
                'size': e.stat(follow_symlinks=False).st_size} for e in entries[offset:offset+200]]
        except OSError:
            raise HTTPException(503, 'Could not read this code folder. Check mount permissions and refresh.') from None
        return {'root': str(root), 'host_path': os.environ.get('CODE_PATH'), 'path': path, 'entries': files,
            'total': len(entries), 'next_offset': offset+200 if offset+200<len(entries) else None}

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
    @router.post("/repositories", status_code=201)
    def add_repository(value: RepositoryInput, request: Request):
        if request.url.path.endswith('/code-projects') and value.folder_paths is None:
            raise HTTPException(400, 'Configure at least one code project folder.')
        try:
            repository_id = repository_store().save(app_name, value, *request_actor(request))
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "This Git source or clone folder has already been added") from None
        return {"id": repository_id}

    @router.put('/code-projects/{repository_id}')
    @router.put("/repositories/{repository_id}")
    def edit_repository(repository_id: UUID, value: RepositoryInput, request: Request):
        if '/code-projects/' in request.url.path and (value.revision is None or value.folder_paths is None):
            raise HTTPException(400, 'Include the current revision and code project folders.')
        repository_store().save(app_name, value, *request_actor(request), repository_id=repository_id)
        return {"id": repository_id, "search_error": refresh_search(repository_id, request)}

    @router.post('/code-projects/{repository_id}/pull', status_code=202)
    @router.post("/repositories/{repository_id}/pull", status_code=202)
    def pull_repository(repository_id: UUID, request: Request):
        job_id = repository_store().enqueue(app_name, repository_id, *request_actor(request))
        return {"job_id": job_id}

    return router
