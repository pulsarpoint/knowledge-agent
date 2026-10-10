# System graph (step 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A system graph per project — four layers of nested nodes (graph in graph), typed edges, attached documents —
with an API that returns one node's inside as JSON, Mermaid, D2 or an SVG drawn by `d2` (tala/elk/dagre), and a
System map page to browse and edit it.

**Architecture:** Migration 0092 adds `ka.kg_nodes` (one parent per node within its layer, cycle/depth trigger),
`ka.kg_edges` and `ka.kg_node_documents`. `system_graph.py` is a pure module that computes a view (collapse to visible
nodes, roll edges up, boundary by lowest common ancestor); `system_graph_render.py` turns a view into Mermaid/D2 text
and runs `d2` for SVG; `system_graph_api.py` (under the admin-only `/api` router) loads rows, edits them and audits.
The UI page `/system-map` keeps its state in the URL and edits through a node sheet.

**Tech Stack:** FastAPI, psycopg3 (dict rows), golang-migrate SQL, PostgreSQL 17, D2 v0.9.0 (bundles TALA, ELK, dagre),
React Router 7, shadcn/ui, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-10-system-graph-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/system-graph` (branch `feature/system-graph`). Never run make targets;
  never touch `.env`, `.local` or the running Docker stack's database (tests use their own disposable databases).
- Never `git stash`; never `git add .superpowers`. Every commit ends with the trailer
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd services/admin-api && uv run pytest -q tests/<file>`. UI: `cd services/admin-ui && npx vitest run
  <file>`; UI tasks also run `npx react-router typegen && npx tsc --noEmit`.
- The app maps request validation errors to **400**; error bodies are `{"error": {"message": ...}}`; an unhandled
  `psycopg.Error` becomes 503, so every database refusal the user can cause must be caught and mapped.
- Migrations: `BEGIN; SET search_path = ka, public; … COMMIT;`, up and down files.
- Layers: `business`, `software`, `data`, `infrastructure`. Edge types: `calls`, `uses`, `depends_on`, `reads`,
  `writes`, `publishes`, `subscribes`, `instance_of`, `runs_on`, `implements`, `stores`.
- Limits: kind ≤ 60, name ≤ 160, description ≤ 4000, properties a JSON object ≤ 16384 characters as text; nesting depth
  ≤ 12 (top level = 1); view depth 1–5 (default 1); a view shows at most 500 nodes (inside + boundary); an aggregated edge
  lists at most 20 `edge_ids`; SVG timeout 20 s; d2 error text trimmed to 2000 characters.
- Messages (exact): `Node not found.` · `Parent not found.` · `Edge not found.` · `Document not found.` ·
  `This node was changed by someone else. Reload and try again.` ·
  `This edge was changed by someone else. Reload and try again.` · `Choose a parent in the same layer.` ·
  `Move or delete its children first.` · `An edge needs two different nodes.` · `This edge already exists.` ·
  `A node named "<name>" already exists here.` · `A node cannot be moved inside itself.` ·
  `Too deep: a layer has at most 12 levels.` · `An edge cannot join a node and a node that contains it.` ·
  `Too many nodes; choose a deeper root or a smaller depth.` · `d2 is not installed.` ·
  `That node is in the <layer> layer.`
- Audit actions: `graph.node.created|updated|moved|deleted` (resource type `graph_node`),
  `graph.edge.created|updated|deleted` (`graph_edge`), `graph.document.attached|detached` (`graph_node`).
- D2 is pinned to **v0.9.0** from `https://github.com/d2lang/d2/releases/download/v0.9.0/`:
  `d2-v0.9.0-linux-amd64.tar.gz` sha256 `5669ddc46b99e942cc96078f4a4e36d5e62103348f4c05179ede27802fdd87a9`,
  `d2-v0.9.0-linux-arm64.tar.gz` sha256 `ac2c028697199479acb321db1e3d68caee9f2ba492ed73caa3cd13f3829bf913`; the archive
  holds `d2-v0.9.0/bin/d2`.

## Review Focus

1. A node named with diagram syntax — `a"; x -> y`, `${x}`, `}`, `#`, a newline — must render as that literal label in
   Mermaid, D2 and SVG and never add shapes or edges (D2 treats `${…}` in double quotes as a variable, so `$` must be
   escaped) (Task 3, `test_hostile_names_are_literal_labels` and `test_real_render_keeps_hostile_labels`).
2. Moving a node into its own subtree, or making a branch deeper than 12 levels by a move, must answer 422 with the
   trigger's sentence, not 503 (Task 4, `test_moves_are_checked`).
3. The root shown in the URL is deleted by someone else: the view answers 404 `Node not found.` and the page shows that
   message with a way back to the layer's top (Task 2 `test_unknown_root`, Task 8 `shows an error when the root is gone`).
4. `d2` missing or failing: Diagram shows the API's message, while Inside/Outside, Mermaid, D2 and JSON keep working
   (Task 3 `test_svg_*`, Task 8 `shows the diagram error but keeps the lists`).
5. A NUL character in a name, description or nested property must answer 400 naming the field, not 503
   (Task 4, `test_nul_characters_are_refused`).

## File Structure

- `db/migrations/0092_system_graph.{up,down}.sql` — the three tables and two trigger functions.
- `db/tests/run.sh` — 0092 down step and up check.
- `services/admin-api/src/knowledge_admin/system_graph.py` — pure view computation (`build_view`, `ViewError`).
- `services/admin-api/src/knowledge_admin/system_graph_render.py` — `mermaid`, `d2`, `svg`, `RenderError`.
- `services/admin-api/src/knowledge_admin/system_graph_api.py` — `/api/graph/...` routes.
- `services/admin-api/src/knowledge_admin/api.py` — include the router.
- `services/admin-api/tests/test_system_graph_migration.py`, `test_system_graph.py`, `test_system_graph_render.py`,
  `test_system_graph_api.py`.
- `deploy/Dockerfile.admin-api` — install pinned `d2`.
- `services/admin-ui/app/lib/systemGraph.ts` — types, URL state, API calls.
- `services/admin-ui/app/components/NodeSheet.tsx` — create / edit / move / delete a node, edges, documents.
- `services/admin-ui/app/components/SystemMapView.tsx` — the page body.
- `services/admin-ui/app/routes/systemMap.tsx`, `app/routes.ts`, `app/components/AdminLayout.tsx` — route and nav.
- Tests: `app/components/__tests__/nodeSheet.test.tsx`, `systemMap.test.tsx`, `adminLayout.test.tsx` (one line).
- Docs: `docs/migrations.md` (Task 1), `deploy/README.md` (Task 6), `docs/user-guide.md` (Task 8).

---

### Task 1: Migration 0092 — nodes, edges, document links

**Files:**
- Create: `db/migrations/0092_system_graph.up.sql`, `db/migrations/0092_system_graph.down.sql`
- Create: `services/admin-api/tests/test_system_graph_migration.py`
- Modify: `db/tests/run.sh`, `docs/migrations.md`

**Interfaces:**
- Produces: `ka.kg_nodes(id uuid, project_id uuid, layer text, kind text, name text, description text, parent_id uuid,
  properties jsonb, revision int, created_by uuid, created_at, updated_at)`; `ka.kg_edges(id uuid, project_id uuid,
  from_id uuid, to_id uuid, type text, description text, revision int, created_by uuid, created_at, updated_at)`;
  `ka.kg_node_documents(node_id uuid, document_id uuid, project_id uuid, created_at)`. Trigger errors are
  `check_violation` with **no constraint name** and the exact sentences from Global Constraints; table CHECKs have names.

- [ ] **Step 1: Write the failing test** `services/admin-api/tests/test_system_graph_migration.py`

```python
"""0092: the system graph — nodes nested within one layer, typed edges, links to documents."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_api import _client
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def node(conn, project, name=None, parent=None, layer='software', kind='service', **extra) -> dict:
    values = {'project_id': project, 'layer': layer, 'kind': kind, 'name': name or f'n-{uuid4().hex[:8]}',
              'parent_id': parent, **extra}
    return conn.execute(f"INSERT INTO ka.kg_nodes({','.join(values)}) VALUES ({','.join(['%s'] * len(values))}) RETURNING *",
                        tuple(values.values())).fetchone()


def edge(conn, project, a, b, type='calls') -> dict:
    return conn.execute("""INSERT INTO ka.kg_edges(project_id,from_id,to_id,type) VALUES (%s,%s,%s,%s) RETURNING *""",
                        (project, a, b, type)).fetchone()


def chain(conn, project, length, top=None) -> list[dict]:
    nodes, parent = [], top
    for _ in range(length):
        parent = node(conn, project, parent=parent)['id']
        nodes.append(parent)
    return nodes


def refused(store, error, message=None):
    """Runs the block in its own connection and expects the database to refuse it."""
    class Block:
        def __enter__(self):
            self.conn = store.connect()
            return self.conn

        def __exit__(self, kind, exc, tb):
            self.conn.close()
            assert kind is not None and issubclass(kind, error), f'expected {error.__name__}, got {kind}'
            if message:
                assert exc.diag.message_primary == message and exc.diag.constraint_name is None
            return True
    return Block()


def test_nodes_nest_within_a_layer(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api = node(conn, project, 'admin-api')
        child = node(conn, project, 'Event processor', parent=api['id'], kind='module')
        assert (child['parent_id'], child['revision'], child['properties'], child['description']) == (api['id'], 1, {}, '')
        node(conn, project, 'admin-api', layer='infrastructure', kind='container')  # same name, other layer
        node(conn, project, 'Event processor', parent=child['id'], kind='module')  # same name, other parent
    with refused(store, psycopg.errors.ForeignKeyViolation) as conn:  # the parent is in another layer
        node(conn, project, 'x', parent=api['id'], layer='data')
    with refused(store, psycopg.errors.UniqueViolation) as conn:  # sibling names are unique, case-insensitive
        node(conn, project, 'EVENT PROCESSOR', parent=api['id'])
    with refused(store, psycopg.errors.UniqueViolation) as conn:  # also at the top level
        node(conn, project, 'Admin-API')
    for bad in [{'layer': 'network'}, {'name': ' '}, {'kind': ''}, {'name': 'x' * 161}, {'kind': 'k' * 61},
                {'description': 'd' * 4001}, {'properties': Jsonb([1])}, {'properties': Jsonb({'x': 'y' * 16400})}]:
        with refused(store, psycopg.errors.CheckViolation) as conn:
            node(conn, project, **{'name': f'ok-{uuid4().hex[:6]}', **bad})


def test_cycles_are_refused(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a, b, c = chain(conn, project, 3)
    with refused(store, psycopg.errors.CheckViolation, 'A node cannot be moved inside itself.') as conn:
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (c, a))
    with refused(store, psycopg.errors.CheckViolation, 'A node cannot be moved inside itself.') as conn:
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (a, a))
    with store.connect() as conn:  # moving to the top level or sideways is fine
        conn.execute('UPDATE ka.kg_nodes SET parent_id=NULL WHERE id=%s', (c,))
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (a, c))


def test_depth_is_capped_at_twelve(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        deep = chain(conn, project, 12)
    with refused(store, psycopg.errors.CheckViolation, 'Too deep: a layer has at most 12 levels.') as conn:
        node(conn, project, parent=deep[-1])
    with store.connect() as conn:
        six = chain(conn, project, 6)
        seven = chain(conn, project, 7)
    with refused(store, psycopg.errors.CheckViolation, 'Too deep: a layer has at most 12 levels.') as conn:
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (six[-1], seven[0]))  # 6 + 7 = 13
    with store.connect() as conn:  # 5 + 7 = 12 is fine
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (six[-2], seven[0]))


def test_a_node_with_children_cannot_be_deleted(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        top, child = chain(conn, project, 2)
    with refused(store, psycopg.errors.ForeignKeyViolation) as conn:
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (top,))
    with store.connect() as conn:
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (child,))
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (top,))


def test_edges(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, events = chain(conn, project, 2)
        worker = node(conn, project, 'repository-worker')['id']
        server = node(conn, project, 'prod-1', layer='infrastructure', kind='server')['id']
        made = edge(conn, project, events, worker)
        assert (made['revision'], made['description']) == (1, '')
        edge(conn, project, server, api, 'instance_of')  # across layers
        edge(conn, project, events, worker, 'uses')  # another type between the same nodes
    with refused(store, psycopg.errors.UniqueViolation) as conn:
        edge(conn, project, events, worker)
    with refused(store, psycopg.errors.CheckViolation) as conn:
        edge(conn, project, worker, worker)
    with refused(store, psycopg.errors.CheckViolation) as conn:
        edge(conn, project, worker, api, 'sends')
    for a, b in [(api, events), (events, api)]:  # a node and the node that contains it
        with refused(store, psycopg.errors.CheckViolation, 'An edge cannot join a node and a node that contains it.') as conn:
            edge(conn, project, a, b, 'depends_on')
    with store.connect() as conn:  # a move may leave such an edge; the view counts it as internal
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (events, worker))
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (worker,))
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_edges WHERE %s IN (from_id,to_id)', (worker,)).fetchone()['n'] == 0


def test_document_links_cascade(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    made = client.post('/api/documentation/documents', headers=HEADERS, json={
        'slug': 'runbook', 'title': 'Runbook', 'markdown': '# Runbook\n'})
    assert made.status_code == 201, made.text
    document = made.json()['id']
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a = node(conn, project)['id']
        b = node(conn, project)['id']
        for n in (a, b):
            conn.execute('INSERT INTO ka.kg_node_documents(node_id,document_id,project_id) VALUES (%s,%s,%s)', (n, document, project))
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (a,))
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_node_documents').fetchone()['n'] == 1
    assert client.delete(f'/api/documentation/documents/{document}', headers=HEADERS).status_code in (200, 204)
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_node_documents').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes WHERE id=%s', (b,)).fetchone()['n'] == 1


def test_down_and_up(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a, b = chain(conn, project, 2)
        edge(conn, project, a, node(conn, project)['id'])
        run(conn, '0092_system_graph.down.sql')
        assert conn.execute("SELECT to_regclass('ka.kg_nodes') IS NULL AND to_regclass('ka.kg_edges') IS NULL "
                            "AND to_regclass('ka.kg_node_documents') IS NULL AS gone").fetchone()['gone']
        assert conn.execute("SELECT count(*) AS n FROM pg_proc WHERE proname IN ('kg_nodes_place','kg_edges_check')").fetchone()['n'] == 0
        run(conn, '0092_system_graph.up.sql')
        assert conn.execute("SELECT to_regclass('ka.kg_nodes_sibling_name') IS NOT NULL AS found").fetchone()['found']
```

If `DELETE /api/documentation/documents/{id}` needs a body or a revision in this codebase, read
`services/admin-api/src/knowledge_admin/documents.py` (`@router.delete('/documents/{document_id}')`) and call it the
way `tests/test_documents.py::test_document_versions_filters_links_and_deletion` does; keep the assertion that the link
is gone and the other node stays.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_system_graph_migration.py`
Expected: FAIL (`relation "ka.kg_nodes" does not exist`).

- [ ] **Step 3: Write the migration**

`db/migrations/0092_system_graph.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- The system graph: four layers (viewpoints), each a tree of nodes — graph in graph, every node is a graph of its
-- children. A node has one parent, in the same project and layer (NULL = top level; the project is the implicit root).
-- Edges join any two nodes of the project, in one layer or across layers, and are stored at the deepest level anyone
-- knows them; the API computes higher levels and never stores them.
CREATE TABLE kg_nodes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    layer text NOT NULL CONSTRAINT kg_nodes_layer CHECK (layer IN ('business', 'software', 'data', 'infrastructure')),
    kind nonblank NOT NULL CONSTRAINT kg_nodes_kind CHECK (length(kind) <= 60),
    name nonblank NOT NULL CONSTRAINT kg_nodes_name CHECK (length(name) <= 160),
    description text NOT NULL DEFAULT '' CONSTRAINT kg_nodes_description CHECK (length(description) <= 4000),
    parent_id uuid,
    properties jsonb NOT NULL DEFAULT '{}' CONSTRAINT kg_nodes_properties
        CHECK (jsonb_typeof(properties) = 'object' AND length(properties::text) <= 16384),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, project_id),
    UNIQUE (id, project_id, layer),
    CONSTRAINT kg_nodes_parent_layer FOREIGN KEY (parent_id, project_id, layer)
        REFERENCES kg_nodes(id, project_id, layer) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX kg_nodes_sibling_name ON kg_nodes (project_id, layer, parent_id, lower(name)) NULLS NOT DISTINCT;
CREATE INDEX kg_nodes_children ON kg_nodes (parent_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON kg_nodes FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON COLUMN kg_nodes.kind IS 'Free text such as service, module, server, table, capability; the UI suggests kinds per layer.';
COMMENT ON COLUMN kg_nodes.properties IS 'Extra facts about the node (port, language, …). Operator context, not instructions.';

-- No node inside itself, and no branch deeper than 12 levels (top level = 1). Placing nodes is serialised per project
-- so two concurrent moves cannot build a cycle together. Errors carry no constraint name; the API shows the sentence.
CREATE FUNCTION kg_nodes_place() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    above integer := 0;
    inside boolean := false;
    height integer := 1;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(NEW.project_id::text));
    IF NEW.parent_id IS NOT NULL THEN
        WITH RECURSIVE up(id, parent_id) AS (
            SELECT id, parent_id FROM kg_nodes WHERE id = NEW.parent_id
            UNION ALL
            SELECT n.id, n.parent_id FROM kg_nodes n JOIN up ON n.id = up.parent_id)
        SELECT count(*), coalesce(bool_or(id = NEW.id), false) INTO above, inside FROM up;
        IF inside OR NEW.parent_id = NEW.id THEN
            RAISE EXCEPTION 'A node cannot be moved inside itself.' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        WITH RECURSIVE down(id, level) AS (
            SELECT NEW.id, 1
            UNION ALL
            SELECT n.id, down.level + 1 FROM kg_nodes n JOIN down ON n.parent_id = down.id)
        SELECT max(level) INTO height FROM down;
    END IF;
    IF above + height > 12 THEN
        RAISE EXCEPTION 'Too deep: a layer has at most 12 levels.' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER kg_nodes_place BEFORE INSERT OR UPDATE OF parent_id ON kg_nodes
    FOR EACH ROW EXECUTE FUNCTION kg_nodes_place();

