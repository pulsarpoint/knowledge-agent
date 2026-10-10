# System graph inventory sync — design

Date: 2026-10-10. Status: approved in conversation, section by section; **revised the same day** — the sync moved from
database triggers to a Python reconcile, and reads moved to an in-memory graph (networkx), at the user's request. Follows
`2026-10-10-system-graph-design.md` (step 1, merged as 8f81e5e); this is roadmap step 3 brought forward (LLM/Diátaxis
output, step 2, is postponed).

## Why

Servers and services added in the inventory do not appear in the system graph; today every node is made by hand. The
inventory (Servers, Services with their deployments, Environments, Code projects) stays the source of truth, and the
graph must follow it automatically: same transaction, whichever page or code path wrote the row.

## Decisions

- **Typed links.** `kg_nodes` gets nullable foreign keys `service_id`, `server_id`, `deployment_id`, `environment_id`,
  `repository_id`; at most one per node; one node per inventory row; the link must match the node's layer.
- **A Python reconcile does the sync** (`graph_sync.sync_inventory(conn, project)`): it reads the inventory, works out
  which synced nodes and edges should exist, compares that with what is stored and writes only the difference. Every
  inventory write path calls it in its own transaction, so the inventory and the graph never disagree; because it
  compares whole pictures, a missed call or an old bug is repaired by the next call. It also runs when admin-api starts
  (that is the backfill for existing inventory). No sync triggers in the database.
- **Reads come from an in-memory graph.** Each process keeps the project's graph in a `networkx.MultiDiGraph`
  (`graph_memory.GraphMemory`), rebuilt whole from the database when the stored graph version changes, and the old copy
  dropped. Views and search use it; writes still go to PostgreSQL first.
- **Mapping** (below). Deployments are nodes under their server (so opening a server shows what runs on it).
  Environments are Infrastructure nodes with `includes` edges (an environment cannot be a parent: a server can be in
  several). `ka.projects` gets no node (the deployment's single project is the implicit root of every layer).
- **Deleting an inventory row** deletes its node when the node has no children and no attached documents; otherwise the
  node stays as a hand-made node, unlinked, with a note in its description. Synced edges always go with their row.
- **Synced items are read-only where the inventory owns them**: a synced node's name, kind, place and existence; synced
  edges entirely. Description, properties, children, hand-made edges and documents stay editable.

## Data model (`0093_graph_inventory_sync`)

### `ka.kg_nodes` — new columns

| column | references | notes |
|---|---|---|
| `service_id` | `services(id, project_id)` | Software layer only |
| `server_id` | `servers(id, project_id)` | Infrastructure only |
| `deployment_id` | `service_deployments(id, project_id)` | Infrastructure only |
| `environment_id` | `environments(id, project_id)` | Infrastructure only |
| `repository_id` | `repositories(id, project_id)` | Software only |

- Foreign keys use `(x, project_id)` so a link can never cross projects, and are `DEFERRABLE INITIALLY DEFERRED`: when an
  inventory row is deleted, the link still points at it until commit, so the reconcile in the same transaction sees
  which node lost its row and applies the delete rule. A write path that forgot to call the sync fails at commit
  instead of leaving a broken link — tests catch it.
- `CHECK (num_nonnulls(service_id, server_id, deployment_id, environment_id, repository_id) <= 1)`.
- `CHECK` that a set link matches the layer (service/repository → `software`; server/deployment/environment →
  `infrastructure`).
- A unique partial index per column (`WHERE x IS NOT NULL`): one node per inventory row.

### `ka.kg_edges`

- New column `synced boolean NOT NULL DEFAULT false`. Sync-made edges have `synced = true`.
- Edge type list gains `includes` (the type CHECK is replaced; diagrams draw it like the other types).

### `ka.kg_graph_version`

One row (`version bigint`). A statement-level trigger on `kg_nodes`, `kg_edges` and `kg_node_documents`
(`AFTER INSERT OR UPDATE OR DELETE … FOR EACH STATEMENT`) adds 1 and calls `pg_notify('ka_graph', version)`. Every
process compares it with the version of its in-memory copy before serving a read.

### Down migration

Deletes synced edges, deletes nodes that are linked and have no children or documents (repeatedly, so deployments go
before their servers), clears the rest of the links, drops the version table and its trigger, the columns and indexes,
and restores the old edge-type CHECK (any remaining `includes` edge is deleted first). `db/tests/run.sh` gets the 0093 down/up checks; the rollback chain still ends at version 83.

## Mapping

| Inventory row | Node | Edges (all `synced`) |
|---|---|---|
| server | Infrastructure, top level, kind `server`, name = server name | — |
| service deployment | Infrastructure, **under its server's node**, kind `instance`, name = `<runtime_name, or the service name when empty> · <environment>` (environment = the row's `environment` label) | `instance_of` → the service's node |
| environment | Infrastructure, top level, kind `environment`, name = environment name | `includes` → node of each server (`environment_servers`) and each service (`environment_services`) |
| service | Software, top level, kind `service`, name = service name | `depends_on` → node of each service it depends on (`service_dependencies`) |
| code project (`repositories`) | Software, top level, kind `repository`, name = repository name | `uses` from each service node linked to it (`service_repositories`) |

