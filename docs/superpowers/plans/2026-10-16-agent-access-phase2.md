# Agent Access Phase 2 (Agent Side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A saved agent stores what it may use as one access object (`ka.agents.access`: resources + roles) and its
instructions in `ka.agents.instructions`; every run narrows that object; the per-type tables, per-server overrides,
on/off switches, "all" switches and fixed/per-run modes are gone; the agent editor gets one Resources tab and a Content
editor switch.

**Architecture:** `get_agent_config` builds the pinned config's sections (code projects, collections, servers, services,
skills, MCP servers, documents) from the agent's access references, so tools and Phase 1's run access stay as they are.
Run start narrows every saved agent with `access.narrow` (picked subset or everything). The agent API reads and writes the
access object; the editor edits it.

**Tech Stack:** PostgreSQL 17, Python 3.14 / FastAPI / psycopg 3 / pytest; React Router 7 / TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-16-agent-access-design.md` (Phase 2 of 3). Phase 1 merged 48a5ae3/015cdb8.

## Global Constraints

- **Simple code, no unnecessary abstractions; rules testable in isolation** (decisions in `access.py` as pure functions).
- Work only in `.claude/worktrees/agent-access-2` (branch `feature/agent-access-2`). Never `make`, never touch
  `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack, never kill processes you
  did not start. UI checks: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once
  (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims, no redirects; migrations correct up and down (down restores the schema and
  converts references back; per-server overrides and connection overrides are not restored). Next migrations: **0113**,
  **0114**.
- Saved agent access object (stored): `{"resources": [{"type", "id", "editable"?}], "roles": {"content_editor": bool}}`.
  Types: `server`, `service`, `environment`, `code_project`, `collection`, `document`, `skill`, `mcp_server` (Phase 1's
  `access.TYPES`). Only references are stored; names/descriptions/paths are loaded at run start.
- References to resources that no longer exist (deleted/archived) are skipped at run start and shown as missing in the
  editor; saving drops them. No cascade code in other services.
- Per-agent server instructions, per-server skill/MCP overrides and per-agent SSH connection overrides are dropped (the
  user chose references only); a server's own SSH settings apply.
- "Answer only from documents" (`documents_grounded`), `expert`, `when_to_use`, `model_id`, `system_prompt`, `enabled`,
  `description` stay. `ka.agent_tools` stays until Phase 3.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check`; `cd services/rag && uv run --frozen pytest -q -p no:warnings` and
  `cd packages/llm-settings && uv run --frozen pytest -q -p no:warnings` where touched.

## Review Focus

1. A saved agent run uses exactly the agent's access (or a picked subset); a pick outside it is refused with the
   `access.narrow` text; the content editor role comes from the agent and is never switched on by a run.
2. A reference to a deleted resource neither breaks run start nor the editor.
3. Event handlers can target any enabled agent and run it with its whole access.
4. Experts and subagents narrow from the right object (an expert from its own agent's access).
5. Nothing reads the dropped tables/columns after Task 3 (grep for every dropped name across admin-api, rag,
   llm-settings, admin-ui and docs).

---

### Task 1: Agents store an access object; runs read it

**Files:** `db/migrations/0113_agent_access.up.sql`/`.down.sql`; `knowledge_agents/config.py` (`get_agent_config`);
`sessions/start.py` (`narrow`), `knowledge_agents/run_resources.py` (fixed/per-run branch), `knowledge_agents/experts.py`,
`knowledge_agents/subagents.py`, `events/handlers_api.py`, `events/processor.py`; tests.

- Migration 0113 adds `ka.agents.access jsonb NOT NULL DEFAULT '{"resources": [], "roles": {"content_editor": false}}'`
  and `ka.agents.instructions text NOT NULL DEFAULT ''`, and converts each agent: resources from `agent_repositories`,
  `agent_collections`, `agent_services`, `operations_servers`, `skills_agents`, the agent-level
  `agent_config_mcp_servers`, with `all_collections`/`all_repositories` expanded to every current collection/code project
  of the project and a switched-off kind left out; instructions from the agent-level `agent_configs` row. Old tables stay
  until Task 3. Down drops the two columns.
- `get_agent_config` reads `access` and `instructions` from `ka.agents` and loads the referenced items into the same
  sections as today (`code.projects`, `documents.collections`, `ssh.servers`, `services.items`, `skills.items`,
  `mcp.servers`, plus `document_access` for document entries with `editable`); a section is enabled when it has items;
  missing references are skipped. Per-server overrides are not read. It also returns `access` (the stored object).
- Run start: every saved agent is narrowed with `access.narrow(agent_access, picked)` — `picked` from the request's
  resources (None when none given); widening is a 400 with the `access.narrow` text. Environments and documents may now
  be picked for saved agents if they are in the agent's access (NO_ENVIRONMENTS/NO_DOCUMENTS go). The run's
  `content_editor` comes from the agent's role (writing-run rules from Phase 1 stay for ad-hoc runs only). The
  fixed/per-run branch in `resolve` is no longer used for saved agents.
- Experts: an expert narrows its own agent's access with what the model passed; what it `accepts` = the types in its
  access. Subagents: unchanged (they already narrow the parent's run access).
- Event handlers: any enabled agent (the `resources == 'fixed'` checks go); runs use the whole access.
- [ ] Step 1: failing tests — migration converts a fixed agent, a per-run agent, an "all collections" agent, a switched-off
  kind and instructions (up and down); a saved agent run's access equals the agent's (with children expanded); a picked
  subset narrows; a pick outside is refused with the exact text; content editor follows the agent; a deleted referenced
  resource is skipped; a handler can target a formerly per-run agent; an expert narrows its own access.
- [ ] Step 2: run — fail. Step 3: implement. Step 4: focused, full backend suite. Step 5: commit
  `feat(agents): a saved agent's access object drives its runs`.

### Task 2: Agent API, wizard, clones and reviews write the access object

**Files:** `agents/profiles_api.py`, `agents/wizard.py`, `agents/clones.py`, `agents/reviews.py`,
`servers/operations_api.py` + `servers/operations_store.py` (agent parts), `servers/service_api.py` (credential check),
`agents/tools_api.py` (switch reads), `knowledge_agents/experts.py` (`accepts`), tests.

- `GET /agents/{id}`: returns `access` with each reference resolved to `{type, id, name, description_short, editable?,
  missing?}`, and `instructions`. `PUT /agents/{id}`: takes `access` (references; each id must exist in the project and
  be of a known type, missing ones dropped) and `instructions`; the per-type id lists, switches, `all_*` and `resources`
  fields go. `GET /agents/{id}/options`: every resource of the project grouped by type (`{type, id, name,
  description_short}`), for the picker. `GET /agents`: counts per type from `access`. `DELETE` no longer touches the old
  tables.
- `GET /agents/{id}/run-resources` (Test tab, chat settings): the agent's access list, for picking a subset.
- Wizard: `AgentProposal` proposes `access` (references + `content_editor`) and `instructions`; `POST` writes them;
  designer prompt/schema updated.
- Clones copy and apply `access` and `instructions`. Reviews write `instructions` (agent level only; server overrides
  gone).
- Remove the agent parts of operations: `/operations/settings`, `/operations/configs/*`,
  `/agents/{id}/servers/{id}/ssh*` and their store code; server-level `/servers/{id}/ssh` stays. The credential check in
  `service_api.py` uses only the server's own SSH settings.
- `tools_api.py` and experts read kinds from `access` instead of the switches.
- [ ] Step 1: failing tests for each endpoint above (including a missing reference returned with `missing: true` and
  dropped on save, an unknown type refused, a wizard-created agent's access, a clone round trip, a review writing
  instructions, the removed endpoints returning 404). Step 2: fail. Step 3: implement. Step 4: focused, full backend
  suite. Step 5: commit `feat(agents): the agent API reads and writes the access object`.

### Task 3: Drop the old storage

**Files:** `db/migrations/0114_drop_agent_resource_tables.up.sql`/`.down.sql`; every remaining reader:
`packages/llm-settings/src/knowledge_llm/profiles.py`, `services/rag` (`workspace_api.py` collection delete),
`docs/database.md`, `docs/rag.md`, `docs/skills.md`, `docs/agent-profiles.md`, `services/admin-api/README.md`; tests and
migration tests that used the old schema.

- 0114 drops `agent_repositories`, `agent_collections`, `agent_services`, `skills_agents`, `operations_servers`,
  `operations_settings`, `agent_configs`, `agent_config_skills`, `agent_config_mcp_servers`, `agent_server_connections`,
  and the `ka.agents` columns `documents_enabled`, `repositories_enabled`, `ssh_enabled`, `skills_enabled`,
  `mcp_enabled`, `services_enabled`, `all_collections`, `all_repositories`, `resources`. Down recreates them (schema as in
  0113's starting point) and converts `access`/`instructions` back (references and agent-level instructions only).
- llm-settings and rag read/write nothing from the dropped tables (rag's collection delete no longer touches agents).
- Docs describe the access object instead of the tables.
- [ ] Step 1: a test that the dropped names are gone (information_schema) and the down migration restores them with the
  converted references. Step 2: fail. Step 3: implement; grep every dropped name across the repo (Review Focus 5).
  Step 4: admin-api, rag, llm-settings suites. Step 5: commit `refactor(agents): drop the per-type resource tables,
  overrides and switches`.

### Task 4: Agent editor — Resources tab and Content editor switch

**Files:** `services/admin-ui/app/features/agents/` (`AgentDetailView.tsx`, `AgentProfileSettings.tsx`, a new
`AgentResources.tsx`, `AgentWizard.tsx`, `wizard.ts`, `AgentsView.tsx`, `api.ts`), `features/servers/OperationsView.tsx`
and `operations.ts` (agent parts removed), `features/sessions/agentSettings.ts` (Test tab / chat settings pick from the
agent's access), tests under `components/__tests__` and `lib/__tests__`.

- Tabs become `general, resources, tools, test` (skills, documents, git, ssh, mcp, services go; `?view=` for a removed
  tab falls back to general — no redirect code beyond the default).
- **Resources tab** (`AgentResources.tsx`): the agent's resources grouped by type with name and short description; an
  "Add" picker per type from `/options` (with "Select all" for a type); remove; a document entry has an Editable toggle;
  a missing reference shows "missing" and is dropped on save; one Save writes `PUT /agents/{id}` with `access`.
- **General tab**: a Content editor switch (`roles.content_editor`, default off, with one line of help: "Lets this agent
  propose services and documents and edit editable documents."); an Instructions text area (`instructions`); the
  resources-mode select and the operations instructions block go.
- Wizard proposes and shows resources and the Content editor switch; list page shows counts per type.
- Test tab / chat settings: pick a subset of the agent's resources (all selected by default).
- [ ] Step 1: failing vitest tests — tabs; add/remove/select-all/editable/missing on the Resources tab and the PUT body;
  the Content editor switch and Instructions save; wizard; Test tab picks a subset. Step 2: fail. Step 3: implement.
  Step 4: vitest, tsc, format:check. Step 5: commit `feat(admin-ui): agent Resources tab and Content editor switch`.
