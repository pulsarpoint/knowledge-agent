# Agent Tools, Step 1 (Tool Selection and Tool Usage) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tools become explicit. Every native agent has a list of tools, each switched **on** or **off**, with
**usage instructions** the model sees (built-in text, editable per agent). The tools are saved in `ka.agent_tools`,
carried in the configuration snapshot and its versions, enforced when the agent is built (catalog tools by
selection, MCP tools by an SDK `tool_filter`), shown on a new **Tools** tab, chosen in the **New agent** wizard and
summarised in **All agents**. Every run shows which tools it called and how often (run record, Generate documents
session, Test chat answers and Show config, Activity → Agent runs). The `code_graph` tool finally reaches the model
when a selected code project has a generated graph. Before any of this, the real tool implementations move into a
new `knowledge_admin/capabilities/` package that knows nothing about the Agents SDK (user decision), so the tool
catalog is SDK-independent and one adapter (`tools/sdk.py`) binds it to the OpenAI Agents SDK.

**Architecture:**
- **Layout (Task 1, a pure move):** `knowledge_admin/capabilities/` holds the implementations: `files.py`,
  `documents.py` (RAG client), `ssh.py` (the SSH transport, from `knowledge_admin/ssh.py`, plus the agent's
  `ssh_run` logic), `shell/client.py` and `shell/worker.py` (from `tools/shell.py` and `shell_worker.py`),
  `code_graph.py` (graph loading from `code_processing.load_code_graph` and the query bridge), `code_search.py`,
  `ast_grep.py`, `mcp.py` (choosing MCP servers and their auth headers, from `agents.py`) and `context.py` (the run
  context type and the per-run "nothing given" rule). `tools/` keeps thin `@function_tool` wrappers in Task 1;
  Task 2 replaces them with `tools/catalog.py` (SDK-free `ToolSpec`s whose `run` calls capabilities) and
  `tools/sdk.py` (`function_tool(spec, usage) -> FunctionTool`). Indexing stays in `code_processing.py`.
- **Schema (Task 3):** `0078_agent_tools`: `ka.agent_tools(agent_id, tool, mode, usage, subagent_allowed,
  updated_at)` as the spec writes it; the up migration backfills `mode='on'` rows for every native agent by the
  "Offered today" rule; the down migration drops the table.
- **Config:** `AgentConfig.tools = {"items": [...]}` loaded by `get_agent_config`; versions keep it; versions and
  pinned session configurations saved before 0078 get `catalog.implied_tools(config)` (the same rule as the
  backfill); Claude Code agents get `{"items": []}`. `get_agent_config` (both paths) and `run_resources.resolve`
  fill `code.graphs` from `capabilities.code_graph.current_graph` (Task 5).
