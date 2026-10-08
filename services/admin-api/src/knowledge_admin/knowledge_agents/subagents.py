"""run_subagent: a native agent hands a separate part of its work to a helper (a subagent) that runs in parallel with
the tools and resources it is given and without the conversation. Subagents run only in native agent sessions (Test
and Generate documents), whose runner puts the turn's Subagents into the run context.

The checks and the subagent's configuration are pure: a deep copy of the parent's effective configuration with only
the given tools on and the resource lists narrowed, the built-in prompt, no instructions and no skills."""
import copy
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from knowledge_admin.knowledge_agents.config import credential_references
from knowledge_admin.knowledge_agents.run_resources import KINDS
from knowledge_admin.tools.catalog import CATALOG, SUBAGENT, item, tools_of
from knowledge_admin.tools.sdk import NOT_OFFERED

MAX_TASK = 20_000
MAX_PER_TURN = 16
PROMPT = (Path(__file__).parent / 'prompts' / 'subagent.md').read_text(encoding='utf-8').strip()
UNAVAILABLE = 'Subagents run only in agent sessions.'
EMPTY_TASK = 'Give the subagent a task.'
LONG_TASK = 'A subagent task is at most 20,000 characters.'
NO_TOOLS = 'Give the subagent at least one tool.'
NO_SPAWN = 'Tool run_subagent cannot be given to a subagent.'
NOT_FOR_SUBAGENTS = 'Tool {name} may not be given to a subagent.'
NOT_GIVEN = '{label} {id} is not one of your resources.'
LIMIT = 'This turn already started 16 subagents.'
FAILED = 'Subagent failed: {message}'


def check_task(task: str) -> None:
    if not task.strip():
        raise ValueError(EMPTY_TASK)
    if len(task) > MAX_TASK:
        raise ValueError(LONG_TASK)


def _listed(config: Mapping[str, Any], capability: str) -> dict[str, dict[str, Any]]:
    """The parent's current resources of one kind, by id; none for a kind it does not use."""
    if not config[capability]['enabled']:
        return {}
    items = {'code': lambda: config['code']['projects'], 'documents': lambda: config['documents'].get('collections', []),
             'ssh': lambda: config['ssh']['servers'], 'services': lambda: config['services']['items']}[capability]()
    return {str(entry['id']): entry for entry in items if 'id' in entry}


def _given_tools(config: Mapping[str, Any], tools: list[str], offered: Collection[str],
                 mcp_tools: Mapping[str, str]) -> tuple[set[str], dict[str, set[str]]]:
    """The given catalog tools, and the given MCP tools by server id. Each name must be offered to this run (MCP
    tools by the name the model sees), allowed for subagents, and not run_subagent."""
    if not tools:
        raise ValueError(NO_TOOLS)
    rows = {entry['tool']: entry for entry in tools_of(config)['items']}
    catalog: set[str] = set()
    mcp: dict[str, set[str]] = {}
    for name in dict.fromkeys(tools):
        if name == SUBAGENT:
            raise ValueError(NO_SPAWN)
        if name in offered:
            row_name = name
        elif name in mcp_tools:
            row_name = mcp_tools[name]
        else:
            raise ValueError(NOT_OFFERED.format(name=name))
        if not rows.get(row_name, {}).get('subagent_allowed', True):
            raise ValueError(NOT_FOR_SUBAGENTS.format(name=name))
        if row_name.startswith('mcp:'):
            _, server, tool = row_name.split(':', 2)
            mcp.setdefault(server, set()).add(tool)
        else:
            catalog.add(row_name)
    return catalog, mcp


