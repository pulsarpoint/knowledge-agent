# System graph (step 1: store, API, documents, diagrams) — design

Date: 2026-10-10. Status: approved in conversation, section by section.

## Why

We want one model of the whole system — business, software, data and infrastructure — that people and later agents can
build up, attach documents to, and view in several ways. One big graph is unreadable, so the model is **graph in graph**:
every node is itself a graph of its children, and a view shows one node's inside at a time. Viewing is always an
**output generated from the graph** (JSON, Mermaid, D2, an SVG rendered by D2); later steps add LLM documents and
Diátaxis documentation from the same graph.

## Roadmap (only step 1 is in this spec)

1. **This spec:** graph tables, API to read a layer as a graph and to edit nodes/edges, attach existing documents,
   Mermaid/D2/SVG output, a System map page.
2. LLM document and Diátaxis output: documentation generation gets an input kind `graph` (a node's subgraph plus its
   attached documents).
3. Sync from the inventory (services, servers, deployments, dependencies become read-only nodes/edges; the inventory
   stays the source of truth). Importers: code graph → modules, migrations/catalog → tables.
4. Node agent: one built-in "Ask this node" agent for every node, context = the node's subtree, edges and documents;
   a `system_graph` tool for other agents reusing the read API.
5. Agents propose graph changes with evidence; a person approves.
6. Watchers (events/handlers) keep the graph current; node history (valid_from/valid_to) arrives here.

## Decisions

- **Four layers**: `business`, `software`, `data`, `infrastructure`. A layer is a viewpoint with its own nesting tree,
  not a zoom level. Layers connect through edges (`instance_of`, `runs_on`, `implements`, `stores`).
- **One parent per node, within its layer.** No node lives in two trees; shared things (a database, a library) are
  their own nodes connected by edges. The project is the implicit root of every layer (`parent_id` NULL = top level).
- **Environment is a filter, not a level** (a server can belong to several environments). Not used in step 1 because
  nothing syncs yet; noted so step 3 keeps the infrastructure tree as `server › instance`.
- **Edges are stored at the deepest level anyone knows them.** Higher levels are always computed, never stored.
- **Attachments reuse `ka.documents`** (versioned documents that already exist) through a link table, like
  `document_services` and `document_servers`.
- **Storage of nesting:** `parent_id` + recursive queries. `ltree` and closure tables are faster to read but cost more
  on moves and in code; not needed at our size.
- **Same access and conventions as the inventory:** the deployment's project, admin-only, audited, `revision` checks.
- **Diagrams:** D2 v0.9.0 bundles the TALA, ELK and dagre layout engines (TALA is open source under MPL-2.0 since
  2026-09-07); the user picks the layout per view.
- Not in step 1: inventory sync, importers, node agent, agent tools, watchers, node history, LLM/Diátaxis output.

## Data model (`0092_system_graph`)

### `ka.kg_nodes`

| column | type / rule |
|---|---|
| `id` | uuid PK, default `gen_random_uuid()` |
| `project_id` | uuid NOT NULL → `projects(id)` |
| `layer` | text NOT NULL, CHECK in (`business`,`software`,`data`,`infrastructure`) |
| `kind` | `nonblank`, ≤ 60 characters, free text (`service`, `module`, `server`, `table`, `capability`, …) |
| `name` | `nonblank`, ≤ 160 characters |
| `description` | text NOT NULL DEFAULT '', ≤ 4000 characters |
| `parent_id` | uuid NULL |
| `properties` | jsonb NOT NULL DEFAULT `{}`, must be an object, ≤ 16 KB as text |
| `revision` | integer NOT NULL DEFAULT 1, > 0 |
| `created_by` | uuid → `principals(id)` |
| `created_at`, `updated_at` | timestamptz, `touch_updated_at` trigger |

- `UNIQUE (id, project_id)` and `UNIQUE (id, project_id, layer)` as foreign-key targets.
- `FOREIGN KEY (parent_id, project_id, layer) REFERENCES kg_nodes(id, project_id, layer)` — the parent is in the same
  project **and** the same layer. `ON DELETE RESTRICT`: a node with children cannot be deleted.
- Sibling names are unique, case-insensitive:
  `CREATE UNIQUE INDEX … ON kg_nodes (project_id, layer, parent_id, lower(name)) NULLS NOT DISTINCT`.
- Index on `parent_id`.
- **Trigger on insert and on `parent_id` change** (takes `pg_advisory_xact_lock` keyed by the project so concurrent
  moves cannot build a cycle together):
  - refuses a parent that is the node itself or one of its descendants (`check_violation`, message
    "A node cannot be moved inside itself");
  - refuses when the node's new depth plus the height of its subtree exceeds **12** (top level = depth 1).

### `ka.kg_edges`