- **Enforcement:** `catalog.effective_tools(config, ssh_servers)` replaces `agents.get_tools` (mode `on` and needs
  met); `create_agent` builds one `FunctionTool` per entry with its usage as the description; `agent()` puts the
  offered names into `context['tools']` (the adapter's backstop) and `shell_enabled = 'shell_run' in tools`; MCP
  servers get `tool_filter=create_static_tool_filter(blocked_tool_names=[...])` for their tools switched off, and
  edited MCP usage goes to the prompt under **Tool notes**. Per-run narrowing (`run_resources.resolve`) is
  unchanged and still decides what each tool reaches.
- **API (Task 6, `tools_api.py`):** `GET /api/tools`, `GET/PUT /api/agents/{id}/tools` (revision-checked, save
  rules), the profile PUT switching default tools on with a capability, the wizard's `tools`, and
  `tools_summary` in `GET /api/agents`.
- **Tool usage (Task 7):** `run_record.tool_counts(conn, run_keys)`; `tool_counts` in `GET /api/agent-runs` (each
  run), `GET /api/agent-runs/{run_key}`, `GET /api/claude-code/sessions/{id}` and
  `GET /api/agents/{id}/sessions/{session_id}` (the session and each run).
- **admin-ui:** `lib/tools.ts`; `AgentToolsView` (the Tools tab after General); a Tools page in the wizard review;
  the All agents **Tools** column; the tool usage line (`toolUsageLine`) in the run record, Agent runs, the session
  page and Test chats.

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations, `db/tests/run.sh`;
- FastAPI, pydantic v2, psycopg3, OpenAI Agents SDK 0.19.2 (`FunctionTool`, `agents.strict_schema`,
  `agents.mcp.create_static_tool_filter`), pytest against the disposable PostgreSQL fixture
  (`knowledge-agent-v3-postgres:17.10-vector0.8.6`);
- React Router 7 with shadcn/ui, Vitest with Testing Library (`services/admin-ui`).

**Spec:** `docs/superpowers/specs/2026-10-08-agent-tools-design.md` (binding), **delivery step 1 only**. Additions
decided after the spec (binding): Task 1 moves the implementations into `capabilities/` first, as its own commit
with no behaviour change; native agents are the focus; **Claude Code agents keep their Read/Grep/Glob/Bash switches
on the General tab** and their Tools tab only lists them with a note (the spec's "move them to the Tools tab" is not
done); edited MCP usage goes into the prompt as "Tool notes"; existing agents keep exactly the tools their
capabilities give today until edited; the coordinator and the board no longer exist (0077). Out of scope (later
steps): `run_subagent` and the "A subagent may use" checklist (step 2), built-in agents and Code reader (step 3),
extra tools per run / "Allow per run" / `0080_run_tools` (step 4), `ask_agent` (step 5).

## Global Constraints

- Implementers and reviewers never run make targets and never touch .env or .local. Never touch the running Docker
  stack's database: tests use the disposable PostgreSQL fixture (`tests/conftest.py`) and `db/tests/run.sh`.
- Credentials never logged, stored in events or history unredacted, or passed to Bash. Tool usage text and MCP tool
  notes are administrator text placed in the prompt and tool descriptions; they never contain or resolve
  credentials. Tool errors keep going through `redact` exactly as today.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12. TypeScript passes `pnpm exec tsc --noEmit`.
- Every task leaves `cd services/admin-api && uv run --frozen pytest -q` and
  `cd services/admin-ui && pnpm exec vitest run` green (each task lists the files it touches; run the whole suite
  before committing).
- Migration: `0078_agent_tools` (Task 3) with `.up.sql` and `.down.sql`, each wrapped in `BEGIN; … COMMIT;` with
  `SET search_path = ka, public;`; `db/tests/run.sh` rollback pin `76` → `77`.
- Step 1 offers only modes `on` and `off`. The `per_run` mode and `subagent_allowed` exist in the schema; the API
  accepts `mode` `on`/`off` only (422 otherwise) and always saves `subagent_allowed = true`. A `per_run` row put in
  by hand is treated as off.
- Tool names: catalog names (`list_files`, `read_file`, `search_files`, `shell_run`, `code_search`, `ast_grep`,
  `code_graph`, `search_documents`, `read_document`, `report_not_covered`, `ssh_run`); MCP tools
  `mcp:<mcp_server_id>:<tool name>`.
- Config item shape (`config['tools']['items']`, catalog order first, then MCP rows by name):
  `{"tool": "read_file", "group": "code", "mode": "on", "usage": null, "subagent_allowed": true}`.
- Texts (API):
  - `tools_api.STALE = 'The tools changed since you opened them. Reload.'` (409);
  - `tools_api.GROUNDED = 'Grounded answers need report_not_covered.'` (400);
  - `tools_api.UNKNOWN = 'Unknown tool: {tool}.'` (400);
  - `tools_api.MCP_NOT_SELECTED = 'MCP tool {tool} belongs to a server this agent has not selected.'` (400);
  - `tools_api.CLAUDE_CODE = 'Claude Code agents use their Claude Code tools. Switch them on the General tab.'` (400).
- Texts (model-facing): `sdk.NOT_OFFERED = 'Tool {name} is not available in this run.'` (the adapter's backstop,
  returned through the SDK's default tool error text); prompt heading `Tool notes:` followed by lines
  `- <server name>/<tool>: <usage>`.
- Texts (needs, `catalog.NEED_REASONS`): `files` → `Needs code projects, or documents without "Answer only from
  documents"`; `code` → `Needs code projects`; `code_graph` → `Needs code projects`; `documents` → `Needs
  documents`; `grounded` → `Needs "Answer only from documents"`; `ssh` → `Needs SSH servers`; MCP → `Needs MCP`.
- UI texts: tab `Tools`; groups `Code`, `Documents`, `Servers`, `MCP`, `Agents` (empty groups are hidden);
  `Edit usage`, `Reset`, `Edited`, `Save tools`, `Tools saved.`; Claude Code note `Claude Code agents use Claude
  Code's own tools on their working folders. Switch them on the General tab.`; capability tabs (native)
  `Tools for these projects are chosen on the Tools tab.`; wizard tab `Tools`; list `7 tools · Code 6 · Servers 1`,
  `No tools`, Claude Code `Read, Grep, Glob, Bash`; usage line `code_graph 12 · read_file 31 · shell_run 4` or `No
  tools used`; Agent runs column `Tools` with the top three and `+n`, the tooltip lists all.

## Review Focus

1. **The model gets a tool that was not selected for it, or reaches more than the run was given.** Examples: a
   catalog tool offered with mode `off`; a tool offered while its capability is off; an MCP tool switched off still
   listed by the server; the adapter running a tool the run was not offered (a model replaying an old tool name);
   `shell_enabled` true without `shell_run`; per-run narrowing skipped for `code.graphs`. Pinned in:
   - Task 2: `test_only_selected_tools_with_met_needs_are_offered`, `test_the_adapter_refuses_a_tool_the_run_was_not_offered`;
   - Task 4: `test_the_model_request_carries_exactly_the_selected_tools`, `test_mcp_tools_switched_off_never_reach_the_model`,
     `test_per_run_narrowing_still_decides_what_tools_reach`;
   - Task 5: `test_a_per_run_run_gets_only_the_graphs_of_its_code_projects`.
2. **Existing agents change behaviour.** Examples: the backfill missing `report_not_covered` for grounded agents,
   giving file tools to grounded agents, rows for Claude Code agents; old versions or pinned sessions without
   `tools` losing tools; a tool's arguments, errors or description text changing in the move or the catalog; the
   moved prompt sentences lost. Pinned in:
   - Task 1: the whole existing suite unchanged except imports;
   - Task 2: `test_catalog_offers_todays_tools_for_every_capability_combination`,
     `test_descriptions_are_the_usage_text_and_keep_todays_guidance`, `test_the_prompt_no_longer_carries_tool_sentences`;
   - Task 3: `test_backfill_reproduces_todays_tools_for_every_capability_combination`,
     `test_versions_and_pinned_configs_without_tools_get_the_implied_tools`.
3. **A save leaves an inconsistent or silently changed selection.** Examples: grounded documents without
   `report_not_covered`; an unknown tool or an MCP tool of an unselected server accepted; a stale revision
   overwriting newer tools; a capability switched on with every tool of it off; a Claude Code agent switched to
   native with no tools; the wizard creating an agent without rows. Pinned in Task 6:
   `test_put_replaces_the_tools_and_enforces_the_save_rules`, `test_a_stale_revision_is_refused`,
   `test_switching_a_capability_on_switches_its_default_tools_on`, `test_a_claude_code_agent_switched_to_native_gets_todays_tools`,
   `test_the_wizard_saves_the_chosen_or_implied_tools`.
4. **Tool usage counts something else than the run.** Examples: a session's count including turns beyond
   `turns`, another session whose id shares a prefix (`cc:s1` vs `cc:s10`), other kinds than `tool_call`, the list
   ordering unstable. Pinned in Task 7: `test_tool_counts_count_tool_calls_of_exactly_the_given_runs`,
   `test_run_lists_and_records_carry_tool_counts`, `test_sessions_count_only_their_own_turns_and_runs`,
   `test_a_test_chat_counts_its_runs_and_each_run`.
5. **The screens show or save something else than what runs.** Examples: the Tools tab saving a reset text instead
   of NULL, an unmet tool shown as available, the Claude Code tab offering native tools, the wizard showing tools
   that do not follow the capability switches, the list column reading the working copy instead of the active
   version. Pinned in:
   - Task 6: `test_the_list_summarises_the_tools_that_run`;
   - Task 8: `the Tools tab switches tools, edits and resets usage and saves once`,
     `unmet needs are greyed out with their reason and link`, `a Claude Code agent lists its Claude Code tools`;
   - Task 9: `the wizard Tools page follows the capabilities until changed and sends the choice`,
     `All agents shows each agent's tools`;
   - Task 10: `runs list and record show the tools each run used`, `a Test chat answer shows its tools and Show config lists the
     conversation's`, `the session page shows the tools the session used`.

## Decisions (where the spec leaves a choice)

- **`capabilities/` holds plain async functions taking the run context dict** (`config`, `credentials`,
  `workspace`, `shell_enabled`, `tools`); no module there imports `agents` (the SDK). `capabilities/__init__.py`
  stays empty, because the shell worker container imports `capabilities.shell.worker` and must not import the
  application config. `code_search` and `ast_grep` move too, so every catalog `run` is SDK-free. MCP: only the
  SDK-free parts move (`selected_servers`, `auth_headers`); building `MCPServerStreamableHttp` stays in
  `agents.create_agent`, and MCP discovery stays in `mcp_api.py` (an API router).
- **The adapter's backstop reads `context['tools']`, filled by `agent()` from the tools actually given to the
  `Agent`** (`assistant.tools`), so the backstop and the model's list can never disagree.
- **A configuration without `tools` uses `implied_tools(config)`** (the backfill rule): versions saved before 0078,
  pinned test-chat and Generate documents configurations, and internal configurations (wizard designer, tuning
  reviewer, whose capabilities are all off). This is what keeps old conversations behaving exactly as before.
- **`code.graphs` is live, like file contents:** the loader recomputes it on every load, for the working copy and
  for versions (a version's saved graphs are replaced), only for native agents with code projects on. A project
  whose graph is missing or stale (`load_code_graph` refuses) is skipped; `CODE_INDEX_ROOT` or `CODE_ROOT` not
  configured means no graphs. Graph entries are `{id, name, graph_path, code_root, source_hash}`.
- **This makes `code_graph` reach the model for agents that have it on and a fresh graph.** That is the spec's
  intended fix, and the only change existing agents see; the backfill itself reproduces the table.
- **Save rules in the profile PUT:** switching code, documents or SSH on while none of that capability's own tools
  (`code`: code_search/ast_grep/code_graph; `documents`: search_documents/read_document/report_not_covered;
  `ssh`: ssh_run) is on switches its default tools on (the backfill rule for the new flags, file tools included);
  a grounded agent always gets `report_not_covered` on in the same save. Switching a capability off keeps rows.
  A Claude Code agent switched to native with no rows gets the implied rows.
- **The wizard sends `tools: list[str] | None`** (catalog names to switch on); `None` applies the backfill rule.
  MCP tools are not chosen in the wizard (they default to on).
- **Revision:** saving tools bumps `ka.agents.revision` (optimistic check `expected_revision`); the Tools tab then
  hands the new revision to the agent page so General keeps saving.
- **"The version diff lists the tools":** Versions has no diff view today; a snapshot's tools appear in Show config
  and `GET /api/agents/{id}/config?version_id=…`, which return the whole configuration.
- **Tool counts on sessions are read with the session** (detail load); live counts while a turn runs are not
  streamed.

## Delivery and test boundaries

Tasks land in order on `feature/agent-tools`, and every suite is green after each task. Task 1 (move) stands alone.
Task 2 (catalog, adapter) needs Task 1. Task 3 (migration, config) needs Task 2's `implied_tools`. Task 4 (MCP
filter, end-to-end selection) needs Tasks 2 and 3. Task 5 (graphs) needs Task 1. Task 6 (API and save rules) needs
Task 3. Task 7 (tool counts) stands alone. Tasks 8 and 9 (UI) need Task 6. Task 10 (usage UI) needs Task 7.
Task 11 updates the docs and runs every suite.

---
### Task 1: Move the tool implementations into `capabilities/` (pure move, no behaviour change)

**Files:**
- Create: `services/admin-api/src/knowledge_admin/capabilities/__init__.py` (docstring only)
- Create: `services/admin-api/src/knowledge_admin/capabilities/context.py`
- Move (`git mv`): `knowledge_admin/ssh.py` → `capabilities/ssh.py`; `knowledge_admin/shell_worker.py` →
  `capabilities/shell/worker.py`
- Create: `capabilities/shell/__init__.py` (docstring only), `capabilities/shell/client.py`, `capabilities/files.py`,
  `capabilities/documents.py`, `capabilities/code_graph.py`, `capabilities/code_search.py`, `capabilities/ast_grep.py`,
  `capabilities/mcp.py`
- Modify: `knowledge_admin/tools/__init__.py`, `tools/files.py`, `tools/documents.py`, `tools/shell.py`, `tools/ssh.py`,
  `tools/code_graph.py`, `tools/code_search.py`, `tools/ast_grep.py` (thin wrappers)
- Modify: `knowledge_admin/code_processing.py` (`load_code_graph` moves out), `agents.py`, `operations_api.py`,
  `operations_store.py`
- Modify: `deploy/compose.yaml` (shell-worker entrypoint)
- Modify tests (imports only): `test_ssh.py`, `test_operations.py`, `test_agents.py`, `test_todo.py`,
  `test_shell_worker.py`, `test_document_tools.py`, `test_run_resource_tools.py`, `test_code_processing.py`,
  `test_sdk_agent.py` (caplog logger name)
- Modify docs: `services/admin-api/README.md`, `services/admin-api/src/knowledge_admin/tools/README.md`

**Interfaces:**
- Produces: `capabilities.context.RunContext` (TypedDict: `config`, `credentials`, `workspace`, `shell_enabled`;
  `tools` added in Task 2), `capabilities.context.NOTHING_GIVEN`, `capabilities.context.require_given(config, kind)`;
  `capabilities.files`: `mounted`, `available_documents`, `_roots`, `_path`, `list_files(context, path=None,
  offset=0, limit=200)`, `read_file(context, path, offset=0, max_bytes=32768, start_line=None, end_line=None)`,
  `search_files(context, path, query, case_sensitive=False, max_matches=100)`; `capabilities.documents`:
  `RAG_TRANSPORT`, `MAX_DOCUMENT_BYTES`, `search_documents(context, query, collection=None)`,
  `read_document(context, collection, path)`, `report_not_covered(context, reason)`; `capabilities.shell.client.shell_run(context,
  command, timeout_seconds=60)`; `capabilities.ssh` (everything of the old `knowledge_admin.ssh`) plus
  `ssh_run(context, server_id, command, as_root=False, timeout_seconds=60)`; `capabilities.code_graph`:
  `load_code_graph(project, expected_hash=None)`, `code_graph(context, project_id, action='overview', query='',
  node_id='', offset=0, limit=50, include_code=False)`; `capabilities.code_search.code_search(context, path, language,
  name, match='exact', offset=0, limit=5)`; `capabilities.ast_grep`: `scan_source`, `ast_grep(context, path,
  language, pattern=None, rule=None, max_matches=20)`; `capabilities.mcp`: `selected_servers(config, ssh_servers)`,
  `auth_headers(server, credentials)`.
- Unchanged: every tool's name, docstring (the model description), arguments, results and error texts;
  `knowledge_admin.tools.AgentContext` (now an alias of `RunContext`); `agents.get_tools`, `get_prompt`,
  `get_paths`, `create_agent`.

- [ ] **Step 1: Write the failing test**

Create `services/admin-api/tests/test_capabilities_layout.py`:

```python
"""Implementations live in knowledge_admin.capabilities without the Agents SDK; tools/ only wraps them."""
import ast
from pathlib import Path

import knowledge_admin

PACKAGE = Path(knowledge_admin.__file__).resolve().parent
CAPABILITIES = PACKAGE / 'capabilities'


def imported_modules(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_capabilities_hold_the_implementations_without_the_agents_sdk():
    expected = {'__init__.py', 'context.py', 'files.py', 'documents.py', 'ssh.py', 'code_graph.py', 'code_search.py',
                'ast_grep.py', 'mcp.py', 'shell/__init__.py', 'shell/client.py', 'shell/worker.py'}
    assert {str(path.relative_to(CAPABILITIES)) for path in CAPABILITIES.rglob('*.py')} == expected
    for path in CAPABILITIES.rglob('*.py'):
        assert not {name for name in imported_modules(path) if name == 'agents' or name.startswith('agents.')}, path
    assert (CAPABILITIES / '__init__.py').read_text().strip().startswith('"""')
    assert imported_modules(CAPABILITIES / '__init__.py') == set()
    assert not (PACKAGE / 'ssh.py').exists() and not (PACKAGE / 'shell_worker.py').exists()


def test_the_shell_worker_imports_without_the_application_config():
    worker = imported_modules(CAPABILITIES / 'shell/worker.py')
    assert not {name for name in worker if name.startswith('knowledge_admin')}


def test_tools_only_wrap_capabilities():
    for name in ('files', 'documents', 'shell', 'ssh', 'code_graph', 'code_search', 'ast_grep'):
        source = (PACKAGE / 'tools' / f'{name}.py').read_text(encoding='utf-8')
        assert 'knowledge_admin.capabilities' in source, name
        for implementation in ('asyncio.create_subprocess_exec', 'httpx.AsyncClient(', 'SSHConnection('):
            assert implementation not in source, (name, implementation)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_capabilities_layout.py -q`
Expected: FAIL: `capabilities` does not exist (`FileNotFoundError`/empty set mismatch).

- [ ] **Step 3: Create the package and move the modules**

```bash
cd services/admin-api/src/knowledge_admin
mkdir -p capabilities/shell
git mv ssh.py capabilities/ssh.py
git mv shell_worker.py capabilities/shell/worker.py
```

`capabilities/__init__.py`:

```python
"""What agents can do, implemented without any agent framework: files, documents, the shell sandbox, SSH, code
graphs and structural search, MCP connections. knowledge_admin.tools adapts these to the model."""
```

`capabilities/shell/__init__.py`:

```python
"""The isolated shell: client.py sends commands from the API process, worker.py runs them in the sandbox."""
```

`capabilities/context.py` (moved from `tools/__init__.py`):

```python
"""The run context every capability receives, and the per-run rule for kinds a run was given nothing of."""
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict

from knowledge_admin.collections import document_collections
from knowledge_admin.knowledge_agents.config import per_run, require_run_resources


class RunContext(TypedDict):
    """Backend-only inputs of one run: the configuration snapshot, resolved credentials, the scratch workspace and
    whether the shell may run. None of these is a model-supplied argument."""

    config: dict[str, Any]
    credentials: Mapping[str, str]
    workspace: Path
    shell_enabled: bool


NOTHING_GIVEN = {
    'code': 'No code project was given for this run.',
    'documents': 'No document collection was given for this run.',
    'ssh': 'No server was given for this run.',
    'files': 'No code project or document collection was given for this run.',
}


def require_given(config: dict[str, Any], kind: str) -> None:
    """Per-run agents: refuse a tool whose kind the run was given nothing of. Their configuration lists exactly the
    run's resources, so an empty list means nothing, never every resource. Fixed agents are unaffected."""
    if not per_run(config):
        return
    require_run_resources(config)
    code = config['code']['projects'] if config['code']['enabled'] else []
    collections = document_collections(config)[1] if config['documents']['enabled'] else []
    empty = {'code': not code, 'documents': not collections, 'ssh': not config['ssh']['servers'],
             'files': not code and not collections}
    if empty[kind]:
        raise ValueError(NOTHING_GIVEN[kind])
```

`tools/__init__.py` becomes:

```python
"""Model-facing tools over knowledge_admin.capabilities."""
from knowledge_admin.capabilities.context import RunContext as AgentContext  # noqa: F401  (the SDK run context)
```

- [ ] **Step 4: Move each implementation, leaving a thin tool**

The rule for every tool below: the capability function has the tool's parameters without `ctx` (same names, same
defaults, same order), takes `context: RunContext` first, and its body is the former tool body with `ctx.context`
replaced by `context`; the docstring stays on the tool. Helpers move with it unchanged (`_roots`, `_path`,
`mounted`, `available_documents`, `_state`, `_allowed`, `_client`, `_project`, `_register`, `RAG_TRANSPORT`,
`MAX_DOCUMENT_BYTES`, `scan_source`), and every `from knowledge_admin.tools import AgentContext, require_given`
becomes `from knowledge_admin.capabilities.context import RunContext, require_given`.

- `capabilities/files.py` ← `tools/files.py` (logger name becomes `knowledge_admin.capabilities.files`);
- `capabilities/documents.py` ← `tools/documents.py`;
- `capabilities/code_search.py` ← `tools/code_search.py` (imports `scan_source` from `capabilities.ast_grep`,
  `_path` from `capabilities.files`);
- `capabilities/ast_grep.py` ← `tools/ast_grep.py`;
- `capabilities/code_graph.py` ← the body of `tools/code_graph.py` plus `load_code_graph` cut from
  `code_processing.py` (it imports `index_directory`, `source_inventory`, `CodeProcessingError` from
  `knowledge_admin.code_processing` and `code_root` from `knowledge_admin.code_paths`). The bridge path
  `Path(__file__).resolve().parents[4] / 'code-graph/query.cjs'` stays correct (same depth under `src/`).
- `capabilities/shell/client.py` ← `tools/shell.py` (imports `_roots` from `capabilities.files`);
- `capabilities/ssh.py` gains `ssh_run(context, ...)` with the body of `tools/ssh.py` (it uses the module's own
  `SSHConnection` and `execute`).

The thin tools: in each `tools/*.py` function keep the `@function_tool` decorator, the signature and the docstring
exactly as they are (they are the model-facing schema and description), drop the moved helpers, import the
capability module (e.g. `from knowledge_admin.capabilities import files`) and replace the body after the docstring
with one line:

| Tool (module) | Body |
| --- | --- |
| `list_files` (`tools/files.py`) | `return await files.list_files(ctx.context, path, offset, limit)` |
| `read_file` | `return await files.read_file(ctx.context, path, offset, max_bytes, start_line, end_line)` |
| `search_files` | `return await files.search_files(ctx.context, path, query, case_sensitive, max_matches)` |
| `search_documents` (`tools/documents.py`) | `return await documents.search_documents(ctx.context, query, collection)` |
| `read_document` | `return await documents.read_document(ctx.context, collection, path)` |
| `report_not_covered` | `return await documents.report_not_covered(ctx.context, reason)` |
| `shell_run` (`tools/shell.py`, `from knowledge_admin.capabilities.shell import client`) | `return await client.shell_run(ctx.context, command, timeout_seconds)` |
| `ssh_run` (`tools/ssh.py`) | `return await ssh.ssh_run(ctx.context, server_id, command, as_root, timeout_seconds)` |
| `code_graph` (`tools/code_graph.py`) | `return await code_graph.code_graph(ctx.context, project_id, action, query, node_id, offset, limit, include_code)` |
| `code_search` (`tools/code_search.py`) | `return await code_search.code_search(ctx.context, path, language, name, match, offset, limit)` |
| `ast_grep` (`tools/ast_grep.py`) | `return await ast_grep.ast_grep(ctx.context, path, language, pattern, rule, max_matches)` |

Each module keeps `from knowledge_admin.tools import AgentContext` for the `RunContextWrapper` annotation.

`capabilities/mcp.py` (from `agents.get_mcp` and the header code in `create_agent`):

```python
"""Which MCP servers an agent connects to and how it authenticates; the connection itself is built by the runner."""
from collections.abc import Mapping
from typing import Any


def selected_servers(config: dict[str, Any], ssh_servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enabled MCP definitions of the agent and of its selected SSH servers, each once."""
    if not config['mcp']['enabled']:
        return []
    servers = list(config['mcp']['servers'])
    for server in ssh_servers:
        servers.extend(server['mcp_servers'])
    return list({server['id']: server for server in servers if server['enabled']}.values())


def auth_headers(server: dict[str, Any], credentials: Mapping[str, str]) -> dict[str, str]:
    """The request headers for one MCP server; its token must have been resolved by the backend."""
    reference = server['bearer_token_ref']
    if reference is None:
        return {}
    if not credentials.get(reference['id']):
        raise ValueError(f"The MCP token for {server['name']} was not supplied.")
    return {'Authorization': 'Bearer ' + credentials[reference['id']]}
```

In `agents.py`: `from knowledge_admin.capabilities import mcp`; delete `get_mcp`; `selected_mcp =
mcp.selected_servers(config, ssh_servers)`; the MCP loop becomes:

```python
        for server in selected_mcp:
            mcp_servers.append(await connections.enter_async_context(MCPServerStreamableHttp(
                name=server['id'], params={'url': server['url'], 'headers': mcp.auth_headers(server, credentials)},
                cache_tools_list=True, client_session_timeout_seconds=timeout_seconds)))
```

and `from knowledge_admin.tools.files import available_documents, list_files, mounted, read_file, search_files`
becomes `from knowledge_admin.capabilities.files import available_documents, mounted` plus
`from knowledge_admin.tools.files import list_files, read_file, search_files`.

- [ ] **Step 5: Update every other import**

- `operations_api.py`: `from knowledge_admin.capabilities import ssh`;
- `operations_store.py`: `from knowledge_admin.capabilities.ssh import SSHConnection`;
- `deploy/compose.yaml` shell-worker entrypoint: `knowledge_admin.capabilities.shell.worker:app`;
- tests: `test_ssh.py` → `from knowledge_admin.capabilities.ssh import …`; `test_operations.py` →
  `from knowledge_admin.capabilities import ssh`; `test_agents.py`, `test_todo.py` →
  `monkeypatch.setattr('knowledge_admin.capabilities.ssh.execute', forbidden)`; `test_shell_worker.py` →
  `from knowledge_admin.capabilities.shell.worker import ShellRequest, app, execute`; `test_document_tools.py` →
  `from knowledge_admin.capabilities import documents` (the `RAG_TRANSPORT` patch target); `test_run_resource_tools.py`
  → `from knowledge_admin.capabilities.context import NOTHING_GIVEN`; `test_code_processing.py` →
  `from knowledge_admin.capabilities.code_graph import load_code_graph`; `test_sdk_agent.py` →
  `caplog.at_level('WARNING', logger='knowledge_admin.capabilities.files')`;
- `services/admin-api/README.md` line 177: `knowledge_admin/capabilities/files.py` (wrapped by
  `knowledge_admin/tools/files.py`); `tools/README.md`: `knowledge_admin.capabilities.ssh.execute`, and a first
  paragraph: "Implementations live in `knowledge_admin/capabilities/`; these modules only adapt them to the
  Agents SDK."

Then confirm nothing references the old paths:

Run: `git grep -n -E "knowledge_admin\.ssh\b|knowledge_admin import ssh|knowledge_admin\.shell_worker|tools import (documents|NOTHING_GIVEN)|code_processing import .*load_code_graph" -- ':!docs/superpowers'`
Expected: no output.

- [ ] **Step 6: Run the suites**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_capabilities_layout.py tests/test_function_tools.py tests/test_document_tools.py tests/test_run_resource_tools.py tests/test_code_search.py tests/test_ast_grep.py tests/test_code_graph_sdk.py tests/test_code_processing.py tests/test_shell_worker.py tests/test_ssh.py tests/test_operations.py tests/test_agent_ssh.py tests/test_sdk_agent.py tests/test_agents.py tests/test_todo.py -q`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS (no test other than the import lines changed).

- [ ] **Step 7: Commit**

```bash
git add -A services/admin-api deploy/compose.yaml
git commit -m "refactor(agents): move tool implementations into knowledge_admin.capabilities

Files, documents, the shell client and sandbox worker, SSH, code graph loading and queries, code search,
ast-grep and MCP server selection now live in capabilities/ without the Agents SDK; tools/ keeps thin
function tools. No behaviour change.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The tool catalog and the SDK adapter

**Files:**
- Create: `services/admin-api/src/knowledge_admin/tools/catalog.py`, `services/admin-api/src/knowledge_admin/tools/sdk.py`
- Delete: `tools/files.py`, `tools/documents.py`, `tools/shell.py`, `tools/ssh.py`, `tools/code_graph.py`,
  `tools/code_search.py`, `tools/ast_grep.py` (their docstrings become catalog usage text)
- Modify: `capabilities/context.py` (`RunContext.tools`), `agents.py` (`agent`, `create_agent`, `get_prompt`; `get_tools` removed)
- Create: `services/admin-api/tests/tool_helpers.py`, `services/admin-api/tests/test_tool_catalog.py`
- Modify tests (imports): `test_function_tools.py`, `test_document_tools.py`, `test_run_resource_tools.py`,
  `test_code_search.py`, `test_ast_grep.py`, `test_code_graph_sdk.py`, `test_code_processing.py`, `test_shell_worker.py`

**Interfaces:**
- Consumes: Task 1's capabilities.
- Produces: `catalog.ToolSpec` (spec fields: `name`, `group`, `label`, `summary`, `usage`, `arguments`, `run`,
  `needs`, `default_on`), `catalog.CATALOG: dict[str, ToolSpec]` (ordered), `catalog.NEED_REASONS`,
  `catalog.NEED_TABS`, `catalog.needs_met(need, config, ssh_servers)`, `catalog.implied_tools(config) -> {'items': [...]}`,
  `catalog.tools_of(config)`, `catalog.effective_tools(config, ssh_servers) -> list[tuple[ToolSpec, str]]`;
  `sdk.function_tool(spec, usage) -> FunctionTool`, `sdk.NOT_OFFERED`; `RunContext.tools: frozenset[str]`.
- Removed: `agents.get_tools`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/tool_helpers.py`:

```python
"""Catalog tools as the model gets them (tools/sdk.py), for tests that call one tool directly. The run context
offers every catalog tool unless the test sets context['tools'] itself."""
import dataclasses

from agents import FunctionTool

from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools.sdk import function_tool


def tools(*names: str) -> list[FunctionTool]:
    built = []
    for name in names:
        spec = CATALOG[name]
        sdk_tool = function_tool(spec, spec.usage)

        async def invoke(ctx, raw, _invoke=sdk_tool.on_invoke_tool):
            ctx.context.setdefault('tools', frozenset(CATALOG))
            return await _invoke(ctx, raw)
        built.append(dataclasses.replace(sdk_tool, on_invoke_tool=invoke))
    return built


def tool(name: str) -> FunctionTool:
    return tools(name)[0]
```

In every test module that imported a tool from `knowledge_admin.tools.<module>`, replace the import with
`from tool_helpers import tools` and, e.g. in `test_function_tools.py`,
`list_files, read_file, search_files, ast_grep, code_graph, code_search, shell_run, ssh_run = tools('list_files',
'read_file', 'search_files', 'ast_grep', 'code_graph', 'code_search', 'shell_run', 'ssh_run')` (each module
unpacks the names it used). In `test_document_tools.py` replace `get_tools` with
`effective_tools` (`names = {spec.name for spec, _ in effective_tools(grounded, [])}`).

Create `services/admin-api/tests/test_tool_catalog.py`:

```python
"""The catalog: SDK-independent tools with usage text, the "offered today" rule, selection and the adapter."""
import asyncio
import itertools
import json

import pytest
from agents import FunctionTool
from agents.tool_context import ToolContext

from knowledge_admin.agents import get_prompt
from knowledge_admin.tools.catalog import CATALOG, effective_tools, implied_tools
from knowledge_admin.tools.sdk import NOT_OFFERED, function_tool

SERVER = {'id': 'host', 'name': 'Host', 'hostname': 'host.test', 'port': 22, 'username': 'ops', 'description': '',
          'tags': [], 'allow_sudo': False, 'instructions': '', 'enabled': True, 'ssh_allowed': True, 'skills': [],
          'mcp_servers': []}


def config(code=False, documents=False, grounded=False, ssh=False, graphs=(), tools=None):
    value = {'id': 'agent', 'name': 'Agent', 'description': '', 'instructions': '', 'enabled': True,
             'runtime': 'openai', 'resources': 'fixed', 'system_prompt': {'content': 'Answer.'},
             'code': {'enabled': code, 'projects': [], 'graphs': list(graphs)},
             'documents': {'enabled': documents, 'grounded': grounded, 'all_collections': False, 'collections': []},
             'ssh': {'enabled': ssh, 'servers': [SERVER] if ssh else []},
             'skills': {'enabled': False, 'items': []}, 'services': {'enabled': False, 'items': []},
             'mcp': {'enabled': False, 'servers': []}}
    if tools is not None:
        value['tools'] = {'items': [{'tool': name, 'group': CATALOG[name].group, 'mode': 'on', 'usage': None,
                                     'subagent_allowed': True} for name in tools]}
    return value


def todays_tools(c, ssh_servers) -> set[str]:
    """agents.get_tools before this change, kept here as the reference."""
    names = set()
    grounded = c['documents']['enabled'] and c['documents'].get('grounded', False)
    if c['code']['enabled'] or c['documents']['enabled'] and not grounded:
        names |= {'list_files', 'read_file', 'search_files', 'shell_run'}
    if c['documents']['enabled']:
        names |= {'search_documents', 'read_document'}
    if grounded:
        names.add('report_not_covered')
    if c['code']['enabled']:
        names |= {'code_search', 'ast_grep'}
    if c['code']['enabled'] and c['code'].get('graphs'):
        names.add('code_graph')
    if ssh_servers:
        names.add('ssh_run')
    return names


def offered(c, ssh_servers=()) -> set[str]:
    return {spec.name for spec, _ in effective_tools(c, list(ssh_servers))}


@pytest.mark.parametrize('code, documents, grounded, ssh, graph', list(itertools.product([False, True], repeat=5)))
def test_catalog_offers_todays_tools_for_every_capability_combination(code, documents, grounded, ssh, graph):
    graphs = [{'id': 'p', 'name': 'P', 'graph_path': '/g', 'code_root': '/c', 'source_hash': 'h'}] if graph else []
    c = config(code, documents, grounded, ssh, graphs)
    servers = [SERVER] if ssh else []
    assert offered(c, servers) == todays_tools(c, servers)  # without 'tools': the implied tools
    c['tools'] = implied_tools(c)
    assert offered(c, servers) == todays_tools(c, servers)  # the same rule, written out


def test_only_selected_tools_with_met_needs_are_offered():
    c = config(code=True, documents=True, ssh=True, tools=['read_file', 'ssh_run', 'search_documents', 'code_graph'])
    assert offered(c, [SERVER]) == {'read_file', 'ssh_run', 'search_documents'}  # code_graph: no graph
    assert offered(c, []) == {'read_file', 'search_documents'}  # ssh_run: no usable server
    c['code']['enabled'] = False
    assert offered(c, [SERVER]) == {'read_file', 'ssh_run', 'search_documents'}  # documents ungrounded: files
    c['documents']['grounded'] = True
    assert offered(c, [SERVER]) == {'ssh_run', 'search_documents'}
    c['tools']['items'][0]['mode'] = 'off'
    c['tools']['items'][2]['mode'] = 'per_run'  # not offered in step 1
    assert offered(c, [SERVER]) == {'ssh_run'}
    assert offered(config(code=True, tools=[]), []) == set()


def test_descriptions_are_the_usage_text_and_keep_todays_guidance():
    for spec in CATALOG.values():
        built = function_tool(spec, spec.usage)
        assert isinstance(built, FunctionTool) and built.name == spec.name and built.description == spec.usage
        assert built.strict_json_schema and built.params_json_schema['additionalProperties'] is False
        assert not {'ctx', 'context', 'config', 'credentials', 'workspace'} & built.params_json_schema['properties'].keys()
    assert 'SetPersonalLimits' in CATALOG['ast_grep'].usage and 'native rule' in CATALOG['ast_grep'].usage
    assert 'complete bodies' in CATALOG['code_search'].usage
    assert 'Selected resource paths are mounted read-only' in CATALOG['shell_run'].usage
    assert 'Use code_graph first' in CATALOG['code_graph'].usage
    assert 'Request as_root only when allow_sudo is true' in CATALOG['ssh_run'].usage
    assert set(CATALOG['ssh_run'].arguments.model_json_schema()['properties']) == {'server_id', 'command', 'as_root', 'timeout_seconds'}
    c = config(code=True, tools=['read_file'])
    c['tools']['items'][0]['usage'] = 'Read only the files you were asked about.'
    [(spec, usage)] = effective_tools(c, [])
    assert usage == 'Read only the files you were asked about.'
    assert function_tool(spec, usage).description == usage


def test_the_adapter_refuses_a_tool_the_run_was_not_offered(tmp_path):
    (tmp_path / 'a.txt').write_text('hello', encoding='utf-8')
    c = config(code=True)
    c['code']['projects'] = [{'id': 'p', 'name': 'P', 'folder_paths': [str(tmp_path.resolve())]}]
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    context = {'config': c, 'credentials': {}, 'workspace': workspace.resolve(), 'shell_enabled': False,
               'tools': frozenset({'list_files'})}
    read = function_tool(CATALOG['read_file'], CATALOG['read_file'].usage)
    payload = json.dumps({'path': str((tmp_path / 'a.txt').resolve())})
    wrapped = ToolContext(context=context, tool_name='read_file', tool_call_id='t1', tool_arguments=payload)
    refused = asyncio.run(read.on_invoke_tool(wrapped, payload))
    assert NOT_OFFERED.format(name='read_file') in refused and 'hello' not in refused
    context['tools'] = frozenset({'read_file'})
    assert 'hello' in asyncio.run(read.on_invoke_tool(wrapped, payload))
    invalid = asyncio.run(read.on_invoke_tool(wrapped, json.dumps({'offset': 1})))
    assert invalid.startswith('An error occurred while running the tool.')  # the SDK's own error text


def test_the_prompt_no_longer_carries_tool_sentences():
    prompt = get_prompt(config(code=True, documents=True, ssh=True), [SERVER], [])
    for sentence in ('Use shell_run for Bash pipelines', 'Use code_search to find', 'Use code_graph first',
                     'Use search_documents to find', 'Use ssh_run with the configured server ID'):
        assert sentence not in prompt
    assert 'Use only the configured resources and tools.' in prompt and '"ssh_servers"' in prompt
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_tool_catalog.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'knowledge_admin.tools.catalog'`.

- [ ] **Step 3: Write the adapter**

Create `tools/sdk.py`:

```python
"""Bind catalog tools to the OpenAI Agents SDK. The only module that knows how a tool reaches the model; another
framework would replace this file, not the catalog."""
from typing import Any

from agents import FunctionTool
from agents.strict_schema import ensure_strict_json_schema
from agents.tool import default_tool_error_function
from agents.tool_context import ToolContext

from knowledge_admin.tools.catalog import ToolSpec

NOT_OFFERED = 'Tool {name} is not available in this run.'


def function_tool(spec: ToolSpec, usage: str) -> FunctionTool:
    """One SDK function tool: the usage text is its description and the argument model its strict JSON schema.

    Invocation validates the arguments, refuses a tool missing from context['tools'] (the run's offered tools, a
    backstop) and runs the catalog function. Errors go back to the model as the SDK's default error text, as with
    decorated function tools; cancellation propagates."""
    async def invoke(ctx: ToolContext[Any], raw: str) -> str:
        try:
            arguments = spec.arguments.model_validate_json(raw or '{}')
            if spec.name not in ctx.context.get('tools', ()):
                raise ValueError(NOT_OFFERED.format(name=spec.name))
            return await spec.run(ctx.context, arguments)
        except Exception as exc:
            return default_tool_error_function(ctx, exc)

    return FunctionTool(name=spec.name, description=usage,
                        params_json_schema=ensure_strict_json_schema(spec.arguments.model_json_schema()),
                        on_invoke_tool=invoke, strict_json_schema=True)
```

- [ ] **Step 4: Write the catalog**

Create `tools/catalog.py`. The `usage` of each tool is the docstring of its former `tools/*.py` function **without
the `Args:` block** (that is exactly the description the SDK sent until now), followed by the sentences
`get_prompt` hard-coded for that tool; the `Args:` lines become `Field(description=...)` on the argument model,
word for word.

```python
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
```

(The first paragraphs of `SHELL_RUN`, `CODE_SEARCH`, `AST_GREP` and `CODE_GRAPH` are today's SDK descriptions
without `Args:`. `shell_run`'s description today also carried its indented `Args:` block, because the docstring's
heredoc example broke the SDK's docstring parsing; its usage text is the same paragraphs, dedented, without it.)

Then delete the seven thin wrappers:

```bash
cd services/admin-api/src/knowledge_admin/tools
git rm files.py documents.py shell.py ssh.py code_graph.py code_search.py ast_grep.py
```

In `capabilities/context.py` add to `RunContext`: `tools: frozenset[str]  # the catalog tools offered to this run`.

- [ ] **Step 5: Build tools from the catalog in `agents.py`**

Imports: remove the seven tool imports; add
`from knowledge_admin.tools.catalog import effective_tools` and `from knowledge_admin.tools.sdk import function_tool`.
Delete `get_tools`. In `create_agent`, replace `tools = get_tools(config, ssh_servers)` with:

```python
    tools = [function_tool(spec, usage) for spec, usage in effective_tools(config, ssh_servers)]
```

In `agent()`, the context starts without tools and gets the offered names once the agent is built:

```python
        context = {'config': config, 'credentials': credentials, 'workspace': workspace,
                   'shell_enabled': False, 'tools': frozenset()}
```

and right after `create_agent(...) as assistant:`:

```python
                    # The adapter's backstop and the shell switch follow exactly the tools the model was given.
                    context['tools'] = frozenset(tool.name for tool in assistant.tools)
                    context['shell_enabled'] = 'shell_run' in context['tools']
```

In `get_prompt`, delete every tool sentence: the `prompt += '\nUse shell_run …'` block (four lines), the
`prompt += '\nUse code_search …'` block (four lines), the `prompt += '\nUse code_graph first …'` block (four lines),
`prompt += '\nUse search_documents …'` and the final `if ssh_servers: prompt += "\nUse ssh_run …"`. The
`resources[...]` assignments, `grounding.POLICY`, the per-run sentence and the general rules stay.

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_tool_catalog.py tests/test_function_tools.py tests/test_document_tools.py tests/test_run_resource_tools.py tests/test_code_search.py tests/test_ast_grep.py tests/test_code_graph_sdk.py tests/test_code_processing.py tests/test_shell_worker.py tests/test_sdk_agent.py tests/test_agent_ssh.py tests/test_capabilities_layout.py -q`
Expected: PASS. (`test_capabilities_layout.test_tools_only_wrap_capabilities` loops over the deleted wrappers:
change its loop to `for name in ('catalog',):` and its first assertion to `'knowledge_admin.capabilities' in source`;
keep the implementation checks.)

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A services/admin-api
git commit -m "feat(tools): SDK-independent tool catalog with usage text and one Agents SDK adapter

Each built-in tool is a ToolSpec (arguments, usage text, need, the capability rule that switched it on); tools/sdk.py
builds the FunctionTool, refuses tools the run was not offered and keeps the SDK's error text. The prompt's tool
sentences move into the usage text. Agents still get exactly the tools their capabilities give.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 3: `0078_agent_tools`, the backfill and `tools` in the configuration and its versions

**Files:**
- Create: `db/migrations/0078_agent_tools.up.sql`, `db/migrations/0078_agent_tools.down.sql`
- Modify: `db/tests/run.sh` (rollback pin `76` → `77`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/config.py`
- Create: `services/admin-api/tests/test_agent_tools_migration.py`, `services/admin-api/tests/test_agent_tools_config.py`

**Interfaces:**
- Consumes: `catalog.CATALOG`, `catalog.item`, `catalog.implied_tools` (Task 2).
- Produces: `ka.agent_tools`; `AgentConfig.tools: dict[str, list[dict]]`; `config.tool_items(rows) -> {'items': [...]}`
  (catalog tools in catalog order, a missing row → `off`; then MCP rows by name).

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_agent_tools_migration.py`:

```python
"""0078: an agent's tools, backfilled from today's capability rule; the checks; up and down."""
import itertools
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from knowledge_admin.tools.catalog import implied_tools

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0078_agent_tools.up.sql', '0078_agent_tools.down.sql'
COMBINATIONS = list(itertools.product([False, True], repeat=4))  # code, documents, grounded, ssh


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def add_agent(conn, agent_id, *, runtime='openai', code=False, documents=False, grounded=False, ssh=False):
    conn.execute('''INSERT INTO ka.agents(id,name,runtime,repositories_enabled,documents_enabled,documents_grounded,ssh_enabled)
        VALUES (%s,%s,%s,%s,%s,%s,%s)''', (agent_id, agent_id, runtime, code, documents, grounded, ssh))
    version = uuid4()
    conn.execute('INSERT INTO ka.agent_prompts(agent,active_version_id,next_version) VALUES (%s,%s,2)', (agent_id, version))
    conn.execute('''INSERT INTO ka.agent_prompt_versions(id,agent,version,title,content,published_at)
        VALUES (%s,%s,1,'Prompt','Answer.',now())''', (version, agent_id))


def flags(code, documents, grounded, ssh):
    return {'runtime': 'openai', 'code': {'enabled': code}, 'documents': {'enabled': documents, 'grounded': grounded},
            'ssh': {'enabled': ssh}}


def rows(conn, agent_id):
    return {row['tool']: row for row in conn.execute(
        'SELECT tool,mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s', (agent_id,))}


def test_backfill_reproduces_todays_tools_for_every_capability_combination(store):
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        for index, combination in enumerate(COMBINATIONS):
            add_agent(conn, f'agent-{index}', **dict(zip(('code', 'documents', 'grounded', 'ssh'), combination)))
        add_agent(conn, 'writer', runtime='claude_code', code=True, documents=True)  # Claude Code: no rows
        conn.commit()
        run(conn, UP)
        conn.commit()
        for index, combination in enumerate(COMBINATIONS):
            expected = {entry['tool'] for entry in implied_tools(flags(*combination))['items'] if entry['mode'] == 'on'}
            saved = rows(conn, f'agent-{index}')
            assert set(saved) == expected, combination
            assert all(row['mode'] == 'on' and row['usage'] is None and row['subagent_allowed'] for row in saved.values())
        assert rows(conn, 'writer') == {}
        # the seeded Search agent answers only from documents
        assert set(rows(conn, 'search')) == {'search_documents', 'read_document', 'report_not_covered'}


def test_checks_and_cascade(store):
    with store.connect() as conn:
        for statement, error in (
                ("INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES ('code','read_file','sometimes')", psycopg.errors.CheckViolation),
                ("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('code','')", psycopg.errors.CheckViolation),
                ("INSERT INTO ka.agent_tools(agent_id,tool,usage) VALUES ('code','x', repeat('a', 8001))", psycopg.errors.CheckViolation),
                ("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('code','run_subagent')", psycopg.errors.CheckViolation),
                ("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('code','ask_agent:search')", psycopg.errors.CheckViolation),
                ("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('missing','read_file')", psycopg.errors.ForeignKeyViolation),
                ("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('code','read_file')", psycopg.errors.UniqueViolation)):
            with pytest.raises(error), conn.transaction():
                conn.execute(statement)
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,subagent_allowed) VALUES ('code','run_subagent',false)")
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id='code' AND tool='run_subagent'")


def test_down_drops_the_table_and_up_backfills_again(store):
    with store.connect() as conn:
        run(conn, DOWN)
        conn.commit()
        assert conn.execute("SELECT to_regclass('ka.agent_tools') AS t").fetchone()['t'] is None
        run(conn, UP)
        conn.commit()
        assert rows(conn, 'code')
```

Create `services/admin-api/tests/test_agent_tools_config.py`:

```python
"""The configuration carries the agent's tools; versions keep them; configurations saved before 0078 get the implied
tools; Claude Code agents have none."""
import pytest

from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.tools.catalog import CATALOG, implied_tools
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_agent_resources import new_version
from test_claude_code_agents import claude_code_agent
from test_repositories import HEADERS


def modes(config) -> dict[str, str]:
    return {entry['tool']: entry['mode'] for entry in config['tools']['items']}


def test_the_configuration_lists_every_catalog_tool_and_the_changed_mcp_tools(store, saved_agent):
    mcp = saved_agent[4]
    with store.connect() as conn:
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id='operations'")
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,usage) VALUES ('operations','read_file','Read narrowly.')")
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES ('operations',%s,'off')", (f"mcp:{mcp['id']}:lookup",))
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert [entry['tool'] for entry in config['tools']['items']] == [*CATALOG, f"mcp:{mcp['id']}:lookup"]
    assert modes(config)['read_file'] == 'on' and modes(config)['list_files'] == 'off'
    assert config['tools']['items'][1] == {'tool': 'read_file', 'group': 'code', 'mode': 'on',
                                           'usage': 'Read narrowly.', 'subagent_allowed': True}
    assert config['tools']['items'][-1]['group'] == 'mcp' and modes(config)[f"mcp:{mcp['id']}:lookup"] == 'off'


def test_versions_keep_the_tools(store, saved_agent):
    client = saved_agent[0]
    with store.connect() as conn:
        conn.execute("DELETE FROM ka.agent_tools WHERE agent_id='operations' AND tool<>'read_file'")
    version = new_version(client, 'Only read_file')
    with store.connect() as conn:
        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool) VALUES ('operations','ssh_run')")
    pinned = get_agent_config('operations', conninfo=store.conninfo, version_id=version['id'])
    assert {tool for tool, mode in modes(pinned).items() if mode == 'on'} == {'read_file'}
    working = get_agent_config('operations', conninfo=store.conninfo, working_copy=True)
    assert {tool for tool, mode in modes(working).items() if mode == 'on'} == {'read_file', 'ssh_run'}


def test_versions_and_pinned_configs_without_tools_get_the_implied_tools(store, saved_agent):
    client = saved_agent[0]
    version = new_version(client, 'Before 0078')
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_versions SET config=config-'tools' WHERE agent='operations'")
    old = get_agent_config('operations', conninfo=store.conninfo, version_id=version['id'])
    assert old['tools'] == implied_tools(old)


def test_claude_code_agents_have_no_catalog_tools(store, saved_agent):
    writer = claude_code_agent(saved_agent[0])
    assert get_agent_config(writer, conninfo=store.conninfo)['tools'] == {'items': []}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_tools_migration.py tests/test_agent_tools_config.py -q`
Expected: FAIL: `FileNotFoundError: … 0078_agent_tools.down.sql` and `KeyError: 'tools'`.

- [ ] **Step 3: Write the migration**

`db/migrations/0078_agent_tools.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- An agent's tools: each switched on or off, with optional usage instructions that replace the built-in text.
-- Catalog tools without a row are off; MCP tools without a row are on with the server's description.
CREATE TABLE agent_tools (
    agent_id         text NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tool             text NOT NULL CHECK (char_length(tool) BETWEEN 1 AND 300),
        -- catalog name ('read_file'), 'mcp:<mcp_server_id>:<tool name>' or, later, 'ask_agent:<agent id>'
    mode             text NOT NULL DEFAULT 'on' CHECK (mode IN ('off','on','per_run')),
        -- per_run: off unless a caller switches it on for one run (a later step)
    usage            text CHECK (usage IS NULL OR char_length(usage) <= 8000),
        -- NULL: the built-in usage instructions (catalog) or the MCP server's description
    subagent_allowed boolean NOT NULL DEFAULT true,
        -- run_subagent may give this tool to a subagent (a later step)
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, tool),
    CONSTRAINT subagents_do_not_spawn CHECK (NOT subagent_allowed OR (tool <> 'run_subagent' AND tool NOT LIKE 'ask_agent:%'))
);
COMMENT ON TABLE agent_tools IS 'The tools of each native agent and their usage instructions.';

-- Every native agent keeps the tools its capabilities gave it before tools were chosen (knowledge_admin.tools.catalog
-- implied_tools). code_graph is still offered only when a selected code project has a generated graph.
INSERT INTO agent_tools(agent_id, tool)
SELECT a.id, t.tool
FROM agents a
CROSS JOIN LATERAL (VALUES
    ('list_files',         a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('read_file',          a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('search_files',       a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('shell_run',          a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('code_search',        a.repositories_enabled),
    ('ast_grep',           a.repositories_enabled),
    ('code_graph',         a.repositories_enabled),
    ('search_documents',   a.documents_enabled),
    ('read_document',      a.documents_enabled),
    ('report_not_covered', a.documents_enabled AND a.documents_grounded),
    ('ssh_run',            a.ssh_enabled)
) AS t(tool, offered)
WHERE a.runtime = 'openai' AND t.offered;

COMMIT;
```

`db/migrations/0078_agent_tools.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- 0077 derives every native agent's tools from its capabilities again; edited usage instructions are dropped.
DROP TABLE agent_tools;

COMMIT;
```

In `db/tests/run.sh` change `[ "$rolled_back_version" = 76 ]` to `[ "$rolled_back_version" = 77 ]`.

- [ ] **Step 4: Load the tools in `get_agent_config`**

In `knowledge_agents/config.py`: add `tools: dict[str, list[dict[str, Any]]]` to `AgentConfig` after `services`,
and add:

```python
def tool_items(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The agent's tools in config shape: every catalog tool (no row: off), then MCP rows by name."""
    from knowledge_admin.tools.catalog import CATALOG, item  # the catalog imports this module

    saved = {row['tool']: row for row in rows}
    items = []
    for name in CATALOG:
        row = saved.get(name)
        items.append(item(name, row['mode'] if row else 'off', row['usage'] if row else None))
    for name in sorted(tool for tool in saved if tool.startswith('mcp:')):
        items.append(item(name, saved[name]['mode'], saved[name]['usage'], group='mcp'))
    return {'items': items}


def native_tools(conn, agent_id: str, runtime: str) -> dict[str, list[dict[str, Any]]]:
    if runtime != 'openai':
        return {'items': []}
    return tool_items(conn.execute('SELECT tool,mode,usage FROM ka.agent_tools WHERE agent_id=%s', (agent_id,)))
```

In the version branch, after `config.setdefault('resources', 'fixed')`:

```python
            if agent['runtime'] != 'openai':
                config['tools'] = {'items': []}
            else:
                from knowledge_admin.tools.catalog import implied_tools  # the catalog imports this module
                config.setdefault('tools', implied_tools(config))  # versions saved before 0078
```

and in the working-copy return add `'tools': native_tools(conn, agent_id, agent['runtime']),` after `'services'`.

- [ ] **Step 5: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_tools_migration.py tests/test_agent_tools_config.py tests/test_agents.py tests/test_agent_versions.py tests/test_knowledge_agents_config.py tests/test_remove_coordinator_migration.py -q`
Expected: PASS.

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, exit 0.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/0078_agent_tools.up.sql db/migrations/0078_agent_tools.down.sql db/tests/run.sh services/admin-api
git commit -m "feat(db): agent tools table, backfilled from today's capability rule (0078)

ka.agent_tools holds each native agent's tools with mode and usage; the configuration and its versions carry them;
versions saved before 0078 get the implied tools; Claude Code agents have none.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Enforcement: MCP tool filter, Tool notes, and the model gets exactly the selected tools

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/tools/catalog.py` (`mcp_blocked`, `mcp_notes`)
- Modify: `services/admin-api/src/knowledge_admin/agents.py` (`create_agent`, `get_prompt`)
- Create: `services/admin-api/tests/test_tool_enforcement.py`

**Interfaces:**
- Consumes: `effective_tools` (Task 2), `config['tools']` (Task 3), `capabilities.mcp` (Task 1).
- Produces: `catalog.mcp_blocked(config, server_id) -> list[str]`, `catalog.mcp_notes(config, servers) -> list[str]`;
  `get_prompt` appends `Tool notes:` when there are notes.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_tool_enforcement.py` (uses `test_sdk_agent`'s Responses and MCP fixture):

```python
"""What the model is given: exactly the selected catalog tools, the MCP tools not switched off, edited usage as
the description or as Tool notes, and per-run narrowing unchanged."""
import asyncio

from agents import Runner

from knowledge_admin.agents import agent, get_prompt
from knowledge_admin.tools.catalog import CATALOG, implied_tools
from test_sdk_agent import config, responses_api  # noqa: F401  (fixtures)


def choose(cfg, on: dict[str, str | None], mcp: dict[str, tuple[str, str | None]] = {}):
    """Switch on exactly `on` (tool → usage or None); MCP rows as name → (mode, usage)."""
    items = [{'tool': name, 'group': spec.group, 'mode': 'on' if name in on else 'off', 'usage': on.get(name),
              'subagent_allowed': True} for name, spec in CATALOG.items()]
    items += [{'tool': name, 'group': 'mcp', 'mode': mode, 'usage': usage, 'subagent_allowed': True}
              for name, (mode, usage) in mcp.items()]
    cfg['tools'] = {'items': items}


def first_request(requests):
    return next(r for r in requests if r['path'] == '/v1/responses')['body']


def test_the_model_request_carries_exactly_the_selected_tools(config, responses_api):
    _, requests = responses_api
    choose(config, {'read_file': 'Read only what the task names.', 'search_documents': None, 'ssh_run': None})
    asyncio.run(agent(config, 'Describe your tools.', credentials={'model-key': 'fixture-api-secret'}))
    body = first_request(requests)
    tools = {tool['name']: tool for tool in body['tools']}
    assert set(tools) == {'read_file', 'search_documents'}  # SSH is off in the fixture: ssh_run's need is unmet
    assert tools['read_file']['description'] == 'Read only what the task names.'
    assert tools['search_documents']['description'] == CATALOG['search_documents'].usage


def test_the_shell_is_switched_by_the_shell_tool(config, responses_api, monkeypatch):
    seen = {}
    real = Runner.run_streamed

    def spy(assistant, task, *, context, **kwargs):
        seen.update(tools=context['tools'], shell=context['shell_enabled'])
        return real(assistant, task, context=context, **kwargs)
    monkeypatch.setattr('knowledge_admin.agents.Runner.run_streamed', spy)
    choose(config, {'read_file': None})
    asyncio.run(agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}))
    assert seen == {'tools': frozenset({'read_file'}), 'shell': False}
    choose(config, {'shell_run': None})
    asyncio.run(agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}))
    assert seen == {'tools': frozenset({'shell_run'}), 'shell': True}


def test_mcp_tools_switched_off_never_reach_the_model(config, responses_api):
    url, requests = responses_api
    servers = [{'id': name, 'name': name, 'description': 'Fixture', 'url': url.removesuffix('/v1') + '/mcp',
                'enabled': True, 'bearer_token_ref': None} for name in ('docs-one', 'docs-two')]
    config['mcp'] = {'enabled': True, 'servers': servers}
    choose(config, {}, {'mcp:docs-one:lookup': ('off', None), 'mcp:docs-two:lookup': ('on', 'Ask for one page only.')})
    asyncio.run(agent(config, 'Describe the tools.', credentials={'model-key': 'fixture-api-secret'}))
    body = first_request(requests)
    names = [tool['name'] for tool in body['tools']]
    assert len(names) == 1 and 'docs-two' in names[0] and 'lookup' in names[0]
    assert 'Tool notes:\n- docs-two/lookup: Ask for one page only.' in body['instructions']


def test_prompt_tool_notes_name_the_server_and_only_edited_tools(config):
    config['mcp'] = {'enabled': True, 'servers': [{'id': 'm1', 'name': 'Docs', 'description': '', 'enabled': True}]}
    choose(config, {}, {'mcp:m1:lookup': ('on', 'Use for API pages.'), 'mcp:m1:search': ('on', None),
                        'mcp:m1:delete': ('off', 'Never.')})
    prompt = get_prompt(config, [], config['mcp']['servers'])
    assert prompt.endswith('Tool notes:\n- Docs/lookup: Use for API pages.')
    choose(config, {})
    assert 'Tool notes' not in get_prompt(config, [], config['mcp']['servers'])


def test_old_configs_without_tools_keep_todays_tools(config, responses_api):
    _, requests = responses_api
    assert 'tools' not in config
    asyncio.run(agent(config, 'Inspect.', credentials={'model-key': 'fixture-api-secret'}))
    names = {tool['name'] for tool in first_request(requests)['tools']}
    expected = {entry['tool'] for entry in implied_tools(config)['items'] if entry['mode'] == 'on'} - {'code_graph'}
    assert names == expected
```

Append to `services/admin-api/tests/test_run_resource_tools.py`:

```python
def test_per_run_narrowing_still_decides_what_tools_reach(folders):
    """A per-run run with every file tool switched on still reads only its own code project."""
    context = run_context(folders, code=['given'])
    context['config']['tools'] = implied_tools(context['config'])
    read = tool('read_file')
    assert 'given' in invoke(read, context, path=str(folders['given'] / 'a.py'))
    refused = invoke(read, context, path=str(folders['other'] / 'b.py'))
    assert 'outside the agent' in refused
```

(with `from knowledge_admin.tools.catalog import implied_tools` and `from tool_helpers import tool` at the top).

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_tool_enforcement.py tests/test_run_resource_tools.py -q`
Expected: FAIL in `test_mcp_tools_switched_off_never_reach_the_model` (two lookup tools) and
`test_prompt_tool_notes_name_the_server_and_only_edited_tools` (no Tool notes); the others pass already (Task 2).

- [ ] **Step 3: Add the MCP helpers to the catalog**

```python
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
```

- [ ] **Step 4: Filter MCP tools and add the notes**

In `agents.py` add `from agents.mcp import create_static_tool_filter` and import `mcp_blocked, mcp_notes` from the
catalog. In `create_agent` the MCP server gets its filter:

```python
        for server in selected_mcp:
            blocked = mcp_blocked(config, server['id'])
            mcp_servers.append(await connections.enter_async_context(MCPServerStreamableHttp(
                name=server['id'], params={'url': server['url'], 'headers': mcp.auth_headers(server, credentials)},
                cache_tools_list=True, client_session_timeout_seconds=timeout_seconds,
                tool_filter=create_static_tool_filter(blocked_tool_names=blocked) if blocked else None)))
```

At the end of `get_prompt`, before `return prompt`:

```python
    notes = mcp_notes(config, selected_mcp)
    if notes:
        prompt += '\n\nTool notes:\n' + '\n'.join(notes)
```

- [ ] **Step 5: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_tool_enforcement.py tests/test_run_resource_tools.py tests/test_sdk_agent.py tests/test_tool_catalog.py -q`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api
git commit -m "feat(agents): the model gets exactly the selected tools; MCP tools filtered, edited MCP usage as Tool notes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `code.graphs` from generated graphs (the `code_graph` fix)

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/capabilities/code_graph.py` (`current_graph`, `GRAPH_KEYS`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/config.py` (`load_graphs`, both loader paths)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/run_resources.py` (`resolve`)
- Create: `services/admin-api/tests/test_code_graphs_config.py`

**Interfaces:**
- Consumes: `capabilities.code_graph.load_code_graph`, `code_processing.index_directory`, `CodeProcessingError`.
- Produces: `capabilities.code_graph.current_graph(project) -> dict | None`;
  `config.load_graphs(conn, project_id, ids) -> list[dict]` (entries `{id, name, graph_path, code_root, source_hash}`).

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_code_graphs_config.py`:

```python
"""The loader lists one graph per selected code project that has a generated, current graph; per-run runs keep
only the graphs of their own code projects."""
import json
from uuid import UUID, uuid4

import pytest

from knowledge_admin.capabilities import code_graph
from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.knowledge_agents.run_resources import RunResources, resolve
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_agent_resources import new_version

GRAPH = {'name': 'Payments', 'graph_path': '/index/g.sqlite', 'code_root': '/work/code/managed/payments', 'source_hash': 'abc'}


@pytest.fixture
def graphs(monkeypatch):
    """current_graph answers from a table of project id → graph (None: no usable graph)."""
    table: dict[str, dict | None] = {}
    monkeypatch.setattr(code_graph, 'current_graph', lambda project: table.get(str(project['id'])))
    return table


def test_projects_with_a_current_graph_are_listed(store, saved_agent, graphs):
    repo = saved_agent[5]
    graphs[repo['id']] = {'id': repo['id'], **GRAPH}
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert config['code']['graphs'] == [{'id': repo['id'], **GRAPH}]
    graphs[repo['id']] = None
    assert get_agent_config('operations', conninfo=store.conninfo)['code']['graphs'] == []
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET repositories_enabled=false WHERE id='operations'")
    graphs[repo['id']] = {'id': repo['id'], **GRAPH}
    assert get_agent_config('operations', conninfo=store.conninfo)['code']['graphs'] == []


def test_versions_get_the_current_graphs(store, saved_agent, graphs):
    client, repo = saved_agent[0], saved_agent[5]
    version = new_version(client, 'Before the graph')
    graphs[repo['id']] = {'id': repo['id'], **GRAPH}
    pinned = get_agent_config('operations', conninfo=store.conninfo, version_id=version['id'])
    assert pinned['code']['graphs'] == [{'id': repo['id'], **GRAPH}]


def test_a_per_run_run_gets_only_the_graphs_of_its_code_projects(store, saved_agent, graphs):
    repo = saved_agent[5]
    other = str(uuid4())
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'other','Other')", (UUID(other), project))
        conn.execute("INSERT INTO ka.code_project_folders(code_project_id,project_id,path) VALUES (%s,%s,'other')", (UUID(other), project))
        conn.execute("UPDATE ka.agents SET resources='per_run',all_repositories=true WHERE id='operations'")
    graphs[repo['id']] = {'id': repo['id'], **GRAPH}
    graphs[other] = {'id': other, **GRAPH, 'name': 'Other'}
    config = get_agent_config('operations', conninfo=store.conninfo)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        only_other = resolve(conn, project, config, RunResources(code_project_ids=[UUID(other)]))
        nothing = resolve(conn, project, config, RunResources())
    assert [graph['id'] for graph in only_other['code']['graphs']] == [other]
    assert nothing['code']['graphs'] == []


def test_current_graph_reads_the_manifest_and_skips_missing_or_stale_graphs(tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_INDEX_ROOT', str(tmp_path / 'indexes'))
    project = {'id': str(uuid4()), 'name': 'App', 'folder_paths': ['app']}
    assert code_graph.current_graph(project) is None  # never generated
    directory = tmp_path / 'indexes' / project['id']
    directory.mkdir(parents=True)
    (directory / 'manifest.json').write_text(json.dumps({'source_hash': 'h1'}))
    calls = []

    def load(selected, expected):
        calls.append(expected)
        return {'id': selected['id'], 'name': selected['name'], 'graph_path': '/g', 'code_root': '/c',
                'source_hash': expected, 'folder_paths': [], 'stats': {}}
    monkeypatch.setattr(code_graph, 'load_code_graph', load)
    assert code_graph.current_graph(project) == {'id': project['id'], 'name': 'App', 'graph_path': '/g',
                                                 'code_root': '/c', 'source_hash': 'h1'}
    assert calls == ['h1']

    def stale(selected, expected):
        raise code_graph.CodeProcessingError('Source or graph selection changed.')
    monkeypatch.setattr(code_graph, 'load_code_graph', stale)
    assert code_graph.current_graph(project) is None
    monkeypatch.delenv('CODE_INDEX_ROOT')
    assert code_graph.current_graph(project) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_code_graphs_config.py -q`
Expected: FAIL: `AttributeError: … has no attribute 'current_graph'`.

- [ ] **Step 3: `current_graph`**

In `capabilities/code_graph.py` (it already imports `json`, `index_directory`, `CodeProcessingError`; add
`from fastapi import HTTPException` for `code_root()`'s refusal):

```python
GRAPH_KEYS = ('id', 'name', 'graph_path', 'code_root', 'source_hash')


def current_graph(project: dict) -> dict | None:
    """The project's generated graph while it still describes the selected source, as the code_graph and shell tools
    read it; None when it was never generated, is stale or the index or code mount is not configured.
    project: id, name and folder_paths relative to CODE_ROOT (as saved in ka.code_project_folders)."""
    try:
        manifest = index_directory(project['id']) / 'manifest.json'
        if not manifest.is_file():
            return None
        graph = load_code_graph(project, json.loads(manifest.read_text())['source_hash'])
    except (CodeProcessingError, HTTPException, OSError, ValueError, KeyError):
        return None
    return {key: graph[key] for key in GRAPH_KEYS}
```

- [ ] **Step 4: Fill `code.graphs` in the loader**

In `knowledge_agents/config.py`:

```python
GRAPH_PROJECTS = '''SELECT r.id::text AS id,r.name,
    ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folder_paths
    FROM ka.repositories r WHERE r.project_id=%s AND r.id=ANY(%s::uuid[]) AND r.archived_at IS NULL
    ORDER BY lower(r.name),r.id'''


def load_graphs(conn, project_id, ids: Iterable[str]) -> list[dict[str, Any]]:
    """The current generated graphs of these code projects (live, like file contents; never taken from a version)."""
    from knowledge_admin.capabilities import code_graph  # imports the code processing stack only when needed

    ids = [str(item) for item in ids]
    if project_id is None or not ids:
        return []
    return [graph for row in conn.execute(GRAPH_PROJECTS, (project_id, ids))
            if (graph := code_graph.current_graph({'id': row['id'], 'name': row['name'],
                                                   'folder_paths': list(row['folder_paths'])})) is not None]
```

Restructure `get_agent_config` so the project id is known in both paths: move the `projects = …` /
`project_id = …` lines (with the single-database check) above `selected_version = …`. Then:

- version path, after the tools defaults:

```python
            config['code']['graphs'] = (load_graphs(conn, project_id, [p['id'] for p in config['code']['projects']])
                                        if config['runtime'] == 'openai' and config['code']['enabled'] else [])
```

- working-copy path: the `code` section becomes

```python
            'code': {'enabled': agent['repositories_enabled'], 'all_projects': agent['all_repositories'],
                'projects': code_projects,
                'graphs': load_graphs(conn, project_id, [p['id'] for p in code_projects])
                          if agent['runtime'] == 'openai' and agent['repositories_enabled'] else []},
```

- [ ] **Step 5: Keep per-run graphs to the run's code projects**

In `run_resources.resolve`, replace the two `if 'graphs' in effective['code']:` lines with:

```python
    graphs = [graph for graph in effective['code'].get('graphs', []) if str(graph.get('id')) in code_ids]
    known = {str(graph['id']) for graph in graphs}
    missing = [item for item in wanted['code_project_ids'] if item not in known]
    if missing and effective['code']['enabled']:
        graphs += load_graphs(conn, project_id, missing)  # "All code projects": projects added after the snapshot
    effective['code']['graphs'] = graphs
```

with `from knowledge_admin.knowledge_agents.config import load_graphs, per_run`. (`load_graphs` only lists
projects that exist and are not archived; `wanted` was already checked against the selection.)

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_code_graphs_config.py tests/test_run_resources.py tests/test_agents.py tests/test_code_processing.py tests/test_knowledge_agents_config.py -q`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api
git commit -m "fix(agents): offer code_graph when a selected code project has a current generated graph

get_agent_config (working copy and versions) and per-run resolution fill code.graphs from load_code_graph;
a per-run run keeps only its own projects' graphs.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 6: Tools API, save rules, the wizard's tools and the list summary

**Files:**
- Create: `services/admin-api/src/knowledge_admin/tools_api.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py` (include the router)
- Modify: `services/admin-api/src/knowledge_admin/tools/catalog.py` (`catalog_listing`, `capability_defaults`, `tools_summary`)
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py` (`update`, `listing`)
- Modify: `services/admin-api/src/knowledge_admin/agent_wizard.py` (`CreateInput.tools`, `create`, `options`)
- Create: `services/admin-api/tests/test_agent_tools_api.py`

**Interfaces:**
- Consumes: Tasks 2–3.
- Produces: `GET /api/tools` → `{"tools": [{name, group, label, summary, usage, needs}]}`;
  `GET /api/agents/{id}/tools` → `{runtime, revision, tools: [{tool, label, summary, group, mode, usage,
  default_usage, subagent_allowed, available, reason, tab}], claude_code_tools?}`;
  `PUT /api/agents/{id}/tools` with `{"expected_revision": n, "tools": [{tool, mode: "on"|"off", usage}]}` → the GET
  shape; `tools_api.switch_on_defaults(conn, agent_id, old_flags, new_flags)`, `tools_api.ensure_native_tools(conn,
  agent_id, flags)`, `tools_api.flags(row)`; `CreateInput.tools: list[str] | None`; `GET /api/agents/wizard/options`
  gains `tools` (the catalog listing); `GET /api/agents` rows gain `tools_summary` (`{on, per_run, groups}` or
  `{claude_code_tools}`).

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_agent_tools_api.py`:

```python
"""The Tools tab API: catalog, an agent's tools, replacing them with the save rules and the revision check; default
tools when a capability is switched on; the wizard's tools; the list summary."""
from uuid import uuid4

from knowledge_admin.knowledge_agents.config import get_agent_config
from knowledge_admin.tools.catalog import CATALOG
from knowledge_admin.tools_api import CLAUDE_CODE, GROUNDED, MCP_NOT_SELECTED, STALE, UNKNOWN
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_agent_resources import listed, new_version
from test_claude_code_agents import claude_code_agent
from test_profiles import create, update, wizard_payload
from test_repositories import HEADERS


def view(client, agent_id='operations') -> dict:
    response = client.get(f'/api/agents/{agent_id}/tools', headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def put(client, agent_id, revision, tools):
    return client.put(f'/api/agents/{agent_id}/tools', headers=HEADERS, json={'expected_revision': revision, 'tools': tools})


def on(value) -> set[str]:
    return {tool['tool'] for tool in value['tools'] if tool['mode'] == 'on'}


def rows(store, agent_id) -> set[str]:
    with store.connect() as conn:
        return {row['tool'] for row in conn.execute("SELECT tool FROM ka.agent_tools WHERE agent_id=%s AND mode='on'", (agent_id,))}


def test_the_catalog_and_an_agents_tools(store, saved_agent):
    client, mcp = saved_agent[0], saved_agent[4]
    assert client.get('/api/tools').status_code == 403
    catalog = client.get('/api/tools', headers=HEADERS).json()['tools']
    assert [tool['name'] for tool in catalog] == list(CATALOG)
    assert set(catalog[0]) == {'name', 'group', 'label', 'summary', 'usage', 'needs'}
    current = view(client)
    names = [tool['tool'] for tool in current['tools']]
    assert names == [*CATALOG, f"mcp:{mcp['id']}:lookup"]
    lookup = current['tools'][-1]
    assert (lookup['label'], lookup['group'], lookup['mode'], lookup['available']) == ('Docs / lookup', 'mcp', 'on', True)
    read = current['tools'][1]
    assert read['default_usage'] == CATALOG['read_file'].usage and read['available'] and read['tab'] == 'git'
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET repositories_enabled=false,documents_enabled=false,ssh_enabled=false WHERE id='operations'")
    unmet = {tool['tool']: tool for tool in view(client)['tools']}
    assert not unmet['read_file']['available']
    assert unmet['read_file']['reason'] == 'Needs code projects, or documents without "Answer only from documents"'
    assert unmet['ssh_run']['reason'] == 'Needs SSH servers' and unmet['ssh_run']['tab'] == 'ssh'


def test_put_replaces_the_tools_and_enforces_the_save_rules(store, saved_agent):
    client, mcp = saved_agent[0], saved_agent[4]
    lookup = f"mcp:{mcp['id']}:lookup"
    revision = view(client)['revision']
    saved = put(client, 'operations', revision, [
        {'tool': 'read_file', 'mode': 'on', 'usage': 'Read narrowly.'}, {'tool': 'ssh_run', 'mode': 'on', 'usage': '  '},
        {'tool': lookup, 'mode': 'off', 'usage': None}, {'tool': 'list_files', 'mode': 'off', 'usage': None}])
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert on(body) == {'read_file', 'ssh_run'} and body['revision'] == revision + 1
    tools = {tool['tool']: tool for tool in body['tools']}
    assert tools['read_file']['usage'] == 'Read narrowly.' and tools['ssh_run']['usage'] is None  # blank: built-in
    config = get_agent_config('operations', conninfo=store.conninfo)
    assert {e['tool'] for e in config['tools']['items'] if e['mode'] == 'on'} == {'read_file', 'ssh_run'}
    for tool, message in (('grep', UNKNOWN.format(tool='grep')),
                          (f'mcp:{uuid4()}:lookup', None), (f"mcp:{mcp['id']}:missing", None)):
        refused = put(client, 'operations', body['revision'], [{'tool': tool, 'mode': 'on', 'usage': None}])
        assert refused.status_code == 400
        assert refused.json()['error']['message'] == (message or MCP_NOT_SELECTED.format(tool=tool))
    assert put(client, 'operations', body['revision'], [{'tool': 'read_file', 'mode': 'per_run', 'usage': None}]).status_code == 422
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET documents_enabled=true,documents_grounded=true WHERE id='operations'")
    grounded = put(client, 'operations', body['revision'], [{'tool': 'search_documents', 'mode': 'on', 'usage': None}])
    assert grounded.status_code == 400 and grounded.json()['error']['message'] == GROUNDED
    assert put(client, 'operations', body['revision'], [{'tool': 'search_documents', 'mode': 'on', 'usage': None},
               {'tool': 'report_not_covered', 'mode': 'on', 'usage': None}]).status_code == 200


def test_a_stale_revision_is_refused(store, saved_agent):
    client = saved_agent[0]
    profile = client.get('/api/agents/operations', headers=HEADERS).json()
    assert put(client, 'operations', profile['revision'], []).status_code == 200
    stale = put(client, 'operations', profile['revision'], [])
    assert stale.status_code == 409 and stale.json()['error']['message'] == STALE
    assert update(client, profile).status_code == 409  # General must reload the new revision


def test_claude_code_agents_keep_their_claude_code_tools(store, saved_agent):
    client = saved_agent[0]
    writer = claude_code_agent(client)
    current = view(client, writer)
    assert current['runtime'] == 'claude_code' and current['tools'] == []
    assert current['claude_code_tools'] == ['Read', 'Grep', 'Glob', 'Bash']
    refused = put(client, writer, current['revision'], [{'tool': 'read_file', 'mode': 'on', 'usage': None}])
    assert refused.status_code == 400 and refused.json()['error']['message'] == CLAUDE_CODE


def test_switching_a_capability_on_switches_its_default_tools_on(store, saved_agent):
    client = saved_agent[0]
    blank = create(client, 'Blank')
    assert rows(store, blank['id']) == set()
    code = update(client, blank, repositories_enabled=True).json()
    defaults = {'list_files', 'read_file', 'search_files', 'shell_run', 'code_search', 'ast_grep', 'code_graph'}
    assert rows(store, blank['id']) == defaults
    only = put(client, blank['id'], code['revision'], [{'tool': 'read_file', 'mode': 'on', 'usage': None}]).json()
    profile = client.get(f"/api/agents/{blank['id']}", headers=HEADERS).json()
    assert profile['revision'] == only['revision']
    documents = update(client, profile, documents_enabled=True).json()
    assert rows(store, blank['id']) == {'read_file', 'search_documents', 'read_document'}  # code keeps its file tools
    grounded = update(client, documents, documents_grounded=True).json()
    assert 'report_not_covered' in rows(store, blank['id'])
    ssh = update(client, grounded, ssh_enabled=True).json()
    assert 'ssh_run' in rows(store, blank['id'])
    off = update(client, ssh, repositories_enabled=False).json()
    assert 'read_file' in rows(store, blank['id'])  # switching off keeps the rows
    update(client, off, repositories_enabled=True)  # none of code's own tools is on: its defaults come back
    assert rows(store, blank['id']) >= defaults


def test_a_claude_code_agent_switched_to_native_gets_todays_tools(store, saved_agent):
    client = saved_agent[0]
    writer = claude_code_agent(client, 'Writer', repositories_enabled=True)
    assert rows(store, writer) == set()
    profile = client.get(f'/api/agents/{writer}', headers=HEADERS).json()
    assert update(client, profile, runtime='openai').status_code == 200
    assert rows(store, writer) == {'list_files', 'read_file', 'search_files', 'shell_run', 'code_search', 'ast_grep', 'code_graph'}


def test_the_wizard_saves_the_chosen_or_implied_tools(store, saved_agent):
    client = saved_agent[0]
    options = client.get('/api/agents/wizard/options', headers=HEADERS).json()
    assert [tool['name'] for tool in options['tools']] == list(CATALOG)
    implied = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Implied', ssh_enabled=True)).json()
    assert rows(store, implied['id']) == {'ssh_run'}
    chosen = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload(
        'Chosen', repositories_enabled=True, tools=['read_file', 'code_graph'])).json()
    assert rows(store, chosen['id']) == {'read_file', 'code_graph'}
    refused = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Odd', tools=['grep']))
    assert refused.status_code == 400 and refused.json()['error']['message'] == UNKNOWN.format(tool='grep')
    writer = claude_code_agent(client, 'Writer', repositories_enabled=True)
    assert rows(store, writer) == set()


def test_the_list_summarises_the_tools_that_run(store, saved_agent):
    client = saved_agent[0]
    revision = view(client)['revision']
    put(client, 'operations', revision, [{'tool': 'read_file', 'mode': 'on', 'usage': None},
                                         {'tool': 'ssh_run', 'mode': 'on', 'usage': None}])
    summary = listed(client)['operations']['tools_summary']
    assert summary == {'on': 3, 'per_run': 0, 'groups': {'code': 1, 'documents': 0, 'servers': 1, 'mcp': 1, 'agents': 0}}
    new_version(client, 'Two tools')  # activates "Initial configuration" (these tools), the draft is not active
    put(client, 'operations', view(client)['revision'], [])
    assert listed(client)['operations']['tools_summary'] == summary  # the active version runs
    writer = claude_code_agent(client)
    assert listed(client)[writer]['tools_summary'] == {'claude_code_tools': ['Read', 'Grep', 'Glob', 'Bash']}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_tools_api.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'knowledge_admin.tools_api'`.

- [ ] **Step 3: Catalog helpers**

Append to `tools/catalog.py`:

```python
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
    """All agents: how many tools are on, per group; Claude Code agents: their Claude Code tools."""
    if config.get('runtime', 'openai') != 'openai':
        return {'claude_code_tools': list(config.get('claude_code_tools', []))}
    chosen = {entry['tool']: entry for entry in tools_of(config)['items']}
    groups = {'code': 0, 'documents': 0, 'servers': 0, 'mcp': 0, 'agents': 0}
    for spec in CATALOG.values():
        if chosen.get(spec.name, {}).get('mode') == 'on':
            groups[spec.group] += 1
    if config['mcp']['enabled']:
        for server in config['mcp']['servers']:
            for found in server.get('tools', []):
                if chosen.get(f"mcp:{server['id']}:{found['name']}", {}).get('mode', 'on') == 'on':
                    groups['mcp'] += 1
    return {'on': sum(groups.values()), 'per_run': 0, 'groups': groups}
```

- [ ] **Step 4: The API**

Create `services/admin-api/src/knowledge_admin/tools_api.py`:

```python
"""An agent's tools: the catalog, the Tools tab (GET/PUT) and the rules that keep a selection consistent."""
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_admin.access import request_actor
from knowledge_admin.tools.catalog import (CATALOG, NEED_REASONS, NEED_TABS, capability_defaults, catalog_listing,
                                           implied_tools, needs_met)

STALE = 'The tools changed since you opened them. Reload.'
GROUNDED = 'Grounded answers need report_not_covered.'
UNKNOWN = 'Unknown tool: {tool}.'
MCP_NOT_SELECTED = 'MCP tool {tool} belongs to a server this agent has not selected.'
CLAUDE_CODE = 'Claude Code agents use their Claude Code tools. Switch them on the General tab.'
MCP_REASON = 'Needs MCP'
# A capability's own tools (by need): switching it on while none of them is on switches its defaults on.
PRIMARY = {'code': ('code', 'code_graph'), 'documents': ('documents', 'grounded'), 'ssh': ('ssh',)}

AGENT = '''SELECT id,runtime,revision,claude_code_tools,repositories_enabled,documents_enabled,documents_grounded,
    ssh_enabled,mcp_enabled FROM ka.agents WHERE id=%s'''
SELECTED_MCP = '''SELECT m.id::text AS id,m.name,m.enabled,m.tools FROM ka.mcp_servers m WHERE EXISTS (
    SELECT 1 FROM ka.agent_config_mcp_servers s JOIN ka.agent_configs c ON c.id=s.config_id
    WHERE s.mcp_server_id=m.id AND c.agent=%s) ORDER BY lower(m.name),m.id'''
ON = "SELECT tool FROM ka.agent_tools WHERE agent_id=%s AND mode='on'"
SWITCH_ON = '''INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES (%s,%s,'on')
    ON CONFLICT (agent_id,tool) DO UPDATE SET mode='on',updated_at=now()'''


class ToolChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tool: str = Field(min_length=1, max_length=300)
    mode: Literal['on', 'off']  # per run comes with a later step
    usage: str | None = Field(default=None, max_length=8000)


class ToolsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1, strict=True)
    tools: list[ToolChoice] = Field(max_length=500)

    @field_validator('tools')
    @classmethod
    def once(cls, value: list[ToolChoice]) -> list[ToolChoice]:
        if len({choice.tool for choice in value}) != len(value):
            raise ValueError('List each tool once.')
        return value


def flags(row: dict[str, Any]) -> dict[str, Any]:
    """The capability switches of an agent row (or a proposal) in config shape, for the catalog rules."""
    return {'runtime': row['runtime'], 'code': {'enabled': row['repositories_enabled']},
            'documents': {'enabled': row['documents_enabled'], 'grounded': row['documents_grounded']},
            'ssh': {'enabled': row['ssh_enabled']}}


def _usage(text: str | None) -> str | None:
    return text if text is not None and text.strip() else None  # blank: the built-in text


def agent_tools(conn, agent_id: str) -> dict[str, Any]:
    agent = conn.execute(AGENT, (agent_id,)).fetchone()
    if agent is None:
        raise HTTPException(404, 'Agent not found.')
    if agent['runtime'] != 'openai':
        return {'runtime': agent['runtime'], 'revision': agent['revision'],
                'claude_code_tools': list(agent['claude_code_tools']), 'tools': []}
    saved = {row['tool']: row for row in conn.execute(
        'SELECT tool,mode,usage,subagent_allowed FROM ka.agent_tools WHERE agent_id=%s', (agent_id,))}
    current = flags(agent)
    tools = []
    for spec in CATALOG.values():
        row = saved.get(spec.name)
        available = needs_met(spec.needs, current, None)
        tools.append({'tool': spec.name, 'label': spec.label, 'summary': spec.summary, 'group': spec.group,
                      'mode': row['mode'] if row else 'off', 'usage': row['usage'] if row else None,
                      'default_usage': spec.usage, 'subagent_allowed': row['subagent_allowed'] if row else True,
                      'available': available, 'reason': None if available else NEED_REASONS[spec.needs],
                      'tab': NEED_TABS.get(spec.needs)})
    for server in conn.execute(SELECTED_MCP, (agent_id,)):
        available = agent['mcp_enabled'] and server['enabled']
        for found in server['tools']:
            name = f"mcp:{server['id']}:{found['name']}"
            row = saved.get(name)
            tools.append({'tool': name, 'label': f"{server['name']} / {found['name']}", 'summary': '', 'group': 'mcp',
                          'mode': row['mode'] if row else 'on', 'usage': row['usage'] if row else None,
                          'default_usage': found.get('description', ''),
                          'subagent_allowed': row['subagent_allowed'] if row else True,
                          'available': available, 'reason': None if available else MCP_REASON, 'tab': 'mcp'})
    return {'runtime': 'openai', 'revision': agent['revision'], 'tools': tools}


def switch_on_defaults(conn, agent_id: str, old: dict[str, Any], new: dict[str, Any]) -> None:
    """Profile save: a capability switched on while none of its own tools is on gets its default tools; grounded
    answers always get report_not_covered. Switching a capability off keeps every row."""
    if new['runtime'] != 'openai':
        return
    on = {row['tool'] for row in conn.execute(ON, (agent_id,))}
    wanted: set[str] = set()
    for capability in ('code', 'documents', 'ssh'):
        if new[capability]['enabled'] and not old[capability]['enabled']:
            own = {spec.name for spec in CATALOG.values() if spec.needs in PRIMARY[capability]}
            if not on & own:
                wanted.update(capability_defaults(capability, new))
    if new['documents']['enabled'] and new['documents']['grounded']:
        wanted.add('report_not_covered')
    for name in sorted(wanted - on):
        conn.execute(SWITCH_ON, (agent_id, name))


def ensure_native_tools(conn, agent_id: str, current: dict[str, Any]) -> None:
    """An agent that becomes native without any tool rows gets the tools its capabilities give (the backfill)."""
    if conn.execute('SELECT 1 FROM ka.agent_tools WHERE agent_id=%s LIMIT 1', (agent_id,)).fetchone():
        return
    for entry in implied_tools(current)['items']:
        if entry['mode'] == 'on':
            conn.execute(SWITCH_ON, (agent_id, entry['tool']))


def build_api(app_name, store):
    router = APIRouter()

    def database():
        if store is None:
            raise HTTPException(503, 'Agent storage is not configured.')
        return store

    @router.get('/tools')
    def catalog():
        return {'tools': catalog_listing()}

    @router.get('/agents/{agent_id}/tools')
    def tools(agent_id: str, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        with database().connect() as conn:
            return agent_tools(conn, agent_id)

    @router.put('/agents/{agent_id}/tools')
    def save(agent_id: str, value: ToolsInput, request: Request):
        with database().connect() as conn:
            agent = conn.execute(AGENT + ' FOR UPDATE', (agent_id,)).fetchone()
            if agent is None:
                raise HTTPException(404, 'Agent not found.')
            if agent['runtime'] != 'openai':
                raise HTTPException(400, CLAUDE_CODE)
            if agent['revision'] != value.expected_revision:
                raise HTTPException(409, STALE)
            servers = {row['id']: {found['name'] for found in row['tools']} for row in conn.execute(SELECTED_MCP, (agent_id,))}
            for choice in value.tools:
                if choice.tool in CATALOG:
                    continue
                kind, _, rest = choice.tool.partition(':')
                server, _, name = rest.partition(':')
                if kind != 'mcp' or not name:
                    raise HTTPException(400, UNKNOWN.format(tool=choice.tool))
                if name not in servers.get(server, set()):
                    raise HTTPException(400, MCP_NOT_SELECTED.format(tool=choice.tool))
            chosen = {choice.tool: choice for choice in value.tools}
            report = chosen.get('report_not_covered')
            if agent['documents_enabled'] and agent['documents_grounded'] and (report is None or report.mode != 'on'):
                raise HTTPException(400, GROUNDED)
            conn.execute('DELETE FROM ka.agent_tools WHERE agent_id=%s', (agent_id,))
            for choice in value.tools:
                usage = _usage(choice.usage)
                if choice.tool.startswith('mcp:') and choice.mode == 'on' and usage is None:
                    continue  # an MCP tool's default: no row
                conn.execute('INSERT INTO ka.agent_tools(agent_id,tool,mode,usage) VALUES (%s,%s,%s,%s)',
                             (agent_id, choice.tool, choice.mode, usage))
            actor = store.actor(conn, *request_actor(request))
            conn.execute('UPDATE ka.agents SET revision=revision+1,updated_by=%s WHERE id=%s', (actor, agent_id))
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,details)
                VALUES (%s,%s,'agent.tools.updated','agent',%s)''', (store.project(conn, app_name), actor, Jsonb(
                {'agent_id': agent_id, 'on': sorted(choice.tool for choice in value.tools if choice.mode == 'on')})))
            return agent_tools(conn, agent_id)

    return router
```

In `api.py`, next to the other agent routers: `router.include_router(tools_api.build_api(app_name, repository_store))`
(and `tools_api` in the module import list).

- [ ] **Step 5: Profile save, wizard and list**

`profiles_api.update`, right after the `UPDATE ka.agents SET …` statement:

```python
                now = flags({'runtime': runtime, 'repositories_enabled': value.repositories_enabled,
                             'documents_enabled': value.documents_enabled, 'documents_grounded': grounded,
                             'ssh_enabled': value.ssh_enabled})
                if runtime == 'openai' and old['runtime'] != 'openai':
                    ensure_native_tools(conn, agent_id, now)
                switch_on_defaults(conn, agent_id, flags(old), now)
```

(`from knowledge_admin.tools_api import ensure_native_tools, flags, switch_on_defaults`; `old` is the agent row
returned by `read`, which has every column used by `flags`.)

`profiles_api.listing`: after the rows query load the working tools and agent-level MCP servers once:

```python
            saved_tools: dict[str, list[dict]] = {}
            for tool in conn.execute('SELECT agent_id,tool,mode,usage FROM ka.agent_tools'):
                saved_tools.setdefault(tool['agent_id'], []).append(tool)
            agent_mcp: dict[str, list[dict]] = {}
            for server in conn.execute('''SELECT c.agent,m.id::text AS id,m.tools FROM ka.agent_configs c
                    JOIN ka.agent_config_mcp_servers s ON s.config_id=c.id JOIN ka.mcp_servers m ON m.id=s.mcp_server_id
                    WHERE c.server_id IS NULL AND m.enabled'''):
                agent_mcp.setdefault(server['agent'], []).append(server)
```

and, per row, before `config = row.pop('version_config')` is used for the other fields:

```python
                working = {'runtime': row['runtime'], 'claude_code_tools': row['claude_code_tools'],
                           'mcp': {'enabled': row['mcp_enabled'], 'servers': agent_mcp.get(row['id'], [])},
                           'tools': tool_items(saved_tools.get(row['id'], []))}
```

then, after the `if config is not None:` block (which already copies the version's `claude_code_tools`):

```python
                row['tools_summary'] = tools_summary(
                    {**config, 'runtime': row['runtime'], 'claude_code_tools': row['claude_code_tools']}
                    if config is not None else working)
```

(`from knowledge_admin.knowledge_agents.config import tool_items` and
`from knowledge_admin.tools.catalog import tools_summary`.)

`agent_wizard.py`:
- `CreateInput.tools: list[str] | None = Field(default=None, max_length=50)  # None: what the capabilities give`;
- `options` returns `{'catalog': …, 'schema': …, 'prompt': …, 'tools': catalog_listing()}`;
- in `create`, before the `INSERT INTO ka.agents`:

```python
                if value.runtime == 'claude_code' and value.tools is not None:
                    raise HTTPException(400, CLAUDE_CODE)
                for name in value.tools or []:
                    if name not in CATALOG:
                        raise HTTPException(400, UNKNOWN.format(tool=name))
```

- after the prompt rows are inserted:

```python
                if value.runtime == 'openai':
                    proposed = flags({'runtime': 'openai', 'repositories_enabled': value.repositories_enabled,
                                      'documents_enabled': value.documents_enabled, 'documents_grounded': False,
                                      'ssh_enabled': value.ssh_enabled})
                    names = value.tools if value.tools is not None else [
                        entry['tool'] for entry in implied_tools(proposed)['items'] if entry['mode'] == 'on']
                    for name in dict.fromkeys(names):
                        conn.execute("INSERT INTO ka.agent_tools(agent_id,tool,mode) VALUES (%s,%s,'on')", (agent_id, name))
```

(`from knowledge_admin.tools.catalog import CATALOG, catalog_listing, implied_tools` and
`from knowledge_admin.tools_api import CLAUDE_CODE, UNKNOWN, flags`.)

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_tools_api.py tests/test_profiles.py tests/test_agent_wizard.py tests/test_agent_resources.py tests/test_claude_code_agents.py tests/test_agent_collections.py tests/test_api.py -q`
Expected: PASS. (Existing tests that compare whole wizard-options or list rows with `==` get the new keys added.)

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api
git commit -m "feat(agents): Tools API with save rules, default tools per capability, wizard tools and list summary

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Tool usage counts on runs, sessions and Test chats

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/run_record.py` (`tool_counts`)
- Modify: `services/admin-api/src/knowledge_admin/agent_runs_api.py`, `claude_code_api.py` (`session_detail`),
  `agent_sessions.py` (`detail`)
- Create: `services/admin-api/tests/test_tool_counts.py`
- Modify: `services/admin-api/tests/test_agent_runs_api.py` (the exact-row assertion gains `tool_counts`)

**Interfaces:**
- Produces: `run_record.tool_counts(conn, run_keys) -> list[{'name': str, 'calls': int}]` (calls descending, then
  name); `tool_counts` on each `GET /api/agent-runs` row, on `GET /api/agent-runs/{run_key}`, on
  `GET /api/claude-code/sessions/{id}` (keys `cc:<id>:1..turns`) and on `GET /api/agents/{id}/sessions/{session_id}`
  (the session over `run:<id>` of its runs, and each run).

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_tool_counts.py`:

```python
"""Which tools each run called and how often, from the run records' tool_call events."""
from uuid import uuid4

from psycopg.types.json import Jsonb

from knowledge_admin.run_record import tool_counts
from test_agent_runs_api import seed
from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_api import _client
from test_claude_code_sessions import sessions, start  # noqa: F401  (fixture)
from test_repositories import HEADERS


def calls(store, run_key, names, *, kind='tool_call', first_seq=10):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for offset, name in enumerate(names):
            conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
                VALUES (%s,%s,%s,%s,'search',%s)''', (project, run_key, first_seq + offset, kind, Jsonb({'name': name})))


def test_tool_counts_count_tool_calls_of_exactly_the_given_runs(store):
    calls(store, 'run:a', ['read_file', 'read_file', 'code_graph', 'shell_run', 'code_graph', 'read_file'])
    calls(store, 'run:b', ['ssh_run'])
    calls(store, 'run:a', ['read_file'], kind='tool_result', first_seq=50)
    calls(store, 'run:ab', ['read_file'])
    with store.connect() as conn:
        assert tool_counts(conn, ['run:a']) == [{'name': 'read_file', 'calls': 3}, {'name': 'code_graph', 'calls': 2},
                                                {'name': 'shell_run', 'calls': 1}]
        assert tool_counts(conn, ['run:a', 'run:b'])[-1] == {'name': 'ssh_run', 'calls': 1}
        assert tool_counts(conn, ['run:none']) == [] and tool_counts(conn, []) == []


def test_run_lists_and_records_carry_tool_counts(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store)
    calls(store, 'run:1', ['search_documents'])
    runs = {run['run_key']: run for run in client.get('/api/agent-runs', headers=HEADERS).json()['runs']}
    assert runs['run:1']['tool_counts'] == [{'name': 'search_documents', 'calls': 2}]
    assert runs['cc:s1:1']['tool_counts'] == []
    record = client.get('/api/agent-runs/run:1', headers=HEADERS).json()
    assert record['tool_counts'] == [{'name': 'search_documents', 'calls': 2}]


def test_sessions_count_only_their_own_turns_and_runs(store, sessions):
    session = start(sessions)
    sid = session['id']
    with store.connect() as conn:
        conn.execute('UPDATE ka.agent_sessions_cc SET turns=2 WHERE id=%s', (sid,))
    calls(store, f'cc:{sid}:1', ['Read', 'Grep'])
    calls(store, f'cc:{sid}:2', ['Read'])
    calls(store, f'cc:{sid}:3', ['Bash'])  # not a turn of this session yet
    calls(store, f'cc:{sid}0:1', ['Glob'])  # another session sharing the prefix
    detail = sessions.client.get(f'/api/claude-code/sessions/{sid}', headers=HEADERS).json()
    assert detail['tool_counts'] == [{'name': 'Read', 'calls': 2}, {'name': 'Grep', 'calls': 1}]


def test_a_test_chat_counts_its_runs_and_each_run(store, saved_agent):
    client = saved_agent[0]
    chat = client.post('/api/agents/operations/sessions', headers=HEADERS, json={'title': 'Test', 'is_test': True}).json()
    runs = [uuid4(), uuid4()]
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'alice', 'alice@test')
        for run in runs:
            conn.execute('''INSERT INTO ka.operations_runs(id,session_id,project_id,actor_id,agent,task,context,status)
                VALUES (%s,%s,%s,%s,'operations','Check disk','{}','completed')''', (run, chat['id'], project, actor))
    calls(store, f'run:{runs[0]}', ['ssh_run', 'ssh_run'])
    calls(store, f'run:{runs[1]}', ['read_file'])
    conversation = client.get(f"/api/agents/operations/sessions/{chat['id']}", headers=HEADERS).json()
    assert conversation['tool_counts'] == [{'name': 'ssh_run', 'calls': 2}, {'name': 'read_file', 'calls': 1}]
    assert {run['id']: run['tool_counts'] for run in conversation['runs']} == {
        str(runs[0]): [{'name': 'ssh_run', 'calls': 2}], str(runs[1]): [{'name': 'read_file', 'calls': 1}]}
```

In `test_agent_runs_api.py`, `test_list_runs_with_filters`: the exact dict gains
`'tool_counts': [{'name': 'search_documents', 'calls': 1}]`.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_tool_counts.py tests/test_agent_runs_api.py -q`
Expected: FAIL: `ImportError: cannot import name 'tool_counts'`.

- [ ] **Step 3: Implement**

`run_record.py`:

```python
TOOL_COUNTS = '''SELECT payload->>'name' AS name, count(*)::int AS calls FROM ka.agent_run_events
    WHERE run_key=ANY(%s) AND kind='tool_call' GROUP BY 1 ORDER BY 2 DESC, 1'''


def tool_counts(conn, run_keys: list[str]) -> list[dict[str, Any]]:
    """How often the given runs called each tool, most used first (ties by name)."""
    if not run_keys:
        return []
    return [dict(row) for row in conn.execute(TOOL_COUNTS, (list(run_keys),))]
```

`agent_runs_api.RUNS`: add, after the `total_tokens` subquery,

```sql
    (SELECT coalesce(jsonb_agg(jsonb_build_object('name', t.name, 'calls', t.calls) ORDER BY t.calls DESC, t.name), '[]'::jsonb)
        FROM (SELECT e.payload->>'name' AS name, count(*)::int AS calls FROM ka.agent_run_events e
              WHERE e.run_key=s.run_key AND e.kind='tool_call' GROUP BY 1) t) AS tool_counts,
```

and in `record` return `'tool_counts': tool_counts(conn, [run_key])` (computed inside the `with` block).

`claude_code_api.session_detail`, before `return row`:

```python
    row['tool_counts'] = tool_counts(conn, [f'cc:{session_id}:{turn}' for turn in range(1, row['turns'] + 1)])
```

`agent_sessions.detail`, after `value['runs'] = …` and the `commands` loop:

```python
            for run in value['runs']:
                run['tool_counts'] = tool_counts(conn, [f"run:{run['id']}"])
            value['tool_counts'] = tool_counts(conn, [f"run:{run['id']}" for run in value['runs']])
```

(`from knowledge_admin.run_record import tool_counts` in the three modules.)

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_tool_counts.py tests/test_agent_runs_api.py tests/test_claude_code_sessions.py tests/test_agent_sessions.py tests/test_run_record.py -q`
Expected: PASS.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api
git commit -m "feat(runs): tool usage counts on run records, sessions and Test chats

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 8: The Tools tab

**Files:**
- Create: `services/admin-ui/app/lib/tools.ts`, `services/admin-ui/app/components/AgentToolsView.tsx`
- Modify: `services/admin-ui/app/components/AgentDetailView.tsx`
- Create: `services/admin-ui/app/components/__tests__/agentTools.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/agentTabs.test.tsx`, `agentTypes.test.tsx` (tab lists gain
  `Tools` after `General`)

**Interfaces:**
- Consumes: Task 6's API.
- Produces (`lib/tools.ts`): `ToolGroup`, `AgentTool`, `AgentTools`, `CatalogTool`, `ToolsSummary`, `TOOL_GROUPS`,
  `NEED_REASONS`, `CLAUDE_CODE_TOOLS_NOTE`, `TOOLS_TAB_NOTE`, `getToolCatalog()`, `getAgentTools(id)`,
  `saveAgentTools(id, revision, tools)`, `toolNeedMet(need, switches)`, `impliedTools(catalog, switches)`,
  `toolsSummaryLabel(summary)`; `AgentToolsView({ agentId, onSaved, onDirtyChange })`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/agentTools.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router';
import AgentDetailView from '../AgentDetailView';
import * as profiles from '../../lib/profiles';
import * as tools from '../../lib/tools';
import * as llm from '../../lib/llm';
import { Toaster } from '~/components/ui/sonner';

vi.mock('../../lib/profiles', async () => ({ ...await vi.importActual('../../lib/profiles'), getAgent: vi.fn(), getAgentOptions: vi.fn(), saveAgent: vi.fn() }));
vi.mock('../../lib/tools', async () => ({ ...await vi.importActual('../../lib/tools'), getAgentTools: vi.fn(), saveAgentTools: vi.fn() }));
vi.mock('../../lib/llm', async () => ({ ...await vi.importActual('../../lib/llm'), getLlmSettings: vi.fn() }));
vi.mock('../OperationsView', () => ({ default: () => null }));
vi.mock('../PromptsView', () => ({ default: () => null }));
vi.mock('../AgentInstructions', () => ({ default: () => null }));
vi.mock('../AgentVersionsView', () => ({ default: () => null }));
vi.mock('../AgentModelSelection', () => ({ default: () => null }));

const agent: profiles.AgentProfile = { id: 'payments', name: 'Payments analyst', description: '', when_to_use: '', enabled: true,
  runtime: 'openai', claude_code_tools: ['Read', 'Grep', 'Glob', 'Bash'], documents_enabled: false, repositories_enabled: true,
  ssh_enabled: false, skills_enabled: false, mcp_enabled: true, services_enabled: false, documents_grounded: false, revision: 4,
  model_id: null, all_collections: false, all_repositories: true, collection_ids: [], repository_ids: [], service_ids: [],
  skill_ids: [], active_prompt: null };
const tool = (patch: Partial<tools.AgentTool>): tools.AgentTool => ({ tool: 'read_file', label: 'Read file',
  summary: 'Read a file or a line range.', group: 'code', mode: 'on', usage: null, default_usage: 'Read a source line range.',
  subagent_allowed: true, available: true, reason: null, tab: 'git', ...patch });
const native: tools.AgentTools = { runtime: 'openai', revision: 4, tools: [
  tool({}),
  tool({ tool: 'shell_run', label: 'Shell', summary: 'Run Bash in a sandbox.', mode: 'off', default_usage: 'Run Bash commands.' }),
  tool({ tool: 'ssh_run', label: 'SSH', summary: 'Run a command on a selected server.', group: 'servers', available: false,
    reason: 'Needs SSH servers', tab: 'ssh', default_usage: 'Execute a command.' }),
  tool({ tool: 'mcp:m1:lookup', label: 'Docs / lookup', summary: '', group: 'mcp', usage: 'Use for API pages.',
    default_usage: 'Look up docs.', tab: 'mcp' }),
] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ is_admin: true })));
  vi.mocked(profiles.getAgent).mockImplementation(async id => ({ ...structuredClone(agent), id, runtime: id === 'writer' ? 'claude_code' : 'openai' }));
  vi.mocked(profiles.getAgentOptions).mockResolvedValue({ collections: [], repositories: [], services: [], skills: [] });
  vi.mocked(llm.getLlmSettings).mockResolvedValue({ models: [], assignments: { search_model_id: null, code_model_id: null, operations_model_id: null, revision: 1 } });
  vi.mocked(tools.getAgentTools).mockImplementation(async id => id === 'writer'
    ? { runtime: 'claude_code', revision: 1, claude_code_tools: ['Read', 'Grep'], tools: [] } : structuredClone(native));
  vi.mocked(tools.saveAgentTools).mockImplementation(async (_id, revision, saved) => ({ ...native, revision: revision + 1, tools: saved }));
});
afterEach(() => vi.unstubAllGlobals());
function Detail() { const { id } = useParams(); return <AgentDetailView key={id} agentId={id!} />; }
const open = (path: string) => render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/agents/:id" element={<Detail />} /></Routes><Toaster /></MemoryRouter>);
const row = (name: string) => screen.getByRole('switch', { name }).closest('[data-tool]') as HTMLElement;

test('the Tools tab switches tools, edits and resets usage and saves once', async () => {
  const user = userEvent.setup();
  open('/agents/payments?view=tools');
  expect(await screen.findByRole('group', { name: 'Code' })).toBeVisible();
  expect(screen.getAllByRole('tab').map(tab => tab.textContent).slice(0, 2)).toEqual(['General', 'Tools']);
  expect(screen.queryByRole('group', { name: 'Agents' })).toBeNull();  // empty groups are hidden
  await user.click(screen.getByRole('switch', { name: 'Shell' }));
  await user.click(within(row('Read file')).getByRole('button', { name: 'Edit usage' }));
  const usage = screen.getByLabelText('Usage for Read file');
  expect(usage).toHaveValue('Read a source line range.');
  await user.clear(usage); await user.type(usage, 'Read only named files.');
  expect(within(row('Read file')).getByText('Edited')).toBeVisible();
  expect(within(row('Docs / lookup')).getByText('Edited')).toBeVisible();
  await user.click(within(row('Docs / lookup')).getByRole('button', { name: 'Edit usage' }));
  await user.click(within(row('Docs / lookup')).getByRole('button', { name: 'Reset' }));
  expect(within(row('Docs / lookup')).queryByText('Edited')).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Save tools' }));
  await waitFor(() => expect(tools.saveAgentTools).toHaveBeenCalledTimes(1));
  const [id, revision, saved] = vi.mocked(tools.saveAgentTools).mock.calls[0];
  expect([id, revision]).toEqual(['payments', 4]);
  expect(saved.map(({ tool: name, mode, usage: text }) => [name, mode, text])).toEqual([
    ['read_file', 'on', 'Read only named files.'], ['shell_run', 'on', null], ['ssh_run', 'on', null], ['mcp:m1:lookup', 'on', null]]);
  expect(await screen.findByText('Tools saved.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save tools' })).toBeDisabled();
});

test('unmet needs are greyed out with their reason and link', async () => {
  open('/agents/payments?view=tools');
  const ssh = await screen.findByRole('switch', { name: 'SSH' });
  const line = ssh.closest('[data-tool]') as HTMLElement;
  expect(line).toHaveClass('opacity-60');
  expect(within(line).getByText(/Needs SSH servers/)).toBeVisible();
  expect(within(line).getByRole('link', { name: 'Open SSH' })).toHaveAttribute('href', '/agents/payments?view=ssh');
  expect(row('Read file')).not.toHaveClass('opacity-60');
});

test('capability tabs of a native agent point to the Tools tab', async () => {
  open('/agents/payments?view=git');
  expect(await screen.findByText(tools.TOOLS_TAB_NOTE)).toBeVisible();
});

test('a Claude Code agent lists its Claude Code tools', async () => {
  open('/agents/writer?view=tools');
  const list = await screen.findByRole('list', { name: 'Claude Code tools' });
  expect(within(list).getAllByRole('listitem').map(item => item.textContent)).toEqual(['Read', 'Grep']);
  expect(screen.getByText(tools.CLAUDE_CODE_TOOLS_NOTE)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Save tools' })).toBeNull();
  expect(screen.queryByRole('switch', { name: 'Read file' })).toBeNull();
});
```

In `agentTabs.test.tsx` the expected tab list becomes
`['General','Tools','Skills','Documents','Code projects','SSH','MCP','Services','Prompts','Versions','Test']`, and
in `agentTypes.test.tsx` `TABS` becomes `['General', 'Tools', 'Skills', …]` likewise.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentTools.test.tsx`
Expected: FAIL: `Failed to resolve import "../../lib/tools"`.

- [ ] **Step 3: `lib/tools.ts`**

```ts
import { request } from './api';
import type { AgentRuntime, ClaudeCodeTool } from './profiles';

export type ToolGroup = 'code' | 'documents' | 'servers' | 'mcp' | 'agents';
export type ToolNeed = 'files' | 'code' | 'code_graph' | 'documents' | 'grounded' | 'ssh';
export interface AgentTool {
  tool: string; label: string; summary: string; group: ToolGroup; mode: 'on' | 'off' | 'per_run';
  usage: string | null; default_usage: string; subagent_allowed: boolean; available: boolean; reason: string | null; tab: string | null;
}
export interface AgentTools { runtime: AgentRuntime; revision: number; claude_code_tools?: ClaudeCodeTool[]; tools: AgentTool[] }
export interface CatalogTool { name: string; group: ToolGroup; label: string; summary: string; usage: string; needs: ToolNeed | null }
export interface ToolsSummary { on?: number; per_run?: number; groups?: Partial<Record<ToolGroup, number>>; claude_code_tools?: string[] }
export const TOOL_GROUPS: [ToolGroup, string][] = [['code', 'Code'], ['documents', 'Documents'], ['servers', 'Servers'], ['mcp', 'MCP'], ['agents', 'Agents']];
export const NEED_REASONS: Record<ToolNeed, string> = {
  files: 'Needs code projects, or documents without "Answer only from documents"', code: 'Needs code projects',
  code_graph: 'Needs code projects', documents: 'Needs documents', grounded: 'Needs "Answer only from documents"', ssh: 'Needs SSH servers' };
export const CLAUDE_CODE_TOOLS_NOTE = "Claude Code agents use Claude Code's own tools on their working folders. Switch them on the General tab.";
export const TOOLS_TAB_NOTE = 'Tools for these projects are chosen on the Tools tab.';
export const TAB_LABELS: Record<string, string> = { git: 'Code projects', documents: 'Documents', ssh: 'SSH', mcp: 'MCP' };

const path = (id: string) => `/api/agents/${encodeURIComponent(id)}/tools`;
export const getToolCatalog = () => request<{ tools: CatalogTool[] }>('/api/tools');
export const getAgentTools = (id: string) => request<AgentTools>(path(id));
/** Replaces the agent's tools; a usage of null keeps the built-in text. */
export const saveAgentTools = (id: string, revision: number, tools: AgentTool[]) => request<AgentTools>(path(id), {
  method: 'PUT', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ expected_revision: revision,
    tools: tools.map(({ tool, mode, usage }) => ({ tool, mode: mode === 'on' ? 'on' : 'off', usage })) }),
});

