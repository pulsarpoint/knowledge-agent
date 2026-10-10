# System graph inventory sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Servers, services (with their deployments), environments and code projects appear in the system graph
automatically, in the same transaction as the inventory write, linked to their rows and read-only where the inventory
owns them.

**Architecture:** Migration 0093 adds typed link columns to `ka.kg_nodes`, a `synced` flag and the `includes` type to
`ka.kg_edges`, and PL/pgSQL sync functions fired by triggers on the inventory tables (`AFTER INSERT/UPDATE`, `BEFORE
DELETE` so the link is still there), plus a backfill. The graph API reports each node's `source` and each edge's
`synced`, and refuses changes the inventory owns. The System map shows where synced nodes come from and hides actions
that do not apply.

**Tech Stack:** PostgreSQL 17 (PL/pgSQL, golang-migrate SQL), FastAPI + psycopg3, React Router 7 + shadcn/ui, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-10-graph-inventory-sync-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/graph-inventory-sync` (branch `feature/graph-inventory-sync`). Never run
  make targets; never touch `.env`, `.local` or the running Docker stack's database.
- Never `git stash`; never `git add .superpowers`. Every commit ends with the trailer
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd services/admin-api && uv run pytest -q tests/<file>`. UI: `cd services/admin-ui && npx vitest run
  <file>` plus `npx react-router typegen && npx tsc --noEmit`. If `services/admin-ui/node_modules` is missing, run
  `pnpm install --frozen-lockfile` there first. The 5 code-graph tests (`test_code_graph_sdk.py`,
  `test_code_graphs_config.py::test_a_generated_graph_answers_code_queries_end_to_end`,
  `test_code_processing.py::test_go_module_root_resolves_package_function_instead_of_same_named_method`) fail in a fresh
  worktree because `services/code-graph/node_modules` is absent — ignore them; known failures also on main:
  `test_deploy.py::test_docs_commands_serve_the_docs_folder_inside_the_data_root`,
  `test_environments.py::test_migration_reapply_and_populated_rollback_protection`.
- Link columns and what they mean (exact):

  | column | table | layer | kind | page | label |
  |---|---|---|---|---|---|
  | `server_id` | `servers` | `infrastructure` | `server` | `/servers` | `Servers` |
  | `deployment_id` | `service_deployments` | `infrastructure` | `instance` | `/services` | `Services` |
  | `environment_id` | `environments` | `infrastructure` | `environment` | `/environments` | `Environments` |
  | `service_id` | `services` | `software` | `service` | `/services` | `Services` |
  | `repository_id` | `repositories` | `software` | `repository` | `/code-projects` | `Code projects` |

- Deployment node name: `<runtime_name, or the service name when runtime_name is blank> · <environment label>` (the
  separator is ` · `, U+00B7 with spaces).
- Name clash rule: first free (case-insensitive, among siblings in the same layer) of `<name>`, `<name> (2)`,
  `<name> (3)`, …; names cut to 160 characters including the suffix.
- Delete note (appended on a new line; description cut from the front to 4000 characters):
  `Removed from <Servers | Services | Services (deployment) | Environments | Code projects> on <YYYY-MM-DD>.`
