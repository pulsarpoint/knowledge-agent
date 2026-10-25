"""Skill import: the skills in a zip, or the skill folder at a git URL, ref and path (shallow clone into a temporary
folder, the commit recorded). Everything is checked before anything is written: paths, symlinks, limits, SKILL.md."""

import io
import os
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from knowledge_admin.code_projects.worker import GIT_COMMAND, git_env
from knowledge_admin.repositories import git_url
from knowledge_admin.skills import rules
from knowledge_admin.skills.store import Files

NO_SKILL = 'The archive has no SKILL.md at its root or in its top-level folders.'
NOT_A_ZIP = 'This file is not a zip archive.'
GIT_FAILED = 'Git could not fetch this repository and ref. Check the URL, the ref and access.'
GIT_TIMEOUT = 'Git timed out. Check the URL and try again.'
ALLOW_LOCAL = False  # tests clone local repositories; the application clones HTTPS only
TIMEOUT = 120


class ImportRefused(ValueError):
    """The import breaks a rule; nothing was written. The message says which."""


def _skill(files: Files) -> dict:
    """{name, description, files} of one skill folder's files, checked against the limits and SKILL.md's rules."""
    if refusal := rules.check_limits([len(content) for content, _ in files.values()]):
        raise ImportRefused(refusal)
    if 'SKILL.md' not in files:
        raise ImportRefused(NO_SKILL)
    try:
        meta = rules.parse_skill_md(files['SKILL.md'][0].decode('utf-8'))
    except UnicodeDecodeError:
        raise ImportRefused('SKILL.md is not UTF-8 text.') from None
    except ValueError as error:
        raise ImportRefused(str(error)) from None
    return {'name': meta['name'], 'description': meta['description'], 'files': files}


def _ignored(path: str) -> bool:
    """macOS archive leftovers."""
    return path.startswith('__MACOSX/') or PurePosixPath(path).name == '.DS_Store'


def _entry_type(info: zipfile.ZipInfo) -> str:
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        return 'symlink'
    if info.is_dir():
        return 'folder'
    return 'file' if not mode or stat.S_ISREG(mode) else 'other'


def _under(files: Files, prefix: str) -> Files:
    return {path.removeprefix(prefix): value for path, value in files.items() if path.startswith(prefix)}


def _top_folders(files: Files) -> list[str]:
    return sorted({path.split('/')[0] for path in files if '/' in path})


def skills_in_zip(data: bytes) -> list[dict]:
    """The skills in an archive, by name: SKILL.md at its root, in its only top-level folder, or one skill per
    top-level folder holding a SKILL.md (in the only top-level folder, too). Raises ImportRefused."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportRefused(NOT_A_ZIP) from None
    with archive:
        infos = [info for info in archive.infolist() if not _ignored(info.filename)]
        if refusal := rules.check_paths([{'path': i.filename, 'type': _entry_type(i)} for i in infos]):
            raise ImportRefused(refusal)
        infos = [info for info in infos if not info.is_dir()]
        if refusal := rules.check_limits([info.file_size for info in infos][: rules.MAX_FILES + 1]):
            if refusal != rules.FOLDER_TOO_LARGE:  # an archive of several skills may hold more than one folder's worth
                raise ImportRefused(refusal)
        files: Files = {}
        for info in infos:
            with archive.open(info) as stream:
                content = stream.read(rules.MAX_FILE + 1)
            files[info.filename] = (content, bool((info.external_attr >> 16) & 0o100))
    if 'SKILL.md' not in files and len(_top_folders(files)) == 1 and not any('/' not in p for p in files):
        files = _under(files, _top_folders(files)[0] + '/')
    if 'SKILL.md' in files:
        return [_skill(files)]
    found = [_skill(_under(files, f'{top}/')) for top in _top_folders(files) if f'{top}/SKILL.md' in files]
    if not found:
        raise ImportRefused(NO_SKILL)
    names = [skill['name'] for skill in found]
    if duplicate := next((name for name in names if names.count(name) > 1), None):
        raise ImportRefused(f'The archive holds two skills named {duplicate}.')
    return sorted(found, key=lambda skill: skill['name'])


def read_folder(folder: Path, *, skip_git: bool = False) -> Files:
    """A folder on disk as a skill's files; a symbolic link or anything but files and folders is refused."""
    entries, files = [], {}
    for directory, folders, names in os.walk(folder):
        here = Path(directory)
        if skip_git and here == folder and '.git' in folders:
            folders.remove('.git')
        for name in [*folders, *names]:
            path = here / name
            info = path.lstat()
            kind = (
                'symlink'
                if stat.S_ISLNK(info.st_mode)
                else 'folder'
                if stat.S_ISDIR(info.st_mode)
                else 'file'
                if stat.S_ISREG(info.st_mode)
                else 'other'
            )
            relative = path.relative_to(folder).as_posix()
            entries.append({'path': relative, 'type': kind})
            if kind == 'file' and not _ignored(relative):
                files[relative] = path
    if refusal := rules.check_paths(entries):
        raise ImportRefused(refusal)
    if refusal := rules.check_limits([path.stat().st_size for path in files.values()]):
        raise ImportRefused(refusal)
    return {
        relative: (path.read_bytes(), bool(path.stat().st_mode & 0o100)) for relative, path in sorted(files.items())
    }


def _url(value: str) -> str:
    if ALLOW_LOCAL and '://' not in value:
        return value
    try:
        return git_url(value)
    except ValueError as error:
        raise ImportRefused(str(error)) from None


def from_git(url: str, ref: str, path: str, *, username: str | None = None, token: str | None = None):
    """(skill, commit): the folder at `path` (the root when empty) of `ref` (the default branch when empty)."""
    remote = _url(url)
    if path and (refusal := rules.check_paths([{'path': path, 'type': 'folder'}])):
        raise ImportRefused(refusal)
    command = [*GIT_COMMAND, *(['-c', 'protocol.file.allow=always'] if ALLOW_LOCAL else [])]
    env = git_env(username, token)
    with tempfile.TemporaryDirectory(prefix='skill-import-') as temporary:
        checkout = Path(temporary) / 'checkout'

        def git(*args: str) -> str:
            try:
                result = subprocess.run(
                    [*command, '-C', str(checkout), *args],
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                raise ImportRefused(GIT_TIMEOUT) from None
            if result.returncode != 0:  # remote output can hold credentials: never returned
                raise ImportRefused(GIT_FAILED)
            return result.stdout.strip()

        checkout.mkdir()
        git('init', '-q')
        git('fetch', '-q', '--no-tags', '--depth=1', '--', remote, ref or 'HEAD')
        commit = git('rev-parse', '--verify', 'FETCH_HEAD^{commit}')
        git('checkout', '-q', '--detach', commit)
        folder = checkout / path if path else checkout
        if folder.is_symlink() or not folder.is_dir() or not folder.resolve().is_relative_to(checkout.resolve()):
            raise ImportRefused(f'The repository has no folder {path} at this ref.')
        return _skill(read_folder(folder, skip_git=not path)), commit