export interface ToolSwitches { repositories_enabled: boolean; documents_enabled: boolean; documents_grounded?: boolean; ssh_enabled: boolean }
/** The capability rule of the backend catalog (needs_met without run-time facts). */
export function toolNeedMet(need: ToolNeed | null, s: ToolSwitches): boolean {
  const grounded = s.documents_enabled && Boolean(s.documents_grounded);
  switch (need) {
    case null: return true;
    case 'files': return s.repositories_enabled || s.documents_enabled && !grounded;
    case 'code': case 'code_graph': return s.repositories_enabled;
    case 'documents': return s.documents_enabled;
    case 'grounded': return grounded;
    case 'ssh': return s.ssh_enabled;
  }
}
/** The tools a new agent gets from its capabilities (the backend's implied_tools). */
export const impliedTools = (catalog: CatalogTool[], s: ToolSwitches) => catalog.filter(t => toolNeedMet(t.needs, s)).map(t => t.name);
/** All agents: "7 tools · Code 6 · Servers 1"; Claude Code agents: "Read, Grep, Glob, Bash". */
export function toolsSummaryLabel(summary?: ToolsSummary): string {
  if (!summary) return '—';
  if (summary.claude_code_tools) return summary.claude_code_tools.length ? summary.claude_code_tools.join(', ') : 'No tools';
  if (!summary.on) return 'No tools';
  const parts = [`${summary.on} tool${summary.on === 1 ? '' : 's'}`,
    ...TOOL_GROUPS.filter(([group]) => summary.groups?.[group]).map(([group, label]) => `${label} ${summary.groups![group]}`)];
  if (summary.per_run) parts.push(`+${summary.per_run} per run`);
  return parts.join(' · ');
}
```

- [ ] **Step 4: `AgentToolsView.tsx`**

```tsx
import { useEffect, useId, useState } from 'react';
import { Link } from 'react-router';
import { toast } from 'sonner';
import { CLAUDE_CODE_TOOLS_NOTE, TAB_LABELS, TOOL_GROUPS, getAgentTools, saveAgentTools, type AgentTool, type AgentTools } from '../lib/tools';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '~/components/ui/card';
import { Field, FieldDescription, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Skeleton } from '~/components/ui/skeleton';
import { Switch } from '~/components/ui/switch';
import { Textarea } from '~/components/ui/textarea';

