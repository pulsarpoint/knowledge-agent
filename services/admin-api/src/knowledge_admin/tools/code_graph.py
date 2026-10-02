"""Query saved source graphs through CodeGraph's public JavaScript SDK."""

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from agents import RunContextWrapper, function_tool

from knowledge_admin.agent_activity import redact
from knowledge_admin.tools import AgentContext


@function_tool
async def code_graph(ctx: RunContextWrapper[AgentContext], project_id: str,
                     action: Literal['overview', 'search', 'routes', 'code', 'context', 'callers', 'callees', 'traverse', 'unresolved'] = 'overview',
                     query: str = '', node_id: str = '', offset: int = 0, limit: int = 50,
                     include_code: bool = False) -> str:
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

    Args:
        project_id: Code graph project ID from the supplied agent resources.
        action: Overview, search, routes, code, context, callers, callees, traverse or unresolved.
        query: Symbol name or text query for search; empty lists all indexed nodes.
        node_id: Exact symbol ID from search; required except overview/search/routes and global unresolved.
        offset: Nonnegative pagination offset; continue using next_offset.
        limit: Maximum returned entries, from 1 to 100.
        include_code: Include full source for returned nodes; code action always includes it.
    """
    config = ctx.context['config']
    if not config['enabled'] or not config['code']['enabled']:
        raise ValueError('Code access is disabled.')
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
                error = redact(stderr.decode(errors='replace'), ctx.context['credentials'].values()).strip()
                raise ValueError(error or 'CodeGraph query failed. Check the graph snapshot and SDK installation.')
            result = json.loads(output.read_text(encoding='utf-8'))
        result.update(source_hash=graph['source_hash'], path_base=graph['code_root'],
            note='CodeGraph SDK results. An absent or heuristic edge is not proof of runtime behavior; verify source and unresolved calls.')
    return redact(json.dumps(result), ctx.context['credentials'].values())
