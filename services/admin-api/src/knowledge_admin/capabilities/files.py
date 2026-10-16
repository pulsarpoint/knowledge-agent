"""Read and search the agent's selected code/document folders and workspace."""

import asyncio
import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.sessions.activity import redact
from knowledge_admin.tools.registry import tool

logger = logging.getLogger(__name__)


def mounted(path: Path) -> bool:
    """An absolute, canonical path to an existing directory."""
    return path.is_absolute() and path.resolve() == path and path.is_dir()


def available_documents(paths: Iterable[Path]) -> list[Path]:
    """Document collection paths that are mounted. A collection path may be missing (e.g. until its
    source writes it); it is skipped with a warning instead of disabling the agent."""
    available = []
    for path in paths:
        if mounted(path):
            available.append(path)
        else:
            logger.warning('Skipping unavailable document collection folder: %s', path)
    return available


def _roots(context: RunContext) -> list[Path]:
    """The workspace and the folders of the run's code projects and collections (their access entries)."""
    config = context['config']
    if not config['enabled']:
        raise ValueError('This agent is disabled.')
    access = config['access']
    paths = [context['workspace']]  # the workspace and code folders must be mounted
    paths.extend(
        Path(path) for project in access_rules.listing(access, 'code_project') for path in project['folder_paths']
    )
    for path in paths:
        if not mounted(path):
            raise ValueError(f'Configured folder is unavailable: {path}')
    paths.extend(
        available_documents(Path(path) for item in access_rules.listing(access, 'collection') for path in item['paths'])
    )
    return list(dict.fromkeys(paths))


def check_list_files(access: dict, **_) -> str | None:
    """None when allowed, else the refusal text. Which paths: _path, inside the folders of these entries."""
    if not access_rules.has_any(access, 'code_project') and not access_rules.has_any(access, 'collection'):
        return access_rules.refuse_none('code_project', 'collection')
    return None


def check_read_file(access: dict, **_) -> str | None:
    """None when allowed, else the refusal text."""
    return check_list_files(access)


def check_search_files(access: dict, **_) -> str | None:
    """None when allowed, else the refusal text."""
    return check_list_files(access)


