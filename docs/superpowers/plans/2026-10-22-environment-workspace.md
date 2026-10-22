# Environment Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Servers belong to environments explicitly, external systems are services connections can point to, service
proposals carry connections and `part_of`, and the environment page becomes a three-column workspace
(servers › services › the service) where everything is added and edited.

**Architecture:** One migration (0127) adds `ka.environment_servers`, `services.address` and
`connections.to_service_id`. The inventory store stays the only writer; rules stay pure. The model, the map, the agent
tools and the discovery approval learn the new pieces. The UI builds the workspace from the environment model and the
existing inventory REST API; the service column is one component reused on the service page.

**Tech Stack:** PostgreSQL 17 migrations; Python 3.14 / FastAPI / psycopg 3 / pytest; React Router 7 / TypeScript /
shadcn/ui / vitest.

**Spec:** `docs/superpowers/specs/2026-10-22-environment-workspace-design.md`

## Global Constraints

- **Simple code, no unnecessary abstractions;** rules are pure functions tested in isolation; the store is the only
  writer of the inventory structure.
- Migration number `0127` (`db/migrations/0127_environment_workspace.up.sql` / `.down.sql`); the down migration restores
  0126's shape. Stack data is test data, but the backfill must be correct.
- Exact texts (from the spec):
  - "Remove the deployments on this server in this environment first."
  - "Connect to an external service, or to a deployment of this service."
  - "This service is the target of connections; remove them or keep the type external."
  - "This was changed meanwhile. Reload to see the latest."
- Audit kinds `membership.added` / `membership.removed`; `add_server_to_environment` is idempotent (no audit row when
  already a member).
- Agents never remove a membership. `save_server(environment_id)` needs the environment in the run's access; an external
  service target needs to be in access or created by this session.
- Proposal approval is one transaction: any store error rolls back the whole approval.
- Work only in `.claude/worktrees/environment-workspace` (branch `feature/environment-workspace`). Never `make`, never
  touch `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack, never kill
  processes you did not start. UI: `node_modules` is a symlink (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees incl. test_code_processing, test_deploy docs command, test_environments rollback);
  `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests
  alone).

## Review Focus

1. A service already targeted by connections is edited to type `custom` → refused with the exact text, nothing
   changed (Task 1 test).
2. A server's last deployment in an environment is removed → the server stays a member (empty), still listed in the
   model and the workspace (Task 1 test; Task 4 test).
3. A proposal connects to a service that runs on two servers without `to_server` → refused naming the servers to choose
   from; with `to_server` → resolved (Task 3 test).
4. A workspace URL names a server or service that is no longer in the environment (removed by someone else) → the
   column falls back to "nothing selected" without an error (Task 4 test).
5. Two people edit the same connection → the second save gets the conflict text and the column reloads (Task 5 test).

---

### Task 1: Membership and external services in the store

**Files:**
- Create: `db/migrations/0127_environment_workspace.up.sql`, `.down.sql`
- Modify: `services/admin-api/src/knowledge_admin/inventory/rules.py` (service `address`; connection `to_service_id`;
  pure `check_target_service(service_type)` and `check_type_change(old_type, new_type, targeted)`),
  `inventory/store.py` (membership functions; `create_deployment` / `update_deployment` add membership; connection
  target external service; service type change guard; `list_servers` returns `environment_ids`),
  `inventory/api.py` (`PUT` / `DELETE /environments/{environment_id}/servers/{server_id}`; service `address`;
  connection `to_service_id`), `inventory/model.py` + `store.load_model_rows` / `load_project_models` (member servers
  incl. empty; `external_services`), `inventory/assets.py` (membership from the table)
- Test: `tests/test_inventory_workspace_migration.py`, `tests/test_inventory_rules.py`,
  `tests/test_inventory_store.py`, `tests/test_inventory_api.py`, `tests/test_inventory_model.py`

**Interfaces:**
- Produces: `store.add_server_to_environment(conn, project_id, environment_id, server_id, author) -> bool` (True when
  added), `store.remove_server_from_environment(conn, project_id, environment_id, server_id, author) -> None` (raises
  `rules.Invalid` with the exact text when deployments exist; `store.NotFound` when not a member);
  connection values accept `to_service_id`; service values accept `address`; model gains
  `external_services: [{id, name, address, description, revision, ports}]` and servers with `deployments: []`;
  connection items in the model carry `to_service_id` and `to_service_name`.

Migration (up):

