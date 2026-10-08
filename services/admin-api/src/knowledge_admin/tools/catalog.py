"""The built-in tools, independent of any agent framework: what each tool does (usage text the model reads), its
arguments, the capability it needs and the rule that switched it on before tools were chosen per agent.
tools/sdk.py turns an entry into an Agents SDK tool."""
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.capabilities import ast_grep, code_graph, code_search, documents, files, ssh
from knowledge_admin.capabilities.context import RunContext
from knowledge_admin.capabilities.shell import client as shell

Group = Literal['code', 'documents', 'servers', 'agents']
Need = Literal['files', 'code', 'code_graph', 'documents', 'grounded', 'ssh']


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
    default_on: Callable[[Mapping[str, Any]], bool]


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


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ListFiles(Arguments):
    path: str | None = Field(None, description='Absolute folder path. Omit it to discover accessible root folders.')
    offset: int = Field(0, description='Number of directory entries to skip for pagination.')
    limit: int = Field(200, description='Maximum entries to return, from 1 to 1000.')


class ReadFile(Arguments):
    path: str = Field(description='Absolute file path from the configured folders or workspace.')
    offset: int = Field(0, description='Byte offset; use next_offset from an earlier result to continue.')
    max_bytes: int = Field(32768, description='Maximum bytes in byte mode, from 1 to 65536.')
    start_line: int | None = Field(None, description='First line (1-based) from a graph symbol or search result. Supply end_line too.')
    end_line: int | None = Field(None, description='Last line, inclusive. Line mode returns this range with line numbers; offset must be zero.')


class SearchFiles(Arguments):
    path: str = Field(description='Absolute file or folder path within the configured roots.')
    query: str = Field(description='Literal text to find, not a regular expression; use a single line.')
    case_sensitive: bool = Field(False, description='Whether letter case must match.')
    max_matches: int = Field(100, description='Maximum matching lines to return, from 1 to 500.')


class ShellRun(Arguments):
    command: str = Field(description='Bash command or multiline script, up to 20000 characters.')
    timeout_seconds: int = Field(60, description='Command timeout from 1 to 300 seconds.')


class CodeSearch(Arguments):
    path: str = Field(description='Absolute source file or directory in a selected code project.')
    language: str = Field(description='Go, Python, JavaScript, TypeScript or Tsx (case-insensitive).')
    name: str = Field(description='Literal function/method name, not a regular expression.')
    match: Literal['exact', 'contains'] = Field('exact', description='Exact name or substring match.')
    offset: int = Field(0, description='Number of matching declarations to skip, starting at zero.')
    limit: int = Field(5, description='Maximum complete declarations returned, from 1 to 20.')


class AstGrep(Arguments):
    path: str = Field(description='Absolute source file or folder within the configured roots.')
    language: str = Field(description='Built-in ast-grep language, such as Go, Python, JavaScript or TypeScript.')
    pattern: str | None = Field(None, description='Native ast-grep code pattern. Omit when supplying rule.')
    rule: str | None = Field(None, description='JSON rule object, e.g. {"kind":"function_declaration"}; omit with pattern. '
                                               'Supply only the rule object, not a full config, language, fix or rewrite.')
    max_matches: int = Field(20, description='Maximum complete matches to return, from 1 to 100.')


class CodeGraph(Arguments):
    project_id: str = Field(description='Code graph project ID from the supplied agent resources.')
    action: Literal['overview', 'search', 'routes', 'code', 'context', 'callers', 'callees', 'traverse', 'unresolved'] = Field(
        'overview', description='Overview, search, routes, code, context, callers, callees, traverse or unresolved.')
    query: str = Field('', description='Symbol name or text query for search; empty lists all indexed nodes.')
    node_id: str = Field('', description='Exact symbol ID from search; required except overview/search/routes and global unresolved.')
    offset: int = Field(0, description='Nonnegative pagination offset; continue using next_offset.')
    limit: int = Field(50, description='Maximum returned entries, from 1 to 100.')
    include_code: bool = Field(False, description='Include full source for returned nodes; code action always includes it.')


