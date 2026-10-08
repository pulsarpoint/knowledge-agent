# Agent tools — design

Date: 2026-10-08. Status: draft for review. Builds on `2026-10-08-per-run-resources-design.md`.

## Goal

An agent is **instructions + skills + tools + resources**:

- **Tools** are what the agent can do. Each tool is switched on or off per agent and has **usage instructions**
  that the model sees. The agent's settings can change them.
- **Resources** are what it works on: code projects, collections, servers and services, either fixed or given per
  run. They stay separate from tools.

Today capabilities imply the tools (`agents.get_tools`), and their usage hints are hard-coded in `agents.get_prompt`.
This spec makes tools explicit and visible, and adds three new abilities:

1. **Tool usage per run**: every run shows which tools it called and how often.
2. **Tools tab**: one switch and editable usage instructions per tool. Tools are saved in versions and listed in
   All agents.
3. **Subagents** (`run_subagent`): a native agent spawns short-lived helpers, each with a subset of its own tools and
   run resources.
4. **Built-in agents**, starting with **Code reader**.
5. **Extra tools per run**: a caller switches on, for one run, a tool the agent allows per run.
6. **`ask_agent`**: call a saved agent as a tool.

The focus is native agents (OpenAI Agents SDK 0.19.2). Claude Code agents keep working with their four Claude Code
tools and get no new features.

**Framework decision:** we stay on the OpenAI Agents SDK. Tools are defined independently of it, as a catalog of
plain async functions with a pydantic argument model. One small adapter turns a catalog entry into an SDK
`FunctionTool`, so a later change of framework only replaces the adapter.

## Today's tools (what the migration preserves)

| Group | Tool | Offered today when |
| --- | --- | --- |
| Code | `list_files`, `read_file`, `search_files`, `shell_run` | Code projects on, or Documents on and not grounded |
| Code | `code_search`, `ast_grep` | Code projects on |
| Code | `code_graph` | Code projects on and `config['code']['graphs']` non-empty (see note) |
| Documents | `search_documents`, `read_document` | Documents on |
| Documents | `report_not_covered` | Documents on and grounded |
| Servers | `ssh_run` | SSH on with at least one usable server |
| MCP | every tool of the selected MCP servers | MCP on |

**Note:** `get_agent_config` never fills `code.graphs`, so `code_graph` is never offered today. Step 1 fixes this. The
loader and `run_resources._fresh` add one entry per code project that has a generated graph, using
`code_processing.load_code_graph`: `id`, `graph_path`, `code_root` and `source_hash`. This is the shape that
`tools/code_graph.py` and `tools/shell.py` already read.

## Tool catalog (code)

- **New module `knowledge_admin/tools/catalog.py`.** It holds one `ToolSpec` per built-in tool:
  ```python
  @dataclass(frozen=True)
  class ToolSpec:
      name: str                      # model-facing name, e.g. 'read_file'
      group: Literal['code', 'documents', 'servers', 'agents']
      label: str                     # Tools tab
      summary: str                   # one line for the Tools tab
      usage: str                     # built-in usage instructions (the default description)
      arguments: type[BaseModel]     # JSON schema for the model
      run: Callable[[AgentContext, BaseModel], Awaitable[str]]
      needs: Literal['files', 'code', 'code_graph', 'documents', 'grounded', 'ssh', None]
      default_on: Callable[[dict], bool]  # the "implied by capabilities" rule above
  ```
- **Built-in usage text.** For each tool it is today's docstring plus the sentences about that tool that
  `get_prompt` hard-codes now. For example, the `shell_run` text gains "Selected resource paths are mounted
  read-only…", and the `code_graph` text gains "Use code_graph first…". `get_prompt` keeps the resources JSON, the
  general rules and grounding, and drops the tool sentences.
- **New module `knowledge_admin/tools/sdk.py`:** `function_tool(spec, usage) -> FunctionTool`. It sets `name`,
  `description=usage`, `params_json_schema=spec.arguments.model_json_schema()` and an `on_invoke_tool` that:
  - validates the arguments;
  - checks that `spec.name` is in `context['tools']`, the run's effective tool names, as a backstop;
  - calls `spec.run`.
