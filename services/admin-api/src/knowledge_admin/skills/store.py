"""Skill folders: <SKILLS_ROOT>/<skill_id>/<version>/ with <version>.json beside it ({created_at, source}).

An update writes the whole new folder, then the row's `folder` switches in one update; a run keeps the folder it began
with. Cleanup keeps each skill's last three folders and the current one, and never deletes a folder a running run
pinned. `folder` in the database is relative to the data folder (DATA_ROOT_DIR)."""

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from psycopg.types.json import Jsonb

from knowledge_admin.data_root import data_dir
from knowledge_admin.skills import rules

Files = dict[str, tuple[bytes, bool]]  # path -> (content, executable)
NOT_CONFIGURED = 'The skills folder is not configured.'
FIELDS = 'id,name,description,enabled,revision,source,folder,created_at,updated_at'
PINNED = """SELECT DISTINCT e->>'folder' AS folder FROM ka.agent_sessions s
    CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(s.agent_config->'access'->'resources')='array'
        THEN s.agent_config->'access'->'resources' ELSE '[]'::jsonb END) e
    WHERE s.status<>'closed' AND e->>'type'='skill' AND jsonb_typeof(e->'folder')='string'"""


def skills_root() -> Path:
    """SKILLS_ROOT, a folder inside the data folder. Raises ValueError when it is not set or lies outside."""
    value = os.environ.get('SKILLS_ROOT')
    if not value:
        raise ValueError(NOT_CONFIGURED)
    root = PurePosixPath(value)
    if not root.is_relative_to(data_dir()) or root == data_dir() or '..' in root.parts:
        raise ValueError('SKILLS_ROOT must be a folder inside DATA_ROOT.')
    return Path(root)


def relative(root: Path, skill_id: str, version: str) -> str:
    """The folder as the database stores it: relative to the data folder."""
    return (PurePosixPath(root) / skill_id / version).relative_to(data_dir()).as_posix()


def absolute(folder: str) -> Path:
    return Path(data_dir()) / folder


def _version(root: Path, skill_id: str, commit: str | None) -> str:
    if commit:
        return commit[:12]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    while (root / skill_id / stamp).exists():  # two saves in one second
        time.sleep(0.2)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return stamp


def write_folder(root: Path, skill_id: str, files: Files, source: dict, *, commit: str | None = None) -> str:
    """Write the complete folder under a staging name, then rename it into place; returns its version. A commit already
    written is reused as it is."""
    version = _version(root, skill_id, commit)
    target = root / skill_id / version
    if target.is_dir():
        return version
    staging = root / skill_id / f'.staging-{uuid4().hex}'
    try:
        for path, (content, executable) in files.items():
            file = staging / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
            file.chmod(0o755 if executable else 0o644)
        staging.mkdir(parents=True, exist_ok=True)
        meta = {'created_at': datetime.now(timezone.utc).isoformat(), 'source': source}
        (root / skill_id / f'{version}.json').write_text(json.dumps(meta))
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return version


def _walk(folder: Path):
    for directory, _, names in os.walk(folder):
        for name in names:
            path = Path(directory) / name
            yield path.relative_to(folder).as_posix(), path


def read_folder(root: Path, skill_id: str, version: str) -> Files:
    """Every file of a folder: content and executable bit."""
    folder = root / skill_id / version
    return {
        relative_path: (path.read_bytes(), bool(path.stat().st_mode & 0o100))
        for relative_path, path in sorted(_walk(folder))
    }


def list_files(folder: Path) -> list[dict]:
    """The folder's files by path: size and executable bit."""
    found = []
    for relative_path, path in sorted(_walk(folder)):
        info = path.stat()
        found.append({'path': relative_path, 'size': info.st_size, 'executable': bool(info.st_mode & 0o100)})
    return found


def kept_folders(root: Path, skill_id: str) -> list[dict]:
    """A skill's folders, newest first: {version, created_at, source}."""
    directory = root / skill_id
    if not directory.is_dir():
        return []
    found = []
    for entry in directory.iterdir():
        if entry.name.startswith('.') or not entry.is_dir():
            continue
        meta_file = directory / f'{entry.name}.json'
        try:
            meta = json.loads(meta_file.read_text())
        except (OSError, ValueError):
            meta = {
                'created_at': datetime.fromtimestamp(entry.stat().st_mtime, timezone.utc).isoformat(),
                'source': None,
            }
        found.append({'version': entry.name, 'created_at': meta.get('created_at'), 'source': meta.get('source')})
    return sorted(found, key=lambda item: item['created_at'] or '', reverse=True)


def remove_folder(root: Path, skill_id: str, version: str) -> None:
    shutil.rmtree(root / skill_id / version, ignore_errors=True)
    (root / skill_id / f'{version}.json').unlink(missing_ok=True)


def cleanup(root: Path, current: dict[str, str | None], pinned: set[str], *, all_skills: bool = False) -> list[str]:
    """Delete the folders no skill keeps (rules.folders_to_delete): of the skills in `current` (id: its current version;
    None for a deleted skill), or with all_skills (at startup) of every skill folder under the root, those not in
    `current` being deleted skills. Returns the deleted folders (relative)."""
    if not root.is_dir():
        return []
    if all_skills:
        directories = [entry for entry in root.iterdir() if entry.is_dir() and not entry.name.startswith('.')]
    else:
        directories = [root / skill for skill in current if (root / skill).is_dir()]
    deleted = []
    for directory in directories:
        skill = directory.name
        versions = [item['version'] for item in kept_folders(root, skill)]
        pinned_versions = {version for version in versions if relative(root, skill, version) in pinned}
        for version in rules.folders_to_delete(versions, current.get(skill), pinned_versions):
            remove_folder(root, skill, version)
            deleted.append(relative(root, skill, version))
        for leftover in directory.glob('.staging-*'):  # an interrupted write, older than an hour
            if time.time() - leftover.stat().st_mtime > 3600:
                shutil.rmtree(leftover, ignore_errors=True)
        if current.get(skill) is None and not any(directory.iterdir()):
            directory.rmdir()
    return deleted


def pinned_folders(conn) -> set[str]:
    """The skill folders runs that are not closed pinned (their config's access entries of type skill)."""
    return {row['folder'] for row in conn.execute(PINNED)}


def current_versions(conn) -> dict[str, str]:
    """Every skill's current version, by id (all projects)."""
    return {
        str(row['id']): PurePosixPath(row['folder']).name
        for row in conn.execute('SELECT id,folder FROM ka.agent_skills')
    }


def switch(conn, project, skill_id, *, folder: str, name: str, description: str, source: dict, actor) -> dict:
    """Point the skill at a folder already written, with the name and description read from its SKILL.md."""
    return conn.execute(
        f"""UPDATE ka.agent_skills SET folder=%s,name=%s,description=%s,source=%s,updated_by=%s,revision=revision+1
        WHERE id=%s AND project_id=%s RETURNING {FIELDS}""",
        (folder, name, description, Jsonb(source), actor, skill_id, project),
    ).fetchone()


def startup_cleanup(repository_store) -> list[str]:
    """At admin-api startup: every skill's folders, deleted skills' too, when SKILLS_ROOT is configured."""
    if not os.environ.get('SKILLS_ROOT'):
        return []
    root = skills_root()
    with repository_store.connect() as conn:
        current, pinned = current_versions(conn), pinned_folders(conn)
    return cleanup(root, current, pinned, all_skills=True)
