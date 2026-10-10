# Inventory Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stored system graph with a System map computed from the inventory, plus one table of connections
(`ka.inventory_links`) and one of document attachments (`ka.inventory_documents`) that the map edits.

**Architecture:** Migration 0095 adds the two tables, moves `service_dependencies` / `service_repositories` rows and graph
document attachments into them, and drops the graph tables. admin-api loses `graph_sync` / `graph_memory` /
`system_graph_api`; a new `inventory_map.py` loads the inventory per request into a networkx tree (environment › server ›
service, or a flat services view) and cuts views with the same roll-up rules as before; `system_graph_render.py` still
draws them. `inventory_links_api.py` serves links and attachments. The System map page switches between the two views
and edits links and documents in a side sheet.

**Tech Stack:** PostgreSQL 17 + golang-migrate SQL files, FastAPI + psycopg3 + networkx (admin-api), React Router 7 +
shadcn/ui + vitest (admin-ui), d2 v0.9.0 for SVG.

**Spec:** `docs/superpowers/specs/2026-10-10-inventory-map-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/inventory-map` on branch `feature/inventory-map`.
- Never run `make` targets. Never touch `.env` or anything under `.local`. Never `git stash`. Never touch the running
  Docker stack's database (tests use their own disposable PostgreSQL through the `store` fixture).