```sql
SET search_path = ka;
CREATE TABLE environment_servers (
    project_id uuid NOT NULL,
    environment_id uuid NOT NULL,
    server_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (environment_id, server_id),
    FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE
);
CREATE INDEX environment_servers_server ON environment_servers (server_id);
INSERT INTO environment_servers (project_id, environment_id, server_id)
SELECT DISTINCT project_id, environment_id, server_id FROM service_deployments;
COMMENT ON TABLE environment_servers IS 'The servers in an environment; a deployment''s server is always a member.';

ALTER TABLE services ADD COLUMN address text
    CONSTRAINT services_address CHECK (address ~ '^[A-Za-z0-9.:-]{1,253}$');
ALTER TABLE connections ADD COLUMN to_service_id uuid;
ALTER TABLE connections ADD FOREIGN KEY (to_service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE;
CREATE INDEX connections_to_service ON connections (to_service_id);
ALTER TABLE connections DROP CONSTRAINT connections_one_target,
    ADD CONSTRAINT connections_one_target CHECK (num_nonnulls(to_deployment_id, to_host, to_service_id) = 1);
ALTER TABLE connections DROP CONSTRAINT connections_once,
    ADD CONSTRAINT connections_once UNIQUE NULLS NOT DISTINCT (from_deployment_id, to_deployment_id, to_host, to_service_id, port, protocol);
```

(Check the existing `SET search_path` convention and the exact constraint names in `0125_inventory_model.up.sql` before
writing; the down migration deletes connections with `to_service_id`, restores both constraints, drops the column, the
address column and the table.)

- [ ] **Step 1: Write failing tests**
  - migration: backfill gives one row per distinct (environment, server) of the deployments; down then up round-trips;
  - rules: `check_target_service('external')` → None, `'custom'` → the exact text; `check_type_change('external',
    'custom', targeted=True)` → the exact text, `targeted=False` → None; `address` validated like `to_host`;
  - store: creating a deployment adds membership once (audit `membership.added` once); `add_server_to_environment`
    twice → second returns False and writes no audit row; removing with deployments → the exact text; removing the last
    deployment keeps membership (Review Focus 2); a connection to an external service; to a custom service → refused;
    changing a targeted service's type → refused and unchanged (Review Focus 1); deleting an external service removes
    its connections;
  - API: PUT/DELETE membership (204; 404 unknown environment/server; 422 with the text when deployments exist);
  - model: an empty member server listed with `deployments: []`; `external_services` with ports; connection items carry
    `to_service_id` / `to_service_name`.
- [ ] **Step 2: Run them; they fail.** `cd services/admin-api && uv run --frozen pytest -q -p no:warnings
  tests/test_inventory_workspace_migration.py tests/test_inventory_rules.py tests/test_inventory_store.py
  tests/test_inventory_api.py tests/test_inventory_model.py`
- [ ] **Step 3: Implement** the migration, rules, store, API, model and assets as above.
- [ ] **Step 4: Tests pass;** `tests/test_inventory_map_models.py` still passes unchanged (its fixture has no empty
  member server or external service — if the backfill makes it differ, stop and report). Full backend suite.
- [ ] **Step 5: Commit** `feat(inventory): explicit server membership and external services`.

### Task 2: The map and the agent tools

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/servers/inventory_map.py` (an empty member server is placed inside its
  environment; an external service is one named external node `external-service:<id>`; connection edges to it as
  today), `capabilities/inventory_tools.py` (`save_server(environment_id)`; `save_service(address)`;
  `save_connection(to_service_id)`; `list_servers` returns `environment_ids`; checks extended), `tools/tools.json`
  descriptions, the tool schema golden (`WRITE_GOLDEN=1`), `docs/agents.md`
- Test: `tests/test_inventory_map.py`, `tests/test_inventory_map_models.py` (new expectations only for the new fixture
  cases — add a second fixture, do not edit the captured one), `tests/test_inventory_tools.py`,
  `tests/test_tool_access_matrix.py`

**Interfaces:**
- Consumes: Task 1's store functions and model fields.
- Produces: tool parameters `environment_id`, `address`, `to_service_id`.

- [ ] **Step 1: Failing tests**: map places an empty member server and draws a named external-service node (both views);
  `save_server` with an environment in access puts it there, outside access → "This environment is not available in
  this run."; `save_connection` to an external service in access / created this session / outside access; to a custom
  service → the exact text; matrix rows.
- [ ] **Step 2: Run; fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Focused + matrix + full backend suite.**
- [ ] **Step 5: Commit** `feat(inventory): the map and agent tools know memberships and external services`.

### Task 3: Service proposals carry connections and part_of

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/capabilities/discovery.py` (`ProposeService.connections` (max 50 of
  `{to_service, to_server, to_host, port, protocol, description}`) and `part_of`; resolution into ids and names in the
  payload), `servers/discovery.py` (approval writes service, deployment, connections, relations in one transaction via
  the store; skips an identical existing connection), `services/admin-ui/app/features/sessions/ProposalSheet.tsx`
  (service proposal: fields, deployment, connections, relations, the run's document proposals about it with links;
  document proposal: names and links its service proposal), its types in `features/sessions/*` api file, `docs/user-guide.md`
  (discovery proposals)
- Test: `tests/test_discovery_tools.py`, `tests/test_discovery_review.py`, `tests/test_resource_proposals.py`,
  `services/admin-ui/app/features/sessions/ProposalSheet.test.tsx`

