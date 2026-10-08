"""run_subagent: a native agent hands a separate part of its work to a helper (a subagent) that runs in parallel with
the tools and resources it is given and without the conversation. Subagents run only in native agent sessions (Test
and Generate documents), whose runner puts the turn's Subagents into the run context."""
from typing import Any

UNAVAILABLE = 'Subagents run only in agent sessions.'


async def run_subagent(context: Any, task: str, tools: list[str], resources: dict[str, list[str]] | None) -> str:
    """The catalog tool: the subagent's final answer, or a ValueError the adapter returns as a tool error."""
    host = context.get('subagents')
    if host is None:
        raise ValueError(UNAVAILABLE)
    return await host.run(context, task, tools, resources)