- Never `git add .superpowers`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd services/admin-api && uv run pytest -q <files>`. UI: `cd services/admin-ui && npx vitest run <files>`
  and `npx tsc --noEmit` (if `node_modules` is missing in the worktree, run `npm ci` there first).
- Entry kinds are exactly `service`, `server`, `environment`, `code_project` (code projects live in `ka.repositories`).
- Link kinds are exactly `depends_on` (service → service), `built_from` (service → code project), `connects_to`
  (any entry → any entry).
- `description` ≤ 4000 characters; `attributes` is a JSON object of at most 20 string → string pairs, keys 1–64
  characters, values ≤ 500 characters.
- Archived code projects (`archived_at IS NOT NULL`) are not entries: they are not on the map, not searchable, and new
  links or attachments to them are refused with 404.
- Map views: `infrastructure` and `services`; depth 1–3; outputs `json`, `mermaid`, `d2`, `svg`; layouts `tala`, `elk`,
  `dagre`. The view JSON keeps `nodes`, `edges`, `breadcrumb`, `boundary`, `root`, `depth` and every node carries
  `entry_kind` and `entry_id`.
- Plain-English messages exactly as written in this plan; errors are `{"error": {"message": ...}}` (the app's existing
  error handler does this for `HTTPException`).

## Review Focus

1. A link to an **archived code project**: the links API refuses new ones (404 "That code project was not found."), the
   map leaves archived code projects and their links out. Tested in Task 3 and Task 5.
2. A **Services form open** while someone edits a `depends_on` or `built_from` link on the map: the form's save must get
   409 (the link write bumps the services' revision). Tested in Task 3.
3. **Deleting an entry** (service, server, environment) removes its links and attachments and the map no longer shows
   them. Tested in Task 3 and Task 4.
4. A link between two services **deployed in two environments** is drawn only between their placements in the same
   environment — never production → staging. Tested in Task 5.
5. A **root that no longer exists** (a server removed from the environment you were looking at) answers 404 "Node not
   found." so the page shows the error and its breadcrumb still leads back. Tested in Task 5.

## Rulings made while planning (spec refinements)

- Spec §3 says a link "is drawn between every pair of their placements". Drawing every pair would put production →
  staging lines on the map. Ruling: when both ends have placements in a common environment (or both at the top level),
  only same-environment pairs are drawn; otherwise every pair. Costs if wrong: a one-line change in `link_edges`.
- `inventory_links` and `inventory_documents` get stored generated columns (`from_kind`, `from_id`, `to_kind`,
  `to_id`; `entry_kind`, `entry_id`) so readers and the unique index do not repeat four-way `COALESCE`s.
- A helper view `ka.inventory_entries(kind, id, project_id, name)` (all four entry tables) gives link ends their names.
- `service_assets` gains a last column `links`: the service's `connects_to` links in both directions. That is how agent
  runs (service context, agent config) and document generation receive them.
- Tests that read `service_dependencies` / `service_repositories` with SQL are rewritten to read `inventory_links`;
  tests of the 0042 / 0092 / 0093 / 0094 migration files are deleted (those files cannot run on the 0095 schema;
  `db/tests/run.sh` still walks the whole down chain).

---

## File structure

| File | Responsibility |
|---|---|
| `db/migrations/0095_inventory_links.up.sql` / `.down.sql` | New tables and view, data moves, graph and old tables dropped / restored |
| `db/tests/run.sh` | 0095 down check, final schema check |
| `services/admin-api/src/knowledge_admin/inventory_map.py` (new) | Load the inventory, place entries in a view, cut a view, search |
| `services/admin-api/src/knowledge_admin/map_api.py` (new) | `/api/map/view/{view}`, `/api/map/search` |
| `services/admin-api/src/knowledge_admin/inventory_links_api.py` (new) | `/api/inventory/links`, `/api/inventory/documents` |
| `services/admin-api/src/knowledge_admin/system_graph_render.py` | Unchanged (Mermaid, D2, SVG) |
| `service_api.py`, `environments.py`, `repositories.py`, `repository_api.py`, `app.py`, `api.py` | Graph sync calls removed; Services writes links |
| `generate_documents.py`, `agent_wizard.py`, `knowledge_agents/run_resources.py`, `knowledge_agents/config.py` | Read `inventory_links` / `service_assets.links` |
| Deleted: `graph_sync.py`, `graph_memory.py`, `system_graph_api.py`, `system_graph.py` and their tests | |
| `services/admin-ui/app/lib/inventoryMap.ts` (new, replaces `systemGraph.ts`) | Types, URL state, API calls, attribute text parsing |
| `services/admin-ui/app/components/SystemMapView.tsx` | View switch, diagram, Inside / Outside lists |
| `services/admin-ui/app/components/EntrySheet.tsx` (new, replaces `NodeSheet.tsx`) | Links (connect, edit, remove) and documents of one entry |
| `docs/user-guide.md` §4.20, `docs/migrations.md`, the two old specs | Docs |

---

### Task 1: Remove the stored-graph code

The graph tables stay for now (Task 2 drops them); this task only stops the app from using them, so the suite stays
green at every commit.

**Files:**
- Delete: `services/admin-api/src/knowledge_admin/graph_sync.py`, `graph_memory.py`, `system_graph_api.py`
- Delete tests: `services/admin-api/tests/test_graph_sync.py`, `test_graph_memory.py`, `test_graph_inventory_sync_api.py`,
  `test_system_graph_api.py`
- Modify: `services/admin-api/src/knowledge_admin/service_api.py`, `environments.py`, `repositories.py`,
  `repository_api.py`, `app.py`, `api.py`
- Test: `services/admin-api/tests/test_services.py`, `test_environments.py`, `test_code_projects.py` (unchanged, must pass)

**Interfaces:**
- Produces: no `knowledge_admin.graph_sync`, `graph_memory` or `system_graph_api` modules; `/api/graph/*` routes are
  gone (Task 5 adds `/api/map/*`). `system_graph.py` and `system_graph_render.py` stay for Task 5.

- [ ] **Step 1: Write the failing test**

Add to `services/admin-api/tests/test_api.py`:

```python
def test_the_stored_graph_api_is_gone(tmp_path):
    client = _client(tmp_path)
    from test_repositories import HEADERS
    assert client.get('/api/graph/view/infrastructure', headers=HEADERS).status_code == 404
    import importlib.util
    for name in ('graph_sync', 'graph_memory', 'system_graph_api'):
        assert importlib.util.find_spec(f'knowledge_admin.{name}') is None
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_api.py::test_the_stored_graph_api_is_gone`
Expected: FAIL (the route answers 503/200 and the modules exist).

- [ ] **Step 3: Remove the code**

1. `git rm` the three modules and the four test files listed above.
2. `api.py`: remove `system_graph_api` from the import list and delete the line
   `router.include_router(system_graph_api.build_api(app_name, repository_store))`.
3. `app.py`: remove `from knowledge_admin import graph_sync`; in `lifespan` delete the `graph = (...)` task and the
   `if graph is not None: graph.cancel(); await asyncio.gather(graph, return_exceptions=True)` block (keep the
   comment-free rest unchanged; drop the two comment lines about `to_thread` that describe the sync).
4. `service_api.py`: remove `from knowledge_admin import graph_sync`; delete the three
   `graph_sync.sync_inventory(conn, project_id)` lines; in both `except psycopg.errors.UniqueViolation as exc:` blocks
   delete the two `if graph_sync.graph_clash(exc): raise HTTPException(409, graph_sync.GRAPH_CLASH) from None` lines and
   change `as exc:` to a bare `except psycopg.errors.UniqueViolation:`.
5. `environments.py`: same — remove the import, the two `sync_inventory` calls and the `graph_clash` lines.
6. `repositories.py`: remove the import and the `graph_sync.sync_inventory(conn, project_id)` line in `save`.
7. `repository_api.py`: remove the import, the `graph_sync.sync_inventory(conn, project)` line after archiving, and the
   `graph_clash` lines in `add_repository` (keep the 409 "This Git source or clone folder has already been added").
8. `grep -rn "graph_sync\|graph_memory\|system_graph_api\|GRAPH_CLASH" services/admin-api/src services/admin-api/tests`
   must print nothing.

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_api.py tests/test_services.py tests/test_environments.py tests/test_code_projects.py tests/test_service_dependency_api.py tests/test_system_graph.py tests/test_system_graph_render.py`
Expected: PASS (`test_environments.py::test_migration_reapply_and_populated_rollback_protection` is a known pre-existing
failure; it must fail the same way as on `main`, nothing new).

- [ ] **Step 5: Commit**

```bash
git add -A services/admin-api
git commit -m "refactor(api): stop syncing the stored system graph; its API and sync are removed

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Migration 0095 and the readers of the old tables

**Files:**
- Create: `db/migrations/0095_inventory_links.up.sql`, `db/migrations/0095_inventory_links.down.sql`
- Modify: `db/tests/run.sh`
- Modify: `services/admin-api/src/knowledge_admin/service_api.py`, `generate_documents.py`, `agent_wizard.py`,
  `knowledge_agents/run_resources.py`, `knowledge_agents/config.py`
- Create test: `services/admin-api/tests/test_inventory_links_migration.py`
- Modify tests: `test_service_dependencies.py`, `test_service_dependency_api.py`, `test_services.py`, `test_generations.py`
- Delete tests: `test_system_graph_migration.py`, `test_graph_inventory_sync_migration.py`,
  `test_graph_environment_tree_migration.py`
- Modify: `docs/migrations.md`

**Interfaces:**
- Produces tables `ka.inventory_links(id, project_id, from_service_id, from_server_id, from_environment_id,
  from_code_project_id, to_service_id, to_server_id, to_environment_id, to_code_project_id, kind, description,
  attributes, revision, created_by, created_at, updated_at, from_kind, from_id, to_kind, to_id)` with unique index
  `inventory_links_once (from_id, to_id, kind)`; `ka.inventory_documents(id, project_id, service_id, server_id,
  environment_id, code_project_id, document_id, created_at, entry_kind, entry_id)` with unique index
  `inventory_documents_once (entry_id, document_id)`; view `ka.inventory_entries(kind, id, project_id, name)`;
  `ka.service_assets` with the same columns as before plus `links` (last).

- [ ] **Step 1: Write the failing migration tests**

Create `services/admin-api/tests/test_inventory_links_migration.py`:

```python
"""0095: connections between inventory entries replace the stored system graph."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0095_inventory_links.up.sql', '0095_inventory_links.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def inventory(conn, project):
    ids = {}
    for name in ('api', 'worker'):
        ids[name] = conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,%s) RETURNING id", (project, name)).fetchone()['id']
    ids['prod-1'] = conn.execute("INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'prod-1',%s,false) RETURNING id",
                                 (project, f'{uuid4().hex[:8]}.internal')).fetchone()['id']
    ids['production'] = conn.execute("INSERT INTO ka.environments(project_id,name) VALUES (%s,'production') RETURNING id", (project,)).fetchone()['id']
    ids['repo'] = conn.execute("""INSERT INTO ka.repositories(project_id,name,remote_url,requested_ref,checkout_path)
        VALUES (%s,'api-code','https://example.test/api.git','main',%s) RETURNING id""", (project, f'r-{uuid4().hex[:8]}')).fetchone()['id']
    ids['doc'] = conn.execute("""INSERT INTO ka.documents(project_id,slug) VALUES (%s,%s) RETURNING id""",
                              (project, f'runbook-{uuid4().hex[:6]}')).fetchone()['id']
    return ids


def link(conn, project, kind, start, end, **extra):
    columns = {'project_id': project, 'kind': kind, f'from_{start[0]}_id': start[1], f'to_{end[0]}_id': end[1], **extra}
    return conn.execute(f"INSERT INTO ka.inventory_links({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))}) RETURNING *",
                        list(columns.values())).fetchone()


def test_links_have_one_end_each_side_no_self_links_and_no_duplicates(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = inventory(conn, project)
        row = link(conn, project, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']), attributes=Jsonb({'port': '5432'}))
        assert (row['from_kind'], row['from_id'], row['to_kind'], row['to_id']) == ('service', ids['api'], 'server', ids['prod-1'])
    for start, end, error in [(('service', ids['api']), ('server', ids['prod-1']), psycopg.errors.UniqueViolation),
                              (('service', ids['api']), ('service', ids['api']), psycopg.errors.CheckViolation),
                              (('service', uuid4()), ('server', ids['prod-1']), psycopg.errors.ForeignKeyViolation)]:
        with pytest.raises(error), store.connect() as conn:
            link(conn, project, 'connects_to', start, end)
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        conn.execute("""INSERT INTO ka.inventory_links(project_id,kind,from_service_id,from_server_id,to_environment_id)
            VALUES (%s,'connects_to',%s,%s,%s)""", (project, ids['api'], ids['prod-1'], ids['production']))
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        link(conn, project, 'calls', ('service', ids['api']), ('service', ids['worker']))


def test_deleting_an_entry_removes_its_links_and_attachments(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = inventory(conn, project)
        link(conn, project, 'connects_to', ('server', ids['prod-1']), ('environment', ids['production']))
        conn.execute('INSERT INTO ka.inventory_documents(project_id,server_id,document_id) VALUES (%s,%s,%s)', (project, ids['prod-1'], ids['doc']))
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (ids['prod-1'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.inventory_links').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.inventory_documents').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.documents WHERE id=%s', (ids['doc'],)).fetchone()['n'] == 1


def test_up_moves_dependencies_code_projects_and_attachments(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = inventory(conn, project)
        run(conn, DOWN)
        conn.execute("INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id,description) VALUES (%s,%s,%s,'stores orders')",
                     (project, ids['api'], ids['worker']))
        conn.execute('INSERT INTO ka.service_repositories(project_id,service_id,repository_id) VALUES (%s,%s,%s)', (project, ids['api'], ids['repo']))
        deployment = conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id)
            VALUES (%s,%s,%s,'production',%s) RETURNING id""", (project, ids['worker'], ids['prod-1'], ids['production'])).fetchone()['id']

        def node(name, layer, **links):
            columns = ['project_id', 'layer', 'kind', 'name', *links]
            return conn.execute(f"INSERT INTO ka.kg_nodes({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))}) RETURNING id",
                                (project, layer, 'x', name, *links.values())).fetchone()['id']
        env_node = node('production', 'infrastructure', environment_id=ids['production'])
        copy = conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id,server_id,environment_id)
            VALUES (%s,'infrastructure','server','prod-1',%s,%s,%s) RETURNING id""", (project, env_node, ids['prod-1'], ids['production'])).fetchone()['id']
        attached = {env_node: 'environment', copy: 'server',
                    node('worker · production', 'infrastructure', deployment_id=deployment): 'service',
                    node('api-code', 'software', repository_id=ids['repo']): 'code_project',
                    node('Checkout', 'business'): None}
        for node_id in attached:
            conn.execute('INSERT INTO ka.kg_node_documents(node_id,document_id,project_id) VALUES (%s,%s,%s)', (node_id, ids['doc'], project))
        run(conn, UP)
        links = {(r['kind'], r['from_id'], r['to_id'], r['description']) for r in conn.execute('SELECT * FROM ka.inventory_links')}
        assert links == {('depends_on', ids['api'], ids['worker'], 'stores orders'), ('built_from', ids['api'], ids['repo'], '')}
        moved = {(r['entry_kind'], r['entry_id']) for r in conn.execute('SELECT entry_kind,entry_id FROM ka.inventory_documents')}
        assert moved == {('environment', ids['production']), ('server', ids['prod-1']), ('service', ids['worker']), ('code_project', ids['repo'])}
        assert conn.execute("SELECT to_regclass('ka.kg_nodes') IS NULL AND to_regclass('ka.service_dependencies') IS NULL AS gone").fetchone()['gone']
        api = conn.execute('SELECT dependencies,repositories,links FROM ka.service_assets WHERE id=%s', (ids['api'],)).fetchone()
        assert [d['service_id'] for d in api['dependencies']] == [str(ids['worker'])]
        assert [r['id'] for r in api['repositories']] == [str(ids['repo'])]
        assert api['links'] == []


def test_down_refuses_what_the_old_schema_cannot_hold(store):
    for setup in ('connects_to', 'attributes', 'attachment'):
        with store.connect() as conn:
            project = store.project(conn, 'shop')
            ids = inventory(conn, project)
            if setup == 'connects_to':
                link(conn, project, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']))
            elif setup == 'attributes':
                link(conn, project, 'depends_on', ('service', ids['api']), ('service', ids['worker']),
                     attributes=Jsonb({'protocol': 'grpc'}))
            else:
                conn.execute('INSERT INTO ka.inventory_documents(project_id,service_id,document_id) VALUES (%s,%s,%s)', (project, ids['api'], ids['doc']))
            with pytest.raises(psycopg.errors.RaiseException, match='Cannot roll back 0095'):
                run(conn, DOWN)
            conn.rollback()


def test_down_and_up_keep_dependencies_and_code_projects(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        ids = inventory(conn, project)
        link(conn, project, 'depends_on', ('service', ids['api']), ('service', ids['worker']), description='calls it')
        link(conn, project, 'built_from', ('service', ids['api']), ('code_project', ids['repo']))
        run(conn, DOWN)
        assert conn.execute('SELECT service_id,depends_on_service_id,description FROM ka.service_dependencies').fetchall() == [
            {'service_id': ids['api'], 'depends_on_service_id': ids['worker'], 'description': 'calls it'}]
        assert conn.execute('SELECT service_id,repository_id FROM ka.service_repositories').fetchall() == [
            {'service_id': ids['api'], 'repository_id': ids['repo']}]
        assert conn.execute("SELECT to_regclass('ka.kg_nodes') IS NOT NULL AND to_regclass('ka.inventory_links') IS NULL AS back").fetchone()['back']
        run(conn, UP)
        assert conn.execute('SELECT count(*) AS n FROM ka.inventory_links').fetchone()['n'] == 2
```

If `ka.documents` or `ka.repositories` need more NOT NULL columns than these inserts give, look at
`tests/test_system_graph_api.py` history (`git show main:services/admin-api/tests/test_system_graph_api.py`) or the
table definition and add the minimum columns; do not change what the test asserts.

- [ ] **Step 2: Run them to see them fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_inventory_links_migration.py`
Expected: FAIL (`relation "ka.inventory_links" does not exist` / missing migration file).

- [ ] **Step 3: Write `db/migrations/0095_inventory_links.up.sql`**

```sql
BEGIN;
SET search_path = ka, public;

-- Connections between inventory entries: services, servers, environments and code projects (ka.repositories). Each
-- side has one nullable column per entry type and exactly one is set, so both ends are real foreign keys in the link's
-- project and deleting an entry deletes its links. from_kind/from_id and to_kind/to_id are derived for readers.
CREATE TABLE inventory_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    from_service_id uuid,
    from_server_id uuid,
    from_environment_id uuid,
    from_code_project_id uuid,
    to_service_id uuid,
    to_server_id uuid,
    to_environment_id uuid,
    to_code_project_id uuid,
    kind text NOT NULL CONSTRAINT inventory_links_kind CHECK (kind IN ('depends_on', 'built_from', 'connects_to')),
    description text NOT NULL DEFAULT '' CONSTRAINT inventory_links_description CHECK (length(description) <= 4000),
    attributes jsonb NOT NULL DEFAULT '{}' CONSTRAINT inventory_links_attributes CHECK (jsonb_typeof(attributes) = 'object'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    from_kind text GENERATED ALWAYS AS (CASE WHEN from_service_id IS NOT NULL THEN 'service'
        WHEN from_server_id IS NOT NULL THEN 'server' WHEN from_environment_id IS NOT NULL THEN 'environment'
        ELSE 'code_project' END) STORED,
    from_id uuid GENERATED ALWAYS AS (COALESCE(from_service_id, from_server_id, from_environment_id, from_code_project_id)) STORED,
    to_kind text GENERATED ALWAYS AS (CASE WHEN to_service_id IS NOT NULL THEN 'service'
        WHEN to_server_id IS NOT NULL THEN 'server' WHEN to_environment_id IS NOT NULL THEN 'environment'
        ELSE 'code_project' END) STORED,
    to_id uuid GENERATED ALWAYS AS (COALESCE(to_service_id, to_server_id, to_environment_id, to_code_project_id)) STORED,
    UNIQUE (id, project_id),
    CONSTRAINT inventory_links_one_from CHECK (num_nonnulls(from_service_id, from_server_id, from_environment_id, from_code_project_id) = 1),
    CONSTRAINT inventory_links_one_to CHECK (num_nonnulls(to_service_id, to_server_id, to_environment_id, to_code_project_id) = 1),
    CONSTRAINT inventory_links_not_self CHECK (COALESCE(from_service_id, from_server_id, from_environment_id, from_code_project_id)
        <> COALESCE(to_service_id, to_server_id, to_environment_id, to_code_project_id)),
    FOREIGN KEY (from_service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (from_server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (from_environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (from_code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX inventory_links_once ON inventory_links (from_id, to_id, kind);
CREATE INDEX inventory_links_to ON inventory_links (to_id);
CREATE INDEX inventory_links_project ON inventory_links (project_id);
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON inventory_links FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE inventory_links IS 'Directed connections between inventory entries: depends_on (service → service), built_from (service → code project), connects_to (any → any).';
COMMENT ON COLUMN inventory_links.description IS 'Why the connection exists. Operator context, not executable instructions.';
COMMENT ON COLUMN inventory_links.attributes IS 'String key/value facts such as protocol or port. Operator context, not instructions.';

-- Documents attached to an inventory entry. The documents live in ka.documents; detaching never deletes them.
CREATE TABLE inventory_documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    service_id uuid,
    server_id uuid,
    environment_id uuid,
    code_project_id uuid,
    document_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    entry_kind text GENERATED ALWAYS AS (CASE WHEN service_id IS NOT NULL THEN 'service'
        WHEN server_id IS NOT NULL THEN 'server' WHEN environment_id IS NOT NULL THEN 'environment'
        ELSE 'code_project' END) STORED,
    entry_id uuid GENERATED ALWAYS AS (COALESCE(service_id, server_id, environment_id, code_project_id)) STORED,
    CONSTRAINT inventory_documents_one_entry CHECK (num_nonnulls(service_id, server_id, environment_id, code_project_id) = 1),
    FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX inventory_documents_once ON inventory_documents (entry_id, document_id);
CREATE INDEX inventory_documents_document ON inventory_documents (document_id);
CREATE INDEX inventory_documents_project ON inventory_documents (project_id);

-- Every inventory entry with its kind and name, for naming link ends.
CREATE VIEW inventory_entries AS
SELECT 'service'::text AS kind, id, project_id, name::text AS name FROM services
UNION ALL SELECT 'server', id, project_id, name::text FROM servers
UNION ALL SELECT 'environment', id, project_id, name::text FROM environments
UNION ALL SELECT 'code_project', id, project_id, name::text FROM repositories;

INSERT INTO inventory_links (project_id, from_service_id, to_service_id, kind, description, created_at, updated_at)
SELECT project_id, service_id, depends_on_service_id, 'depends_on', description, created_at, updated_at
FROM service_dependencies;
INSERT INTO inventory_links (project_id, from_service_id, to_code_project_id, kind)
SELECT project_id, service_id, repository_id, 'built_from' FROM service_repositories;

-- Graph attachments move to the entry behind the node (a deployment's go to its service); hand-drawn nodes' go away.
INSERT INTO inventory_documents (project_id, service_id, server_id, environment_id, code_project_id, document_id, created_at)
SELECT l.project_id, COALESCE(n.service_id, d.service_id), n.server_id,
    CASE WHEN n.server_id IS NULL THEN n.environment_id END, n.repository_id, l.document_id, min(l.created_at)
FROM kg_node_documents l
JOIN kg_nodes n ON n.id = l.node_id
LEFT JOIN service_deployments d ON d.id = n.deployment_id
WHERE num_nonnulls(n.server_id, n.environment_id, n.service_id, n.repository_id, n.deployment_id) > 0
GROUP BY 1, 2, 3, 4, 5, 6;

CREATE OR REPLACE VIEW service_assets AS
SELECT s.id, s.project_id, s.name, s.description, s.owner, s.operations_notes,
    s.revision, s.created_at, s.updated_at,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', r.id, 'name', r.name, 'remote_url', r.remote_url,
        'requested_ref', r.requested_ref, 'current_commit', r.current_commit,
        'checkout_path', r.checkout_path, 'synced_at', r.synced_at,
        'archived', r.archived_at IS NOT NULL) ORDER BY lower(r.name), r.id)
        FROM inventory_links l JOIN repositories r ON r.id = l.to_code_project_id
        WHERE l.kind = 'built_from' AND l.from_service_id = s.id), '[]'::jsonb) AS repositories,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', d.id, 'server_id', d.server_id, 'environment_id', d.environment_id, 'environment', COALESCE(e.name,d.environment),
        'runtime', d.runtime, 'runtime_name', d.runtime_name, 'port', d.port,
        'log_location', d.log_location, 'deployed_version', d.deployed_version,
        'server', to_jsonb(h) - 'project_id')
        ORDER BY COALESCE(e.name,d.environment), h.name, d.runtime_name, d.id)
        FROM service_deployments d JOIN server_assets h ON h.id=d.server_id
        LEFT JOIN environments e ON e.id=d.environment_id
        WHERE d.service_id=s.id), '[]'::jsonb) AS deployments,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'service_id',other.id,'name',other.name,'description',l.description)
        ORDER BY lower(other.name),other.id)
        FROM inventory_links l JOIN services other ON other.id=l.to_service_id
        WHERE l.kind='depends_on' AND l.from_service_id=s.id),'[]'::jsonb) AS dependencies,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'service_id',other.id,'name',other.name,'description',l.description)
        ORDER BY lower(other.name),other.id)
        FROM inventory_links l JOIN services other ON other.id=l.from_service_id
        WHERE l.kind='depends_on' AND l.to_service_id=s.id),'[]'::jsonb) AS dependents,
    s.service_type, s.service_role,
    COALESCE((SELECT jsonb_agg(jsonb_build_object('id',e.id,'name',e.name) ORDER BY lower(e.name),e.id)
        FROM environment_services es JOIN environments e ON e.id=es.environment_id
        WHERE es.service_id=s.id),'[]'::jsonb) AS environments,
    COALESCE((SELECT jsonb_agg(jsonb_build_object(
        'id', l.id, 'direction', CASE WHEN l.from_id = s.id THEN 'outgoing' ELSE 'incoming' END,
        'kind', l.kind, 'entry_kind', o.kind, 'entry_id', o.id, 'name', o.name,
        'description', l.description, 'attributes', l.attributes)
        ORDER BY lower(o.name), o.id, l.id)
        FROM inventory_links l
        JOIN inventory_entries o ON o.id = CASE WHEN l.from_id = s.id THEN l.to_id ELSE l.from_id END
        WHERE l.kind = 'connects_to' AND (l.from_id = s.id OR l.to_id = s.id)), '[]'::jsonb) AS links
FROM services s;

-- The stored system graph (0092-0094) and the two link tables it replaces.
DROP TRIGGER kg_graph_changed ON documents;
DROP TABLE kg_node_documents;
DROP TABLE kg_edges;
DROP TABLE kg_nodes;
DROP TABLE kg_graph_version;
DROP FUNCTION kg_document_changed();
DROP FUNCTION kg_graph_changed();
DROP FUNCTION kg_edges_check();
DROP FUNCTION kg_nodes_place();
DROP TABLE service_dependencies;
DROP TABLE service_repositories;

COMMIT;
```

- [ ] **Step 4: Write `db/migrations/0095_inventory_links.down.sql`**

```sql
BEGIN;
SET search_path = ka, public;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM inventory_links WHERE kind = 'connects_to' OR attributes <> '{}'::jsonb)
        OR EXISTS (SELECT 1 FROM inventory_documents) THEN
        RAISE EXCEPTION 'Cannot roll back 0095: remove connects_to links, link attributes and attached documents first.';
    END IF;
END $$;

-- service_repositories as 0003 made it, service_dependencies as 0042 made it.
CREATE TABLE service_repositories (
    project_id uuid NOT NULL,
    service_id uuid NOT NULL,
    repository_id uuid NOT NULL,
    PRIMARY KEY (service_id, repository_id),
    FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id),
    FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id)
);
CREATE INDEX service_repositories_repository ON service_repositories(repository_id);
-- (paste here, verbatim, the CREATE TABLE service_dependencies … COMMENT ON COLUMN statements of
--  0042_service_dependencies.up.sql — everything between "SET search_path=ka,public;" and "COMMIT;")

INSERT INTO service_repositories (project_id, service_id, repository_id)
SELECT project_id, from_service_id, to_code_project_id FROM inventory_links WHERE kind = 'built_from';
INSERT INTO service_dependencies (project_id, service_id, depends_on_service_id, description, created_at, updated_at)
SELECT project_id, from_service_id, to_service_id, description, created_at, updated_at FROM inventory_links WHERE kind = 'depends_on';

DROP VIEW service_assets;
-- (paste here, verbatim, the "CREATE OR REPLACE VIEW service_assets AS … FROM services s;" statement of
--  0050_service_environments.up.sql)

DROP VIEW inventory_entries;
DROP TABLE inventory_documents;
DROP TABLE inventory_links;

-- The stored system graph, empty: paste here, verbatim and in this order, everything between
-- "SET search_path = ka, public;" and "COMMIT;" of 0092_system_graph.up.sql, 0093_graph_inventory_sync.up.sql and
-- 0094_graph_environment_tree.up.sql. After a rollback the old code's start-up sync refills it.

COMMIT;
```

The three "paste here" comments are instructions for you: replace each with the exact statements named (copy them
from the files; do not retype). Then delete the instruction comments.

- [ ] **Step 5: `db/tests/run.sh`**

1. In the down chain right after the two `run_migrate postgres up` lines, insert before the first
   `run_migrate postgres down 1`:

```sh
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
links_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.inventory_links') IS NULL AND to_regclass('ka.kg_nodes') IS NOT NULL
        AND to_regclass('ka.service_dependencies') IS NOT NULL")
[ "$links_removed" = t ] || { echo 'FAIL: 0095 down did not restore the graph and link tables' >&2; exit 1; }
```

   The chain must still end at version 83 (`rolled_back_version`): count the `down 1` steps after your change and make
   sure it is one more than before.
2. Replace the three final check blocks `graph=…`, `sync=…`, `tree=…` (with their `PASS:` lines) by:

```sh
links=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.inventory_links') IS NOT NULL AND to_regclass('ka.inventory_documents') IS NOT NULL
        AND to_regclass('ka.kg_nodes') IS NULL AND to_regclass('ka.service_dependencies') IS NULL")
[ "$links" = t ] || { echo 'FAIL: 0095 inventory links missing or the stored graph still there' >&2; exit 1; }
printf '%s\n' 'PASS: inventory links replace the stored system graph.'
```

- [ ] **Step 6: Switch the readers**

`service_api.py` (`save`):

```python
                original_links = {(row['from_id'], row['to_id']): row['description']
                    for row in conn.execute('''SELECT from_id,to_id,description FROM ka.inventory_links
                        WHERE kind='depends_on' AND (from_id=%s OR to_id=%s)''', (service_id,service_id))}
```

replaces the `service_dependencies` query; the `DELETE FROM ka.service_repositories … INSERT …` pair becomes

```python
                # Unlisted code projects lose their link; listed ones keep theirs (and its description and attributes).
                conn.execute('''DELETE FROM ka.inventory_links WHERE kind='built_from' AND from_service_id=%s
                    AND NOT (to_code_project_id=ANY(%s))''', (service_id, value.repository_ids))
                for repository_id in value.repository_ids:
                    conn.execute('''INSERT INTO ka.inventory_links(project_id,from_service_id,to_code_project_id,kind)
                        VALUES (%s,%s,%s,'built_from') ON CONFLICT (from_id,to_id,kind) DO NOTHING''',
                        (project_id, service_id, repository_id))
```

and the dependency writes become

```python
                for source,target in original_links.keys() - links.keys():
                    conn.execute('''DELETE FROM ka.inventory_links WHERE kind='depends_on'
                        AND from_service_id=%s AND to_service_id=%s''', (source,target))
                for source,target in changed_pairs & links.keys():
                    conn.execute('''INSERT INTO ka.inventory_links(project_id,from_service_id,to_service_id,kind,description)
                        VALUES (%s,%s,%s,'depends_on',%s) ON CONFLICT (from_id,to_id,kind)
                        DO UPDATE SET description=EXCLUDED.description, revision=inventory_links.revision+1''',
                        (project_id,source,target,links[source,target]))
```

In `delete_service`: the related-services query reads

```python
            related_ids = [row['id'] for row in conn.execute('''SELECT to_id AS id FROM ka.inventory_links
                WHERE kind='depends_on' AND from_id=%s UNION SELECT from_id FROM ka.inventory_links
                WHERE kind='depends_on' AND to_id=%s''', (service_id,service_id))]
```

and the `DELETE FROM ka.service_repositories WHERE service_id=%s` line is removed (links cascade).

`generate_documents.py`: the code-project query becomes

```python
    for row in conn.execute(f'''SELECT l.from_service_id AS service_id,r.id,r.name,{FOLDER_PATHS} FROM ka.inventory_links l
            JOIN ka.repositories r ON r.id=l.to_code_project_id WHERE l.kind='built_from' AND l.from_service_id=ANY(%s)
            AND r.archived_at IS NULL ORDER BY lower(r.name),r.id''',
```

the services query selects `id,name,description,deployments,links`, each service in the returned context gets
`'connections': [connection(link) for link in s['links']]` with

```python
def connection(link: dict) -> str:
    """A connects_to link of a service as one line: 'connects to prod-db (server)' or 'billing (service) connects to it'."""
    other = f"{link['name']} ({link['entry_kind'].replace('_', ' ')})"
    return f'connects to {other}' if link['direction'] == 'outgoing' else f'{other} connects to it'
```

and `_sections` prints them for services: `('connections: ', s['connections'])` after the `code: ` part. Update exact
expected dicts in `tests/test_generations.py` only by adding `'connections': []` (and the SQL insert on line ~54 to
`INSERT INTO ka.inventory_links(project_id,from_service_id,to_code_project_id,kind) VALUES (%s,%s,%s,'built_from')`).

`agent_wizard.py`: `ARRAY(SELECT repository_id FROM ka.service_repositories r WHERE r.service_id=s.id)` becomes
`ARRAY(SELECT to_code_project_id FROM ka.inventory_links l WHERE l.kind='built_from' AND l.from_service_id=s.id)`.

`knowledge_agents/run_resources.py`: `SERVICE_CODE` becomes

```python
SERVICE_CODE = '''SELECT r.id FROM ka.inventory_links l JOIN ka.repositories r ON r.id=l.to_code_project_id
    WHERE l.kind='built_from' AND r.project_id=%s AND l.from_service_id=ANY(%s) AND r.archived_at IS NULL
    ORDER BY lower(r.name),r.id'''
```

`knowledge_agents/config.py`: add `s.links` after `s.dependents` in the services query.

- [ ] **Step 7: Rewrite the direct-SQL tests**

- `test_service_dependencies.py`: keep the API-level parts of each test; every SQL read or write of
  `ka.service_dependencies` becomes the same against `ka.inventory_links` with `kind='depends_on'`
  (`service_id` → `from_service_id`, `depends_on_service_id` → `to_service_id`). Delete the tests that run
  `0042_service_dependencies.up/down.sql` and any test that only checks the old table's own constraints that
  `test_inventory_links_migration.py` now covers (self-link, description length).
- `test_service_dependency_api.py` lines ~45, 55, 69: same column renames against `ka.inventory_links`.
- `test_services.py` lines ~47 and ~102: `ka.service_repositories` → `ka.inventory_links` with `kind='built_from'`,
  `repository_id` → `to_code_project_id`, `service_id` → `from_service_id`.
- `git rm` `test_system_graph_migration.py`, `test_graph_inventory_sync_migration.py`,
  `test_graph_environment_tree_migration.py`.
- `grep -rn "service_dependencies\|service_repositories\|kg_" services/admin-api/src services/admin-api/tests` prints
  nothing.

- [ ] **Step 8: `docs/migrations.md`**

Append:

```markdown
## 0095_inventory_links

Connections between inventory entries replace the stored system graph. `inventory_links` holds `depends_on`
(service → service), `built_from` (service → code project) and `connects_to` (any entry → any entry) with a description
and string attributes; each end is a foreign key to its entry table and deleting an entry deletes its links.
`inventory_documents` attaches documents to entries. Rows of `service_dependencies` and `service_repositories` move into
`inventory_links`; documents attached to graph nodes move to the entry behind the node (a deployment's to its service),
and attachments of hand-drawn nodes are dropped (the documents stay). `kg_nodes`, `kg_edges`, `kg_node_documents`,
`kg_graph_version` and their triggers are dropped. `service_assets` reads the new table and gains `links` (the service's
`connects_to` links). The down migration refuses while `connects_to` links, link attributes or attachments exist;
otherwise it restores the old tables and an empty graph, which the old code's start-up sync refills.
```

- [ ] **Step 9: Run the tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_inventory_links_migration.py tests/test_service_dependencies.py tests/test_service_dependency_api.py tests/test_services.py tests/test_generations.py tests/test_service_environments.py tests/test_environments.py tests/test_code_projects.py tests/test_code_project_remove.py`
Expected: PASS (except the known `test_environments.py::test_migration_reapply_and_populated_rollback_protection`).
Then run the whole backend suite: `uv run pytest -q` — no new failures compared with `main` (known: the code-graph
tests without `services/code-graph/node_modules`, `test_deploy` docs, the `test_environments` migration test).
Run `bash db/tests/run.sh` from the worktree root if Docker is available; it must print the new PASS line.

- [ ] **Step 10: Commit**

```bash
git add db/migrations/0095_inventory_links.up.sql db/migrations/0095_inventory_links.down.sql db/tests/run.sh docs/migrations.md services/admin-api
git commit -m "feat(db): inventory links and document attachments replace the stored system graph (0095)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Links API

**Files:**
- Create: `services/admin-api/src/knowledge_admin/inventory_links_api.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py` (include the router)
- Test: `services/admin-api/tests/test_inventory_links_api.py`

**Interfaces:**
- Consumes: `ka.inventory_links`, `ka.inventory_entries` (Task 2).
- Produces: `inventory_links_api.build_api(app: str, store: RepositoryStore | None) -> APIRouter`; constants
  `ENTRY_TABLES: dict[str, str]`, `ENTRY_LABELS: dict[str, str]`, model `Entry(kind: EntryKind, id: UUID)` and
  helper `find_entry(conn, project, entry: Entry) -> None` (404 when missing) — Task 4 reuses them in the same module.
- Link JSON: `{"id", "kind", "from": {"kind","id","name"}, "to": {"kind","id","name"}, "description", "attributes",
  "revision", "created_at", "updated_at"}`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_inventory_links_api.py`:

```python
"""Inventory → connections between services, servers, environments and code projects."""
from uuid import uuid4

import pytest

from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def entries(client, store):
    service = lambda name: client.post('/api/services', headers=HEADERS, json={'name': name}).json()['id']
    made = {'api': service('api'), 'worker': service('worker'),
            'prod-1': client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()['id'],
            'production': client.post('/api/environments', headers=HEADERS, json={'name': 'production'}).json()['id'],
            'code': str(store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', ''))}
    return made


def connect(client, kind, start, end, **fields):
    return client.post('/api/inventory/links', headers=HEADERS, json={'from': {'kind': start[0], 'id': start[1]},
                                                                      'to': {'kind': end[0], 'id': end[1]}, 'kind': kind, **fields})


def test_create_list_update_and_delete_a_connection(client, store):
    ids = entries(client, store)
    made = connect(client, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']),
                   description='reads its database', attributes={'port': '5432'})
    assert made.status_code == 201, made.text
    link = made.json()['link']
    assert (link['from'], link['to']) == ({'kind': 'service', 'id': ids['api'], 'name': 'api'},
                                          {'kind': 'server', 'id': ids['prod-1'], 'name': 'prod-1'})
    assert (link['description'], link['attributes'], link['revision']) == ('reads its database', {'port': '5432'}, 1)
    for kind, entry_id in (('service', ids['api']), ('server', ids['prod-1'])):  # either end lists it
        listed = client.get('/api/inventory/links', headers=HEADERS, params={'entry_kind': kind, 'entry_id': entry_id}).json()['links']
        assert [row['id'] for row in listed] == [link['id']]
    assert client.get('/api/inventory/links', headers=HEADERS, params={'entry_kind': 'service', 'entry_id': ids['worker']}).json()['links'] == []
    saved = client.put(f"/api/inventory/links/{link['id']}", headers=HEADERS,
                       json={'description': 'replica', 'attributes': {}, 'revision': 1})
    assert saved.status_code == 200 and saved.json()['link']['revision'] == 2
    assert message(client.put(f"/api/inventory/links/{link['id']}", headers=HEADERS,
                              json={'description': 'x', 'attributes': {}, 'revision': 1})) == (
        409, 'This connection was changed by someone else. Reload and try again.')
    assert client.delete(f"/api/inventory/links/{link['id']}", headers=HEADERS).status_code == 204
    assert message(client.delete(f"/api/inventory/links/{link['id']}", headers=HEADERS)) == (404, 'Connection not found.')
    with store.connect() as conn:
        actions = [row['action'] for row in conn.execute(
            "SELECT action FROM ka.audit_events WHERE action LIKE 'inventory.link.%' ORDER BY created_at,id")]
    assert actions == ['inventory.link.created', 'inventory.link.updated', 'inventory.link.deleted']


def test_kinds_only_join_the_entries_they_allow(client, store):
    ids = entries(client, store)
    assert message(connect(client, 'depends_on', ('service', ids['api']), ('server', ids['prod-1']))) == (
        422, 'A service can only depend on another service.')
    assert message(connect(client, 'built_from', ('server', ids['prod-1']), ('code_project', ids['code']))) == (
        422, 'Only a service can be built from a code project.')
    assert message(connect(client, 'connects_to', ('server', ids['prod-1']), ('server', ids['prod-1']))) == (
        422, 'An entry cannot connect to itself.')
    assert connect(client, 'depends_on', ('service', ids['api']), ('service', ids['worker'])).status_code == 201
    assert connect(client, 'depends_on', ('service', ids['worker']), ('service', ids['api'])).status_code == 201  # mutual
    assert message(connect(client, 'depends_on', ('service', ids['api']), ('service', ids['worker']))) == (
        409, 'This connection already exists.')
    assert connect(client, 'connects_to', ('service', ids['api']), ('service', ids['worker'])).status_code == 201  # another kind
    assert connect(client, 'built_from', ('service', ids['api']), ('code_project', ids['code'])).status_code == 201
    assert connect(client, 'connects_to', ('environment', ids['production']), ('server', ids['prod-1'])).status_code == 201


def test_unknown_archived_and_bad_input_are_refused(client, store):
    ids = entries(client, store)
    assert message(connect(client, 'connects_to', ('server', str(uuid4())), ('service', ids['api']))) == (
        404, 'That server was not found.')
    with store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (ids['code'],))
    assert message(connect(client, 'built_from', ('service', ids['api']), ('code_project', ids['code']))) == (
        404, 'That code project was not found.')
    too_many = {f'k{i}': 'v' for i in range(21)}
    assert message(connect(client, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']), attributes=too_many)) == (
        422, 'Use at most 20 attributes.')
    assert message(connect(client, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']), attributes={' ': 'v'})) == (
        422, 'Attribute names are 1–64 characters.')
    assert message(connect(client, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']), attributes={'k': 'v' * 501})) == (
        422, 'Attribute values are at most 500 characters.')
    assert connect(client, 'connects_to', ('service', ids['api']), ('server', ids['prod-1']), attributes={'port': 5432}).status_code == 422
    assert message(client.get('/api/inventory/links', headers=HEADERS, params={'entry_kind': 'service'})) == (
        400, 'Give both entry_kind and entry_id, or neither.')


def test_entries_of_another_project_are_not_found(client, store):
    ids = entries(client, store)
    with store.connect() as conn:
        other = store.project(conn, 'elsewhere')
        foreign = conn.execute("INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'far',%s,false) RETURNING id",
                               (other, f'{uuid4().hex[:8]}.internal')).fetchone()['id']
    assert message(connect(client, 'connects_to', ('service', ids['api']), ('server', str(foreign)))) == (
        404, 'That server was not found.')
    assert message(client.put(f'/api/inventory/links/{uuid4()}', headers=HEADERS,
                              json={'description': '', 'attributes': {}, 'revision': 1})) == (404, 'Connection not found.')


def test_a_map_edit_makes_an_open_services_form_stale(client, store):
    ids = entries(client, store)
    revision = next(s['revision'] for s in client.get('/api/services', headers=HEADERS).json()['services'] if s['id'] == ids['api'])
    assert connect(client, 'depends_on', ('service', ids['api']), ('service', ids['worker'])).status_code == 201
    stale = client.put(f"/api/services/{ids['api']}", headers=HEADERS, json={'name': 'api', 'revision': revision})
    assert stale.status_code == 409
    services = {s['name']: s for s in client.get('/api/services', headers=HEADERS).json()['services']}
    assert [d['service_id'] for d in services['api']['dependencies']] == [ids['worker']]
    assert [d['service_id'] for d in services['worker']['dependents']] == [ids['api']]


def test_deleting_an_entry_deletes_its_links(client, store):
    ids = entries(client, store)
    connect(client, 'connects_to', ('service', ids['worker']), ('server', ids['prod-1']))
    revision = next(s['revision'] for s in client.get('/api/services', headers=HEADERS).json()['services'] if s['id'] == ids['worker'])
    assert client.delete(f"/api/services/{ids['worker']}", headers=HEADERS,
                         params={'revision': revision, 'confirmed': True}).status_code == 200
    assert client.get('/api/inventory/links', headers=HEADERS).json()['links'] == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_inventory_links_api.py`
Expected: FAIL with 404 on `/api/inventory/links`.

- [ ] **Step 3: Write `inventory_links_api.py`**

```python
"""Inventory → connections between entries (services, servers, environments, code projects) in ka.inventory_links, and
documents attached to entries in ka.inventory_documents. The System map draws and edits both; the Services page edits a
service's depends_on and built_from links through its own form too, so writes to those bump the services' revision."""
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.access import request_actor
from knowledge_admin.event_templates import has_nul
from knowledge_admin.repositories import RepositoryStore

EntryKind = Literal['service', 'server', 'environment', 'code_project']
LinkKind = Literal['depends_on', 'built_from', 'connects_to']
ENTRY_TABLES = {'service': 'services', 'server': 'servers', 'environment': 'environments', 'code_project': 'repositories'}
ENTRY_LABELS = {'service': 'service', 'server': 'server', 'environment': 'environment', 'code_project': 'code project'}
ALLOWED = {'depends_on': ({'service'}, {'service'}), 'built_from': ({'service'}, {'code_project'}),
           'connects_to': (set(ENTRY_TABLES), set(ENTRY_TABLES))}
WRONG_PAIR = {'depends_on': 'A service can only depend on another service.',
              'built_from': 'Only a service can be built from a code project.'}
SERVICE_FORM_KINDS = ('depends_on', 'built_from')
SELF = 'An entry cannot connect to itself.'
DUPLICATE = 'This connection already exists.'
STALE = 'This connection was changed by someone else. Reload and try again.'
NO_LINK = 'Connection not found.'
MAX_ATTRIBUTES, MAX_KEY, MAX_VALUE = 20, 64, 500
LINK = '''SELECT l.id,l.kind,l.description,l.attributes,l.revision,l.created_at,l.updated_at,
    jsonb_build_object('kind',l.from_kind,'id',l.from_id,'name',f.name) AS "from",
    jsonb_build_object('kind',l.to_kind,'id',l.to_id,'name',t.name) AS "to"
    FROM ka.inventory_links l JOIN ka.inventory_entries f ON f.id=l.from_id JOIN ka.inventory_entries t ON t.id=l.to_id'''


class Entry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: EntryKind
    id: UUID


class LinkFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str = Field(default='', max_length=4000)
    attributes: dict[str, str] = Field(default_factory=dict)


class LinkDraft(LinkFields):
    start: Entry = Field(alias='from')
    end: Entry = Field(alias='to')
    kind: LinkKind


class LinkChange(LinkFields):
    revision: int = Field(ge=1, strict=True)


def check_fields(value: LinkFields) -> None:
    if has_nul(value.description):
        raise HTTPException(400, 'Remove the NUL character from Description.')
    if has_nul(value.attributes):
        raise HTTPException(400, 'Remove the NUL character from the attributes.')
    if len(value.attributes) > MAX_ATTRIBUTES:
        raise HTTPException(422, f'Use at most {MAX_ATTRIBUTES} attributes.')
    for key, text in value.attributes.items():
        if not key.strip() or len(key) > MAX_KEY:
            raise HTTPException(422, f'Attribute names are 1–{MAX_KEY} characters.')
        if len(text) > MAX_VALUE:
            raise HTTPException(422, f'Attribute values are at most {MAX_VALUE} characters.')


def lock_inventory(conn, project: UUID) -> None:
    """Services saves take this lock before rewriting a service's links; every link write takes it first too."""
    conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))


def find_entry(conn, project: UUID, entry: Entry) -> None:
    archived = ' AND archived_at IS NULL' if entry.kind == 'code_project' else ''
    found = conn.execute(f'SELECT 1 FROM ka.{ENTRY_TABLES[entry.kind]} WHERE id=%s AND project_id=%s{archived} FOR SHARE',
                         (entry.id, project)).fetchone()
    if found is None:
        raise HTTPException(404, f'That {ENTRY_LABELS[entry.kind]} was not found.')


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Inventory storage is not configured')
        return store

    def audit(conn, project: UUID, request: Request, action: str, resource_type: str, resource_id: UUID, details: dict) -> None:
        actor = database().actor(conn, *request_actor(request))
        conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,%s,%s,%s)""", (project, actor, action, resource_type, resource_id, Jsonb(details)))

    def link_row(conn, project: UUID, link_id: UUID) -> dict:
        row = conn.execute(LINK + ' WHERE l.id=%s AND l.project_id=%s', (link_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NO_LINK)
        return row

    def locked(conn, project: UUID, link_id: UUID) -> dict:
        row = conn.execute('''SELECT kind,revision,from_kind,from_id,to_kind,to_id FROM ka.inventory_links
            WHERE id=%s AND project_id=%s FOR UPDATE''', (link_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NO_LINK)
        return row

    def touch_services(conn, kind: str, ends: list[tuple[str, UUID]]) -> None:
        """An open Services form holds the service's revision; changing a link that form shows makes it stale."""
        if kind in SERVICE_FORM_KINDS:
            conn.execute('UPDATE ka.services SET revision=revision+1 WHERE id=ANY(%s)',
                         ([entry_id for entry_kind, entry_id in ends if entry_kind == 'service'],))

    @router.get('/inventory/links')
    def links(entry_kind: EntryKind | None = None, entry_id: UUID | None = None) -> dict:
        if (entry_kind is None) != (entry_id is None):
            raise HTTPException(400, 'Give both entry_kind and entry_id, or neither.')
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            rows = conn.execute(LINK + ''' WHERE l.project_id=%s AND (%s::uuid IS NULL OR l.from_id=%s OR l.to_id=%s)
                ORDER BY l.kind,lower(f.name),lower(t.name),l.id''', (project, entry_id, entry_id, entry_id)).fetchall()
        return {'links': rows}

    @router.post('/inventory/links', status_code=201)
    def create(value: LinkDraft, request: Request) -> dict:
        check_fields(value)
        starts, ends = ALLOWED[value.kind]
        if value.start.kind not in starts or value.end.kind not in ends:
            raise HTTPException(422, WRONG_PAIR[value.kind])
        if value.start.id == value.end.id:
            raise HTTPException(422, SELF)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            lock_inventory(conn, project)
            find_entry(conn, project, value.start)
            find_entry(conn, project, value.end)
            actor = db.actor(conn, *request_actor(request))
            try:
                with conn.transaction():
                    link_id = conn.execute(f'''INSERT INTO ka.inventory_links(project_id,from_{value.start.kind}_id,
                        to_{value.end.kind}_id,kind,description,attributes,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                        (project, value.start.id, value.end.id, value.kind, value.description, Jsonb(value.attributes),
                         actor)).fetchone()['id']
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, DUPLICATE) from None
            touch_services(conn, value.kind, [(value.start.kind, value.start.id), (value.end.kind, value.end.id)])
            audit(conn, project, request, 'inventory.link.created', 'inventory_link', link_id,
                  {'kind': value.kind, 'from': f'{value.start.kind}:{value.start.id}', 'to': f'{value.end.kind}:{value.end.id}'})
            return {'link': link_row(conn, project, link_id)}

    @router.put('/inventory/links/{link_id}')
    def save(link_id: UUID, value: LinkChange, request: Request) -> dict:
        check_fields(value)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            lock_inventory(conn, project)
            current = locked(conn, project, link_id)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE)
            conn.execute('UPDATE ka.inventory_links SET description=%s,attributes=%s,revision=revision+1 WHERE id=%s',
                         (value.description, Jsonb(value.attributes), link_id))
            touch_services(conn, current['kind'], [(current['from_kind'], current['from_id']), (current['to_kind'], current['to_id'])])
            audit(conn, project, request, 'inventory.link.updated', 'inventory_link', link_id, {'kind': current['kind']})
            return {'link': link_row(conn, project, link_id)}

    @router.delete('/inventory/links/{link_id}', status_code=204)
    def delete(link_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            lock_inventory(conn, project)
            current = locked(conn, project, link_id)
            conn.execute('DELETE FROM ka.inventory_links WHERE id=%s', (link_id,))
            touch_services(conn, current['kind'], [(current['from_kind'], current['from_id']), (current['to_kind'], current['to_id'])])
            audit(conn, project, request, 'inventory.link.deleted', 'inventory_link', link_id,
                  {'kind': current['kind'], 'from': f"{current['from_kind']}:{current['from_id']}",
                   'to': f"{current['to_kind']}:{current['to_id']}"})
        return Response(status_code=204)

    return router
```

In `api.py`, add `inventory_links_api` to the `from knowledge_admin import …` list and
`router.include_router(inventory_links_api.build_api(app_name, repository_store))` where `system_graph_api` used to be.

The Services page's `save` already takes `SELECT id FROM ka.projects … FOR NO KEY UPDATE` first, so link writes and
Services saves are serialised by the same lock.

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_inventory_links_api.py tests/test_services.py tests/test_service_dependency_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/inventory_links_api.py services/admin-api/src/knowledge_admin/api.py services/admin-api/tests/test_inventory_links_api.py
git commit -m "feat(api): connect inventory entries — depends_on, built_from and connects_to links with attributes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Documents on entries API

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/inventory_links_api.py`
- Test: `services/admin-api/tests/test_inventory_documents_api.py`

**Interfaces:**
- Consumes: `Entry`, `find_entry`, `lock_inventory`, `audit` from Task 3 (same module, inside `build_api`).
- Produces: `GET /api/inventory/documents?entry_kind=&entry_id=` → `{"documents": [{"id", "entry_kind", "entry_id",
  "document_id", "slug", "title", "created_at"}]}` (ordered by lower(title), id); `POST /api/inventory/documents`
  `{"entry": {"kind","id"}, "document_id"}` → 201 `{"attachment": {...}}`; `DELETE /api/inventory/documents/{id}` → 204.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_inventory_documents_api.py`:

```python
"""Inventory → documents attached to services, servers, environments and code projects."""
from uuid import uuid4

import pytest

from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def document(client, slug='runbook', title='Runbook'):
    made = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': slug, 'title': title, 'markdown': f'# {title}\n'})
    assert made.status_code == 201, made.text
    return made.json()['id']


def test_attach_list_and_detach(client, store):
    server = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()['id']
    runbook, backups = document(client), document(client, 'backups', 'Backups')
    entry = {'kind': 'server', 'id': server}
    first = client.post('/api/inventory/documents', headers=HEADERS, json={'entry': entry, 'document_id': runbook})
    assert first.status_code == 201, first.text
    assert client.post('/api/inventory/documents', headers=HEADERS, json={'entry': entry, 'document_id': backups}).status_code == 201
    assert message(client.post('/api/inventory/documents', headers=HEADERS, json={'entry': entry, 'document_id': runbook})) == (
        409, 'This document is already attached.')
    listed = client.get('/api/inventory/documents', headers=HEADERS, params={'entry_kind': 'server', 'entry_id': server}).json()['documents']
    assert [(row['title'], row['slug'], row['document_id']) for row in listed] == [('Backups', 'backups', backups), ('Runbook', 'runbook', runbook)]
    attachment = first.json()['attachment']['id']
    assert client.delete(f'/api/inventory/documents/{attachment}', headers=HEADERS).status_code == 204
    assert message(client.delete(f'/api/inventory/documents/{attachment}', headers=HEADERS)) == (404, 'Attachment not found.')
    assert client.get(f'/api/documentation/documents/{runbook}', headers=HEADERS).status_code == 200  # the document stays
    with store.connect() as conn:
        actions = [row['action'] for row in conn.execute(
            "SELECT action FROM ka.audit_events WHERE action LIKE 'inventory.document.%' ORDER BY created_at,id")]
    assert actions == ['inventory.document.attached', 'inventory.document.attached', 'inventory.document.detached']


def test_unknown_entries_and_documents_are_refused(client):
    service = client.post('/api/services', headers=HEADERS, json={'name': 'api'}).json()['id']
    assert message(client.post('/api/inventory/documents', headers=HEADERS,
                               json={'entry': {'kind': 'service', 'id': service}, 'document_id': str(uuid4())})) == (404, 'Document not found.')
    assert message(client.post('/api/inventory/documents', headers=HEADERS,
                               json={'entry': {'kind': 'environment', 'id': str(uuid4())}, 'document_id': document(client)})) == (
        404, 'That environment was not found.')
    assert client.get('/api/inventory/documents', headers=HEADERS, params={'entry_kind': 'service'}).status_code == 422


def test_deleting_the_entry_or_the_document_removes_the_attachment(client, store):
    environment = client.post('/api/environments', headers=HEADERS, json={'name': 'production'}).json()
    runbook = document(client)
    client.post('/api/inventory/documents', headers=HEADERS, json={'entry': {'kind': 'environment', 'id': environment['id']}, 'document_id': runbook})
    assert client.delete(f"/api/environments/{environment['id']}", headers=HEADERS,
                         params={'revision': environment['revision'], 'confirmed': True}).status_code == 200
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.inventory_documents').fetchone()['n'] == 0
```

(If `GET /api/documentation/documents/{id}` is not the document read route, use the route the Documents page uses —
find it with `grep -n "@router.get" services/admin-api/src/knowledge_admin/documents.py` — and keep the assertion that
the document still exists.)

- [ ] **Step 2: Run them to see them fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_inventory_documents_api.py`
Expected: FAIL with 404 / 405 on `/api/inventory/documents`.

- [ ] **Step 3: Add the routes to `inventory_links_api.py`**

Module level:

```python
NO_DOCUMENT = 'Document not found.'
NO_ATTACHMENT = 'Attachment not found.'
ATTACHED = 'This document is already attached.'
ATTACHMENT = '''SELECT a.id,a.entry_kind,a.entry_id,a.document_id,d.slug,d.title,a.created_at
    FROM ka.inventory_documents a JOIN ka.document_details d ON d.id=a.document_id'''


class AttachmentDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    entry: Entry
    document_id: UUID
```

Inside `build_api`, before `return router`:

```python
    @router.get('/inventory/documents')
    def attachments(entry_kind: EntryKind, entry_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            rows = conn.execute(ATTACHMENT + ' WHERE a.project_id=%s AND a.entry_id=%s ORDER BY lower(d.title),a.id',
                                (project, entry_id)).fetchall()
        return {'documents': rows}

    @router.post('/inventory/documents', status_code=201)
    def attach(value: AttachmentDraft, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            lock_inventory(conn, project)
            find_entry(conn, project, value.entry)
            if conn.execute('SELECT 1 FROM ka.documents WHERE id=%s AND project_id=%s FOR SHARE',
                            (value.document_id, project)).fetchone() is None:
                raise HTTPException(404, NO_DOCUMENT)
            try:
                with conn.transaction():
                    attachment = conn.execute(f'''INSERT INTO ka.inventory_documents(project_id,{value.entry.kind}_id,document_id)
                        VALUES (%s,%s,%s) RETURNING id''', (project, value.entry.id, value.document_id)).fetchone()['id']
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, ATTACHED) from None
            audit(conn, project, request, 'inventory.document.attached', value.entry.kind, value.entry.id,
                  {'document_id': str(value.document_id)})
            return {'attachment': conn.execute(ATTACHMENT + ' WHERE a.id=%s', (attachment,)).fetchone()}

    @router.delete('/inventory/documents/{attachment_id}', status_code=204)
    def detach(attachment_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            lock_inventory(conn, project)
            removed = conn.execute('''DELETE FROM ka.inventory_documents WHERE id=%s AND project_id=%s
                RETURNING entry_kind,entry_id,document_id''', (attachment_id, project)).fetchone()
            if removed is None:
                raise HTTPException(404, NO_ATTACHMENT)
            audit(conn, project, request, 'inventory.document.detached', removed['entry_kind'], removed['entry_id'],
                  {'document_id': str(removed['document_id'])})
        return Response(status_code=204)
```

- [ ] **Step 4: Run the tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_inventory_documents_api.py tests/test_inventory_links_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/inventory_links_api.py services/admin-api/tests/test_inventory_documents_api.py
git commit -m "feat(api): attach documents to services, servers, environments and code projects

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The map computed from the inventory

**Files:**
- Create: `services/admin-api/src/knowledge_admin/inventory_map.py`, `services/admin-api/src/knowledge_admin/map_api.py`
- Delete: `services/admin-api/src/knowledge_admin/system_graph.py`, `services/admin-api/tests/test_system_graph.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py`
- Modify test: `services/admin-api/tests/test_system_graph_render.py` (only if it builds views with
  `system_graph.build_view`; make it build plain view dicts instead, same assertions)
- Test: `services/admin-api/tests/test_inventory_map.py`, `services/admin-api/tests/test_map_api.py`

**Interfaces:**
- Consumes: tables from Task 2; `system_graph_render.mermaid(view)`, `.d2(view)`, `.svg(text, layout)`,
  `.RenderError` (unchanged; they read `view['nodes']` with `id`, `parent_id`, `kind`, `name`; `view['boundary']`
  with `id`, `name`; `view['edges']` with `from`, `to`, `type`, `count`).
- Produces: `inventory_map.Inventory`, `inventory_map.load(conn, project) -> Inventory`,
  `inventory_map.build_view(inventory, view: str, root: str | None, depth: int) -> dict`,
  `inventory_map.search(inventory, q: str, kind: str | None, limit: int) -> list[dict]`, `ViewError(status, message)`,
  `MAX_DEPTH = 3`; routes `GET /api/map/view/{infrastructure|services}` and `GET /api/map/search`.
- Map node ids are strings: `environment:<env>`, `server:<env|top>:<server>`, `service:<env|top>:<server>:<service>`
  (deployed), `service:<env>:<service>` (environment member without a deployment there), and in the services view
  `service:<service>`, `code_project:<code project>`.

- [ ] **Step 1: Write the failing unit tests**

Create `services/admin-api/tests/test_inventory_map.py` (pure — builds `Inventory` by hand):

```python
"""The System map from the inventory: placements, links, roll-up, drill-in, search."""
from uuid import uuid4

import pytest

from knowledge_admin.inventory_map import Inventory, ViewError, build_view, search

E = {name: uuid4() for name in ('production', 'staging', 'prod-1', 'spare', 'api', 'worker', 'db', 'code', 'old-code')}


def inventory(**overrides) -> Inventory:
    names = {('environment', E['production']): 'production', ('environment', E['staging']): 'staging',
             ('server', E['prod-1']): 'prod-1', ('server', E['spare']): 'spare',
             ('service', E['api']): 'api', ('service', E['worker']): 'worker', ('service', E['db']): 'db',
             ('code_project', E['code']): 'api-code'}
    base = dict(
        entries={key: {'name': name, 'description': ''} for key, name in names.items()},
        environment_servers={(E['production'], E['prod-1']), (E['staging'], E['prod-1'])},
        environment_services={(E['production'], E['api']), (E['production'], E['db']), (E['staging'], E['api'])},
        deployments={(E['api'], E['prod-1'], E['production']), (E['api'], E['prod-1'], E['staging']),
                     (E['worker'], E['prod-1'], None)},
        links=[], documents={})
    base.update(overrides)
    return Inventory(**base)


def names(view) -> dict:
    by_id = {node['id']: node for node in view['nodes']}
    return {node['name'] if node['parent_id'] is None else f"{by_id[node['parent_id']]['name']} › {node['name']}": node
            for node in view['nodes']}


def test_infrastructure_places_servers_under_environments_and_services_under_servers():
    view = build_view(inventory(), 'infrastructure', None, 3)
    tree = names(view)
    assert set(tree) == {'production', 'staging', 'production › prod-1', 'staging › prod-1', 'prod-1',
                         'prod-1 › api', 'prod-1 › worker', 'production › db', 'spare'}
    # prod-1 › api appears twice (production and staging copies); the top-level prod-1 holds the deployment without an environment
    assert sum(1 for n in view['nodes'] if n['name'] == 'api') == 2
    top_prod = next(n for n in view['nodes'] if n['id'] == f"server:top:{E['prod-1']}")
    assert [n['name'] for n in view['nodes'] if n['parent_id'] == top_prod['id']] == ['worker']
    db = next(n for n in view['nodes'] if n['name'] == 'db')
    assert db['parent_id'] == f"environment:{E['production']}" and db['id'] == f"service:{E['production']}:{E['db']}"
    assert {(n['entry_kind'], n['entry_id']) for n in view['nodes'] if n['name'] == 'prod-1'} == {('server', E['prod-1'])}


def test_depth_folds_and_drill_in_shows_inside_with_breadcrumb():
    top = build_view(inventory(), 'infrastructure', None, 1)
    assert [n['name'] for n in top['nodes']] == ['prod-1', 'production', 'spare', 'staging']
    production = next(n for n in top['nodes'] if n['name'] == 'production')
    assert production['expandable'] and production['child_count'] == 2
    inside = build_view(inventory(), 'infrastructure', f"server:{E['production']}:{E['prod-1']}", 1)
    assert [n['name'] for n in inside['nodes']] == ['api']
    assert [c['name'] for c in inside['breadcrumb']] == ['production', 'prod-1']
    assert inside['root']['entry_id'] == E['prod-1']


def test_links_stay_within_one_environment_and_roll_up_with_counts():
    links = [{'id': uuid4(), 'kind': 'connects_to', 'from': ('service', E['api']), 'to': ('server', E['prod-1'])},
             {'id': uuid4(), 'kind': 'connects_to', 'from': ('environment', E['staging']), 'to': ('service', E['db'])},
             {'id': uuid4(), 'kind': 'depends_on', 'from': ('service', E['api']), 'to': ('service', E['db'])}]
    view = build_view(inventory(links=links), 'infrastructure', None, 3)
    by_id = {n['id']: n for n in view['nodes']}
    drawn = {(by_id[e['from']]['id'], by_id[e['to']]['id'], e['type']) for e in view['edges']}
    # api → prod-1 is drawn per environment (production copy → production prod-1, staging copy → staging prod-1),
    # never across environments or to the top-level prod-1; depends_on is not an infrastructure link
    assert all(e['type'] == 'connects_to' for e in view['edges'])
    api_to_server = {(a, b) for a, b, _ in drawn if a.startswith('service:')}
    assert api_to_server == {(f"service:{E['production']}:{E['prod-1']}:{E['api']}", f"server:{E['production']}:{E['prod-1']}"),
                             (f"service:{E['staging']}:{E['prod-1']}:{E['api']}", f"server:{E['staging']}:{E['prod-1']}")}
    assert (f"environment:{E['staging']}", f"service:{E['production']}:{E['db']}", 'connects_to') in drawn
    top = build_view(inventory(links=links), 'infrastructure', None, 1)
    assert [(e['from'], e['to'], e['count']) for e in top['edges']] == [
        (f"environment:{E['staging']}", f"environment:{E['production']}", 1)]


def test_drilled_in_views_show_the_other_end_outside():
    links = [{'id': uuid4(), 'kind': 'connects_to', 'from': ('service', E['db']), 'to': ('server', E['spare'])}]
    view = build_view(inventory(links=links), 'infrastructure', f"environment:{E['production']}", 1)
    assert [b['name'] for b in view['boundary']] == ['spare']
    assert view['edges'][0]['to'] == f"server:top:{E['spare']}" and view['edges'][0]['count'] == 1


def test_services_view_is_flat_with_all_link_kinds_and_no_archived_code():
    links = [{'id': uuid4(), 'kind': 'depends_on', 'from': ('service', E['api']), 'to': ('service', E['db'])},
             {'id': uuid4(), 'kind': 'built_from', 'from': ('service', E['api']), 'to': ('code_project', E['code'])},
             {'id': uuid4(), 'kind': 'built_from', 'from': ('service', E['worker']), 'to': ('code_project', E['old-code'])},
             {'id': uuid4(), 'kind': 'connects_to', 'from': ('service', E['api']), 'to': ('server', E['prod-1'])}]
    view = build_view(inventory(links=links), 'services', None, 1)
    assert [n['name'] for n in view['nodes']] == ['api', 'api-code', 'db', 'worker']
    assert sorted(e['type'] for e in view['edges']) == ['built_from', 'depends_on']  # archived code and servers are not here


def test_unknown_root_and_search():
    with pytest.raises(ViewError) as missing:
        build_view(inventory(), 'infrastructure', f"server:{E['staging']}:{E['spare']}", 1)
    assert (missing.value.status, str(missing.value)) == (404, 'Node not found.')
    hits = search(inventory(), 'PRO', None, 10)
    assert [(h['entry_kind'], h['name']) for h in hits] == [('server', 'prod-1'), ('environment', 'production')]  # by name
    assert [h['name'] for h in search(inventory(), 'a', 'service', 10)] == ['api']


def test_documents_come_with_every_placement():
    doc = {'id': uuid4(), 'slug': 'runbook', 'title': 'Runbook'}
    view = build_view(inventory(documents={('server', E['prod-1']): [doc]}), 'infrastructure', None, 3)
    assert [n['documents'] for n in view['nodes'] if n['name'] == 'prod-1'] == [[doc], [doc], [doc]]
```

- [ ] **Step 2: Write the failing API tests**

Create `services/admin-api/tests/test_map_api.py`:

```python
"""Environments → System map over HTTP: views from the inventory in every format, and search."""
import shutil
from uuid import uuid4

import pytest

from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def setup(client):
    server = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()
    production = client.post('/api/environments', headers=HEADERS, json={'name': 'production', 'server_ids': [server['id']]}).json()
    api = client.post('/api/services', headers=HEADERS, json={'name': 'api', 'environment_ids': [production['id']],
        'deployments': [{'server_id': server['id'], 'environment_id': production['id'], 'runtime_name': 'api'}]}).json()
    return server['id'], production['id'], api['id']


def test_infrastructure_view_from_the_inventory(client):
    server, production, api = setup(client)
    view = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 3}).json()
    assert [(n['name'], n['entry_kind']) for n in view['nodes']] == [('production', 'environment'), ('prod-1', 'server'), ('api', 'service')]
    assert set(view) == {'view', 'depth', 'root', 'breadcrumb', 'nodes', 'boundary', 'edges'}
    for fmt, kind in (('mermaid', 'text/plain'), ('d2', 'text/plain')):
        response = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'format': fmt})
        assert response.status_code == 200 and response.headers['content-type'].startswith(kind) and '"production"' in response.text
    if shutil.which('d2'):
        svg = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'format': 'svg', 'layout': 'elk'})
        assert svg.status_code == 200 and svg.headers['content-type'] == 'image/svg+xml'


def test_a_removed_placement_is_not_found_and_bad_input_is_refused(client):
    server, production, api = setup(client)
    root = f'server:{uuid4()}:{server}'
    response = client.get('/api/map/view/infrastructure', headers=HEADERS, params={'root': root})
    assert (response.status_code, response.json()['error']['message']) == (404, 'Node not found.')
    assert client.get('/api/map/view/business', headers=HEADERS).status_code == 422
    assert client.get('/api/map/view/infrastructure', headers=HEADERS, params={'depth': 4}).status_code == 422


def test_links_and_search(client):
    server, production, api = setup(client)
    worker = client.post('/api/services', headers=HEADERS, json={'name': 'worker'}).json()['id']
    client.post('/api/inventory/links', headers=HEADERS, json={'from': {'kind': 'service', 'id': api}, 'to': {'kind': 'service', 'id': worker},
                                                             'kind': 'depends_on'})
    view = client.get('/api/map/view/services', headers=HEADERS).json()
    assert [(e['from'], e['to'], e['type']) for e in view['edges']] == [(f'service:{api}', f'service:{worker}', 'depends_on')]
    hits = client.get('/api/map/search', headers=HEADERS, params={'q': 'wor'}).json()['entries']
    assert hits == [{'entry_kind': 'service', 'entry_id': worker, 'name': 'worker'}]
    assert client.get('/api/map/search', headers=HEADERS, params={'q': 'p', 'kind': 'server'}).json()['entries'][0]['name'] == 'prod-1'
```

- [ ] **Step 3: Run them to see them fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_inventory_map.py tests/test_map_api.py`
Expected: FAIL (`ModuleNotFoundError: knowledge_admin.inventory_map`, 404 on `/api/map/...`).

- [ ] **Step 4: Write `inventory_map.py`**

```python
"""Environments → System map, computed from the inventory on every request — nothing about the map is stored. Two views:
infrastructure (environment › server › service, from memberships and deployments) and services (services and code
projects, flat). Links (ka.inventory_links) are drawn between the nodes that show their entries. A view shows one node's
inside down to a depth, folds deeper nodes into the visible node that contains them, and rolls every link up to the
nodes shown (graph in graph)."""
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

import networkx as nx

VIEWS = ('infrastructure', 'services')
ENTRY_KINDS = ('service', 'server', 'environment', 'code_project')
VIEW_LINKS = {'infrastructure': {'connects_to'}, 'services': {'depends_on', 'built_from', 'connects_to'}}
MAX_DEPTH, MAX_VISIBLE, MAX_LINK_IDS = 3, 500, 20
TOP = 'top'
NOT_FOUND = 'Node not found.'
TOO_MANY = 'Too many nodes; choose a deeper root or a smaller depth.'

Key = tuple[str, UUID]  # (entry kind, entry id)


class ViewError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Inventory:
    entries: dict[Key, dict]                                # name, description (archived code projects left out)
    environment_servers: set[tuple[UUID, UUID]] = field(default_factory=set)    # (environment, server)
    environment_services: set[tuple[UUID, UUID]] = field(default_factory=set)   # (environment, service)
    deployments: set[tuple[UUID, UUID, UUID | None]] = field(default_factory=set)  # (service, server, environment)
    links: list[dict] = field(default_factory=list)          # id, kind, from: Key, to: Key
    documents: dict[Key, list[dict]] = field(default_factory=dict)


ENTRY_ROWS = {
    'environment': 'SELECT id,name,description FROM ka.environments WHERE project_id=%s',
    'server': 'SELECT id,name,description FROM ka.servers WHERE project_id=%s',
    'service': 'SELECT id,name,description FROM ka.services WHERE project_id=%s',
    'code_project': 'SELECT id,name,description FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL',
}


def load(conn, project: UUID) -> Inventory:
    entries = {(kind, row['id']): {'name': row['name'], 'description': row['description']}
               for kind, sql in ENTRY_ROWS.items() for row in conn.execute(sql, (project,))}
    documents: dict[Key, list[dict]] = {}
    for row in conn.execute('''SELECT a.entry_kind,a.entry_id,d.id,d.slug,d.title FROM ka.inventory_documents a
            JOIN ka.document_details d ON d.id=a.document_id WHERE a.project_id=%s ORDER BY lower(d.title),d.id''', (project,)):
        documents.setdefault((row['entry_kind'], row['entry_id']), []).append({'id': row['id'], 'slug': row['slug'], 'title': row['title']})
    return Inventory(
        entries=entries,
        environment_servers={(r['environment_id'], r['server_id']) for r in conn.execute(
            'SELECT environment_id,server_id FROM ka.environment_servers WHERE project_id=%s', (project,))},
        environment_services={(r['environment_id'], r['service_id']) for r in conn.execute(
            'SELECT environment_id,service_id FROM ka.environment_services WHERE project_id=%s', (project,))},
        deployments={(r['service_id'], r['server_id'], r['environment_id']) for r in conn.execute(
            'SELECT service_id,server_id,environment_id FROM ka.service_deployments WHERE project_id=%s', (project,))},
        links=[{'id': r['id'], 'kind': r['kind'], 'from': (r['from_kind'], r['from_id']), 'to': (r['to_kind'], r['to_id'])}
               for r in conn.execute('''SELECT id,kind,from_kind,from_id,to_kind,to_id FROM ka.inventory_links
                   WHERE project_id=%s ORDER BY id''', (project,))],
        documents=documents)


def node_id(kind: str, entry: UUID, *where: UUID | None) -> str:
    return ':'.join([kind, *(TOP if place is None else str(place) for place in where), str(entry)])


def placements(inventory: Inventory, view: str) -> tuple[nx.DiGraph, dict[Key, list[str]]]:
    """The view's containment tree (edges parent → child; node attributes entry, environment, name, description) and the
    map nodes that show each entry."""
    tree, shown = nx.DiGraph(), defaultdict(list)

    def add(node: str, entry: Key, parent: str | None, environment: UUID | None) -> None:
        tree.add_node(node, entry=entry, environment=environment, **inventory.entries[entry])
        if parent is not None:
            tree.add_edge(parent, node)
        shown[entry].append(node)

    known = inventory.entries  # rows are read a moment apart: skip anything whose entry was not read
    if view == 'services':
        for kind, entry in inventory.entries:
            if kind in ('service', 'code_project'):
                add(node_id(kind, entry), (kind, entry), None, None)
        return tree, shown

    for kind, entry in inventory.entries:
        if kind == 'environment':
            add(node_id('environment', entry), ('environment', entry), None, entry)
    homes: dict[UUID, set] = defaultdict(set)  # server → environments it is placed in (None = top level)
    deployments = sorted((d for d in inventory.deployments if ('service', d[0]) in known and ('server', d[1]) in known
                          and (d[2] is None or ('environment', d[2]) in known)), key=str)
    for environment, server in inventory.environment_servers:
        if ('environment', environment) in known:
            homes[server].add(environment)
    for _, server, environment in deployments:
        homes[server].add(environment)
    for kind, server in inventory.entries:
        if kind == 'server':
            for environment in homes[server] or {None}:
                parent = None if environment is None else node_id('environment', environment)
                add(node_id('server', server, environment), ('server', server), parent, environment)
    deployed = set()
    for service, server, environment in deployments:
        node = node_id('service', service, environment, server)
        if node not in tree:  # several runtimes of one service on one server are one node
            add(node, ('service', service), node_id('server', server, environment), environment)
        deployed.add((environment, service))
    for environment, service in inventory.environment_services:
        if (environment, service) not in deployed and ('service', service) in known and ('environment', environment) in known:
            add(node_id('service', service, environment), ('service', service), node_id('environment', environment), environment)
    return tree, shown


def link_edges(inventory: Inventory, view: str, tree: nx.DiGraph, shown: dict[Key, list[str]]) -> list[dict]:
    """One edge per pair of nodes showing a link's ends. When both ends are shown in a common environment (or both at the
    top level) only those pairs are drawn, so a link never crosses from one environment's copy to another's."""
    edges = []
    for link in inventory.links:
        if link['kind'] not in VIEW_LINKS[view]:
            continue
        starts, ends = shown.get(link['from'], []), shown.get(link['to'], [])
        pairs = [(a, b) for a in starts for b in ends if tree.nodes[a]['environment'] == tree.nodes[b]['environment']]
        for a, b in pairs or [(a, b) for a in starts for b in ends]:
            edges.append({'id': link['id'], 'from_id': a, 'to_id': b, 'type': link['kind']})
    return edges


def build_view(inventory: Inventory, view: str, root: str | None, depth: int) -> dict:
    tree, shown_by_entry = placements(inventory, view)
    edges = link_edges(inventory, view, tree, shown_by_entry)

    def order(node: str) -> tuple:
        return tree.nodes[node]['name'].lower(), node

    def kids(node: str | None) -> list[str]:
        found = tree.successors(node) if node is not None else (n for n in tree if tree.in_degree(n) == 0)
        return sorted(found, key=order)

    def parent(node: str) -> str | None:
        return next(iter(tree.predecessors(node)), None)

    def path(node: str | None) -> list[str]:
        ids = []
        while node is not None:
            ids.append(node)
            node = parent(node)
        return ids[::-1]

    def about(node: str) -> dict:
        data = tree.nodes[node]
        kind, entry = data['entry']
        return {'id': node, 'name': data['name'], 'kind': kind, 'entry_kind': kind, 'entry_id': entry}

    if root is not None and root not in tree:
        raise ViewError(404, NOT_FOUND)
    root_path = path(root)
    rows: dict[str, dict] = {}
    representative: dict[str, str] = {}

    def walk(node: str, level: int, visible_parent: str | None, holder: str | None) -> None:
        if level <= depth:
            holder = node
            inner = kids(node)
            data = tree.nodes[node]
            rows[node] = {**about(node), 'parent_id': visible_parent, 'description': data['description'],
                          'child_count': len(inner), 'expandable': bool(inner) and level == depth, 'internal_edges': 0,
                          'documents': list(inventory.documents.get(data['entry'], []))}
            if len(rows) > MAX_VISIBLE:
                raise ViewError(422, TOO_MANY)
        representative[node] = holder
        for child in kids(node):
            walk(child, level + 1, node if level <= depth else visible_parent, holder)

    for child in kids(root):
        walk(child, 1, None, None)

    def outside(node: str) -> str | None:
        """The node an outside end is drawn as, or None when it is the root or contains it."""
        target = path(node)
        common = 0
        while common < min(len(root_path), len(target)) and root_path[common] == target[common]:
            common += 1
        return target[common] if common < len(target) else None

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
                boundary[end_id] = about(end_id)
        group = grouped.setdefault((a, b, edge['type']), {'from': a, 'to': b, 'type': edge['type'], 'count': 0, 'link_ids': []})
        group['count'] += 1
        if edge['id'] not in group['link_ids'] and len(group['link_ids']) < MAX_LINK_IDS:
            group['link_ids'].append(edge['id'])
    if len(rows) + len(boundary) > MAX_VISIBLE:
        raise ViewError(422, TOO_MANY)
    for group in grouped.values():
        group['link_ids'].sort(key=str)
    nodes = []

    def emit(node: str) -> None:  # parents before children, siblings by name
        nodes.append(rows[node])
        for child in kids(node):
            if child in rows:
                emit(child)
    for child in kids(root):
        emit(child)
    return {
        'view': view,
        'depth': depth,
        'root': None if root is None else {**about(root), 'description': tree.nodes[root]['description']},
        'breadcrumb': [{key: about(node)[key] for key in ('id', 'name', 'kind')} for node in root_path],
        'nodes': nodes,
        'boundary': sorted(boundary.values(), key=lambda node: (node['name'].lower(), node['id'])),
        'edges': sorted(grouped.values(), key=lambda edge: (edge['from'], edge['to'], edge['type'])),
    }


def search(inventory: Inventory, q: str, kind: str | None, limit: int) -> list[dict]:
    needle = q.strip().lower()
    hits = [(key, data) for key, data in inventory.entries.items()
            if (kind is None or key[0] == kind) and needle in data['name'].lower()]
    hits.sort(key=lambda hit: (hit[1]['name'].lower(), hit[0][0], str(hit[0][1])))
    return [{'entry_kind': key[0], 'entry_id': key[1], 'name': data['name']} for key, data in hits[:limit]]
```

The `nodes` order in the tests above is parents before children (`emit`). If a test's expected list disagrees with
this rule, fix the test's expected order to the rule — do not change the rule.

- [ ] **Step 5: Write `map_api.py` and wire it**

```python
"""Environments → System map: views of the inventory (inventory_map.py) as JSON, Mermaid, D2 or SVG, and entry search."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse

from knowledge_admin import inventory_map, system_graph_render
from knowledge_admin.event_templates import has_nul
from knowledge_admin.repositories import RepositoryStore

SVG_HEADERS = {'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
               'X-Content-Type-Options': 'nosniff'}


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def inventory():
        if store is None:
            raise HTTPException(503, 'Inventory storage is not configured')
        with store.connect() as conn:
            return inventory_map.load(conn, store.project(conn, app))

    @router.get('/map/view/{view}', response_model=None)
    def view(view: Literal['infrastructure', 'services'], root: str | None = Query(None, max_length=200),
             depth: int = Query(1, ge=1, le=inventory_map.MAX_DEPTH),
             format: Literal['json', 'mermaid', 'd2', 'svg'] = 'json', layout: Literal['tala', 'elk', 'dagre'] = 'tala'):
        try:
            graph = inventory_map.build_view(inventory(), view, root, depth)
        except inventory_map.ViewError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        if format == 'json':
            return graph
        if format == 'mermaid':
            return PlainTextResponse(system_graph_render.mermaid(graph))
        text = system_graph_render.d2(graph)
        if format == 'd2':
            return PlainTextResponse(text)
        try:
            return Response(system_graph_render.svg(text, layout), media_type='image/svg+xml', headers=SVG_HEADERS)
        except system_graph_render.RenderError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @router.get('/map/search')
    def search(q: str = Query('', max_length=200), kind: Literal['service', 'server', 'environment', 'code_project'] | None = None,
               limit: int = Query(50, ge=1, le=200)) -> dict:
        if has_nul(q):
            raise HTTPException(400, 'Remove the NUL character from the search.')
        return {'entries': inventory_map.search(inventory(), q, kind, limit)}

    return router
```

`api.py`: import `map_api` and `router.include_router(map_api.build_api(app_name, repository_store))`. `git rm`
`system_graph.py` and `tests/test_system_graph.py`; if `tests/test_system_graph_render.py` imports `system_graph`,
replace those calls with literal view dicts of the same shape (`nodes` with `id`, `parent_id`, `kind`, `name`;
`boundary`; `edges` with `from`, `to`, `type`, `count`) and keep its assertions. `system_graph_render._shape` maps
unknown kinds to `rectangle`, so entry kinds need no change there.

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_inventory_map.py tests/test_map_api.py tests/test_system_graph_render.py tests/test_inventory_links_api.py tests/test_inventory_documents_api.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A services/admin-api
git commit -m "feat(api): the System map is computed from the inventory — environments hold servers, servers their services

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: System map page — views, links and documents

**Files:**
- Create: `services/admin-ui/app/lib/inventoryMap.ts`, `services/admin-ui/app/components/EntrySheet.tsx`
- Modify: `services/admin-ui/app/components/SystemMapView.tsx`
- Delete: `services/admin-ui/app/lib/systemGraph.ts`, `services/admin-ui/app/components/NodeSheet.tsx`,
  `services/admin-ui/app/components/__tests__/nodeSheet.test.tsx`
- Test: `services/admin-ui/app/components/__tests__/systemMap.test.tsx` (rewrite),
  `services/admin-ui/app/components/__tests__/entrySheet.test.tsx` (new),
  `services/admin-ui/app/lib/__tests__/inventoryMap.test.ts` (new)

**Interfaces:**
- Consumes: `/api/map/view/{map}`, `/api/map/search`, `/api/inventory/links`, `/api/inventory/documents` (Tasks 3–5);
  `getDocuments` from `../lib/documents`; `request` and `ApiError` from `../lib/api`.
- Produces: page URL state `?map=infrastructure|services&root=&depth=&view=&layout=` (defaults `infrastructure`, none,
  1, `diagram`, `tala`).

- [ ] **Step 1: Write the failing lib tests**

Create `services/admin-ui/app/lib/__tests__/inventoryMap.test.ts`:

```ts
import { formatAttributes, linkKindsFrom, mapQuery, parseAttributes, readMapState, targetKinds, viewUrl } from "../inventoryMap";

test("reads and writes the page state with defaults", () => {
  expect(readMapState(new URLSearchParams(""))).toEqual({ map: "infrastructure", root: null, depth: 1, view: "diagram", layout: "tala" });
  expect(readMapState(new URLSearchParams("map=services&depth=9&view=bogus"))).toEqual({ map: "services", root: null, depth: 1, view: "diagram", layout: "tala" });
  expect(mapQuery({ map: "services", root: "service:1", depth: 2, view: "d2", layout: "elk" }).toString())
    .toBe("map=services&root=service%3A1&depth=2&view=d2&layout=elk");
  expect(viewUrl("infrastructure", "server:top:1", 1, "svg", "dagre")).toBe("/api/map/view/infrastructure?root=server%3Atop%3A1&depth=1&format=svg&layout=dagre");
});

test("link kinds follow the entry kinds the API allows", () => {
  expect(linkKindsFrom("service")).toEqual(["connects_to", "depends_on", "built_from"]);
  expect(linkKindsFrom("server")).toEqual(["connects_to"]);
  expect(targetKinds("depends_on")).toEqual(["service"]);
  expect(targetKinds("built_from")).toEqual(["code_project"]);
  expect(targetKinds("connects_to")).toBeUndefined();
});

test("attributes are edited as name: value lines", () => {
  expect(parseAttributes("")).toEqual({ value: {} });
  expect(parseAttributes("port: 5432\n\nprotocol:  https:443 ")).toEqual({ value: { port: "5432", protocol: "https:443" } });
  expect(parseAttributes("no colon")).toEqual({ problem: "Write each attribute as name: value." });
  expect(parseAttributes(": x")).toEqual({ problem: "Write each attribute as name: value." });
  expect(parseAttributes("a: 1\na: 2")).toEqual({ problem: 'The attribute "a" appears twice.' });
  expect(parseAttributes(Array.from({ length: 21 }, (_, i) => `k${i}: v`).join("\n"))).toEqual({ problem: "Use at most 20 attributes." });
  expect(parseAttributes(`${"k".repeat(65)}: v`)).toEqual({ problem: "Attribute names are 1–64 characters." });
  expect(parseAttributes(`k: ${"v".repeat(501)}`)).toEqual({ problem: "Attribute values are at most 500 characters." });
  expect(formatAttributes({ port: "5432", protocol: "https" })).toBe("port: 5432\nprotocol: https");
});
```

- [ ] **Step 2: Write `app/lib/inventoryMap.ts`**

```ts
import { request } from "./api";

export type MapName = "infrastructure" | "services";
export const MAPS: MapName[] = ["infrastructure", "services"];
export const MAP_LABELS: Record<MapName, string> = { infrastructure: "Infrastructure", services: "Services" };
export type EntryKind = "service" | "server" | "environment" | "code_project";
export const ENTRY_LABELS: Record<EntryKind, string> = { service: "service", server: "server", environment: "environment", code_project: "code project" };
export const ENTRY_PAGES: Record<EntryKind, { page: string; label: string }> = {
  service: { page: "/services", label: "Services" }, server: { page: "/servers", label: "Servers" },
  environment: { page: "/environments", label: "Environments" }, code_project: { page: "/code-projects", label: "Code projects" } };
export type LinkKind = "depends_on" | "built_from" | "connects_to";
export const LINK_LABELS: Record<LinkKind, string> = { depends_on: "depends on", built_from: "built from", connects_to: "connects to" };
/** The kinds a link from this entry can have (as the API allows). */
export const linkKindsFrom = (kind: EntryKind): LinkKind[] => kind === "service" ? ["connects_to", "depends_on", "built_from"] : ["connects_to"];
/** The entry kinds a link of this kind can point at; undefined = any. */
export const targetKinds = (kind: LinkKind): EntryKind[] | undefined =>
  kind === "depends_on" ? ["service"] : kind === "built_from" ? ["code_project"] : undefined;