- **The tool modules** (`files.py`, `shell.py`, `code_graph.py`, …) lose `@function_tool`. Their bodies become
  `run(context, args)`, with the same behaviour and the same errors.
- **MCP tools are not catalog entries.** Their names and descriptions come from each server's discovered list,
  `ka.mcp_servers.tools`.

## Feature 1 — Tool usage per run

- **Source:** `ka.agent_run_events` rows of kind `tool_call`. Native runs and Claude Code turns already record
  `payload.name`. Subagent and `ask_agent` calls are recorded in the parent's record (features 3 and 6), so they are
  counted too. No migration is needed.
- **One query helper:** `run_record.tool_counts(conn, run_keys) -> list[{name, calls}]`, ordered by calls
  descending, then name:
  `SELECT payload->>'name' AS name, count(*) AS calls FROM ka.agent_run_events WHERE run_key=ANY(%s) AND kind='tool_call' GROUP BY 1`.
  The `UNIQUE(run_key, seq)` index serves it.
- **APIs:**
  - `GET /api/agent-runs`: each run gains `tool_counts`. It is a correlated subquery like `total_tokens`, using
    `jsonb_agg` ordered by count.
  - `GET /api/agent-runs/{run_key}`: gains `tool_counts`.
  - `GET /api/claude-code/sessions/{id}` (Generate documents session, either agent type): gains `tool_counts` over
    all its turns. The keys are `cc:<id>:1..n`, built from the session's turn count with `=ANY`, not a prefix scan.
  - `GET /api/agents/{agent_id}/sessions/{session_id}` (Test chat): gains `tool_counts` over its runs
    (`run:<operations_runs.id>`). Each run in `runs` carries its own counts.
- **Screens:** one line of the form `code_graph 12 · read_file 31 · shell_run 4`:
  - in the run record header (Activity → Agent runs → run);
  - in the session page header (Generate documents);
  - under each Test chat answer and in the Test conversation's **Show config**;
  - in **Activity → Agent runs**, a **Tools** column with the top three and "+n". Its tooltip lists all of them.
  - A run without tool calls shows "No tools used".

## Feature 2 — Tools tab (fixed tool selection)

### Data model

The migration is `0078_agent_tools`. Migration numbers follow the coordinator removal, which is expected to be 0077;
renumber at implementation if needed.

```sql
CREATE TABLE ka.agent_tools (
    agent_id         text NOT NULL REFERENCES ka.agents(id) ON DELETE CASCADE,
    tool             text NOT NULL CHECK (char_length(tool) BETWEEN 1 AND 300),
        -- catalog name ('read_file', 'run_subagent'), 'mcp:<mcp_server_id>:<tool name>' or 'ask_agent:<agent id>'
    mode             text NOT NULL DEFAULT 'on' CHECK (mode IN ('off','on','per_run')),
        -- per_run: off unless a caller switches it on for one run (feature 5)
    usage            text CHECK (usage IS NULL OR char_length(usage) <= 8000),
        -- NULL: the built-in usage instructions (catalog) or the MCP server's description
    subagent_allowed boolean NOT NULL DEFAULT true,
        -- run_subagent may give this tool to a subagent (feature 3)
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, tool),
    CONSTRAINT subagents_do_not_spawn CHECK (NOT subagent_allowed OR (tool <> 'run_subagent' AND tool NOT LIKE 'ask_agent:%'))
);
```

- **What a missing row means:**
  - **Catalog tools:** off. Agents get explicit rows, from the backfill and on creation.
  - **MCP tools:** `on` with the server's description. MCP servers are still selected on the MCP tab, and a tool the
    server adds later is available as today. A row exists only once someone changes that tool.
- **Backfill:** the up migration inserts `mode='on'` rows for every existing agent with `runtime='openai'`, following
  the "Offered today" table. It reads `repositories_enabled`, `documents_enabled`, `documents_grounded` and
  `ssh_enabled`.
  - `code_graph` is inserted with `repositories_enabled`. It is still offered only when a selected project has a graph.
  - The down migration drops the table.
