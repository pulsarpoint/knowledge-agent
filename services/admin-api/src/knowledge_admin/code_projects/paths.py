"""Paths in the application's mounted code directory."""

import os
from pathlib import Path, PurePosixPath

from fastapi import HTTPException


def relative_code_path(value: str) -> str:
    if (
        not value
        or len(value) > 1000
        or '\\' in value
        or '\x00' in value
        or PurePosixPath(value).is_absolute()
        or any(part in ('', '.', '..', '.git', '.ka-sync') for part in value.split('/'))
    ):
        raise ValueError('Choose a folder path inside the code root, without dot segments or Git internals.')
    return value


# Before DATA_ROOT the code folder was mounted here; jobs and graphs saved then still hold it.
LEGACY_CODE_ROOT = PurePosixPath('/work/code')


def rebase_code_path(value: str) -> str:
    """Map a path saved under the old /work/code mount to the current CODE_ROOT; other paths stay."""
    path, root = PurePosixPath(value), os.environ.get('CODE_ROOT')
    if not root or not path.is_relative_to(LEGACY_CODE_ROOT) or path.is_relative_to(root):
        return value
    return str(PurePosixPath(root) / path.relative_to(LEGACY_CODE_ROOT))


def code_root() -> Path:
    value = os.environ.get('CODE_ROOT')
    if not value:
        raise HTTPException(503, 'The code folder mount is not configured.')
    root = Path(value).resolve()
    if not root.is_dir():
        raise HTTPException(503, 'The code folder mount is unavailable.')
    return root


def code_path(root: Path, path: str) -> Path:
    """Reject escapes and symlinks, including a symlink in a parent directory."""
    if path == '':
        return root
    if '\\' in path or '\x00' in path or any(p in ('', '.', '..') for p in path.split('/')):
        raise HTTPException(400, 'Use a path relative to the code root.')
    candidate = root / path
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        raise HTTPException(400, 'This code path cannot be resolved.') from None
    if resolved != candidate or not resolved.is_relative_to(root) or candidate.is_symlink():
        raise HTTPException(403, 'This path is outside the code root or uses a symbolic link.')
    return candidate