function ToolRow({ tool, editing, disabled, onEdit, onChange }: { tool: AgentTool; editing: boolean; disabled: boolean; onEdit: () => void; onChange: (patch: Partial<AgentTool>) => void }) {
  const id = useId();
  return <div data-tool={tool.tool} className={`flex flex-col gap-3 p-3 ${tool.available ? '' : 'opacity-60'}`}>
    <div className="flex items-start gap-3">
      <Switch id={id} aria-label={tool.label} disabled={disabled} checked={tool.mode === 'on'} onCheckedChange={on => onChange({ mode: on ? 'on' : 'off' })} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2"><label htmlFor={id} className="text-sm font-medium">{tool.label}</label>
          {tool.group !== 'mcp' && <code className="text-xs text-muted-foreground">{tool.tool}</code>}
          {tool.usage !== null && <Badge variant="outline">Edited</Badge>}</div>
        {tool.summary && <p className="text-sm text-muted-foreground">{tool.summary}</p>}
        {!tool.available && tool.reason && <p className="text-sm text-muted-foreground">{tool.reason}{tool.tab && <> · <Link className="underline underline-offset-4" to={`?view=${tool.tab}`}>Open {TAB_LABELS[tool.tab] ?? tool.tab}</Link></>}</p>}
      </div>
      <Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={onEdit}>{editing ? 'Close' : 'Edit usage'}</Button>
    </div>
    {editing && <Field>
      <FieldLabel htmlFor={`${id}-usage`}>Usage for {tool.label}</FieldLabel>
      <Textarea id={`${id}-usage`} rows={8} maxLength={8000} disabled={disabled} value={tool.usage ?? tool.default_usage}
        onChange={event => onChange({ usage: event.target.value === tool.default_usage ? null : event.target.value })} />
      <FieldDescription>{tool.group === 'mcp' ? 'Added to the prompt under Tool notes; the server keeps its own description.' : 'Replaces the built-in description the model reads.'}</FieldDescription>
      <Button type="button" variant="outline" size="sm" className="w-fit" disabled={disabled || tool.usage === null} onClick={() => onChange({ usage: null })}>Reset</Button>
    </Field>}
  </div>;
}

