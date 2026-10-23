"""DATA_ROOT inside the containers (DATA_ROOT_DIR) and on the host (DATA_ROOT, display only)."""

import os
import re
from pathlib import PurePosixPath

DEFAULT_DIR = '/work/data'


def data_dir() -> PurePosixPath:
    root = PurePosixPath(os.environ.get('DATA_ROOT_DIR') or DEFAULT_DIR)
    if root.anchor != '/' or '..' in root.parts:
        raise ValueError('DATA_ROOT_DIR must be an absolute path mounted in the container')
    return root


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


# Lowercase-only: on case-insensitive filesystems (macOS) Payments and payments are one directory.
SEGMENT = r'[a-z0-9_-][a-z0-9._-]*'
FOLDER = re.compile(rf'{SEGMENT}(/{SEGMENT})*')
FOLDER_RULE = (
    'A documentation folder is a path such as payments or team/payments: each segment uses lowercase letters, digits, '
    "'.', '_' and '-' and does not start with '.'; node_modules and a first segment services are not allowed; "
    'at most 200 characters.'
)


def documentation_folder(value: str) -> str:
    """A documentation folder: lowercase segments; `services` and node_modules are reserved. Raises ValueError(FOLDER_RULE)."""
    parts = value.split('/')
    if len(value) > 200 or not FOLDER.fullmatch(value) or 'node_modules' in parts or parts[0] == 'services':
        raise ValueError(FOLDER_RULE)
    return value


DATA_SEGMENT = r'[A-Za-z0-9_-][A-Za-z0-9._-]*'
DATA_FOLDER = re.compile(rf'{DATA_SEGMENT}(/{DATA_SEGMENT})*')
DATA_FOLDER_RULE = (
    "A folder is a path under DATA_ROOT such as code/payments or jira: names of letters, digits, '.', '_' "
    "and '-' separated by '/', none starting with '.'; at most 500 characters."
)


def data_folder(value: str) -> str:
    """A folder under DATA_ROOT as RAG names it (relative, no '..', no hidden segment). Raises ValueError(DATA_FOLDER_RULE)."""
    if len(value) > 500 or not DATA_FOLDER.fullmatch(value):
        raise ValueError(DATA_FOLDER_RULE)
    return value