export type MapView = "diagram" | "mermaid" | "d2" | "json";
export const VIEWS: MapView[] = ["diagram", "mermaid", "d2", "json"];
export const VIEW_LABELS: Record<MapView, string> = { diagram: "Diagram", mermaid: "Mermaid", d2: "D2", json: "JSON" };
export type Layout = "tala" | "elk" | "dagre";
export const LAYOUTS: Layout[] = ["tala", "elk", "dagre"];
export const MAX_DEPTH = 3;
export const MAX_ATTRIBUTES = 20;

export interface GraphDocument { id: string; slug: string; title: string }
export interface EntryRef { kind: EntryKind; id: string }
export interface MapEntry extends EntryRef { name: string }
export interface ViewNode { id: string; parent_id: string | null; kind: EntryKind; name: string; description: string; entry_kind: EntryKind;
  entry_id: string; child_count: number; expandable: boolean; internal_edges: number; documents: GraphDocument[] }
export interface BoundaryNode { id: string; name: string; kind: EntryKind; entry_kind: EntryKind; entry_id: string }
export interface ViewEdge { from: string; to: string; type: LinkKind; count: number; link_ids: string[] }
export interface Crumb { id: string; name: string; kind: EntryKind }
export interface MapGraph { view: MapName; depth: number; root: (Crumb & { description: string; entry_kind: EntryKind; entry_id: string }) | null;
  breadcrumb: Crumb[]; nodes: ViewNode[]; boundary: BoundaryNode[]; edges: ViewEdge[] }