export default function AgentToolsView({ agentId, onSaved, onDirtyChange }: { agentId: string; onSaved?: (revision: number) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [saved, setSaved] = useState<AgentTools>();
  const [draft, setDraft] = useState<AgentTool[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => {
    let live = true;
    getAgentTools(agentId).then(value => { if (live) { setSaved(value); setDraft(value.tools); setDirty(false); setError(''); } })
      .catch(reason => { if (live) setError(reason instanceof Error ? reason.message : 'Could not load the tools.'); });
    return () => { live = false; };
  }, [agentId, reload]);
  function change(name: string, patch: Partial<AgentTool>) { setDraft(current => current.map(item => item.tool === name ? { ...item, ...patch } : item)); setDirty(true); }
  async function save() {
    if (!saved) return;
    setBusy(true); setError('');
    try {
      const next = await saveAgentTools(agentId, saved.revision, draft);
      setSaved(next); setDraft(next.tools); setDirty(false); setEditing(null); onSaved?.(next.revision);
      toast.success('Tools saved.', { id: 'agent-tools-saved' });
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save the tools.'); }
    finally { setBusy(false); }
  }
  if (!saved) return error ? <Alert variant="destructive"><AlertDescription>{error}<Button variant="outline" size="sm" onClick={() => setReload(n => n + 1)}>Retry</Button></AlertDescription></Alert> : <Skeleton className="h-64 w-full" />;
  if (saved.runtime === 'claude_code') return <Card><CardHeader><CardTitle>Tools</CardTitle><CardDescription>{CLAUDE_CODE_TOOLS_NOTE}</CardDescription></CardHeader>
    <CardContent>{saved.claude_code_tools?.length ? <ul aria-label="Claude Code tools" className="flex flex-wrap gap-2">{saved.claude_code_tools.map(name => <li key={name}><Badge variant="secondary">{name}</Badge></li>)}</ul>
      : <p className="text-sm text-muted-foreground">No Claude Code tools are on.</p>}</CardContent></Card>;
  return <Card><CardHeader><CardTitle>Tools</CardTitle><CardDescription>What this agent can do. The usage instructions are what the model reads about each tool; edit them to steer when and how it is used.</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-6">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {TOOL_GROUPS.map(([group, label]) => {
        const items = draft.filter(item => item.group === group);
        return items.length ? <FieldSet key={group}><FieldLegend>{label}</FieldLegend><div className="flex flex-col divide-y rounded-md border">
          {items.map(item => <ToolRow key={item.tool} tool={item} disabled={busy} editing={editing === item.tool}
            onEdit={() => setEditing(current => current === item.tool ? null : item.tool)} onChange={patch => change(item.tool, patch)} />)}
        </div></FieldSet> : null;
      })}
    </CardContent>
    <CardFooter className="flex-wrap gap-2"><Button disabled={busy || !dirty} onClick={() => void save()}>Save tools</Button>
      <Button variant="outline" disabled={busy || !dirty} onClick={() => { setDraft(saved.tools); setDirty(false); setEditing(null); }}>Discard changes</Button></CardFooter>
  </Card>;
}
```

- [ ] **Step 5: Add the tab**

In `AgentDetailView.tsx`:
- `const sections = ['general','tools','skills','documents','git','ssh','mcp','services','prompts','versions','test'];`
- state `const [toolsDirty, setToolsDirty] = useState(false);` and
  `const [toolsOpened, setToolsOpened] = useState(false); useEffect(() => { if (view === 'tools') setToolsOpened(true); }, [view]);`
- `AgentProfileSettings`'s wrapper: `hidden={['test','prompts','versions','tools'].includes(view)}`;
- versions: `dirty={profileDirty || configurationDirty || toolsDirty}`;
- inside `TabsContent`, after the working-configuration alert:

```tsx
          {!claudeCode && ['git','documents','ssh','mcp'].includes(view) && <p className="text-sm text-muted-foreground"><span>{TOOLS_TAB_NOTE}</span> <Link className="underline underline-offset-4" to="?view=tools">Open Tools</Link></p>}
          {toolsOpened && <div hidden={view !== 'tools'}><AgentToolsView key={reload} agentId={agentId} onDirtyChange={setToolsDirty}
            onSaved={revision => setProfile(current => current && { ...current, revision })} /></div>}
