# Inventory map — design

Date: 2026-10-10
Status: approved in conversation; supersedes the stored system graph
(`2026-10-10-system-graph-design.md`, `2026-10-10-graph-inventory-sync-design.md`).

## Why

The System map was a stored graph (`kg_nodes`, `kg_edges`) kept in step with
the inventory by a reconcile (`graph_sync`). Almost every infrastructure node
was a read-only copy of an inventory row, and most of the code existed to keep
that copy in step (server copies per environment, moves, name parking,
releases). The business, software and data layers had no rows behind them and
were not used.

The inventory already holds most of what a map needs:

| Connection | Table today |
|---|---|
| environment contains server | `environment_servers` |
| environment contains service | `environment_services` |
| service runs on server in an environment | `service_deployments` |
| service depends on service | `service_dependencies` |
| service is built from a code project | `service_repositories` |

So the model becomes: **four inventory entry types — environment, server,
service, code project — and one table of connections between them.** The map
is computed from these tables on every request; nothing about the map is
stored.

## Intent

- The inventory is the whole model. No separate business/data/graph nodes.
- Connections between entries are made and removed in the UI, on the map.
- A connection is a row in a mapping table that carries attributes.
- Later (not in this change): an agent proposes connections; the operator
  accepts or rejects them. The table is shaped so a `status` column is all
  that step adds.

## 1. `ka.inventory_links`

| Column | Rule |
|---|---|
| `id uuid` PK, `project_id uuid NOT NULL` | `UNIQUE (id, project_id)` |
| `from_service_id`, `from_server_id`, `from_environment_id`, `from_code_project_id` | nullable; CHECK exactly one is set |
| `to_service_id`, `to_server_id`, `to_environment_id`, `to_code_project_id` | nullable; CHECK exactly one is set |
| `kind text NOT NULL` | CHECK `kind IN ('depends_on','built_from','connects_to')` |
| `description text NOT NULL DEFAULT ''` | CHECK `length(description) <= 4000` |
| `attributes jsonb NOT NULL DEFAULT '{}'` | CHECK it is a JSON object; values are strings (enforced by the API) |
| `revision integer NOT NULL DEFAULT 1` | CHECK `> 0`; edits are conflict-checked like other inventory rows |
| `created_at`, `updated_at` | `touch_updated_at` trigger |

- Every entry column is a composite FK `(x_id, project_id)` to its table
  (`services`, `servers`, `environments`, `repositories` for code projects)
  with `ON DELETE CASCADE`: both ends are in the link's project, and deleting
  an entry removes its links.
- No self-links: CHECK that the from entry and the to entry differ.
- One link per (from, to, kind): a unique index over the from columns, the to
  columns and `kind`, `NULLS NOT DISTINCT`.
- Direction matters; mutual links are allowed.
- Indexes on each entry column so "links of this entry" is cheap.

Allowed pairs, enforced by the API (422 with a plain message otherwise):

| Kind | From | To |
|---|---|---|
| `depends_on` | service | service |
| `built_from` | service | code project |
| `connects_to` | any entry | any entry |

`attributes` is an object of at most 20 string → string pairs; keys are
1–64 characters, values at most 500 characters.

## 2. `ka.inventory_documents`

Attaches an existing document to an entry.

- `service_id`, `server_id`, `environment_id`, `code_project_id`: nullable,
  CHECK exactly one set; composite FKs to the entry tables with
  `ON DELETE CASCADE`.
- `document_id`, `project_id`, `created_at`; FK `(document_id, project_id)` to
  `documents` with `ON DELETE CASCADE`.
- Unique per (entry, document).
- Deleting an entry or a document removes the attachment, never the document.

## 3. The map

Built per request: read the inventory rows of the project, build a networkx
graph in memory, cut the requested view, render it. No cache, no version
table, no sync.

### Views

| View | Nodes | Containment | Lines |
|---|---|---|---|
| `infrastructure` | environments, servers, services | environment › server › service | `connects_to` links between these entries |
| `services` | services, code projects | none (flat) | `depends_on`, `built_from`, and `connects_to` links between services and code projects |

Infrastructure placement:

- An environment is a top-level node.
- A server appears under each environment it belongs to (`environment_servers`)
  or has a deployment in. Each placement is a separate map node whose id is
  derived from (environment, server); the entry behind it is the same server.
- A server with no environment, or with a deployment whose environment is
  NULL, also appears at the top level.
- A service appears under a server for each deployment on that server in that
  environment (environment NULL → under the top-level server).
- A service that belongs to an environment (`environment_services`) but has no
  deployment there appears directly under the environment.
- A service with no environment and no deployment is not in this view (it is
  in the services view).

A link between two entries is drawn between every pair of their placements in
the current view; at a level above them it rolls up to their nearest visible
containers as one line with a count, as the stored graph did.