class SearchDocuments(Arguments):
    query: str = Field(description='Words or a question to search for.')
    collection: str | None = Field(None, description='Optional collection name, such as "jira", to search only that collection.')


class ReadDocument(Arguments):
    collection: str = Field(description='The collection name shown before the path, such as "jira".')
    path: str = Field(description='The document\'s full path as shown after the collection, such as "jira/DEMO-1.md".')


class ReportNotCovered(Arguments):
    reason: str = Field(description='What information is missing from the documentation.')


class SshRun(Arguments):
    server_id: str = Field(description="Exact server ID from the agent's configured SSH server list.")
    command: str = Field(description='Shell command to run remotely; check the returned exit_code.')
    as_root: bool = Field(False, description='Request noninteractive sudo to root for this command.')
    timeout_seconds: int = Field(60, description='Remote command time limit, from 1 to 300 seconds.')


# Usage: today's docstring without Args, then the sentences get_prompt said about the tool.
LIST_FILES = 'List accessible roots or the immediate children of one selected folder.'
READ_FILE = ('Read a source line range or a paginated UTF-8 byte excerpt from a selected file.\n\n'
             'Use read_file for surrounding comments, imports and targeted ranges.')
SEARCH_FILES = ('Search literal text in selected files using ripgrep, with file/line citations.\n\n'
                'Git ignore rules apply; hidden files and symlinks within folders are skipped.\n'
                'A truncated result can be narrowed by searching a subfolder or a longer query.\n\n'
                'Use search_files for text discovery.')
SHELL_RUN = '''Run Bash commands or Python scripts in an isolated Linux workspace.

Available programs include bash, python/python3, rg, ast-grep, git, sed, awk,
find and standard Linux utilities. Pipelines, Bash arrays and heredocs work.
For Python use: python - <<'PY'
import json
print(json.dumps({'ok': True}))
PY
Use rg -n 'symbol' /work/data/code/project to locate code, then ast-grep or sed
to inspect complete functions or focused ranges. Python's sqlite3 module can
query configured graph databases; use sqlite3.connect('file:PATH?mode=ro', uri=True).
Quote paths and use single quotes around ast-grep patterns containing $.

The working directory is writable scratch space shared across shell calls
within this run. Selected code, documents and graph paths retain the absolute
paths in the agent resources and are read-only. Other projects, credentials
and network access are unavailable. Use ssh_run for configured remote servers.
Shell variables do not persist between calls; save intermediate work to files.
Inspect exit_code, stderr and timed_out. Output over 32 KiB per stream is
truncated: write large results to scratch files and inspect focused sections.

Use shell_run for Bash pipelines, rg, ast-grep and Python scripts. Selected resource paths are mounted read-only at
the same absolute paths; pwd shows writable scratch space. Inspect focused source ranges; use code_graph for indexed
relationships when available. Scratch files persist between tool calls within this run. Network access is disabled
in the shell; use ssh_run for configured servers.'''
CODE_SEARCH = '''Find functions/methods by name and return complete bodies, imports and call sites.

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

Use code_search to find a function or method by name and read its complete body, imports and call sites. Calls
returned by code_search are syntax, including nested closures; resolve package/receiver targets before following
them.'''
AST_GREP = '''Search source structure with ast-grep and return complete matched code and line ranges.

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

Use ast_grep for custom structural patterns, classes and other declaration forms.'''
CODE_GRAPH = '''Search and explore indexed source with CodeGraph's own SDK functions.

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

Use code_graph first: search for symbols, code for full bodies, context for imports/types, and traverse for
reachable calls. It uses the CodeGraph SDK directly; follow pagination and inspect edge metadata and unresolved
references. Then verify business rules, state changes, SQL and external effects with source-file tools. Graph paths
are relative to CODE_ROOT, as returned by the tool. Static edges are evidence, not proof of runtime execution.'''
SEARCH_DOCUMENTS = ('Search the knowledge base and return numbered excerpts to cite as [n].\n\n'
                    'Use search_documents to find knowledge-base excerpts and read_document for a whole document.')
