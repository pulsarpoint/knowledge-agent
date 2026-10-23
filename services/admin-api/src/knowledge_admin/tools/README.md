# Agent function tools

Implementations live in `knowledge_admin/capabilities/`:
`files.py` (list/read/search files), `ast_grep.py`, `code_search.py`, `code_graph.py` (the CodeGraph queries and
`current_graph`), `documents.py` (`search_documents`, `read_document`, `report_not_covered`), `document_tools.py` (`list_documents`, `get_document`, `new_document`, `update_document`, `edit_document`), `ssh.py`, `subagents.py`, `skills.py`, `mcp.py`
(MCP server selection and headers), `context.py` (`RunContext`) and `shell/` (`client.py` for the API side,
`worker.py` for the shell worker service). They do not import the Agents SDK.

Each built-in tool is a function marked `@tool` (`tools/registry.py`), next to its implementation. Its signature after
the run context is its argument schema (`Annotated[..., Field(description=...)]` gives the descriptions) and its
docstring is the usage text the model reads. `usage=` and `arguments=` override the docstring and the schema; a
`*args` or `**kwargs` parameter needs `arguments=`, and a name registered twice is an error.

`tools/tools.json` holds the other attributes of each tool, in the order the runner uses: `name`, `group` (`code`,
`documents`, `servers`, `inventory`, `memory` or `agents`), `label` and `summary` (a short line). `tools/catalog.py`
joins the file with the registered functions (`load_catalog`) and raises on a tool missing from either side, a
duplicate, or an unknown group. `CATALOG` is the result, a `ToolSpec` per tool (name,
group, label, summary, usage text, argument model, run). Every run gets every catalog tool with its built-in usage
text: no per-agent choice, needs or usage overrides (migration 0115 dropped `ka.agent_tools` and `ka.tools.needs`).
What a tool may do is decided by its `check_<tool>` against the run's access object (`knowledge_admin/access.py`).

`ka.tools` is the table copy of `tools.json` (migration 0108): `tools/fixture.py` `sync_tools` replaces it with the file
in one transaction at the admin-api start, under an advisory lock, so concurrent starts wait for each other. The file
is the only writer; edit the file, not the table, and a start after the edit makes the table match.

`tools/sdk.py` is the only Agents SDK binding: `function_tool(spec, usage)` makes a `FunctionTool` whose description
and argument schema are the same in every run (what a run may use is in its prompt and checked by the tool), and the
adapter refuses a tool missing from `context['tools']` (`Tool <name> is not available in this run.`) as a backstop.

`create_agent` (`knowledge_admin/agents/runner.py`, `instructions_and_tools`) builds every catalog tool; the MCP
servers in the run's access offer every one of their tools (no filter, no notes in the prompt).