def _path(context: RunContext, path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError('Use an absolute path from list_files.')
    candidate = candidate.resolve()
    if not any(candidate.is_relative_to(root) for root in _roots(context)):
        raise ValueError('This path is outside the agent’s selected folders.')
    if not candidate.exists():
        raise ValueError('The requested path does not exist.')
    return candidate


@tool
async def list_files(
    context: RunContext,
    path: Annotated[
        str | None, Field(description='Absolute folder path. Omit it to discover accessible root folders.')
    ] = None,
    offset: Annotated[int, Field(description='Number of directory entries to skip for pagination.')] = 0,
    limit: Annotated[int, Field(description='Maximum entries to return, from 1 to 1000.')] = 200,
) -> str:
    """List accessible roots or the immediate children of one selected folder."""
    if refusal := check_list_files(context['config']['access'], path=path):
        return refusal
    if offset < 0 or not 1 <= limit <= 1000:
        raise ValueError('Use a nonnegative offset and a limit from 1 to 1000.')
    if path is None:
        entries = [{'path': str(root), 'type': 'directory'} for root in _roots(context)]
    else:
        directory = _path(context, path)
        if not directory.is_dir():
            raise ValueError('Choose a directory to list.')
        roots = _roots(context)
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            resolved = child.resolve()
            if not any(resolved.is_relative_to(root) for root in roots):
                continue
            if child.is_dir() or child.is_file():
                entries.append({'path': str(child), 'type': 'directory' if child.is_dir() else 'file'})
    next_offset = offset + limit if offset + limit < len(entries) else None
    return json.dumps({'entries': entries[offset : offset + limit], 'next_offset': next_offset})


@tool
async def read_file(
    context: RunContext,
    path: Annotated[str, Field(description='Absolute file path from the configured folders or workspace.')],
    offset: Annotated[int, Field(description='Byte offset; use next_offset from an earlier result to continue.')] = 0,
    max_bytes: Annotated[int, Field(description='Maximum bytes in byte mode, from 1 to 65536.')] = 32768,
    start_line: Annotated[
        int | None, Field(description='First line (1-based) from a graph symbol or search result. Supply end_line too.')
    ] = None,
    end_line: Annotated[
        int | None,
        Field(description='Last line, inclusive. Line mode returns this range with line numbers; offset must be zero.'),
    ] = None,
) -> str:
    """Read a source line range or a paginated UTF-8 byte excerpt from a selected file.

    Use read_file for surrounding comments, imports and targeted ranges."""
    if refusal := check_read_file(context['config']['access'], path=path):
        return refusal
    if offset < 0 or not 1 <= max_bytes <= 65536:
        raise ValueError('Use a nonnegative offset and max_bytes from 1 to 65536.')
    selected = _path(context, path)
    if not selected.is_file():
        raise ValueError('Choose a regular text file.')
    if start_line is not None or end_line is not None:
        if start_line is None or end_line is None or start_line < 1 or end_line < start_line or offset != 0:
            raise ValueError('Supply start_line and end_line with 1 <= start_line <= end_line, and offset zero.')
        with selected.open('rb') as stream:
            if b'\0' in stream.read(8192):
                raise ValueError('This is a binary file; choose a text file.')
        with selected.open(encoding='utf-8', errors='replace') as stream:
            content = []
            for number, line in enumerate(stream, 1):
                if number > end_line:
                    break
                if number >= start_line:
                    content.append(f'{number}: {line}')
        return json.dumps(
            {
                'path': str(selected),
                'start_line': start_line,
                'end_line': start_line + len(content) - 1 if content else None,
                'content': redact(''.join(content), context['credentials'].values()),
            }
        )
    with selected.open('rb') as stream:
        if b'\0' in stream.read(8192):
            raise ValueError('This is a binary file; choose a text file.')
        stream.seek(offset)
        data = stream.read(max_bytes)
        more = bool(stream.read(1))
    return json.dumps(
        {
            'path': str(selected),
            'offset': offset,
            'content': redact(data.decode('utf-8', errors='replace'), context['credentials'].values()),
            'next_offset': offset + len(data) if more else None,
        }
    )


@tool
async def search_files(
    context: RunContext,
    path: Annotated[str, Field(description='Absolute file or folder path within the configured roots.')],
    query: Annotated[str, Field(description='Literal text to find, not a regular expression; use a single line.')],
    case_sensitive: Annotated[bool, Field(description='Whether letter case must match.')] = False,
    max_matches: Annotated[int, Field(description='Maximum matching lines to return, from 1 to 500.')] = 100,
) -> str:
    """Search literal text in selected files using ripgrep, with file/line citations.

    Git ignore rules apply; hidden files and symlinks within folders are skipped.
    A truncated result can be narrowed by searching a subfolder or a longer query.

    Use search_files for text discovery."""
    if refusal := check_search_files(context['config']['access'], path=path):
        return refusal
    if not query or '\n' in query or '\0' in query or len(query) > 4000 or not 1 <= max_matches <= 500:
        raise ValueError('Provide a single-line query up to 4000 characters and max_matches from 1 to 500.')
    selected = _path(context, path)
    if not selected.is_dir() and not selected.is_file():
        raise ValueError('Choose a regular file or directory.')
    args = [
        'rg',
        '--line-number',
        '--with-filename',
        '--fixed-strings',
        '--color',
        'never',
        '--max-columns',
        '2000',
        '--max-columns-preview',
    ]
    if not case_sensitive:
        args.append('--ignore-case')
    process = await asyncio.create_subprocess_exec(
        *args, '--', query, str(selected), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    matches = []
    truncated = False
    timed_out = False
    try:
        async with asyncio.timeout(15):
            while line := await process.stdout.readline():
                if len(matches) == max_matches:
                    truncated = True
                    break
                matches.append(line.decode('utf-8', errors='replace').rstrip('\n'))
            if not truncated:
                await process.wait()
    except TimeoutError:
        timed_out = True
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()
    return json.dumps(
        {
            'matches': [redact(line, context['credentials'].values()) for line in matches],
            'truncated': truncated,
            'timed_out': timed_out,
            'error': 'Some files could not be searched.'
            if process.returncode not in (0, 1) and not truncated and not timed_out
            else None,
        }
    )
