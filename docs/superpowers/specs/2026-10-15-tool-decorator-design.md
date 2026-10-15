# Tools as decorated functions; tool attributes in a fixture and `ka.tools`

Date: 2026-10-15. Status: v2 — tool attributes moved to a fixture stored in the database (2026-10-15), for review.
Builds on: the tool catalog (0078 agent tools), resource runs (0106: `get_resource`, fixed tool schemas).

## Why

`tools/catalog.py` is ~880 lines for 18 tools. Each tool is written four times: a Pydantic `Arguments` class, a
`USAGE` string constant, a nine-field positional `ToolSpec(...)` entry and a `_call(...)` wrapper — while the
capability function it calls (`capabilities/files.list_files`, `ssh.ssh_run`, …) already has exactly the same
arguments. Adding or changing a tool means editing three places that must agree.

Decided with the person using it:

1. A tool is a plain function with a decorator; its signature is the argument schema and its docstring is the text
   the model reads. That is all the code says about a tool.
2. Our own decorator, not the Agents SDK's `@function_tool`: `capabilities/` and the catalog stay free of the SDK, and
   `tools/sdk.py` stays the one binding (per-agent usage text, the not-offered backstop, redaction, trace spans,
   `TOOL_CALL_ID`).
3. The attributes used to build agents — group, label, summary, needs, order — are not code: they are rows of
   `ka.tools`, filled from one fixture file. The tools are fixed for now and these change rarely; a change is an edit
   of the fixture.
4. Tool schemas are the same in every run (already true since 0106); this change must not alter any schema or
   description the model sees.

## The decorator

New module `knowledge_admin/tools/registry.py` (imports nothing from `capabilities/`, so capability modules can import
it without a cycle):

```python
@dataclass(frozen=True)
class Tool:
    name: str
    usage: str
    arguments: type[BaseModel]
    run: Callable[[RunContext, BaseModel], Awaitable[str]]

def tool(fn=None, *, name: str | None = None, usage: str | None = None, arguments: type[BaseModel] | None = None):
    ...
```

- `@tool` (or `@tool(...)` with an override) returns the function **unchanged** (still directly callable and testable)
  and records a `Tool` in a module-level `REGISTRY` (a duplicate name raises at import).
- `name`: the function name unless given.
- `usage`: the function's docstring (`inspect.cleandoc`), or `usage=` when given; a tool with neither raises at
  import.
- `arguments`: built from the signature with `pydantic.create_model(<CamelCase of the name>, __config__=ConfigDict(
  extra='forbid'), ...)` — `list_files` → `ListFiles`, today's class names, so schema titles do not change. The first
  parameter (`context`) is skipped; every other parameter becomes a field with its annotation and default. Field
  descriptions and constraints come from `Annotated[..., Field(...)]` in the signature. `**kwargs` parameters are not
  allowed unless `arguments=` is given.
- `arguments=` overrides the generated model for the tools whose model needs more than a signature can say (nested
  models with validators, inherited config): `propose_service`, `propose_document`, `save_document`, `edit_document`.
  Those models (`Proposal`, `DeploymentProposal`, `ProposeService`, `ProposeDocument`, `SaveDocument`,
  `EditDocument`) move next to their functions (`capabilities/discovery.py`, `capabilities/document_tools.py`).
- `run`: `await fn(context, **arguments.model_dump())` — today's `_call`, built by the decorator.

Example (`capabilities/ssh.py`):

```python
@tool
async def ssh_run(
    context: RunContext,
    server_id: Annotated[str, Field(description="Exact server ID from the agent's configured SSH server list.")],
    command: Annotated[str, Field(description='Shell command to run remotely; check the returned exit_code.')],
    as_root: Annotated[bool, Field(description='Request noninteractive sudo to root for this command.')] = False,
    timeout_seconds: Annotated[int, Field(description='Remote command time limit, from 1 to 300 seconds.')] = 60,
) -> str:
    """Execute a command on a server selected for this agent and return its output.

    Read the server with get_resource first and follow its instructions. ...
    """
```