export interface Link { id: string; kind: LinkKind; from: MapEntry; to: MapEntry; description: string; attributes: Record<string, string>; revision: number }
export interface EntryHit { entry_kind: EntryKind; entry_id: string; name: string }
export interface Attachment { id: string; entry_kind: EntryKind; entry_id: string; document_id: string; slug: string; title: string }
export interface MapState { map: MapName; root: string | null; depth: number; view: MapView; layout: Layout }

function pick<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? value as T : fallback;
}
/** The System map's state from the page URL; anything unknown falls back to the defaults. */
export function readMapState(params: URLSearchParams): MapState {
  const depth = Number(params.get("depth"));
  return { map: pick(params.get("map"), MAPS, "infrastructure"), root: params.get("root") || null,
    depth: Number.isInteger(depth) && depth >= 1 && depth <= MAX_DEPTH ? depth : 1,
    view: pick(params.get("view"), VIEWS, "diagram"), layout: pick(params.get("layout"), LAYOUTS, "tala") };
}
/** The page URL for a state: the map always, everything else only when it is not the default. */
export function mapQuery(state: MapState): URLSearchParams {
  const query = new URLSearchParams({ map: state.map });
  if (state.root) query.set("root", state.root);
  if (state.depth !== 1) query.set("depth", String(state.depth));
  if (state.view !== "diagram") query.set("view", state.view);
  if (state.layout !== "tala") query.set("layout", state.layout);
  return query;
}
export function viewUrl(map: MapName, root: string | null, depth: number, format: "json" | "mermaid" | "d2" | "svg", layout: Layout = "tala") {
  const query = new URLSearchParams();
  if (root) query.set("root", root);
  query.set("depth", String(depth));
  query.set("format", format);
  if (format === "svg") query.set("layout", layout);
  return `/api/map/view/${map}?${query}`;
}
const send = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const entryQuery = (entry: EntryRef) => new URLSearchParams({ entry_kind: entry.kind, entry_id: entry.id });

