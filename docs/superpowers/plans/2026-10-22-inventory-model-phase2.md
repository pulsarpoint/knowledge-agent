# Inventory Model Phase 2 (Environment Model, Agent Tools, Inventory Editor Role) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One environment model (JSON) built by a pure function and served at `GET /environments/{id}/model`; agent tools
that read and change the inventory through the same store as the UI; writes gated by a new role `inventory_editor`
(per agent, default off) and audited with the agent and session.

**Architecture:** `inventory/model.py` (pure `build_model(rows) -> dict`) + a store read that loads the rows; the endpoint
returns it. `capabilities/inventory_tools.py` holds the tools, each with a pure `check_<tool>`; writes call
`inventory/store.py` with an author `{actor (session's person), agent_id, session_id}`. The role lives in the access
object like `content_editor`. The map (Phase 3) will draw from the model.

**Tech Stack:** PostgreSQL 17, Python 3.14 / FastAPI / psycopg 3 / openai-agents 0.19 / pytest; React Router 7 /
TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-22-inventory-model-design.md` (sections "Environment model", "Agent tools").
Adjusted split: this phase also builds the model and its endpoint (the tool needs it); Phase 3 is the map drawing from it.

## Global Constraints

- **Simple code, no unnecessary abstractions; decisions testable in isolation** (`build_model` and every
  `check_<tool>` are pure; the access matrix covers the new tools).
- **One write path:** tools call `inventory/store.py`; nothing else writes the inventory structure.
- Work only in `.claude/worktrees/inventory-tools` (branch `feature/inventory-tools`). Never `make`, never touch
  `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack, never kill processes you
  did not start. UI checks: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once
  (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims. No migration expected (roles live in the access jsonb); if one is needed it
  is **0127**.
- The model never contains secrets (no SSH credentials, key references, usernames).
- **Access rules for the tools (controller decision):** reads need the environment / server / service / deployment to be
  usable in the run's access (`access.can_use`, children included). Writes need the role `inventory_editor` **and** the
  target to be in the run's access **or created by this session** (the store's audit `session_id`), so an agent can add
  a server and then deploy on it and connect it within one run. Creating a server or service needs only the role and at
  least one environment in the run's access. Refusal texts (exact): `This agent may not change the inventory (inventory
  editor is off).`, the existing `This <type> is not available in this run.`
- Tools for runs with a session only; subagents and experts do not get the write tools (they get the reads).
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests alone).

## Review Focus

1. A run without `inventory_editor` can read but never write; the role is only ever narrowed (runs, experts,
   subagents never gain it).
2. An agent can create a server, deploy a service on it and connect it in one run; it cannot touch things outside its
   access that it did not create.
3. UI and tool writes produce identical rows; tool writes are audited with agent and session.
4. The model has no secrets and matches the rows exactly (servers → deployments, connections incl. external hosts,
   relations).
5. The access matrix covers every new tool.

---

### Task 1: The environment model

**Files:** `services/admin-api/src/knowledge_admin/inventory/model.py` (pure `build_model(environment, servers,
services, deployments, connections, relations, code_projects) -> dict` with the spec's shape: `{environment, servers:
[{…, deployments: […]}], services, connections, relations, external: [{host, ports}]}`; connections whose other end is
outside the environment appear with that end's environment named), a store read `load_model_rows(conn, project_id,
environment_id)`, `GET /environments/{id}/model` in `inventory/api.py`; tests `tests/test_inventory_model.py` (pure
builder cases + the endpoint; no secret fields).

- [ ] Step 1: failing tests. Step 2: fail. Step 3: implement. Step 4: focused + full backend suite. Step 5: commit
  `feat(inventory): the environment model`.

### Task 2: The inventory editor role

**Files:** `services/admin-api/src/knowledge_admin/access.py` (`is_inventory_editor`; `narrow` keeps roles only
switched off — both roles), run start (`sessions/run_access.py` / `start.py`: the agent's role copied, a request may only
switch it off; ad-hoc runs: off), experts/subagents (role narrowed like content_editor), `agents/profiles_api.py` (PUT
accepts `roles.inventory_editor`), UI `features/agents/AgentProfileSettings.tsx` (switch **Inventory editor** under
Content editor, help "Lets this agent add and change servers, services, deployments, connections and relations."),
Debug panel shows both roles; tests.

- [ ] Step 1: failing tests (narrowing, start, experts/subagents never gain it, PUT, UI switch). Step 2: fail. Step 3:
  implement. Step 4: backend + UI suites. Step 5: commit `feat(access): the inventory editor role`.

### Task 3: Agent tools

**Files:** `services/admin-api/src/knowledge_admin/capabilities/inventory_tools.py` (reads: `get_environment_model
(environment_id)`, `list_servers(environment_id)`, `list_deployments(server_id | service_id)`, `list_connections
(service_id | deployment_id)`; writes: `save_server`, `save_service`, `save_deployment`, `save_connection`,
`save_relation` (create without `id`, update with `id` + `revision`), `remove_inventory_item(kind, id, revision)`; each
with a pure `check_<tool>` per the access rules; writes call the store with `{actor: session's person, agent_id,
session_id}`; replies are JSON; store errors become the tool's refusal text), `tools/tools.json`, `tools/catalog.py`
import, withholding write tools from subagents/experts, the store's "created by this session" lookup (audit rows or a
`created_session_id` read), tool schema snapshot regenerated for the new tools, docs (`docs/agents.md` tools,
`docs/user-guide.md` Inventory editor); tests `tests/test_inventory_tools.py` + matrix rows.

- [ ] Step 1: failing tests — each read (access), each write (role off refused; in access; created this session;
  outside access refused), the create-server → deploy → connect flow in one run, audit rows with agent and session,
  identical rows to the API, subagent/expert tool lists. Step 2: fail. Step 3: implement. Step 4: focused + matrix + full
  backend suite. Step 5: commit `feat(inventory): agent tools read and change the inventory`.