READ_DOCUMENT = 'Read one knowledge-base document returned by search_documents; cite it with its [n].'
REPORT_NOT_COVERED = 'Record that the retrieved documents do not answer the question.'
SSH_RUN = ('Execute a command on a server selected for this agent and return its output.\n\n'
           "Follow the selected server's instructions. Use as_root for sudo only when\n"
           'allow_sudo is enabled. The backend supplies the login, key and host verification;\n'
           'never request credentials from the user or include them in command arguments.\n\n'
           "Use ssh_run with the configured server ID. Follow each server's instructions. "
           'Request as_root only when allow_sudo is true.')


def _call(function: Callable[..., Awaitable[str]]) -> Callable[[RunContext, BaseModel], Awaitable[str]]:
    async def run(context: RunContext, arguments: BaseModel) -> str:
        return await function(context, **arguments.model_dump())
    return run


CATALOG: dict[str, ToolSpec] = {spec.name: spec for spec in (
    ToolSpec('list_files', 'code', 'List files', 'List the selected folders and their contents.', LIST_FILES,
             ListFiles, _call(files.list_files), 'files', _files),
    ToolSpec('read_file', 'code', 'Read file', 'Read a file or a line range.', READ_FILE,
             ReadFile, _call(files.read_file), 'files', _files),
    ToolSpec('search_files', 'code', 'Search files', 'Find literal text with ripgrep.', SEARCH_FILES,
             SearchFiles, _call(files.search_files), 'files', _files),
    ToolSpec('shell_run', 'code', 'Shell', 'Run Bash, rg, ast-grep, git and Python in an isolated sandbox.', SHELL_RUN,
             ShellRun, _call(shell.shell_run), 'files', _files),
    ToolSpec('code_search', 'code', 'Code search', 'Find functions by name with their bodies and calls.', CODE_SEARCH,
             CodeSearch, _call(code_search.code_search), 'code', _code),
    ToolSpec('ast_grep', 'code', 'Structural search', 'Match syntax patterns with ast-grep.', AST_GREP,
             AstGrep, _call(ast_grep.ast_grep), 'code', _code),
    ToolSpec('code_graph', 'code', 'Code graph',
             'Search symbols and follow callers and callees. Offered when a selected code project has a generated graph.',
             CODE_GRAPH, CodeGraph, _call(code_graph.code_graph), 'code_graph', _code),
    ToolSpec('search_documents', 'documents', 'Search documents', 'Search the knowledge base for citable excerpts.',
             SEARCH_DOCUMENTS, SearchDocuments, _call(documents.search_documents), 'documents', _documents),
    ToolSpec('read_document', 'documents', 'Read document', 'Read one knowledge-base document.', READ_DOCUMENT,
             ReadDocument, _call(documents.read_document), 'documents', _documents),
    ToolSpec('report_not_covered', 'documents', 'Report not covered', 'Mark a question the documents do not answer.',
             REPORT_NOT_COVERED, ReportNotCovered, _call(documents.report_not_covered), 'grounded', _grounded),
    ToolSpec('ssh_run', 'servers', 'SSH', 'Run a command on a selected server.', SSH_RUN,
             SshRun, _call(ssh.ssh_run), 'ssh', _ssh),
)}

NEED_REASONS: dict[str, str] = {
    'files': 'Needs code projects, or documents without "Answer only from documents"',
    'code': 'Needs code projects', 'code_graph': 'Needs code projects', 'documents': 'Needs documents',
    'grounded': 'Needs "Answer only from documents"', 'ssh': 'Needs SSH servers'}
NEED_TABS: dict[str, str] = {'files': 'git', 'code': 'git', 'code_graph': 'git', 'documents': 'documents',
                             'grounded': 'documents', 'ssh': 'ssh'}


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
    return _ssh(config) if ssh_servers is None else bool(ssh_servers)


def item(name: str, mode: str, usage: str | None = None, group: str | None = None) -> dict[str, Any]:
    return {'tool': name, 'group': group or CATALOG[name].group, 'mode': mode, 'usage': usage, 'subagent_allowed': True}