```

(imports `AgentToolsView` and `TOOLS_TAB_NOTE`). `AgentProfileSettings` already copies a new `profile.revision`
into its draft, so General keeps saving after a tools save.

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/agentTools.test.tsx app/components/__tests__/agentTabs.test.tsx app/components/__tests__/agentTypes.test.tsx`
Expected: PASS.

Run: `cd services/admin-ui && pnpm exec vitest run`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-ui
git commit -m "feat(admin-ui): Tools tab with switches, editable usage instructions and unmet needs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: The wizard's Tools page and the All agents Tools column

**Files:**
- Modify: `services/admin-ui/app/lib/agentWizard.ts` (`WizardOptions.tools`, `NewAgent.tools`)
- Modify: `services/admin-ui/app/components/AgentWizard.tsx`
- Modify: `services/admin-ui/app/lib/profiles.ts` (`AgentSummary.tools_summary`), `services/admin-ui/app/components/AgentsView.tsx`
- Modify: `services/admin-ui/app/components/__tests__/agentWizard.test.tsx`, `agentTypes.test.tsx`

**Interfaces:**
- Consumes: `lib/tools.ts` (Task 8), `GET /api/agents/wizard/options` `tools` and `CreateInput.tools` (Task 6),
  `tools_summary` (Task 6).