CREATE TABLE kg_edges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    from_id uuid NOT NULL,
    to_id uuid NOT NULL,
    type text NOT NULL CONSTRAINT kg_edges_type CHECK (type IN ('calls', 'uses', 'depends_on', 'reads', 'writes',
        'publishes', 'subscribes', 'instance_of', 'runs_on', 'implements', 'stores')),
    description text NOT NULL DEFAULT '' CONSTRAINT kg_edges_description CHECK (length(description) <= 4000),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT kg_edges_two_nodes CHECK (from_id <> to_id),
    CONSTRAINT kg_edges_once UNIQUE (from_id, to_id, type),
    FOREIGN KEY (from_id, project_id) REFERENCES kg_nodes(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_id, project_id) REFERENCES kg_nodes(id, project_id) ON DELETE CASCADE
);
CREATE INDEX kg_edges_to ON kg_edges (to_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON kg_edges FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE kg_edges IS 'Directed, typed edges between graph nodes. Adding a type is a migration (each type has a diagram style).';

-- Containment already says "inside": an edge between a node and its ancestor or descendant is refused when it is made.
-- A later move may still produce one; views count it as internal. A self-edge is left to kg_edges_two_nodes.
CREATE FUNCTION kg_edges_check() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF NEW.from_id = NEW.to_id THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        WITH RECURSIVE up(id, parent_id) AS (
            SELECT id, parent_id FROM kg_nodes WHERE id = NEW.from_id
            UNION ALL
            SELECT n.id, n.parent_id FROM kg_nodes n JOIN up ON n.id = up.parent_id)
        SELECT 1 FROM up WHERE id = NEW.to_id)
    OR EXISTS (
        WITH RECURSIVE up(id, parent_id) AS (
            SELECT id, parent_id FROM kg_nodes WHERE id = NEW.to_id
            UNION ALL
            SELECT n.id, n.parent_id FROM kg_nodes n JOIN up ON n.id = up.parent_id)
        SELECT 1 FROM up WHERE id = NEW.from_id) THEN
        RAISE EXCEPTION 'An edge cannot join a node and a node that contains it.' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER kg_edges_check BEFORE INSERT ON kg_edges FOR EACH ROW EXECUTE FUNCTION kg_edges_check();

-- Documents attached to a node (the documents themselves live in ka.documents; detaching never deletes them).
CREATE TABLE kg_node_documents (
    node_id uuid NOT NULL,
    document_id uuid NOT NULL,
    project_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id, document_id),
    FOREIGN KEY (node_id, project_id) REFERENCES kg_nodes(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE
);
CREATE INDEX kg_node_documents_document ON kg_node_documents (document_id);

COMMIT;
```

`db/migrations/0092_system_graph.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DROP TABLE kg_node_documents;
DROP TABLE kg_edges;
DROP TABLE kg_nodes;
DROP FUNCTION kg_edges_check();
DROP FUNCTION kg_nodes_place();

COMMIT;
```

In `db/tests/run.sh`, insert right after the second `run_migrate postgres up >> "$test_log" …` line (before the 0091
`down 1` step that checks `manual_removed`):

```sh
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
graph_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.kg_nodes') IS NULL AND to_regclass('ka.kg_edges') IS NULL
        AND to_regclass('ka.kg_node_documents') IS NULL")
[ "$graph_removed" = t ] || { echo 'FAIL: 0092 down left system graph tables' >&2; exit 1; }
```

and after the line `printf '%s\n' 'PASS: events can be made by hand (source manual).'`:

```sh
graph=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.kg_nodes') IS NOT NULL AND to_regclass('ka.kg_edges') IS NOT NULL
        AND to_regclass('ka.kg_node_documents') IS NOT NULL")
[ "$graph" = t ] || { echo 'FAIL: 0092 system graph tables missing' >&2; exit 1; }
printf '%s\n' 'PASS: the system graph has nodes, edges and document links.'
```

The rollback chain still ends at version 83 (one more `down 1` before the existing ones).

In `docs/migrations.md`, after the `## 0091_manual_events` section, add:

```markdown
## 0092_system_graph

The system graph (Environments → System map). `ka.kg_nodes` holds nodes in four layers (`business`, `software`,
`data`, `infrastructure`); each node has at most one parent, in the same project and layer (`parent_id` NULL = top
level), sibling names are unique ignoring case, and a trigger refuses a node inside itself or a branch deeper than 12
levels. `ka.kg_edges` holds typed, directed edges between any two nodes of the project (same or different layers); an
edge between a node and a node that contains it is refused when made. `ka.kg_node_documents` links nodes to
`ka.documents`. Deleting a node with children is refused; deleting a node removes its edges and document links;
deleting a document removes its links. The down migration drops the three tables and both trigger functions.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_system_graph_migration.py tests/test_manual_events_migration.py`
(if the second file does not exist, run `tests/test_events_migration.py` instead). Expected: PASS.
Then from the worktree root: `sh db/tests/run.sh` — exit 0 and the new PASS line printed.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0092_system_graph.up.sql db/migrations/0092_system_graph.down.sql db/tests/run.sh \
  docs/migrations.md services/admin-api/tests/test_system_graph_migration.py
git commit -m "feat(db): system graph nodes, edges and document links (0092)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `system_graph.py` — views and roll-up

**Files:**
- Create: `services/admin-api/src/knowledge_admin/system_graph.py`
- Test: `services/admin-api/tests/test_system_graph.py`

**Interfaces:**
- Produces: `LAYERS: tuple[str, ...]`, `EDGE_TYPES: tuple[str, ...]`, `MAX_DEPTH = 5`, `MAX_VISIBLE = 500`,
  `MAX_EDGE_IDS = 20`; `class ViewError(Exception)` with `.status: int` and `str(exc)` the message;
  `build_view(nodes: list[dict], edges: list[dict], documents: dict, layer: str, root_id, depth: int) -> dict`.
  - `nodes`: every node of the project, dicts with `id, parent_id, layer, kind, name, description`.
  - `edges`: every edge of the project, dicts with `id, from_id, to_id, type`.
  - `documents`: `{node_id: [{'id', 'slug', 'title'}, …]}`.
  - Returns `{'layer', 'depth', 'root', 'breadcrumb', 'nodes', 'boundary', 'edges'}`:
    `root` = `{'id','name','kind','description'}` or None; `breadcrumb` = `[{'id','name','kind'}]` from the top of the
    layer to the root inclusive (empty at the top); `nodes` in depth-first order, siblings sorted by
    `(name.lower(), str(id))`, each `{'id','parent_id','layer','kind','name','description','child_count','expandable',
    'internal_edges','documents'}` where `parent_id` is the visible parent or None for the root's direct children;
    `boundary` = `[{'id','name','kind','layer'}]` sorted by `(LAYERS.index(layer), name.lower(), str(id))`;
    `edges` = `[{'from','to','type','count','edge_ids'}]` sorted by `(str(from), str(to), type)`.
  - Errors: unknown root → `ViewError(404, 'Node not found.')`; root in another layer →
    `ViewError(422, 'That node is in the <root layer> layer.')`; more than `MAX_VISIBLE` nodes →
    `ViewError(422, 'Too many nodes; choose a deeper root or a smaller depth.')`.

- [ ] **Step 1: Write the failing test** `services/admin-api/tests/test_system_graph.py`

```python
"""Graph in graph: a view shows one node's inside, collapses deeper nodes and rolls edges up to what is shown."""
import pytest

from knowledge_admin.system_graph import ViewError, build_view


def n(id, parent=None, layer='software', kind='module', name=None):
    return {'id': id, 'parent_id': parent, 'layer': layer, 'kind': kind, 'name': name or id, 'description': ''}


def e(id, a, b, type='calls'):
    return {'id': id, 'from_id': a, 'to_id': b, 'type': type}


NODES = [
    n('api', kind='service', name='admin-api'),
    n('events', 'api', name='Event processor'),
    n('match', 'events', name='matching'),
    n('tpl', 'events', name='templates'),
    n('tasks', 'api', name='Task runner'),
    n('worker', kind='service', name='repository-worker'),
    n('pull', 'worker', name='pull'),
    n('prod', layer='infrastructure', kind='server', name='prod-1'),
    n('prod-api', 'prod', layer='infrastructure', kind='container', name='admin-api @ prod-1'),
]


def view(edges=(), root=None, depth=1, layer='software', nodes=NODES, documents=None):
    return build_view(list(nodes), list(edges), documents or {}, layer, root, depth)


def ids(rows):
    return [row['id'] for row in rows]


def test_top_of_a_layer_shows_its_top_level_nodes_with_edges_rolled_up():
    result = view([e('e1', 'match', 'pull')])
    assert (result['root'], result['breadcrumb'], result['layer'], result['depth']) == (None, [], 'software', 1)
    assert ids(result['nodes']) == ['api', 'worker']
    api = result['nodes'][0]
    assert (api['parent_id'], api['child_count'], api['expandable'], api['internal_edges'], api['documents']) == (None, 2, True, 0, [])
    assert result['edges'] == [{'from': 'api', 'to': 'worker', 'type': 'calls', 'count': 1, 'edge_ids': ['e1']}]
    assert result['boundary'] == []


def test_inside_a_node_edges_group_and_internal_edges_are_counted():
    result = view([e('e1', 'match', 'tasks'), e('e2', 'tpl', 'tasks'), e('e3', 'match', 'tpl'), e('e4', 'match', 'tasks', 'uses')],
                  root='api')
    assert result['root'] == {'id': 'api', 'name': 'admin-api', 'kind': 'service', 'description': ''}
    assert result['breadcrumb'] == [{'id': 'api', 'name': 'admin-api', 'kind': 'service'}]
    assert ids(result['nodes']) == ['events', 'tasks']
    events, tasks = result['nodes']
    assert (events['internal_edges'], events['expandable'], tasks['child_count'], tasks['expandable']) == (1, True, 0, False)
    assert result['edges'] == [
        {'from': 'events', 'to': 'tasks', 'type': 'calls', 'count': 2, 'edge_ids': ['e1', 'e2']},
        {'from': 'events', 'to': 'tasks', 'type': 'uses', 'count': 1, 'edge_ids': ['e4']},
    ]


def test_depth_two_shows_grandchildren_nested_under_their_parent():
    result = view([e('e3', 'match', 'tpl')], root='api', depth=2)
    assert ids(result['nodes']) == ['events', 'match', 'tpl', 'tasks']
    assert [row['parent_id'] for row in result['nodes']] == [None, 'events', 'events', None]
    assert result['nodes'][0]['expandable'] is False  # its children are shown
    assert result['edges'] == [{'from': 'match', 'to': 'tpl', 'type': 'calls', 'count': 1, 'edge_ids': ['e3']}]


def test_boundary_in_the_same_layer_is_the_child_of_the_lowest_common_ancestor():
    result = view([e('e1', 'match', 'tasks'), e('e2', 'match', 'pull'), e('e3', 'tpl', 'worker')], root='events')
    assert ids(result['boundary']) == ['worker', 'tasks']  # by name: repository-worker, Task runner
    assert result['edges'] == [
        {'from': 'match', 'to': 'tasks', 'type': 'calls', 'count': 1, 'edge_ids': ['e1']},
        {'from': 'match', 'to': 'worker', 'type': 'calls', 'count': 1, 'edge_ids': ['e2']},
        {'from': 'tpl', 'to': 'worker', 'type': 'calls', 'count': 1, 'edge_ids': ['e3']},
    ]
    assert result['boundary'][0] == {'id': 'worker', 'name': 'repository-worker', 'kind': 'service', 'layer': 'software'}


def test_boundary_in_another_layer_is_that_layers_top_level_node():
    result = view([e('e1', 'prod-api', 'tasks', 'instance_of')], root='api')
    assert result['boundary'] == [{'id': 'prod', 'name': 'prod-1', 'kind': 'server', 'layer': 'infrastructure'}]
    assert result['edges'] == [{'from': 'prod', 'to': 'tasks', 'type': 'instance_of', 'count': 1, 'edge_ids': ['e1']}]


def test_boundaries_sort_by_layer_then_name():
    nodes = NODES + [n('biz', layer='business', kind='capability', name='Ticket triage')]
    result = view([e('e1', 'tasks', 'worker'), e('e2', 'prod-api', 'tasks', 'instance_of'), e('e3', 'tasks', 'biz', 'implements')],
                  root='api', nodes=nodes)
    assert ids(result['boundary']) == ['biz', 'worker', 'prod']


def test_edges_to_a_container_count_as_internal():
    # A move can leave an edge between a node and its ancestor; the view never draws it.
    result = view([e('e1', 'match', 'api'), e('e2', 'match', 'events')], root='api')
    assert result['edges'] == [] and result['boundary'] == []
    assert result['nodes'][0]['internal_edges'] == 2
    top = view([e('e1', 'match', 'api')])
    assert top['edges'] == [] and top['nodes'][0]['internal_edges'] == 1


def test_edges_outside_the_view_are_ignored():
    assert view([e('e1', 'pull', 'worker')], root='api')['edges'] == []
    assert view([e('e1', 'prod-api', 'prod')], root='api')['edges'] == []


def test_breadcrumb_and_documents():
    documents = {'match': [{'id': 'd1', 'slug': 'matching', 'title': 'Matching'}]}
    result = view(root='events', documents=documents)
    assert [crumb['id'] for crumb in result['breadcrumb']] == ['api', 'events']
    assert result['nodes'][0]['documents'] == [{'id': 'd1', 'slug': 'matching', 'title': 'Matching'}]


def test_unknown_root():
    with pytest.raises(ViewError) as raised:
        view(root='gone')
    assert (raised.value.status, str(raised.value)) == (404, 'Node not found.')


def test_root_in_another_layer():
    with pytest.raises(ViewError) as raised:
        view(root='prod')
    assert (raised.value.status, str(raised.value)) == (422, 'That node is in the infrastructure layer.')


def test_an_empty_layer_and_a_leaf():
    assert view(layer='data')['nodes'] == []
    leaf = view(root='match')
    assert (leaf['nodes'], leaf['edges'], leaf['boundary']) == ([], [], [])


def test_too_many_nodes():
    many = [n('top', kind='service')] + [n(f'c{i}', 'top') for i in range(501)]
    with pytest.raises(ViewError) as raised:
        view(root='top', nodes=many)
    assert (raised.value.status, str(raised.value)) == (422, 'Too many nodes; choose a deeper root or a smaller depth.')
    assert len(view(root='top', nodes=many[:501])['nodes']) == 500


def test_boundary_counts_toward_the_limit():
    many = [n('top', kind='service')] + [n(f'c{i}', 'top') for i in range(499)] + [n(f'o{i}', kind='service') for i in range(2)]
    edges = [e(f'x{i}', 'c0', f'o{i}') for i in range(2)]
    with pytest.raises(ViewError):
        view(edges, root='top', nodes=many)


def test_edge_ids_are_capped_at_twenty():
    nodes = NODES + [n(f'm{i}', 'events') for i in range(25)]
    edges = [e(f'x{i}', f'm{i}', 'tasks') for i in range(25)]
    [edge] = view(edges, root='api')['edges']
    assert edge['count'] == 25 and len(edge['edge_ids']) == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_system_graph.py`
Expected: FAIL (`ModuleNotFoundError: knowledge_admin.system_graph`).

- [ ] **Step 3: Write the implementation** `services/admin-api/src/knowledge_admin/system_graph.py`

```python
"""System graph views — graph in graph: a view shows one node's inside (its descendants down to a depth), collapses
deeper nodes into the visible node that contains them, and rolls every stored edge up to the nodes shown. Pure
functions over rows that system_graph_api.py loads."""
from collections import defaultdict

LAYERS = ('business', 'software', 'data', 'infrastructure')
EDGE_TYPES = ('calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes', 'subscribes', 'instance_of', 'runs_on',
              'implements', 'stores')
MAX_DEPTH, MAX_VISIBLE, MAX_EDGE_IDS = 5, 500, 20
NOT_FOUND = 'Node not found.'
TOO_MANY = 'Too many nodes; choose a deeper root or a smaller depth.'


class ViewError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _order(node: dict) -> tuple:
    return node['name'].lower(), str(node['id'])


def build_view(nodes: list[dict], edges: list[dict], documents: dict, layer: str, root_id, depth: int) -> dict:
    by_id = {node['id']: node for node in nodes}
    children = defaultdict(list)
    for node in nodes:
        children[node['parent_id']].append(node)

    def kids(node_id) -> list[dict]:
        found = children[node_id] if node_id is not None else [n for n in children[None] if n['layer'] == layer]
        return sorted(found, key=_order)

    def path(node_id) -> list:
        """Ids from the top of the node's layer down to the node."""
        ids = []
        while node_id is not None:
            ids.append(node_id)
            node_id = by_id[node_id]['parent_id']
        return ids[::-1]

    root = None
    if root_id is not None:
        root = by_id.get(root_id)
        if root is None:
            raise ViewError(404, NOT_FOUND)
        if root['layer'] != layer:
            raise ViewError(422, f"That node is in the {root['layer']} layer.")
    root_path = path(root_id) if root_id is not None else []

    shown, representative = [], {}

    def walk(node: dict, level: int, visible_parent, holder) -> None:
        if level <= depth:
            holder = node['id']
            inner = kids(node['id'])
            shown.append({'id': node['id'], 'parent_id': visible_parent, 'layer': node['layer'], 'kind': node['kind'],
                          'name': node['name'], 'description': node['description'], 'child_count': len(inner),
                          'expandable': bool(inner) and level == depth, 'internal_edges': 0,
                          'documents': list(documents.get(node['id'], []))})
            if len(shown) > MAX_VISIBLE:
                raise ViewError(422, TOO_MANY)
        representative[node['id']] = holder
        for child in kids(node['id']):
            walk(child, level + 1, node['id'] if level <= depth else visible_parent, holder)

    for child in kids(root_id):
        walk(child, 1, None, None)

    def outside(node_id):
        """The node an outside end is drawn as, or None when it is the root or contains it."""
        target = path(node_id)
        if by_id[node_id]['layer'] != layer:
            return target[0]
        common = 0
        while common < min(len(root_path), len(target)) and root_path[common] == target[common]:
            common += 1
        return target[common] if common < len(target) else None

    rows = {row['id']: row for row in shown}
    boundary, grouped = {}, {}
    for edge in edges:
        start, end = edge['from_id'], edge['to_id']
        if start not in representative and end not in representative:
            continue
        a = representative[start] if start in representative else outside(start)
        b = representative[end] if end in representative else outside(end)
        if a is None or b is None or a == b:
            rows[a if a is not None and a in rows else b]['internal_edges'] += 1
            continue
        for end_id in (a, b):
            if end_id not in rows and end_id not in boundary:
                node = by_id[end_id]
                boundary[end_id] = {'id': end_id, 'name': node['name'], 'kind': node['kind'], 'layer': node['layer']}
        group = grouped.setdefault((a, b, edge['type']), {'from': a, 'to': b, 'type': edge['type'], 'count': 0, 'edge_ids': []})
        group['count'] += 1
        if len(group['edge_ids']) < MAX_EDGE_IDS:
            group['edge_ids'].append(edge['id'])
    if len(shown) + len(boundary) > MAX_VISIBLE:
        raise ViewError(422, TOO_MANY)

    for group in grouped.values():
        group['edge_ids'].sort(key=str)
    return {
        'layer': layer,
        'depth': depth,
        'root': None if root is None else {key: root[key] for key in ('id', 'name', 'kind', 'description')},
        'breadcrumb': [{key: by_id[node_id][key] for key in ('id', 'name', 'kind')} for node_id in root_path],
        'nodes': shown,
        'boundary': sorted(boundary.values(), key=lambda node: (LAYERS.index(node['layer']), *_order(node))),
        'edges': sorted(grouped.values(), key=lambda edge: (str(edge['from']), str(edge['to']), edge['type'])),
    }
```

