# Inventory Model Phase 1 (Model, API, UI) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One inventory model — deployments with a required environment, network **connections** between deployments
(or to external hosts), **service relations** (`depends_on`, `part_of`, `built_from`) — written and read through one
`inventory` module and one REST API that the UI uses; the old membership tables, `inventory_links`, the aggregate
`save_service` and the free-text environment are gone.

**Architecture:** A new `knowledge_admin/inventory/` package: `rules.py` (pure validation), `store.py` (create / update /
delete / list functions with revision checks and audit, the only code that writes these tables), `api.py` (REST
endpoints calling the store). Every reader of membership or links switches to deployments / connections / relations.
Agent tools (Phase 2) and the environment model JSON + map (Phase 3) build on this.

**Tech Stack:** PostgreSQL 17, Python 3.14 / FastAPI / psycopg 3 / pytest; React Router 7 / TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-22-inventory-model-design.md`. Phase numbering: the spec's phases 1–2 are this
phase (merged so there is never a second source of truth); the spec's phases 3 and 4 become Phases 2 and 3.

## Global Constraints

- **Simple code, no unnecessary abstractions; rules testable in isolation** (`inventory/rules.py` is pure).
- **One write path:** after this phase nothing outside `inventory/store.py` inserts, updates or deletes servers,
  services, environments, deployments, connections or relations (grep for each table name in INSERT/UPDATE/DELETE).
- Work only in `.claude/worktrees/inventory-model` (branch `feature/inventory-model`). Never `make`, never touch
  `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack, never kill processes you
  did not start. UI checks: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once
  (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims or redirects; migrations correct up and down (down may lose new-only data,
  noted). Next migrations: **0125** (new tables + conversion), **0126** (drop the old structures).
- Values: connection `protocol` 1–20 chars `[a-z0-9.+-]`; `port` 1–65535; `to_host` a hostname or IP (1–253 chars);
  relation kinds `depends_on` | `part_of` | `built_from`; exactly one target per connection (deployment or host) and per
  relation (service or code project, matching the kind).
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests alone).

## Review Focus

1. Environment of a server/service = environments of its deployments, everywhere (run access children, documents,
   get_resource, map, UI lists) — no reader still uses membership.
2. Run access: an environment still brings its services and servers (now via deployments); a service its servers and
   code projects (`built_from` relations); a server its deployed services.
3. The conversion keeps every relationship: depends_on/built_from → relations; connects_to between deployed services →
   connections, else depends_on.
4. Deleting a server/service/environment refuses while deployments or connections use it; deleting a deployment removes
   its connections.
5. Discovery's accept and the UI forms produce the same rows as direct store calls.

---

### Task 1: Tables, conversion and the store

**Files:** `db/migrations/0125_inventory_model.up.sql`/`.down.sql` (create `ka.connections` and `ka.service_relations` per
the spec with project-scoped composite FKs and CHECKs; backfill `service_deployments.environment_id` from the free-text
`environment` (create missing environments) and set it NOT NULL; convert `inventory_links` to relations/connections per
the spec rules; old tables stay until Task 4); new package `services/admin-api/src/knowledge_admin/inventory/`
(`rules.py`: pure validators for each item; `store.py`: `list_*` / `create_*` / `update_*` / `delete_*` for servers,
services, environments, deployments, connections, relations — revision checks, project scoping, delete rules, audit
rows with actor, and with agent + session when given; returns plain dicts); tests `tests/test_inventory_rules.py`,
`tests/test_inventory_store.py`, `tests/test_inventory_migration.py`.

- [ ] Step 1: failing tests — every rule; store CRUD + revision conflicts + delete rules + audit; conversion rules
  (incl. connects_to with an undeployed end → depends_on; server/environment ends dropped). Step 2: fail. Step 3:
  implement. Step 4: focused + full backend suite. Step 5: commit `feat(inventory): connections, service relations and
  one store`.

### Task 2: Every reader on the new model

**Files:** `sessions/run_access.py` (children: environment → servers and services via deployments; service → servers via
deployments and code projects via `built_from`; server → deployed services), `capabilities/inventory.py`
(`get_resource`: relations and connections with port/protocol; environment contents via deployments),
`documents/store.py`, `agents/wizard.py`, `servers/discovery.py` (readers), `servers/inventory_map.py` (membership
derived from deployments; infrastructure edges from connections between deployment nodes; services view from relations
and connections rolled up), the `*_assets` views (redefined in 0126 or replaced by store reads — choose the simpler),
tests.

- [ ] Step 1: failing tests for Review Focus 1–2 and the map edges. Step 2: fail. Step 3: implement. Step 4: focused +
  full backend suite. Step 5: commit `refactor(inventory): every reader uses deployments, connections and relations`.

### Task 3: One API; writers on the store

**Files:** `inventory/api.py` (REST: `/servers`, `/services`, `/environments` CRUD without membership lists;
`/deployments` CRUD; `/connections` CRUD; `/relations` CRUD; list reads `GET /environments/{id}/servers`,
`/servers/{id}/deployments`, `/services/{id}/deployments`, `/services/{id}/connections`, `/deployments/{id}/connections`,
`/services/{id}/relations`); remove the aggregate `save_service` and the old service/server/environment write endpoints
and `servers/inventory_links_api.py`; `servers/discovery.py` accept → store; `api.py` router wiring; tests (API
validation and errors, discovery accept produces the same rows).

- [ ] Step 1: failing tests. Step 2: fail. Step 3: implement. Step 4: focused + full backend suite; grep that no INSERT/
  UPDATE/DELETE on the inventory tables remains outside `inventory/store.py` (and migrations). Step 5: commit
  `feat(inventory): one REST API; writers use the store`.

### Task 4: UI, docs, drop the old structures

**Files:** `db/migrations/0126_inventory_drop_old.up.sql`/`.down.sql` (drop `environment_services`, `environment_servers`,
`inventory_links`, `inventory_entries` if unused, the free-text `service_deployments.environment`; down recreates them
empty or backfilled where simple); UI in `services/admin-ui/app/features/servers/` (ServiceForm: deployments and
relations through the new endpoints; EnvironmentForm without membership; server/environment pages list via the new
reads; the system map's EntrySheet edits connections (deployment, port, protocol, description) and relations instead of
generic links; `serviceGraph.ts`/`ServiceDependencyGraph.tsx`/`ServiceTalaGraph.tsx` read relations — or are removed if
the map's services view covers them; say which), tests; docs (`docs/user-guide.md` inventory sections,
`docs/architecture.md` if it describes links, `docs/migrations.md` 0125–0126).

- [ ] Step 1: failing vitest tests for the forms and the connection editor. Step 2: fail. Step 3: implement. Step 4: UI
  checks + full backend suite. Step 5: commit `feat(admin-ui): inventory forms on the new API; old structures dropped`.
