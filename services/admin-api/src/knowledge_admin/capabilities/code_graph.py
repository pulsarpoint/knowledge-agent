"""Query saved source graphs through CodeGraph's public JavaScript SDK."""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

from pydantic import Field

from knowledge_admin import access as access_rules
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.inventory import connect, project_of
from knowledge_admin.code_projects.paths import code_root, rebase_code_path
from knowledge_admin.code_projects.processing import CodeProcessingError, index_directory, source_inventory
from knowledge_admin.knowledge_agents.config import load_graphs
from knowledge_admin.sessions.activity import redact
from knowledge_admin.tools.registry import tool


def load_code_graph_hashing(project: dict, expected_hash: str | None = None) -> dict:
    """Require an explicitly generated graph that still describes the selected source, hashing every source file to
    check it. For the benchmark export and tests only: loading a configuration must not use it (it reads all source
    files); configurations use current_graph."""
    directory = index_directory(project['id'])
    manifest = directory / 'manifest.json'
    if not manifest.is_file():
        raise CodeProcessingError('No code graph yet. Generate the graph in Code projects first.')
    saved = json.loads(manifest.read_text())
    current = source_inventory(project)
    if saved['source_hash'] != current['source_hash'] or expected_hash != saved['source_hash']:
        raise CodeProcessingError(
            'Source or graph selection changed. Generate and inspect the graph again before continuing.'
        )
    graph_path = directory / saved['source_hash'] / 'graph.sqlite'
    if not graph_path.is_file():
        raise CodeProcessingError('The graph snapshot is unavailable. Generate the graph again.')
    root = code_root()
    return {
        key: value
        for key, value in {
            **saved,
            'name': project['name'],
            'folder_paths': [str(root / p) for p in current['folders']],
            'code_root': str(root / saved.get('relative_root', '.')),
            'graph_path': str(graph_path),
        }.items()
        if key != 'files'
    }


SYNCED_AFTER = 'The code was synced after this graph was generated; regenerate it or confirm with read_file.'
SOURCE_HASH = re.compile(r'[0-9a-f]{64}')
USABLE = ('ready', 'partial')  # generation statuses with a graph worth querying


def _relative_folders(paths, root: PurePosixPath) -> list[str]:
    """Folder paths as source_inventory names them: relative to CODE_ROOT, legacy /work/code paths rebased."""
    folders = set()
    for value in paths:
        path = PurePosixPath(rebase_code_path(value))
        folders.add(str(path.relative_to(root)) if path.is_absolute() and path.is_relative_to(root) else value)
    return sorted(folders)


