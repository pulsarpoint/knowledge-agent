"""The built-in tools: each is a function marked @tool in knowledge_admin.capabilities (its signature the arguments,
its docstring the usage text) plus its attributes from tools/tools.json (group, label, summary, needs, order).
tools/sdk.py turns an entry into an Agents SDK tool."""

import typing
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
    ssh,
)
from knowledge_admin.capabilities.context import ACCESS, RunContext
from knowledge_admin.capabilities.shell import client as shell  # noqa: F401
from knowledge_admin.tools.fixture import load_fixture
from knowledge_admin.tools.registry import REGISTRY, Tool, tool

Group = Literal['code', 'documents', 'servers', 'inventory', 'agents']
Need = Literal[
    'files',
    'code',
    'code_graph',
    'documents',
    'grounded',
    'ssh',
    'server_proposals',
    'resource_proposals',
    'editable_document',
    'resources',
]
GROUPS = typing.get_args(Group)
NEEDS = typing.get_args(Need)
SUBAGENT = 'run_subagent'


@dataclass(frozen=True)
class ToolSpec:
    name: str
    group: Group
    label: str
    summary: str
    usage: str
    arguments: type[BaseModel]
    run: Callable[[RunContext, BaseModel], Awaitable[str]]
    needs: Need | None


def _grounded(config: Mapping[str, Any]) -> bool:
    return bool(config['documents']['enabled'] and config['documents'].get('grounded', False))


def _files(config: Mapping[str, Any]) -> bool:
    return bool(config['code']['enabled'] or config['documents']['enabled'] and not _grounded(config))


def _code(config: Mapping[str, Any]) -> bool:
    return bool(config['code']['enabled'])


def _documents(config: Mapping[str, Any]) -> bool:
    return bool(config['documents']['enabled'])


def _ssh(config: Mapping[str, Any]) -> bool:
    return bool(config['ssh']['enabled'])


class SubagentResources(BaseModel):
    model_config = ConfigDict(extra='forbid')

    code_project_ids: list[str] = Field(default_factory=list, description='Code project IDs from your resources.')
    collection_ids: list[str] = Field(default_factory=list, description='Document collection IDs from your resources.')
    server_ids: list[str] = Field(default_factory=list, description='SSH server IDs from your resources.')
    service_ids: list[str] = Field(default_factory=list, description='Service IDs from your resources.')


@tool
async def run_subagent(
    context: RunContext,
    task: Annotated[
        str, Field(description='The complete task, up to 20000 characters: the helper does not see this conversation.')
    ],
    tools: Annotated[
        list[str], Field(description='Names of your tools the helper may use, as you see them; at least one.')
    ],
    resources: Annotated[
        SubagentResources | None,
        Field(description='The resources the helper may use, from yours; omit it to give all of yours.'),
    ] = None,
) -> str:
    """Start a helper for a separate, well-defined part of your work, such as exploring one project or checking one question. Give it a complete task, because it does not see this conversation. Give it the fewest tools and resources it needs. Helpers may run in parallel. You get their final answer as the result; verify important claims."""
    # Imported here: the subagent runtime imports the native session runtime, which imports this module.
    from knowledge_admin.knowledge_agents import subagents

    return await subagents.run_subagent(context, task, tools, resources)


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
        if entry['needs'] is not None and entry['needs'] not in NEEDS:
            raise ValueError(f'tools.json {name}: unknown needs {entry["needs"]!r}')
        found = registry[name]
        catalog[name] = ToolSpec(
            name,
            entry['group'],
            entry['label'],
            entry['summary'],
            found.usage,
            found.arguments,
            found.run,
            entry['needs'],
        )
    missing = sorted(set(registry) - set(catalog))
    if missing:
        raise ValueError(f'Registered tools not in tools.json: {", ".join(missing)}')
    return catalog


CATALOG: dict[str, ToolSpec] = load_catalog(load_fixture(), REGISTRY)

