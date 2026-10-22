# Inventory Drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every agent write to the inventory is a draft a person confirms or discards; everything (environments,
servers, services, deployments, memberships, connections, relations) can be a draft; one well-documented batch tool
drafts a whole picture; the Inventory editor role and service proposals are removed.

**Architecture:** Migration 0128 adds `status` to seven tables. Pure rules list what confirm and discard reach; the
store stays the only writer (`confirm`, `discard`, agent writes forced to draft). Agent tools write drafts, refuse
confirmed entries and gain `save_environment` and `save_inventory_draft` (a pure name resolver plus one transaction).
The UI shows drafts with Confirm / Discard everywhere inventory is shown.

**Tech Stack:** PostgreSQL 17; Python 3.14 / FastAPI / psycopg 3 / pydantic / pytest; React Router 7 / TypeScript /
shadcn/ui / vitest.

**Spec:** `docs/superpowers/specs/2026-10-23-inventory-drafts-design.md`

## Global Constraints

- **Simple code, no unnecessary abstractions;** rules are pure functions tested in isolation; the store is the only
  writer.
- Migration `0128_inventory_drafts` (up/down); existing rows confirmed; service proposals deleted, proposal kind
  document only. Stack data is test data, but up/down must be correct.
- Exact texts:
  - "This <type> is confirmed; only a person can change it. Add a new draft instead, or describe the change in your
    answer." (`<type>` = environment, server, service, deployment, connection, relation, membership)
  - batch errors: "<part>[<index>]: <reason>" (e.g. "connections[0]: no service named clickhous. Known services:
    clickhouse, clickhouse-backup.")
- People (UI/REST) write confirmed entries; editing a draft keeps it a draft; only people confirm and discard.
- No `inventory_editor` and no `propose_service` left in code, tool schemas, UI or docs (grep), except migration history
  and old specs/plans. Stored access objects that still carry `inventory_editor` load without error.
- Subagents and experts get only the read tools (unchanged).
- Work only in `.claude/worktrees/chat-proposals` (branch `feature/inventory-drafts`). Never `make`, never touch
  `.env`/`.local`, never `git stash` in any form (WIP commit instead), never touch the running Docker stack, never kill
  processes you did not start, never spawn subagents. UI: `node_modules` is a symlink (never commit it, never
  `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees incl. test_code_processing, test_deploy docs command, test_environments rollback);
  `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm format:check` (documentSaves and
  skills are slow: rerun alone).

## Review Focus

1. Confirming a draft connection whose deployments, server, service and environment are drafts confirms all of them
   in one transaction; a stale revision on any confirms nothing (Task 1).
2. Discarding a draft service removes its draft deployments, connections, relations and memberships it caused, never a
   confirmed entry (Task 1).
3. An agent names a confirmed service in `save_inventory_draft` with different fields → used as an end, unchanged, and
   the reply says it was used, not changed (Task 2).
4. A batch with one bad name writes nothing and names the entry (Task 2).
5. A person edits a draft in the workspace → it stays a draft; Confirm then confirms it (Task 4).

---

### Task 1: Draft status, confirm and discard in the store

**Files:**
- Create: `db/migrations/0128_inventory_drafts.up.sql` / `.down.sql`
- Modify: `services/admin-api/src/knowledge_admin/inventory/rules.py` (pure `confirm_reach(kind, id, rows)` and
  `discard_reach(kind, id, rows)` over the rows the store loads), `inventory/store.py` (every create/update takes
  `status` — `'confirmed'` default, `'draft'` for agents; updating a draft keeps draft; `confirm(conn, project_id, kind,
  id, revision, author) -> list[dict]`, `discard(...) -> list[dict]`, audit kinds `<kind>.confirmed` /
  `<kind>.discarded`; membership id `<environment_id>:<server_id>`), `inventory/api.py` (`POST
  /inventory/{kind}/{id}/confirm`, `.../discard`, body `{revision}`; responses list the entries reached), `model.py`
  and `assets.py` and `load_project_models` (every entry carries `status`; the model also counts `drafts`)
- Test: `tests/test_inventory_drafts_migration.py`, `tests/test_inventory_rules.py`, `tests/test_inventory_store.py`,
  `tests/test_inventory_api.py`, `tests/test_inventory_model.py`

- [ ] Step 1: failing tests — migration (columns, defaults, service proposals deleted, down deletes drafts); rules for
  each kind (Review Focus 1, 2); store create draft / update draft stays draft / confirm cascade / discard cascade /
  stale revision → Conflict and nothing changed / audit rows; API 200 with the reached list, 409 stale, 404 unknown;
  model status and `drafts` count.
- [ ] Step 2: run; fail. Step 3: implement. Step 4: focused + full backend suite;
  `tests/test_inventory_map_models.py` must still pass unchanged (all its rows are confirmed).
- [ ] Step 5: commit `feat(inventory): draft status with confirm and discard`.

### Task 2: Agent tools write drafts; the batch tool; tool documentation; the role goes

**Files:**
- Modify: `capabilities/inventory_tools.py` (writes create/update drafts, refuse confirmed with the exact text; new
  `save_environment`; new `save_inventory_draft` with a pure resolver `resolve_draft(payload, recorded) -> plan` and one
  transaction; reads return `status`), `capabilities/inventory.py` (`get_resource` carries status), `access.py`
  (remove `is_inventory_editor`, `inventory_editor` from ROLES and refusal texts; unknown roles ignored),
  `sessions/start.py`, `sessions/run_access.py`, `sessions/review.py`, `knowledge_agents/config.py`, `experts.py`,
  `subagents.py`, `agents/profiles_api.py`, `agents/wizard.py`, `tools/tools.json` (full descriptions with formats and
  an example per tool), `tools/README.md`, the schema golden (`WRITE_GOLDEN=1`), `docs/agents.md`
- Modify (UI part of the role removal): `features/agents/AgentProfileSettings.tsx` (switch gone), `features/agents/api.ts`,
  `features/agents/wizard.ts`, the Debug panel roles, their tests (`agentGeneral`, `debugPanel`)
- Test: `tests/test_inventory_tools.py`, `tests/test_inventory_draft_batch.py` (resolver pure + end-to-end),
  `tests/test_tool_access_matrix.py`, `tests/test_access.py`, run-start / experts tests that mentioned the role

- [ ] Step 1: failing tests — each save tool creates a draft without any role, updates a draft, refuses a confirmed
  entry with the exact text; `remove_inventory_item` drafts only; the batch resolver: in-call names first, recorded
  next, several servers without `from_server` → error naming them, unknown name → "<part>[<i>]: …", confirmed entry
  used not changed (Review Focus 3), all-or-nothing (Review Focus 4), the reply lists created / updated / used with ids;
  the spec's example payload drafts everything it describes; an old access object with `inventory_editor` loads; grep
  test: no `inventory_editor` in `src/` and `app/`.
- [ ] Step 2: run; fail. Step 3: implement. Tool descriptions follow the spec's "Tool documentation" bullet exactly
  (purpose, drafts, every field with format and allowed values, naming, example).
- [ ] Step 4: focused + matrix + full backend + UI suites.
- [ ] Step 5: commit `feat(inventory): agents write drafts; save_inventory_draft; the inventory editor role is gone`.

### Task 3: Service proposals go

**Files:**
- Modify: `capabilities/discovery.py` (remove `propose_service`, `ProposeService`, connection resolution used only by
  it, `check_propose_service`; `propose_document` unchanged; its description points inventory findings to the save
  tools), `servers/discovery.py` (remove service approval paths; document approval unchanged), `tools/tools.json`,
  schema golden, `features/sessions/ProposalSheet.tsx` and `features/servers/discovery.ts` (service parts gone),
  `features/servers/ProposalsPage.tsx`, `ProposeWizard` texts if they mention service proposals, `docs/user-guide.md`,
  `docs/agents.md`
- Test: `tests/test_discovery_tools.py`, `tests/test_discovery_review.py`, `tests/test_resource_proposals.py`, UI
  proposal tests

- [ ] Step 1: update tests to the document-only proposals; add: a document proposal about a service still links to
  it; grep test: no `propose_service`.
- [ ] Step 2: run; fail. Step 3: remove. Step 4: full backend + UI suites.
- [ ] Step 5: commit `refactor(discovery): service proposals give way to inventory drafts`.

### Task 4: Drafts in the UI

**Files:**
- Modify: `features/servers/EnvironmentWorkspace.tsx` (Draft badges; Confirm / Discard on servers and services rows;
  filter All / Confirmed / Drafts in the URL `?drafts=only|none`), `ServicePanel.tsx` (badges and buttons on fields,
  deployments, connections, relations), `EnvironmentPage.tsx` (header "N drafts to review" link), `EnvironmentsView.tsx`
  (draft environments), `ServerPage.tsx` services table, `SystemMapView.tsx` + the d2/mermaid renderers in
  `servers/system_graph_render.py` and `servers/inventory_map.py` (draft nodes/edges carry `draft: true`, drawn dashed;
  a Show drafts switch, on by default, `?drafts=0` hides them), `inventory.ts` / `environments.ts` (confirm / discard
  calls, `status` types), a small `ConfirmDraftSheet.tsx` (lists what Confirm will also confirm, from a dry-run: `POST
  .../confirm?dry_run=1` — add it to the API in this task), `docs/user-guide.md`
- Test: UI tests for each place; backend tests for `dry_run` and the map's `draft` flag (new fixture; the captured
  `map_models_expected.json` stays unchanged)

- [ ] Step 1: failing tests — badges; Confirm shows the reach list then confirms; Discard; filter; header count; a
  person's edit of a draft keeps the badge (Review Focus 5); map draft flag and dashed rendering; Show drafts off hides
  them.
- [ ] Step 2: run; fail. Step 3: implement. Step 4: full UI + backend suites.
- [ ] Step 5: commit `feat(inventory): drafts in the workspace, pages and map`.