Note on `walk`: nodes deeper than `depth` keep the visible ancestor as `holder` (their representative); `shown` only
grows for levels ≤ `depth`, and the 500 check fires while walking, so a huge subtree never builds a huge list.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/admin-api && uv run pytest -q tests/test_system_graph.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/system_graph.py services/admin-api/tests/test_system_graph.py
git commit -m "feat(api): system graph views roll edges up to the nodes shown

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 3: `system_graph_render.py` — Mermaid, D2 and SVG

**Files:**
- Create: `services/admin-api/src/knowledge_admin/system_graph_render.py`
- Test: `services/admin-api/tests/test_system_graph_render.py`
- Modify: `docs/superpowers/specs/2026-10-10-system-graph-design.md` (one sentence, Step 6)

**Interfaces:**
- Consumes: a view dict shaped as `system_graph.build_view` returns (Task 2).
- Produces: `LAYOUTS = ('tala', 'elk', 'dagre')`, `SVG_TIMEOUT = 20`; `class RenderError(Exception)` with `.status`;
  `d2_string(text: str) -> str`, `mermaid_string(text: str) -> str`, `mermaid(view: dict) -> str`,
  `d2(view: dict) -> str`, `svg(text: str, layout: str) -> bytes`.

Facts checked against the real D2 v0.9.0 binary while planning: inside a double-quoted D2 string, `${…}` is a variable
substitution and **every** `$` must be written `\$` (an unescaped `$HOME` or a lone `${` fails to compile); with `\\`,
`\"` and `\$` escaped, labels such as `a"; x -> y`, `} brace`, `# hash`, `[x] {y}` and `@import` render literally with
all three layouts; `d2 --layout=<name> - -` reads stdin and writes the SVG to stdout (it prints `success: …` to stderr).

- [ ] **Step 1: Write the failing test** `services/admin-api/tests/test_system_graph_render.py`

```python
"""Diagram text from a system graph view: generated keys, labels that are always literal, and SVG through d2."""
import html
import re
import shutil
import subprocess

import pytest

from knowledge_admin import system_graph_render as render


def node(id, name, parent=None, kind='module'):
    return {'id': id, 'parent_id': parent, 'layer': 'software', 'kind': kind, 'name': name, 'description': '',
            'child_count': 0, 'expandable': False, 'internal_edges': 0, 'documents': []}


VIEW = {
    'layer': 'software', 'depth': 2, 'root': None, 'breadcrumb': [],
    'nodes': [node('events', 'Event processor'), node('match', 'matching', 'events'), node('store', 'events table', kind='database')],
    'boundary': [{'id': 'prod', 'name': 'prod-1', 'kind': 'server', 'layer': 'infrastructure'}],
    'edges': [{'from': 'match', 'to': 'store', 'type': 'writes', 'count': 3, 'edge_ids': ['a', 'b', 'c']},
              {'from': 'prod', 'to': 'events', 'type': 'instance_of', 'count': 1, 'edge_ids': ['d']}],
}


def test_d2_nests_containers_and_styles_the_boundary():
    assert render.d2(VIEW) == '\n'.join([
        'n1: "Event processor" {',
        '  n2: "matching" {shape: rectangle}',
        '}',
        'n3: "events table" {shape: cylinder}',
        'outside: "Outside" {',
        '  style.stroke-dash: 4',
        '  n4: "prod-1" {style.stroke-dash: 4}',
        '}',
        'n1.n2 -> n3: "writes ×3"',
        'outside.n4 -> n1: "instance of"',
    ]) + '\n'


def test_mermaid_uses_subgraphs_and_marks_the_boundary():
    assert render.mermaid(VIEW) == '\n'.join([
        'flowchart LR',
        '  subgraph n1["Event processor"]',
        '    n2["matching"]',
        '  end',
        '  n3[("events table")]',
        '  subgraph outside["Outside"]',
        '    n4["prod-1"]',
        '  end',
        '  n2 -->|"writes ×3"| n3',
        '  n4 -->|"instance of"| n1',
        '  classDef boundary stroke-dasharray: 4 4',
        '  class n4 boundary',
    ]) + '\n'


@pytest.mark.parametrize('kind, shape', [('table', 'cylinder'), ('Database', 'cylinder'), ('queue', 'queue'),
                                         ('person', 'person'), ('actor', 'person'), ('service', 'rectangle')])
def test_shapes_follow_the_kind(kind, shape):
    view = {**VIEW, 'nodes': [node('x', 'x', kind=kind)], 'boundary': [], 'edges': []}
    assert render.d2(view) == f'n1: "x" {{shape: {shape}}}\n'


def test_an_empty_view():
    empty = {**VIEW, 'nodes': [], 'boundary': [], 'edges': []}
    assert (render.d2(empty), render.mermaid(empty)) == ('', 'flowchart LR\n')


def test_hostile_names_are_literal_labels():
    assert render.d2_string('a"; x -> y') == '"a\\"; x -> y"'
    assert render.d2_string('${x} costs $5 } # done') == '"\\${x} costs \\$5 } # done"'
    assert render.d2_string('c:\\path') == '"c:\\\\path"'
    assert render.d2_string('two\nlines\tand\r\x07bell') == '"two lines and  bell"'
    assert render.mermaid_string('a"; x -> y') == '"a#34;; x -#62; y"'
    assert render.mermaid_string('<b>&`#1;') == '"#60;b#62;#38;#96;#35;1;"'
    names = ['a"; x -> y', '}', '${x}', '# hash', 'two\nlines', 'end']
    view = {**VIEW, 'nodes': [node(f'h{i}', name) for i, name in enumerate(names)], 'boundary': [],
            'edges': [{'from': 'h0', 'to': 'h1', 'type': 'calls', 'count': 1, 'edge_ids': ['x']}]}
    d2_lines = render.d2(view).splitlines()
    assert len(d2_lines) == len(names) + 1 and sum('->' in line.split(':')[0] for line in d2_lines) == 1
    mermaid_lines = render.mermaid(view).splitlines()
    assert len(mermaid_lines) == len(names) + 2 and sum('-->' in line for line in mermaid_lines) == 1


def test_svg_runs_d2_with_the_layout(monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen.update(args=args, **kwargs)
        return subprocess.CompletedProcess(args, 0, stdout=b'<svg/>', stderr=b'success: compiled')
    monkeypatch.setattr(render.shutil, 'which', lambda name: '/usr/local/bin/d2')
    monkeypatch.setattr(render.subprocess, 'run', run)
    assert render.svg('a -> b\n', 'elk') == b'<svg/>'
    assert seen['args'] == ['/usr/local/bin/d2', '--layout=elk', '-', '-']
    assert (seen['input'], seen['timeout'], seen['capture_output'], seen['check']) == (b'a -> b\n', 20, True, False)


def test_svg_without_d2(monkeypatch):
    monkeypatch.setattr(render.shutil, 'which', lambda name: None)
    with pytest.raises(render.RenderError) as raised:
        render.svg('a', 'tala')
    assert (raised.value.status, str(raised.value)) == (503, 'd2 is not installed.')


def test_svg_failures(monkeypatch):
    monkeypatch.setattr(render.shutil, 'which', lambda name: '/usr/local/bin/d2')
    monkeypatch.setattr(render.subprocess, 'run',
                        lambda args, **kw: subprocess.CompletedProcess(args, 1, stdout=b'', stderr=b'err: ' + b'x' * 3000))
    with pytest.raises(render.RenderError) as raised:
        render.svg('a', 'tala')
    assert raised.value.status == 502 and str(raised.value) == ('err: ' + 'x' * 3000)[:2000]

    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, 20)
    monkeypatch.setattr(render.subprocess, 'run', slow)
    with pytest.raises(render.RenderError) as raised:
        render.svg('a', 'tala')
    assert (raised.value.status, str(raised.value)) == (502, 'd2 took longer than 20 s.')
    with pytest.raises(render.RenderError) as raised:
        render.svg('a', 'neato')
    assert (raised.value.status, str(raised.value)) == (400, 'Choose tala, elk or dagre.')


@pytest.mark.skipif(shutil.which('d2') is None, reason='d2 is not installed')
@pytest.mark.parametrize('layout', ['tala', 'elk', 'dagre'])
def test_real_render_keeps_hostile_labels(layout):
    names = ['a"; x -> y', '${x} costs $5', '} brace', '# hash', '[x] {y} @import']
    view = {**VIEW, 'nodes': [node(f'h{i}', name) for i, name in enumerate(names)], 'boundary': [],
            'edges': [{'from': 'h0', 'to': 'h1', 'type': 'calls', 'count': 2, 'edge_ids': ['x', 'y']}]}
    svg = render.svg(render.d2(view), layout).decode()
    texts = {html.unescape(text) for text in re.findall(r'<text[^>]*>(.*?)</text>', svg)}
    assert set(names) <= texts and 'calls ×2' in texts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_system_graph_render.py`
Expected: FAIL (`ModuleNotFoundError`). (The real-render tests are skipped when `d2` is not on PATH.)

- [ ] **Step 3: Write the implementation** `services/admin-api/src/knowledge_admin/system_graph_render.py`

```python
"""Diagrams of a system graph view (system_graph.build_view): Mermaid and D2 text, and an SVG drawn by the d2 binary.
Keys are generated (n1, n2, …); names only ever appear as escaped, quoted labels, so a node name cannot add shapes,
edges, variables or imports. No icons, links or imports are emitted, so d2 never fetches anything."""
import shutil
import subprocess
from collections import defaultdict

LAYOUTS = ('tala', 'elk', 'dagre')
SVG_TIMEOUT = 20
MAX_ERROR = 2000
SHAPES = {'database': 'cylinder', 'table': 'cylinder', 'queue': 'queue', 'person': 'person', 'actor': 'person'}
MERMAID_SHAPES = {'cylinder': ('[(', ')]')}
MERMAID_ESCAPES = {'#': '#35;', '"': '#34;', '<': '#60;', '>': '#62;', '&': '#38;', '`': '#96;'}


class RenderError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _flat(text: str) -> str:
    """One line: control characters (newlines, tabs, bells) become spaces."""
    return ''.join(' ' if ord(char) < 32 or ord(char) == 127 else char for char in text)


def d2_string(text: str) -> str:
    return '"' + _flat(text).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$') + '"'


def mermaid_string(text: str) -> str:
    return '"' + ''.join(MERMAID_ESCAPES.get(char, char) for char in _flat(text)) + '"'


def _shape(node: dict) -> str:
    return SHAPES.get(node['kind'].strip().lower(), 'rectangle')


def _label(edge: dict) -> str:
    kind = edge['type'].replace('_', ' ')
    return kind if edge['count'] == 1 else f"{kind} ×{edge['count']}"


def _keys(view: dict) -> dict:
    return {node['id']: f'n{index}' for index, node in enumerate([*view['nodes'], *view['boundary']], start=1)}


def _children(view: dict) -> dict:
    children = defaultdict(list)
    for node in view['nodes']:
        children[node['parent_id']].append(node)
    return children


def d2(view: dict) -> str:
    key, children = _keys(view), _children(view)
    lines, where = [], {}

    def place(node: dict, path: str, indent: str) -> None:
        name = f"{path}.{key[node['id']]}" if path else key[node['id']]
        where[node['id']] = name
        inner = children.get(node['id'], [])
        if inner:
            lines.append(f"{indent}{key[node['id']]}: {d2_string(node['name'])} {{")
            for child in inner:
                place(child, name, indent + '  ')
            lines.append(f'{indent}}}')
        else:
            lines.append(f"{indent}{key[node['id']]}: {d2_string(node['name'])} {{shape: {_shape(node)}}}")

    for node in children.get(None, []):
        place(node, '', '')
    if view['boundary']:
        lines += ['outside: "Outside" {', '  style.stroke-dash: 4']
        for node in view['boundary']:
            where[node['id']] = f"outside.{key[node['id']]}"
            lines.append(f"  {key[node['id']]}: {d2_string(node['name'])} {{style.stroke-dash: 4}}")
        lines.append('}')
    for edge in view['edges']:
        lines.append(f"{where[edge['from']]} -> {where[edge['to']]}: {d2_string(_label(edge))}")
    return ''.join(line + '\n' for line in lines)


def mermaid(view: dict) -> str:
    key, children = _keys(view), _children(view)
    lines = ['flowchart LR']

    def place(node: dict, indent: str) -> None:
        inner = children.get(node['id'], [])
        if inner:
            lines.append(f"{indent}subgraph {key[node['id']]}[{mermaid_string(node['name'])}]")
            for child in inner:
                place(child, indent + '  ')
            lines.append(f'{indent}end')
        else:
            start, end = MERMAID_SHAPES.get(_shape(node), ('[', ']'))
            lines.append(f"{indent}{key[node['id']]}{start}{mermaid_string(node['name'])}{end}")

    for node in children.get(None, []):
        place(node, '  ')
    if view['boundary']:
        lines.append('  subgraph outside["Outside"]')
        lines += [f"    {key[node['id']]}[{mermaid_string(node['name'])}]" for node in view['boundary']]
        lines.append('  end')
    for edge in view['edges']:
        lines.append(f"  {key[edge['from']]} -->|{mermaid_string(_label(edge))}| {key[edge['to']]}")
    if view['boundary']:
        lines.append('  classDef boundary stroke-dasharray: 4 4')
        lines.append('  class ' + ','.join(key[node['id']] for node in view['boundary']) + ' boundary')
    return ''.join(line + '\n' for line in lines)


