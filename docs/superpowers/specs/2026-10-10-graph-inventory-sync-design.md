# System graph inventory sync — design

Date: 2026-10-10. Status: approved in conversation, section by section. Follows
`2026-10-10-system-graph-design.md` (step 1, merged as 8f81e5e); this is roadmap step 3 brought forward (LLM/Diátaxis
output, step 2, is postponed).

## Why

Servers and services added in the inventory do not appear in the system graph; today every node is made by hand. The
inventory (Servers, Services with their deployments, Environments, Code projects) stays the source of truth, and the
graph must follow it automatically: same transaction, whichever page or code path wrote the row.

## Decisions

- **Typed links.** `kg_nodes` gets nullable foreign keys `service_id`, `server_id`, `deployment_id`, `environment_id`,
  `repository_id`; at most one per node; one node per inventory row; the link must match the node's layer.
- **Database triggers do the sync** (not API code): inventory rows are written from several places (services API with
  its deployments, servers API, environments API, migrations), and only triggers cover every path. Each trigger runs in
  the writer's transaction, so the inventory and the graph never disagree.
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

- Foreign keys use `(x, project_id)` so a link can never cross projects, and `ON DELETE SET NULL (x)` as a backstop (the
  delete trigger decides first; see Sync).
- `CHECK (num_nonnulls(service_id, server_id, deployment_id, environment_id, repository_id) <= 1)`.
- `CHECK` that a set link matches the layer (service/repository → `software`; server/deployment/environment →
  `infrastructure`).
- A unique partial index per column (`WHERE x IS NOT NULL`): one node per inventory row.

### `ka.kg_edges`

- New column `synced boolean NOT NULL DEFAULT false`. Sync-made edges have `synced = true`.
- Edge type list gains `includes` (the type CHECK is replaced; diagrams draw it like the other types).

### Down migration

Deletes synced edges, deletes nodes that are linked and have no children or documents, clears the rest of the links,
drops the triggers, functions, columns and indexes, and restores the old edge-type CHECK (any remaining `includes` edge
is deleted first). `db/tests/run.sh` gets the 0093 down/up checks; the rollback chain still ends at version 83.

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

## Sync (triggers, `AFTER INSERT OR UPDATE OR DELETE … FOR EACH ROW`)

All sync functions take the same per-project advisory lock as `kg_nodes_place`
(`pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(project_id::text))`) before touching the graph, so inventory
saves and graph edits serialise the same way.

| Change | Effect |
|---|---|
| server / service / environment / repository inserted | create its node at the top of its layer, linked |
| … name updated | rename its node (suffix rule) |
| deployment inserted | create its node under the server's node; `instance_of` edge to the service's node |
| deployment `server_id` updated | move its node under the new server's node |
| deployment `runtime_name`, `environment` updated | rename its node (environment renames already rewrite the `environment` label of every deployment, so deployment names follow) |
| `service_dependencies` insert / delete | add / remove the `depends_on` edge |
| `service_repositories` insert / delete | add / remove the `uses` edge |
| `environment_servers`, `environment_services` insert / delete | add / remove the `includes` edge |
| any linked row deleted | its synced edges are deleted; then its node is deleted if it has no children and no documents, otherwise its link is cleared and `Removed from <Servers / Services / Services (deployment) / Environments / Code projects> on <YYYY-MM-DD>.` is appended to its description (on a new line; description truncated from the front if it would exceed 4000 characters) |

- A node missing when a trigger expects it (e.g. a server's node was unlinked earlier) is created on demand; an edge
  whose end node is missing is skipped.
- Cascading deletes (an environment deleted → its `environment_servers` rows) fire the row triggers; the order is safe
  because edge removal tolerates already-deleted nodes.
- Deleting a server is already refused by the API while deployments use it, so deployment nodes are always handled
  first.

**Backfill.** The migration creates nodes and synced edges for every existing inventory row, in this order: servers,
environments, services, repositories, deployments, then the edge tables. The suffix rule applies to clashes with
hand-made nodes made since 0092.

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

- **Migration / triggers (`db` tests via the `store` fixture):** each row of the Sync table; deployment moved to another
  server; environment rename renames its deployment nodes; suffix rule on a clash with a hand-made node (create and
  rename); delete with and without children/documents (node gone vs unlinked with the note); link/layer CHECK and
  one-node-per-row index; backfill of rows that existed before 0093; down migration and up again; `run.sh` chain.
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