- **Rows for `run_subagent` and `ask_agent:*`** are always saved with `subagent_allowed=false`; the API sets it, and
  the check is the backstop.
- **Agent revision:** saving tools bumps `ka.agents.revision`, using the same optimistic check as the profile update.

### Configuration snapshot and versions

- **`AgentConfig` gains a `tools` field:**
  ```json
  {"items": [{"tool": "read_file", "group": "code", "mode": "on", "usage": null, "subagent_allowed": true}, ...]}
  ```
  `get_agent_config` loads it from `ka.agent_tools`. Catalog tools without a row are listed with `mode: "off"`.
- **Versioned:** the field is part of the snapshot. Activating a version applies its tools.
- **Older versions:** versions saved before 0078 have no `tools`. `config.setdefault('tools', implied_tools(config))`
  derives them with the same rule as the backfill, as `resources` defaults to `fixed`.
- **The version diff and Show config** list the tools.
- **Claude Code agents:** `tools` is empty, and `claude_code_tools` stays their tool list.

### Enforcement (native)

1. `agents.get_tools` is replaced by `catalog.effective_tools(config, ssh_servers)`. It returns
   `[(spec, usage)]` for every catalog tool whose mode is `on`, or `per_run` with the tool listed in
   `config['run_tools']`, and whose `needs` is met:
   - `files`: code is on, or documents are on and not grounded;
   - `code_graph`: at least one graph;
   - `ssh`: at least one usable server;
   - …
   `create_agent` turns each entry into a tool with `sdk.function_tool`.
2. **MCP:** `create_agent` passes each `MCPServerStreamableHttp` a
   `tool_filter=create_static_tool_filter(blocked_tool_names=[…])`. It blocks the server's tools whose mode is `off`,
   or `per_run` and not given. MCP usage overrides are appended to the prompt under "Tool notes"
   (`- <server>/<tool>: <usage>`), because MCP descriptions come from the server.
