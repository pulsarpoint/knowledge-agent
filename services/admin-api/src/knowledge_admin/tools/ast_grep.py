"""Read complete syntax nodes with the ast-grep CLI in selected source folders."""

from agents import RunContextWrapper, function_tool

from knowledge_admin.capabilities import ast_grep as capability
from knowledge_admin.tools import AgentContext


@function_tool
async def ast_grep(ctx: RunContextWrapper[AgentContext], path: str, language: str,
                   pattern: str | None = None, rule: str | None = None,
                   max_matches: int = 20) -> str:
    """Search source structure with ast-grep and return complete matched code and line ranges.

    Use for whole functions, methods, classes, and structural patterns. Use
    search_files for literal text and code_graph for callers/callees. This tool
    parses syntax, not semantic type resolution; verify receiver/package matches.
    Choose a source file or narrow folder from the configured code projects.
    Git ignores apply to directory searches; symlinks are not followed.

    Supply exactly one native pattern or a native rule object encoded as JSON.
    $NAME matches one syntax node; $$$NAME matches a sequence. Patterns depend on
    language and declaration shape; a method pattern need not match a free function.

    Go method body: language="Go",
    pattern="func ($RECEIVER) SetPersonalLimits($$$ARGS) { $$$BODY }".
    Go functions or methods by name, regardless of signature: language="Go",
    rule='{"any":[{"kind":"function_declaration"},{"kind":"method_declaration"}],
    "has":{"field":"name","regex":"^SetPersonalLimits$"}}'.
    Python function: language="Python", pattern="def process($$$ARGS): $$$BODY".

    Results contain the complete matched node once, including its signature/body
    when a whole declaration is matched; adjacent doc comments are not included.
    Use read_file for surrounding comments/imports or other source ranges. If
    truncated, narrow the folder/pattern or request more matches. Empty results
    do not prove a symbol is absent: check language, pattern shape, and ignores.

    Args:
        path: Absolute source file or folder within the configured roots.
        language: Built-in ast-grep language, such as Go, Python, JavaScript or TypeScript.
        pattern: Native ast-grep code pattern. Omit when supplying rule.
        rule: JSON rule object, e.g. {"kind":"function_declaration"}; omit with pattern.
            Supply only the rule object, not a full config, language, fix or rewrite.
        max_matches: Maximum complete matches to return, from 1 to 100.
    """
    return await capability.ast_grep(ctx.context, path, language, pattern, rule, max_matches)