def svg(text: str, layout: str) -> bytes:
    if layout not in LAYOUTS:
        raise RenderError(400, 'Choose tala, elk or dagre.')
    binary = shutil.which('d2')
    if binary is None:
        raise RenderError(503, 'd2 is not installed.')
    try:
        done = subprocess.run([binary, f'--layout={layout}', '-', '-'], input=text.encode(), capture_output=True,
                              timeout=SVG_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        raise RenderError(502, f'd2 took longer than {SVG_TIMEOUT} s.') from None
    if done.returncode != 0:
        message = done.stderr.decode(errors='replace').strip() or f'd2 exited with {done.returncode}.'
        raise RenderError(502, message[:MAX_ERROR])
    return done.stdout
```

Check the hostile-name test's arithmetic against this code before running: the six names are top-level leaves (one
line each) plus one edge line for D2 (7 lines); Mermaid adds the `flowchart LR` header (8 lines). The name `end` is
safe because keys, not names, are Mermaid identifiers.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/admin-api && uv run pytest -q tests/test_system_graph_render.py`
Expected: PASS (3 real-render tests skipped without `d2`). Optional, if you want the real-render tests to run on macOS:
download `https://github.com/d2lang/d2/releases/download/v0.9.0/d2-v0.9.0-macos-arm64.tar.gz` (sha256
`eaf6c0c143e56dd9fa97bfb6df25ea9c1ebce40245f056a0768cf1a6c15d3064`) into a scratch directory outside the worktree,
extract it, and run the tests with that `bin` directory first on `PATH`. Do not commit the binary.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/system_graph_render.py services/admin-api/tests/test_system_graph_render.py
git commit -m "feat(api): Mermaid, D2 and SVG diagrams of a system graph view

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Align the spec** — labels are names only (no kind text, which keeps escaping to one place). In
`docs/superpowers/specs/2026-10-10-system-graph-design.md`, replace `**Labels** are the node name (plus kind in smaller
text where the format allows), escaped` with `**Labels** are the node name (the kind shows through the shape and in
the side list), escaped`, and add after the Mermaid bullet's first sentence: `Edge labels write the type with spaces
(\`instance of\`).` Commit:

```bash
git add docs/superpowers/specs/2026-10-10-system-graph-design.md
git commit -m "docs: system graph labels are names; edge types read with spaces

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: System graph API — views, search, nodes

**Files:**
- Create: `services/admin-api/src/knowledge_admin/system_graph_api.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py` (import and include the router)
- Test: `services/admin-api/tests/test_system_graph_api.py`

**Interfaces:**
- Consumes: `system_graph.build_view`, `ViewError`, `MAX_DEPTH`, `LAYERS` (Task 2); `system_graph_render.mermaid`,
  `d2`, `svg`, `RenderError` (Task 3); `events_api.like`; `event_templates.has_nul`; `access.request_actor`.
- Produces (routes under the admin-only `/api` router):
  - `GET /api/graph/view/{layer}?root=&depth=1&format=json|mermaid|d2|svg&layout=tala|elk|dagre` → the view JSON,
    `text/plain` for mermaid/d2, `image/svg+xml` for svg.
  - `GET /api/graph/nodes?layer=&q=&limit=50` → `{"nodes": [{id, layer, kind, name, path: [ancestor names]}]}`.
  - `GET /api/graph/nodes/{id}` → `{"node": NodeDetail}` with `id, layer, kind, name, description, parent_id,
    properties, revision, created_at, updated_at, breadcrumb: [{id,name,kind}] (top → node, inclusive),
    children: [{id,name,kind,child_count}], outgoing / incoming: [{id,type,description,revision,
    node: {id,name,kind,layer}}], documents: [{id,slug,title}]`.
  - `POST /api/graph/nodes` (201, `{"node": NodeDetail}`), `PUT /api/graph/nodes/{id}` (`{"node": NodeDetail}`),
    `DELETE /api/graph/nodes/{id}` (204).
  - Module helpers Task 5 reuses inside `build_api`: `database()`, `audit(conn, project, request, action,
    resource_type, resource_id, details)`, `node_row(conn, project, node_id, lock=False)`, `detail(conn, project,
    node_id)`, `refused(conn, duplicate, missing)` context manager, `documents_of(conn, project, node_id=None)`.

- [ ] **Step 1: Write the failing test** `services/admin-api/tests/test_system_graph_api.py`

```python
"""Environments → System map API: nodes in four layers, views of one node's inside, search."""
from uuid import uuid4

import pytest

from knowledge_admin import system_graph_render
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def make(client, name, parent=None, layer='software', kind='service', **extra) -> dict:
    made = client.post('/api/graph/nodes', headers=HEADERS,
                       json={'layer': layer, 'kind': kind, 'name': name, 'parent_id': parent, **extra})
    assert made.status_code == 201, made.text
    return made.json()['node']


def post(client, **body):
    return client.post('/api/graph/nodes', headers=HEADERS, json={'layer': 'software', 'kind': 'service', 'name': 'x', **body})


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def actions(store, prefix='graph.') -> list[str]:
    with store.connect() as conn:
        return [row['action'] for row in conn.execute(
            "SELECT action FROM ka.audit_events WHERE starts_with(action, %s) ORDER BY created_at", (prefix,)).fetchall()]


def link(store, a, b, type='calls') -> None:
    with store.connect() as conn:
        conn.execute("""INSERT INTO ka.kg_edges(project_id,from_id,to_id,type)
            SELECT project_id,%s,%s,%s FROM ka.kg_nodes WHERE id=%s""", (a, b, type, a))


def change(client, node, **values):
    current = client.get(f"/api/graph/nodes/{node['id']}", headers=HEADERS).json()['node']
    body = {key: current[key] for key in ('kind', 'name', 'description', 'properties', 'parent_id', 'revision')}
    return client.put(f"/api/graph/nodes/{node['id']}", headers=HEADERS, json={**body, **values})


def test_create_and_read(client, store):
    api = make(client, ' admin-api ', description='The admin API', properties={'port': 8766})
    assert (api['name'], api['layer'], api['kind'], api['revision'], api['parent_id'], api['properties'], api['description']) == (
        'admin-api', 'software', 'service', 1, None, {'port': 8766}, 'The admin API')
    events = make(client, 'Event processor', parent=api['id'], kind='module')
    assert [crumb['name'] for crumb in events['breadcrumb']] == ['admin-api', 'Event processor']
    got = client.get(f"/api/graph/nodes/{api['id']}", headers=HEADERS).json()['node']
    assert got['children'] == [{'id': events['id'], 'name': 'Event processor', 'kind': 'module', 'child_count': 0}]
    assert (got['outgoing'], got['incoming'], got['documents'], got['breadcrumb']) == (
        [], [], [], [{'id': api['id'], 'name': 'admin-api', 'kind': 'service'}])
    assert message(client.get(f'/api/graph/nodes/{uuid4()}', headers=HEADERS)) == (404, 'Node not found.')
    assert actions(store) == ['graph.node.created', 'graph.node.created']


def test_input_is_validated(client):
    assert message(post(client, name='  ')) == (400, 'Enter a name.')
    assert message(post(client, kind=' ')) == (400, 'Enter a kind.')
    for body in [{'layer': 'network'}, {'name': 'x' * 161}, {'kind': 'k' * 61}, {'description': 'd' * 4001},
                 {'properties': [1]}, {'surprise': 1}, {'parent_id': 'nope'}]:
        assert post(client, **body).status_code == 400, body
    assert message(post(client, properties={'x': 'y' * 16400})) == (400, 'Properties are too large (16 KB at most).')


def test_nul_characters_are_refused(client):
    for body, label in [({'name': 'a\x00b'}, 'Name'), ({'kind': 'k\x00'}, 'Kind'), ({'description': '\x00'}, 'Description'),
                        ({'properties': {'deep': ['\x00']}}, 'Properties'), ({'properties': {'k\x00': 1}}, 'Properties')]:
        assert message(post(client, **body)) == (400, f'Remove the NUL character from {label}.')


def test_sibling_names_are_unique(client):
    api = make(client, 'admin-api')
    assert message(post(client, name='ADMIN-API')) == (409, 'A node named "ADMIN-API" already exists here.')
    make(client, 'admin-api', layer='infrastructure', kind='container')
    make(client, 'admin-api', parent=api['id'], kind='module')
    other = make(client, 'other')
    assert message(change(client, other, name='Admin-Api')) == (409, 'A node named "Admin-Api" already exists here.')


def test_parents_must_exist_in_the_same_layer(client):
    api = make(client, 'admin-api')
    assert message(post(client, parent_id=str(uuid4()))) == (404, 'Parent not found.')
    assert message(post(client, layer='data', kind='table', parent_id=api['id'])) == (422, 'Choose a parent in the same layer.')


def test_edit_and_stale_revision(client, store):
    api = make(client, 'admin-api')
    body = {'kind': 'service', 'name': 'Admin API', 'description': 'd', 'properties': {'a': 1}, 'parent_id': None, 'revision': 1}
    saved = client.put(f"/api/graph/nodes/{api['id']}", headers=HEADERS, json=body)
    assert saved.status_code == 200, saved.text
    assert (saved.json()['node']['name'], saved.json()['node']['revision']) == ('Admin API', 2)
    assert message(client.put(f"/api/graph/nodes/{api['id']}", headers=HEADERS, json=body)) == (
        409, 'This node was changed by someone else. Reload and try again.')
    assert message(client.put(f'/api/graph/nodes/{uuid4()}', headers=HEADERS, json=body)) == (404, 'Node not found.')
    assert client.put(f"/api/graph/nodes/{api['id']}", headers=HEADERS, json={**body, 'revision': 2, 'layer': 'data'}).status_code == 400
    assert actions(store)[-1] == 'graph.node.updated'


def test_moves_are_checked(client, store):
    api = make(client, 'admin-api')
    events = make(client, 'Event processor', parent=api['id'], kind='module')
    match = make(client, 'matching', parent=events['id'], kind='module')
    worker = make(client, 'repository-worker')
    assert message(change(client, api, parent_id=match['id'])) == (422, 'A node cannot be moved inside itself.')
    assert message(change(client, api, parent_id=api['id'])) == (422, 'A node cannot be moved inside itself.')
    moved = change(client, events, parent_id=worker['id'])
    assert moved.status_code == 200, moved.text
    assert [crumb['name'] for crumb in moved.json()['node']['breadcrumb']] == ['repository-worker', 'Event processor']
    assert actions(store)[-1] == 'graph.node.moved'
    assert change(client, events, parent_id=None).status_code == 200
    deepest = worker['id']
    for level in range(10):  # worker is level 1; this chain reaches level 11
        deepest = make(client, f'level {level + 2}', parent=deepest, kind='module')['id']
    assert message(change(client, events, parent_id=deepest)) == (422, 'Too deep: a layer has at most 12 levels.')  # 11 + 2


def test_delete(client, store):
    api = make(client, 'admin-api')
    events = make(client, 'Event processor', parent=api['id'], kind='module')
    assert message(client.delete(f"/api/graph/nodes/{api['id']}", headers=HEADERS)) == (409, 'Move or delete its children first.')
    assert client.delete(f"/api/graph/nodes/{events['id']}", headers=HEADERS).status_code == 204
    assert client.delete(f"/api/graph/nodes/{api['id']}", headers=HEADERS).status_code == 204
    assert message(client.delete(f"/api/graph/nodes/{api['id']}", headers=HEADERS)) == (404, 'Node not found.')
    assert actions(store)[-2:] == ['graph.node.deleted', 'graph.node.deleted']


def test_search(client):
    api = make(client, 'admin-api')
    make(client, 'Event processor', parent=api['id'], kind='module')
    make(client, 'Events table', layer='data', kind='table')
    make(client, '100% done', kind='module')
    found = lambda **params: [(row['name'], row['path'], row['layer']) for row in
                              client.get('/api/graph/nodes', headers=HEADERS, params=params).json()['nodes']]
    assert found(q='EVENT') == [('Event processor', ['admin-api'], 'software'), ('Events table', [], 'data')]
    assert found(q='event', layer='software') == [('Event processor', ['admin-api'], 'software')]
    assert found(q='%') == [('100% done', [], 'software')]
    assert [name for name, _, _ in found()] == ['100% done', 'admin-api', 'Event processor', 'Events table']
    assert len(found(limit=2)) == 2
    assert client.get('/api/graph/nodes', headers=HEADERS, params={'layer': 'network'}).status_code == 400


def test_views(client, store):
    api = make(client, 'admin-api')
    events = make(client, 'Event processor', parent=api['id'], kind='module')
    worker = make(client, 'repository-worker')
    link(store, events['id'], worker['id'])
    top = client.get('/api/graph/view/software', headers=HEADERS).json()
    assert [node['name'] for node in top['nodes']] == ['admin-api', 'repository-worker']
    assert [(edge['from'], edge['to'], edge['type'], edge['count']) for edge in top['edges']] == [(api['id'], worker['id'], 'calls', 1)]
    inside = client.get('/api/graph/view/software', headers=HEADERS, params={'root': api['id']}).json()
    assert ([node['name'] for node in inside['nodes']], [node['name'] for node in inside['boundary']]) == (
        ['Event processor'], ['repository-worker'])
    assert inside['root']['name'] == 'admin-api' and [crumb['name'] for crumb in inside['breadcrumb']] == ['admin-api']
    assert message(client.get('/api/graph/view/software', headers=HEADERS, params={'root': str(uuid4())})) == (404, 'Node not found.')
    assert message(client.get('/api/graph/view/data', headers=HEADERS, params={'root': api['id']})) == (
        422, 'That node is in the software layer.')
    for params in [{'depth': 0}, {'depth': 6}, {'format': 'png'}, {'layout': 'neato'}, {'root': 'nope'}]:
        assert client.get('/api/graph/view/software', headers=HEADERS, params=params).status_code == 400, params
    assert client.get('/api/graph/view/network', headers=HEADERS).status_code == 400


def test_view_formats(client, store, monkeypatch):
    api = make(client, 'admin-api')
    mermaid = client.get('/api/graph/view/software', headers=HEADERS, params={'format': 'mermaid'})
    assert mermaid.headers['content-type'].startswith('text/plain') and mermaid.text == 'flowchart LR\n  n1["admin-api"]\n'
    d2 = client.get('/api/graph/view/software', headers=HEADERS, params={'format': 'd2'})
    assert d2.headers['content-type'].startswith('text/plain') and d2.text == 'n1: "admin-api" {shape: rectangle}\n'
    monkeypatch.setattr(system_graph_render, 'svg', lambda text, layout: f'<svg data-layout="{layout}">{text}</svg>'.encode())
    svg = client.get('/api/graph/view/software', headers=HEADERS, params={'format': 'svg', 'layout': 'elk'})
    assert svg.headers['content-type'] == 'image/svg+xml'
    assert svg.text == '<svg data-layout="elk">n1: "admin-api" {shape: rectangle}\n</svg>'


def test_svg_without_d2(client, monkeypatch):
    make(client, 'admin-api')
    monkeypatch.setattr(system_graph_render.shutil, 'which', lambda name: None)
    assert message(client.get('/api/graph/view/software', headers=HEADERS, params={'format': 'svg'})) == (503, 'd2 is not installed.')


def test_graph_requires_admin(client):
    readers = {'X-Forwarded-Groups': 'readers', 'X-Forwarded-User': 'reader-id', 'X-Forwarded-Email': 'reader@example.test'}
    assert client.get('/api/graph/view/software', headers=readers).status_code == 403
    assert client.post('/api/graph/nodes', headers=readers, json={}).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_system_graph_api.py`
Expected: FAIL (404 for every `/api/graph/...` route).

- [ ] **Step 3: Write the implementation** `services/admin-api/src/knowledge_admin/system_graph_api.py`

```python
"""Environments → System map: the system graph's nodes, edges and attached documents, and views of one node's inside
(graph in graph) as JSON, Mermaid, D2 or SVG. system_graph.py computes views; system_graph_render.py draws them."""
import json
from contextlib import contextmanager
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin import system_graph, system_graph_render
from knowledge_admin.access import request_actor
from knowledge_admin.event_templates import has_nul
from knowledge_admin.events_api import like
from knowledge_admin.repositories import RepositoryStore

Layer = Literal['business', 'software', 'data', 'infrastructure']
EdgeType = Literal['calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes', 'subscribes', 'instance_of', 'runs_on',
                   'implements', 'stores']
NO_NODE = 'Node not found.'
NO_PARENT = 'Parent not found.'
NO_EDGE = 'Edge not found.'
NO_DOCUMENT = 'Document not found.'
STALE_NODE = 'This node was changed by someone else. Reload and try again.'
STALE_EDGE = 'This edge was changed by someone else. Reload and try again.'
OTHER_LAYER = 'Choose a parent in the same layer.'
HAS_CHILDREN = 'Move or delete its children first.'
SAME_NODE = 'An edge needs two different nodes.'
DUPLICATE_EDGE = 'This edge already exists.'
INSIDE_ITSELF = 'A node cannot be moved inside itself.'
REFUSED = 'The graph refused this change.'
MAX_PROPERTIES = 16384
NODE = 'id,layer,kind,name,description,parent_id,properties,revision,created_at,updated_at'
EDGE = 'id,from_id,to_id,type,description,revision,created_at,updated_at'


class NodeFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(max_length=60)
    name: str = Field(max_length=160)
    parent_id: UUID | None = None
    description: str = Field(default='', max_length=4000)
    properties: dict = Field(default_factory=dict)


class NodeDraft(NodeFields):
    layer: Layer


class NodeChange(NodeFields):
    revision: int = Field(ge=1, strict=True)


class EdgeDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    from_id: UUID
    to_id: UUID
    type: EdgeType
    description: str = Field(default='', max_length=4000)


class EdgeChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str = Field(default='', max_length=4000)
    revision: int = Field(ge=1, strict=True)


def bad(message: str) -> HTTPException:
    return HTTPException(400, message)


def duplicate_name(name: str) -> str:
    return f'A node named "{name}" already exists here.'


def clean(value: NodeFields) -> dict:
    """The node's columns, validated: 400 with a message for the form when something is wrong."""
    for label, text in (('Kind', value.kind), ('Name', value.name), ('Description', value.description),
                        ('Properties', value.properties)):
        if has_nul(text):
            raise bad(f'Remove the NUL character from {label}.')
    kind, name = value.kind.strip(), value.name.strip()
    if not kind:
        raise bad('Enter a kind.')
    if not name:
        raise bad('Enter a name.')
    if len(json.dumps(value.properties, ensure_ascii=False)) > MAX_PROPERTIES:
        raise bad('Properties are too large (16 KB at most).')
    return {'kind': kind, 'name': name, 'description': value.description, 'properties': value.properties,
            'parent_id': value.parent_id}


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Graph storage is not configured')
        return store

    def audit(conn, project: UUID, request: Request, action: str, resource_type: str, resource_id: UUID, details: dict) -> None:
        actor = database().actor(conn, *request_actor(request))
        conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,%s,%s,%s)""", (project, actor, action, resource_type, resource_id, Jsonb(details)))

    @contextmanager
    def refused(conn, duplicate: str, missing: tuple[int, str] = (422, OTHER_LAYER)):
        """Runs a write in a savepoint and turns the database's refusals into answers for the form."""
        try:
            with conn.transaction():
                yield
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, duplicate) from None
        except psycopg.errors.CheckViolation as exc:
            # The graph's triggers raise without a constraint name and with a sentence meant for people.
            if exc.diag.constraint_name is None and exc.diag.message_primary:
                raise HTTPException(422, exc.diag.message_primary) from None
            raise bad(REFUSED) from None
        except psycopg.errors.ForeignKeyViolation:
            raise HTTPException(*missing) from None

    def node_row(conn, project: UUID, node_id: UUID, lock: bool = False) -> dict:
        row = conn.execute(f"SELECT {NODE} FROM ka.kg_nodes WHERE id=%s AND project_id=%s{' FOR UPDATE' if lock else ''}",
                           (node_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NO_NODE)
        return row

    def check_parent(conn, project: UUID, layer: str, parent_id: UUID | None) -> None:
        if parent_id is None:
            return
        parent = conn.execute('SELECT layer FROM ka.kg_nodes WHERE id=%s AND project_id=%s', (parent_id, project)).fetchone()
        if parent is None:
            raise HTTPException(404, NO_PARENT)
        if parent['layer'] != layer:
            raise HTTPException(422, OTHER_LAYER)

    def breadcrumb(conn, node_id: UUID) -> list[dict]:
        return conn.execute("""WITH RECURSIVE up(id,parent_id,name,kind,steps) AS (
                SELECT id,parent_id,name,kind,0 FROM ka.kg_nodes WHERE id=%s
                UNION ALL
                SELECT p.id,p.parent_id,p.name,p.kind,up.steps+1 FROM ka.kg_nodes p JOIN up ON p.id=up.parent_id)
            SELECT id,name,kind FROM up ORDER BY steps DESC""", (node_id,)).fetchall()

    def documents_of(conn, project: UUID, node_id: UUID | None = None) -> dict:
        rows = conn.execute("""SELECT l.node_id,d.id,d.slug,d.title FROM ka.kg_node_documents l
            JOIN ka.document_details d ON d.id=l.document_id
            WHERE l.project_id=%s AND (%s::uuid IS NULL OR l.node_id=%s) ORDER BY lower(d.title),d.id""",
            (project, node_id, node_id)).fetchall()
        attached: dict = {}
        for row in rows:
            attached.setdefault(row['node_id'], []).append({'id': row['id'], 'slug': row['slug'], 'title': row['title']})
        return attached

    def detail(conn, project: UUID, node_id: UUID) -> dict:
        node = node_row(conn, project, node_id)
        children = conn.execute("""SELECT c.id,c.name,c.kind,(SELECT count(*) FROM ka.kg_nodes g WHERE g.parent_id=c.id) AS child_count
            FROM ka.kg_nodes c WHERE c.parent_id=%s ORDER BY lower(c.name),c.id""", (node_id,)).fetchall()

        def edges(mine: str, other: str) -> list[dict]:
            return conn.execute(f"""SELECT e.id,e.type,e.description,e.revision,
                    jsonb_build_object('id',n.id,'name',n.name,'kind',n.kind,'layer',n.layer) AS node
                FROM ka.kg_edges e JOIN ka.kg_nodes n ON n.id=e.{other}
                WHERE e.{mine}=%s ORDER BY e.type,lower(n.name),e.id""", (node_id,)).fetchall()
        return {**node, 'breadcrumb': breadcrumb(conn, node_id), 'children': children,
                'outgoing': edges('from_id', 'to_id'), 'incoming': edges('to_id', 'from_id'),
                'documents': documents_of(conn, project, node_id).get(node_id, [])}

    @router.get('/graph/view/{layer}', response_model=None)
    def view(layer: Layer, root: UUID | None = None, depth: int = Query(1, ge=1, le=system_graph.MAX_DEPTH),
             format: Literal['json', 'mermaid', 'd2', 'svg'] = 'json',
             layout: Literal['tala', 'elk', 'dagre'] = 'tala'):
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            nodes = conn.execute('SELECT id,parent_id,layer,kind,name,description FROM ka.kg_nodes WHERE project_id=%s',
                                 (project,)).fetchall()
            edges = conn.execute('SELECT id,from_id,to_id,type FROM ka.kg_edges WHERE project_id=%s', (project,)).fetchall()
            documents = documents_of(conn, project)
        try:
            graph = system_graph.build_view(nodes, edges, documents, layer, root, depth)
        except system_graph.ViewError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        if format == 'json':
            return graph
        if format == 'mermaid':
            return PlainTextResponse(system_graph_render.mermaid(graph))
        text = system_graph_render.d2(graph)
        if format == 'd2':
            return PlainTextResponse(text)
        try:
            return Response(system_graph_render.svg(text, layout), media_type='image/svg+xml')
        except system_graph_render.RenderError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @router.get('/graph/nodes')
    def search(layer: Layer | None = None, q: str = Query('', max_length=200), limit: int = Query(50, ge=1, le=200)) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            rows = conn.execute("""SELECT id,layer,kind,name FROM ka.kg_nodes
                WHERE project_id=%s AND (%s::text IS NULL OR layer=%s) AND name ILIKE %s ESCAPE '\\'
                ORDER BY lower(name),id LIMIT %s""", (project, layer, layer, like(q.strip()), limit)).fetchall()
            everything = {row['id']: row for row in conn.execute(
                'SELECT id,parent_id,name FROM ka.kg_nodes WHERE project_id=%s', (project,)).fetchall()}

        def path(node_id) -> list[str]:
            names, parent = [], everything[node_id]['parent_id']
            while parent is not None:
                names.append(everything[parent]['name'])
                parent = everything[parent]['parent_id']
            return names[::-1]
        return {'nodes': [{**row, 'path': path(row['id'])} for row in rows]}

    @router.get('/graph/nodes/{node_id}')
    def one(node_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            return {'node': detail(conn, db.project(conn, app), node_id)}

    @router.post('/graph/nodes', status_code=201)
    def create(value: NodeDraft, request: Request) -> dict:
        fields = clean(value)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            check_parent(conn, project, value.layer, fields['parent_id'])
            actor = db.actor(conn, *request_actor(request))
            with refused(conn, duplicate_name(fields['name'])):
                node_id = conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,description,parent_id,properties,created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (project, value.layer, fields['kind'], fields['name'], fields['description'], fields['parent_id'],
                     Jsonb(fields['properties']), actor)).fetchone()['id']
            audit(conn, project, request, 'graph.node.created', 'graph_node', node_id,
                  {'layer': value.layer, 'name': fields['name']})
            return {'node': detail(conn, project, node_id)}

    @router.put('/graph/nodes/{node_id}')
    def save(node_id: UUID, value: NodeChange, request: Request) -> dict:
        fields = clean(value)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            current = node_row(conn, project, node_id, lock=True)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE_NODE)
            if fields['parent_id'] == node_id:
                raise HTTPException(422, INSIDE_ITSELF)
            check_parent(conn, project, current['layer'], fields['parent_id'])
            with refused(conn, duplicate_name(fields['name'])):
                conn.execute("""UPDATE ka.kg_nodes SET kind=%s,name=%s,description=%s,parent_id=%s,properties=%s,
                    revision=revision+1 WHERE id=%s""",
                    (fields['kind'], fields['name'], fields['description'], fields['parent_id'], Jsonb(fields['properties']),
                     node_id))
            moved = fields['parent_id'] != current['parent_id']
            audit(conn, project, request, 'graph.node.moved' if moved else 'graph.node.updated', 'graph_node', node_id,
                  {'name': fields['name'], 'parent_id': str(fields['parent_id']) if fields['parent_id'] else None})
            return {'node': detail(conn, project, node_id)}

    @router.delete('/graph/nodes/{node_id}', status_code=204)
    def delete(node_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            node = node_row(conn, project, node_id, lock=True)
            if conn.execute('SELECT 1 FROM ka.kg_nodes WHERE parent_id=%s LIMIT 1', (node_id,)).fetchone():
                raise HTTPException(409, HAS_CHILDREN)
            with refused(conn, HAS_CHILDREN, (409, HAS_CHILDREN)):  # a child added meanwhile
                conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (node_id,))
            audit(conn, project, request, 'graph.node.deleted', 'graph_node', node_id,
                  {'layer': node['layer'], 'name': node['name']})
        return Response(status_code=204)

    return router