`run_subagent` (`capabilities/subagents.py`; group `agents`) is offered in every run that has the turn's `Subagents`
host (a native session turn, an expert's call), never in a subagent's own run. Its arguments are the task and,
optionally, the resources to give (from the run's own). Its run is `knowledge_agents/subagents.run_subagent`, which
finds the host in the run context (`context['subagents']`) and the call id in `capabilities.context.TOOL_CALL_ID`
(set by `tools/sdk.py` around each invocation). `subagent_config` narrows the resources to the parent's current lists;
the subagent gets every tool but `run_subagent` and the write tools, the parent's MCP servers, no experts, and the
parent's access narrowed.

`schedule_wakeup`, `list_wakeups`, `cancel_wakeup` (`capabilities/wakeups.py`; group `agents`) queue, list and cancel
messages to the run's own session (`config['session_id']`) for later: an input with kind `wakeup` and `not_before`
(migration 0123), at most 3 pending per session, 1 minute to 30 days ahead. The session worker delivers a due one as the
next turn (`Scheduled wake-up (set <time>): <message>`), never as a comment inside a running turn. Offered only in a
session turn (`catalog.WAKEUPS`): not in a subagent's run, an expert's call (`config['expert']`, set by
`experts.load`) or an internal call.

The inventory tools (`capabilities/inventory_tools.py`; group `inventory`): reads `get_environment_model`,
`list_servers`, `list_deployments`, `list_connections`, offered in every run (every entry with its `status`, draft or
confirmed); writes `save_environment`, `save_server`, `save_service`, `save_deployment`, `save_connection`,
`save_relation`, `remove_inventory_item` and `save_inventory_draft` through `inventory/store.py`, offered only in a
session turn (`catalog.INVENTORY_WRITES`, withheld like the wake-ups) and allowed, without any role, for items in the
run's access or created by the session (`store.created_in_session`); a write on an existing deployment, or a connection
from it, needs its environment, server and service all usable. Every write is a draft (`status='draft'`): a new entry is
created as one, a draft is updated, a confirmed entry is refused (`This <type> is confirmed; only a person can change
it. ...`); only a person confirms or discards. `save_inventory_draft` takes a whole picture by name (environment,
servers, services, deployments, connections, relations): the pure `resolve_draft(payload, recorded)` plans it over the
rows the run may name (a confirmed entry is used, never changed; a draft is updated; the rest created) and
`apply_draft` writes the plan in one transaction, all or nothing, errors as `<part>[<index>]: <reason>`. The usage
texts state each tool's purpose, the draft rule, every field's format and allowed values, and an example; the argument
schemas' field descriptions say the same per field.

The document tools (`capabilities/document_tools.py`; group `documents`, `get_document` in `inventory`): reads
`list_documents` (a server's, service's or environment's documents and its children's, the inventory document first,
with `published` and `your_draft`) and `get_document` (this run's draft of the document when it has one, else the live
version), offered in every run; writes `new_document`, `update_document` (the whole text; `resource` links the document
to a server, service or environment when the draft is published: the draft holds the link,
`store.promote_draft` adds it) and `edit_document` (one exact passage), offered only in a session turn
(`catalog.DOCUMENT_WRITES`, withheld like the inventory writes). Every write is the run's draft in the real documents
(one per document; a person publishes it on the document's page) and every write reply ends with `This is a draft: a
person publishes it on the document's page.` No role: a document is usable when it is in the run's access or linked to
a resource in it, and `new_document` needs its resource in the access (`{type, id}` or `{type, name}`, resolved among
the run's resources by `resource_of`). `new_document` names the folder and slug from the resource as discovery did
(`drafts.free_folder`, `drafts.free_slug`), refuses a title already used by a document linked to the resource (`A
document titled "<title>" is already linked to this <type> (<id>); update it with update_document instead.`) and
returns the draft's id, which only its run uses until it is published and which stays the document's id afterwards.
`read_document` is different: it reads a knowledge-base file from `search_documents` results, for citing.

`load_skill` (`capabilities/skills.py`; group `agents`) returns the instructions of a skill in the run's access, by
name, read from `ka.agent_skills` (enabled only; any other name is refused with `This skill is not available in this
run.`). The run's instructions list its skills by name and description only (`skills.section`, used by
`runner.get_prompt` and `sessions/text.agent_system_prompt`); a run without skills has no Skills section.

Expert tools (`ask_<slug>`) are not catalog tools: they are not in the tool catalog and have no usage text of their
own. A session pins the experts it may call (`config['experts']`); the runtime builds one function tool per entry
with `tools/sdk.py` (name, description and parameters from the entry; experts that accept resources also take `resources`), and
`knowledge_agents/experts.py` runs the call. A run gets every enabled expert other than its own agent, or those its
start request names (`expert_ids`). Subagents never get them, and an expert's own run has an empty `experts` list.
Calls share the turn's limit of 16 helpers with subagents.

- `list_files`, `read_file`, `search_files`. Read-only access to the
  enabled code/document roots and the run's workspace. Absolute paths are checked
  after resolving symlinks; searches use the installed `rg` executable.
- `ast_grep`. Runs the native ast-grep CLI in search-only mode to
  return complete functions, methods, classes, or other matched syntax nodes.
  Refuses a run without code projects. Uses the file tools' selected-root checks,
  secret redaction, and a 30-second timeout; it does not enable general shell
  access. Its SDK tool description includes patterns and a function-name rule.
- `code_search`. Searches functions and methods by literal name
  using the same Tree-sitter engine, without requiring an index or hand-written
  patterns. Returns full bodies, Go receivers, imports and syntactic call sites.
  Supports Go, Python, JavaScript, TypeScript and TSX; other constructs/languages
  remain accessible with `ast_grep`. Calls include nested closures and are not
  resolved targets or execution order. Available only in selected code folders.
- `code_graph`. Uses CodeGraph's public JavaScript SDK through
  `services/code-graph/query.cjs`, without hand-written graph SQL. Supports search,
  route inventory, full source, imports/types/references, direct callers/callees,
  recursive outgoing call traversal and unresolved references scoped to a node.
  Preserves native fields and edge metadata. Source hashes must match the index.
  The pinned SDK ignores `readOnly` on open, so it opens a disposable snapshot
  copy; source and published snapshots remain unchanged.
- Inventory tools (`get_resource`, `get_document`).
  `get_resource(resource_type, resource_id)` — `resource_type` one
  of `server`, `service`, `environment`, `code_project` (a fixed enum), `resource_id` a plain uuid — returns everything
  recorded about one resource as JSON: its fields, its `inventory_document` (id, title; or null), its other `documents`
  (id, title; from their links) and `links` (`{type, id, name, relation,
  ...}`) the model follows by passing `type` and `id` again; lists stop at 100 (`truncated`). A server's connection
  (username, port, sudo) comes from the run's configuration. Scoped to the run's allowlist: anything
  the run was not given (a linked resource too) or a malformed id returns "The <type> <id> is not available in this
  run.", the same as for a missing entry. `get_document` reads documents the run was given or linked to those
  resources. The prompt names a run's starting resource in one line (`Resource: server <uuid> (<name>)`) and lists
  servers and services as an index (`{type, id, name, description}`); details come from `get_resource`.
- `ssh_run`. Resolves a selected server and its credential reference from
  the supplied snapshot/context. Reuses `knowledge_admin.capabilities.ssh.execute`, including
  optional host verification, sudo restrictions, timeout and cancellation handling.
- `shell_run`. A normal function tool sending Bash commands to the
  internal `shell-worker` service. Refuses a run without code projects or collections.
  The worker provides Bash, Python, `rg`, `ast-grep`, Git and standard utilities.
  Each command sees only selected source/document/graph paths, mounted read-only,
  plus writable scratch files shared with the file tools for the duration of a run.
  Commands receive no application credentials or network access. Timeouts and
  client disconnects terminate the command and its background processes.

`AgentContext` (`capabilities.context.RunContext`) is a TypedDict documenting the configuration snapshot,
resolved credentials, workspace, shell switch and offered tool names. The SDK injects it; none of these values
appear in the model-facing tool argument schema. Tools do not query PostgreSQL or decrypt keys.

Shell commands execute in the worker; the SDK stays in the administration
process. MCP uses
SDK integration; skills and Mermaid guidance remain instructions.

The SDK's `FileSearchTool` searches OpenAI-hosted vector stores, not these local
folders or our PostgreSQL index. `WebSearchTool` is also an OpenAI-hosted Responses
tool. Neither is currently registered. `ShellTool` requires a local executor or
an OpenAI-hosted environment. The installed LiteLLM/Bedrock integration accepts
function tools, so those SDK tool classes cannot be substituted directly for our
functions. `search_files` is local literal-text search; a tool for our RAG
`/search` API would be a separate capability.

## Structural source search

Use `code_graph` for indexed symbol lookup, full source and relationships;
`code_search` for named functions without an index; `search_files` for literal
text; `ast_grep` for custom syntax patterns; and `read_file` for surrounding source.
Tree-sitter parses syntax; receiver/type resolution still needs source
and graph evidence. Native syntax and language details are documented in the
[ast-grep CLI reference](https://ast-grep.github.io/reference/cli.html).

For example, `code_graph(action="search", query="SetPersonalLimits", project_id=...)`
returns matching node IDs. Use `action="code"` for the body, `action="context"`
for imports/types, and `action="traverse"` to list all reachable indexed calls.
Traverse has no arbitrary depth cutoff; `limit` controls response pagination,
not traversal depth. Follow `next_offset`. Set `include_code=true` to include
complete source on a page. The returned nodes may also include interface methods
or types where the underlying graph models a conversion as a call. Indexed calls
and SDK callees are not a proof of all runtime paths; inspect each edge's kind
and resolution metadata. Handler traversal also does not automatically include
the middleware registered elsewhere in the router.

The agent receives a normal function tool. Example arguments for a Go method:

```json
{
  "path": "/work/code/dgp/responsible-gaming-psql/controller/personal_limits.go",
  "language": "Go",
  "pattern": "func ($RECEIVER) SetPersonalLimits($$$ARGS) { $$$BODY }"
}
```

`$NAME` matches one syntax node; `$$$NAME` matches a sequence, including the
function body. Patterns depend on declaration shape. For free functions **or**
methods with any signature, supply `rule` instead of `pattern`. The `rule`
argument is a JSON string containing the native rule object:

```json
{
  "any": [
    {"kind": "function_declaration"},
    {"kind": "method_declaration"}
  ],
  "has": {"field": "name", "regex": "^SetPersonalLimits$"}
}
```

Python example: `language="Python"`,
`pattern="def process($$$ARGS): $$$BODY"`. Other built-in languages use their
native ast-grep patterns and node kinds. Return values contain `matches` with
absolute `path`, one-based inclusive `start_line`/`end_line`, and full `code`, plus
`truncated` and `warning`. Code is included once rather than repeated in native
metavariable metadata. The default is 20 complete matches; narrow the query or
increase `max_matches` up to 100 when the result is truncated. Adjacent doc
comments are not part of a function's matched syntax node; use `read_file` for
them. Empty results can reflect ignore rules or a pattern mismatch.

The runtime pins `@ast-grep/cli` to 0.39.4 in `services/code-graph/package-lock.json`.
The Docker build runs only that package's binary-install script and copies the
native executable onto `PATH`. For local development, install ast-grep 0.39.4 on
`PATH`, or run `npm ci --ignore-scripts` in `services/code-graph`, followed by
`node node_modules/@ast-grep/cli/postinstall.js`, and add its `node_modules/.bin`
directory to `PATH` when running tests.

The tool supplies its own empty ast-grep config so source repositories cannot
load custom parser libraries or project rules. It accepts no CLI flags, rewrite
operations, or shell commands. Code-disabled phases do not receive this tool.
The API inspection phase uses source tools; overview/messaging final writing
steps disable them after collecting evidence.

## Bash and Python

The model sees usage examples directly in `shell_run`'s description. For example:

```bash
rg -n 'SetPersonalLimits' /work/code/dgp/responsible-gaming-psql
ast-grep run --lang go --pattern 'func ($RECEIVER) SetPersonalLimits($$$ARGS) { $$$BODY }' /work/code/dgp/responsible-gaming-psql/controller
python - <<'PYTHON'
import json
from pathlib import Path
Path('notes.json').write_text(json.dumps({'inspected': ['SetPersonalLimits']}))
PYTHON
```

`pwd` returns this run's scratch directory. Use absolute resource paths from the
prompt. Shell calls share scratch files, but not shell variables or a persistent
Python interpreter. Scratch files are removed when the run ends. Tool responses
report stdout, stderr, exit code, timeout and truncation (32 KiB per stream).

Compose sets `AGENT_SHELL_URL=http://shell-worker:8796` and
`AGENT_WORKSPACE_ROOT=/work/agent-workspaces` on the API and the shell worker.
The shared workspace volume uses the same paths in all three containers; the
runner assigns scratch ownership to the shell worker's UID 1000. No database
migration or separate model connection is needed.

The worker has no published port, uses an internal network, runs as UID 1000
without capabilities, and receives no database, LLM or SSH credentials. Bubblewrap
creates fresh user/PID/mount/network namespaces per command with an empty `/proc`.
Docker's seccomp filter is relaxed only for this worker to permit unprivileged
namespace creation. The worker uses a read-only root filesystem, a 1 GiB memory
limit and a 256-process limit. Hosts must allow unprivileged user namespaces;
execution fails if they are unavailable, with no unsandboxed fallback.

The worker is an internal trusted backend: resource paths come from the saved
agent configuration, never tool arguments supplied by the model. Do not publish
its HTTP endpoint. Commands cannot access that endpoint from their namespace.