- API refusal messages (409, exact): `This node comes from <label>. Change it there.` ·
  `This node comes from <label>. Its place follows the inventory.` · `This node comes from <label>. Delete it there.` ·
  `This edge comes from the inventory. Change it in <label>.` (label of the edge's from-node source).
- Edge types gain `includes` (environment → server / service).

## Review Focus

1. Saving a service in Services with deployments, dependencies, code projects and environments must still succeed when
   a hand-made graph node already uses the same name — the synced node takes `(2)`, never an error (Task 1
   `test_clash_takes_a_suffix`, Task 2 `test_services_page_saves_sync_the_graph`).
2. Deleting an inventory row whose node has a hand-made child or an attached document keeps the node, unlinked, with the
   note; nothing hand-made disappears (Task 1 `test_delete_keeps_nodes_with_children_or_documents`, Task 2
   `test_deleting_a_service_keeps_hand_made_children`).
3. Older migrations' down files and the migration tests that replay them must keep working with 0093 applied: tests that
   step down past 0093 must run 0093's down first (Task 1 Step 5).
4. A synced node can still get a description, properties, children, hand-made edges and documents (Task 2
   `test_synced_nodes_refuse_only_what_the_inventory_owns`).
5. Renaming an environment renames its deployments' nodes; changing a deployment's server moves its node (Task 1
   `test_deployments_follow_their_server_and_environment`).

## File Structure

- `db/migrations/0093_graph_inventory_sync.{up,down}.sql` — columns, checks, indexes, sync functions, triggers, backfill.
- `db/tests/run.sh` — 0093 down/up checks.
- `services/admin-api/tests/test_graph_inventory_sync_migration.py` — trigger behaviour at SQL level.
- `services/admin-api/src/knowledge_admin/system_graph.py` — `includes` type; `source_kind` and `synced` in views.
- `services/admin-api/src/knowledge_admin/system_graph_api.py` — `source`, `synced`, refusals.
- `services/admin-api/tests/test_system_graph.py`, `test_system_graph_api.py` — updated expectations; new tests in
  `test_graph_inventory_sync_api.py` (through the inventory APIs).
- `services/admin-ui/app/lib/systemGraph.ts`, `components/NodeSheet.tsx`, `components/SystemMapView.tsx` + tests.
- Docs: `docs/migrations.md`, `deploy/README.md` (Task 1); `docs/user-guide.md` (Task 3).

---

### Task 1: Migration 0093 — links, sync triggers, backfill

**Files:**
- Create: `db/migrations/0093_graph_inventory_sync.up.sql`, `db/migrations/0093_graph_inventory_sync.down.sql`
- Create: `services/admin-api/tests/test_graph_inventory_sync_migration.py`
- Modify: `db/tests/run.sh`, `docs/migrations.md`, `deploy/README.md`; any existing migration test that replays down
  files past 0093 (Step 5)

**Interfaces:**
- Produces: columns `ka.kg_nodes.{server_id, deployment_id, environment_id, service_id, repository_id}` (uuid, nullable);
  `ka.kg_edges.synced boolean`; edge type `includes`; SQL functions `ka.kg_node_for(link text, row_id uuid) → uuid`,
  `ka.kg_sync_row(link text, row_id uuid) → uuid` (creates/updates the node for an inventory row and returns its id,
  NULL when the row does not exist).

- [ ] **Step 1: Write the failing test** `services/admin-api/tests/test_graph_inventory_sync_migration.py`

```python
"""0093: inventory rows keep linked graph nodes and synced edges, in the same transaction."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def server(conn, project, name='prod-1') -> dict:
    return conn.execute("""INSERT INTO ka.servers(project_id,name,hostname) VALUES (%s,%s,%s) RETURNING *""",
                        (project, name, f'{uuid4().hex[:8]}.internal')).fetchone()


def service(conn, project, name='admin-api') -> dict:
    return conn.execute('INSERT INTO ka.services(project_id,name) VALUES (%s,%s) RETURNING *', (project, name)).fetchone()


def environment(conn, project, name='production') -> dict:
    return conn.execute('INSERT INTO ka.environments(project_id,name) VALUES (%s,%s) RETURNING *', (project, name)).fetchone()


def deployment(conn, project, service_id, server_id, environment='production', runtime_name='') -> dict:
    return conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name)
        VALUES (%s,%s,%s,%s,%s) RETURNING *""", (project, service_id, server_id, environment, runtime_name)).fetchone()


def node(conn, link, row_id) -> dict | None:
    return conn.execute(f'SELECT * FROM ka.kg_nodes WHERE {link}=%s', (row_id,)).fetchone()


def edges(conn) -> set[tuple]:
    rows = conn.execute("""SELECT f.name AS a,t.name AS b,e.type,e.synced FROM ka.kg_edges e
        JOIN ka.kg_nodes f ON f.id=e.from_id JOIN ka.kg_nodes t ON t.id=e.to_id""").fetchall()
    return {(row['a'], row['b'], row['type'], row['synced']) for row in rows}


def test_rows_get_linked_nodes(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host, api, prod = server(conn, project), service(conn, project), environment(conn, project)
        repo_id = store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', '')
        for link, row_id, layer, kind, name in [('server_id', host['id'], 'infrastructure', 'server', 'prod-1'),
                                                ('service_id', api['id'], 'software', 'service', 'admin-api'),
                                                ('environment_id', prod['id'], 'infrastructure', 'environment', 'production')]:
            found = node(conn, link, row_id)
            assert (found['layer'], found['kind'], found['name'], found['parent_id']) == (layer, kind, name, None)
        repo = node(conn, 'repository_id', repo_id)
        assert (repo['layer'], repo['kind'], repo['parent_id']) == ('software', 'repository', None)


def test_renames_follow_the_inventory(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host = server(conn, project)
        conn.execute("UPDATE ka.servers SET name='prod-01' WHERE id=%s", (host['id'],))
        renamed = node(conn, 'server_id', host['id'])
        assert (renamed['name'], renamed['revision']) == ('prod-01', 2)
        conn.execute("UPDATE ka.servers SET hostname='other.internal' WHERE id=%s", (host['id'],))
        assert node(conn, 'server_id', host['id'])['revision'] == 2  # only name changes touch the node


def test_deployments_follow_their_server_and_environment(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, one, two = service(conn, project), server(conn, project, 'prod-1'), server(conn, project, 'prod-2')
        made = deployment(conn, project, api['id'], one['id'])
        instance = node(conn, 'deployment_id', made['id'])
        assert (instance['layer'], instance['kind'], instance['name'], instance['parent_id']) == (
            'infrastructure', 'instance', 'admin-api · production', node(conn, 'server_id', one['id'])['id'])
        assert ('admin-api · production', 'admin-api', 'instance_of', True) in edges(conn)
        conn.execute("UPDATE ka.service_deployments SET server_id=%s WHERE id=%s", (two['id'], made['id']))
        assert node(conn, 'deployment_id', made['id'])['parent_id'] == node(conn, 'server_id', two['id'])['id']
        conn.execute("UPDATE ka.service_deployments SET environment='staging', runtime_name='api-blue' WHERE id=%s", (made['id'],))
        assert node(conn, 'deployment_id', made['id'])['name'] == 'api-blue · staging'
        conn.execute("UPDATE ka.service_deployments SET runtime_name='' WHERE id=%s", (made['id'],))
        conn.execute("UPDATE ka.services SET name='admin' WHERE id=%s", (api['id'],))
        assert node(conn, 'deployment_id', made['id'])['name'] == 'admin · staging'  # service renames reach deployments


def test_relation_tables_make_synced_edges(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, worker = service(conn, project, 'admin-api'), service(conn, project, 'worker')
        host, prod = server(conn, project), environment(conn, project)
        repo_id = store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', '')
        repo_name = node(conn, 'repository_id', repo_id)['name']
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)',
                     (project, api['id'], worker['id']))
        conn.execute('INSERT INTO ka.service_repositories(project_id,service_id,repository_id) VALUES (%s,%s,%s)',
                     (project, api['id'], repo_id))
        conn.execute('INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                     (project, prod['id'], host['id']))
        conn.execute('INSERT INTO ka.environment_services(project_id,environment_id,service_id) VALUES (%s,%s,%s)',
                     (project, prod['id'], api['id']))
        assert edges(conn) == {('admin-api', 'worker', 'depends_on', True), ('admin-api', repo_name, 'uses', True),
                               ('production', 'prod-1', 'includes', True), ('production', 'admin-api', 'includes', True)}
        conn.execute('DELETE FROM ka.service_dependencies WHERE service_id=%s', (api['id'],))
        conn.execute('DELETE FROM ka.environment_servers WHERE server_id=%s', (host['id'],))
        assert edges(conn) == {('admin-api', repo_name, 'uses', True), ('production', 'admin-api', 'includes', True)}
        conn.execute('DELETE FROM ka.environments WHERE id=%s', (prod['id'],))  # cascades its memberships
        assert edges(conn) == {('admin-api', repo_name, 'uses', True)} and node(conn, 'environment_id', prod['id']) is None


def test_hand_made_edges_are_left_alone(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, worker = service(conn, project, 'admin-api'), service(conn, project, 'worker')
        a, w = node(conn, 'service_id', api['id'])['id'], node(conn, 'service_id', worker['id'])['id']
        conn.execute("INSERT INTO ka.kg_edges(project_id,from_id,to_id,type) VALUES (%s,%s,%s,'depends_on')", (project, a, w))
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)',
                     (project, api['id'], worker['id']))
        conn.execute('DELETE FROM ka.service_dependencies WHERE service_id=%s', (api['id'],))
        assert edges(conn) == {('admin-api', 'worker', 'depends_on', False)}


def test_clash_takes_a_suffix(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'infrastructure','server','prod-1'),
            (%s,'infrastructure','server','prod-1 (2)')""", (project, project))
        host = server(conn, project, 'PROD-1')
        assert node(conn, 'server_id', host['id'])['name'] == 'PROD-1 (3)'
        conn.execute("UPDATE ka.servers SET name='unique' WHERE id=%s", (host['id'],))
        assert node(conn, 'server_id', host['id'])['name'] == 'unique'
        long_name = 'x' * 160
        conn.execute("INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'software','service',%s)", (project, long_name))
        assert len(node(conn, 'service_id', service(conn, project, long_name)['id'])['name']) == 160


def test_delete_removes_bare_nodes(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
        made = deployment(conn, project, api['id'], host['id'])
        conn.execute('DELETE FROM ka.service_deployments WHERE id=%s', (made['id'],))
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))
        conn.execute('DELETE FROM ka.services WHERE id=%s', (api['id'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_edges').fetchone()['n'] == 0


def test_delete_keeps_nodes_with_children_or_documents(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    made = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'runbook', 'title': 'Runbook', 'markdown': '# Runbook\n'})
    assert made.status_code == 201, made.text
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
        host_node = node(conn, 'server_id', host['id'])
        conn.execute("UPDATE ka.kg_nodes SET description='Main host' WHERE id=%s", (host_node['id'],))
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id)
            VALUES (%s,'infrastructure','disk','data disk',%s)""", (project, host_node['id']))
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))
        kept = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (host_node['id'],)).fetchone()
        assert kept['server_id'] is None
        assert kept['description'].startswith('Main host\nRemoved from Servers on ')
        api_node = node(conn, 'service_id', api['id'])
        conn.execute('INSERT INTO ka.kg_node_documents(node_id,document_id,project_id) VALUES (%s,%s,%s)',
                     (api_node['id'], made.json()['id'], project))
        conn.execute('DELETE FROM ka.services WHERE id=%s', (api['id'],))
        kept = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (api_node['id'],)).fetchone()
        assert kept['service_id'] is None and kept['description'].startswith('Removed from Services on ')


def test_backfill_and_down_up(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        run(conn, '0093_graph_inventory_sync.down.sql')
        api, host, prod = service(conn, project), server(conn, project), environment(conn, project)
        deployment(conn, project, api['id'], host['id'])
        conn.execute('INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                     (project, prod['id'], host['id']))
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'software','service','admin-api')""", (project,))
        run(conn, '0093_graph_inventory_sync.up.sql')
        assert node(conn, 'service_id', api['id'])['name'] == 'admin-api (2)'
        assert node(conn, 'deployment_id', conn.execute('SELECT id FROM ka.service_deployments').fetchone()['id'])['parent_id'] == \
            node(conn, 'server_id', host['id'])['id']
        assert ('production', 'prod-1', 'includes', True) in edges(conn)
        run(conn, '0093_graph_inventory_sync.down.sql')
        names = {row['name'] for row in conn.execute('SELECT name FROM ka.kg_nodes').fetchall()}
        assert names == {'admin-api'}  # synced nodes with nothing attached are gone, the hand-made one stays
        assert conn.execute("SELECT count(*) AS n FROM information_schema.columns WHERE table_schema='ka' "
                            "AND table_name='kg_nodes' AND column_name='server_id'").fetchone()['n'] == 0
        run(conn, '0093_graph_inventory_sync.up.sql')


def test_link_rules(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
    for sql, values, error in [
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id) VALUES (%s,'software','x','x',%s)",
         (project, host['id']), psycopg.errors.CheckViolation),                       # layer must match the link
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id) VALUES (%s,'infrastructure','x','y',%s)",
         (project, host['id']), psycopg.errors.UniqueViolation),                      # one node per row
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id,service_id) VALUES (%s,'infrastructure','x','z',%s,%s)",
         (project, uuid4(), api['id']), psycopg.errors.CheckViolation)]:              # one link per node
        with pytest.raises(error), store.connect() as conn:
            conn.execute(sql, values)
    with store.connect() as conn:
        includes = conn.execute("""SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint
            WHERE conname='kg_edges_type' AND conrelid='ka.kg_edges'::regclass""").fetchone()['d']
        assert "'includes'" in includes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_inventory_sync_migration.py`
Expected: FAIL (no `server_id` column / no node found).

- [ ] **Step 3: Write the migration** `db/migrations/0093_graph_inventory_sync.up.sql`

```sql
BEGIN;
SET search_path = ka, public;

-- The inventory (Servers, Services with their deployments, Environments, Code projects) is the source of truth; graph
-- nodes link to their rows and triggers keep the graph in step, in the writer's transaction.
ALTER TABLE kg_nodes
    ADD COLUMN server_id uuid,
    ADD COLUMN deployment_id uuid,
    ADD COLUMN environment_id uuid,
    ADD COLUMN service_id uuid,
    ADD COLUMN repository_id uuid,
    ADD CONSTRAINT kg_nodes_server FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE SET NULL (server_id),
    ADD CONSTRAINT kg_nodes_deployment FOREIGN KEY (deployment_id, project_id) REFERENCES service_deployments(id, project_id) ON DELETE SET NULL (deployment_id),
    ADD CONSTRAINT kg_nodes_environment FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE SET NULL (environment_id),
    ADD CONSTRAINT kg_nodes_service FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id) ON DELETE SET NULL (service_id),
    ADD CONSTRAINT kg_nodes_repository FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id) ON DELETE SET NULL (repository_id),
    ADD CONSTRAINT kg_nodes_one_source CHECK (num_nonnulls(server_id, deployment_id, environment_id, service_id, repository_id) <= 1),
    ADD CONSTRAINT kg_nodes_source_layer CHECK (
        (server_id IS NULL AND deployment_id IS NULL AND environment_id IS NULL OR layer = 'infrastructure')
        AND (service_id IS NULL AND repository_id IS NULL OR layer = 'software'));
CREATE UNIQUE INDEX kg_nodes_by_server ON kg_nodes (server_id) WHERE server_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_deployment ON kg_nodes (deployment_id) WHERE deployment_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_environment ON kg_nodes (environment_id) WHERE environment_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_service ON kg_nodes (service_id) WHERE service_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_repository ON kg_nodes (repository_id) WHERE repository_id IS NOT NULL;
COMMENT ON COLUMN kg_nodes.server_id IS 'Synced from Servers: name, kind, place and existence follow the row.';

ALTER TABLE kg_edges
    ADD COLUMN synced boolean NOT NULL DEFAULT false,
    DROP CONSTRAINT kg_edges_type,
    ADD CONSTRAINT kg_edges_type CHECK (type IN ('calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes',
        'subscribes', 'instance_of', 'runs_on', 'implements', 'stores', 'includes'));
COMMENT ON COLUMN kg_edges.synced IS 'Made by the inventory sync (dependencies, code projects, environment members, deployments); read-only in the graph.';

-- The node linked to an inventory row (link = one of the five column names).
CREATE FUNCTION kg_node_for(link text, row_id uuid) RETURNS uuid LANGUAGE plpgsql STABLE SET search_path = ka, pg_temp AS $$
DECLARE found uuid;
BEGIN
    IF link NOT IN ('server_id', 'deployment_id', 'environment_id', 'service_id', 'repository_id') THEN
        RAISE EXCEPTION 'unknown graph link %', link;
    END IF;
    EXECUTE format('SELECT id FROM kg_nodes WHERE %I = $1', link) INTO found USING row_id;
    RETURN found;
END $$;

-- The first free sibling name: name, name (2), name (3), … (case-insensitive, at most 160 characters).
CREATE FUNCTION kg_free_name(project uuid, the_layer text, parent uuid, wanted text, self uuid) RETURNS text
    LANGUAGE plpgsql STABLE SET search_path = ka, pg_temp AS $$
DECLARE
    candidate text := left(btrim(wanted), 160);
    n integer := 1;
BEGIN
    WHILE EXISTS (SELECT 1 FROM kg_nodes WHERE project_id = project AND layer = the_layer
            AND parent_id IS NOT DISTINCT FROM parent AND lower(name) = lower(candidate) AND id IS DISTINCT FROM self) LOOP
        n := n + 1;
        candidate := left(btrim(wanted), 160 - length(' (' || n || ')')) || ' (' || n || ')';
    END LOOP;
    RETURN candidate;
END $$;

CREATE FUNCTION kg_sync_edge(project uuid, from_node uuid, to_node uuid, edge_type text) RETURNS void
    LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    IF from_node IS NULL OR to_node IS NULL OR from_node = to_node THEN
        RETURN;
    END IF;
    -- A hand-made edge with the same ends and type stays hand-made.
    INSERT INTO kg_edges(project_id, from_id, to_id, type, synced) VALUES (project, from_node, to_node, edge_type, true)
        ON CONFLICT ON CONSTRAINT kg_edges_once DO NOTHING;
END $$;

CREATE FUNCTION kg_unsync_edge(from_node uuid, to_node uuid, edge_type text) RETURNS void
    LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
BEGIN
    DELETE FROM kg_edges WHERE synced AND from_id = from_node AND to_id = to_node AND type = edge_type;
END $$;

-- Creates or updates the node of one inventory row and returns its id (NULL when the row does not exist).
CREATE FUNCTION kg_sync_row(link text, row_id uuid) RETURNS uuid LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    project uuid;
    wanted text;
    the_layer text;
    the_kind text;
    parent uuid;
    server uuid;
    owner uuid;
    found uuid;
    current_name text;
    current_parent uuid;
    final_name text;
BEGIN
    CASE link
        WHEN 'server_id' THEN
            SELECT s.project_id, s.name INTO project, wanted FROM servers s WHERE s.id = row_id;
            the_layer := 'infrastructure'; the_kind := 'server';
        WHEN 'environment_id' THEN
            SELECT e.project_id, e.name INTO project, wanted FROM environments e WHERE e.id = row_id;
            the_layer := 'infrastructure'; the_kind := 'environment';
        WHEN 'service_id' THEN
            SELECT s.project_id, s.name INTO project, wanted FROM services s WHERE s.id = row_id;
            the_layer := 'software'; the_kind := 'service';
        WHEN 'repository_id' THEN
            SELECT r.project_id, r.name INTO project, wanted FROM repositories r WHERE r.id = row_id;
            the_layer := 'software'; the_kind := 'repository';
        WHEN 'deployment_id' THEN
            SELECT d.project_id, coalesce(nullif(btrim(d.runtime_name), ''), s.name) || ' · ' || d.environment,
                   d.server_id, d.service_id
                INTO project, wanted, server, owner
                FROM service_deployments d JOIN services s ON s.id = d.service_id WHERE d.id = row_id;
            the_layer := 'infrastructure'; the_kind := 'instance';
        ELSE
            RAISE EXCEPTION 'unknown graph link %', link;
    END CASE;
    IF project IS NULL THEN
        RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(project::text));
    IF link = 'deployment_id' THEN
        parent := kg_sync_row('server_id', server);
    END IF;
    found := kg_node_for(link, row_id);
    IF found IS NULL THEN
        EXECUTE format('INSERT INTO kg_nodes(project_id, layer, kind, name, parent_id, %I) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id', link)
            INTO found USING project, the_layer, the_kind, kg_free_name(project, the_layer, parent, wanted, NULL), parent, row_id;
    ELSE
        SELECT n.name, n.parent_id INTO current_name, current_parent FROM kg_nodes n WHERE n.id = found;
        final_name := kg_free_name(project, the_layer, parent, wanted, found);
        IF current_name IS DISTINCT FROM final_name OR current_parent IS DISTINCT FROM parent THEN
            UPDATE kg_nodes SET name = final_name, parent_id = parent, revision = revision + 1 WHERE id = found;
        END IF;
    END IF;
    IF link = 'deployment_id' THEN
        DELETE FROM kg_edges WHERE synced AND from_id = found AND type = 'instance_of'
            AND to_id IS DISTINCT FROM kg_node_for('service_id', owner);
        PERFORM kg_sync_edge(project, found, kg_sync_row('service_id', owner), 'instance_of');
    END IF;
    RETURN found;
END $$;

-- A linked row is going away: its synced edges go; its node goes too unless something hand-made hangs off it, in which
-- case the node stays, unlinked, with a note.
CREATE FUNCTION kg_sync_release(link text, row_id uuid, label text) RETURNS void
    LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    found uuid := kg_node_for(link, row_id);
    project uuid;
BEGIN
    IF found IS NULL THEN
        RETURN;
    END IF;
    SELECT n.project_id INTO project FROM kg_nodes n WHERE n.id = found;
    PERFORM pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(project::text));
    DELETE FROM kg_edges WHERE synced AND (from_id = found OR to_id = found);
    IF EXISTS (SELECT 1 FROM kg_nodes WHERE parent_id = found) OR EXISTS (SELECT 1 FROM kg_node_documents WHERE node_id = found) THEN
        EXECUTE format($sql$UPDATE kg_nodes SET %I = NULL, revision = revision + 1,
            description = right(CASE WHEN description = '' THEN '' ELSE description || E'\n' END
                || 'Removed from ' || $2 || ' on ' || to_char(now(), 'YYYY-MM-DD') || '.', 4000)
            WHERE id = $1$sql$, link) USING found, label;
    ELSE
        DELETE FROM kg_nodes WHERE id = found;
    END IF;
END $$;

-- Row triggers on the five inventory tables. TG_ARGV: link column, delete label.
CREATE FUNCTION kg_sync_inventory() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    link text := TG_ARGV[0];
    deployment record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM kg_sync_release(link, OLD.id, TG_ARGV[1]);
        RETURN OLD;
    END IF;
    PERFORM kg_sync_row(link, NEW.id);
    IF link = 'service_id' AND TG_OP = 'UPDATE' THEN  -- deployment names use the service name
        FOR deployment IN SELECT id FROM service_deployments WHERE service_id = NEW.id LOOP
            PERFORM kg_sync_row('deployment_id', deployment.id);
        END LOOP;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER kg_sync AFTER INSERT OR UPDATE OF name ON servers FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('server_id', 'Servers');
CREATE TRIGGER kg_sync_release BEFORE DELETE ON servers FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('server_id', 'Servers');
CREATE TRIGGER kg_sync AFTER INSERT OR UPDATE OF name ON environments FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('environment_id', 'Environments');
CREATE TRIGGER kg_sync_release BEFORE DELETE ON environments FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('environment_id', 'Environments');
CREATE TRIGGER kg_sync AFTER INSERT OR UPDATE OF name ON services FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('service_id', 'Services');
CREATE TRIGGER kg_sync_release BEFORE DELETE ON services FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('service_id', 'Services');
CREATE TRIGGER kg_sync AFTER INSERT OR UPDATE OF name ON repositories FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('repository_id', 'Code projects');
CREATE TRIGGER kg_sync_release BEFORE DELETE ON repositories FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('repository_id', 'Code projects');
CREATE TRIGGER kg_sync AFTER INSERT OR UPDATE OF server_id, service_id, runtime_name, environment ON service_deployments
    FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('deployment_id', 'Services (deployment)');
CREATE TRIGGER kg_sync_release BEFORE DELETE ON service_deployments
    FOR EACH ROW EXECUTE FUNCTION kg_sync_inventory('deployment_id', 'Services (deployment)');

-- Relation tables become synced edges. TG_ARGV: from link, from column, to link, to column, edge type.
CREATE FUNCTION kg_sync_relation() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE
    r jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        r := to_jsonb(OLD);
        PERFORM kg_unsync_edge(kg_node_for(TG_ARGV[0], (r ->> TG_ARGV[1])::uuid), kg_node_for(TG_ARGV[2], (r ->> TG_ARGV[3])::uuid), TG_ARGV[4]);
        RETURN OLD;
    END IF;
    r := to_jsonb(NEW);
    PERFORM kg_sync_edge((r ->> 'project_id')::uuid, kg_sync_row(TG_ARGV[0], (r ->> TG_ARGV[1])::uuid),
                         kg_sync_row(TG_ARGV[2], (r ->> TG_ARGV[3])::uuid), TG_ARGV[4]);
    RETURN NEW;
END $$;

CREATE TRIGGER kg_sync AFTER INSERT OR DELETE ON service_dependencies
    FOR EACH ROW EXECUTE FUNCTION kg_sync_relation('service_id', 'service_id', 'service_id', 'depends_on_service_id', 'depends_on');
CREATE TRIGGER kg_sync AFTER INSERT OR DELETE ON service_repositories
    FOR EACH ROW EXECUTE FUNCTION kg_sync_relation('service_id', 'service_id', 'repository_id', 'repository_id', 'uses');
CREATE TRIGGER kg_sync AFTER INSERT OR DELETE ON environment_servers
    FOR EACH ROW EXECUTE FUNCTION kg_sync_relation('environment_id', 'environment_id', 'server_id', 'server_id', 'includes');
CREATE TRIGGER kg_sync AFTER INSERT OR DELETE ON environment_services
    FOR EACH ROW EXECUTE FUNCTION kg_sync_relation('environment_id', 'environment_id', 'service_id', 'service_id', 'includes');

-- Backfill what the inventory already holds.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT id FROM servers ORDER BY created_at, id LOOP PERFORM kg_sync_row('server_id', r.id); END LOOP;
    FOR r IN SELECT id FROM environments ORDER BY created_at, id LOOP PERFORM kg_sync_row('environment_id', r.id); END LOOP;
    FOR r IN SELECT id FROM services ORDER BY created_at, id LOOP PERFORM kg_sync_row('service_id', r.id); END LOOP;
    FOR r IN SELECT id FROM repositories ORDER BY created_at, id LOOP PERFORM kg_sync_row('repository_id', r.id); END LOOP;
    FOR r IN SELECT id FROM service_deployments ORDER BY id LOOP PERFORM kg_sync_row('deployment_id', r.id); END LOOP;
    FOR r IN SELECT * FROM service_dependencies LOOP
        PERFORM kg_sync_edge(r.project_id, kg_node_for('service_id', r.service_id), kg_node_for('service_id', r.depends_on_service_id), 'depends_on');
    END LOOP;
    FOR r IN SELECT * FROM service_repositories LOOP
        PERFORM kg_sync_edge(r.project_id, kg_node_for('service_id', r.service_id), kg_node_for('repository_id', r.repository_id), 'uses');
    END LOOP;
    FOR r IN SELECT * FROM environment_servers LOOP
        PERFORM kg_sync_edge(r.project_id, kg_node_for('environment_id', r.environment_id), kg_node_for('server_id', r.server_id), 'includes');
    END LOOP;
    FOR r IN SELECT * FROM environment_services LOOP
        PERFORM kg_sync_edge(r.project_id, kg_node_for('environment_id', r.environment_id), kg_node_for('service_id', r.service_id), 'includes');
    END LOOP;
END $$;

COMMIT;
```

Notes for the implementer (these are correctness requirements, not suggestions):
- **Delete triggers are `BEFORE DELETE`.** The `ON DELETE SET NULL` foreign keys are system `AFTER` triggers that would
  clear the link before an `AFTER DELETE` trigger could find the node.
- If a table lacks `created_at` (check `servers`, `environments`, `services`, `repositories`), order that loop by `id`
  only; the order between tables (servers, environments, services, repositories, deployments, then the four relation
  tables) is what matters.
- `kg_nodes_place` (from 0092) also takes the project lock; taking it again in the same transaction is a no-op.

`db/migrations/0093_graph_inventory_sync.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DROP TRIGGER kg_sync ON servers;
DROP TRIGGER kg_sync_release ON servers;
DROP TRIGGER kg_sync ON environments;
DROP TRIGGER kg_sync_release ON environments;
DROP TRIGGER kg_sync ON services;
DROP TRIGGER kg_sync_release ON services;
DROP TRIGGER kg_sync ON repositories;
DROP TRIGGER kg_sync_release ON repositories;
DROP TRIGGER kg_sync ON service_deployments;
DROP TRIGGER kg_sync_release ON service_deployments;
DROP TRIGGER kg_sync ON service_dependencies;
DROP TRIGGER kg_sync ON service_repositories;
DROP TRIGGER kg_sync ON environment_servers;
DROP TRIGGER kg_sync ON environment_services;

DELETE FROM kg_edges WHERE synced OR type = 'includes';
-- Linked nodes with nothing hand-made attached go (deployments before their servers); the rest are unlinked.
DO $$
DECLARE removed integer;
BEGIN
    LOOP
        DELETE FROM kg_nodes n
            WHERE num_nonnulls(n.server_id, n.deployment_id, n.environment_id, n.service_id, n.repository_id) = 1
              AND NOT EXISTS (SELECT 1 FROM kg_nodes c WHERE c.parent_id = n.id)
              AND NOT EXISTS (SELECT 1 FROM kg_node_documents d WHERE d.node_id = n.id);
        GET DIAGNOSTICS removed = ROW_COUNT;
        EXIT WHEN removed = 0;
    END LOOP;
END $$;

DROP FUNCTION kg_sync_relation();
DROP FUNCTION kg_sync_inventory();
DROP FUNCTION kg_sync_release(text, uuid, text);
DROP FUNCTION kg_sync_row(text, uuid);
DROP FUNCTION kg_unsync_edge(uuid, uuid, text);
DROP FUNCTION kg_sync_edge(uuid, uuid, uuid, text);
DROP FUNCTION kg_free_name(uuid, text, uuid, text, uuid);
DROP FUNCTION kg_node_for(text, uuid);

ALTER TABLE kg_edges
    DROP COLUMN synced,
    DROP CONSTRAINT kg_edges_type,
    ADD CONSTRAINT kg_edges_type CHECK (type IN ('calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes',
        'subscribes', 'instance_of', 'runs_on', 'implements', 'stores'));
ALTER TABLE kg_nodes
    DROP COLUMN server_id,
    DROP COLUMN deployment_id,
    DROP COLUMN environment_id,
    DROP COLUMN service_id,
    DROP COLUMN repository_id;

COMMIT;
```

(Dropping the columns drops their foreign keys, checks and indexes. The node `DELETE` loop runs while the columns still
exist, then they go.)

`db/tests/run.sh`: insert, right after the second `run_migrate postgres up >> "$test_log" …` line (before the 0092
`down 1` step), a 0093 `down 1` with a check that `ka.kg_nodes` has no `server_id` column, and after the line
`printf '%s\n' 'PASS: the system graph has nodes, edges and document links.'` an up check that `ka.kg_nodes` has a
`server_id` column and `ka.kg_edges` a `synced` column, printing
`PASS: inventory rows sync into the system graph.` — same shape as the 0092 lines next to them.

`docs/migrations.md`, after `## 0092_system_graph`:

```markdown
## 0093_graph_inventory_sync

Servers, services, service deployments, environments and code projects are linked to system graph nodes
(`kg_nodes.server_id`, `service_id`, `deployment_id`, `environment_id`, `repository_id`; at most one per node, one node
per row, the layer must match). Triggers on the inventory tables create, rename and move those nodes in the same
transaction (a deployment's node sits under its server's node), and turn service dependencies, service code projects and
environment members into edges marked `synced` (`depends_on`, `uses`, and the new type `includes`). Deleting a row
deletes its node unless the node has children or documents; then the node stays, unlinked, with "Removed from … on
<date>." added to its description. The migration fills the graph from the existing inventory (a clash with a hand-made
node's name gets " (2)"). The down migration deletes synced edges and `includes` edges, deletes linked nodes with nothing
attached, and drops the links, triggers and functions.
```

`deploy/README.md`, after `## Upgrading to the system graph`:

```markdown
## Upgrading to the inventory sync

Migration 0093 links the system graph to the inventory: every server, service, deployment, environment and code project
gets a node (Environments → System map), kept in step whenever the inventory changes. The migration creates the nodes
for the existing inventory; nodes made by hand keep their names, and a synced node whose name is already taken gets
" (2)".
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_inventory_sync_migration.py tests/test_system_graph_migration.py`
Expected: PASS. Then `sh db/tests/run.sh` from the worktree root — exit 0 and the new PASS line printed.

- [ ] **Step 5: Keep the existing migration tests working**

0093 adds foreign keys from `kg_nodes` to `servers`, `services`, `service_deployments`, `environments` and
`repositories`, and triggers on those tables. Run the migration-related test files:
`cd services/admin-api && uv run pytest -q tests/test_system_graph_migration.py tests/test_environments.py tests/test_services.py tests/test_servers.py tests/test_service_environments.py tests/test_service_dependencies.py tests/test_service_dependency_api.py tests/test_documents.py tests/test_repositories.py tests/test_events_migration.py tests/test_event_handlers_migration.py`
and then the whole suite once. Any test that replays an older down migration past 0093 on a fully migrated database
(look for `read_text()` of `*.down.sql` files) must run `0093_graph_inventory_sync.down.sql` first (and, where it already
steps over 0092, keep that order: 0093 down, then 0092 down), exactly like those tests already step over later
migrations. `test_system_graph_migration.py::test_down_and_up` runs 0092 down: add 0093 down before it and 0093 up after
0092 up. Do not change any other assertion. The two known failures listed in Global Constraints stay as they are.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/0093_graph_inventory_sync.up.sql db/migrations/0093_graph_inventory_sync.down.sql db/tests/run.sh \
  docs/migrations.md deploy/README.md services/admin-api/tests/
git commit -m "feat(db): inventory rows sync into the system graph (0093)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Graph API — sources, synced edges, refusals; sync through the inventory APIs

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/system_graph.py`, `services/admin-api/src/knowledge_admin/system_graph_api.py`
- Modify: `services/admin-api/tests/test_system_graph.py` (expected dicts gain the new keys)
- Create: `services/admin-api/tests/test_graph_inventory_sync_api.py`

**Interfaces:**
- Consumes: Task 1 columns and `synced`.
- Produces: `GET /api/graph/nodes/{id}` → node with `source: {kind, id, page, label} | null`; edges in `outgoing` /
  `incoming` with `synced`; view nodes and boundary nodes with `source_kind`; view edges with `synced`; `includes` as an
  edge type for `POST /api/graph/edges`.

- [ ] **Step 1: Update `system_graph.py`.** `EDGE_TYPES` gains `'includes'` (last). In `build_view`, every node row in
  `shown` and every boundary dict gets `'source_kind': node.get('source_kind')`, and every aggregated edge gets
  `'synced'`: start the group with `'synced': True` and set `group['synced'] = group['synced'] and bool(edge.get('synced'))`
  for each underlying edge. In `services/admin-api/tests/test_system_graph.py`, add `'synced': False` to every expected
  edge dict and `'source_kind': None` to every expected boundary dict (no other changes), and add:

```python
def test_sources_and_synced_edges_carry_through():
    nodes = [n('api', kind='service', name='admin-api'), n('worker', kind='service', name='worker'),
             n('events', 'api', name='Event processor')]
    nodes[0]['source_kind'] = 'service'
    edges = [e('e1', 'api', 'worker', 'depends_on'), e('e2', 'events', 'worker')]
    edges[0]['synced'] = True
    result = view(edges, nodes=nodes)
    assert [row['source_kind'] for row in result['nodes']] == ['service', None]
    assert result['edges'] == [{'from': 'api', 'to': 'worker', 'type': 'calls', 'count': 1, 'edge_ids': ['e2'], 'synced': False},
                               {'from': 'api', 'to': 'worker', 'type': 'depends_on', 'count': 1, 'edge_ids': ['e1'], 'synced': True}]
```

- [ ] **Step 2: Write the failing API tests** `services/admin-api/tests/test_graph_inventory_sync_api.py`

```python
"""Inventory pages write the graph: Servers, Services (deployments, dependencies, code projects), Environments."""
from uuid import uuid4

import pytest

from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def nodes(client, layer, root=None) -> dict:
    params = {'root': root} if root else {}
    return {row['name']: row for row in client.get(f'/api/graph/view/{layer}', headers=HEADERS, params=params).json()['nodes']}


def detail(client, node_id) -> dict:
    return client.get(f'/api/graph/nodes/{node_id}', headers=HEADERS).json()['node']


def message(response) -> tuple:
    return response.status_code, response.json()['error']['message']


def test_services_page_saves_sync_the_graph(client, store):
    made = client.post('/api/graph/nodes', headers=HEADERS, json={'layer': 'software', 'kind': 'service', 'name': 'API'})
    assert made.status_code == 201  # a hand-made node that clashes with the service below
    one = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()
    two = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-2', 'hostname': 'two.internal'}).json()
    prod = client.post('/api/environments', headers=HEADERS, json={'name': 'production', 'server_ids': [one['id']]}).json()
    worker = client.post('/api/services', headers=HEADERS, json={'name': 'Worker'}).json()
    repo = str(store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', ''))
    response = client.post('/api/services', headers=HEADERS, json={
        'name': 'API', 'environment_ids': [prod['id']], 'repository_ids': [repo],
        'dependencies': [{'service_id': worker['id']}],
        'deployments': [{'server_id': one['id'], 'environment_id': prod['id'], 'runtime_name': 'api'},
                        {'server_id': two['id'], 'environment_id': prod['id']}]})
    assert response.status_code in (200, 201), response.text
    api = response.json()
    software = nodes(client, 'software')
    assert {'API', 'API (2)', 'Worker'} <= set(software)
    assert software['API (2)']['source_kind'] == 'service' and software['API']['source_kind'] is None
    infrastructure = nodes(client, 'infrastructure')
    assert {name: row['source_kind'] for name, row in infrastructure.items()} == {
        'prod-1': 'server', 'prod-2': 'server', 'production': 'environment'}
    on_one = nodes(client, 'infrastructure', infrastructure['prod-1']['id'])
    on_two = nodes(client, 'infrastructure', infrastructure['prod-2']['id'])
    assert list(on_one) == ['api · production'] and list(on_two) == ['API · production']
    service_node = detail(client, software['API (2)']['id'])
    assert service_node['source'] == {'kind': 'service', 'id': api['id'], 'page': '/services', 'label': 'Services'}
    assert {(edge['type'], edge['node']['name'], edge['synced']) for edge in service_node['incoming']} >= {
        ('instance_of', 'api · production', True), ('instance_of', 'API · production', True), ('includes', 'production', True)}
    assert any(edge['type'] == 'uses' and edge['synced'] for edge in service_node['outgoing'])
    assert ('depends_on', 'Worker', True) in {(edge['type'], edge['node']['name'], edge['synced']) for edge in service_node['outgoing']}


def test_moving_a_deployment_moves_its_node(client):
    one = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()
    two = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-2', 'hostname': 'two.internal'}).json()
    api = client.post('/api/services', headers=HEADERS, json={'name': 'API', 'deployments': [
        {'server_id': one['id'], 'environment': 'production'}]}).json()
    current = client.get(f"/api/services/{api['id']}/context", headers=HEADERS).json()['service']
    deployment = current['deployments'][0]
    saved = client.put(f"/api/services/{api['id']}", headers=HEADERS, json={
        'name': 'API', 'revision': current['revision'],
        'deployments': [{'id': deployment['id'], 'server_id': two['id'], 'environment': 'production'}]})
    assert saved.status_code == 200, saved.text
    infrastructure = nodes(client, 'infrastructure')
    assert nodes(client, 'infrastructure', infrastructure['prod-1']['id']) == {}
    assert list(nodes(client, 'infrastructure', infrastructure['prod-2']['id'])) == ['API · production']


def test_deleting_a_service_keeps_hand_made_children(client):
    api = client.post('/api/services', headers=HEADERS, json={'name': 'API'}).json()
    service_node = nodes(client, 'software')['API']
    child = client.post('/api/graph/nodes', headers=HEADERS, json={
        'layer': 'software', 'kind': 'module', 'name': 'Event processor', 'parent_id': service_node['id']})
    assert child.status_code == 201
    current = client.get(f"/api/services/{api['id']}/context", headers=HEADERS).json()['service']
    assert client.delete(f"/api/services/{api['id']}?revision={current['revision']}&confirmed=true", headers=HEADERS).status_code == 200
    kept = detail(client, service_node['id'])
    assert kept['source'] is None and 'Removed from Services on ' in kept['description']
    bare = client.post('/api/servers', headers=HEADERS, json={'name': 'spare', 'hostname': 'spare.internal'}).json()
    assert 'spare' in nodes(client, 'infrastructure')
    assert client.delete(f"/api/servers/{bare['id']}?revision=1&confirmed=true", headers=HEADERS).status_code == 200
    assert 'spare' not in nodes(client, 'infrastructure')


def test_synced_nodes_refuse_only_what_the_inventory_owns(client):
    client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'})
    host = nodes(client, 'infrastructure')['prod-1']
    current = detail(client, host['id'])
    body = {key: current[key] for key in ('kind', 'name', 'description', 'properties', 'parent_id', 'revision')}
    url = f"/api/graph/nodes/{host['id']}"
    assert message(client.put(url, headers=HEADERS, json={**body, 'name': 'renamed'})) == (409, 'This node comes from Servers. Change it there.')
    assert message(client.put(url, headers=HEADERS, json={**body, 'kind': 'vm'})) == (409, 'This node comes from Servers. Change it there.')
    other = client.post('/api/graph/nodes', headers=HEADERS, json={'layer': 'infrastructure', 'kind': 'rack', 'name': 'Rack A'}).json()['node']
    assert message(client.put(url, headers=HEADERS, json={**body, 'parent_id': other['id']})) == (
        409, 'This node comes from Servers. Its place follows the inventory.')
    assert message(client.delete(url, headers=HEADERS)) == (409, 'This node comes from Servers. Delete it there.')
    saved = client.put(url, headers=HEADERS, json={**body, 'description': 'Main host', 'properties': {'cpu': 8}})
    assert saved.status_code == 200, saved.text
    assert saved.json()['node']['description'] == 'Main host'
    child = client.post('/api/graph/nodes', headers=HEADERS, json={'layer': 'infrastructure', 'kind': 'disk', 'name': 'disk', 'parent_id': host['id']})
    assert child.status_code == 201
    edge = client.post('/api/graph/edges', headers=HEADERS, json={'from_id': other['id'], 'to_id': host['id'], 'type': 'includes'})
    assert edge.status_code == 201 and edge.json()['edge']['type'] == 'includes'


def test_synced_edges_are_read_only(client):
    api = client.post('/api/services', headers=HEADERS, json={'name': 'API'}).json()
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'prod-1', 'hostname': 'one.internal'}).json()
    client.post('/api/environments', headers=HEADERS, json={'name': 'production', 'server_ids': [host['id']], 'service_ids': [api['id']]})
    environment = nodes(client, 'infrastructure')['production']
    [synced, *_] = detail(client, environment['id'])['outgoing']
    assert synced['synced'] is True
    url = f"/api/graph/edges/{synced['id']}"
    assert message(client.put(url, headers=HEADERS, json={'description': 'x', 'revision': synced['revision']})) == (
        409, 'This edge comes from the inventory. Change it in Environments.')
    assert message(client.delete(url, headers=HEADERS)) == (409, 'This edge comes from the inventory. Change it in Environments.')
    view = client.get('/api/graph/view/infrastructure', headers=HEADERS).json()
    assert view['edges'] == [] or all('synced' in edge for edge in view['edges'])