```

In `services/admin-api/src/knowledge_admin/api.py`, add `system_graph_api` to the `from knowledge_admin import …` list
and, next to `router.include_router(service_api.build_api(app_name, repository_store))`, add
`router.include_router(system_graph_api.build_api(app_name, repository_store))`.

If `test_graph_requires_admin` shows the readers' POST answering 400 instead of 403, check how `require_admin` runs
relative to body validation in `api.py`; the router-level dependency must refuse first — do not weaken the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/admin-api && uv run pytest -q tests/test_system_graph_api.py tests/test_system_graph.py tests/test_system_graph_render.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/system_graph_api.py services/admin-api/src/knowledge_admin/api.py \
  services/admin-api/tests/test_system_graph_api.py
git commit -m "feat(api): system graph nodes, search and views (JSON, Mermaid, D2, SVG)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: System graph API — edges and documents

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/system_graph_api.py` (routes inside `build_api`, before `return router`)
- Test: `services/admin-api/tests/test_system_graph_api.py` (append)

**Interfaces:**
- Consumes (Task 4, inside `build_api`): `database()`, `audit(...)`, `refused(conn, duplicate, missing)`,
  `node_row(...)`, `detail(...)`; module constants `EDGE`, `NO_EDGE`, `NO_NODE`, `NO_DOCUMENT`, `STALE_EDGE`,
  `SAME_NODE`, `DUPLICATE_EDGE`; models `EdgeDraft`, `EdgeChange`.
- Produces: `POST /api/graph/edges` (201, `{"edge": {id, from_id, to_id, type, description, revision, created_at,
  updated_at}}`), `PUT /api/graph/edges/{id}` (`{"edge": …}`), `DELETE /api/graph/edges/{id}` (204),
  `PUT|DELETE /api/graph/nodes/{id}/documents/{document_id}` (204, idempotent).

- [ ] **Step 1: Write the failing tests** — append to `services/admin-api/tests/test_system_graph_api.py`:

```python
def test_edges(client, store):
    api = make(client, 'admin-api')
    events = make(client, 'Event processor', parent=api['id'], kind='module')
    worker = make(client, 'repository-worker')
    prod = make(client, 'prod-1', layer='infrastructure', kind='server')
    add = lambda a, b, type='calls', **extra: client.post('/api/graph/edges', headers=HEADERS,
                                                          json={'from_id': a, 'to_id': b, 'type': type, **extra})
    made = add(events['id'], worker['id'], description='pulls')
    assert made.status_code == 201, made.text
    edge = made.json()['edge']
    assert (edge['from_id'], edge['to_id'], edge['type'], edge['description'], edge['revision']) == (
        events['id'], worker['id'], 'calls', 'pulls', 1)
    out = client.get(f"/api/graph/nodes/{events['id']}", headers=HEADERS).json()['node']['outgoing']
    assert [(row['type'], row['node']['name'], row['node']['layer'], row['description']) for row in out] == [
        ('calls', 'repository-worker', 'software', 'pulls')]
    back = client.get(f"/api/graph/nodes/{worker['id']}", headers=HEADERS).json()['node']['incoming']
    assert [(row['id'], row['node']['name']) for row in back] == [(edge['id'], 'Event processor')]
    assert message(add(events['id'], worker['id'])) == (409, 'This edge already exists.')
    assert message(add(worker['id'], worker['id'])) == (422, 'An edge needs two different nodes.')
    assert message(add(events['id'], api['id'], 'depends_on')) == (422, 'An edge cannot join a node and a node that contains it.')
    assert message(add(events['id'], str(uuid4()))) == (404, 'Node not found.')
    assert add(events['id'], worker['id'], 'sends').status_code == 400
    assert message(add(events['id'], worker['id'], 'uses', description='a\x00')) == (400, 'Remove the NUL character from Description.')
    assert add(prod['id'], api['id'], 'instance_of').status_code == 201  # across layers
    endpoint = f"/api/graph/edges/{edge['id']}"
    edited = client.put(endpoint, headers=HEADERS, json={'description': 'pulls tickets', 'revision': 1})
    assert edited.status_code == 200 and (edited.json()['edge']['description'], edited.json()['edge']['revision']) == ('pulls tickets', 2)
    assert message(client.put(endpoint, headers=HEADERS, json={'description': 'x', 'revision': 1})) == (
        409, 'This edge was changed by someone else. Reload and try again.')
    assert client.put(endpoint, headers=HEADERS, json={'description': 'x', 'revision': 2, 'type': 'uses'}).status_code == 400
    assert client.delete(endpoint, headers=HEADERS).status_code == 204
    assert message(client.delete(endpoint, headers=HEADERS)) == (404, 'Edge not found.')
    assert message(client.put(endpoint, headers=HEADERS, json={'description': 'x', 'revision': 3})) == (404, 'Edge not found.')
    assert actions(store, 'graph.edge.') == ['graph.edge.created', 'graph.edge.created', 'graph.edge.updated', 'graph.edge.deleted']


def test_documents(client, store):
    made = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'runbook', 'title': 'Runbook', 'markdown': '# Runbook\n'})
    assert made.status_code == 201, made.text
    document = made.json()['id']
    api = make(client, 'admin-api')
    endpoint = f"/api/graph/nodes/{api['id']}/documents/{document}"
    assert client.put(endpoint, headers=HEADERS).status_code == 204
    assert client.put(endpoint, headers=HEADERS).status_code == 204  # idempotent
    expected = [{'id': document, 'slug': 'runbook', 'title': 'Runbook'}]
    assert client.get(f"/api/graph/nodes/{api['id']}", headers=HEADERS).json()['node']['documents'] == expected
    assert client.get('/api/graph/view/software', headers=HEADERS).json()['nodes'][0]['documents'] == expected
    assert message(client.put(f"/api/graph/nodes/{api['id']}/documents/{uuid4()}", headers=HEADERS)) == (404, 'Document not found.')
    assert message(client.put(f'/api/graph/nodes/{uuid4()}/documents/{document}', headers=HEADERS)) == (404, 'Node not found.')
    assert client.delete(endpoint, headers=HEADERS).status_code == 204
    assert client.delete(endpoint, headers=HEADERS).status_code == 204
    assert client.get(f"/api/graph/nodes/{api['id']}", headers=HEADERS).json()['node']['documents'] == []
    assert actions(store, 'graph.document.') == ['graph.document.attached', 'graph.document.detached']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_system_graph_api.py -k "edges or documents"`
Expected: FAIL (404/405 for the new routes).

- [ ] **Step 3: Add the routes** inside `build_api` in `system_graph_api.py`, before `return router`:

```python
    def edge_row(conn, project: UUID, edge_id: UUID, lock: bool = False) -> dict:
        row = conn.execute(f"SELECT {EDGE} FROM ka.kg_edges WHERE id=%s AND project_id=%s{' FOR UPDATE' if lock else ''}",
                           (edge_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NO_EDGE)
        return row

    @router.post('/graph/edges', status_code=201)
    def create_edge(value: EdgeDraft, request: Request) -> dict:
        if has_nul(value.description):
            raise bad('Remove the NUL character from Description.')
        if value.from_id == value.to_id:
            raise HTTPException(422, SAME_NODE)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            for node_id in (value.from_id, value.to_id):
                node_row(conn, project, node_id)
            actor = db.actor(conn, *request_actor(request))
            with refused(conn, DUPLICATE_EDGE, (404, NO_NODE)):
                edge_id = conn.execute("""INSERT INTO ka.kg_edges(project_id,from_id,to_id,type,description,created_by)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (project, value.from_id, value.to_id, value.type, value.description, actor)).fetchone()['id']
            audit(conn, project, request, 'graph.edge.created', 'graph_edge', edge_id,
                  {'type': value.type, 'from_id': str(value.from_id), 'to_id': str(value.to_id)})
            return {'edge': edge_row(conn, project, edge_id)}

    @router.put('/graph/edges/{edge_id}')
    def save_edge(edge_id: UUID, value: EdgeChange, request: Request) -> dict:
        if has_nul(value.description):
            raise bad('Remove the NUL character from Description.')
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            current = edge_row(conn, project, edge_id, lock=True)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE_EDGE)
            conn.execute('UPDATE ka.kg_edges SET description=%s,revision=revision+1 WHERE id=%s', (value.description, edge_id))
            audit(conn, project, request, 'graph.edge.updated', 'graph_edge', edge_id, {'type': current['type']})
            return {'edge': edge_row(conn, project, edge_id)}

    @router.delete('/graph/edges/{edge_id}', status_code=204)
    def delete_edge(edge_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            row = conn.execute('DELETE FROM ka.kg_edges WHERE id=%s AND project_id=%s RETURNING type,from_id,to_id',
                               (edge_id, project)).fetchone()
            if row is None:
                raise HTTPException(404, NO_EDGE)
            audit(conn, project, request, 'graph.edge.deleted', 'graph_edge', edge_id,
                  {'type': row['type'], 'from_id': str(row['from_id']), 'to_id': str(row['to_id'])})
        return Response(status_code=204)

    @router.put('/graph/nodes/{node_id}/documents/{document_id}', status_code=204)
    def attach(node_id: UUID, document_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            node_row(conn, project, node_id)
            if conn.execute('SELECT 1 FROM ka.documents WHERE id=%s AND project_id=%s', (document_id, project)).fetchone() is None:
                raise HTTPException(404, NO_DOCUMENT)
            with refused(conn, '', (404, NO_DOCUMENT)):  # the document was deleted meanwhile
                added = conn.execute("""INSERT INTO ka.kg_node_documents(node_id,document_id,project_id) VALUES (%s,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING node_id""", (node_id, document_id, project)).fetchone()
            if added:
                audit(conn, project, request, 'graph.document.attached', 'graph_node', node_id, {'document_id': str(document_id)})
        return Response(status_code=204)

    @router.delete('/graph/nodes/{node_id}/documents/{document_id}', status_code=204)
    def detach(node_id: UUID, document_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            node_row(conn, project, node_id)
            removed = conn.execute('DELETE FROM ka.kg_node_documents WHERE node_id=%s AND document_id=%s RETURNING node_id',
                                   (node_id, document_id)).fetchone()
            if removed:
                audit(conn, project, request, 'graph.document.detached', 'graph_node', node_id, {'document_id': str(document_id)})
        return Response(status_code=204)
```

`detail` (Task 4) is not needed here; do not add an unused import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_system_graph_api.py tests/test_system_graph_migration.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/system_graph_api.py services/admin-api/tests/test_system_graph_api.py
git commit -m "feat(api): system graph edges and attached documents

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `d2` in the admin-api image

**Files:**
- Modify: `deploy/Dockerfile.admin-api`, `deploy/README.md`
- Test: `services/admin-api/tests/test_deploy.py` (append one test)

**Interfaces:**
- Produces: `/usr/local/bin/d2` (v0.9.0) in the admin-api image; `system_graph_render.svg` finds it with `shutil.which`.

- [ ] **Step 1: Write the failing test** — append to `services/admin-api/tests/test_deploy.py` (it already imports
`Path`; if not, add `from pathlib import Path`):

```python
def test_admin_api_image_pins_d2_for_system_map_diagrams():
    dockerfile = (Path(__file__).resolve().parents[3] / 'deploy/Dockerfile.admin-api').read_text()
    assert 'FROM debian:bookworm-slim AS d2' in dockerfile
    assert 'https://github.com/d2lang/d2/releases/download/${D2_VERSION}/d2-${D2_VERSION}-linux-${arch}.tar.gz' in dockerfile
    assert 'ARG D2_VERSION=v0.9.0' in dockerfile
    assert '5669ddc46b99e942cc96078f4a4e36d5e62103348f4c05179ede27802fdd87a9' in dockerfile  # linux-amd64
    assert 'ac2c028697199479acb321db1e3d68caee9f2ba492ed73caa3cd13f3829bf913' in dockerfile  # linux-arm64
    assert 'sha256sum -c -' in dockerfile
    assert 'COPY --from=d2 /usr/local/bin/d2 /usr/local/bin/d2' in dockerfile and 'RUN d2 --version' in dockerfile
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_deploy.py::test_admin_api_image_pins_d2_for_system_map_diagrams`
Expected: FAIL.

- [ ] **Step 3: Change the Dockerfile.** In `deploy/Dockerfile.admin-api`, add this stage after the `codegraph` stage
(before `FROM python:3.12-slim`):

```dockerfile
# D2 draws System map diagrams; the release bundles the TALA, ELK and dagre layout engines.
FROM debian:bookworm-slim AS d2
ARG TARGETARCH
ARG D2_VERSION=v0.9.0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in \
        amd64) sum=5669ddc46b99e942cc96078f4a4e36d5e62103348f4c05179ede27802fdd87a9 ;; \
        arm64) sum=ac2c028697199479acb321db1e3d68caee9f2ba492ed73caa3cd13f3829bf913 ;; \
        *) echo "d2: unsupported architecture $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/d2.tar.gz "https://github.com/d2lang/d2/releases/download/${D2_VERSION}/d2-${D2_VERSION}-linux-${arch}.tar.gz"; \
    echo "$sum  /tmp/d2.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/d2.tar.gz -C /tmp; \
    install -m 0755 "/tmp/d2-${D2_VERSION}/bin/d2" /usr/local/bin/d2
```

and in the final stage, right after `RUN ast-grep --version`:

```dockerfile
COPY --from=d2 /usr/local/bin/d2 /usr/local/bin/d2
RUN d2 --version
```

Add to `deploy/README.md`, after the `## Upgrading to agent tasks and Notifications` section:

```markdown
## Upgrading to the system graph

After pulling this version, run the database migration (`make up` runs it, or `make db-migrate`). Migration 0092 adds
the system graph tables (Environments → System map).

- The admin-api image now contains `d2` v0.9.0 (with the TALA, ELK and dagre layouts) to draw System map diagrams;
  rebuild admin-api (`make up` does). The build downloads the release from GitHub and checks its sha256.
- Without `d2` (for example admin-api running outside Docker), the Diagram view says "d2 is not installed." and the
  Mermaid, D2 and JSON views still work.
```