- Produces: `WizardOptions.tools?: CatalogTool[]`; `NewAgent.tools?: string[]` (sent only after a switch was
  changed); `AgentSummary.tools_summary?: ToolsSummary`.

- [ ] **Step 1: Write the failing tests**

Append to `agentWizard.test.tsx` (and give the shared `options` a `tools` list):

```tsx
const catalogTools = [
  { name: 'read_file', group: 'code', label: 'Read file', summary: 'Read a file.', usage: '', needs: 'files' },
  { name: 'code_search', group: 'code', label: 'Code search', summary: 'Find functions.', usage: '', needs: 'code' },
  { name: 'search_documents', group: 'documents', label: 'Search documents', summary: 'Search.', usage: '', needs: 'documents' },
  { name: 'ssh_run', group: 'servers', label: 'SSH', summary: 'Run a command.', usage: '', needs: 'ssh' },
] as const;
// in `const options`: tools: [...catalogTools],

test('the wizard Tools page follows the capabilities until changed and sends the choice', async () => {
  const { user } = await open();
  await user.click(screen.getByRole('button', { name: 'Generate proposal' }));
  await screen.findByLabelText('Agent name');
  await user.click(screen.getByRole('tab', { name: 'Tools' }));
  const checked = () => ['Read file', 'Code search', 'Search documents', 'SSH'].filter(name => screen.getByRole('switch', { name }).getAttribute('aria-checked') === 'true');
  expect(checked()).toEqual(['Read file', 'Code search', 'Search documents', 'SSH']);
  await user.click(screen.getByRole('tab', { name: 'SSH' }));
  await user.click(screen.getByRole('switch', { name: 'Enable SSH' }));
  await user.click(screen.getByRole('tab', { name: 'Tools' }));
  expect(checked()).toEqual(['Read file', 'Code search', 'Search documents']);  // follows the capabilities
  await user.click(screen.getByRole('switch', { name: 'Code search' }));
  await user.click(screen.getByRole('tab', { name: 'SSH' }));
  await user.click(screen.getByRole('switch', { name: 'Enable SSH' }));
  await user.click(screen.getByRole('tab', { name: 'Tools' }));
  expect(checked()).toEqual(['Read file', 'Search documents']);  // the administrator's choice now
  await user.click(screen.getByRole('button', { name: 'Create agent' }));
  expect(api.createWizardAgent).toHaveBeenCalledWith(expect.objectContaining({ tools: ['read_file', 'search_documents'] }));
});

test('an untouched Tools page lets the backend apply the capabilities; Claude Code agents have no Tools page', async () => {
  const { user } = await open();
  await user.click(screen.getByRole('button', { name: 'Generate proposal' }));
  await screen.findByLabelText('Agent name');
  await user.click(screen.getByRole('button', { name: 'Create agent' }));
  expect(vi.mocked(api.createWizardAgent).mock.calls[0][0]).not.toHaveProperty('tools');
  await user.click(screen.getByRole('button', { name: 'Edit role' }));
  await user.click(screen.getByRole('combobox', { name: 'Agent type' }));
  await user.click(screen.getByRole('option', { name: 'Claude Code' }));
  await user.click(screen.getByRole('button', { name: 'Back to proposal' }));
  expect(screen.queryByRole('tab', { name: 'Tools' })).toBeNull();
});
```