**Names.** A synced node's name follows its row. If a sibling already has that name (case-insensitive), the sync uses the
first free of `<name> (2)`, `<name> (3)`, … — an inventory save never fails because of the graph. On rename, the same
rule applies to the new name.

## Sync (`graph_sync.py`)

`sync_inventory(conn, project) -> SyncResult` runs inside the caller's transaction:

1. Take the project's graph lock (`pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(project::text))`), the same
   lock graph writes take first, so syncs and graph edits never deadlock.
2. Read the inventory: servers, environments, services, code projects that are not archived, deployments (with their
   service name), and the four relation tables.
3. Build the **desired** synced nodes (link, layer, kind, parent link, wanted name) and edges (from link, to link, type)
   per the Mapping table.
4. Read the **stored** linked nodes and synced edges.
5. Apply the difference, in this order: create missing nodes (servers before deployments), rename and move changed ones
   (suffix rule), release nodes whose row is gone or archived (delete rule below), add missing synced edges, delete
   synced edges no longer wanted. Unchanged rows are not touched (no revision bump).

**Delete rule.** A linked node whose row is gone (or whose code project is archived) loses its synced edges; it is
deleted if it has no children and no documents, otherwise its link is cleared and
`Removed from <Servers / Services / Services (deployment) / Environments / Code projects> on <YYYY-MM-DD>.` is appended
to its description (new line; cut from the front to 4000 characters). Releases run deepest first, so a deployment node is
handled before its server's node.

**Hand-made edges** with the same ends and type as a wanted synced edge are left as they are (the sync does not add a
duplicate and never deletes them).

**Who calls it.** Every write of the inventory, at the end of its transaction: Servers (create, edit, delete), Services
(create, edit with deployments, dependencies, code projects and environments, delete), Environments (create, edit,
delete), Code projects (create, edit, archive), and the admin-api start-up (backfill; logs and continues on failure, like
the other start-up tasks). Each call is cheap at our size (a few queries over a few hundred rows).

## In-memory graph (`graph_memory.py`)

- `GraphMemory.get(conn, project) -> Snapshot` returns the current snapshot: a `networkx.MultiDiGraph` (node attributes:
  layer, kind, name, description, parent_id, source_kind; edge keys: edge id, attributes: type, synced) plus the
  documents per node and the version it was built from. If the stored `kg_graph_version` differs from the snapshot's,
  it rebuilds the whole snapshot from the database and replaces the old one (thread-safe; one rebuild at a time).
- `system_graph.build_view` and search read from the snapshot instead of querying all rows per request. Node detail,
  edits and refusals keep reading single rows from PostgreSQL (they need the current `revision`).
- One snapshot per project per process (admin-api; later the agent tools). `networkx` becomes an admin-api dependency.

## API (`system_graph_api.py`)

- `GET /api/graph/nodes/{id}` adds `source`: `{"kind": "server" | "service" | "deployment" | "environment" |
  "repository", "id": "<row id>", "page": "/servers" | "/services" | "/services" | "/environments" | "/code-projects",
  "label": "Servers" | "Services" | "Services" | "Environments" | "Code projects"}`, or `null`.
- Edges in node detail and in views add `synced` (views: an aggregated edge is `synced` only when every underlying edge
  is).
- View nodes and boundary nodes add `source_kind` (the kind above or `null`).
- Refusals (409), exact messages:
  - rename or kind change of a synced node: `This node comes from <label>. Change it there.`
  - move (parent change) of a synced node: `This node comes from <label>. Its place follows the inventory.`
  - delete of a synced node: `This node comes from <label>. Delete it there.`
  - edit or delete of a synced edge: `This edge comes from the inventory. Change it in <label of the from node's source>.`