| column | type / rule |
|---|---|
| `id` | uuid PK |
| `project_id` | uuid NOT NULL |
| `from_id`, `to_id` | uuid NOT NULL; `FOREIGN KEY (x, project_id) REFERENCES kg_nodes(id, project_id) ON DELETE CASCADE` |
| `type` | text NOT NULL, CHECK in (`calls`,`uses`,`depends_on`,`reads`,`writes`,`publishes`,`subscribes`,`instance_of`,`runs_on`,`implements`,`stores`) |
| `description` | text NOT NULL DEFAULT '', ≤ 4000 |
| `revision`, `created_by`, `created_at`, `updated_at` | as on nodes |

- `UNIQUE (from_id, to_id, type)`; `CHECK (from_id <> to_id)`; indexes on `from_id` and `to_id`.
- Edges may join any two nodes in the project, in the same or different layers.
- Trigger on insert refuses an edge between a node and its own ancestor or descendant (containment
  already says that). A later **move** may make an existing edge connect ancestor and descendant; that is allowed and
  the view counts such an edge as internal (see roll-up).
- Adding an edge type is a migration (each type has a diagram style).

### `ka.kg_node_documents`

`(node_id, document_id, project_id)`, PK `(node_id, document_id)`, both foreign keys through `project_id`
(`ON DELETE CASCADE` on either side), index on `document_id`. Detaching never deletes the document.

### Down migration

Drops the three tables, the trigger functions and indexes. `db/tests/run.sh` rollback chain is extended.

## Views and roll-up (`system_graph.py`)

A view is `(layer, root, depth)`. `root` NULL = the top of the layer. `depth` 1–5, default 1.

Computed in Python: load every node of the project (`id, parent_id, layer, kind, name, description`) and every edge
with at least one end inside the root's subtree; then:

1. **Visible nodes:** descendants of the root down to `depth` levels (the root itself is not drawn; it is the frame).
2. **Representative:** each node inside the subtree maps to its deepest visible ancestor (or itself). Hidden
   descendants are counted on their representative as `child_count` (direct children) and the view marks nodes whose
   children are not shown as expandable.
3. **Edges inside:** map both ends to representatives. Same representative → counted as `internal_edges` on that node,
   not drawn. Otherwise group by `(from, to, type)` → one edge with `count` and up to 20 underlying `edge_ids`.
4. **Boundary:** an edge with one end outside the subtree. The outside end is shown as the **child of the lowest common
   ancestor** of the root and the outside node, treating the project as the common root of all layers. Same layer:
   viewing `admin-api`, a call into a `repository-worker` module shows as `repository-worker`. Other layer: the outside
   node's top-level node in its layer (e.g. `prod-1`), with its layer. Boundary nodes are grouped and counted the same
   way as inside edges.
5. **Limit:** more than **500** visible nodes (inside + boundary) → 422 "Too many nodes; choose a deeper root or a
   smaller depth".

JSON shape:

```json
{
  "layer": "software",
  "root": {"id": "…", "name": "admin-api", "kind": "service"} ,
  "breadcrumb": [{"id": "…", "name": "admin-api"}],
  "depth": 1,
  "nodes": [{"id": "…", "parent_id": "…", "name": "…", "kind": "…", "description": "…",
             "child_count": 3, "expandable": true, "internal_edges": 2,
             "documents": [{"id": "…", "slug": "…", "title": "…"}]}],
  "boundary": [{"id": "…", "name": "prod-1", "kind": "server", "layer": "infrastructure"}],
  "edges": [{"from": "…", "to": "…", "type": "calls", "count": 3, "edge_ids": ["…"]}]
}
```

`breadcrumb` is the path from the top of the layer down to the root, inclusive. `root` is null and `breadcrumb` empty
at the top of a layer.

## Diagram formats (`system_graph_render.py`)

All formats are produced from the JSON view.

- **Keys** are generated (`n1`, `n2`, …), never names. **Labels** are the node name (plus kind in smaller text where
  the format allows), escaped so that any name renders literally: quotes, backslashes, braces, `->`, `;`, `#`, `$`/`${…}`
  substitutions, newlines (replaced by spaces). Mermaid labels use `"…"` with `#quot;` entities.
- **Mermaid:** `flowchart LR`. A visible node that has visible children becomes a `subgraph`; boundary nodes go in a
  subgraph "Outside". Edge label `type` or `type ×N`.
- **D2:** visible nodes with visible children become containers. Shapes by `kind`: `database`, `table` → `cylinder`;
  `queue` → `queue`; `person`, `actor` → `person`; otherwise `rectangle`. Boundary nodes `style.stroke-dash: 4` in an
  `Outside` container. Edge labels as in Mermaid. No `icon`, `link`, `import` or `vars` are ever emitted.
- **SVG:** the D2 text is piped to `d2 --layout=<tala|elk|dagre> - -` as a subprocess with a **20 s** timeout and no
  shell. Non-zero exit or timeout → 502 with d2's stderr (trimmed to 2000 characters). `d2` missing on PATH → 503
  "d2 is not installed". The response is `image/svg+xml`.

## API (`system_graph_api.py`, admin-only, under `/api/graph`)