Append to `agentTypes.test.tsx`:

```tsx
test('All agents shows each agent\'s tools', async () => {
  vi.mocked(profiles.getAgents).mockResolvedValue({ agents: [
    { ...structuredClone(writer), id: 'analyst', name: 'Analyst', runtime: 'openai', model_name: null, prompt_title: null, prompt_version: null,
      tools_summary: { on: 3, per_run: 0, groups: { code: 1, documents: 0, servers: 1, mcp: 1, agents: 0 } } },
    { ...structuredClone(writer), model_name: null, prompt_title: null, prompt_version: null,
      tools_summary: { claude_code_tools: ['Read', 'Grep', 'Glob', 'Bash'] } }] });
  render(<MemoryRouter><AgentsView /></MemoryRouter>);
  expect(await screen.findByRole('columnheader', { name: 'Tools' })).toBeVisible();
  expect(within(screen.getByText('Analyst').closest('tr')!).getByText('3 tools · Code 1 · Servers 1 · MCP 1')).toBeVisible();
  expect(within(screen.getByText('Technical writer').closest('tr')!).getByText('Read, Grep, Glob, Bash')).toBeVisible();
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/agentWizard.test.tsx app/components/__tests__/agentTypes.test.tsx`
Expected: FAIL: no tab `Tools`, no column `Tools`.

- [ ] **Step 3: Implement the wizard page**

`lib/agentWizard.ts`: `WizardOptions` gains `tools?: CatalogTool[]`; `NewAgent` gains `tools?: string[]`
(`import type { CatalogTool } from './tools'`).

`AgentWizard.tsx`:
- state `const [toolChoice, setToolChoice] = useState<string[] | null>(null);` (null: follow the capabilities);
  `generate()` and `startBlank()` call `setToolChoice(null)`;
- derived values:

```tsx
  const catalogTools = options?.tools ?? [];
  const impliedNames = proposal ? impliedTools(catalogTools, proposal) : [];
  const chosenTools = toolChoice ?? impliedNames;
  const inCatalogOrder = (names: string[]) => catalogTools.map(tool => tool.name).filter(name => names.includes(name));
```

- the tab list appends `...(runtime === 'openai' ? [['tools','Tools']] : [])` after `['services','Services']`;
- the page:

```tsx
        {runtime === 'openai' && <TabsContent value="tools"><FieldGroup>
          <FieldDescription>{toolChoice ? 'Your choice of tools.' : 'Follows the capability switches until you change a tool.'} Usage instructions can be edited on the agent’s Tools tab after it is created.</FieldDescription>
          {TOOL_GROUPS.map(([group, label]) => {
            const items = catalogTools.filter(tool => tool.group === group);
            return items.length ? <FieldSet key={group}><FieldLegend>{label}</FieldLegend>{items.map(tool => {
              const met = toolNeedMet(tool.needs, proposal);
              return <Field key={tool.name} orientation="horizontal" className={met ? '' : 'opacity-60'}>
                <Switch id={`wizard-tool-${tool.name}`} checked={chosenTools.includes(tool.name)}
                  onCheckedChange={on => setToolChoice(inCatalogOrder([...chosenTools.filter(name => name !== tool.name), ...(on ? [tool.name] : [])]))} />
                <FieldContent><FieldLabel htmlFor={`wizard-tool-${tool.name}`}>{tool.label}</FieldLabel>
                  <FieldDescription>{tool.summary}{!met && tool.needs ? ` ${NEED_REASONS[tool.needs]}.` : ''}</FieldDescription></FieldContent>
              </Field>; })}</FieldSet> : null;
          })}
          {toolChoice && <Button type="button" variant="outline" className="w-fit" onClick={() => setToolChoice(null)}>Follow the capabilities</Button>}
        </FieldGroup></TabsContent>}
```

- `create()` sends `{ ...proposal, runtime, resources, claude_code_tools: [...CLAUDE_CODE_TOOL_NAMES], ...(runtime === 'openai' && toolChoice ? { tools: inCatalogOrder(toolChoice) } : {}) }`;
- imports: `import { NEED_REASONS, TOOL_GROUPS, impliedTools, toolNeedMet } from '../lib/tools';`.

- [ ] **Step 4: The list column**

`lib/profiles.ts`: `AgentSummary` gains `tools_summary?: ToolsSummary` (`import type { ToolsSummary } from './tools'`).
`AgentsView.tsx`: header `<TableHead>Tools</TableHead>` after `Resources`; cell
`<TableCell className="max-w-xs whitespace-normal">{toolsSummaryLabel(a.tools_summary)}</TableCell>` after the
Resources cell; the search text adds `${toolsSummaryLabel(a.tools_summary)}`; the empty row's `colSpan` becomes 9.

- [ ] **Step 5: Run the tests**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/agentWizard.test.tsx app/components/__tests__/agentTypes.test.tsx app/components/__tests__/agentTabs.test.tsx`
Expected: PASS.

Run: `cd services/admin-ui && pnpm exec vitest run`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui
git commit -m "feat(admin-ui): wizard Tools page following the capabilities, and the All agents Tools column

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Tool usage on the screens

**Files:**
- Modify: `services/admin-ui/app/lib/runs.ts`, `lib/claudeCode.ts`, `lib/operations.ts`
- Modify: `services/admin-ui/app/components/RunsView.tsx`, `RunRecordView.tsx`, `ClaudeCodeSessionView.tsx`, `AgentTestView.tsx`
- Modify: `services/admin-ui/app/components/__tests__/runs.test.tsx`, `claudeCodeSession.test.tsx`, `agentSessions.test.tsx`

**Interfaces:**
- Consumes: Task 7's `tool_counts`.
- Produces: `ToolCount`, `toolUsageLine(counts)` (`code_graph 12 · read_file 31` or `No tools used`) and
  `topTools(counts, limit = 3)` (adds `+n`) in `lib/runs.ts`; `tool_counts` on `RunSummary`, `RunRecord`,
  `SessionDetail`, `OperationsRun`, `AgentConversation`.

- [ ] **Step 1: Write the failing tests**

`runs.test.tsx`: the shared run gains `tool_counts: [{ name: 'read_file', calls: 31 }, { name: 'code_graph', calls: 12 }, { name: 'shell_run', calls: 4 }, { name: 'ssh_run', calls: 1 }]`, and:

```tsx
test('runs list and record show the tools each run used', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.startsWith('/api/agent-runs/') ? json({ run_key: 'run:1', agent_id: 'search', user: null,
    tool_counts: [{ name: 'code_graph', calls: 12 }, { name: 'read_file', calls: 31 }], events: [
      { seq: 1, kind: 'start', agent_id: 'search', payload: { kind: 'test', title: 'Graph run' }, created_at: '2026-10-04T10:00:00Z' }] })
    : json({ next: null, runs: [runs.runs[0], { ...runs.runs[0], run_key: 'run:2', title: 'Quiet run', tool_counts: [] }] })));
  render(<MemoryRouter><RunsView /></MemoryRouter>);
  expect(await screen.findByRole('columnheader', { name: 'Tools' })).toBeVisible();
  const busy = screen.getByText('How many retries?').closest('tr')!;
  const cell = within(busy).getByText('read_file 31 · code_graph 12 · shell_run 4 +1');
  expect(cell.closest('td')).toHaveAttribute('title', 'read_file 31 · code_graph 12 · shell_run 4 · ssh_run 1');
  expect(within(screen.getByText('Quiet run').closest('tr')!).getByText('No tools used')).toBeVisible();
  render(<MemoryRouter><RunRecordView runKey="run:1" /></MemoryRouter>);
  expect(await screen.findByLabelText('Tools used')).toHaveTextContent('code_graph 12 · read_file 31');
});
```

`claudeCodeSession.test.tsx` (the `detail` fixture gains `tool_counts: [{ name: 'Read', calls: 5 }, { name: 'Grep', calls: 2 }]`):

```tsx
test('the session page shows the tools the session used', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([]) : Response.json(detail)));
  render(<MemoryRouter><ClaudeCodeSessionView sessionId="s1" /></MemoryRouter>);
  expect(await screen.findByLabelText('Tools used')).toHaveTextContent('Read 5 · Grep 2');
});
```

`agentSessions.test.tsx`:

```tsx
it('a Test chat answer shows its tools and Show config lists the conversation\'s', async () => {
  vi.mocked(api.getAgentSession).mockResolvedValue({ ...conversation, tool_counts: [{ name: 'read_file', calls: 2 }],
    runs: [{ ...run, tool_counts: [{ name: 'read_file', calls: 2 }] }, { ...run, id: 'run-2', task: 'Second', answer: 'Second answer', tool_counts: [] }] });
  renderPage('?view=test&session_id=session-1');
  await screen.findByText('Second answer');
  expect(screen.getAllByLabelText('Tools used').map(line => line.textContent)).toEqual(['read_file 2', 'No tools used']);
  fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
  await waitFor(() => expect(screen.getByLabelText('Agent configuration')).toHaveTextContent('"tool_counts"'));
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/runs.test.tsx app/components/__tests__/claudeCodeSession.test.tsx app/components/__tests__/agentSessions.test.tsx`
Expected: FAIL: no `Tools` column, no `Tools used` line.

- [ ] **Step 3: Implement**

`lib/runs.ts`:

```ts
export interface ToolCount { name: string; calls: number }
/** "code_graph 12 · read_file 31", or "No tools used". */
export const toolUsageLine = (counts?: ToolCount[] | null) =>
  counts?.length ? counts.map(({ name, calls }) => `${name} ${calls}`).join(' · ') : 'No tools used';
/** The most used tools and how many more: "read_file 31 · code_graph 12 · shell_run 4 +1". */
export function topTools(counts?: ToolCount[] | null, limit = 3) {
  if (!counts?.length) return 'No tools used';
  const more = counts.length - limit;
  return toolUsageLine(counts.slice(0, limit)) + (more > 0 ? ` +${more}` : '');
}
```

and `RunSummary.tool_counts: ToolCount[]`, `RunRecord.tool_counts?: ToolCount[]`.
`lib/claudeCode.ts`: `SessionDetail.tool_counts?: ToolCount[]`. `lib/operations.ts`: `OperationsRun.tool_counts?: ToolCount[]`,
`AgentConversation.tool_counts?: ToolCount[]` (type imports from `./runs`).

`RunsView.tsx`: header `<TableHead>Tools</TableHead>` after `Tokens`; cell
`<TableCell className="max-w-xs whitespace-normal" title={toolUsageLine(row.tool_counts)}>{topTools(row.tool_counts)}</TableCell>`.

`RunRecordView.tsx`, in the header line after the tokens span:
`<span>·</span><span aria-label="Tools used">{toolUsageLine(record.tool_counts)}</span>`.

`ClaudeCodeSessionView.tsx`, after the tokens paragraph:
`{session.tool_counts && <p className="text-sm text-muted-foreground" aria-label="Tools used">{toolUsageLine(session.tool_counts)}</p>}`.

`AgentTestView.tsx`:
- in the answer `details`, first line: `{run.tool_counts && <p className="text-xs text-muted-foreground" aria-label="Tools used">{toolUsageLine(run.tool_counts)}</p>}`;
- Show config: `JSON.stringify(conversation || !perRun ? {prompt:task,config,...(conversation?.tool_counts ? {tool_counts:conversation.tool_counts} : {})} : {prompt:task,config,run_resources:runResources},null,2)`.

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/runs.test.tsx app/components/__tests__/claudeCodeSession.test.tsx app/components/__tests__/agentSessions.test.tsx`
Expected: PASS.

Run: `cd services/admin-ui && pnpm exec vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui
git commit -m "feat(admin-ui): show which tools each run used in Agent runs, run records, sessions and Test chats

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Documentation and the full verification

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/tools/README.md`, `services/admin-api/README.md`
- Modify: `docs/agent-profiles.md`, `docs/agents.md`, `docs/AGENTS.md`, `docs/code-processing.md`, `docs/migrations.md`,
  `docs/user-guide.md`

- [ ] **Step 1: Update the docs**

- `tools/README.md`: replace "Import decorated functions directly. There is no tool factory or registry." and the
  example with: implementations in `knowledge_admin/capabilities/` (one line per module); `tools/catalog.py`
  (`ToolSpec`, usage text, needs, `implied_tools`, `effective_tools`); `tools/sdk.py` (the only Agents SDK binding;
  the backstop on `context['tools']`); how `create_agent` builds tools, the MCP `tool_filter` and Tool notes. Keep
  the ast-grep, code graph, Bash and shell-worker sections, with the worker at `capabilities/shell/worker.py`.
- `docs/agent-profiles.md`: a **Tools** section: the tab, on/off, Edit usage and Reset, "Needs …" rows, MCP tools
  default on and Tool notes, the save rules (grounded needs report_not_covered; switching a capability on brings
  its default tools), Claude Code agents keep their four tools on General, the wizard Tools page, the list column.
- `docs/agents.md` and `docs/AGENTS.md`: tools are part of the configuration and its versions; versions and
  conversations saved before 0078 use the tools their capabilities gave; tool usage per run (Agent runs, run
  record, session page, Test chats); `code_graph` is offered when a selected code project has a current graph.
- `docs/code-processing.md`: the agent's `code.graphs` comes from the generated graph (`current_graph`), live.
- `docs/migrations.md`: `0078_agent_tools` (backfill rule; down drops the table and edited usage text).
- `docs/user-guide.md`: one paragraph on choosing tools.

- [ ] **Step 2: Run every suite**

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: PASS.

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, exit 0.

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run`
Expected: PASS.

Run: `git grep -n -E "get_tools|knowledge_admin\.shell_worker|from knowledge_admin\.tools\.(files|shell|ssh|documents|code_graph|code_search|ast_grep) import" -- ':!docs/superpowers'`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add services/admin-api docs
git commit -m "docs: agent tools (selection, usage instructions, tool usage per run) and the capabilities layout

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Follow-ups (later delivery steps of the spec, not in this plan)

- **Step 2, `run_subagent`:** the tool and its switch in the Agents group, "A subagent may use" (`subagent_allowed`)
  on the Tools tab and in the wizard, the subagent prompt, the parallel limit, Stop, recording with
  `parent_call_id`, tokens and the collapsible block.
- **Step 3, built-in agents and Code reader:** `0079_builtin_agents`, install at start-up, the update notice.
- **Step 4, extra tools per run:** "Allow per run" (`mode = 'per_run'`), `0080_run_tools`, `run_tools.resolve`,
  the pickers in the Test tab and Generate documents.
- **Step 5, `ask_agent`:** callee switches in the Agents group, the tool, no-escalation checks, the callee block.
- Live tool counts while a session turn runs (today they refresh with the session detail).