- [ ] **Step 4: Verify**

Run: `cd services/admin-api && uv run pytest -q tests/test_deploy.py::test_admin_api_image_pins_d2_for_system_map_diagrams`
Expected: PASS. Then build only the new stage and run it (no make; this does not touch the running stack):
`docker build --target d2 -f deploy/Dockerfile.admin-api -t ka-d2-check .` from the worktree root, then
`docker run --rm ka-d2-check /usr/local/bin/d2 --version` → `v0.9.0`, and
`docker run --rm ka-d2-check /usr/local/bin/d2 layout` lists `tala (bundled)`. Remove the image afterwards with
`docker rmi ka-d2-check`; if that command is refused, report it and leave the image.

- [ ] **Step 5: Commit**

```bash
git add deploy/Dockerfile.admin-api deploy/README.md services/admin-api/tests/test_deploy.py
git commit -m "build: admin-api image ships d2 v0.9.0 for System map diagrams

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 7: UI — `lib/systemGraph.ts` and the node sheet

**Files:**
- Create: `services/admin-ui/app/lib/systemGraph.ts`
- Create: `services/admin-ui/app/components/NodeSheet.tsx`
- Test: `services/admin-ui/app/components/__tests__/nodeSheet.test.tsx`

**Interfaces:**
- Consumes: the API from Tasks 4–5; `request`, `ApiError` from `app/lib/api.ts`; `getDocuments` from
  `app/lib/documents.ts` (`GET /api/documentation/documents?q=…` → `{documents: DocumentSummary[], total}`).
- Produces (`systemGraph.ts`): types `Layer`, `EdgeType`, `MapView`, `Layout`, `GraphView`, `ViewNode`,
  `BoundaryNode`, `ViewEdge`, `NodeDetail`, `EdgeRow`, `NodeHit`, `GraphDocument`, `MapState`; constants `LAYERS`,
  `LAYER_LABELS`, `EDGE_TYPES`, `EDGE_LABELS`, `KIND_SUGGESTIONS`, `VIEWS`, `VIEW_LABELS`, `LAYOUTS`, `MAX_DEPTH`;
  functions `readMapState(params)`, `mapQuery(state)`, `viewUrl(layer, root, depth, format, layout?)`,
  `getView`, `getViewText`, `getNode`, `searchNodes(q, layer?)`, `createNode`, `updateNode`, `deleteNode`,
  `createEdge`, `deleteEdge`, `attachDocument`, `detachDocument`.
- Produces (`NodeSheet.tsx`): `export type SheetTarget = { nodeId: string } | { create: { layer: Layer; parentId:
  string | null; parentName: string | null } }` and `export default function NodeSheet({ target, onClose, onChanged })`.

- [ ] **Step 1: Write `services/admin-ui/app/lib/systemGraph.ts`** (types and calls; it is exercised by the tests below)

```ts
import { request } from "./api";

export type Layer = "business" | "software" | "data" | "infrastructure";
export const LAYERS: Layer[] = ["business", "software", "data", "infrastructure"];
export const LAYER_LABELS: Record<Layer, string> = { business: "Business", software: "Software", data: "Data", infrastructure: "Infrastructure" };
export const EDGE_TYPES = ["calls", "uses", "depends_on", "reads", "writes", "publishes", "subscribes", "instance_of", "runs_on", "implements", "stores"] as const;
export type EdgeType = typeof EDGE_TYPES[number];
export const EDGE_LABELS: Record<EdgeType, string> = { calls: "calls", uses: "uses", depends_on: "depends on", reads: "reads",
  writes: "writes", publishes: "publishes", subscribes: "subscribes", instance_of: "instance of", runs_on: "runs on",
  implements: "implements", stores: "stores" };
export const KIND_SUGGESTIONS: Record<Layer, string[]> = {
  business: ["capability", "process", "actor"],
  software: ["system", "service", "module", "component", "library", "job"],
  data: ["database", "schema", "table", "queue", "bucket"],
  infrastructure: ["environment", "cluster", "server", "container", "network"],
};
export type MapView = "diagram" | "mermaid" | "d2" | "json";
export const VIEWS: MapView[] = ["diagram", "mermaid", "d2", "json"];
export const VIEW_LABELS: Record<MapView, string> = { diagram: "Diagram", mermaid: "Mermaid", d2: "D2", json: "JSON" };
export type Layout = "tala" | "elk" | "dagre";
export const LAYOUTS: Layout[] = ["tala", "elk", "dagre"];
export const MAX_DEPTH = 5;

export interface GraphDocument { id: string; slug: string; title: string }
export interface ViewNode { id: string; parent_id: string | null; layer: Layer; kind: string; name: string; description: string;
  child_count: number; expandable: boolean; internal_edges: number; documents: GraphDocument[] }
export interface BoundaryNode { id: string; name: string; kind: string; layer: Layer }
export interface ViewEdge { from: string; to: string; type: EdgeType; count: number; edge_ids: string[] }
export interface Crumb { id: string; name: string; kind: string }
export interface GraphView { layer: Layer; depth: number; root: (Crumb & { description: string }) | null; breadcrumb: Crumb[];
  nodes: ViewNode[]; boundary: BoundaryNode[]; edges: ViewEdge[] }
export interface EdgeRow { id: string; type: EdgeType; description: string; revision: number;
  node: { id: string; name: string; kind: string; layer: Layer } }
export interface NodeDetail { id: string; layer: Layer; kind: string; name: string; description: string; parent_id: string | null;
  properties: Record<string, unknown>; revision: number; created_at: string; updated_at: string; breadcrumb: Crumb[];
  children: (Crumb & { child_count: number })[]; outgoing: EdgeRow[]; incoming: EdgeRow[]; documents: GraphDocument[] }
export interface NodeHit { id: string; layer: Layer; kind: string; name: string; path: string[] }
export interface NodeFieldsInput { kind: string; name: string; description: string; properties: Record<string, unknown>; parent_id: string | null }
export interface NewNodeInput extends NodeFieldsInput { layer: Layer }
export interface NodeChangeInput extends NodeFieldsInput { revision: number }
export interface NewEdgeInput { from_id: string; to_id: string; type: EdgeType; description: string }

export interface MapState { layer: Layer; root: string | null; depth: number; view: MapView; layout: Layout }

function pick<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? value as T : fallback;
}
/** The System map's state from the page URL; anything unknown falls back to the defaults. */
export function readMapState(params: URLSearchParams): MapState {
  const depth = Number(params.get("depth"));
  return { layer: pick(params.get("layer"), LAYERS, "software"), root: params.get("root") || null,
    depth: Number.isInteger(depth) && depth >= 1 && depth <= MAX_DEPTH ? depth : 1,
    view: pick(params.get("view"), VIEWS, "diagram"), layout: pick(params.get("layout"), LAYOUTS, "tala") };
}
/** The page URL for a state: the layer always, everything else only when it is not the default. */
export function mapQuery(state: MapState): URLSearchParams {
  const query = new URLSearchParams({ layer: state.layer });
  if (state.root) query.set("root", state.root);
  if (state.depth !== 1) query.set("depth", String(state.depth));
  if (state.view !== "diagram") query.set("view", state.view);
  if (state.layout !== "tala") query.set("layout", state.layout);
  return query;
}
export function viewUrl(layer: Layer, root: string | null, depth: number, format: "json" | "mermaid" | "d2" | "svg", layout: Layout = "tala") {
  const query = new URLSearchParams();
  if (root) query.set("root", root);
  query.set("depth", String(depth));
  query.set("format", format);
  if (format === "svg") query.set("layout", layout);
  return `/api/graph/view/${layer}?${query}`;
}
const send = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const getView = (layer: Layer, root: string | null, depth: number, signal?: AbortSignal) =>
  request<GraphView>(viewUrl(layer, root, depth, "json"), { signal });
export const getViewText = (layer: Layer, root: string | null, depth: number, format: "mermaid" | "d2" | "svg", layout: Layout, signal?: AbortSignal) =>
  request<string>(viewUrl(layer, root, depth, format, layout), { signal }, "text");
export const getNode = (id: string, signal?: AbortSignal) => request<{ node: NodeDetail }>(`/api/graph/nodes/${id}`, { signal });
export function searchNodes(q: string, layer?: Layer) {
  const query = new URLSearchParams();
  if (layer) query.set("layer", layer);
  query.set("q", q);
  return request<{ nodes: NodeHit[] }>(`/api/graph/nodes?${query}`);
}
export const createNode = (input: NewNodeInput) => request<{ node: NodeDetail }>("/api/graph/nodes", send("POST", input));
export const updateNode = (id: string, input: NodeChangeInput) => request<{ node: NodeDetail }>(`/api/graph/nodes/${id}`, send("PUT", input));
export const deleteNode = (id: string) => request<void>(`/api/graph/nodes/${id}`, { method: "DELETE" });
export const createEdge = (input: NewEdgeInput) => request<{ edge: unknown }>("/api/graph/edges", send("POST", input));
export const deleteEdge = (id: string) => request<void>(`/api/graph/edges/${id}`, { method: "DELETE" });
export const attachDocument = (nodeId: string, documentId: string) =>
  request<void>(`/api/graph/nodes/${nodeId}/documents/${documentId}`, { method: "PUT" });
export const detachDocument = (nodeId: string, documentId: string) =>
  request<void>(`/api/graph/nodes/${nodeId}/documents/${documentId}`, { method: "DELETE" });
```

- [ ] **Step 2: Write the failing test** `services/admin-ui/app/components/__tests__/nodeSheet.test.tsx`

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import NodeSheet, { type SheetTarget } from "../NodeSheet";
import { mapQuery, readMapState } from "../../lib/systemGraph";

const events = { id: "events", layer: "software", kind: "module", name: "Event processor", description: "Runs handlers", parent_id: "api",
  properties: { language: "python" }, revision: 3, created_at: "2026-10-10T10:00:00Z", updated_at: "2026-10-10T10:00:00Z",
  breadcrumb: [{ id: "api", name: "admin-api", kind: "service" }, { id: "events", name: "Event processor", kind: "module" }],
  children: [{ id: "match", name: "matching", kind: "module", child_count: 0 }],
  outgoing: [{ id: "e1", type: "calls", description: "", revision: 1, node: { id: "worker", name: "repository-worker", kind: "service", layer: "software" } }],
  incoming: [{ id: "e2", type: "instance_of", description: "", revision: 1, node: { id: "prod", name: "prod-1", kind: "server", layer: "infrastructure" } }],
  documents: [{ id: "d1", slug: "runbook", title: "Runbook" }] };

type Reply = { status?: number; body?: unknown };
interface Call { method: string; url: string; body: unknown }
function api(routes: Record<string, (body: unknown) => Reply>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body });
    const route = routes[`${method} ${url}`];
    if (!route) throw new Error(`unexpected ${method} ${url}`);
    const { status = 200, body: out = {} } = route(body);
    return new Response(status === 204 ? null : JSON.stringify(out), { status });
  }));
  return calls;
}
const sent = (calls: Call[], method: string) => calls.filter(call => call.method === method);
function open(target: SheetTarget) {
  const onClose = vi.fn(), onChanged = vi.fn();
  render(<MemoryRouter><NodeSheet target={target} onClose={onClose} onChanged={onChanged} /></MemoryRouter>);
  return { onClose, onChanged };
}
const loaded = { "GET /api/graph/nodes/events": () => ({ body: { node: events } }) };
afterEach(() => vi.unstubAllGlobals());

test("the map state reads and writes the URL with defaults left out", () => {
  expect(readMapState(new URLSearchParams("layer=data&root=x&depth=9&view=png&layout=elk"))).toEqual(
    { layer: "data", root: "x", depth: 1, view: "diagram", layout: "elk" });
  expect(readMapState(new URLSearchParams(""))).toEqual({ layer: "software", root: null, depth: 1, view: "diagram", layout: "tala" });
  expect(mapQuery({ layer: "software", root: null, depth: 1, view: "diagram", layout: "tala" }).toString()).toBe("layer=software");
  expect(mapQuery({ layer: "data", root: "x", depth: 2, view: "d2", layout: "elk" }).toString()).toBe("layer=data&root=x&depth=2&view=d2&layout=elk");
});

test("creates a node inside the current root", async () => {
  const user = userEvent.setup();
  const calls = api({ "POST /api/graph/nodes": () => ({ status: 201, body: { node: events } }) });
  const { onClose, onChanged } = open({ create: { layer: "software", parentId: "api", parentName: "admin-api" } });
  expect(screen.getByText("In the Software layer, inside admin-api.")).toBeInTheDocument();
  const create = screen.getByRole("button", { name: "Create" });
  expect(create).toBeDisabled();
  await user.type(screen.getByLabelText("Kind"), "module");
  await user.type(screen.getByLabelText("Name"), "  Event processor ");
  await user.click(screen.getByLabelText("Properties"));
  await user.paste("[1]");
  expect(screen.getByText('Properties must be a JSON object, like {"port": 8766}.')).toBeInTheDocument();
  expect(create).toBeDisabled();
  await user.clear(screen.getByLabelText("Properties"));
  await user.paste('{"language": "python"}');
  await user.click(create);
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(onChanged).toHaveBeenCalled();
  expect(sent(calls, "POST")[0].body).toEqual({ layer: "software", parent_id: "api", kind: "module", name: "Event processor",
    description: "", properties: { language: "python" } });
});

test("shows the API's message when creating fails", async () => {
  const user = userEvent.setup();
  api({ "POST /api/graph/nodes": () => ({ status: 409, body: { error: { message: 'A node named "x" already exists here.' } } }) });
  open({ create: { layer: "data", parentId: null, parentName: null } });
  expect(screen.getByText("In the Data layer, at the top level.")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Kind"), "table");
  await user.type(screen.getByLabelText("Name"), "x");
  await user.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByText('A node named "x" already exists here.')).toBeInTheDocument();
});

test("edits a node and saves it with its revision", async () => {
  const user = userEvent.setup();
  const calls = api({ ...loaded, "PUT /api/graph/nodes/events": () => ({ body: { node: { ...events, name: "Events", revision: 4 } } }) });
  const { onChanged } = open({ nodeId: "events" });
  expect(await screen.findByText("Software · admin-api › Event processor")).toBeInTheDocument();
  const name = screen.getByLabelText("Name");
  expect(name).toHaveValue("Event processor");
  expect(screen.getByLabelText("Properties")).toHaveValue('{\n  "language": "python"\n}');
  await user.clear(name);
  await user.type(name, "Events");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(sent(calls, "PUT")[0].body).toEqual({ kind: "module", name: "Events", description: "Runs handlers",
    properties: { language: "python" }, parent_id: "api", revision: 3 });
});

test("a stale save shows the message", async () => {
  const user = userEvent.setup();
  api({ ...loaded, "PUT /api/graph/nodes/events": () => ({ status: 409,
    body: { error: { message: "This node was changed by someone else. Reload and try again." } } }) });
  open({ nodeId: "events" });
  await user.click(await screen.findByRole("button", { name: "Save" }));
  expect(await screen.findByText("This node was changed by someone else. Reload and try again.")).toBeInTheDocument();
});

test("moves the node under a node found by name, or to the top level", async () => {
  const user = userEvent.setup();
  const calls = api({ ...loaded,
    "GET /api/graph/nodes?layer=software&q=work": () => ({ body: { nodes: [
      { id: "worker", layer: "software", kind: "service", name: "repository-worker", path: [] },
      { id: "events", layer: "software", kind: "module", name: "Event processor", path: ["admin-api"] }] } }),
    "PUT /api/graph/nodes/events": () => ({ body: { node: events } }) });
  open({ nodeId: "events" });
  const move = within(await screen.findByRole("region", { name: "Move" }));
  await user.type(move.getByLabelText("New parent"), "work");
  await user.click(move.getByRole("button", { name: "Find" }));
  expect(await move.findByText("repository-worker")).toBeInTheDocument();
  expect(move.queryByRole("button", { name: "Move here: Event processor" })).toBeNull();  // not into itself
  await user.click(move.getByRole("button", { name: "Move here: repository-worker" }));
  await waitFor(() => expect(sent(calls, "PUT")).toHaveLength(1));
  expect(sent(calls, "PUT")[0].body).toEqual({ kind: "module", name: "Event processor", description: "Runs handlers",
    properties: { language: "python" }, parent_id: "worker", revision: 3 });
  await user.click(move.getByRole("button", { name: "Move to the top level" }));
  await waitFor(() => expect(sent(calls, "PUT")).toHaveLength(2));
  expect((sent(calls, "PUT")[1].body as { parent_id: unknown }).parent_id).toBeNull();
});

test("lists, adds and removes edges", async () => {
  const user = userEvent.setup();
  const calls = api({ ...loaded,
    "GET /api/graph/nodes?q=prod": () => ({ body: { nodes: [{ id: "prod", layer: "infrastructure", kind: "server", name: "prod-1", path: [] }] } }),
    "POST /api/graph/edges": () => ({ status: 201, body: { edge: {} } }),
    "DELETE /api/graph/edges/e1": () => ({ status: 204 }) });
  open({ nodeId: "events" });
  const edges = within(await screen.findByRole("region", { name: "Edges" }));
  expect(edges.getByText("calls → repository-worker")).toBeInTheDocument();
  expect(edges.getByText("prod-1 → instance of")).toBeInTheDocument();
  await user.type(edges.getByLabelText("Target"), "prod");
  await user.click(edges.getByRole("button", { name: "Find" }));
  expect(await edges.findByText("prod-1 · Infrastructure")).toBeInTheDocument();
  await user.click(edges.getByRole("button", { name: "Add edge: prod-1" }));
  await waitFor(() => expect(sent(calls, "POST")).toHaveLength(1));
  expect(sent(calls, "POST")[0].body).toEqual({ from_id: "events", to_id: "prod", type: "uses", description: "" });
  await user.click(edges.getByRole("button", { name: "Remove calls edge to repository-worker" }));
  await waitFor(() => expect(sent(calls, "DELETE").map(call => call.url)).toEqual(["/api/graph/edges/e1"]));
});

test("attaches and detaches documents", async () => {
  const user = userEvent.setup();
  const calls = api({ ...loaded,
    "GET /api/documentation/documents?q=pay": () => ({ body: { documents: [{ id: "d2", slug: "payments", title: "Payments" },
      { id: "d1", slug: "runbook", title: "Runbook" }], total: 2 } }),
    "PUT /api/graph/nodes/events/documents/d2": () => ({ status: 204 }),
    "DELETE /api/graph/nodes/events/documents/d1": () => ({ status: 204 }) });
  open({ nodeId: "events" });
  const documents = within(await screen.findByRole("region", { name: "Documents" }));
  expect(documents.getByRole("link", { name: "Runbook" })).toHaveAttribute("href", "/documents/d1");
  await user.type(documents.getByLabelText("Find a document"), "pay");
  await user.click(documents.getByRole("button", { name: "Find" }));
  await user.click(await documents.findByRole("button", { name: "Attach: Payments" }));
  expect(documents.queryByRole("button", { name: "Attach: Runbook" })).toBeNull();  // already attached
  await user.click(documents.getByRole("button", { name: "Detach Runbook" }));
  await waitFor(() => expect(calls.filter(call => call.url.includes("/documents/d")).map(call => `${call.method} ${call.url}`)).toEqual([
    "PUT /api/graph/nodes/events/documents/d2", "DELETE /api/graph/nodes/events/documents/d1"]));
});

test("deletes after confirming, and shows why it cannot", async () => {
  const user = userEvent.setup();
  let reply: Reply = { status: 409, body: { error: { message: "Move or delete its children first." } } };
  api({ ...loaded, "DELETE /api/graph/nodes/events": () => reply });
  const { onClose, onChanged } = open({ nodeId: "events" });
  await user.click(await screen.findByRole("button", { name: "Delete" }));
  await user.click(screen.getByRole("button", { name: "Delete node" }));
  expect(await screen.findByText("Move or delete its children first.")).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
  reply = { status: 204 };
  await user.click(screen.getByRole("button", { name: "Delete" }));
  await user.click(screen.getByRole("button", { name: "Delete node" }));
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(onChanged).toHaveBeenCalled();
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/nodeSheet.test.tsx`
Expected: FAIL (`Failed to resolve import "../NodeSheet"`).

