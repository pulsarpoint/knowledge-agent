"""RAG collections as agents see them, including configs saved before collections existed."""
import re
from pathlib import PurePosixPath
from typing import Any

from knowledge_admin.data_root import data_dir, jira_folder

LEGACY_RAG_ROOT = PurePosixPath('/work/rag-data')  # where folder-era configs (before 0063) kept folders


def slug(path: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', path.lower()).strip('-')


def _with_paths(item: dict[str, Any]) -> dict[str, Any]:
    """Configs saved between collections and collection paths hold one container `path` per collection."""
    if 'paths' in item:
        return item
    rest = {key: value for key, value in item.items() if key != 'path'}
    return {**rest, 'paths': [item['path']] if item.get('path') else []}


def _legacy_folder(top_or_path: str) -> tuple[str, PurePosixPath] | None:
    """(collection name, container folder) for a folder-era entry, or None when it names no folder.

    Folder-era entries name a top-level rag-data folder. Jira moved to the Jira folder (migration 0067 also
    rewrote saved /work/rag-data/jira paths to /work/data/jira); other rag-data folders keep their old path,
    which no longer exists. A path already under DATA_ROOT_DIR keeps its first segment.
    """
    path, rag_root, root = PurePosixPath(top_or_path), LEGACY_RAG_ROOT, data_dir()
    jira = PurePosixPath(jira_folder())
    if not path.is_absolute():  # a search_folder's first segment
        if not path.parts:
            return None
        top = path.parts[0]
        return ('jira', root / jira) if top == 'jira' else (top, rag_root / top)
    if path.is_relative_to(rag_root) and path != rag_root:
        top = path.relative_to(rag_root).parts[0]
        return ('jira', root / jira) if top == 'jira' else (top, rag_root / top)
    if path.is_relative_to(root) and path != root:
        relative = path.relative_to(root)
        if relative.is_relative_to(jira):
            return 'jira', root / jira
        return relative.parts[0], root / relative.parts[0]
    return None


def document_collections(config: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """(all_collections, [{id?, name, description, paths}]) for current, path-era and folder-era configs."""
    documents = config['documents']
    if 'collections' in documents:
        return bool(documents.get('all_collections')), [_with_paths(item) for item in documents['collections']]
    items: dict[str, dict[str, Any]] = {}
    for folder in documents.get('folders', []):
        found = _legacy_folder(folder['search_folder'].split('.')[0] if 'search_folder' in folder else folder['path'])
        if found is None:
            continue
        name, path = found
        items.setdefault(slug(name), {'name': slug(name), 'description': folder.get('description', ''), 'paths': [str(path)]})
    return bool(documents.get('all_folders')), list(items.values())
