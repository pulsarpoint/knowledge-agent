"""Skill import: the skills in a zip, or the skill folder at a git URL, ref and path (shallow clone into a temporary
folder, the commit recorded). Everything is checked before anything is written: paths, symlinks, limits, SKILL.md."""

import io
import os
import stat
import subprocess
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from knowledge_admin.code_projects.worker import GIT_COMMAND, git_env
from knowledge_admin.repositories import git_url
from knowledge_admin.skills import rules
from knowledge_admin.skills.store import Files

NO_SKILL = 'The archive has no SKILL.md at its root or in its top-level folders.'
NOT_A_ZIP = 'This file is not a zip archive.'
UNREADABLE = 'The archive cannot be read: it is damaged, encrypted or uses an unsupported compression.'
ARCHIVE_TOO_LARGE = 'An archive can hold at most 50 MB of skills.'
MAX_ARCHIVE = 50 * rules.MB
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
    return 'file' if stat.S_IFMT(mode) in (0, stat.S_IFREG) else 'other'  # no type bits: a plain file


def _skill_prefixes(paths: list[str]) -> list[str]:
    """Where the skills of an archive are, from its file paths alone: '' for SKILL.md at the root, else each top-level
    folder holding a SKILL.md, looking inside the only top-level folder when the root has no files."""
    base = ''
    tops = sorted({path.split('/')[0] for path in paths if '/' in path})
    if 'SKILL.md' not in paths and len(tops) == 1 and all('/' in path for path in paths):
        base = tops[0] + '/'
    inside = [path.removeprefix(base) for path in paths if path.startswith(base)]
    if 'SKILL.md' in inside:
        return [base]
    folders = sorted({path.split('/')[0] for path in inside if '/' in path})
    return [f'{base}{folder}/' for folder in folders if f'{folder}/SKILL.md' in inside]


def _read(archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> bytes:
    """The entry's content, read with a running count: a header that understates the size cannot get past the file
    limit or the skill's remaining budget."""
    limit = min(rules.MAX_FILE, budget)
    chunks, total = [], 0
    with archive.open(info) as stream:
        while chunk := stream.read(64 * 1024):
            total += len(chunk)
            if total > limit:
                raise ImportRefused(rules.FILE_TOO_LARGE if total > rules.MAX_FILE else rules.FOLDER_TOO_LARGE)
            chunks.append(chunk)
    return b''.join(chunks)


def skills_in_zip(data: bytes) -> list[dict]:
    """The skills in an archive, by name: SKILL.md at its root, in its only top-level folder, or one skill per
    top-level folder holding a SKILL.md (in the only top-level folder, too). Paths and the declared sizes (per skill,
    and 50 MB for the archive) are checked before anything is read. Raises ImportRefused."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportRefused(NOT_A_ZIP) from None
    with archive:
        infos = [info for info in archive.infolist() if not _ignored(info.filename)]
        if refusal := rules.check_paths([{'path': i.filename, 'type': _entry_type(i)} for i in infos]):
            raise ImportRefused(refusal)
        infos = [info for info in infos if not info.is_dir()]
        prefixes = _skill_prefixes([info.filename for info in infos])
        if not prefixes:
            raise ImportRefused(NO_SKILL)
        groups = {prefix: [info for info in infos if info.filename.startswith(prefix)] for prefix in prefixes}
        for members in groups.values():
            if refusal := rules.check_limits([info.file_size for info in members]):
                raise ImportRefused(refusal)
        if sum(info.file_size for members in groups.values() for info in members) > MAX_ARCHIVE:
            raise ImportRefused(ARCHIVE_TOO_LARGE)
        found = []
        try:
            for prefix, members in groups.items():
                files: Files = {}
                budget = rules.MAX_FOLDER
                for info in members:
                    content = _read(archive, info, budget)
                    budget -= len(content)
                    files[info.filename.removeprefix(prefix)] = (content, bool((info.external_attr >> 16) & 0o100))
                found.append(_skill(files))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError, OSError):
            raise ImportRefused(UNREADABLE) from None
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
