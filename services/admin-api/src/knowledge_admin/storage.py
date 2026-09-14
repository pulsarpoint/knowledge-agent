"""Read-only overview of the deployment's configured content folders."""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

StorageArea = Literal['code', 'rag', 'documents', 'bootstrap']
ROOTS = {
    'code': ('CODE_ROOT', 'CODE_PATH'),
    'rag': ('RAG_SOURCE_ROOT', 'RG_DATA'),
    'documents': ('DOCUMENT_OUTPUT_ROOT', 'GENERATED_DOCS_PATH'),
    'bootstrap': ('BOOTSTRAP_DOCS_ROOT', 'DOCS_PATH'),
}


def storage_path(area: StorageArea, path: str) -> Path:
    configured = os.environ.get(ROOTS[area][0], '')
    root = Path(configured)
    if not configured or not root.is_absolute() or not root.is_dir():
        raise HTTPException(503, 'This storage folder is not configured or is unavailable.')
    root = root.resolve()
    if path and ('\\' in path or '\x00' in path or any(p in ('', '.', '..') for p in path.split('/'))):
        raise HTTPException(400, 'Use a relative path inside the selected storage folder.')
    target = root / path
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError):
        raise HTTPException(400, 'This storage path cannot be resolved.') from None
    if resolved != target or not resolved.is_relative_to(root) or target.is_symlink():
        raise HTTPException(403, 'Symbolic links and paths outside the selected storage folder cannot be opened.')
    return target


def build_api() -> APIRouter:
    router = APIRouter(prefix='/storage')

    @router.get('')
    def overview():
        locations = []
        for area, (container_variable, host_variable) in ROOTS.items():
            configured = os.environ.get(container_variable) or None
            host = os.environ.get(host_variable) or None
            available = bool(configured and Path(configured).is_absolute() and Path(configured).is_dir())
            locations.append({'id': area, 'container_path': configured, 'host_path': host,
                              'host_variable': host_variable, 'host_path_relative': bool(host and not Path(host).is_absolute()),
                              'status': 'available' if available else 'unavailable' if configured else 'unconfigured'})
        return {'locations': locations}

    @router.get('/{area}/entries')
    def entries(area: StorageArea, path: str = Query(default='', max_length=1000), offset: int = Query(default=0, ge=0)):
        directory = storage_path(area, path)
        if not directory.is_dir():
            raise HTTPException(404, 'Folder not found.')
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.casefold(), e.name))
            values = []
            for entry in children[offset:offset+200]:
                stat = entry.stat(follow_symlinks=False)
                kind = 'symlink' if entry.is_symlink() else 'folder' if entry.is_dir(follow_symlinks=False) else 'file' if entry.is_file(follow_symlinks=False) else 'other'
                values.append({'name': entry.name, 'path': f'{path}/{entry.name}' if path else entry.name,
                               'kind': kind, 'size': stat.st_size if kind == 'file' else None,
                               'modified_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc)})
        except OSError:
            raise HTTPException(503, 'Could not read this folder. Check mount permissions and refresh.') from None
        return {'path': path, 'entries': values, 'total': len(children),
                'next_offset': offset+200 if offset+200 < len(children) else None}

    @router.get('/{area}/file')
    def file(area: StorageArea, path: str = Query(min_length=1, max_length=1000)):
        target = storage_path(area, path)
        if not target.is_file():
            raise HTTPException(404, 'File not found.')
        try:
            with target.open('rb') as source:
                content = source.read(512001)
        except OSError:
            raise HTTPException(503, 'Could not read this file.') from None
        if len(content) > 512000:
            raise HTTPException(413, 'Preview is limited to 512 KB. Open this file on the host to view it.')
        try:
            if b'\x00' in content:
                raise ValueError('binary')
            text = content.decode('utf-8')
        except ValueError:
            raise HTTPException(415, 'Preview is available for UTF-8 text files only.') from None
        return {'path': path, 'text': text}

    return router