- [ ] **Step 4: Write `services/admin-ui/app/components/NodeSheet.tsx`**

```tsx
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { ApiError } from "../lib/api";
import { getDocuments, type DocumentSummary } from "../lib/documents";
import { EDGE_LABELS, EDGE_TYPES, KIND_SUGGESTIONS, LAYER_LABELS, attachDocument, createEdge, createNode, deleteEdge, deleteNode,
  detachDocument, getNode, searchNodes, updateNode, type EdgeType, type Layer, type NodeDetail, type NodeHit } from "../lib/systemGraph";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

export type SheetTarget = { nodeId: string } | { create: { layer: Layer; parentId: string | null; parentName: string | null } };

interface Draft { kind: string; name: string; description: string; properties: string }
const EMPTY: Draft = { kind: "", name: "", description: "", properties: "" };
const say = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

/** Reads the Properties field: the object it holds, or what is wrong with it. */
function parseProperties(text: string): { value: Record<string, unknown> } | { problem: string } {
  if (!text.trim()) return { value: {} };
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { return { problem: "Properties must be valid JSON." }; }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return { problem: 'Properties must be a JSON object, like {"port": 8766}.' };
  return { value: parsed as Record<string, unknown> };
}
const ready = (draft: Draft) => draft.kind.trim() !== "" && draft.name.trim() !== "" && "value" in parseProperties(draft.properties);
const propertiesOf = (draft: Draft) => { const parsed = parseProperties(draft.properties); return "value" in parsed ? parsed.value : {}; };

function DraftFields({ id, layer, draft, onChange }: { id: string; layer: Layer; draft: Draft; onChange: (draft: Draft) => void }) {
  const parsed = parseProperties(draft.properties);
  const problem = "problem" in parsed ? parsed.problem : null;
  return <FieldGroup>
    <Field><FieldLabel htmlFor={`${id}-kind`}>Kind</FieldLabel>
      <Input id={`${id}-kind`} list={`${id}-kinds`} value={draft.kind} maxLength={60} autoComplete="off" onChange={event => onChange({ ...draft, kind: event.target.value })} />
      <datalist id={`${id}-kinds`}>{KIND_SUGGESTIONS[layer].map(kind => <option key={kind} value={kind} />)}</datalist></Field>
    <Field><FieldLabel htmlFor={`${id}-name`}>Name</FieldLabel>
      <Input id={`${id}-name`} value={draft.name} maxLength={160} onChange={event => onChange({ ...draft, name: event.target.value })} /></Field>
    <Field><FieldLabel htmlFor={`${id}-description`}>Description</FieldLabel>
      <Textarea id={`${id}-description`} value={draft.description} maxLength={4000} rows={3} onChange={event => onChange({ ...draft, description: event.target.value })} /></Field>
    <Field data-invalid={problem !== null || undefined}><FieldLabel htmlFor={`${id}-properties`}>Properties</FieldLabel>
      <Textarea id={`${id}-properties`} value={draft.properties} rows={4} spellCheck={false} className="font-mono text-xs" placeholder='{"port": 8766}'
        aria-invalid={problem !== null} onChange={event => onChange({ ...draft, properties: event.target.value })} />
      {problem ? <FieldError>{problem}</FieldError> : <FieldDescription>Optional JSON object with extra facts.</FieldDescription>}</Field>
  </FieldGroup>;
}

/** Search by name; each result gets one action button. */
function NodePicker({ label, layer, exclude, action, onPick }: { label: string; layer?: Layer; exclude: string; action: string; onPick: (hit: NodeHit) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<NodeHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function find(event: FormEvent) {
    event.preventDefault(); setError(null);
    try { setHits((await searchNodes(q.trim(), layer)).nodes.filter(hit => hit.id !== exclude)); }
    catch (reason) { setError(say(reason, "Could not search.")); }
  }
  return <form onSubmit={find} className="flex flex-col gap-2">
    <div className="flex gap-2"><Input aria-label={label} value={q} placeholder="Search by name" onChange={event => setQ(event.target.value)} />
      <Button type="submit" variant="outline">Find</Button></div>
    {error && <p className="text-destructive text-sm">{error}</p>}
    {hits && (hits.length === 0 ? <p className="text-muted-foreground text-sm">No nodes found.</p>
      : <ul className="flex flex-col gap-1">{hits.map(hit => <li key={hit.id} className="flex items-center justify-between gap-2 text-sm">
        <span>{hit.path.length > 0 && <span className="text-muted-foreground">{hit.path.join(" › ")} › </span>}
          {layer ? hit.name : `${hit.name} · ${LAYER_LABELS[hit.layer]}`}</span>
        <Button type="button" size="sm" variant="outline" aria-label={`${action}: ${hit.name}`} onClick={() => onPick(hit)}>{action}</Button></li>)}</ul>)}
  </form>;
}

function DocumentPicker({ attached, onPick }: { attached: string[]; onPick: (document: DocumentSummary) => void }) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function find(event: FormEvent) {
    event.preventDefault(); setError(null);
    try { setFound((await getDocuments(new URLSearchParams({ q: q.trim() }))).documents); }
    catch (reason) { setError(say(reason, "Could not search documents.")); }
  }
  const shown = found?.filter(document => !attached.includes(document.id));
  return <form onSubmit={find} className="flex flex-col gap-2">
    <div className="flex gap-2"><Input aria-label="Find a document" value={q} placeholder="Title or slug" onChange={event => setQ(event.target.value)} />
      <Button type="submit" variant="outline">Find</Button></div>
    {error && <p className="text-destructive text-sm">{error}</p>}
    {shown && (shown.length === 0 ? <p className="text-muted-foreground text-sm">No documents to attach.</p>
      : <ul className="flex flex-col gap-1">{shown.map(document => <li key={document.id} className="flex items-center justify-between gap-2 text-sm">
        <span>{document.title}</span>
        <Button type="button" size="sm" variant="outline" aria-label={`Attach: ${document.title}`} onClick={() => onPick(document)}>Attach</Button></li>)}</ul>)}
  </form>;
}

function CreateNode({ layer, parentId, parentName, onCreated }: { layer: Layer; parentId: string | null; parentName: string | null; onCreated: () => void }) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(null);
    try {
      await createNode({ layer, parent_id: parentId, kind: draft.kind.trim(), name: draft.name.trim(), description: draft.description, properties: propertiesOf(draft) });
      onCreated();
    } catch (reason) { setError(say(reason, "Could not create the node.")); setSaving(false); }
  }
  return <>
    <SheetHeader><SheetTitle>New node</SheetTitle>
      <SheetDescription>In the {LAYER_LABELS[layer]} layer, {parentName ? `inside ${parentName}` : "at the top level"}.</SheetDescription></SheetHeader>
    <form onSubmit={submit} className="flex flex-col gap-4 px-4 pb-4">
      <DraftFields id="new-node" layer={layer} draft={draft} onChange={setDraft} />
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <SheetFooter><Button type="submit" disabled={saving || !ready(draft)}>{saving && <Spinner data-icon="inline-start" />}Create</Button></SheetFooter>
    </form>
  </>;
}

function NodeDetails({ nodeId, onChanged, onDeleted }: { nodeId: string; onChanged: () => void; onDeleted: () => void }) {
  const [node, setNode] = useState<NodeDetail | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [edgeType, setEdgeType] = useState<EdgeType>("uses");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const load = useCallback(async () => {
    try {
      const { node: loaded } = await getNode(nodeId);
      setNode(loaded);
      setDraft({ kind: loaded.kind, name: loaded.name, description: loaded.description,
        properties: Object.keys(loaded.properties).length ? JSON.stringify(loaded.properties, null, 2) : "" });
    } catch (reason) { setError(say(reason, "Could not load the node.")); }
  }, [nodeId]);
  useEffect(() => { void load(); }, [load]);

  async function act(work: () => Promise<unknown>, fallback: string) {
    setBusy(true); setError(null);
    try { await work(); onChanged(); await load(); }
    catch (reason) { setError(say(reason, fallback)); }
    finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true); setError(null);
    try { await deleteNode(nodeId); onDeleted(); }
    catch (reason) { setError(say(reason, "Could not delete the node.")); setBusy(false); setConfirming(false); }
  }

  if (!node) return <>
    <SheetHeader><SheetTitle>Node</SheetTitle><SheetDescription>Loading…</SheetDescription></SheetHeader>
    <div className="px-4">{error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Spinner />}</div>
  </>;
  // Moving keeps the saved fields; unsaved edits in the form stay in the form.
  const moveTo = (parentId: string | null) => act(() => updateNode(node.id, { kind: node.kind, name: node.name, description: node.description,
    properties: node.properties, parent_id: parentId, revision: node.revision }), "Could not move the node.");
  const save = (event: FormEvent) => {
    event.preventDefault();
    void act(() => updateNode(node.id, { kind: draft.kind.trim(), name: draft.name.trim(), description: draft.description,
      properties: propertiesOf(draft), parent_id: node.parent_id, revision: node.revision }), "Could not save the node.");
  };
  const noEdges = node.outgoing.length === 0 && node.incoming.length === 0;
  return <>
    <SheetHeader><SheetTitle>{node.name}</SheetTitle>
      <SheetDescription>{LAYER_LABELS[node.layer]} · {node.breadcrumb.map(crumb => crumb.name).join(" › ")}</SheetDescription></SheetHeader>
    <div className="flex flex-col gap-6 px-4 pb-4">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      <form onSubmit={save} className="flex flex-col gap-4">
        <DraftFields id="node" layer={node.layer} draft={draft} onChange={setDraft} />
        <div><Button type="submit" disabled={busy || !ready(draft)}>Save</Button></div>
      </form>
      <section aria-label="Move" className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Move</h3>
        <NodePicker label="New parent" layer={node.layer} exclude={node.id} action="Move here" onPick={hit => void moveTo(hit.id)} />
        {node.parent_id && <div><Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void moveTo(null)}>Move to the top level</Button></div>}
      </section>
      <section aria-label="Edges" className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Edges</h3>
        {noEdges ? <p className="text-muted-foreground text-sm">No edges yet.</p> : <ul className="flex flex-col gap-1 text-sm">
          {node.outgoing.map(edge => <li key={edge.id} className="flex items-center justify-between gap-2">
            <span>{`${EDGE_LABELS[edge.type]} → ${edge.node.name}`}</span>
            <Button type="button" size="sm" variant="ghost" aria-label={`Remove ${EDGE_LABELS[edge.type]} edge to ${edge.node.name}`}
              onClick={() => void act(() => deleteEdge(edge.id), "Could not remove the edge.")}>Remove</Button></li>)}
          {node.incoming.map(edge => <li key={edge.id} className="flex items-center justify-between gap-2">
            <span>{`${edge.node.name} → ${EDGE_LABELS[edge.type]}`}</span>
            <Button type="button" size="sm" variant="ghost" aria-label={`Remove ${EDGE_LABELS[edge.type]} edge from ${edge.node.name}`}
              onClick={() => void act(() => deleteEdge(edge.id), "Could not remove the edge.")}>Remove</Button></li>)}
        </ul>}
        <Field><FieldLabel htmlFor="node-edge-type">New edge</FieldLabel>
          <Select value={edgeType} onValueChange={value => setEdgeType(value as EdgeType)}>
            <SelectTrigger id="node-edge-type" aria-label="Edge type" className="w-full"><SelectValue /></SelectTrigger>
            <SelectContent><SelectGroup>{EDGE_TYPES.map(type => <SelectItem key={type} value={type}>{EDGE_LABELS[type]}</SelectItem>)}</SelectGroup></SelectContent>
          </Select></Field>
        <NodePicker label="Target" exclude={node.id} action="Add edge"
          onPick={hit => void act(() => createEdge({ from_id: node.id, to_id: hit.id, type: edgeType, description: "" }), "Could not add the edge.")} />
      </section>
      <section aria-label="Documents" className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">Documents</h3>
        {node.documents.length === 0 ? <p className="text-muted-foreground text-sm">No documents attached.</p> : <ul className="flex flex-col gap-1 text-sm">
          {node.documents.map(document => <li key={document.id} className="flex items-center justify-between gap-2">
            <Link to={`/documents/${document.id}`} className="underline">{document.title}</Link>
            <Button type="button" size="sm" variant="ghost" aria-label={`Detach ${document.title}`}
              onClick={() => void act(() => detachDocument(node.id, document.id), "Could not detach the document.")}>Detach</Button></li>)}
        </ul>}
        <DocumentPicker attached={node.documents.map(document => document.id)}
          onPick={document => void act(() => attachDocument(node.id, document.id), "Could not attach the document.")} />
      </section>
      <section aria-label="Delete node" className="flex flex-wrap items-center gap-2">
        {confirming ? <>
          <span className="text-sm">Delete {node.name}? Its edges and document links go with it.</span>
          <Button type="button" variant="destructive" disabled={busy} onClick={() => void remove()}>Delete node</Button>
          <Button type="button" variant="outline" onClick={() => setConfirming(false)}>Cancel</Button>
        </> : <Button type="button" variant="outline" onClick={() => setConfirming(true)}>Delete</Button>}
      </section>
    </div>
  </>;
}

export default function NodeSheet({ target, onClose, onChanged }: { target: SheetTarget; onClose: () => void; onChanged: () => void }) {
  return <Sheet open onOpenChange={next => { if (!next) onClose(); }}>
    <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
      {"create" in target
        ? <CreateNode {...target.create} onCreated={() => { onChanged(); onClose(); }} />
        : <NodeDetails nodeId={target.nodeId} onChanged={onChanged} onDeleted={() => { onChanged(); onClose(); }} />}
    </SheetContent>
  </Sheet>;
}
```

Notes for the implementer: the pick buttons are `type="button"` because they sit inside the search forms; the details
form and the pickers are separate forms (no nesting). If Testing Library cannot find a `region` by the section's
`aria-label`, keep the `aria-label` and query with `screen.getByLabelText` on the section instead — do not drop the
labels. The test's `Remove … edge to/from …` names use `EDGE_LABELS` (`instance of`, not `instance_of`).

- [ ] **Step 5: Run tests and type checks**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/nodeSheet.test.tsx && npx react-router typegen && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui/app/lib/systemGraph.ts services/admin-ui/app/components/NodeSheet.tsx \
  services/admin-ui/app/components/__tests__/nodeSheet.test.tsx
git commit -m "feat(admin-ui): node sheet to create, edit, move and delete graph nodes, edges and documents

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: UI — System map page, navigation, user guide

**Files:**
- Create: `services/admin-ui/app/components/SystemMapView.tsx`, `services/admin-ui/app/routes/systemMap.tsx`
- Modify: `services/admin-ui/app/routes.ts`, `services/admin-ui/app/components/AdminLayout.tsx`,
  `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`, `docs/user-guide.md`
- Test: `services/admin-ui/app/components/__tests__/systemMap.test.tsx`

**Interfaces:**
- Consumes: `systemGraph.ts` (Task 7: `readMapState`, `mapQuery`, `getView`, `getViewText`, `LAYERS`, `LAYER_LABELS`,
  `VIEWS`, `VIEW_LABELS`, `LAYOUTS`, `MAX_DEPTH`, types); `NodeSheet`, `SheetTarget` (Task 7); `Mermaid` default
  export from `app/components/Mermaid.tsx` (`<Mermaid chart={text} />`).
- Produces: route `/system-map`; nav link **System map** in the Environments group.

- [ ] **Step 1: Write the failing test** `services/admin-ui/app/components/__tests__/systemMap.test.tsx`

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import SystemMapView from "../SystemMapView";

vi.mock("../Mermaid", () => ({ default: ({ chart }: { chart: string }) => <pre data-testid="mermaid">{chart}</pre> }));

const node = (id: string, name: string, extra = {}) => ({ id, parent_id: null, layer: "software", kind: "service", name, description: "",
  child_count: 0, expandable: false, internal_edges: 0, documents: [], ...extra });
const top = { layer: "software", depth: 1, root: null, breadcrumb: [],
  nodes: [node("api", "admin-api", { child_count: 2, expandable: true }), node("worker", "repository-worker")], boundary: [],
  edges: [{ from: "api", to: "worker", type: "calls", count: 2, edge_ids: ["e1", "e2"] }] };
const inside = { layer: "software", depth: 1, root: { id: "api", name: "admin-api", kind: "service", description: "" },
  breadcrumb: [{ id: "api", name: "admin-api", kind: "service" }], nodes: [node("events", "Event processor", { kind: "module" })],
  boundary: [{ id: "prod", name: "prod-1", kind: "server", layer: "infrastructure" }],
  edges: [{ from: "prod", to: "events", type: "instance_of", count: 1, edge_ids: ["e3"] }] };
const empty = { layer: "data", depth: 1, root: null, breadcrumb: [], nodes: [], boundary: [], edges: [] };

