"""Admin-only repository management routes."""

import os
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request

from knowledge_admin.repositories import RepositoryInput, RepositoryStore
from knowledge_admin.access import request_actor
from knowledge_admin.code_paths import code_path, code_root


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def repository_store() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, "Repository storage is not configured")
        return store

    @router.get("/repositories")
    def list_repositories():
        return {"repositories": repository_store().list(app_name)}

    @router.get('/code-projects')
    def list_code_projects():
        return {'projects': repository_store().list(app_name)}

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
        return {"id": repository_id}

    @router.post('/code-projects/{repository_id}/pull', status_code=202)
    @router.post("/repositories/{repository_id}/pull", status_code=202)
    def pull_repository(repository_id: UUID, request: Request):
        job_id = repository_store().enqueue(app_name, repository_id, *request_actor(request))
        return {"job_id": job_id}

    return router