DOCUMENT_EDIT_TOOLS = ('save_document', 'edit_document')
PROPOSAL_TOOLS = ('propose_service', 'propose_document')
# Never chosen for a saved agent: a run is started from a resource (and asks for the proposal tools), documents are
# given, per run, and get_resource is on in every run that has resources (it is how the model reads them).
RUN_ONLY_NEEDS = ('server_proposals', 'resource_proposals', 'editable_document', 'resources')
# Where each proposal tool works: the resource types a run may start from to ask for it.
PROPOSAL_STARTS = {'server_proposals': ('server',), 'resource_proposals': ('server', 'service', 'environment')}
NEED_REASONS: dict[str, str] = {
    'files': 'Needs code projects, or documents without "Answer only from documents"',
    'code': 'Needs code projects',
    'code_graph': 'Needs code projects',
    'documents': 'Needs documents',
    'grounded': 'Needs "Answer only from documents"',
    'ssh': 'Needs SSH servers',
    'server_proposals': 'Needs a run started from a server that turns it on',
    'resource_proposals': 'Needs a run started from a server, service or environment that turns it on',
    'editable_document': 'Needs a document to edit',
    'resources': 'Always on for a run with servers, services, environments or code projects',
}
NEED_TABS: dict[str, str] = {
    'files': 'git',
    'code': 'git',
    'code_graph': 'git',
    'documents': 'documents',
    'grounded': 'documents',
    'ssh': 'ssh',
}


def needs_met(need: str | None, config: Mapping[str, Any], ssh_servers: list[dict[str, Any]] | None) -> bool:
    """Whether a tool can work in this configuration. ssh_servers None: judge by the capability switch only (the
    Tools tab); a list: the usable servers of this run. code_graph needs a graph at run time."""
    if need is None:
        return True
    if need == 'files':
        return _files(config)
    if need == 'code':
        return _code(config)
    if need == 'code_graph':
        return _code(config) and (ssh_servers is None or bool(config['code'].get('graphs')))
    if need == 'documents':
        return _documents(config)
    if need == 'grounded':
        return _grounded(config)
    if need in PROPOSAL_STARTS:  # only a run started from such a resource whose request turned the tool on
        started = config.get('started_from') or {}
        tool = 'propose_service' if need == 'server_proposals' else 'propose_document'
        return started.get('type') in PROPOSAL_STARTS[need] and tool in config.get('proposal_tools', [])
    if need == 'editable_document':
        return any(entry.get('editable') for entry in config.get('document_access', []))
    if need == 'resources':
        return has_resources(config)
    return _ssh(config) if ssh_servers is None else bool(ssh_servers)


def has_resources(config: Mapping[str, Any]) -> bool:
    """Whether the run has a server, service, environment or code project (get_resource's allowlist kinds)."""
    return any(ACCESS[kind](config) for kind in ('server', 'service', 'environment', 'code_project'))


def item(
    name: str, mode: str, usage: str | None = None, group: str | None = None, subagent_allowed: bool = True
) -> dict[str, Any]:
    return {
        'tool': name,
        'group': group or CATALOG[name].group,
        'mode': mode,
        'usage': usage,
        'subagent_allowed': subagent_allowed,
    }


def effective_tools(config: Mapping[str, Any], ssh_servers: list[dict[str, Any]]) -> list[tuple[ToolSpec, str]]:
    """The catalog tools this run offers, in catalog order, with their usage text: switched on, and their need met;
    get_resource (need resources) in every run that has resources, whatever its row says."""
    chosen = {entry['tool']: entry for entry in config['tools']['items']}
    offered = []
    for spec in CATALOG.values():
        if spec.needs == 'resources':
            if needs_met(spec.needs, config, ssh_servers):
                offered.append((spec, spec.usage))
            continue
        entry = chosen.get(spec.name)
        if entry is None or entry['mode'] != 'on' or not needs_met(spec.needs, config, ssh_servers):
            continue
        offered.append((spec, entry['usage'] or spec.usage))
    return offered


def _mcp_rows(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry['tool']: entry for entry in config['tools']['items'] if entry['tool'].startswith('mcp:')}