export const getView = (map: MapName, root: string | null, depth: number, signal?: AbortSignal) =>
  request<MapGraph>(viewUrl(map, root, depth, "json"), { signal });
export const getViewText = (map: MapName, root: string | null, depth: number, format: "mermaid" | "d2" | "svg", layout: Layout, signal?: AbortSignal) =>
  request<string>(viewUrl(map, root, depth, format, layout), { signal }, "text");
export function searchEntries(q: string, kind?: EntryKind) {
  const query = new URLSearchParams();
  if (kind) query.set("kind", kind);
  query.set("q", q);
  return request<{ entries: EntryHit[] }>(`/api/map/search?${query}`);
}
export const getLinks = (entry: EntryRef) => request<{ links: Link[] }>(`/api/inventory/links?${entryQuery(entry)}`);
export const createLink = (input: { from: EntryRef; to: EntryRef; kind: LinkKind; description: string; attributes: Record<string, string> }) =>
  request<{ link: Link }>("/api/inventory/links", send("POST", input));
export const updateLink = (id: string, input: { description: string; attributes: Record<string, string>; revision: number }) =>
  request<{ link: Link }>(`/api/inventory/links/${id}`, send("PUT", input));
export const deleteLink = (id: string) => request<void>(`/api/inventory/links/${id}`, { method: "DELETE" });
export const getAttachments = (entry: EntryRef) => request<{ documents: Attachment[] }>(`/api/inventory/documents?${entryQuery(entry)}`);
export const attachDocument = (entry: EntryRef, documentId: string) =>
  request<{ attachment: Attachment }>("/api/inventory/documents", send("POST", { entry, document_id: documentId }));