3. **The run context:**
   - `context['tools']` holds the effective tool names (the adapter's backstop);
   - `shell_enabled` becomes `'shell_run' in context['tools']`.
4. **The existing per-run narrowing does not change.** Tools still reach only the resources the configuration lists.

### Rules when saving

| Rule | Behaviour |
| --- | --- |
| Documents grounded and `report_not_covered` not `on` | 400 "Grounded answers need report_not_covered." |
| Unknown catalog tool, MCP tool of a server the agent has not selected, or `ask_agent:` target that is not a native agent | 400 naming it |
| A capability switched on for the first time while that group has no tool `on` | The group's default tools are switched on in the same save, by the backfill rule |
| A capability switched off | Its tools keep their rows and are shown as "Needs code projects" (or documents, or servers); they are not offered |

### API

- `GET /api/tools`: the catalog. For each tool: `name`, `group`, `label`, `summary`, `usage` and `needs`.
- `GET /api/agents/{agent_id}/tools`: the agent's tools, grouped. Each one has:
  - `tool`, `label`, `group`, `mode`, `usage` (or null) and `default_usage`;
  - `subagent_allowed`;
  - `available`, with a `reason` such as "Needs code projects";
  - MCP tools of the selected servers, from `ka.mcp_servers.tools`, labelled `<server> / <tool>`;
  - the `ask_agent:` entries once feature 6 ships;
  - the agent's `revision`.
- `PUT /api/agents/{agent_id}/tools`: the body is
  `{"expected_revision": n, "tools": [{"tool", "mode", "usage", "subagent_allowed"}]}`. It replaces the agent's rows
  and returns the GET shape. A revision mismatch returns 409.
- **The All agents listing** (`GET /api/agents`) gains `tools_summary`, for example:
  - `{"on": 7, "per_run": 1, "groups": {"code": 6, "documents": 0, "servers": 1, "mcp": 0, "agents": 0}}`;
  - for Claude Code agents, the `claude_code_tools` list.

### Screens

- **Tools tab** on every agent, after General. It is added to `sections` in `AgentDetailView`.
  - **Native agents:**
    - Groups: **Code**, **Documents**, **Servers**, **MCP** and **Agents**.
    - Each row has the tool name, its one-line summary and a switch.
    - When the tool is off, a checkbox reads "Allow per run". It is shown once feature 5 ships.
    - **Edit usage** opens a textarea with the current text. **Reset** returns it to the built-in text.
    - A badge "Edited" marks changed text.
    - A row whose needs are unmet is greyed out with its reason and a link to the capability tab.
    - Tools are saved with one **Save tools** button, and the "edits the working configuration" note applies as on
      the other tabs.
  - **Claude Code agents:** the four Claude Code tool switches move here from the General tab, with the same
    `claude_code_tools` column. There are no usage instructions and no other groups.
- **New agent wizard:** a **Tools** step after the capabilities. It is prefilled by the backfill rule and shows the
  same switches. With feature 3 it also shows run_subagent and the tools a subagent may be given.
- **All agents list:** a **Tools** column, for example "7 tools · Code 6 · Servers 1" or "+1 per run". For Claude Code
  agents it shows "Read, Grep, Glob, Bash".
- **Capability tabs:** one line of help: "Tools for these projects are chosen on the Tools tab."

## Feature 3 — `run_subagent`

### Configuration

- **The switch:** `run_subagent` is a catalog tool in the **Agents** group. It is off by default, also in the backfill
  and the wizard, and only native agents have it.
- **What a subagent may be given:** when the tool is on, the Tools tab and the wizard show
  **"A subagent may use"**, one checkbox per tool of the agent (`subagent_allowed`).
  - By default every tool except `run_subagent` is allowed.
  - `ask_agent:*` is never allowed, by the database check.
  - The tool is versioned like the other tools.
- **Usage instructions:** they are editable like any tool's. The built-in text is:
  > Start a helper for a separate, well-defined part of your work, such as exploring one project or checking one
  > question. Give it a complete task, because it does not see this conversation. Give it the fewest tools and
  > resources it needs. Helpers may run in parallel. You receive their final answer as the tool result; verify
  > important claims.

### Tool

- **Signature:** `run_subagent(task: str, tools: list[str], resources: {code_project_ids, collection_ids, server_ids, service_ids} | None)`.
- **`tools`:** it must be non-empty. Each name must be in the parent's effective tools for this run and have
  `subagent_allowed`. MCP tools are named as the model sees them.
- **`resources`:** each id must be among the parent's current resource lists, which are its narrowed run resources
  for per-run agents. `None` gives the subagent the parent's current lists. These are plain subset checks on the
  parent's config, with no database lookups.

### Run (`knowledge_agents/subagents.py`)

- **The sub-config** is a deep copy of the parent's effective config with these changes:
  - the resource lists are narrowed to `resources`, as `run_resources.resolve` narrows them;
  - `tools` lists only the given tools as `on`;
  - `system_prompt.content` is the built-in prompt `knowledge_agents/prompts/subagent.md`, given below;
  - `instructions` is empty and skills are off;
  - the model stays the parent's model;
  - `record` is removed.
- **The subagent prompt** (`subagent.md`):
  > You are a helper started by another agent for one task. Work only on that task with the tools and resources you
  > were given. Read before you conclude; cite evidence as file:line, document path or command. If the task cannot
  > be done with what you have, say what is missing. Finish with a concise answer for the agent that started you.
- **Credentials:** only those the sub-config references (its model, its servers' keys and its MCP tokens), taken
  from the parent's already decrypted map. No new decryption happens, and redaction uses the parent's secrets list.
- **Running it:** `create_agent(sub_config, …)`, then `Runner.run(…, context=sub_context, hooks=SubagentHooks(...))`.
  - `max_turns` and `turn_timeout_seconds` follow the parent's.
  - The subagent shares the parent's scratch workspace.
  - It returns the subagent's final output, redacted, as the tool result.
- **One level:** the sub-config never contains `run_subagent` or `ask_agent`, by the database check and in code.
- **In parallel:** the SDK runs one response's function calls concurrently. An `asyncio.Semaphore` in the parent
  context limits subagents to `AGENT_MAX_SUBAGENTS` (default 4) per run; further calls wait.
- **Stop:**
  - Cancelling the parent's task cancels the awaiting tool coroutine and with it the subagent's `Runner.run`.
  - `SubagentHooks.on_tool_start` and the model-call filter raise `CancelledError` once the parent's `SessionEvents`
    is closed, as for the parent.
- **Recording:** `SubagentHooks` forwards every event to the parent's recorder and hooks with
  `payload.parent_call_id = <run_subagent call_id>` and `payload.agent = 'subagent'`.
  - The kinds stay the existing ones (`instructions`, `reasoning`, `message`, `tool_call`, `tool_result`, `usage`),
    so no check changes.
  - Usage events go through the parent's `record_usage`, so the session totals include subagents. In Test chats,
    `context.usage` adds them.

### Screens

The session page, the run record and the Test activity show the events with a `parent_call_id` as a **collapsible
block** under the `run_subagent` call: "Subagent · 14 tool calls · 23k tokens". It is collapsed by default and
expands to the same event list.

## Feature 4 — Built-in agents and Code reader

### Data model

The migration is `0079_builtin_agents`.

```sql
CREATE TABLE ka.builtin_agents (
    key            text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9-]{1,60}$'),  -- 'code-reader'
    agent_id       text UNIQUE REFERENCES ka.agents(id) ON DELETE SET NULL, -- NULL: the user deleted it
    template_hash  text NOT NULL,  -- sha256 of the template last installed or applied
    fields_hash    text NOT NULL,  -- sha256 of the agent's template fields right after that
    installed_at   timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
```

- **Templates** live in the repo under
  `services/admin-api/src/knowledge_admin/builtin_agents/<key>/agent.json` and `prompt.md`.
- **The template fields** are: name, description, `when_to_use`, `runtime`, `resources`, the capability switches with
  "all" selections, the tools (mode, usage and subagent_allowed), instructions and the prompt.

### Install at start-up

`builtin_agents.install(store)` runs in the admin-api lifespan, in one transaction under an advisory lock. For each
template:

| State | Action |
| --- | --- |
| No row | Create the agent, its first prompt version and its tool rows. The model is the first enabled model that `model_fits('openai', …)` accepts, or NULL. Insert the row. If the name is taken, use "<name> (built-in)". |
| Row, `agent_id` NULL | Nothing: a deleted built-in is never reinstalled. |
| Row, same `template_hash` | Nothing. |
| Row, template changed | Nothing automatic. |

- **Update notice:** `GET /api/agents/{id}` gains `builtin: {key, update_available}`. `update_available` is true when
  the template changed and the agent's current `fields_hash` equals the stored one, meaning the user has not edited
  the template fields.
  - The General tab then shows "A newer version of Code reader is available" with an **Update** button.
  - `POST /api/agents/{id}/builtin-update` applies the template: a new prompt version, the fields and the tools. It
    updates both hashes and returns 409 when the agent was edited in the meantime.
- **Edited agents** get no notice. The model choice and `enabled` are not template fields, so picking a model is not
  an edit.

### Code reader

- **Configuration:**
  - native, **per-run resources**, code projects on with "all projects";
  - documents, SSH, MCP, services and skills off;
  - tools `read_file`, `shell_run` and `code_graph` on, everything else off;
  - `run_subagent` off.
- **The prompt** (`prompt.md`, a few hundred tokens):
  > You read code projects to answer questions or to write documentation about them. You are given one or more
  > code projects for this run; they are listed in your resources.
  >
  > Work from evidence. Start with code_graph when a project has a graph: search symbols, then follow callers,
  > callees and context. Use shell_run for rg, ast-grep, git log and small Python scripts. Read focused line ranges
  > with read_file (start_line/end_line), never whole large files.
  >
  > Cite every claim as path:line or path:start-end. Distinguish what you read from what you infer, and say what you
  > could not find. Do not guess from names. When writing documentation, structure it with headings, explain
  > purpose before detail, and keep code excerpts short.

## Feature 5 — Extra tools per run

### Data model

The migration is `0080_run_tools`:

- a new nullable column `run_tools jsonb CHECK (run_tools IS NULL OR jsonb_typeof(run_tools)='array')` on
  `ka.agent_sessions_cc`, `ka.agent_sessions` and `ka.operations_runs`, like `run_resources`;
- the per-run mode is the existing `ka.agent_tools.mode = 'per_run'`.

### Rules

- **Requests:** `RunInput`, the Generate documents `SessionInput` and later `ask_agent` accept
  `tools: list[str] | None`, with at most 50 names.
- **Validation:** `run_tools.resolve(config, requested)` is called next to `run_resources.resolve`.
  - Each name must be an agent tool with mode `per_run`. Otherwise the run is refused with 400 "Tool <name> is not
    allowed per run for this agent".
  - A name already `on` is accepted and changes nothing.
  - The effective config gains `run_tools`.
- **Fixed and per-run agents** both accept per-run tools. This is independent of resources.
- **Pinned:** the choice is fixed for a conversation or session, as the resources are. Changing it later returns 409.
- **Subagents** can be given a per-run tool only when it is on for the parent's run and `subagent_allowed`.

### API and screens

- `GET /api/agents/{id}/run-resources` gains `tools: [{tool, label, group}]` for the per-run tools.
- The **Test tab** and **Generate documents** show **Tools for this run**, a multi-select next to the resources
  picker. It appears only when the agent has per-run tools.
- **Show config** and the session page list the run's extra tools.

## Feature 6 — `ask_agent`

### Configuration

- **The Agents group** lists every other native, enabled agent as `ask_agent:<agent id>`. Each is off by default and
  shows the agent's `when_to_use`. Claude Code agents are listed greyed out: "Claude Code agents cannot be asked".
- **Model-facing tool:** one `ask_agent` tool is offered when at least one callee is on. Its parameter `agent` is an
  enum of the callee ids, and its description lists each callee's name and `when_to_use`.
- **Switching a callee on is the administrator's grant.** A **fixed** callee uses its own resources and credentials.

### Tool

- **Signature:** `ask_agent(agent: str, question: str, resources: {...} | None)`.
- **Loading the callee:** `get_agent_config(callee)` reads its active version. A disabled or missing callee, or a
  missing model, is a tool error the model sees.
- **Per-run callee:** `resources` must be a subset of the caller's current resource lists, which means no escalation.
  `run_resources.resolve(conn, project, callee_config, RunResources(...))` then checks them against the callee's
  allowed selection. `None` passes nothing, and the callee's tools say "No … was given for this run".
- **Fixed callee:** passing `resources` is a tool error, "This agent uses fixed resources".
- **The callee run:**
  - The callee's credentials come from `load_credentials`, are added to the parent's secrets list and are cleared
    when the call ends.
  - It runs with its own prompt, tools and model through `agent(...)` with `stream=False`.
  - Its `ask_agent` tools are removed, so it cannot ask in turn. It may still use `run_subagent` if it has it.
  - Its final output is returned as the tool result.
- **Stop, recording, tokens and the block view** work as for subagents: `parent_call_id`, with
  `payload.agent = <callee name>`. The parent's record and session hold the callee's events and tokens.

## Errors

| Problem | Behaviour |
| --- | --- |
| Saving tools with a stale revision | 409 "The tools changed since you opened them. Reload." |
| Grounded agent without report_not_covered | 400 "Grounded answers need report_not_covered." |
| Unknown tool or invalid ask_agent target when saving | 400 naming it |
| Tool needs a capability that is off | Not offered; Tools tab shows the reason |
| Per-run tool not allowed | 400 "Tool <name> is not allowed per run for this agent" |
| Run tools changed in an existing conversation | 409, as for resources |
| run_subagent with a tool the parent lacks or that is not allowed for subagents | Tool error: "Tool <name> cannot be given to a subagent." |
| run_subagent or ask_agent with a resource the parent lacks | Tool error: "<kind> <name> is not available to you in this run." |
| ask_agent callee disabled, missing or without a model | Tool error naming the agent |
| Resources passed to a fixed callee | Tool error: "This agent uses fixed resources." |
| Built-in update after an edit | 409 "This agent was edited; the update was not applied." |

## Testing

- **Tool usage:** `tool_counts` over several run keys, both agent types and subagent events. The new fields in the
  runs list, run detail, session detail and Test chat responses.
- **Migration 0078:**
  - the backfill matches the "Offered today" table per capability combination, including grounded documents and
    Claude Code agents getting no rows;
  - the checks;
  - up and down.
- **Catalog:**
  - `effective_tools` honours mode, needs and `run_tools`;
  - the adapter refuses a name outside `context['tools']`;
  - the descriptions equal the usage text;
  - the MCP `tool_filter` blocks tools that are off;
  - `get_prompt` no longer contains the moved tool sentences.
- **Config:** the snapshot carries `tools`, the version round-trip keeps it, old versions get the implied tools, and
  the loader fills `code.graphs`.
- **API:** GET and PUT of tools, the save rules, the 409 on revision and the list `tools_summary`.
- **Subagents:**
  - subset checks for tools and resources;
  - no `run_subagent` or `ask_agent` inside;
  - the semaphore limit;
  - Stop cancels a running subagent;
  - events carry `parent_call_id`;
  - tokens reach the session totals;
  - secrets are redacted in subagent events.
- **Built-in agents:**
  - install is idempotent;
  - a deleted agent is not reinstalled;
  - a name collision gets the suffix;
  - the update notice appears only when unedited;
  - update applies the template and returns 409 after an edit;
  - picking a model is not an edit.
- **Per-run tools:** validation, recording on the three tables, pinned per conversation, and fixed agents accepted.
- **ask_agent:** the callee enum, no escalation of resources, the fixed callee rule, the callee's credentials
  redacted, no nested `ask_agent`, Stop.
- **UI:**
  - the Tools tab (switches, Edit usage and Reset, unmet needs, the subagent checklist, Claude Code tools);
  - the wizard Tools step;
  - the list column;
  - the tool usage line and the Agent runs column;
  - the collapsible subagent and callee blocks;
  - the Tools for this run picker.

## Delivery steps (each ships alone, in this order)

1. **Tool selection and tool usage.**
   - The catalog, the SDK adapter and `0078_agent_tools` with its backfill.
   - `tools` in the config and in versions.
   - Enforcement in `create_agent`, including the MCP filter.
   - The loader fills `code.graphs`.
   - The Tools tab: switches and usage instructions, Claude Code tools moved here, the wizard step and the list
     column.
   - Tool counts in run records, sessions, Test chats and Agent runs.
   - No per-run mode, run_subagent or ask_agent yet. The mode column exists, but only `on` and `off` are offered.
2. **run_subagent.** The tool, its switch and the "A subagent may use" checklist on the Tools tab and in the wizard.
   Also the subagent prompt, the parallel limit, Stop, recording, tokens and the collapsible block.
3. **Built-in agents and Code reader.** `0079_builtin_agents`, install at start-up, the update notice and the Code
   reader template. This needs only step 1; doing it third gives a real per-run agent to try subagents and later
   ask_agent with.
4. **Extra tools per run.** "Allow per run" on the Tools tab, `0080_run_tools`, validation, the pickers in the Test
   tab and Generate documents, and recording.
5. **ask_agent.** The callee switches in the Agents group, the tool, the no-escalation checks and the callee block.

## Out of scope

- **Later tools:** `code_overview`, a project summary tool; output limits for `read_file` and `shell_run`.
- New Claude Code features, such as per-tool usage instructions or subagents for Claude Code agents.
- Per-run tools for subagents beyond what the parent has, and `ask_agent` passing per-run tools to the callee.
- More built-in agents (SSH agent, Docs agent) and the documentation builder that calls them.
- A per-run model choice.
- Switching agent framework (the catalog keeps it possible).