def mcp_blocked(config: Mapping[str, Any], server_id: str) -> list[str]:
    """The tool names of one MCP server the model must not get: switched off (or per run, not offered in step 1).
    A tool without a row is on."""
    prefix = f'mcp:{server_id}:'
    return sorted(
        name.removeprefix(prefix)
        for name, entry in _mcp_rows(config).items()
        if name.startswith(prefix) and entry['mode'] != 'on'
    )


def mcp_allowed(config: Mapping[str, Any], server_id: str) -> list[str]:
    """The tool names of one MCP server switched on in the configuration: a subagent's allowlist (subagents.py), so a
    tool without a row stays off."""
    prefix = f'mcp:{server_id}:'
    return sorted(
        name.removeprefix(prefix)
        for name, entry in _mcp_rows(config).items()
        if name.startswith(prefix) and entry['mode'] == 'on'
    )


def mcp_notes(config: Mapping[str, Any], servers: list[dict[str, Any]]) -> list[str]:
    """Edited usage of MCP tools that are on, as prompt lines: the description itself comes from the server."""
    rows = _mcp_rows(config)
    notes = []
    for server in servers:
        prefix = f'mcp:{server["id"]}:'
        for name in sorted(rows):
            entry = rows[name]
            if name.startswith(prefix) and entry['mode'] == 'on' and entry['usage']:
                notes.append(f'- {server["name"]}/{name.removeprefix(prefix)}: {entry["usage"]}')
    return notes


def catalog_listing() -> list[dict[str, Any]]:
    return [
        {
            'name': spec.name,
            'group': spec.group,
            'label': spec.label,
            'summary': spec.summary,
            'usage': spec.usage,
            'needs': spec.needs,
        }
        for spec in CATALOG.values()
        if spec.needs not in RUN_ONLY_NEEDS
    ]


CAPABILITY_NEEDS: dict[str, tuple[str, ...]] = {
    'code': ('files', 'code', 'code_graph'),
    'documents': ('documents', 'grounded'),
    'ssh': ('ssh',),
}


def capability_tools(capability: str, config: Mapping[str, Any]) -> list[str]:
    """All tools of one capability, by need, in catalog order (not only those switched on by default). With code projects
    off and "Answer only from documents" off, documents also bring the file tools (their need, files).
    report_not_covered belongs to "Answer only from documents" and is listed only while it is on."""
    files = capability == 'documents' and not _code(config) and not _grounded(config)
    needs = CAPABILITY_NEEDS[capability] + (('files',) if files else ())
    return [
        spec.name
        for spec in CATALOG.values()
        if spec.needs in needs and (spec.name != 'report_not_covered' or _grounded(config))
    ]


def enabled_capability_tools(config: Mapping[str, Any]) -> list[str]:
    """The tools of every capability switched on, each once, in catalog order."""
    names = {
        name
        for capability in CAPABILITY_NEEDS
        if config[capability]['enabled']
        for name in capability_tools(capability, config)
    }
    return [name for name in CATALOG if name in names]


def tools_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    """All agents: how many tools are on with their need met (by the capability switches, as the Tools tab judges),
    per group, MCP tools of enabled servers included."""
    chosen = {entry['tool']: entry for entry in config['tools']['items']}
    groups = {'code': 0, 'documents': 0, 'servers': 0, 'inventory': 0, 'mcp': 0, 'agents': 0}
    for spec in CATALOG.values():
        if chosen.get(spec.name, {}).get('mode') == 'on' and needs_met(spec.needs, config, None):
            groups[spec.group] += 1
    if config['mcp']['enabled']:
        for server in config['mcp']['servers']:
            if not server.get('enabled', True):
                continue
            for found in server.get('tools', []):
                if chosen.get(f'mcp:{server["id"]}:{found["name"]}', {}).get('mode', 'on') == 'on':
                    groups['mcp'] += 1
    return {'on': sum(groups.values()), 'per_run': 0, 'groups': groups}