Docstrings of decorated functions are model-facing text (today's `USAGE` constants, verbatim). Developer notes that
were in those docstrings move to comments above the function.

## Where tools live

- Decorated in place: `files.list_files`, `read_file`, `search_files`; `shell.client.shell_run`;
  `code_search.code_search`; `ast_grep.ast_grep`; `code_graph.code_graph`; `documents.search_documents`,
  `read_document`, `report_not_covered`; `ssh.ssh_run`; `inventory.get_resource`; `document_tools.get_document`,
  `save_document`, `edit_document`; `discovery.propose_service`, `propose_document`.
- `run_subagent` stays a small decorated function in `tools/catalog.py` with its lazy import (the subagent runtime
  imports the catalog).
- Text shared by several tools (today's `INVENTORY` paragraph) stays a constant: a docstring cannot be built from
  one, so `get_resource` and `get_document` pass `usage=` instead of a docstring. Every other tool uses its
  docstring.

## Tool attributes: the fixture and `ka.tools`

- **Fixture** `services/admin-api/src/knowledge_admin/tools/tools.json` (packaged with the code): a list, in the order
  the Tools tab and `effective_tools` use (today's), of
  `{"name", "group", "label", "summary", "needs"}` — the values of today's `ToolSpec` entries. `needs` is one of
  today's `Need` values or `null`.
- **Table** (migration 0107):

  ```sql
  CREATE TABLE ka.tools (
      name       text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]{0,63}$'),
      position   integer NOT NULL UNIQUE,
      tool_group text NOT NULL CHECK (tool_group IN ('code','documents','servers','inventory','agents')),
      label      text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80),
      summary    text NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 300),
      needs      text CHECK (needs IN ('files','code','code_graph','documents','grounded','ssh',
                                       'server_proposals','resource_proposals','editable_document','resources'))
  );
  ```

  The migration creates it empty; down drops it. `ka.agent_tools.tool` is not a foreign key (its rows also name MCP
  tools `mcp:<server>:<tool>`).
- **Sync.** `tools/fixture.py` `sync_tools(store)` applies the fixture in one transaction: upsert every entry
  (`position` = its index), delete rows whose name is not in the fixture. It runs at admin-api start (the `lifespan`
  in `app.py`, before serving; a failure stops the start) and is idempotent, so restarting after a fixture edit is the
  update. agent-session-worker does not sync.
- **In the code**, `tools/catalog.py` reads the same fixture file at import (not the table — no database needed at
  import, in tests, or in the worker) and builds `CATALOG: dict[str, ToolSpec]` in fixture order, where
  `ToolSpec` = the registered `Tool` plus `group`, `label`, `summary`, `needs`. `ToolSpec` keeps today's field names,
  so the 23 places that read `CATALOG` do not change. The table is the same data for SQL readers and for anyone
  looking at the database; it can never differ from the fixture after a start.
- **Checks at import** (fail the app start and the tests): every fixture entry has a registered tool, every registered
  tool has a fixture entry, names are unique, `group` and `needs` are known values.

## What else `tools/catalog.py` keeps

- Unchanged: `Need`, `needs_met`, `has_resources`, `NEED_REASONS`, `NEED_TABS`, `RUN_ONLY_NEEDS`, `PROPOSAL_STARTS`,
  `DOCUMENT_EDIT_TOOLS`, `PROPOSAL_TOOLS`, `SUBAGENT`, `item`, `effective_tools`, the `mcp_*` helpers,
  `catalog_listing`, `CAPABILITY_NEEDS`, `capability_tools`, `enabled_capability_tools`, `tools_summary`.
- Removed: every `Arguments` subclass and `USAGE` constant now in the functions, the `ToolSpec(...)` entries, `_call`,
  `ToolSpec.default_on` and the `_never` rule that only fed it (the `_files`/`_code`/`_documents`/`_ssh`/`_grounded`
  rules stay, `needs_met` uses them), `implied_tools`, and `tools_of`'s fallback: `tools_of(config)` becomes
  `config['tools']` (every loaded configuration has it, `knowledge_agents/config.py`; a session pinned before 0078 is
  test data). Callers of `tools_of` (`config.py`, `subagents.py`) read `config['tools']` directly and `tools_of` is
  removed.

## `tools/sdk.py`

Unchanged except the import: `ToolSpec` comes from `tools/catalog.py` as before. `function_tool(spec, usage)` still
builds the `FunctionTool` from `spec.arguments` and `spec.run`, with the per-agent usage text.

## Error handling

- Import-time errors (fail the app start and the tests, never a run): duplicate tool name, missing docstring/usage,
  `**kwargs` without `arguments=`, a parameter without an annotation, fixture and registry disagreeing, an unknown
  group or need in the fixture.
- Start-up: `sync_tools` failing (database unavailable, a CHECK violated) stops the admin-api start with the error.
- Run time: unchanged — argument validation errors and tool errors go back to the model through `_function_tool`.

## Testing

- **Golden schemas (the main check).** Before converting anything, a test writes every tool's `name`, description
  (`usage`), strict JSON schema (`ensure_strict_json_schema(arguments.model_json_schema())`), `group`, `label`,
  `summary` and `needs`, in catalog order, to `tests/fixtures/tool_schemas.json` from today's catalog, committed
  first. After the conversion the same test must produce identical JSON, titles included.
- Decorator unit tests: the function stays callable unchanged; schema from `Annotated` fields and defaults; `extra`
  forbidden; `arguments=` override; `usage=` override; each import-time error above.
- Fixture: `sync_tools` on an empty table inserts every entry in order; after editing a label and removing an entry in
  a test fixture, a second sync updates the one and deletes the other; running it twice changes nothing; a fixture
  with an unknown need fails the CHECK and rolls back. Catalog checks: a fixture entry without a function and a
  function without an entry both raise.
- Migration 0107 up/down round trip.
- Existing `test_tool_catalog.py`, `test_document_tool_binding.py`, `test_tool_enforcement.py`, `test_tool_counts.py`
  pass (tests that only checked `implied_tools`/`default_on` are removed with them); `catalog_listing()` unchanged.
- Full backend suite.

## Out of scope

- Editing tool attributes in the UI (the fixture is the only writer of `ka.tools`).
- A tool↔resource table.
- Expert tools (`ask_<slug>`) — built per session from `config['experts']`, unchanged.
- MCP tools — unchanged.
- Code projects, collections and skills in the prompt JSON (a later step of the resource-context work).