### Kept from the System map

Drill-in by node, breadcrumb, boundary nodes, roll-up, outputs JSON / Mermaid
/ D2 / SVG with layout tala / elk / dagre, and search across all four entry
types. The view response keeps the current shape (`nodes`, `edges`,
`breadcrumb`, `boundary`, `root`, `depth`), with each node carrying
`entry_kind` and `entry_id` so the UI can open the entry, its links and its
documents.

## 4. API

- `GET /api/map/view/{infrastructure|services}?root=&depth=&format=&layout=`
- `GET /api/map/search?q=`
- `GET /api/inventory/links?entry_kind=&entry_id=` — all links, or those of
  one entry (either end).
- `POST /api/inventory/links` — create; 422 for a pair the kind does not allow,
  a self-link or bad attributes; 404 for an entry outside the project; 409 for
  a duplicate.
- `PUT /api/inventory/links/{id}` — description and attributes, with
  `revision`; 409 on a stale revision. From, to and kind are fixed; change
  them by removing and re-adding.
- `DELETE /api/inventory/links/{id}`
- `GET /api/inventory/documents?entry_kind=&entry_id=`,
  `POST /api/inventory/documents`, `DELETE /api/inventory/documents/{id}`

All under the existing admin guard.

## 5. Readers of the old tables

Each switches to `inventory_links` with its output unchanged:

- Services API and page: `dependencies` and `repository_ids` read and write
  `depends_on` and `built_from` links.
- Document generation: the "supplied relationships".
- Agent wizard and agent run resources: service dependencies and code
  projects.
- The `service_assets` view: recreated over `inventory_links` with the same
  columns.

Document generation and agent runs also receive `connects_to` links, labelled
as such.

## 6. Retiring the stored graph

Migration 0095:

- Up: create `inventory_links` and `inventory_documents`; copy
  `service_dependencies` → `depends_on` and `service_repositories` →
  `built_from`; move attachments from `kg_node_documents` to the entry behind
  the node (a deployment node's attachments go to its service); recreate
  `service_assets`; drop `kg_node_documents`, `kg_edges`, `kg_nodes`,
  `kg_graph_version` and their triggers and functions, the link columns, and
  `service_dependencies`, `service_repositories`.
- Attachments on hand-drawn nodes (no inventory link) are dropped. The
  documents remain in Documents.
- Down: refuse while any `connects_to` link, any non-empty `attributes`, or
  any `inventory_documents` row exists (the old schema cannot hold them).
  Otherwise recreate the 0094 schema (graph tables empty), copy `depends_on`
  and `built_from` back, recreate the old `service_assets`, drop the new
  tables. After a rollback, the old code's start-up sync refills the graph
  with inventory nodes.

admin-api loses `graph_sync`, the graph version tracking in `graph_memory`,
the graph node/edge write endpoints, the "the inventory owns this" refusals
and `graph_clash`, and every call to the sync from inventory writes and
start-up. The rendering (`system_graph_render`) and search are kept and fed
from the inventory.

## 7. UI — System map page

- An Infrastructure / Services switch replaces the layer picker.
- Selecting a node opens a side panel: the entry's name and kind with
  "Open in Servers / Services / Environments / Code projects", its links
  (kind, other end, description, attributes) and its documents.
- "Connect to…" opens a dialog: target entry (searchable), kind (only the
  kinds valid for the pair), description, attributes. Links can be edited and
  removed from the panel. Attach / Detach for documents.
- The node create / edit / move / delete forms are removed.
- The Services page keeps its dependency and code project fields.

## 8. Docs

User guide §4.20 rewritten for the inventory map, with an upgrade note
(hand-drawn nodes and their attachments are gone; documents remain).
`docs/migrations.md` gets a 0095 section. The two stored-graph specs get a
"Superseded by" note at the top.

## 9. Testing

- Migration 0095: rows move both ways; attachments move to the right entry;
  hand-drawn attachments dropped and documents kept; down refuses with
  `connects_to`, attributes or attachments present; down → up round trip.
- Links API: allowed pairs per kind; self-link, duplicate, unknown and
  cross-project entries refused; attribute limits; stale revision 409;
  deleting an entry removes its links.
- Documents API: attach, detach, duplicate, cascade on entry and document
  delete.
- Map: placement rules above (environment membership, deployment,
  NULL-environment deployment, unassigned server, service without
  deployment); roll-up with counts; drill-in and breadcrumb; services view;
  search; every output format.
- Existing Services API, document generation and agent resource tests pass
  unchanged.
- UI: view switch, side panel, Connect to dialog (kinds filtered by pair),
  edit with conflict, remove, attach/detach.

## Not in scope

- Agent-proposed links (`status` column, review UI) — the next step.
- Creating entries from the map.
- Generated (LLM / Diátaxis) documents from the map.
