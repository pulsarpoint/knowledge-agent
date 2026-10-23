"""The built-in tools: each is a function marked @tool in knowledge_admin.capabilities (its signature the arguments,
its docstring the usage text) plus its attributes from tools/tools.json (group, label, summary, order).
tools/sdk.py turns an entry into an Agents SDK tool."""

import typing
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

# Imported for their @tool registrations.
from knowledge_admin.capabilities import (  # noqa: F401
    ast_grep,
    code_graph,
    code_search,
    discovery,
    document_tools,
    documents,
    files,
    inventory,
    inventory_tools,
    memory,
    skills,
    ssh,
    subagents,
    wakeups,
)
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.shell import client as shell  # noqa: F401
from knowledge_admin.tools.fixture import load_fixture
from knowledge_admin.tools.registry import REGISTRY, Tool

Group = Literal['code', 'documents', 'servers', 'inventory', 'memory', 'agents']
GROUPS = typing.get_args(Group)
SUBAGENT = 'run_subagent'
WAKEUPS = frozenset({'schedule_wakeup', 'list_wakeups', 'cancel_wakeup'})  # a session turn's own tools
# Inventory writes: only a session turn (audited with its session), never a subagent or an expert.
INVENTORY_WRITES = frozenset(
    {
        'save_environment',
        'save_server',
        'save_service',
        'save_deployment',
        'save_connection',
        'save_relation',
        'remove_inventory_item',
        'save_inventory_draft',
    }
)
# Document writes: only a session turn (its drafts are the session's), never a subagent or an expert.
DOCUMENT_WRITES = frozenset({'new_document', 'update_document', 'edit_document'})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    group: Group
    label: str
    summary: str
    usage: str
    arguments: type[BaseModel]
    run: Callable[[RunContext, BaseModel], Awaitable[str]]


def load_catalog(entries: list[dict[str, Any]], registry: Mapping[str, Tool]) -> dict[str, ToolSpec]:
    """Join the fixture's attributes with the registered functions, in fixture order; any disagreement raises."""
    catalog: dict[str, ToolSpec] = {}
    for entry in entries:
        name = entry['name']
        if name in catalog:
            raise ValueError(f'tools.json lists {name} twice.')
        if name not in registry:
            raise ValueError(f'tools.json entry has no registered function: {name}')
        if entry['group'] not in GROUPS:
            raise ValueError(f'tools.json {name}: unknown group {entry["group"]!r}')
        found = registry[name]
        catalog[name] = ToolSpec(
            name,
            entry['group'],
            entry['label'],
            entry['summary'],
            found.usage,
            found.arguments,
            found.run,
        )
    missing = sorted(set(registry) - set(catalog))
    if missing:
        raise ValueError(f'Registered tools not in tools.json: {", ".join(missing)}')
    return catalog


CATALOG: dict[str, ToolSpec] = load_catalog(load_fixture(), REGISTRY)
