"""RAG collections as agents see them, including configs saved before collections existed."""
import os
import re
from pathlib import PurePosixPath
from typing import Any

AREA_ROOTS = {'code': 'CODE_ROOT', 'code-index': 'CODE_INDEX_ROOT', 'rag-data': 'RAG_SOURCE_ROOT',
              'generated-documents': 'DOCUMENT_OUTPUT_ROOT', 'docs': 'BOOTSTRAP_DOCS_ROOT'}


def slug(path: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', path.lower()).strip('-')


def area_root(source_path: str) -> PurePosixPath:
    area, _, rest = source_path.partition('/')
    root = PurePosixPath(os.environ.get(AREA_ROOTS[area], ''))
    if root.anchor != '/' or '..' in root.parts:
        raise ValueError(f'{AREA_ROOTS[area]} must be an absolute path mounted in the agent container')
    return root / rest if rest else root


def _with_paths(item: dict[str, Any]) -> dict[str, Any]:
    """Configs saved between collections and collection paths hold one container `path` per collection."""
    if 'paths' in item:
        return item
    rest = {key: value for key, value in item.items() if key != 'path'}
    return {**rest, 'paths': [item['path']] if item.get('path') else []}


def document_collections(config: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """(all_collections, [{id?, name, description, paths}]) for current, path-era and folder-era configs."""
    documents = config['documents']
    if 'collections' in documents:
        return bool(documents.get('all_collections')), [_with_paths(item) for item in documents['collections']]
    rag_root = PurePosixPath(os.environ.get('RAG_SOURCE_ROOT', '/work/rag-data'))
    items: dict[str, dict[str, Any]] = {}
    for folder in documents.get('folders', []):
        if 'search_folder' in folder:
            top = folder['search_folder'].split('.')[0]
        else:
            # Older configs may carry only the container path; keep its first segment below RAG_SOURCE_ROOT.
            path = PurePosixPath(folder['path'])
            if not path.is_relative_to(rag_root) or path == rag_root:
                continue
            top = path.relative_to(rag_root).parts[0]
        items.setdefault(slug(top), {'name': slug(top), 'description': folder.get('description', ''), 'paths': [str(rag_root / top)]})
    return bool(documents.get('all_folders')), list(items.values())