export const detachDocument = (attachmentId: string) => request<void>(`/api/inventory/documents/${attachmentId}`, { method: "DELETE" });

/** Reads the Attributes field — one "name: value" per line — into an object, or says what is wrong. */
export function parseAttributes(text: string): { value: Record<string, string> } | { problem: string } {
  const value: Record<string, string> = {};
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    const colon = line.indexOf(":");
    const name = colon < 0 ? "" : line.slice(0, colon).trim();
    if (!name) return { problem: "Write each attribute as name: value." };
    if (name in value) return { problem: `The attribute "${name}" appears twice.` };
    value[name] = line.slice(colon + 1).trim();
  }
  const entries = Object.entries(value);
  if (entries.length > MAX_ATTRIBUTES) return { problem: `Use at most ${MAX_ATTRIBUTES} attributes.` };
  if (entries.some(([name]) => name.length > 64)) return { problem: "Attribute names are 1–64 characters." };
  if (entries.some(([, text]) => text.length > 500)) return { problem: "Attribute values are at most 500 characters." };
  return { value };
}
export const formatAttributes = (attributes: Record<string, string>) => Object.entries(attributes).map(([name, text]) => `${name}: ${text}`).join("\n");
```

- [ ] **Step 3: Run the lib tests**

Run: `cd services/admin-ui && npx vitest run app/lib/__tests__/inventoryMap.test.ts`
Expected: PASS.

- [ ] **Step 4: Write the failing component tests**

Rewrite `services/admin-ui/app/components/__tests__/systemMap.test.tsx`:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import SystemMapView from "../SystemMapView";

vi.mock("../Mermaid", () => ({ default: ({ chart }: { chart: string }) => <pre data-testid="mermaid">{chart}</pre> }));

const node = (id: string, name: string, kind: string, extra = {}) => ({ id, parent_id: null, kind, name, description: "", entry_kind: kind,
  entry_id: `${kind}-id`, child_count: 0, expandable: false, internal_edges: 0, documents: [], ...extra });
const top = { view: "infrastructure", depth: 1, root: null, breadcrumb: [], boundary: [], edges: [],
  nodes: [node("environment:p", "production", "environment", { child_count: 1, expandable: true }), node("server:top:s", "spare", "server")] };
const inside = { view: "infrastructure", depth: 1, root: { id: "environment:p", name: "production", kind: "environment", description: "", entry_kind: "environment", entry_id: "p" },
  breadcrumb: [{ id: "environment:p", name: "production", kind: "environment" }], nodes: [node("server:p:s1", "prod-1", "server")],
  boundary: [{ id: "server:top:s", name: "spare", kind: "server", entry_kind: "server", entry_id: "s" }],
  edges: [{ from: "server:p:s1", to: "server:top:s", type: "connects_to", count: 2, link_ids: ["l1", "l2"] }] };
const services = { view: "services", depth: 1, root: null, breadcrumb: [], boundary: [], edges: [], nodes: [] };

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
function Where() { return <output data-testid="where">{useLocation().search}</output>; }
const view = (entry = "/system-map") => render(<MemoryRouter initialEntries={[entry]}>
  <Routes><Route path="/system-map" element={<><SystemMapView /><Where /></>} /></Routes></MemoryRouter>);
const where = () => screen.getByTestId("where").textContent;
const TOP = "/api/map/view/infrastructure?depth=1&format=json";
const TOP_SVG = "/api/map/view/infrastructure?depth=1&format=svg&layout=tala";
const PROD = "/api/map/view/infrastructure?root=environment%3Ap&depth=1&format=json";
const PROD_SVG = "/api/map/view/infrastructure?root=environment%3Ap&depth=1&format=svg&layout=tala";
afterEach(() => vi.unstubAllGlobals());

test("shows the infrastructure map with Inside and Outside lists", async () => {
  api({ [TOP]: { json: top }, [TOP_SVG]: { text: "<svg/>" } });
  view();
  expect(screen.getByRole("tab", { name: "Infrastructure" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByRole("img", { name: "Infrastructure map" })).toBeInTheDocument();
  const list = within(screen.getByRole("region", { name: "Inside" }));
  expect(list.getByRole("button", { name: "production" })).toBeInTheDocument();
  expect(list.getByText("environment · +1 inside")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /New node/ })).toBeNull();
});

test("opens an environment, lists what its links reach outside, and goes back with the breadcrumb", async () => {
  const user = userEvent.setup();
  api({ [TOP]: { json: top }, [TOP_SVG]: { text: "<svg/>" }, [PROD]: { json: inside }, [PROD_SVG]: { text: "<svg/>" } });
  view();
  await user.click(await screen.findByRole("button", { name: "Open production" }));
  await waitFor(() => expect(where()).toBe("?map=infrastructure&root=environment%3Ap"));
  const outside = within(await screen.findByRole("region", { name: "Outside" }));
  expect(await outside.findByText("server · 2 links")).toBeInTheDocument();
  await user.click(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("button", { name: "Infrastructure" }));
  await waitFor(() => expect(where()).toBe("?map=infrastructure"));
});

test("switches to the services map and explains an empty map", async () => {
  const user = userEvent.setup();
  api({ [TOP]: { json: top }, [TOP_SVG]: { text: "<svg/>" }, ["/api/map/view/services?depth=1&format=json"]: { json: services } });
  view();
  await user.click(await screen.findByRole("tab", { name: "Services" }));
  await waitFor(() => expect(where()).toBe("?map=services"));
  expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument();
});

test("a root that no longer exists shows the error and the breadcrumb still leads back", async () => {
  api({ ["/api/map/view/infrastructure?root=server%3Ax%3Ay&depth=1&format=json"]: { status: 404, error: "Node not found." },
        ["/api/map/view/infrastructure?root=server%3Ax%3Ay&depth=1&format=svg&layout=tala"]: { status: 404, error: "Node not found." } });
  view("/system-map?map=infrastructure&root=server:x:y");
  expect(await screen.findByText("Node not found.")).toBeInTheDocument();
  expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("button", { name: "Infrastructure" })).toBeInTheDocument();
});
```