```

The payload keys (`environment_ids`, `repository_ids`, `dependencies[].service_id`, `deployments[].environment_id`,
`deployments[].runtime_name`) are `ServiceInput` fields in `services/admin-api/src/knowledge_admin/services.py`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_inventory_sync_api.py tests/test_system_graph.py`
Expected: FAIL (`source` / `source_kind` / `synced` missing; refusals not implemented).

- [ ] **Step 4: Implement in `system_graph_api.py`**

```python
EdgeType = Literal['calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes', 'subscribes', 'instance_of', 'runs_on',
                   'implements', 'stores', 'includes']
SOURCES = {  # link column → (kind, page, label)
    'server_id': ('server', '/servers', 'Servers'),
    'deployment_id': ('deployment', '/services', 'Services'),
    'environment_id': ('environment', '/environments', 'Environments'),
    'service_id': ('service', '/services', 'Services'),
    'repository_id': ('repository', '/code-projects', 'Code projects'),
}
LINKS = ','.join(SOURCES)
NODE = f'id,layer,kind,name,description,parent_id,properties,revision,created_at,updated_at,{LINKS}'
EDGE = 'id,from_id,to_id,type,description,revision,synced,created_at,updated_at'
SOURCE_KIND = ('CASE ' + ' '.join(f"WHEN {link} IS NOT NULL THEN '{kind}'" for link, (kind, _, _) in SOURCES.items())
               + ' END AS source_kind')


def source_of(row: dict) -> dict | None:
    """Where a node comes from, or None for a hand-made node."""
    for link, (kind, page, label) in SOURCES.items():
        if row.get(link):
            return {'kind': kind, 'id': row[link], 'page': page, 'label': label}
    return None


def public(row: dict) -> dict:
    """A node row without the five link columns, with its source."""
    return {**{key: value for key, value in row.items() if key not in SOURCES}, 'source': source_of(row)}
```

