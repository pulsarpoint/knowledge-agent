"""Query saved source graphs through CodeGraph's public JavaScript SDK."""

from typing import Literal

from agents import RunContextWrapper, function_tool

from knowledge_admin.capabilities import code_graph as capability
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
    return await capability.code_graph(ctx.context, project_id, action, query, node_id, offset, limit, include_code)
