"""DATA_ROOT inside the containers (DATA_ROOT_DIR) and on the host (DATA_ROOT, display only)."""
import os
from pathlib import PurePosixPath

DEFAULT_DIR = '/work/data'


def data_dir() -> PurePosixPath:
    root = PurePosixPath(os.environ.get('DATA_ROOT_DIR') or DEFAULT_DIR)
    if root.anchor != '/' or '..' in root.parts:
        raise ValueError('DATA_ROOT_DIR must be an absolute path mounted in the container')
    return root


def data_path(source_path: str) -> PurePosixPath:
    """A RAG collection folder (relative to DATA_ROOT) as a container path."""
    return data_dir() / source_path


def _relative(container_path: str) -> str | None:
    """The path relative to DATA_ROOT_DIR ('' for the root itself), or None outside it."""
    path, root = PurePosixPath(container_path), data_dir()
    if path == root:
        return ''
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else None


def host_path(container_path: str | None) -> str | None:
    """Where a container path under DATA_ROOT_DIR lives on the host; None when unknown."""
    host = (os.environ.get('DATA_ROOT') or '').rstrip('/')
    relative = _relative(container_path) if container_path else None
    if not host or relative is None:
        return None
    return host + ('/' + relative if relative else '')


def jira_folder() -> str:
    """JIRA_FOLDER: where the Jira export writes, relative to DATA_ROOT (the RAG writer's path prefix)."""
    relative = _relative(os.environ.get('JIRA_EXPORT_ROOT') or f'{data_dir()}/jira')
    if not relative:
        raise ValueError('JIRA_EXPORT_ROOT must be a folder inside DATA_ROOT_DIR')
    return relative
