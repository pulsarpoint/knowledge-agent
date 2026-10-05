"""Searchable code projects: each mapped folder is a path, owned by the project, of its chosen RAG collection."""
import os
import re
from uuid import UUID

import httpx
from fastapi import HTTPException

from knowledge_admin.rag_collections import Owner, RagCollections
from knowledge_admin.repositories import RepositoryStore

OWNER_KIND = 'code_project'


def default_collection(name: str, project_id: UUID) -> str:
    """The project's slug: lowercase letters, digits and single hyphens, at most 60 characters."""
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60].strip('-')
    return slug or f'code-{project_id.hex[:8]}'


def top_folders(folders: list[str]) -> list[str]:
    """Folders not inside another mapped folder; a collection may not hold overlapping paths."""
    return [f for f in folders if not any(f != other and f.startswith(other + '/') for other in folders)]


def sync_paths(rag: RagCollections, project_id: UUID, collection: str | None, folders: list[str]) -> None:
    """Make RAG hold exactly code/<folder> for each folder in `collection`; None removes them everywhere."""
    if collection is None:
        rag.remove_paths(Owner(OWNER_KIND, project_id))
        return
    wanted = top_folders(folders)
    for row in rag.owned_paths(OWNER_KIND, project_id):
        if row['owner_key'] not in wanted:
            rag.remove_paths(Owner(OWNER_KIND, project_id, row['owner_key']))
    for folder in wanted:  # adding an owned path again moves it here from another collection
        rag.add_path(collection, f'code/{folder}', Owner(OWNER_KIND, project_id, folder))


def reconcile(store: RepositoryStore, app_name: str, client: httpx.Client | None, repository_id: UUID,
              actor: UUID, *, release: bool = False) -> str | None:
    """Bring RAG in line with the committed project row and record the outcome in search_error.

    Projects that are not searchable are left alone unless `release` (Searchable switched off, or a
    searchable project archived). Returns None, or the message the page shows with Retry; a failure
    never rolls back the project.
    """
    with store.connect() as conn:
        project = store.project(conn, app_name)
        row = conn.execute('''SELECT r.search_collection, r.archived_at,
            ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folders
            FROM ka.repositories r WHERE r.id=%s AND r.project_id=%s''', (repository_id, project)).fetchone()
    collection = row['search_collection'] if row['archived_at'] is None else None
    if collection is None and not release:
        return None
    key = os.environ.get('RAG_BACKOFFICE_KEY')
    error = None
    try:
        if client is None or not key:
            raise HTTPException(503, 'RAG administration is not configured')
        sync_paths(RagCollections(client, key, project, actor), repository_id, collection, row['folders'])
    except HTTPException as exc:
        error = str(exc.detail)[:2000]
    with store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET search_error=%s WHERE id=%s', (error, repository_id))
    return error
