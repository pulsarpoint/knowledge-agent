"""Query saved source graphs through CodeGraph's public JavaScript SDK."""

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from knowledge_admin.agent_activity import redact
from knowledge_admin.capabilities.context import RunContext, require_given
from knowledge_admin.code_paths import code_root
from knowledge_admin.code_processing import CodeProcessingError, index_directory, source_inventory


def load_code_graph(project: dict, expected_hash: str | None = None) -> dict:
    """Require an explicitly generated graph that still describes the selected source."""
    directory = index_directory(project['id'])
    manifest = directory / 'manifest.json'
    if not manifest.is_file():
        raise CodeProcessingError('No code graph yet. Generate the graph in Code projects first.')
    saved = json.loads(manifest.read_text())
    current = source_inventory(project)
    if saved['source_hash'] != current['source_hash'] or expected_hash != saved['source_hash']:
        raise CodeProcessingError('Source or graph selection changed. Generate and inspect the graph again before continuing.')
    graph_path = directory / saved['source_hash'] / 'graph.sqlite'
    if not graph_path.is_file():
        raise CodeProcessingError('The graph snapshot is unavailable. Generate the graph again.')
    root = code_root()
    return {key: value for key, value in {**saved, 'name': project['name'],
        'folder_paths': [str(root / p) for p in current['folders']],
        'code_root': str(root / saved.get('relative_root', '.')), 'graph_path': str(graph_path)}.items() if key != 'files'}


async def code_graph(context: RunContext, project_id: str,
                     action: Literal['overview', 'search', 'routes', 'code', 'context', 'callers', 'callees', 'traverse', 'unresolved'] = 'overview',
                     query: str = '', node_id: str = '', offset: int = 0, limit: int = 50,
                     include_code: bool = False) -> str:
    config = context['config']
    if not config['enabled'] or not config['code']['enabled']:
        raise ValueError('Code access is disabled.')
    require_given(config, 'code')
    graph = next((g for g in config['code'].get('graphs', []) if g['id'] == project_id), None)
    if graph is None:
        raise ValueError('Choose a configured code graph project.')
    if offset < 0 or not 1 <= limit <= 100 or len(query) > 300:
        raise ValueError('Use a nonnegative offset, a limit from 1 to 100 and a short symbol query.')
    if action == 'overview':
        result = {key: value for key, value in graph.items() if key != 'graph_path'}
    else:
        if action not in ('search', 'routes', 'unresolved') and not node_id:
            raise ValueError('Choose a node ID from symbol search.')
        bridge = Path(os.environ.get('CODEGRAPH_QUERY_BRIDGE', str(
            Path(__file__).resolve().parents[4] / 'code-graph/query.cjs')))
        with TemporaryDirectory(prefix='knowledge-codegraph-query-') as directory:
            request = Path(directory) / 'request.json'
            output = Path(directory) / 'result.json'
            request.write_text(json.dumps({'graph': graph, 'action': action, 'query': query,
                'node_id': node_id, 'offset': offset, 'limit': limit, 'include_code': include_code}), encoding='utf-8')
            # No model credentials, project config, auto-sync or telemetry in this process.
            env = {'PATH': os.environ.get('PATH', ''), 'HOME': directory,
                   'CODEGRAPH_TELEMETRY': '0', 'DO_NOT_TRACK': '1', 'CODEGRAPH_NO_DAEMON': '1',
                   'NODE_NO_WARNINGS': '1', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
            process = await asyncio.create_subprocess_exec('node', str(bridge), str(request), str(output),
                cwd=directory, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
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
        result.update(source_hash=graph['source_hash'], path_base=graph['code_root'],
            note='CodeGraph SDK results. An absent or heuristic edge is not proof of runtime behavior; verify source and unresolved calls.')
    return redact(json.dumps(result), context['credentials'].values())