def _generated_at(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def current_graph(project: dict) -> dict | None:
    """The project's generated graph as the code_graph and shell tools read it, decided from what generation stored
    (the manifest and the snapshot it names) without reading or hashing source files; None when it was never
    generated, generation did not end ready or partial, its snapshot is gone, the manifest is not one generation
    writes, the folder selection changed since, or the index or code mount is not configured. Source edits are not detected here: code queries compare each file
    with its indexed hash, and a sync after generation (project's synced_at) adds a note.
    project: id, name, folder_paths (absolute under CODE_ROOT or relative to it) and optionally synced_at."""
    root = PurePosixPath(os.environ.get('CODE_ROOT', ''))
    if root.anchor != '/':
        return None
    try:
        directory = index_directory(project['id'])
        manifest = directory / 'manifest.json'
        if not manifest.is_file():
            return None
        saved = json.loads(manifest.read_text())
        if saved.get('status') not in USABLE:
            return None
        source_hash, relative_root = saved['source_hash'], PurePosixPath(saved.get('relative_root', '.'))
        if (
            not isinstance(source_hash, str)
            or not SOURCE_HASH.fullmatch(source_hash)
            or relative_root.is_absolute()
            or '..' in relative_root.parts
        ):
            return None
        graph_path = directory / source_hash / 'graph.sqlite'
        if not graph_path.is_file():
            return None
        folders = _relative_folders(saved['folder_paths'], root)
        if folders != _relative_folders(project['folder_paths'], root):
            return None
    except (CodeProcessingError, OSError, ValueError, KeyError, TypeError):
        return None
    graph = {
        'id': str(project['id']),
        'name': project['name'],
        'graph_path': str(graph_path),
        'code_root': str(root / relative_root),
        'source_hash': source_hash,
        'folder_paths': [str(root / folder) for folder in folders],
        'generated_at': saved.get('processed_at'),
    }
    generated, synced = _generated_at(saved.get('processed_at')), project.get('synced_at')
    if generated is not None and synced is not None and synced > generated:
        graph['note'] = SYNCED_AFTER
    return graph


def check_code_graph(access: dict, project_id: str, **_) -> str | None:
    """None when allowed, else the refusal text."""
    if not access_rules.has_any(access, 'code_project'):
        return access_rules.refuse_none('code_project')
    if not access_rules.can_use(access, 'code_project', project_id):
        return access_rules.refuse_use('code_project')
    return None


@tool
async def code_graph(
    context: RunContext,
    project_id: Annotated[
        str, Field(description='Code graph project ID: a code project ID from get_available_resources.')
    ],
    action: Annotated[
        Literal['overview', 'search', 'routes', 'code', 'context', 'callers', 'callees', 'traverse', 'unresolved'],
        Field(description='Overview, search, routes, code, context, callers, callees, traverse or unresolved.'),
    ] = 'overview',
    query: Annotated[
        str, Field(description='Symbol name or text query for search; empty lists all indexed nodes.')
    ] = '',
    node_id: Annotated[
        str,
        Field(description='Exact symbol ID from search; required except overview/search/routes and global unresolved.'),
    ] = '',
    offset: Annotated[int, Field(description='Nonnegative pagination offset; continue using next_offset.')] = 0,
    limit: Annotated[int, Field(description='Maximum returned entries, from 1 to 100.')] = 50,
    include_code: Annotated[
        bool, Field(description='Include full source for returned nodes; code action always includes it.')
    ] = False,
) -> str:
    """Search and explore indexed source with CodeGraph's own SDK functions.

    Start with search; it returns every exact-name overload, or text-search hits
    when there is no exact name. Use code for a complete function body, context
    for imports/types/references, and callees/callers for direct relationships.
    Traverse follows all reachable outgoing calls; results are paginated, with
    edges grouped by their source node on each page. Continue with next_offset.
    Routes lists the indexed API registrations. Unresolved can be scoped to a node.

    Nodes and edges retain native CodeGraph fields (filePath, startLine, metadata).
    File paths are relative to path_base. Code is read with getCode only after its
    file hash matches the index. Changed/missing source is reported, not silently
    paired with stale symbol ranges. No code is shortened. Edge confidence and
    resolvedBy are evidence, not guarantees: verify import/receiver resolution,
    branches, middleware, SQL and side effects. A missing edge proves no absence.
    These operations do not use an LLM, re-index, or modify the saved graph/source.

    Use code_graph first: search for symbols, code for full bodies, context for imports/types, and traverse for
    reachable calls. It uses the CodeGraph SDK directly; follow pagination and inspect edge metadata and unresolved
    references. Then verify business rules, state changes, SQL and external effects with source-file tools. Graph paths
    are relative to CODE_ROOT, as returned by the tool. Static edges are evidence, not proof of runtime execution."""
    config = context['config']
    if refusal := check_code_graph(config['access'], project_id=project_id):
        return refusal

    def current() -> dict | None:  # the code project's current graph (live, like file contents)
        with connect() as conn:
            return next(iter(load_graphs(conn, project_of(conn, config), [project_id])), None)

    graph = await asyncio.to_thread(current)
    if graph is None:
        raise ValueError('Choose a configured code graph project.')
    if offset < 0 or not 1 <= limit <= 100 or len(query) > 300:
        raise ValueError('Use a nonnegative offset, a limit from 1 to 100 and a short symbol query.')
    if action == 'overview':
        result = {key: value for key, value in graph.items() if key != 'graph_path'}
    else:
        if action not in ('search', 'routes', 'unresolved') and not node_id:
            raise ValueError('Choose a node ID from symbol search.')
        bridge = Path(
            os.environ.get('CODEGRAPH_QUERY_BRIDGE', str(Path(__file__).resolve().parents[4] / 'code-graph/query.cjs'))
        )
        with TemporaryDirectory(prefix='knowledge-codegraph-query-') as directory:
            request = Path(directory) / 'request.json'
            output = Path(directory) / 'result.json'
            request.write_text(
                json.dumps(
                    {
                        'graph': graph,
                        'action': action,
                        'query': query,
                        'node_id': node_id,
                        'offset': offset,
                        'limit': limit,
                        'include_code': include_code,
                    }
                ),
                encoding='utf-8',
            )
            # No model credentials, project config, auto-sync or telemetry in this process.
            env = {
                'PATH': os.environ.get('PATH', ''),
                'HOME': directory,
                'CODEGRAPH_TELEMETRY': '0',
                'DO_NOT_TRACK': '1',
                'CODEGRAPH_NO_DAEMON': '1',
                'NODE_NO_WARNINGS': '1',
                'GIT_CONFIG_NOSYSTEM': '1',
                'GIT_CONFIG_GLOBAL': os.devnull,
            }
            process = await asyncio.create_subprocess_exec(
                'node',
                str(bridge),
                str(request),
                str(output),
                cwd=directory,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(60):
                    _, stderr = await process.communicate()
            except TimeoutError:
                raise ValueError('CodeGraph query timed out. Try a specific symbol or direct callees.') from None
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.communicate()
            if process.returncode != 0 or not output.is_file():
                error = redact(stderr.decode(errors='replace'), context['credentials'].values()).strip()
                raise ValueError(error or 'CodeGraph query failed. Check the graph snapshot and SDK installation.')
            result = json.loads(output.read_text(encoding='utf-8'))
        note = 'CodeGraph SDK results. An absent or heuristic edge is not proof of runtime behavior; verify source and unresolved calls.'
        result.update(
            source_hash=graph['source_hash'],
            path_base=graph['code_root'],
            generated_at=graph.get('generated_at'),
            note=f'{graph["note"]} {note}' if graph.get('note') else note,
        )
    return redact(json.dumps(result), context['credentials'].values())
