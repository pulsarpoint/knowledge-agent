"""Bounded, read-only access to tracked source files in an allowed repository."""
import os
import subprocess
import time
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException, Query
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import load_profile
from code_agent.config import Repo

MAX_FILE_BYTES = 512_000


def source_path(root: Path, path: str) -> Path:
    if not path or '\\' in path or '\x00' in path or PurePosixPath(path).is_absolute() or any(p in ('', '.', '..', '.git') for p in path.split('/')):
        raise HTTPException(400, 'Use a relative repository file path.')
    try:
        resolved = (root / path).resolve(strict=True)
    except OSError:
        raise HTTPException(404, 'Source file not found.') from None
    if resolved != root.resolve() / path or not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise HTTPException(403, 'Path is outside the repository.')
    return resolved


def tracked_files(root: Path) -> list[str]:
    # The checkout is read-only. Git's index excludes ignored local configuration and .git internals.
    try:
        output = subprocess.run(['git', '-c', f'safe.directory={root.resolve()}', '-c', 'core.fsmonitor=false',
            '-c', 'core.hooksPath=/dev/null', '-C', str(root), 'ls-files', '-z', '--cached'],
            check=True, capture_output=True, timeout=10).stdout
    except (subprocess.SubprocessError, OSError):
        raise HTTPException(503, 'Source inventory is unavailable.') from None
    if len(output) > 8_000_000:
        raise HTTPException(413, 'Repository inventory exceeds the source browsing limit.')
    return sorted(set(output.decode('utf-8', errors='replace').strip('\x00').split('\x00'))) if output else []


def read_text(root: Path, path: str) -> str:
    target = source_path(root, path)
    if target.stat().st_size > MAX_FILE_BYTES:
        raise HTTPException(413, 'Source file is too large to read.')
    data = target.read_bytes()
    if len(data) > MAX_FILE_BYTES or b'\x00' in data:
        raise HTTPException(400, 'Use a text source file below 512 KB.')
    return data.decode('utf-8', errors='replace')


def project_files(repo: Repo) -> list[str]:
    if repo.folder_paths is None:
        return tracked_files(repo.path)
    paths = set()
    for folder in repo.folder_paths:
        root = repo.path / folder
        if root.resolve() != root or not root.is_dir():
            continue
        if (root / '.git').exists():
            paths.update(f'{folder}/{path}' for path in tracked_files(root))
        else:
            for directory, folders, files in os.walk(root, followlinks=False):
                folders[:] = [name for name in folders if name not in ('.git', '.ka-sync') and not (Path(directory)/name).is_symlink()]
                paths.update(str((Path(directory)/name).relative_to(repo.path)) for name in files
                    if not (Path(directory)/name).is_symlink())
                if len(paths)>50000:
                    raise HTTPException(413, 'Code project inventory exceeds the source browsing limit.')
        if len(paths)>50000:
            raise HTTPException(413, 'Code project inventory exceeds the source browsing limit.')
    return sorted(paths)


def build_api(available_repos) -> APIRouter:
    router = APIRouter(prefix='/repos')

    def repository(name: str, agent_id: str, revision: int):
        try:
            selected = load_profile(agent_id)
        except ModelUnavailable as exc:
            raise HTTPException(409, str(exc)) from None
        if selected['revision'] != revision or not selected['repositories_enabled']:
            raise HTTPException(403, 'Repository access changed. Start a new run.')
        if not selected['all_repositories'] and name not in selected['repository_ids']:
            raise HTTPException(403, 'Repository is outside this agent configuration.')
        repo = next((repo for repo, _ in available_repos() if repo.name == name), None)
        if repo is None:
            raise HTTPException(404, 'Code project folders not found.')
        return repo

    @router.get('/{name}/files')
    def files(name: str, agent_id: str, revision: int, prefix: str = Query(default='', max_length=1000), offset: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500)):
        repo = repository(name, agent_id, revision)
        paths = [path for path in project_files(repo) if path.startswith(prefix)]
        return {'files': paths[offset:offset+limit], 'total': len(paths), 'next_offset': offset+limit if offset+limit<len(paths) else None}

    @router.get('/{name}/file')
    def file(name: str, agent_id: str, revision: int, path: str = Query(max_length=1000), start_line: int = Query(default=1, ge=1), line_count: int = Query(default=150, ge=1, le=400)):
        repo = repository(name, agent_id, revision)
        if path not in project_files(repo):
            raise HTTPException(404, 'Tracked source file not found.')
        lines = read_text(repo.path, path).splitlines()
        text = '\n'.join(f'{i+start_line}: {line}' for i,line in enumerate(lines[start_line-1:start_line-1+line_count]))
        return {'path': path, 'start_line': start_line, 'total_lines': len(lines), 'text': text[:60000], 'truncated': len(text)>60000}

    @router.get('/{name}/search')
    def search(name: str, agent_id: str, revision: int, query: str = Query(min_length=1,max_length=500), prefix: str = Query(default='',max_length=1000)):
        repo = repository(name, agent_id, revision)
        matches, total_bytes, truncated = [], 0, False
        deadline = time.monotonic()+8
        for path in project_files(repo):
            if not path.startswith(prefix):
                continue
            if time.monotonic()>deadline or total_bytes>10_000_000 or len(matches)>=100:
                truncated=True; break
            try:
                text = read_text(repo.path, path)
            except (HTTPException, OSError):
                continue
            total_bytes += len(text)
            for line_number,line in enumerate(text.splitlines(),1):
                if query.casefold() in line.casefold():
                    matches.append({'path': path, 'line': line_number, 'text': line[:1000]})
                    if len(matches)>=100:
                        truncated=True; break
        return {'matches': matches, 'truncated': truncated}
    return router
