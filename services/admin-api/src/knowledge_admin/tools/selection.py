"""Tool selection: the catalog tools a run never offers. A saved agent's off tools (ka.agents.disabled_tools) stay off
in every run of it; a run's request can switch more off, never one back on. Off tools are stored, so a tool added to
the catalog later is on. agents/runner.instructions_and_tools withholds config['disabled_tools']."""

from collections.abc import Iterable

from knowledge_admin.tools.catalog import CATALOG

UNKNOWN = 'Unknown tool: {name}.'


def validate(names: Iterable[str]) -> list[str]:
    """The names, each once, sorted. ValueError (UNKNOWN) for a name that is not a catalog tool."""
    names = list(names)
    for name in names:
        if name not in CATALOG:
            raise ValueError(UNKNOWN.format(name=name))
    return sorted(set(names))


def disabled_for(agent_disabled: Iterable[str], requested: Iterable[str]) -> frozenset[str]:
    """The tools a run switches off: its agent's and the request's (an ad-hoc run has no agent's: ()). Subagents and
    experts get their caller's set this way too (an expert with its own), so never fewer are off."""
    return frozenset(agent_disabled) | frozenset(requested)