type Page = { status?: number; json?: unknown; text?: string; error?: string };
function api(pages: Record<string, Page>) {
  const fetch = vi.fn(async (url: string) => {
    const page = pages[url];
    if (!page) throw new Error(`unexpected ${url}`);
    if (page.error) return new Response(JSON.stringify({ error: { message: page.error } }), { status: page.status ?? 500 });
    return new Response(page.text ?? JSON.stringify(page.json), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const urls = (fetch: ReturnType<typeof api>) => fetch.mock.calls.map(call => call[0]);
function Where() { return <output data-testid="where">{useLocation().search}</output>; }
const view = (entry = "/system-map") => render(<MemoryRouter initialEntries={[entry]}>
  <Routes><Route path="/system-map" element={<><SystemMapView /><Where /></>} /></Routes></MemoryRouter>);
const where = () => screen.getByTestId("where").textContent;
const TOP = "/api/graph/view/software?depth=1&format=json";
const TOP_SVG = "/api/graph/view/software?depth=1&format=svg&layout=tala";
const API = "/api/graph/view/software?root=api&depth=1&format=json";
const API_SVG = "/api/graph/view/software?root=api&depth=1&format=svg&layout=tala";
afterEach(() => vi.unstubAllGlobals());

test("shows the top of a layer as a diagram with Inside and Outside lists", async () => {
  api({ [TOP]: { json: top }, [TOP_SVG]: { text: "<svg><text>admin-api</text></svg>" } });
  view();
  expect(screen.getByRole("tab", { name: "Software" })).toHaveAttribute("aria-selected", "true");
  const diagram = await screen.findByRole("img", { name: "Software map" });
  expect(diagram.getAttribute("src")).toBe(`data:image/svg+xml;charset=utf-8,${encodeURIComponent("<svg><text>admin-api</text></svg>")}`);
  const insideList = within(screen.getByRole("region", { name: "Inside" }));
  expect(insideList.getByRole("button", { name: "admin-api" })).toBeInTheDocument();
  expect(insideList.getByText("service · +2 inside")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Outside" })).getByText("Nothing outside.")).toBeInTheDocument();
});

test("opens a node, shows what is outside it, and goes back with the breadcrumb", async () => {
  const user = userEvent.setup();
  const fetch = api({ [TOP]: { json: top }, [TOP_SVG]: { text: "<svg/>" }, [API]: { json: inside }, [API_SVG]: { text: "<svg/>" } });
  view();
  await user.click(await screen.findByRole("button", { name: "Open admin-api" }));
  await waitFor(() => expect(where()).toBe("?layer=software&root=api"));
  expect(urls(fetch)).toContain(API);
  const breadcrumb = within(screen.getByRole("navigation", { name: "Breadcrumb" }));
  expect(await breadcrumb.findByRole("button", { name: "admin-api" })).toBeInTheDocument();
  const outside = within(screen.getByRole("region", { name: "Outside" }));
  expect(outside.getByText("prod-1")).toBeInTheDocument();
  expect(outside.getByText("Infrastructure · 1 edge")).toBeInTheDocument();
  await user.click(breadcrumb.getByRole("button", { name: "Software" }));
  await waitFor(() => expect(where()).toBe("?layer=software"));
});

test("an outside node opens in its own layer", async () => {
  const user = userEvent.setup();
  api({ [API]: { json: inside }, [API_SVG]: { text: "<svg/>" },
    "/api/graph/view/infrastructure?root=prod&depth=1&format=json": { json: { ...empty, layer: "infrastructure" } },
    "/api/graph/view/infrastructure?root=prod&depth=1&format=svg&layout=tala": { text: "<svg/>" } });
  view("/system-map?layer=software&root=api");
  await user.click(await screen.findByRole("button", { name: "Open prod-1" }));
  await waitFor(() => expect(where()).toBe("?layer=infrastructure&root=prod"));
});

test("keeps layer, root, depth and view in the URL", async () => {
  const user = userEvent.setup();
  const fetch = api({
    "/api/graph/view/infrastructure?root=prod&depth=2&format=json": { json: { ...inside, layer: "infrastructure", boundary: [], edges: [] } },
    "/api/graph/view/infrastructure?root=prod&depth=2&format=d2": { text: 'n1: "admin-api @ prod-1" {shape: rectangle}\n' },
    "/api/graph/view/business?depth=2&format=json": { json: { ...empty, layer: "business" } },
    "/api/graph/view/business?depth=2&format=d2": { text: "" } });
  view("/system-map?layer=infrastructure&root=prod&depth=2&view=d2");
  expect(await screen.findByText('n1: "admin-api @ prod-1" {shape: rectangle}')).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "D2" })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByLabelText("Layout")).toBeNull();  // layouts only apply to the diagram
  await user.click(screen.getByRole("tab", { name: "Business" }));
  await waitFor(() => expect(where()).toBe("?layer=business&depth=2&view=d2"));
  expect(urls(fetch)).toContain("/api/graph/view/business?depth=2&format=json");
});

test("shows the diagram error but keeps the lists and the other views", async () => {
  const user = userEvent.setup();
  api({ [TOP]: { json: top }, [TOP_SVG]: { status: 503, error: "d2 is not installed." },
    "/api/graph/view/software?depth=1&format=mermaid": { text: "flowchart LR\n" } });
  view();
  expect(await screen.findByText("d2 is not installed.")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Inside" })).getByRole("button", { name: "admin-api" })).toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Mermaid" }));
  expect((await screen.findByTestId("mermaid")).textContent).toBe("flowchart LR\n");
});

test("shows an error when the root is gone", async () => {
  const user = userEvent.setup();
  api({ "/api/graph/view/software?root=gone&depth=1&format=json": { status: 404, error: "Node not found." },
    "/api/graph/view/software?root=gone&depth=1&format=svg&layout=tala": { status: 404, error: "Node not found." },
    [TOP]: { json: top }, [TOP_SVG]: { text: "<svg/>" } });
  view("/system-map?layer=software&root=gone");
  expect((await screen.findAllByText("Node not found.")).length).toBeGreaterThan(0);  // the diagram may say it too, briefly
  await user.click(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("button", { name: "Software" }));
  expect(await screen.findByRole("img", { name: "Software map" })).toBeInTheDocument();
});

test("the JSON view shows the view with Copy, and an empty layer offers New node", async () => {
  api({ "/api/graph/view/data?depth=1&format=json": { json: empty } });
  view("/system-map?layer=data&view=json");
  expect(await screen.findByText("Nothing here yet. Add a node to start.")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "New node" }).length).toBeGreaterThan(0);
});

test("New node opens the sheet in the current layer and root", async () => {
  const user = userEvent.setup();
  api({ [API]: { json: inside }, [API_SVG]: { text: "<svg/>" } });
  view("/system-map?layer=software&root=api");
  await screen.findByRole("button", { name: "Open Event processor" });
  await user.click(screen.getAllByRole("button", { name: "New node" })[0]);
  expect(await screen.findByText("In the Software layer, inside admin-api.")).toBeInTheDocument();
});
```

Add to `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`, after the line asserting the Servers link:

```tsx
  expect(environments.getByRole("link", { name: "System map" })).toHaveAttribute("href", "/system-map");
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/systemMap.test.tsx app/components/__tests__/adminLayout.test.tsx`
Expected: FAIL (`../SystemMapView` missing; no System map link).

- [ ] **Step 3: Write `services/admin-ui/app/components/SystemMapView.tsx`**

```tsx
import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { ArrowRight, Copy, Plus } from "lucide-react";
import { ApiError } from "../lib/api";
import { LAYERS, LAYER_LABELS, LAYOUTS, MAX_DEPTH, VIEWS, VIEW_LABELS, getView, getViewText, mapQuery, readMapState,
  type GraphView, type Layer, type Layout, type MapState, type MapView } from "../lib/systemGraph";
import Mermaid from "./Mermaid";
import NodeSheet, { type SheetTarget } from "./NodeSheet";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Spinner } from "~/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "~/components/ui/tabs";

const say = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;
type Source = { text: string } | { error: string } | null;

function SourceText({ text }: { text: string }) {
  return <div className="flex flex-col gap-2">
    <Button variant="outline" size="sm" className="self-end" onClick={() => void navigator.clipboard?.writeText(text)}><Copy />Copy</Button>
    <pre className="overflow-auto text-xs"><code>{text}</code></pre>
  </div>;
}

export default function SystemMapView() {
  const [params, setParams] = useSearchParams();
  const state = readMapState(params);
  const { layer, root, depth, view: mode, layout } = state;
  const [graph, setGraph] = useState<GraphView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<Source>(null);
  const [sheet, setSheet] = useState<SheetTarget | null>(null);
  const [version, setVersion] = useState(0);
  const go = (change: Partial<MapState>) => setParams(mapQuery({ ...state, ...change }));

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getView(layer, root, depth, controller.signal).then(setGraph, reason => {
      if (controller.signal.aborted) return;
      setGraph(null); setError(say(reason, "Could not load the map."));
    });
    return () => controller.abort();
  }, [layer, root, depth, version]);

  useEffect(() => {
    if (mode === "json") return;
    const controller = new AbortController();
    setSource(null);
    getViewText(layer, root, depth, mode === "diagram" ? "svg" : mode, layout, controller.signal).then(
      text => setSource({ text }),
      reason => { if (!controller.signal.aborted) setSource({ error: say(reason, "Could not draw the map.") }); });
    return () => controller.abort();
  }, [layer, root, depth, mode, layout, version]);

  const inside = graph?.nodes.filter(node => node.parent_id === null) ?? [];
  const edgeCount = (id: string) => (graph?.edges ?? []).filter(edge => edge.from === id || edge.to === id).reduce((sum, edge) => sum + edge.count, 0);
  const isEmpty = graph !== null && graph.nodes.length === 0 && graph.boundary.length === 0;
  const newNode = () => setSheet({ create: { layer, parentId: root, parentName: graph?.root?.name ?? null } });

  let diagram: ReactNode = null;
  if (!error) {
    if (isEmpty) diagram = <div className="flex flex-col items-start gap-3"><p className="text-muted-foreground text-sm">Nothing here yet. Add a node to start.</p>
      <Button variant="outline" onClick={newNode}><Plus />New node</Button></div>;
    else if (mode === "json") diagram = graph ? <SourceText text={JSON.stringify(graph, null, 2)} /> : <Spinner />;
    else if (source === null) diagram = <Spinner />;
    else if ("error" in source) diagram = <Alert variant="destructive"><AlertDescription>{source.error}</AlertDescription></Alert>;
    else if (mode === "diagram") diagram = <img alt={`${LAYER_LABELS[layer]} map`} className="max-w-full"
      src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(source.text)}`} />;
    else if (mode === "mermaid") diagram = <Mermaid chart={source.text} />;
    else diagram = <SourceText text={source.text} />;
  }

  return <div className="flex flex-col gap-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Tabs value={layer} onValueChange={value => go({ layer: value as Layer, root: null })}>
        <TabsList aria-label="Layer">{LAYERS.map(item => <TabsTrigger key={item} value={item}>{LAYER_LABELS[item]}</TabsTrigger>)}</TabsList>
      </Tabs>
      <Button onClick={newNode}><Plus />New node</Button>
    </div>
    <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 text-sm">
      <Button variant="link" className="h-auto p-0" onClick={() => go({ root: null })}>{LAYER_LABELS[layer]}</Button>
      {graph?.breadcrumb.map(crumb => <span key={crumb.id} className="flex items-center gap-1"><span aria-hidden="true">›</span>
        <Button variant="link" className="h-auto p-0" onClick={() => go({ root: crumb.id })}>{crumb.name}</Button></span>)}
    </nav>
    <div className="flex flex-wrap items-center gap-3">
      <Select value={String(depth)} onValueChange={value => go({ depth: Number(value) })}>
        <SelectTrigger aria-label="Depth" className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent><SelectGroup>{Array.from({ length: MAX_DEPTH }, (_, index) => index + 1).map(level =>
          <SelectItem key={level} value={String(level)}>Depth {level}</SelectItem>)}</SelectGroup></SelectContent>
      </Select>
      <Tabs value={mode} onValueChange={value => go({ view: value as MapView })}>
        <TabsList aria-label="View">{VIEWS.map(item => <TabsTrigger key={item} value={item}>{VIEW_LABELS[item]}</TabsTrigger>)}</TabsList>
      </Tabs>
      {mode === "diagram" && <Select value={layout} onValueChange={value => go({ layout: value as Layout })}>
        <SelectTrigger aria-label="Layout" className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent><SelectGroup>{LAYOUTS.map(item => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectGroup></SelectContent>
      </Select>}
    </div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
      <section aria-label="Diagram" className="min-h-64 overflow-auto rounded-md border p-3">{diagram}</section>
      <aside className="flex flex-col gap-4">
        <section aria-label="Inside" className="flex flex-col gap-2">
          <h2 className="text-sm font-medium">Inside</h2>
          {inside.length === 0 ? <p className="text-muted-foreground text-sm">No nodes.</p> : <ul className="flex flex-col gap-1">
            {inside.map(node => <li key={node.id} className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 flex-col">
                <button type="button" className="truncate text-left text-sm underline-offset-4 hover:underline" onClick={() => setSheet({ nodeId: node.id })}>{node.name}</button>
                <span className="text-muted-foreground text-xs">{node.child_count > 0 ? `${node.kind} · +${node.child_count} inside` : node.kind}</span>
              </div>
              <Button size="icon" variant="ghost" aria-label={`Open ${node.name}`} onClick={() => go({ root: node.id })}><ArrowRight /></Button>
            </li>)}
          </ul>}
        </section>
        <section aria-label="Outside" className="flex flex-col gap-2">
          <h2 className="text-sm font-medium">Outside</h2>
          {(graph?.boundary.length ?? 0) === 0 ? <p className="text-muted-foreground text-sm">Nothing outside.</p> : <ul className="flex flex-col gap-1">
            {graph!.boundary.map(node => { const count = edgeCount(node.id); return <li key={node.id} className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 flex-col"><span className="truncate text-sm">{node.name}</span>
                <span className="text-muted-foreground text-xs">{`${LAYER_LABELS[node.layer]} · ${count} ${count === 1 ? "edge" : "edges"}`}</span></div>
              <Button size="icon" variant="ghost" aria-label={`Open ${node.name}`} onClick={() => go({ layer: node.layer, root: node.id })}><ArrowRight /></Button>
            </li>; })}
          </ul>}
        </section>
      </aside>
    </div>
    {sheet && <NodeSheet target={sheet} onClose={() => setSheet(null)} onChanged={() => setVersion(value => value + 1)} />}
  </div>;
}
```

`services/admin-ui/app/routes/systemMap.tsx`:

```tsx
import AdminLayout from "../components/AdminLayout";
import SystemMapView from "../components/SystemMapView";

export default function SystemMapRoute() {
  return <AdminLayout><SystemMapView /></AdminLayout>;
}
```

In `services/admin-ui/app/routes.ts`, add after `route("environments", "./routes/environments.tsx"),`:
`route("system-map", "./routes/systemMap.tsx"),`.

In `services/admin-ui/app/components/AdminLayout.tsx`:
- add `Workflow` to the `lucide-react` import;
- next to `const servers = pathname === \`/servers\`;` add `const systemMap = pathname === "/system-map";`;
- in the `title` expression, before `services ? "Services"`, add `systemMap ? "System map" :`;
- in the Environments `SidebarMenu`, after the Servers item, add
  `<SidebarMenuItem><SidebarMenuButton asChild isActive={systemMap}><Link to="/system-map"><Workflow /><span>System map</span></Link></SidebarMenuButton></SidebarMenuItem>`.

- [ ] **Step 4: Write the user guide section.** In `docs/user-guide.md`, after the `### 4.19 Notifications` section
(before the `---` that precedes `## 5. End-to-end workflows`), add:

```markdown
### 4.20 System map

Environments → **System map** describes the whole system as a graph you open one level at a time (graph in graph):
every node is itself a graph of the nodes inside it.

- **Layers.** Business (capabilities, processes), Software (services, modules, components), Data (databases, tables,
  queues) and Infrastructure (servers, containers). A node lives in one layer and has at most one parent there; edges
  may join nodes of any layers (for example a container is an *instance of* a service).
- **Browsing.** The page shows the inside of one node: **Inside** lists its children (→ opens one; "+N inside" says it
  has more), **Outside** lists what they connect to elsewhere (→ opens it in its own layer). The breadcrumb goes back
  up. **Depth** shows more levels at once (up to 5); deeper nodes are folded into the box that contains them, and edges
  are added up (`calls ×3`). Edges inside one box are not drawn. Everything you choose is in the page address, so a view
  can be shared as a link.
- **Views.** **Diagram** is drawn by D2 with the layout you pick (tala, elk or dagre); **Mermaid** draws the same view
  with Mermaid; **D2** and **JSON** show the source, with **Copy**. If the server cannot draw a diagram (for example
  "d2 is not installed."), the other views still work.
- **Editing.** **New node** adds a node inside the node you are looking at (kind, name, description, optional
  properties as a JSON object). Click a node's name to open it: edit and **Save**, **Move** it under another node of the
  same layer (or to the top level), add or remove **Edges** (type + target found by name, in any layer), attach or
  detach **Documents** from Docs → Documents, or **Delete** it (a node that still has nodes inside cannot be deleted).
  Names are unique among siblings; a node cannot be moved inside itself; a layer has at most 12 levels.
```

- [ ] **Step 5: Run tests and type checks**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/systemMap.test.tsx app/components/__tests__/adminLayout.test.tsx app/components/__tests__/nodeSheet.test.tsx && npx react-router typegen && npx tsc --noEmit`
Expected: PASS, no type errors. Then the full UI suite: `npx vitest run` — everything passes except the known
intermittent `skills.test.tsx` / `agentTools.test.tsx` (re-run those two alone if they fail).

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui/app/components/SystemMapView.tsx services/admin-ui/app/routes/systemMap.tsx \
  services/admin-ui/app/routes.ts services/admin-ui/app/components/AdminLayout.tsx \
  services/admin-ui/app/components/__tests__/systemMap.test.tsx services/admin-ui/app/components/__tests__/adminLayout.test.tsx \
  docs/user-guide.md
git commit -m "feat(admin-ui): System map page — layers, graph in graph, diagram views

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Final check (after Task 8)

- Backend: `cd services/admin-api && uv run pytest -q` — all pass except the two known failures
  (`tests/test_deploy.py::test_docs_commands_serve_the_docs_folder_inside_the_data_root`,
  `tests/test_environments.py::test_migration_reapply_and_populated_rollback_protection`, both failing on main too).
- `sh db/tests/run.sh` from the worktree root exits 0.
- UI: `cd services/admin-ui && npx vitest run && npx tsc --noEmit`.
