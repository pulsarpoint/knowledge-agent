"""Read and search the agent's selected code/document folders and workspace."""

from agents import RunContextWrapper, function_tool

from knowledge_admin.capabilities import files
from knowledge_admin.tools import AgentContext


@function_tool
async def list_files(ctx: RunContextWrapper[AgentContext], path: str | None = None,
                     offset: int = 0, limit: int = 200) -> str:
    """List accessible roots or the immediate children of one selected folder.

    Args:
        path: Absolute folder path. Omit it to discover accessible root folders.
        offset: Number of directory entries to skip for pagination.
        limit: Maximum entries to return, from 1 to 1000.
    """
    return await files.list_files(ctx.context, path, offset, limit)


@function_tool
async def read_file(ctx: RunContextWrapper[AgentContext], path: str,
                    offset: int = 0, max_bytes: int = 32768,
                    start_line: int | None = None, end_line: int | None = None) -> str:
    """Read a source line range or a paginated UTF-8 byte excerpt from a selected file.

    Args:
        path: Absolute file path from the configured folders or workspace.
        offset: Byte offset; use next_offset from an earlier result to continue.
        max_bytes: Maximum bytes in byte mode, from 1 to 65536.
        start_line: First line (1-based) from a graph symbol or search result. Supply end_line too.
        end_line: Last line, inclusive. Line mode returns this range with line numbers; offset must be zero.
    """
    return await files.read_file(ctx.context, path, offset, max_bytes, start_line, end_line)


@function_tool
async def search_files(ctx: RunContextWrapper[AgentContext], path: str, query: str,
                       case_sensitive: bool = False, max_matches: int = 100) -> str:
    """Search literal text in selected files using ripgrep, with file/line citations.

    Git ignore rules apply; hidden files and symlinks within folders are skipped.
    A truncated result can be narrowed by searching a subfolder or a longer query.

    Args:
        path: Absolute file or folder path within the configured roots.
        query: Literal text to find, not a regular expression; use a single line.
        case_sensitive: Whether letter case must match.
        max_matches: Maximum matching lines to return, from 1 to 500.
    """
    return await files.search_files(ctx.context, path, query, case_sensitive, max_matches)
