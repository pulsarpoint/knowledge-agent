# Agent Access Phase 1 (Run Side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every run carries one access object (`ka.agent_sessions.access`: resources + roles); one pure module decides
access; every tool checks it; agents list their resources with `get_available_resources`; the resource JSON block
leaves the prompt.

**Architecture:** `knowledge_admin/access.py` holds pure functions over the access object. Run start builds the object
(saved agents from today's agent tables, ad-hoc runs from the pick, children expanded, `content_editor` set for
writing runs) and stores it. Each tool is rewritten as a pure check (arguments + access → `None` or refusal text) plus
the work; one table-driven test runs every tool's check against a set of access objects.

**Tech Stack:** PostgreSQL 17, Python 3.14 / FastAPI / psycopg 3 / openai-agents 0.19 / pytest; React Router 7 /
TypeScript / vitest (Debug panel only).

**Spec:** `docs/superpowers/specs/2026-10-16-agent-access-design.md` (Phase 1 of 3).

## Global Constraints

- **Simple code, no unnecessary abstractions; rules testable in isolation** (pure functions, no DB/framework).
- Work only in `.claude/worktrees/agent-access` (branch `feature/agent-access`). Never `make`, never touch
  `.env`/`.local`, never `git stash`, never touch the running Docker stack, never kill processes you did not start.
  UI checks: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once (never commit it,
  never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims; migrations correct up and down. Next migration: **0112**.
- Exact refusal texts: `This <type> is not available in this run.` (`<type>` in words: server, service, environment,
  code project, collection, document, skill, MCP server); `This agent may not change content (content editor is off).`;
  `This run has no <type>s.`
- Resource types: `server`, `service`, `environment`, `code_project`, `collection`, `document`, `skill`, `mcp_server`.
  Run entries: `{type, id, name, description_short, source}`, `source` = `given` or `via <type> <id>`; a document entry
  may carry `editable` (default false).
- Writing runs get `content_editor: true`: Documents → New, Ask agent on a document (revision), Propose wizard; plain
  resource chats `false`; saved agents in Phase 1: `false` unless the run is one of those writing runs (Phase 2 adds the
  agent switch).
- Phase 1 keeps tool selection as it is (modes/needs stay until Phase 3) and keeps agent storage (Phase 2); memory tools
  are not resource-gated (unchanged).
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check`.

## Review Focus

1. No tool decides access any other way than through `access.py` (grep `allowed(`, `ACCESS`, `require_given`,
   `run_resources`, `chosen_resources`, `document_access` in capabilities).
2. Narrowing never widens: a picked resource outside the saved agent's list is refused at start; a role is only ever
   switched off.
3. A plain resource chat cannot propose, save or edit; a Documents → New run can create its document; an Ask agent run
   can edit its document; a Propose wizard run can propose.
4. A child added via a parent is usable by tools and shown with `via …` in `get_available_resources` and Debug.
5. The table-driven test covers every tool in the catalog (it fails when a new tool has no row).

---

### Task 1: `access.py` — the rules

**Files:** Create `services/admin-api/src/knowledge_admin/access.py`, `services/admin-api/tests/test_access.py`.

**Produces (exact):**
```python
TYPES = ('server', 'service', 'environment', 'code_project', 'collection', 'document', 'skill', 'mcp_server')
TYPE_WORDS = {'server': 'server', 'service': 'service', 'environment': 'environment', 'code_project': 'code project',
              'collection': 'collection', 'document': 'document', 'skill': 'skill', 'mcp_server': 'MCP server'}
NOT_AVAILABLE = 'This {type} is not available in this run.'
NOT_EDITOR = 'This agent may not change content (content editor is off).'
NONE_OF = 'This run has no {type}s.'

def entry(access: dict, type: str, id: str) -> dict | None
def can_use(access: dict, type: str, id: str) -> bool
def can_write_document(access: dict, document_id: str) -> bool
def can_propose(access: dict, type: str, id: str) -> bool
def can_create_document(access: dict, built_for: list[tuple[str, str]]) -> bool
def has_any(access: dict, type: str) -> bool
def refuse_use(type: str) -> str          # NOT_AVAILABLE with TYPE_WORDS
def refuse_none(type: str) -> str         # NONE_OF with TYPE_WORDS
def narrow(agent_access: dict, picked: list[dict] | None, roles_off: list[str] | None = None) -> dict
    # picked = None → the agent's list; otherwise each picked {type,id} must be in the agent's list (ValueError naming it);
    # roles only switched off
def expand(access: dict, children: dict[tuple[str, str], list[dict]]) -> dict
    # children[(type,id)] = [{type,id,name,description_short}] supplied by the caller; adds them with source 'via <type> <id>'
    # unless already present; keeps given entries' source 'given'
def listing(access: dict, type: str | None = None) -> list[dict]   # what get_available_resources returns
```
- [ ] Step 1: tests first — every function, including: ids compared as strings; `editable` default false;
  `can_write_document` false without `content_editor` or without `editable`; `can_create_document` false when any
  built-for is missing; `narrow` refuses widening and never switches a role on; `expand` marks sources and does not
  duplicate; `listing` filters by type and keeps order (given first, then children); refusal texts exact.
- [ ] Step 2: run — fail. Step 3: implement (plain dict/list code, no classes). Step 4: pass. Step 5: commit
  `feat(access): pure access rules over the run's access object`.

### Task 2: Run start builds and stores the access object; get_available_resources; prompt and Debug

**Files:** Create `db/migrations/0112_session_access.up.sql`/`.down.sql` (`ALTER TABLE ka.agent_sessions ADD COLUMN
access jsonb NOT NULL DEFAULT '{"resources": [], "roles": {"content_editor": false}}'`; existing rows get an object built
from their `run_resources`/`chosen_resources`/`document_access` where present, else empty); modify
`sessions/start.py` (build the access object for every run: saved agents from today's pinned config's resource sections
— servers, services, code projects, collections, documents, skills, MCP servers — narrowed by the request; ad-hoc runs
from the pick; children loaded with the existing `run_resources.bring`/`pin_services` queries and passed to
`access.expand`; `content_editor` per Global Constraints; store in `agent_sessions.access` and also put it in the pinned
config as `config['access']` so tools read it from the run context), `agents/runner.py` (`get_prompt` drops the resource
JSON block and the "This run was given only…" sentence; keeps instructions, the `Resource:` line and anything that is not
the resource block), a new tool `get_available_resources(type?)` in `capabilities/inventory.py` (returns
`access.listing(...)`), `tools/tools.json` (its entry, no need), `sessions/api.py` debug/preview (return `access`),
`services/admin-ui/app/features/sessions/DebugPanel.tsx` (show resources with source and roles), tests
`tests/test_session_access.py`, the debug/preview tests, prompt snapshot tests that contained the resource block.

- [ ] Step 1: failing tests — saved-agent run and ad-hoc server/service/environment runs store the expected access
  object (children `via …`); narrowing refused when widening (400 with the ValueError text); writing runs get
  `content_editor: true`, a plain chat `false`; `get_available_resources` returns the listing and filters by type;
  the prompt no longer contains the resource block; Debug/preview return `access`; UI Debug shows sources and roles.
- [ ] Step 2: run — fail. Step 3: implement. Step 4: focused files, full backend suite, UI checks. Step 5: commit
  `feat(sessions): every run stores its access object; get_available_resources; resources leave the prompt`.

### Task 3: Tools rewritten — check + work, every check through `access.py`, the table-driven test

**Files:** every tool module in `services/admin-api/src/knowledge_admin/capabilities/` (`files.py`, `code_search.py`,
`ast_grep.py`, `code_graph.py`, `documents.py`, `document_tools.py`, `inventory.py`, `ssh.py`, `discovery.py`,
`subagents.py`, `memory.py` (unchanged access, but give it a check returning `None` so the table covers it),
`shell/client.py`, `mcp.py`); `capabilities/context.py` (remove `ACCESS`, `allowed`, `require_given` and the section
readers once nothing uses them; keep `RunContext`); create `services/admin-api/tests/test_tool_access_matrix.py`.

**Shape of every tool (exact pattern):**
```python
def check_ssh_run(access: dict, server_id: str, **_) -> str | None:
    """None when allowed, else the refusal text."""
    if not access_rules.has_any(access, 'server'):
        return access_rules.refuse_none('server')
    if not access_rules.can_use(access, 'server', server_id):
        return access_rules.refuse_use('server')
    return None

@tool
async def ssh_run(context: RunContext, server_id: ..., command: ..., ...) -> str:
    """…unchanged docstring…"""
    if refusal := check_ssh_run(context.config['access'], server_id=server_id):
        return refusal
    return await _run_ssh(context, server_id, command, ...)   # the work, unchanged behaviour
```
- One `check_<tool>` per tool, next to the tool, pure (arguments + access → `None` | refusal). Writes:
  `save_document`/`edit_document` → `can_write_document`; a new document → `can_create_document`;
  `propose_service`/`propose_document` → `can_propose`; `run_subagent` → the requested resources must be usable
  (`can_use`) and it narrows with `access.narrow`; `load_skill` arrives in Phase 3.
- Behaviour beyond access unchanged (same results, same docstrings and schemas — the tool schema snapshot must not
  change except for the new `get_available_resources` from Task 2).
- **Table-driven test** (`test_tool_access_matrix.py`): a set of access objects (empty; one server; server with its
  services via; read-only document; editable document without and with `content_editor`; a resource not in the list) ×
  every tool in `CATALOG` (rows built from the catalog so a new tool without a row fails — Review Focus 5) → expected
  `None` or the exact refusal. No database, no agent run.
- [ ] Step 1: write the matrix test first (it fails: no `check_*` functions). Step 2: run — fail. Step 3: rewrite the
  tools one module at a time, re-running that module's existing tests. Step 4: matrix test, all capability/tool tests,
  full backend suite. Step 5: commit `refactor(tools): every tool checks access through access.py; access matrix test`.