def implied_tools(config: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The tools the capabilities gave before tools were chosen (the 0078 backfill rule). Claude Code: none."""
    if config.get('runtime', 'openai') != 'openai':
        return {'items': []}
    return {'items': [item(spec.name, 'on' if spec.default_on(config) else 'off') for spec in CATALOG.values()]}


def tools_of(config: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The configuration's tools; a configuration saved without them (before 0078) gets the implied tools."""
    return config['tools'] if 'tools' in config else implied_tools(config)


def effective_tools(config: Mapping[str, Any], ssh_servers: list[dict[str, Any]]) -> list[tuple[ToolSpec, str]]:
    """The catalog tools this run offers, in catalog order, with their usage text: switched on, and their need met."""
    chosen = {entry['tool']: entry for entry in tools_of(config)['items']}
    offered = []
    for spec in CATALOG.values():
        entry = chosen.get(spec.name)
        if entry is None or entry['mode'] != 'on' or not needs_met(spec.needs, config, ssh_servers):
            continue
        offered.append((spec, entry['usage'] or spec.usage))
    return offered


def _mcp_rows(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry['tool']: entry for entry in tools_of(config)['items'] if entry['tool'].startswith('mcp:')}


def mcp_blocked(config: Mapping[str, Any], server_id: str) -> list[str]:
    """The tool names of one MCP server the model must not get: switched off (or per run, not offered in step 1).
    A tool without a row is on."""
    prefix = f'mcp:{server_id}:'
    return sorted(name.removeprefix(prefix) for name, entry in _mcp_rows(config).items()
                  if name.startswith(prefix) and entry['mode'] != 'on')


def mcp_notes(config: Mapping[str, Any], servers: list[dict[str, Any]]) -> list[str]:
    """Edited usage of MCP tools that are on, as prompt lines: the description itself comes from the server."""
    rows = _mcp_rows(config)
    notes = []
    for server in servers:
        prefix = f"mcp:{server['id']}:"
        for name in sorted(rows):
            entry = rows[name]
            if name.startswith(prefix) and entry['mode'] == 'on' and entry['usage']:
                notes.append(f"- {server['name']}/{name.removeprefix(prefix)}: {entry['usage']}")
    return notes


def catalog_listing() -> list[dict[str, Any]]:
    return [{'name': spec.name, 'group': spec.group, 'label': spec.label, 'summary': spec.summary,
             'usage': spec.usage, 'needs': spec.needs} for spec in CATALOG.values()]


def capability_defaults(capability: str, config: Mapping[str, Any]) -> list[str]:
    """The tools a capability switched on gets, by the backfill rule for the new switches. With code projects on,
    the file tools belong to code, so documents do not bring them."""
    needs = {'code': ('files', 'code', 'code_graph'), 'ssh': ('ssh',),
             'documents': ('documents', 'grounded') + (() if _code(config) else ('files',))}[capability]
    return [spec.name for spec in CATALOG.values() if spec.needs in needs and spec.default_on(config)]


def tools_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    """All agents: how many tools are on with their need met (by the capability switches, as the Tools tab judges),
    per group, MCP tools of enabled servers included; Claude Code agents: their Claude Code tools."""
    if config.get('runtime', 'openai') != 'openai':
        return {'claude_code_tools': list(config.get('claude_code_tools', []))}
    chosen = {entry['tool']: entry for entry in tools_of(config)['items']}
    groups = {'code': 0, 'documents': 0, 'servers': 0, 'mcp': 0, 'agents': 0}
    for spec in CATALOG.values():
        if chosen.get(spec.name, {}).get('mode') == 'on' and needs_met(spec.needs, config, None):
            groups[spec.group] += 1
    if config['mcp']['enabled']:
        for server in config['mcp']['servers']:
            if not server.get('enabled', True):
                continue
            for found in server.get('tools', []):
                if chosen.get(f"mcp:{server['id']}:{found['name']}", {}).get('mode', 'on') == 'on':
                    groups['mcp'] += 1
    return {'on': sum(groups.values()), 'per_run': 0, 'groups': groups}