**Interfaces:**
- Consumes: Task 1's external services and store.
- Produces: payload `connections: [{to_deployment_id?, to_service_id?, to_host?, label, port, protocol, description}]`
  (`label` like `postgres @ db-1 (prod)` or the external service name or the host) and `part_of: [{id, name}]`.

- [ ] **Step 1: Failing tests**:
  - resolution: one-server target; two servers without `to_server` → refused naming the servers (Review Focus 3); with
    `to_server` → resolved; same environment preferred over another; external service by name; host; unknown name →
    "No service named …";
  - approval writes everything; a failing connection (e.g. the target deleted meanwhile) rolls back the service and
    deployment too; an identical existing connection is not duplicated; `part_of` written;
  - the sheet shows connections, relations and linked document proposals; a document proposal links to its service
    proposal.
- [ ] **Step 2: Run; fail.**
- [ ] **Step 3: Implement.** Keep resolution a pure function over the rows it is given (test it without a database).
- [ ] **Step 4: Focused + full backend + UI suites.**
- [ ] **Step 5: Commit** `feat(discovery): service proposals carry connections and part_of`.

### Task 4: The workspace — servers and services columns

**Files:**
- Create: `services/admin-ui/app/features/servers/EnvironmentWorkspace.tsx` (the three columns; selection from
  `?server=&service=`; narrow screens show one column with a back link), `AddServerDialog.tsx` (pick a server not in the
  environment, or create one: name, hostname, description), `AddServiceDialog.tsx` (pick or create a service, plus
  runtime, runtime name, port, version, log location → deployment on the selected server here)
- Modify: `features/servers/EnvironmentPage.tsx` (Info tab = header + workspace), `features/servers/inventory.ts` /
  `environments.ts` (membership calls, `getEnvironmentModel`, types for the model incl. `external_services`),
  `docs/user-guide.md` (environments)
- Test: `EnvironmentWorkspace.test.tsx`, `AddServerDialog.test.tsx`, `AddServiceDialog.test.tsx`

**Interfaces:**
- Consumes: `GET /environments/{id}/model`, `PUT/DELETE /environments/{id}/servers/{server_id}`, existing create
  endpoints.
- Produces: `EnvironmentWorkspace({ environmentId })`; a column-3 slot rendering `ServicePanel` (Task 5) — until Task 5,
  a placeholder showing the service name and its deployment.

- [ ] **Step 1: Failing tests**: columns render from a model; clicking a server sets `?server=`, clicking a service sets
  `?service=`; an empty member server is listed with "no services"; stale ids in the URL fall back to nothing selected
  (Review Focus 4); Add server (existing and new) calls the API and reloads; Remove from environment shows the refusal
  text when deployments exist; Add service (existing and new) creates the deployment on the selected server.
- [ ] **Step 2: Run; fail.**
- [ ] **Step 3: Implement** with the existing shadcn components (`Dialog`, `Button`, `Select`/`Command` as used
  elsewhere in `features/servers`).
- [ ] **Step 4: UI suite, tsc, format.**
- [ ] **Step 5: Commit** `feat(environments): the workspace — servers and services`.

### Task 5: The service column

**Files:**
- Create: `services/admin-ui/app/features/servers/ServicePanel.tsx` (fields with edit; the deployment here with edit and
  remove; where else it runs; connections out and in with edit/remove; relations (depends on, part of, needed by, has
  part) with remove; code projects with remove; documents via `EntryDocuments`), `AddConnectionDialog.tsx` (target:
  a deployment grouped by environment and server, an external service, or a host; port, protocol, description),
  `AddRelationDialog.tsx` (depends on / part of a service, or built from a code project)
- Modify: `EnvironmentWorkspace.tsx` (column 3 = `ServicePanel`), `ServicePage.tsx` (uses `ServicePanel`;
  `ServiceDetails.tsx` removed if nothing else uses it), `ServiceForm.tsx` (Address field for type `external`),
  `docs/user-guide.md`
- Test: `ServicePanel.test.tsx`, `AddConnectionDialog.test.tsx`, `AddRelationDialog.test.tsx`

**Interfaces:**
- Consumes: existing inventory REST endpoints (connections, relations, deployments, services, documents), Task 1's
  `to_service_id` and `address`.
- Produces: `ServicePanel({ serviceId, environmentId?, serverId?, onChanged })`.

- [ ] **Step 1: Failing tests**: the panel lists connections both ways with labels; Add connection to a deployment, an
  external service and a host; Add relation of each kind; remove buttons send the revision; a 409 shows "This was
  changed meanwhile. Reload to see the latest." and reloads (Review Focus 5); the service page renders the same panel;
  the Address field appears only for type external.
- [ ] **Step 2: Run; fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: UI suite, tsc, format; backend suite unchanged.**
- [ ] **Step 5: Commit** `feat(environments): the service column with connections, relations and documents`.