def _narrow(sub: dict[str, Any], resources: Mapping[str, list[str]]) -> None:
    """Narrow each resource list to `resources`, as run_resources.resolve narrows a per-run agent's: every id must be
    in the parent's current list; an empty list gives nothing of that kind, never everything.

    Kept separate from run_resources.resolve on purpose: resolve checks a per-run agent's request against the
    database (allowed and still available), while a subagent may only narrow its parent's already resolved lists,
    without reading the database. Keep the two rules in step."""
    given: dict[str, list[str]] = {}
    chosen: dict[str, list[dict[str, Any]]] = {}
    for kind in KINDS:
        listed = _listed(sub, kind.capability)
        ids = list(dict.fromkeys(str(entry) for entry in resources.get(kind.key) or []))
        for entry in ids:
            if entry not in listed:
                raise ValueError(NOT_GIVEN.format(label=kind.label, id=entry))
        given[kind.key], chosen[kind.key] = ids, [listed[entry] for entry in ids]
    code_ids = set(given['code_project_ids'])
    sub['code'] = {**sub['code'], 'all_projects': False, 'projects': chosen['code_project_ids'],
                   'graphs': [graph for graph in sub['code'].get('graphs', []) if str(graph.get('id')) in code_ids]}
    documents = {key: value for key, value in sub['documents'].items() if key not in ('folders', 'all_folders')}
    sub['documents'] = {**documents, 'all_collections': False, 'collections': chosen['collection_ids']}
    sub['ssh'] = {**sub['ssh'], 'servers': chosen['server_ids']}
    sub['services'] = {**sub['services'], 'items': chosen['service_ids']}
    sub['resources'], sub['run_resources'] = 'per_run', given


def _only(sub: dict[str, Any], catalog: set[str], mcp: dict[str, set[str]], mcp_tools: Mapping[str, str]) -> None:
    """Only the given tools on: catalog tools by name; MCP servers kept only for given MCP tools, every other tool of
    those servers off (from the snapshot's tool lists, the run's live names and the parent's own mcp:<server>:* rows,
    so a tool switched off for the parent stays off for the subagent)."""
    rows = {entry['tool']: entry for entry in tools_of(sub)['items']}
    items = [item(name, 'on' if name in catalog else 'off', rows.get(name, {}).get('usage')) for name in CATALOG]

    def keep(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [server for server in servers if str(server['id']) in mcp]
    sub['mcp'] = {**sub['mcp'], 'enabled': bool(mcp) and sub['mcp']['enabled'], 'servers': keep(sub['mcp']['servers'])}
    for server in sub['ssh']['servers']:
        server['mcp_servers'] = keep(server.get('mcp_servers', []))
    kept = [*sub['mcp']['servers'], *(found for server in sub['ssh']['servers'] for found in server['mcp_servers'])]
    for server_id, given in sorted(mcp.items()):
        listed = {tool['name'] for server in kept if str(server['id']) == server_id for tool in server.get('tools', [])}
        listed |= {name.split(':', 2)[2] for name in [*mcp_tools.values(), *rows] if name.startswith(f'mcp:{server_id}:')}
        for tool in sorted(listed):
            name = f'mcp:{server_id}:{tool}'
            items.append(item(name, 'on' if tool in given else 'off', rows.get(name, {}).get('usage'), group='mcp'))
    sub['tools'] = {'items': items}


def subagent_config(config: Mapping[str, Any], tools: list[str], resources: Mapping[str, list[str]] | None, *,
                    offered: Collection[str], mcp_tools: Mapping[str, str]) -> dict[str, Any]:
    """The configuration a subagent runs with, or a ValueError naming the tool or resource that cannot be given.
    `offered`: the catalog tools of the parent's run (context['tools']); `mcp_tools`: its MCP tools as the model sees
    them (context['mcp_tools']). resources None: the parent's lists and mode; otherwise a per-run run of exactly those."""
    catalog, mcp = _given_tools(config, tools, offered, mcp_tools)
    sub = copy.deepcopy(dict(config))
    sub.pop('record', None)
    if resources is not None:
        _narrow(sub, resources)
    _only(sub, catalog, mcp, mcp_tools)
    sub['name'] = f"{config['name']} · subagent"
    sub['system_prompt'] = {'content': PROMPT}
    sub['instructions'] = ''
    sub['skills'] = {'enabled': False, 'items': []}
    return sub


def referenced_credentials(config: Mapping[str, Any], credentials: Mapping[str, str]) -> dict[str, str]:
    """The parent turn's already decrypted credentials that a subagent's configuration references; nothing else."""
    wanted = {str(reference['id']) for reference in credential_references(config) if reference is not None}
    return {key: value for key, value in credentials.items() if str(key) in wanted}


async def run_subagent(context: Any, task: str, tools: list[str], resources: dict[str, list[str]] | None) -> str:
    """The catalog tool: the subagent's final answer, or a ValueError the adapter returns as a tool error."""
    host = context.get('subagents')
    if host is None:
        raise ValueError(UNAVAILABLE)
    return await host.run(context, task, tools, resources)
