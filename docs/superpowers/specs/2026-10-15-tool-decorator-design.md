# Tools as decorated functions

Date: 2026-10-15. Status: for review.
Builds on: the tool catalog (0078 agent tools), resource runs (0106: `get_resource`, fixed tool schemas).

## Why

`tools/catalog.py` is ~880 lines for 18 tools. Each tool is written four times: a Pydantic `Arguments` class, a
`USAGE` string constant, a nine-field positional `ToolSpec(...)` entry and a `_call(...)` wrapper — while the
capability function it calls (`capabilities/files.list_files`, `ssh.ssh_run`, …) already has exactly the same
arguments. Adding or changing a tool means editing three places that must agree.

Decided with the person using it:

1. A tool is a plain function with a decorator; its signature is the argument schema and its docstring is the text
   the model reads.
2. Our own decorator, not the Agents SDK's `@function_tool`: `capabilities/` and the catalog stay free of the SDK, and
   `tools/sdk.py` stays the one binding (per-agent usage text, the not-offered backstop, redaction, trace spans,
   `TOOL_CALL_ID`).
3. Tool schemas are the same in every run (already true since 0106); this change must not alter any schema or
   description the model sees.

## The decorator

New module `knowledge_admin/tools/registry.py` (imports nothing from `capabilities/`, so capability modules can import
it without a cycle):

```python
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

def tool(*, group: Group, label: str, summary: str, needs: Need | None = None,
         name: str | None = None, usage: str | None = None, arguments: type[BaseModel] | None = None):
    ...
```

- `tool(...)` returns the function **unchanged** (still directly callable and testable) and records a `ToolSpec` in a
  module-level `REGISTRY` (a duplicate name raises at import).
- `name`: the function name unless given.
- `usage`: the function's docstring (`inspect.cleandoc`), or `usage=` when given; a tool with neither raises at
  import.
- `arguments`: built from the signature with `pydantic.create_model(<CamelCase of the name>, __config__=ConfigDict(
  extra='forbid'), ...)` — `list_files` → `ListFiles`, today's class names, so schema titles do not change. The first parameter (`context`) is skipped; every other parameter becomes a field with its
  annotation and default. Field descriptions and constraints come from `Annotated[..., Field(...)]` in the signature.
  `**kwargs` parameters are not allowed unless `arguments=` is given.
- `arguments=` overrides the generated model for the tools whose model needs more than a signature can say (nested
  models with validators, inherited config): `propose_service`, `propose_document`, `save_document`, `edit_document`.
  Those models (`Proposal`, `DeploymentProposal`, `ProposeService`, `ProposeDocument`, `SaveDocument`,
  `EditDocument`) move next to their functions (`capabilities/discovery.py`, `capabilities/document_tools.py`).
- `run`: `await fn(context, **arguments.model_dump())` — today's `_call`, built by the decorator.

Example (`capabilities/ssh.py`):

```python
@tool(group='servers', label='SSH', summary='Run a command on a selected server.', needs='ssh')
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

## What `tools/catalog.py` keeps

- `CATALOG: dict[str, ToolSpec]` — built from `REGISTRY` in one explicit `ORDER` tuple of names (the Tools tab and
  `effective_tools` order today's: list_files, read_file, search_files, shell_run, code_search, ast_grep, code_graph,
  search_documents, read_document, report_not_covered, ssh_run, get_resource, get_document, propose_service,
  propose_document, save_document, edit_document, run_subagent). The catalog imports the capability modules so they
  register; a registered tool missing from `ORDER`, or a name in `ORDER` not registered, raises at import.
- Unchanged: `Need`, `needs_met`, `has_resources`, `NEED_REASONS`, `NEED_TABS`, `RUN_ONLY_NEEDS`, `PROPOSAL_STARTS`,
  `DOCUMENT_EDIT_TOOLS`, `PROPOSAL_TOOLS`, `SUBAGENT`, `item`, `effective_tools`, the `mcp_*` helpers,
  `catalog_listing`, `CAPABILITY_NEEDS`, `capability_tools`, `enabled_capability_tools`, `tools_summary`.
- Removed: every `Arguments` subclass and `USAGE` constant now in the functions, `_call`, `ToolSpec.default_on` and the
  `_files`/`_code`/`_documents`/`_ssh`/`_grounded`/`_never` rules that only fed it (the ones `needs_met` uses stay),
  `implied_tools`, and `tools_of`'s fallback: `tools_of(config)` becomes `config['tools']` (every loaded
  configuration has it, `knowledge_agents/config.py`; a session pinned before 0078 is test data). Callers of `tools_of`
  (`config.py`, `subagents.py`) read `config['tools']` directly and `tools_of` is removed.

## `tools/sdk.py`

Unchanged except the import: `ToolSpec` comes from `tools/registry.py`. `function_tool(spec, usage)` still builds the
`FunctionTool` from `spec.arguments` and `spec.run`, with the per-agent usage text.

## Error handling

- Import-time errors (fail the app start and the tests, never a run): duplicate tool name, missing docstring/usage,
  `**kwargs` without `arguments=`, a parameter without an annotation, `ORDER` and `REGISTRY` disagreeing.
- Run time: unchanged — argument validation errors and tool errors go back to the model through `_function_tool`.

## Testing

- **Golden schemas (the main check).** Before converting anything, a test writes every tool's `name`, description
  (`usage`) and strict JSON schema (`ensure_strict_json_schema(arguments.model_json_schema())`) to
  `tests/fixtures/tool_schemas.json` from today's catalog, committed first. After the conversion the same test must
  produce identical JSON, titles included (generated model names are today's class names).
- Decorator unit tests: the function stays callable unchanged; schema from `Annotated` fields and defaults; `extra`
  forbidden; `arguments=` override; `usage=` override; each import-time error above.
- Catalog: `CATALOG` order equals `ORDER`; `catalog_listing()` unchanged; existing `test_tool_catalog.py`,
  `test_document_tool_binding.py`, `test_tool_enforcement.py`, `test_tool_counts.py` pass (tests that only checked
  `implied_tools`/`default_on` are removed with them).
- Full backend suite.

## Out of scope

- Moving `needs` to the database or a tool↔resource table (not pursued).
- Expert tools (`ask_<slug>`) — built per session from `config['experts']`, unchanged.
- MCP tools — unchanged.
- Code projects, collections and skills in the prompt JSON (a later step of the resource-context work).
