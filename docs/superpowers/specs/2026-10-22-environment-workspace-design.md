# Environment Workspace — Design

Approved direction: 2026-10-22 (conversation). Builds on the inventory model (`2026-10-22-inventory-model-design.md`,
phases 1–3 merged a1b831e).

## Problem

- The environment, server and service pages show the inventory as separate lists. To see what runs where and how it is
  connected, a person jumps between pages, and there is no place to add a connection or a relation next to the service
  it belongs to.
- A server belongs to an environment only through a deployment, so an empty server cannot be put in an environment.
- A discovery run's service proposal can carry only a deployment, `depends_on` and `built_from`. It cannot propose
  connections or `part_of`, and its review sheet shows little more than that, so the reviewer sees the documentation but
  not how the service is connected.

## Decisions (from the conversation)

1. **Server membership is explicit.** A server is put in an environment on its own; services are still placed only by
   deployments.
2. **Service proposals are extended** with connections and `part_of`; the review sheet shows everything the proposal
   would write.
3. **The workspace lives on the environment page.** The separate server and service pages stay for links from
   elsewhere.

## 1. Server membership

- New table `ka.environment_servers (environment_id, server_id, created_at)`, primary key on both, both foreign keys
  `ON DELETE CASCADE`. Migration `0127`; the up migration backfills it from existing deployments (`SELECT DISTINCT
  environment_id, server_id FROM ka.service_deployments`).
- Rules:
  - a deployment's server must be a member of the deployment's environment; `store.create_deployment` adds the
    membership when it is missing (same transaction, audited), so creating a deployment never fails on membership;
  - removing a server from an environment is refused while it has deployments there: "Remove the deployments on this
    server in this environment first.";
  - moving a deployment to another environment (update) adds the membership there the same way.
- Store: `add_server_to_environment(conn, project_id, environment_id, server_id, author)` (idempotent: already a member →
  no change, no audit row) and `remove_server_from_environment(conn, project_id, environment_id, server_id, author)`.
  Both write the inventory audit (kinds `membership.added` / `membership.removed`).
- REST: `PUT /environments/{id}/servers/{server_id}` and `DELETE /environments/{id}/servers/{server_id}`.
- The environment model lists the member servers (each with its deployments in this environment, possibly none). The
  map's infrastructure view draws a member server with no deployments inside its environment instead of as unplaced. The
  assets read model (environments list, server page) shows membership from the table.
- Agent tools: `save_server` gains an optional `environment_id` that puts the server (new or existing) in that
  environment; the environment must be in the run's access. Removing a membership is not an agent operation.
  `list_servers` reports each server's environments.

## 2. Service proposals carry connections and part_of

- `propose_service` gains:
  - `connections`: up to 50 of `{to_service, to_server, to_host, port, protocol, description}` — the proposed
    deployment connects either to a recorded service's deployment (`to_service`, plus `to_server` when the service runs
    on more than one server; looked up in the proposal's environment first, then in any environment) or to an external
    host (`to_host`). Unknown names are refused with the same style of message as `depends_on` ("No service named …").
  - `part_of`: names of recorded services this service is part of (library or component).
- The payload stores the resolved ids and names (as `depends_on` does today), so the sheet can show them and approval
  can write them.
- Approving the proposal writes, in one transaction through the inventory store: the service (new or updated), the
  deployment, then its connections and relations. An existing connection with the same ends, port and protocol is not
  duplicated. A rejected or partly applied proposal does not exist: any store error rolls the whole approval back with the
  error shown.
- The review sheet for a service proposal shows every part, each with what is new or changed against today: the service
  fields; the deployment; connections (from → to, port, protocol); relations (depends on, part of, built from); and the
  document proposals of the same run about this service (titles, each opening its own sheet). The sheet of a document
  proposal about a proposed service names that service proposal and links to it.

## 3. The environment workspace

The environment page keeps its header (name, description, map link, propose button, documents) and its Chats tab. The
Info tab becomes a workspace of three columns.

```
┌ Servers ─────────┐┌ Services on app-1 ───┐┌ payments-api ─────────────────────────┐
│ app-1        ●   ││ payments-api     ●   ││ type · role · owner · description      │
│ db-1             ││ nginx                ││ Deployment here: docker · :8000 · v1.4 │
│ cache-1 (empty)  ││ worker               ││ Also runs on: prod / app-2             │
│                  ││                      ││ Connections   → postgres@db-1:5432  [+]│
│ [+ Add server]   ││ [+ Add service]      ││               ← nginx@app-1:80         │
│                  ││                      ││ Relations     part of billing      [+] │
│                  ││                      ││ Code projects payments-repo        [+] │
│                  ││                      ││ Documents     Runbook              [+] │
└──────────────────┘└──────────────────────┘└────────────────────────────────────────┘
```

- **Column 1, Servers:** the environment's member servers, with their deployment count. **Add server** opens a dialog:
  pick a server that is not yet in this environment, or create a new one (name, hostname, description). Each row has
  edit and "Remove from environment".
- **Column 2, Services on the selected server:** the deployments on that server in this environment. **Add service**
  opens a dialog: pick a recorded service or create a new one (name, type, role, owner, description), plus the deployment
  fields (runtime, runtime name, port, version, log location).
- **Column 3, the selected service:**
  - its fields (edit) and its deployment here (edit, remove), and where else it runs;
  - connections out and in, each with edit and remove; **+** adds a connection from this deployment to a deployment of
    another service (picker grouped by environment and server) or to an external host, with port, protocol and
    description;
  - relations: depends on, part of, and the reverse (needed by, has part), with remove; **+** adds one;
  - code projects (`built_from`), with remove; **+** adds one;
  - documents linked to the service (the existing documents list, with link and new document).
- The selection lives in the URL (`?server=<id>&service=<id>`), so links, reload and Back work.
- Narrow screens show one column at a time, with a back link to the previous column.
- Every change goes through the existing inventory REST API (plus the membership endpoints) and sends the revision it
  read; a conflict shows "This was changed meanwhile. Reload to see the latest." and reloads the column.
- The service page reuses the column 3 component, so a service looks the same in both places.
- The workspace reads one environment model (`GET /environments/{id}/model`) and reloads it after each change; the pickers
  read the existing list endpoints.

## Not in scope

- Agent-proposed removal of connections or relations.
- Drag and drop, or editing on the system map.
- Deleting servers or services from the workspace (they can be removed from the environment or have their deployment
  removed; deleting the record stays on its own page).

## Testing

- Store and rules: membership added by a deployment, idempotent add, refused removal, backfill in the migration, audit
  rows; the model lists empty member servers; the map places them.
- Proposals: resolving connection targets (one server, several servers, other environment, unknown name, external host),
  `part_of`, approval writing everything in one transaction and rolling back on error, no duplicate connections.
- UI (vitest): column selection from the URL, each add dialog, conflict message, the proposal sheet showing every part.
