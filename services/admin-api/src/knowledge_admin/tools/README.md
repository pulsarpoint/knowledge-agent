# Agent function tools

Implementations live in `knowledge_admin/capabilities/`. `catalog.py` describes each built-in tool
without the Agents SDK (`ToolSpec`: usage text, argument model, needed capability, the capability rule that
switched it on); `sdk.py` turns a catalog entry into an SDK `FunctionTool` and refuses a tool missing from the
run context's `tools`. `catalog.effective_tools(config, ssh_servers)` returns the tools a run offers.

- `list_files`, `read_file`, `search_files`. Read-only access to the
  enabled code/document roots and the run's workspace. Absolute paths are checked
  after resolving symlinks; searches use the installed `rg` executable.
- `ast_grep`. Runs the native ast-grep CLI in search-only mode to
  return complete functions, methods, classes, or other matched syntax nodes.
  Registered for code-enabled agents. Uses the file tools' selected-root checks,
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
- `ssh_run`. Resolves a selected server and its credential reference from
  the supplied snapshot/context. Reuses `knowledge_admin.capabilities.ssh.execute`, including
  optional host verification, sudo restrictions, timeout and cancellation handling.
- `shell_run`. A normal function tool sending Bash commands to the
  internal `shell-worker` service. Registered when code or documents are enabled.
  The worker provides Bash, Python, `rg`, `ast-grep`, Git and standard utilities.
  Each command sees only selected source/document/graph paths, mounted read-only,
  plus writable scratch files shared with the file tools for the duration of a run.
  Commands receive no application credentials or network access. Timeouts and
  client disconnects terminate the command and its background processes.

`AgentContext` (`capabilities.context.RunContext`) is a TypedDict documenting the configuration snapshot,
resolved credentials, workspace, shell switch and offered tool names. The SDK injects it; none of these values
appear in the model-facing tool argument schema. Tools do not query PostgreSQL or decrypt keys.

```python
from agents import Agent, Runner
from knowledge_admin.tools.catalog import effective_tools
from knowledge_admin.tools.sdk import function_tool

tools = [function_tool(spec, usage) for spec, usage in effective_tools(config, ssh_servers)]
assistant = Agent(name=config['name'], model=model, instructions=instructions, tools=tools)
names = frozenset(tool.name for tool in tools)
run = Runner.run_streamed(assistant, task, context={
    'config': config, 'credentials': credentials, 'workspace': workspace,
    'shell_enabled': 'shell_run' in names, 'tools': names,
})
```

The specialist runner in `knowledge_admin.agents.agent` builds its tools this way. Shell commands execute in the worker; the SDK stays in the administration
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
