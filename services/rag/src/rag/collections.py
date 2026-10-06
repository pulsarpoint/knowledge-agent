"""Collection folders under DATA_ROOT: confined paths, glob selection and walks."""

import errno
import os
import re
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

AREAS = {
    'code': ('CODE_ROOT', 'CODE_PATH'),
    'code-index': ('CODE_INDEX_ROOT', 'CODE_INDEX_PATH'),
    'rag-data': ('RAG_SOURCE_ROOT', 'RG_DATA'),
    'generated-documents': ('DOCUMENT_OUTPUT_ROOT', 'GENERATED_DOCS_PATH'),
    'docs': ('BOOTSTRAP_DOCS_ROOT', 'DOCS_PATH'),
}
WRITABLE_AREA = 'rag-data'
SOURCE_PATH = re.compile(r'(code|code-index|rag-data|generated-documents|docs)(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*')
SKIPPED_DIRECTORIES = {'.git', 'node_modules'}
MAX_FILES = 20_000
MAX_STATUS_ENTRIES = 20_000  # status walks stop (truncated) after visiting this many entries
MAX_SYNC_ENTRIES = 10 * MAX_FILES  # a sync fails rather than walk an unbounded tree
MAX_BYTES = 200_000_000
MAX_FOLDERS = 1000  # the folder browser lists at most this many children
FOLDER_NAME = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9._-]*')  # a name that can be a source_path segment


class FolderMissing(HTTPException):
    def __init__(self, source: str):
        self.source = source
        super().__init__(409, f'Path {source} is missing. Nothing was removed from the index; '
                              'restore the folder or remove the path from the collection.')


def source_path(value: str) -> str:
    if len(value) > 500 or not SOURCE_PATH.fullmatch(value):
        raise HTTPException(400, 'Choose a folder inside code, code-index, rag-data, generated-documents or docs')
    return value


def overlaps(a: str, b: str) -> bool:
    """Whether one collection path contains the other; equal paths overlap."""
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


def relative_path(value: str) -> str:
    parts = value.split('/')
    if (not value or len(value) > 1000 or value.startswith('/') or '\\' in value
            or any(part in ('', '.', '..') or len(part) > 255 or any(ord(c) < 32 for c in part) for part in parts)):
        raise HTTPException(400, 'Use a file path inside the collection folder')
    return value