Then, inside `build_api`:
- `detail()` returns `{**public(node), 'breadcrumb': …}` and its edge query selects `e.synced` too.
- `create()` and every other place that returned the raw node row keep going through `detail()` (no other change).
- `view()` selects `id,parent_id,layer,kind,name,description,{SOURCE_KIND}` for nodes and `id,from_id,to_id,type,synced`
  for edges.
- `save()`: after the revision check, `source = source_of(current)`; if `source` and (`fields['kind'] != current['kind']`
  or `fields['name'] != current['name']`) → `HTTPException(409, f"This node comes from {source['label']}. Change it there.")`;
  if `source` and `fields['parent_id'] != current['parent_id']` →
  `HTTPException(409, f"This node comes from {source['label']}. Its place follows the inventory.")`.
- `delete()`: after `node_row`, if `source_of(node)` → `HTTPException(409, f"This node comes from {label}. Delete it there.")`
  (before the children check).
- `save_edge()` and `delete_edge()`: load the edge with `edge_row(..., lock=True)` first (delete currently deletes
  directly — change it to load, check, then delete); if `edge['synced']`, find the from-node's source label
  (`source_of(node_row(conn, project, edge['from_id']))`, falling back to `'the inventory pages'` if the from-node has
  no source) and raise `HTTPException(409, f"This edge comes from the inventory. Change it in {label}.")`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_inventory_sync_api.py tests/test_system_graph.py tests/test_system_graph_api.py tests/test_system_graph_render.py tests/test_graph_inventory_sync_migration.py`
Expected: PASS. `test_system_graph_api.py` may compare node or edge dicts that now have `source` / `synced` keys — update
those expectations only by adding the new keys with their hand-made values (`None` / `False`).

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/system_graph.py services/admin-api/src/knowledge_admin/system_graph_api.py \
  services/admin-api/tests/test_system_graph.py services/admin-api/tests/test_system_graph_api.py \
  services/admin-api/tests/test_graph_inventory_sync_api.py
git commit -m "feat(api): graph nodes show their inventory source; synced items are read-only where the inventory owns them

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: UI — source line, read-only fields, synced badges; user guide

**Files:**
- Modify: `services/admin-ui/app/lib/systemGraph.ts`, `services/admin-ui/app/components/NodeSheet.tsx`,
  `services/admin-ui/app/components/SystemMapView.tsx`, `docs/user-guide.md`
- Test: `services/admin-ui/app/components/__tests__/nodeSheet.test.tsx`, `__tests__/systemMap.test.tsx`

**Interfaces:**
- Consumes: Task 2 fields `source`, `synced`, `source_kind`; edge type `includes`.

- [ ] **Step 1: Types and labels** in `systemGraph.ts`:

```ts
export const EDGE_TYPES = ["calls", "uses", "depends_on", "reads", "writes", "publishes", "subscribes", "instance_of", "runs_on", "implements", "stores", "includes"] as const;
// EDGE_LABELS gains: includes: "includes"
export type SourceKind = "server" | "deployment" | "environment" | "service" | "repository";
export const SOURCE_LABELS: Record<SourceKind, string> = { server: "Servers", deployment: "Services", environment: "Environments",
  service: "Services", repository: "Code projects" };