Create `services/admin-ui/app/components/__tests__/entrySheet.test.tsx`:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import EntrySheet from "../EntrySheet";

const api = { kind: "service" as const, id: "api", name: "api" };
const link = { id: "l1", kind: "connects_to", from: { kind: "service", id: "api", name: "api" }, to: { kind: "server", id: "s1", name: "prod-1" },
  description: "reads its database", attributes: { port: "5432" }, revision: 1 };
const LINKS = "/api/inventory/links?entry_kind=service&entry_id=api";
const DOCS = "/api/inventory/documents?entry_kind=service&entry_id=api";

function server(routes: Record<string, (init?: RequestInit) => { status?: number; json?: unknown }>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const route = routes[key];
    if (!route) throw new Error(`unexpected ${key}`);
    const { status = 200, json } = route(init);
    return new Response(status === 204 ? null : JSON.stringify(json), { status });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const sheet = (onChanged = vi.fn()) => render(<MemoryRouter><EntrySheet entry={api} onClose={() => {}} onChanged={onChanged} /></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

test("shows the entry's links and documents with a link to its page", async () => {
  server({ [`GET ${LINKS}`]: () => ({ json: { links: [link] } }), [`GET ${DOCS}`]: () => ({ json: { documents: [
    { id: "a1", entry_kind: "service", entry_id: "api", document_id: "d1", slug: "runbook", title: "Runbook" }] } }) });
  sheet();
  expect(await screen.findByText("connects to → prod-1")).toBeInTheDocument();
  expect(screen.getByText("reads its database · port: 5432")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open in Services" })).toHaveAttribute("href", "/services");
  expect(screen.getByRole("link", { name: "Runbook" })).toHaveAttribute("href", "/documents/d1");
});

test("connects to an entry: kinds follow the entry, targets follow the kind", async () => {
  const user = userEvent.setup();
  const onChanged = vi.fn();
  let posted: unknown = null;
  const fetch = server({ [`GET ${LINKS}`]: () => ({ json: { links: [] } }), [`GET ${DOCS}`]: () => ({ json: { documents: [] } }),
    ["GET /api/map/search?kind=service&q=wor"]: () => ({ json: { entries: [{ entry_kind: "service", entry_id: "w", name: "worker" }] } }),
    ["POST /api/inventory/links"]: init => { posted = JSON.parse(String(init?.body)); return { status: 201, json: { link } }; } });
  sheet(onChanged);
  const connect = within(await screen.findByRole("region", { name: "Connect" }));
  await user.click(connect.getByRole("combobox", { name: "Connection kind" }));
  expect(screen.getAllByRole("option").map(option => option.textContent)).toEqual(["connects to", "depends on", "built from"]);
  await user.click(screen.getByRole("option", { name: "depends on" }));
  await user.type(connect.getByLabelText("Attributes"), "protocol: grpc");
  await user.type(connect.getByRole("textbox", { name: "Find an entry" }), "wor");
  await user.click(connect.getByRole("button", { name: "Find" }));
  await user.click(await connect.findByRole("button", { name: "Connect: worker" }));
  await waitFor(() => expect(posted).toEqual({ from: { kind: "service", id: "api" }, to: { kind: "service", id: "w" }, kind: "depends_on",
    description: "", attributes: { protocol: "grpc" } }));
  expect(onChanged).toHaveBeenCalled();
  expect(fetch.mock.calls.filter(call => call[0] === LINKS)).toHaveLength(2);  // reloaded after connecting
});

test("edits a link, shows a conflict, and removes it", async () => {
  const user = userEvent.setup();
  server({ [`GET ${LINKS}`]: () => ({ json: { links: [link] } }), [`GET ${DOCS}`]: () => ({ json: { documents: [] } }),
    ["PUT /api/inventory/links/l1"]: () => ({ status: 409, json: { error: { message: "This connection was changed by someone else. Reload and try again." } } }),
    ["DELETE /api/inventory/links/l1"]: () => ({ status: 204 }) });
  sheet();
  await user.click(await screen.findByRole("button", { name: "Edit connects to → prod-1" }));
  const description = screen.getAllByLabelText("Description")[0];  // the edit form comes before Connect
  await user.clear(description);
  await user.type(description, "replica");
  await user.click(screen.getByRole("button", { name: "Save connection" }));
  expect(await screen.findByText("This connection was changed by someone else. Reload and try again.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Remove connects to → prod-1" }));
  await waitFor(() => expect(screen.queryByText(/changed by someone else/)).toBeNull());
});

test("attaches and detaches documents", async () => {
  const user = userEvent.setup();
  let attached = false;
  server({ [`GET ${LINKS}`]: () => ({ json: { links: [] } }),
    [`GET ${DOCS}`]: () => ({ json: { documents: attached ? [{ id: "a1", entry_kind: "service", entry_id: "api", document_id: "d1", slug: "runbook", title: "Runbook" }] : [] } }),
    ["GET /api/documentation/documents?q=run"]: () => ({ json: { documents: [{ id: "d1", slug: "runbook", title: "Runbook" }] } }),
    ["POST /api/inventory/documents"]: () => { attached = true; return { status: 201, json: { attachment: {} } }; },
    ["DELETE /api/inventory/documents/a1"]: () => { attached = false; return { status: 204 }; } });
  sheet();
  await user.type(await screen.findByRole("textbox", { name: "Find a document" }), "run");
  await user.click(within(screen.getByRole("region", { name: "Documents" })).getByRole("button", { name: "Find" }));
  await user.click(await screen.findByRole("button", { name: "Attach: Runbook" }));
  await user.click(await screen.findByRole("button", { name: "Detach Runbook" }));
  await waitFor(() => expect(screen.getByText("No documents attached.")).toBeInTheDocument());
});
```

The documents search URL must match what `getDocuments(new URLSearchParams({ q }))` in `app/lib/documents.ts` requests;
check that file and adjust the route key in the test, not the component.

- [ ] **Step 5: Run them to see them fail**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/systemMap.test.tsx app/components/__tests__/entrySheet.test.tsx`
Expected: FAIL (`EntrySheet` missing, old page renders layer tabs).

- [ ] **Step 6: Write `app/components/EntrySheet.tsx`**

```tsx
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link as RouterLink } from "react-router";
import { ApiError } from "../lib/api";
import { getDocuments, type DocumentSummary } from "../lib/documents";
import { ENTRY_LABELS, ENTRY_PAGES, LINK_LABELS, attachDocument, createLink, deleteLink, detachDocument, formatAttributes, getAttachments,
  getLinks, linkKindsFrom, parseAttributes, searchEntries, targetKinds, updateLink,
  type Attachment, type EntryHit, type Link, type LinkKind, type MapEntry } from "../lib/inventoryMap";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Textarea } from "~/components/ui/textarea";

const say = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;
/** How a link reads from this entry: "depends on → worker" or "billing → connects to". */
const linkText = (link: Link, entryId: string) => link.from.id === entryId ? `${LINK_LABELS[link.kind]} → ${link.to.name}` : `${link.from.name} → ${LINK_LABELS[link.kind]}`;
const linkNote = (link: Link) => [link.description, ...Object.entries(link.attributes).map(([name, text]) => `${name}: ${text}`)].filter(Boolean).join(" · ");

function LinkFields({ id, description, attributes, onDescription, onAttributes }: { id: string; description: string; attributes: string;
  onDescription: (text: string) => void; onAttributes: (text: string) => void }) {
  const parsed = parseAttributes(attributes);
  const problem = "problem" in parsed ? parsed.problem : null;
  return <FieldGroup>
    <Field><FieldLabel htmlFor={`${id}-description`}>Description</FieldLabel>
      <Textarea id={`${id}-description`} value={description} maxLength={4000} rows={2} onChange={event => onDescription(event.target.value)} /></Field>
    <Field data-invalid={problem !== null || undefined}><FieldLabel htmlFor={`${id}-attributes`}>Attributes</FieldLabel>
      <Textarea id={`${id}-attributes`} value={attributes} rows={3} spellCheck={false} className="font-mono text-xs" placeholder="port: 5432"
        aria-invalid={problem !== null} onChange={event => onAttributes(event.target.value)} />
      {problem ? <FieldError>{problem}</FieldError> : <FieldDescription>Optional; one name: value per line.</FieldDescription>}</Field>
  </FieldGroup>;
}

function EditLink({ link, onSaved, onCancel }: { link: Link; onSaved: () => void; onCancel: () => void }) {
  const [description, setDescription] = useState(link.description);
  const [attributes, setAttributes] = useState(formatAttributes(link.attributes));
  const [error, setError] = useState<string | null>(null);
  const parsed = parseAttributes(attributes);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!("value" in parsed)) return;
    setError(null);
    try { await updateLink(link.id, { description, attributes: parsed.value, revision: link.revision }); onSaved(); }
    catch (reason) { setError(say(reason, "Could not save the connection.")); }
  }
  return <form onSubmit={save} className="flex flex-col gap-2 rounded-md border p-2">
    <LinkFields id={`link-${link.id}`} description={description} attributes={attributes} onDescription={setDescription} onAttributes={setAttributes} />
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="flex gap-2"><Button type="submit" size="sm" disabled={!("value" in parsed)}>Save connection</Button>
      <Button type="button" size="sm" variant="outline" onClick={onCancel}>Cancel</Button></div>
  </form>;
}

function Connect({ entry, onConnected }: { entry: MapEntry; onConnected: () => void }) {
  const kinds = linkKindsFrom(entry.kind);
  const [kind, setKind] = useState<LinkKind>(kinds[0]);
  const [description, setDescription] = useState("");
  const [attributes, setAttributes] = useState("");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<EntryHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const targets = targetKinds(kind);
  const parsed = parseAttributes(attributes);
  async function find(event: FormEvent) {
    event.preventDefault(); setError(null);
    try { setHits((await searchEntries(q.trim(), targets?.length === 1 ? targets[0] : undefined)).entries.filter(hit => hit.entry_id !== entry.id)); }
    catch (reason) { setError(say(reason, "Could not search.")); }
  }
  async function connect(hit: EntryHit) {
    if (!("value" in parsed)) return;
    setError(null);
    try {
      await createLink({ from: { kind: entry.kind, id: entry.id }, to: { kind: hit.entry_kind, id: hit.entry_id }, kind, description, attributes: parsed.value });
      setDescription(""); setAttributes(""); setHits(null); setQ(""); onConnected();
    } catch (reason) { setError(say(reason, "Could not connect.")); }
  }
  return <section aria-label="Connect" className="flex flex-col gap-2">
    <h3 className="text-sm font-medium">Connect to…</h3>
    <Select value={kind} onValueChange={value => { setKind(value as LinkKind); setHits(null); }}>
      <SelectTrigger aria-label="Connection kind" className="w-full"><SelectValue /></SelectTrigger>
      <SelectContent><SelectGroup>{kinds.map(item => <SelectItem key={item} value={item}>{LINK_LABELS[item]}</SelectItem>)}</SelectGroup></SelectContent>
    </Select>
    <LinkFields id="connect" description={description} attributes={attributes} onDescription={setDescription} onAttributes={setAttributes} />
    <form onSubmit={find} className="flex gap-2">
      <Input aria-label="Find an entry" value={q} placeholder={targets ? `Find a ${ENTRY_LABELS[targets[0]]}` : "Find by name"} onChange={event => setQ(event.target.value)} />
      <Button type="submit" variant="outline">Find</Button></form>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {hits && (hits.length === 0 ? <p className="text-muted-foreground text-sm">Nothing found.</p>
      : <ul className="flex flex-col gap-1">{hits.map(hit => <li key={`${hit.entry_kind}:${hit.entry_id}`} className="flex items-center justify-between gap-2 text-sm">
        <span>{hit.name} <span className="text-muted-foreground">· {ENTRY_LABELS[hit.entry_kind]}</span></span>
        <Button type="button" size="sm" variant="outline" disabled={!("value" in parsed)} aria-label={`Connect: ${hit.name}`} onClick={() => void connect(hit)}>Connect</Button>
      </li>)}</ul>)}
  </section>;
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

export default function EntrySheet({ entry, onClose, onChanged }: { entry: MapEntry; onClose: () => void; onChanged: () => void }) {
  const [links, setLinks] = useState<Link[] | null>(null);
  const [documents, setDocuments] = useState<Attachment[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const [linked, attached] = await Promise.all([getLinks(entry), getAttachments(entry)]);
      setLinks(linked.links); setDocuments(attached.documents);
    } catch (reason) { setError(say(reason, "Could not load the entry.")); }
  }, [entry.kind, entry.id]);
  useEffect(() => { void load(); }, [load]);
  async function act(work: () => Promise<unknown>, fallback: string) {
    setError(null);
    try { await work(); setEditing(null); onChanged(); await load(); }
    catch (reason) { setError(say(reason, fallback)); }
  }
  const page = ENTRY_PAGES[entry.kind];
  return <Sheet open onOpenChange={next => { if (!next) onClose(); }}>
    <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
      <SheetHeader><SheetTitle>{entry.name}</SheetTitle>
        <SheetDescription>{ENTRY_LABELS[entry.kind]} · <RouterLink to={page.page} className="underline">Open in {page.label}</RouterLink></SheetDescription></SheetHeader>
      <div className="flex flex-col gap-6 px-4 pb-4">
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <section aria-label="Connections" className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Connections</h3>
          {links === null ? <Spinner /> : links.length === 0 ? <p className="text-muted-foreground text-sm">No connections yet.</p>
            : <ul className="flex flex-col gap-2 text-sm">{links.map(link => { const text = linkText(link, entry.id); return <li key={link.id} className="flex flex-col gap-1">
              <div className="flex items-center justify-between gap-2"><span>{text}</span>
                <span className="flex gap-1">
                  <Button type="button" size="sm" variant="ghost" aria-label={`Edit ${text}`} onClick={() => setEditing(link.id)}>Edit</Button>
                  <Button type="button" size="sm" variant="ghost" aria-label={`Remove ${text}`} onClick={() => void act(() => deleteLink(link.id), "Could not remove the connection.")}>Remove</Button>
                </span></div>
              {linkNote(link) && <span className="text-muted-foreground text-xs">{linkNote(link)}</span>}
              {editing === link.id && <EditLink link={link} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); onChanged(); void load(); }} />}
            </li>; })}</ul>}
        </section>
        <Connect entry={entry} onConnected={() => { onChanged(); void load(); }} />
        <section aria-label="Documents" className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Documents</h3>
          {documents.length === 0 ? <p className="text-muted-foreground text-sm">No documents attached.</p> : <ul className="flex flex-col gap-1 text-sm">
            {documents.map(document => <li key={document.id} className="flex items-center justify-between gap-2">
              <RouterLink to={`/documents/${document.document_id}`} className="underline">{document.title}</RouterLink>
              <Button type="button" size="sm" variant="ghost" aria-label={`Detach ${document.title}`}
                onClick={() => void act(() => detachDocument(document.id), "Could not detach the document.")}>Detach</Button></li>)}
          </ul>}
          <DocumentPicker attached={documents.map(document => document.document_id)}
            onPick={document => void act(() => attachDocument(entry, document.id), "Could not attach the document.")} />
        </section>
      </div>
    </SheetContent>
  </Sheet>;
}
```

The EditLink conflict test expects the error to disappear when the link is removed: `act` clears `error` and closes the
editor, which unmounts `EditLink` and its message.

- [ ] **Step 7: Rewrite `app/components/SystemMapView.tsx`**

Keep the existing structure (state from the URL, two effects that load the JSON view and the drawn view, `SourceText`,
the Diagram / Mermaid / D2 / JSON tabs, depth and layout selects, Inside / Outside asides) and change:

1. Imports from `../lib/inventoryMap` instead of `../lib/systemGraph`: `ENTRY_LABELS, MAPS, MAP_LABELS, LAYOUTS,
   MAX_DEPTH, VIEWS, VIEW_LABELS, getView, getViewText, mapQuery, readMapState` and types `MapGraph, MapName, Layout,
   MapState, MapView, MapEntry`; import `EntrySheet` instead of `NodeSheet`; drop the `Plus` icon.
2. `const { map, root, depth, view: mode, layout } = state;` and every `layer` use becomes `map`; effects depend on
   `map`.
3. `const [entry, setEntry] = useState<MapEntry | null>(null);` replaces `sheet`; clicking a name in Inside or Outside
   calls `setEntry({ kind: node.entry_kind, id: node.entry_id, name: node.name })`.
4. The top bar: `<Tabs value={map} onValueChange={value => go({ map: value as MapName, root: null })}>` with
   `<TabsList aria-label="Map">{MAPS.map(item => <TabsTrigger key={item} value={item}>{MAP_LABELS[item]}</TabsTrigger>)}</TabsList>`;
   no New node button; breadcrumb root button text `MAP_LABELS[map]`.
5. Empty: `<p className="text-muted-foreground text-sm">Nothing here yet. Add environments, servers, services and code projects on their pages, or connect entries here.</p>`.
6. Diagram `alt={`${MAP_LABELS[map]} map`}`.
7. Inside caption: `${ENTRY_LABELS[node.entry_kind]}${node.child_count > 0 ? ` · +${node.child_count} inside` : ""}`.
   Outside caption: `${ENTRY_LABELS[node.entry_kind]} · ${count} ${count === 1 ? "link" : "links"}`; its arrow does
   `go({ root: node.id })`.
8. Render `{entry && <EntrySheet entry={entry} onClose={() => setEntry(null)} onChanged={() => setVersion(value => value + 1)} />}`.
9. The breadcrumb stays visible when the view fails to load (it renders from `graph?.breadcrumb`, and its root button
   is always there).

Delete `app/lib/systemGraph.ts`, `app/components/NodeSheet.tsx`, `app/components/__tests__/nodeSheet.test.tsx`;
`grep -rn "systemGraph\|NodeSheet" services/admin-ui/app` prints nothing.

- [ ] **Step 8: Run the UI tests and type check**

Run: `cd services/admin-ui && npx vitest run && npx tsc --noEmit`
Expected: all tests PASS, no type errors.

- [ ] **Step 9: Commit**

```bash
git add -A services/admin-ui
git commit -m "feat(admin-ui): System map shows the inventory and connects entries; documents attach to entries

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Docs

**Files:**
- Modify: `docs/user-guide.md` (§4.20)
- Modify: `docs/superpowers/specs/2026-10-10-system-graph-design.md`,
  `docs/superpowers/specs/2026-10-10-graph-inventory-sync-design.md` (first lines)
- Test: `services/admin-api/tests/test_docs.py` if it exists and checks the guide (run it); otherwise none.

- [ ] **Step 1: Replace §4.20 of `docs/user-guide.md`**

Replace everything from `### 4.20 System map` up to the `---` before `## 5. End-to-end workflows` with:

```markdown
### 4.20 System map

Environments → **System map** draws your inventory — environments, servers, services and code projects — and the
connections between them. Nothing is drawn by hand: add entries on their own pages, and connect them here.

- **Infrastructure.** Each environment holds its servers (the servers you added to it on the Environments page, and
  any server with a deployment there), and each server holds the services deployed on it in that environment. A server
  in two environments appears in both. A server in no environment, or with a deployment that has no environment, sits
  at the top level. A service that belongs to an environment but is not deployed there sits directly in the
  environment. Lines are *connects to* connections.
- **Services.** Every service and code project, with *depends on*, *built from* and *connects to* lines. Archived code
  projects are left out.
- **Browsing.** **Inside** lists what the current box holds (→ opens it; "+N inside" says it holds more), **Outside**
  lists what its connections reach elsewhere. The breadcrumb goes back up. **Depth** shows up to 3 levels; deeper
  entries fold into their box and lines add up (`connects to ×3`). A connection is drawn inside one environment: it
  never joins one environment's copy of a server to another's. Everything you choose is in the page address.
- **Views.** **Diagram** is drawn by D2 with the layout you pick (tala, elk or dagre); **Mermaid** draws the same view
  with Mermaid; **D2** and **JSON** show the source, with **Copy**.
- **Connections.** Click an entry's name: the panel shows its connections and documents and links to its page.
  **Connect to…** picks a kind — *depends on* (service → service), *built from* (service → code project) or
  *connects to* (anything → anything) — an optional description and attributes (one `name: value` per line, at most
  20), then the target by name. **Edit** changes the description and attributes; to change the ends or the kind,
  remove the connection and add it again. The Services page edits the same *depends on* and *built from*
  connections; an open Services form that someone changed meanwhile asks you to reload.
- **Documents.** Attach documents from Docs → Documents to any entry; detaching never deletes the document. Deleting an
  entry removes its connections and attachments.
- **Upgrading from the stored graph.** Service dependencies and code project links become connections. Documents
  attached to graph nodes move to the server, environment, service or code project behind the node. Nodes you drew by
  hand (and their attachments) are gone; the documents themselves stay in Docs → Documents.
```

- [ ] **Step 2: Mark the old specs superseded**

Insert as the second line of each of the two old spec files (after the title line):

```markdown
> **Superseded** by `2026-10-10-inventory-map-design.md`: the stored graph was replaced by a map computed from the inventory (migration 0095).
```

- [ ] **Step 3: Run doc checks and the full suites**

Run: `cd services/admin-api && uv run pytest -q` and `cd services/admin-ui && npx vitest run && npx tsc --noEmit`
Expected: no failures beyond the known ones listed in Task 2 Step 9.
Run: `grep -rn "System map\|kg_\|graph_sync" docs/user-guide.md README.md 2>/dev/null` and fix any remaining sentence
that describes layers, hand-made nodes or the graph sync.

- [ ] **Step 4: Commit**

```bash
git add docs
git commit -m "docs: System map from the inventory, connections and attached documents

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