@lru_cache(maxsize=512)
def glob_regex(pattern: str) -> re.Pattern:
    """Translate a path glob: ** spans folders, * and ? stay within one segment, [..] is a character class."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            out.append('(?:.*/)?'); i += 3
        elif pattern.startswith('**', i):
            out.append('.*'); i += 2
        elif pattern[i] == '*':
            out.append('[^/]*'); i += 1
        elif pattern[i] == '?':
            out.append('[^/]'); i += 1
        elif pattern[i] == '[':
            end = pattern.find(']', i + 1)
            body = pattern[i + 1:end] if end != -1 else ''
            negated = body.startswith('!')
            members = body[1:] if negated else body
            if end == -1 or not members:
                out.append(re.escape('[')); i += 1
            else:
                members = members.replace('\\', '\\\\').replace('[', '\\[').replace('^', '\\^')
                out.append('(?!/)[' + ('^' if negated else '') + members + ']'); i = end + 1
        else:
            out.append(re.escape(pattern[i])); i += 1
    try:
        return re.compile(''.join(out), re.DOTALL)
    except re.error:
        raise HTTPException(400, f'Invalid pattern: {pattern}') from None


def matches(path: str, include: list[str], exclude: list[str]) -> bool:
    return (any(glob_regex(p).fullmatch(path) for p in include)
            and not any(glob_regex(p).fullmatch(path) for p in exclude))


def visible(path: str) -> bool:
    """False for hidden segments (incl. .env* and .git) and skipped directories like node_modules."""
    return not any(part.startswith('.') or part in SKIPPED_DIRECTORIES for part in path.split('/'))


def indexable(path: str, include: list[str], exclude: list[str]) -> bool:
    """The one rule for which collection files sync indexes and the file API serves."""
    return visible(path) and matches(path, include, exclude)


def refuse_hidden(path: str) -> None:
    if not visible(path):
        raise HTTPException(400, 'Hidden files, .env files and .git or node_modules folders cannot be changed here')


@dataclass
class Walk:
    files: list[dict] = field(default_factory=list)
    newest_mtime: float | None = None
    skipped: dict[str, int] = field(default_factory=lambda: {'links': 0, 'special': 0, 'unreadable': 0, 'invalid_names': 0})
    truncated: bool = False
    missing: list[str] = field(default_factory=list)  # paths absent during a truncating walk
    visited: int = 0  # entries visited, counted across every path of a collection


class Areas:
    def __init__(self, roots: dict[str, Path], host_roots: dict[str, str | None]):
        self.roots = {area: Path(root) for area, root in roots.items()}
        self.host_roots = host_roots

    @classmethod
    def from_env(cls):
        roots = {area: Path(os.environ[env]) for area, (env, _) in AREAS.items() if os.environ.get(env)}
        hosts = {area: os.environ.get(host) for area, (_, host) in AREAS.items()}
        return cls(roots, hosts)

    def _split(self, source: str) -> tuple[Path, list[str]]:
        area, *rest = source_path(source).split('/')
        root = self.roots.get(area)
        if root is None:
            raise HTTPException(503, f'The {area} folder is not mounted in the RAG service')
        return root, rest

    def container_path(self, source: str) -> Path:
        root, rest = self._split(source)
        return root.joinpath(*rest)

    def host_path(self, source: str) -> str | None:
        area, _, rest = source.partition('/')
        host = self.host_roots.get(area)
        return None if not host else host.rstrip('/') + ('/' + rest if rest else '')

    @staticmethod
    def writable(source: str) -> bool:
        return source.split('/')[0] == WRITABLE_AREA

    @contextmanager
    def _directory(self, source: str, parts: list[str], *, create: bool = False):
        root, rest = self._split(source)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, part in enumerate([*rest, *parts]):
                if create:
                    try:
                        os.mkdir(part, mode=0o750, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    def present(self, source: str) -> bool:
        """Whether the collection folder exists as a real directory (no walk)."""
        try:
            with self._directory(source, []):
                return True
        except OSError:
            return False

    def ensure_folder(self, source: str) -> None:
        """Create a writable-area collection folder (0o750, no links followed); other areas are left alone."""
        if self.writable(source):
            with self._directory(source, [], create=True):
                pass

    def subfolders(self, source: str, limit: int = MAX_FOLDERS) -> tuple[list[str], bool]:
        """Child folders that could be connected to a collection, sorted, and whether more exist.

        Links are never followed or listed; hidden folders, .git, node_modules and names that are not
        valid source_path segments are left out.
        """
        try:
            with self._directory(source, []) as fd:
                found = []
                for name in sorted(os.listdir(fd)):
                    if not visible(name) or not FOLDER_NAME.fullmatch(name):
                        continue
                    try:
                        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISDIR(entry.st_mode):
                        if len(found) == limit:
                            return found, True
                        found.append(name)
                return found, False
        except (FileNotFoundError, NotADirectoryError):
            raise HTTPException(404, 'Folder does not exist') from None

    def walk(self, source: str, include: list[str], exclude: list[str], *, limit: int = MAX_FILES,
             truncate: bool = False, max_entries: int | None = None, into: Walk | None = None) -> Walk:
        """List indexable files of one folder; patterns match paths relative to it.

        `limit` caps matching files; `max_entries` caps every entry visited. With `truncate` either cap
        stops the walk and marks it truncated; otherwise it fails. With `into`, files are added to that
        walk under their full DATA_ROOT path and the caps count everything already in it.
        """
        result = Walk() if into is None else into
        if max_entries is None:
            max_entries = MAX_STATUS_ENTRIES if truncate else MAX_SYNC_ENTRIES

        def newer(value: float):
            result.newest_mtime = value if result.newest_mtime is None else max(result.newest_mtime, value)

        def visit(fd: int, prefix: str) -> bool:
            newer(os.fstat(fd).st_mtime)
            try:
                names = sorted(os.listdir(fd))
            except PermissionError:
                result.skipped['unreadable'] += 1
                return True
            for name in names:
                result.visited += 1
                if result.visited > max_entries:
                    if truncate:
                        result.truncated = True
                        return False
                    raise HTTPException(400, f'This collection has more than {max_entries} entries in its folders; '
                                             'narrow its paths or include patterns')
                try:
                    name.encode('utf-8')
                except UnicodeEncodeError:
                    result.skipped['invalid_names'] += 1
                    continue
                try:
                    entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                except PermissionError:
                    result.skipped['unreadable'] += 1
                    continue
                relative = f'{prefix}/{name}' if prefix else name
                if stat.S_ISLNK(entry.st_mode):
                    result.skipped['links'] += 1
                elif stat.S_ISDIR(entry.st_mode):
                    if not visible(name):
                        continue
                    try:
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    except PermissionError:
                        result.skipped['unreadable'] += 1
                        continue
                    except OSError:
                        continue
                    try:
                        if not visit(child, relative):
                            return False
                    finally:
                        os.close(child)
                elif stat.S_ISREG(entry.st_mode):
                    if not indexable(relative, include, exclude):
                        continue
                    if len(result.files) >= limit:
                        if truncate:
                            result.truncated = True
                            return False
                        raise HTTPException(400, f'This collection selects more than {limit} files; narrow its include patterns')
                    result.files.append({'path': relative if into is None else f'{source}/{relative}', 'source': source,
                                         'size': entry.st_size, 'mtime': entry.st_mtime})
                    newer(entry.st_mtime)
                elif indexable(relative, include, exclude):
                    result.skipped['special'] += 1
            return True

        with ExitStack() as stack:
            try:
                fd = stack.enter_context(self._directory(source, []))
            except (FileNotFoundError, NotADirectoryError):
                raise FolderMissing(source) from None
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise HTTPException(409, f'The collection folder {source} is a link; point the collection at a real folder') from None
                raise
            visit(fd, '')
        return result

    def walk_paths(self, sources: list[str], include: list[str], exclude: list[str], *, limit: int = MAX_FILES,
                   truncate: bool = False, max_entries: int | None = None) -> Walk:
        """Walk every path of a collection into one result with full DATA_ROOT-relative file paths.

        The caps apply to the collection as a whole. With `truncate` (status and listings) a missing
        path is recorded in `missing` and the other paths are still walked; otherwise the first missing
        path raises FolderMissing, so a sync never runs while a folder is absent.
        """
        result = Walk()
        for source in sources:
            if result.truncated:
                break
            try:
                self.walk(source, include, exclude, limit=limit, truncate=truncate, max_entries=max_entries, into=result)
            except FolderMissing:
                if not truncate:
                    raise
                result.missing.append(source)
        return result

    def read(self, source: str, path: str, limit: int) -> bytes:
        *parents, name = relative_path(path).split('/')
        with self._directory(source, parents) as fd:
            leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(leaf, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise HTTPException(400, 'Only regular files are supported')
                data = stream.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(400, f'File exceeds the configured size limit: {path}')
        return data

    def write(self, source: str, path: str, data: bytes) -> None:
        if not self.writable(source):
            raise HTTPException(400, 'This collection folder is read-only; only rag-data collections accept files')
        *parents, name = relative_path(path).split('/')
        refuse_hidden(path)
        with self._directory(source, parents, create=True) as fd:
            temporary = f'.upload-{uuid4().hex}'
            try:
                leaf = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=fd)
                with os.fdopen(leaf, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