| method & path | does |
|---|---|
| `GET /api/graph/view/{layer}?root=&depth=1&format=json\|mermaid\|d2\|svg&layout=tala\|elk\|dagre` | A view (above; `/view/` keeps it apart from `/nodes`). `mermaid`/`d2` return `text/plain`. Unknown root, or a root from another layer → 404 / 422 |
| `GET /api/graph/nodes?layer=&q=&limit=50` | Search by name (case-insensitive substring), for pickers; each result includes its breadcrumb path |
| `GET /api/graph/nodes/{id}` | Node + breadcrumb + children + incoming and outgoing edges (with the other end's name and layer) + documents |
| `POST /api/graph/nodes` | Create: `layer`, `kind`, `name`, `parent_id?`, `description?`, `properties?` |
| `PUT /api/graph/nodes/{id}` | Edit `kind`, `name`, `description`, `properties`, `parent_id` (move) with `revision`. `layer` cannot change |
| `DELETE /api/graph/nodes/{id}` | 409 "Move or delete its children first" while it has children |
| `POST /api/graph/edges` | Create: `from_id`, `to_id`, `type`, `description?` |
| `PUT /api/graph/edges/{id}` | Edit `description` with `revision` (ends and type are fixed; delete and recreate instead) |
| `DELETE /api/graph/edges/{id}` | |
| `PUT /api/graph/nodes/{id}/documents/{document_id}` | Attach (idempotent) |
| `DELETE /api/graph/nodes/{id}/documents/{document_id}` | Detach (idempotent) |

Errors are mapped to clear messages: stale `revision` → 409; duplicate sibling name → 409 "A node named … already
exists here"; parent in another layer or project → 422; cycle or depth > 12 → 422 with the trigger's message; duplicate
edge → 409; ancestor edge → 422. Request bodies are Pydantic models with the same limits as the columns. Every change
writes an audit row like the inventory (`graph.node.created|updated|moved|deleted`, `graph.edge.created|updated|deleted`,
`graph.document.attached|detached`).

## Packaging

`deploy/Dockerfile.admin-api` downloads the pinned **D2 v0.9.0** release archive for `TARGETARCH` (amd64, arm64),
verifies its sha256 (values pinned in the Dockerfile), and installs `d2` to `/usr/local/bin`. Outside Docker, `svg`
returns 503 and the other formats work.

## UI (`/system-map`)

In the navigation next to Services, Servers and Environments.

- Layer tabs (Business · Software · Data · Infrastructure), breadcrumb, Depth (1–5), View (Diagram · Mermaid · D2 ·
  JSON), Layout (tala · elk · dagre; only for Diagram), **New node**. All of these live in the URL query, so a view is a
  shareable link.
- **Diagram** shows the SVG as `<img>` (so it cannot run scripts). **Mermaid** uses the existing `Mermaid` component.
  **D2** and **JSON** show the source with a Copy button. An error from the API (503/502/422) is shown in place of the
  diagram; the side list still works.
- Side list: **Inside** (visible children; → makes one the root; "+N inside" when it has children) and **Outside**
  (boundary nodes with layer and edge counts; → opens that node in its own layer).
- Clicking a node name opens the **node sheet**: Details (kind, name, description, properties as JSON; Move with a
  parent picker limited to the same layer; Delete), Edges (list in/out; Add edge = type + target picker searching all
  layers; Remove), Documents (attach from existing documents via a picker; detach; each links to its document page).
- **New node** creates under the current root in the current layer.
- Empty layer: an empty state with **New node**.

## Testing

- **Migration (`db/tests`):** parent in another layer refused; cycle refused (direct and through a descendant); depth
  13 refused, including by moving a deep subtree; case-insensitive duplicate sibling name refused, also at top level;
  delete with children refused; self-edge and ancestor/descendant edge refused; cross-layer edge allowed; document link
  cascades; down migration; rollback chain.
- **Roll-up (pure unit tests):** collapse to visible ancestor; grouping with counts; internal edges hidden and counted;
  boundary in the same layer (lowest common ancestor) and across layers; edges made ancestor/descendant by a move are
  internal; the 500-node limit; depth 1–5.
- **Rendering:** nesting in Mermaid and D2; hostile names (`a"; x -> y`, `}`, `${x}`, `#`, newline) render as literal
  labels and produce no extra shapes or edges; shapes by kind; SVG subprocess mocked for success, non-zero exit,
  timeout and missing binary; one real render per layout, skipped when `d2` is not installed.
- **API:** CRUD, move errors, revision conflict, duplicate edge, idempotent attach/detach, documents from another
  project refused, audit rows, admin-only access.
- **UI (Vitest):** layer tabs and breadcrumb navigation, URL state round-trip, view switching, error state, node sheet
  forms (create, move, add edge, attach document).

## Docs

`docs/user-guide.md` gets a System map section; `docs/migrations.md` gets 0092; `deploy/README.md` notes that the
admin-api image now contains `d2`.
