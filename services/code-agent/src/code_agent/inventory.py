"""Resolve code projects and their mapped folders inside the read-only mount."""
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from code_agent.config import Repo


def repositories(root: Path) -> list[tuple[Repo, str]]:
    root = root.resolve(strict=True)
    with psycopg.connect('', row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        rows = conn.execute('''SELECT r.id,r.name,
            ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folder_paths
            FROM ka.repositories r WHERE r.archived_at IS NULL ORDER BY r.name,r.id''').fetchall()
    result = []
    for row in rows:
        folders = []
        for folder in row['folder_paths']:
            candidate = root / folder
            try:
                path = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if path == candidate and path != root and path.is_relative_to(root) and path.is_dir():
                folders.append(folder)
        if folders:
            result.append((Repo(name=str(row['id']), path=root, folder_paths=tuple(folders)), row['name']))
    return result
