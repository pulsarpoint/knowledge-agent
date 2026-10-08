"""Find named functions and inspect their syntax using the installed Tree-sitter engine."""

from typing import Literal

from agents import RunContextWrapper, function_tool

from knowledge_admin.capabilities import code_search as capability
from knowledge_admin.tools import AgentContext


@function_tool
async def code_search(ctx: RunContextWrapper[AgentContext], path: str, language: str,
                      name: str, match: Literal['exact', 'contains'] = 'exact',
                      offset: int = 0, limit: int = 5) -> str:
    """Find functions/methods by name and return complete bodies, imports and call sites.

    Use this before reading whole files. Example: language="Go",
    name="SetPersonalLimits", path=<selected project folder>. Search a narrower
    file/folder to distinguish same-named symbols; receivers are included for Go.
    Supports Go, Python, JavaScript, TypeScript and Tsx. Includes named functions,
    methods, and JS/TS variables assigned arrow functions or function expressions.
    For classes, anonymous callbacks or custom patterns, use ast_grep.

    Calls are syntactic expressions inside each definition, including nested
    closures. They are NOT resolved targets or a guaranteed execution sequence.
    Follow package aliases using the returned imports; use code_graph for indexed
    callees and verify uncertain edges. Calls may include type conversions.
    Bodies are never shortened. Calls and imports use source locations, without
    repeating bodies/arguments. Results are sorted and paginated; follow next_offset.
    Git ignores apply to directory searches; symlinks are not followed. Empty
    results do not prove absence (other declaration forms/languages may need ast_grep).
    No build, code execution, LLM, graph generation, or dependencies are required.

    Args:
        path: Absolute source file or directory in a selected code project.
        language: Go, Python, JavaScript, TypeScript or Tsx (case-insensitive).
        name: Literal function/method name, not a regular expression.
        match: Exact name or substring match.
        offset: Number of matching declarations to skip, starting at zero.
        limit: Maximum complete declarations returned, from 1 to 20.
    """
    return await capability.code_search(ctx.context, path, language, name, match, offset, limit)