export interface NodeSource { kind: SourceKind; id: string; page: string; label: string }
```

and add `source_kind: SourceKind | null` to `ViewNode` and `BoundaryNode`, `synced: boolean` to `ViewEdge` and
`EdgeRow`, `source: NodeSource | null` to `NodeDetail`.

- [ ] **Step 2: Write the failing tests.** In `nodeSheet.test.tsx`, give the existing `events` fixture `source: null`
and both its edges `synced: false`, then add:

```tsx
test("a synced node shows where it comes from and keeps only what the graph owns", async () => {
  const host = { ...events, id: "host", layer: "infrastructure", kind: "server", name: "prod-1", parent_id: null,
    breadcrumb: [{ id: "host", name: "prod-1", kind: "server" }],
    source: { kind: "server", id: "s1", page: "/servers", label: "Servers" },
    outgoing: [], incoming: [{ id: "e9", type: "includes", description: "", revision: 1, synced: true,
      node: { id: "env", name: "production", kind: "environment", layer: "infrastructure" } }] };
  api({ "GET /api/graph/nodes/host": () => ({ body: { node: host } }) });
  open({ nodeId: "host" });
  expect(await screen.findByText("From Servers")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open Servers" })).toHaveAttribute("href", "/servers");
  expect(screen.getByLabelText("Name")).toHaveAttribute("readonly");
  expect(screen.getByLabelText("Kind")).toHaveAttribute("readonly");
  expect(screen.getByLabelText("Description")).not.toHaveAttribute("readonly");
  expect(screen.queryByRole("region", { name: "Move" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  const edges = within(screen.getByRole("region", { name: "Edges" }));
  expect(edges.getByText("production → includes")).toBeInTheDocument();
  expect(edges.getByText("synced")).toBeInTheDocument();
  expect(edges.queryByRole("button", { name: /Remove/ })).toBeNull();
  expect(edges.getByLabelText("Target")).toBeInTheDocument();  // hand-made edges can still be added
});
```

In `systemMap.test.tsx`, give the `node()` helper `source_kind: null`, boundary fixtures `source_kind: null`, edges
`synced: false`, and add:

```tsx
test("synced nodes say where they come from", async () => {
  api({ [TOP]: { json: { ...top, nodes: [node("api", "admin-api", { source_kind: "service" }), node("worker", "repository-worker")] } },
    [TOP_SVG]: { text: "<svg/>" } });
  view();
  const inside = within(await screen.findByRole("region", { name: "Inside" }));
  expect(await inside.findByText("service · from Services")).toBeInTheDocument();
  expect(inside.getByText("service")).toBeInTheDocument();  // a hand-made node keeps the plain kind
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/nodeSheet.test.tsx app/components/__tests__/systemMap.test.tsx`
Expected: FAIL.

- [ ] **Step 4: Implement**
- `NodeSheet.tsx`, `NodeDetails`: when `node.source`, render under the header
  `<p className="text-sm">From {node.source.label} · <Link to={node.source.page} className="underline">Open {node.source.label}</Link></p>`
  (the visible text "From Servers" must be its own text node: wrap it in `<span>`); pass `readOnly` for Kind and Name to
  `DraftFields` (new optional prop `locked?: boolean` that sets `readOnly` on those two inputs and a
  `FieldDescription` "Change it in {label}." under Name); do not render the Move section or the Delete section; in the
  edge lists, a synced edge renders `<Badge variant="secondary">synced</Badge>` after its text and no Remove button
  (keep the edge text in its existing `<span>` and put the badge outside it, so the text still matches exactly).
- `SystemMapView.tsx`: in the Inside list, the kind line is `` `${node.kind} · from ${SOURCE_LABELS[node.source_kind]}` ``
  when `node.source_kind` is set (plus ` · +N inside` as before); Outside likewise shows ` · from <label>` after the
  layer when set.
- `docs/user-guide.md` §4.20 System map: add a bullet:

```markdown
- **From the inventory.** Every server, service (with its deployments), environment and code project has a node, kept in
  step automatically: a server's node holds its deployments (`<runtime or service> · <environment>`), an environment
  *includes* its servers and services, a service *depends on* the services it uses and *uses* its code projects. Such
  nodes say **From Servers** (or Services, Environments, Code projects) with a link to that page; their name, kind and
  place follow the inventory, and their synced edges are read-only. You can still give them a description and
  properties, add nodes inside them, connect them with your own edges and attach documents. Deleting the row deletes its
  node, unless you added something to it — then the node stays, with "Removed from … on <date>." in its description.
```

- [ ] **Step 5: Run tests and type checks**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/nodeSheet.test.tsx app/components/__tests__/systemMap.test.tsx && npx react-router typegen && npx tsc --noEmit`
Expected: PASS, clean. Then the whole UI suite: `npx vitest run`.

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui/app/lib/systemGraph.ts services/admin-ui/app/components/NodeSheet.tsx \
  services/admin-ui/app/components/SystemMapView.tsx services/admin-ui/app/components/__tests__/nodeSheet.test.tsx \
  services/admin-ui/app/components/__tests__/systemMap.test.tsx docs/user-guide.md
git commit -m "feat(admin-ui): System map shows which nodes come from the inventory and keeps them read-only

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Final check

- Backend: `cd services/admin-api && uv run pytest -q` — only the known failures listed in Global Constraints.
- `sh db/tests/run.sh` exits 0.
- UI: `cd services/admin-ui && npx vitest run && npx tsc --noEmit`.