- Description and properties of synced nodes remain editable (same `revision` rules). Creating children under synced
  nodes, hand-made edges to/from them, and attaching documents work as before.

## UI

- Inside / Outside lists: synced nodes show `<kind> · from <label>`.
- Node sheet for a synced node: a line `From <label> · Open <label>` linking to `source.page`; Kind and Name read-only;
  the Move section and Delete are hidden; Description, Properties, Edges (add) and Documents work as before.
- Synced edges show a `synced` badge and no Remove button.
- Diagrams: unchanged.

## Testing

- **Migration:** link/layer CHECK, one-node-per-row index, deferred foreign keys (a delete without a sync fails at
  commit), version bump and notify per statement, down and up again, `run.sh` chain.
- **Sync (`graph_sync`, against the `store` fixture):** create for each kind; rename; deployment moved to another server;
  environment rename renames its deployment nodes; service rename renames deployments without a runtime name; each
  relation table makes and removes its synced edge; suffix rule on a clash (create and rename); delete with and without
  children/documents; archived code projects; hand-made edge with the same ends left alone; a second run changes
  nothing (no revision bumps); repair of a node changed behind the sync's back.
- **In-memory graph:** builds from the database; a write bumps the version and the next `get` rebuilds; unchanged
  version returns the same snapshot object; views and search from the snapshot match the old SQL results.
- **Through the real APIs:** save a service with two deployments and two environments in Services → nodes and edges in
  `/api/graph`; change a deployment's server; delete the service whose node has a hand-made child → node kept, unlinked;
  delete a server with no deployments.
- **Graph API:** the four refusals with their messages; description/property edit of a synced node allowed; `source` and
  `synced` fields; `includes` accepted as an edge type.
- **UI (Vitest):** source line and link, read-only Kind/Name, hidden Move/Delete, synced badge without Remove, list
  badge.

## Docs

`docs/user-guide.md` §4.20 (what syncs, what is read-only, the delete rule); `docs/migrations.md` 0093;
`deploy/README.md` upgrade note (the migration fills the graph from the existing inventory).

## Revision 3 — environments contain their servers (2026-10-10, after merge ce659ae)

The user wants the infrastructure view as a strict tree: **Environment › Server › Deployment**. A server can belong to
several environments, and a node has one parent, so a server gets **one node per environment it is in** (option 1,
chosen over a single node shown inside several environments).

- **Placements.** A server is placed under an environment when it is a member (`environment_servers`) or has a deployment
  in that environment (`service_deployments.environment_id`). It is placed at the top level when it is in no environment
  at all, or has a deployment with no environment (a legacy free-text label).
- **Server copy nodes.** A server's node is identified by the pair (server, environment): `server_id` plus
  `environment_id` (NULL for the top-level copy). Each copy is named like the server (suffix rule among its siblings),
  shows "From Servers", and is read-only for name, kind and place like any synced node. Environment nodes keep
  `environment_id` alone.
- **Deployments** sit under the copy of their server for their environment (the top-level copy when the deployment has no
  environment).
- **Edges.** `includes` from an environment to a server is dropped (containment says it). `includes` from an environment
  to a service stays (the cross-layer link to the Software view).
- **Renames and deletes.** Renaming a server renames every copy. Deleting a server releases every copy (delete rule; note
  "Removed from Servers"). Removing a server from an environment releases that copy (note "Removed from Environments").
- **Moving instead of releasing.** When a server's copy is no longer wanted where it is but the same server needs a copy
  that does not exist yet, the existing node is moved there (its children and documents come along) instead of being
  released and a new one made. This also turns each existing one-per-server node into the copy for the server's first
  environment (alphabetical) on the first sync after the upgrade.
- **Schema (`0094_graph_environment_tree`).** A node may carry `server_id` together with `environment_id` (and no other
  link); the one-node-per-server index becomes one node per (server, environment) pair (NULLS NOT DISTINCT); the
  one-node-per-environment index only counts nodes without `server_id`. The down migration unlinks every copy that has an
  environment (both links cleared, nodes kept as hand-made) before restoring the old indexes and check.
- **Creation order** in the sync: environments, then servers, then services, code projects, deployments.
