"""Pure skill rules: SKILL.md frontmatter, file paths, limits, changed files and which folders cleanup deletes."""

import re

import yaml

NAME = re.compile(r'[a-z0-9]+(-[a-z0-9]+)*')
NO_FRONTMATTER = 'SKILL.md must start with a frontmatter block between --- lines.'
BAD_FRONTMATTER = "SKILL.md's frontmatter is not valid YAML with keys and values."
NAME_RULE = "SKILL.md's name must be 1-64 lowercase letters, digits and single hyphens, such as pdf-forms."
DESCRIPTION_RULE = "SKILL.md's description must be text of 1-1024 characters."

MB = 1024 * 1024
MAX_FOLDER, MAX_FILES, MAX_FILE = 10 * MB, 500, 5 * MB
FOLDER_TOO_LARGE = 'A skill folder can hold at most 10 MB.'
TOO_MANY_FILES = 'A skill folder can hold at most 500 files.'
FILE_TOO_LARGE = 'A skill file can be at most 5 MB.'
KEEP = 3


def parse_skill_md(text: str) -> dict:
    """{name, description, body} of a SKILL.md. Raises ValueError with the rule it breaks."""
    text = text.replace('\r\n', '\n').removeprefix('﻿')
    if not text.startswith('---\n'):
        raise ValueError(NO_FRONTMATTER)
    end = text.find('\n---', 3)
    if end == -1 or text[end + 4 : end + 5] not in ('', '\n'):
        raise ValueError(NO_FRONTMATTER)
    try:
        meta = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        raise ValueError(BAD_FRONTMATTER) from None
    if not isinstance(meta, dict):
        raise ValueError(BAD_FRONTMATTER)
    name, description = meta.get('name'), meta.get('description')
    if not isinstance(name, str) or len(name) > 64 or not NAME.fullmatch(name):
        raise ValueError(NAME_RULE)
    if not isinstance(description, str) or not description.strip() or len(description.strip()) > 1024:
        raise ValueError(DESCRIPTION_RULE)
    return {'name': name, 'description': description.strip(), 'body': text[end + 5 :].lstrip('\n')}


def check_paths(entries: list[dict]) -> str | None:
    """None when every entry ({path, type: file | folder | symlink | other}) is a relative file or folder path without
    '..', else the refusal text. Folders may end with '/', as a zip names them."""
    seen = set()
    for item in entries:
        path = item['path']
        if item['type'] == 'folder':
            path = path.removesuffix('/')
        if item['type'] == 'symlink':
            return f'{path} is a symbolic link; skills cannot contain links.'
        if item['type'] not in ('file', 'folder'):
            return f'{path} is not a file or a folder.'
        if (
            not path
            or path.startswith('/')
            or '\\' in path
            or '\x00' in path
            or any(part in ('', '.', '..') for part in path.split('/'))
        ):
            return f'{path!r} is not a relative path inside the skill folder.'
        if path in seen:
            return f'{path} appears twice.'
        seen.add(path)
    return None


def check_limits(sizes: list[int]) -> str | None:
    """None when the files (their sizes) fit a skill folder: 500 files, 5 MB each, 10 MB in all."""
    if len(sizes) > MAX_FILES:
        return TOO_MANY_FILES
    if any(size > MAX_FILE for size in sizes):
        return FILE_TOO_LARGE
    if sum(sizes) > MAX_FOLDER:
        return FOLDER_TOO_LARGE
    return None


def changed_files(old: dict[str, bytes], new: dict[str, bytes]) -> list[dict]:
    """The files that differ between two versions, by path: added, removed or modified."""
    changes = []
    for path in sorted(old.keys() | new.keys()):
        if path not in new:
            changes.append({'path': path, 'change': 'removed'})
        elif path not in old:
            changes.append({'path': path, 'change': 'added'})
        elif old[path] != new[path]:
            changes.append({'path': path, 'change': 'modified'})
    return changes


def folders_to_delete(folders: list[str], current: str | None, pinned: set[str]) -> list[str]:
    """Of a skill's folders (newest first), the ones to delete: all but the newest three and the current one, except
    those a running run pinned. A deleted skill has no current folder and keeps none but the pinned."""
    keep = set(folders[:KEEP]) if current is not None else set()
    return [folder for folder in folders if folder not in keep and folder != current and folder not in pinned]
