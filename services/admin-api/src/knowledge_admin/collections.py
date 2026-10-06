"""RAG collections as agents see them, including configs saved before collections existed."""
import re
from pathlib import PurePosixPath
from typing import Any

LEGACY_RAG_ROOT = PurePosixPath('/work/rag-data')  # where folder-era configs (before 0063) kept folders


def slug(path: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', path.lower()).strip('-')


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
    rag_root = LEGACY_RAG_ROOT
    items: dict[str, dict[str, Any]] = {}
    for folder in documents.get('folders', []):
        if 'search_folder' in folder:
            top = folder['search_folder'].split('.')[0]
        else:
            # Older configs may carry only the container path; keep its first segment below the old rag-data mount.
            path = PurePosixPath(folder['path'])
            if not path.is_relative_to(rag_root) or path == rag_root:
                continue
            top = path.relative_to(rag_root).parts[0]
        items.setdefault(slug(top), {'name': slug(top), 'description': folder.get('description', ''), 'paths': [str(rag_root / top)]})
    return bool(documents.get('all_folders')), list(items.values())
