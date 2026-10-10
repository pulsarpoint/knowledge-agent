# System graph inventory sync — Python reconcile and in-memory graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 0093 sync triggers with a Python reconcile called by every inventory write (and at start-up), and
serve graph views and search from an in-memory networkx graph rebuilt when the stored graph changes; then finish the UI.

**Architecture:** Migration 0093 keeps only schema: link columns with deferred foreign keys, `synced` edges, the
`includes` type, and a one-row `kg_graph_version` bumped by statement triggers. `graph_sync.sync_inventory(conn,
project)` computes the wanted synced nodes and edges from the inventory and applies the difference in the caller's
transaction. `graph_memory.GraphMemory` keeps a `networkx.MultiDiGraph` snapshot per project and rebuilds it when the
version changes; `system_graph_api` reads views and search from it.

**Tech Stack:** PostgreSQL 17, FastAPI + psycopg3, networkx, React Router 7 + shadcn/ui, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-10-graph-inventory-sync-design.md` (revised 86ebf4e). Supersedes Tasks 1 and 3
of `docs/superpowers/plans/2026-10-10-graph-inventory-sync.md`; that plan's Task 2 (commit 448572f: `source`, `synced`,
refusals, lock order) stays and is reviewed in Task 2 below.

## Global Constraints

- Work only in the worktree `.claude/worktrees/graph-inventory-sync` (branch `feature/graph-inventory-sync`). Never run
  make targets; never touch `.env`, `.local` or the running Docker stack's database.
- Never `git stash`; never `git add .superpowers`. Every commit ends with
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd services/admin-api && uv run pytest -q tests/<file> < /dev/null` (always `< /dev/null`). UI:
  `cd services/admin-ui && npx vitest run <file>` + `npx react-router typegen && npx tsc --noEmit` (`pnpm install
  --frozen-lockfile` first if `node_modules` is missing). Known failures to ignore: the 5 code-graph tests (no
  `services/code-graph/node_modules` in the worktree), `test_deploy.py::test_docs_commands_serve_the_docs_folder_inside_the_data_root`,
  `test_environments.py::test_migration_reapply_and_populated_rollback_protection`.
- The graph lock: `SELECT pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(%s::text))` with the project id. The
  sync takes it first; graph writes already take it before row locks (448572f).
- Mapping, names, delete note: as in the spec — link columns `server_id`, `environment_id`, `service_id`,
  `repository_id`, `deployment_id`; deployment name `<runtime_name, or the service name when blank> · <environment>`;
  clash rule `<name>`, `<name> (2)`, … (case-insensitive among siblings of the same layer and parent, cut to 160
  characters including the suffix); delete note on a new line
  `Removed from <Servers | Environments | Services | Code projects | Services (deployment)> on <YYYY-MM-DD>.`, description
  cut from the front to 4000 characters. Archived code projects count as gone.

## Review Focus

1. An inventory delete path that forgets to call the sync must fail loudly at commit (deferred foreign key), and every
   real delete path (Servers, Services, Environments) must call it (Task 2 `test_every_inventory_page_keeps_the_graph`).
2. A second `sync_inventory` right after a first one changes nothing — no revision bumps, no version bump (Task 2
   `test_a_second_sync_changes_nothing`).
3. A view requested right after any graph write shows the write (version bump → rebuild) in the same process (Task 3
   `test_views_follow_writes`).
4. Start-up sync failing (e.g. database briefly down) must not stop admin-api from starting (Task 2
   `test_startup_sync_failure_is_logged`).
5. Two processes: a write in one makes the other's next read rebuild (Task 3 `test_another_connection_sees_the_new_version`).

---

### Task 1: Migration 0093 becomes schema only

**Files:**
- Modify: `db/migrations/0093_graph_inventory_sync.up.sql`, `db/migrations/0093_graph_inventory_sync.down.sql`
- Modify: `services/admin-api/tests/test_graph_inventory_sync_migration.py` (schema tests only)
- Rename + rework later: the behaviour tests move to `services/admin-api/tests/test_graph_sync.py` in Task 2 — in this
  task, **delete** them from the migration test file (they test triggers that no longer exist); keep the schema tests.
- Modify: `docs/migrations.md` (0093 section), `deploy/README.md` (upgrade note)

**Interfaces:**
- Produces: link columns with `DEFERRABLE INITIALLY DEFERRED` foreign keys (no `ON DELETE` action); `kg_edges.synced`;
  type `includes`; table `ka.kg_graph_version(only boolean PK, version bigint)` with one row; statement trigger
  function `ka.kg_graph_changed()` on `kg_nodes`, `kg_edges`, `kg_node_documents` that bumps the version and
  `pg_notify('ka_graph', <version>)`. No sync functions, no inventory triggers, no backfill.

- [ ] **Step 1: Replace the migration test file content** `services/admin-api/tests/test_graph_inventory_sync_migration.py`:

```python
"""0093: graph nodes can link to inventory rows (deferred foreign keys), edges can be synced, and every graph change
bumps one version number (and notifies ka_graph)."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def server(conn, project, name='prod-1') -> dict:
    return conn.execute("""INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,%s,%s,false) RETURNING *""",
                        (project, name, f'{uuid4().hex[:8]}.internal')).fetchone()


def version(conn) -> int:
    return conn.execute('SELECT version FROM ka.kg_graph_version').fetchone()['version']


def test_link_rules(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host = server(conn, project)
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id)
            VALUES (%s,'infrastructure','server','prod-1',%s)""", (project, host['id']))
    for sql, values, error in [
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id) VALUES (%s,'software','x','x',%s)",
         (project, host['id']), psycopg.errors.CheckViolation),                       # layer must match the link
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id) VALUES (%s,'infrastructure','x','y',%s)",
         (project, host['id']), psycopg.errors.UniqueViolation),                      # one node per row
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id,environment_id) VALUES (%s,'infrastructure','x','z',%s,%s)",
         (project, host['id'], uuid4()), psycopg.errors.CheckViolation)]:             # one link per node
        with pytest.raises(error), store.connect() as conn:
            conn.execute(sql, values)
    with store.connect() as conn:
        includes = conn.execute("""SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint
            WHERE conname='kg_edges_type' AND conrelid='ka.kg_edges'::regclass""").fetchone()['d']
        assert "'includes'" in includes


def test_a_delete_without_a_sync_fails_at_commit(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host = server(conn, project)
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id)
            VALUES (%s,'infrastructure','server','prod-1',%s)""", (project, host['id']))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), store.connect() as conn:
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))  # the link is checked at commit
    with store.connect() as conn:  # unlinking in the same transaction (what the sync does) lets it commit
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))
        conn.execute('UPDATE ka.kg_nodes SET server_id=NULL WHERE server_id=%s', (host['id'],))


def test_every_graph_statement_bumps_the_version_and_notifies(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        start = version(conn)
        node = conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'software','service','a'),
            (%s,'software','service','b') RETURNING id""", (project, project)).fetchall()
        assert version(conn) == start + 1  # one statement, one bump
        conn.execute("INSERT INTO ka.kg_edges(project_id,from_id,to_id,type) VALUES (%s,%s,%s,'calls')",
                     (project, node[0]['id'], node[1]['id']))
        conn.execute("UPDATE ka.kg_nodes SET description='x' WHERE id=%s", (node[0]['id'],))
        assert version(conn) == start + 3
    listener = psycopg.connect(store.conninfo, autocommit=True)
    try:
        listener.execute('LISTEN ka_graph')
        with store.connect() as conn:
            conn.execute("UPDATE ka.kg_nodes SET description='y' WHERE id=%s", (node[0]['id'],))
        notice = next(listener.notifies(timeout=5))
        assert (notice.channel, int(notice.payload)) == ('ka_graph', start + 4)
    finally:
        listener.close()


def test_down_and_up(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host = server(conn, project)
        linked = conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id)
            VALUES (%s,'infrastructure','server','prod-1',%s) RETURNING id""", (project, host['id'])).fetchone()['id']
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id)
            VALUES (%s,'infrastructure','disk','disk',%s)""", (project, linked))  # keeps its linked parent
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id)
            VALUES (%s,'infrastructure','server','prod-2',%s)""", (project, server(conn, project, 'prod-2')['id']))
        run(conn, '0093_graph_inventory_sync.down.sql')
        names = {row['name'] for row in conn.execute('SELECT name FROM ka.kg_nodes').fetchall()}
        assert names == {'prod-1', 'disk'}  # the bare linked node went, the one with a child stayed (unlinked)
        assert conn.execute("SELECT to_regclass('ka.kg_graph_version') IS NULL AS gone").fetchone()['gone']
        run(conn, '0093_graph_inventory_sync.up.sql')
        assert version(conn) == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_inventory_sync_migration.py < /dev/null`
Expected: FAIL (`kg_graph_version` missing; the delete succeeds because the trigger handles it).

- [ ] **Step 3: Rewrite `db/migrations/0093_graph_inventory_sync.up.sql`**

```sql
BEGIN;
SET search_path = ka, public;

-- Graph nodes can link to inventory rows (Servers, Services and their deployments, Environments, Code projects). The
-- admin API's sync (graph_sync.py) keeps them in step in the writer's transaction. The links are checked at commit, so
-- the sync can still see which node lost its row after a delete — and a delete path that forgot the sync fails loudly.
ALTER TABLE kg_nodes
    ADD COLUMN server_id uuid,
    ADD COLUMN deployment_id uuid,
    ADD COLUMN environment_id uuid,
    ADD COLUMN service_id uuid,
    ADD COLUMN repository_id uuid,
    ADD CONSTRAINT kg_nodes_server FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT kg_nodes_deployment FOREIGN KEY (deployment_id, project_id) REFERENCES service_deployments(id, project_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT kg_nodes_environment FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT kg_nodes_service FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT kg_nodes_repository FOREIGN KEY (repository_id, project_id) REFERENCES repositories(id, project_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT kg_nodes_one_source CHECK (num_nonnulls(server_id, deployment_id, environment_id, service_id, repository_id) <= 1),
    ADD CONSTRAINT kg_nodes_source_layer CHECK (
        (server_id IS NULL AND deployment_id IS NULL AND environment_id IS NULL OR layer = 'infrastructure')
        AND (service_id IS NULL AND repository_id IS NULL OR layer = 'software'));
CREATE UNIQUE INDEX kg_nodes_by_server ON kg_nodes (server_id) WHERE server_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_deployment ON kg_nodes (deployment_id) WHERE deployment_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_environment ON kg_nodes (environment_id) WHERE environment_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_service ON kg_nodes (service_id) WHERE service_id IS NOT NULL;
CREATE UNIQUE INDEX kg_nodes_by_repository ON kg_nodes (repository_id) WHERE repository_id IS NOT NULL;

ALTER TABLE kg_edges
    ADD COLUMN synced boolean NOT NULL DEFAULT false,
    DROP CONSTRAINT kg_edges_type,
    ADD CONSTRAINT kg_edges_type CHECK (type IN ('calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes',
        'subscribes', 'instance_of', 'runs_on', 'implements', 'stores', 'includes'));
COMMENT ON COLUMN kg_edges.synced IS 'Made by the inventory sync (dependencies, code projects, environment members, deployments); read-only in the graph.';

-- One number that changes whenever the graph does: processes keep the graph in memory and rebuild it when it moves.
CREATE TABLE kg_graph_version (
    only boolean PRIMARY KEY DEFAULT true CHECK (only),
    version bigint NOT NULL DEFAULT 0
);
INSERT INTO kg_graph_version DEFAULT VALUES;

CREATE FUNCTION kg_graph_changed() RETURNS trigger LANGUAGE plpgsql SET search_path = ka, pg_temp AS $$
DECLARE next_version bigint;
BEGIN
    UPDATE kg_graph_version SET version = version + 1 RETURNING version INTO next_version;
    PERFORM pg_notify('ka_graph', next_version::text);
    RETURN NULL;
END $$;
CREATE TRIGGER kg_graph_changed AFTER INSERT OR UPDATE OR DELETE ON kg_nodes FOR EACH STATEMENT EXECUTE FUNCTION kg_graph_changed();
CREATE TRIGGER kg_graph_changed AFTER INSERT OR UPDATE OR DELETE ON kg_edges FOR EACH STATEMENT EXECUTE FUNCTION kg_graph_changed();
CREATE TRIGGER kg_graph_changed AFTER INSERT OR UPDATE OR DELETE ON kg_node_documents FOR EACH STATEMENT EXECUTE FUNCTION kg_graph_changed();

COMMIT;
```

`db/migrations/0093_graph_inventory_sync.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DROP TRIGGER kg_graph_changed ON kg_node_documents;
DROP TRIGGER kg_graph_changed ON kg_edges;
DROP TRIGGER kg_graph_changed ON kg_nodes;
DROP FUNCTION kg_graph_changed();
DROP TABLE kg_graph_version;

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

`db/tests/run.sh`: keep the existing 0093 down/up checks (they look at the `server_id` column and `synced`); add
`AND to_regclass('ka.kg_graph_version') IS NOT NULL` to the up check.

`docs/migrations.md` — replace the `## 0093_graph_inventory_sync` section with:

```markdown
## 0093_graph_inventory_sync

System graph nodes can link to inventory rows (`kg_nodes.server_id`, `service_id`, `deployment_id`, `environment_id`,
`repository_id`; at most one per node, one node per row, the layer must match). The links are foreign keys checked at
commit: the admin API's sync (`graph_sync.py`), run by every inventory write and at start-up, creates, renames, moves
and releases linked nodes and keeps edges marked `synced` (`depends_on`, `uses`, `instance_of`, and the new type
`includes`). A delete that skipped the sync fails at commit. `kg_graph_version` holds one number that every statement on
`kg_nodes`, `kg_edges` and `kg_node_documents` increases (with a `ka_graph` notification), so processes know when to
rebuild their in-memory graph. The down migration deletes synced and `includes` edges, deletes linked nodes with nothing
attached, and drops the links, the version table and its trigger.
```

`deploy/README.md` — replace the body of `## Upgrading to the inventory sync` with:

```markdown
Migration 0093 links the system graph to the inventory. When admin-api starts it gives every server, service,
deployment, environment and code project a node (Environments → System map) and from then on keeps them in step whenever
the inventory changes. Nodes made by hand keep their names; a synced node whose name is already taken gets " (2)".
```

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_inventory_sync_migration.py tests/test_system_graph_migration.py < /dev/null`
Expected: PASS. `sh db/tests/run.sh` exits 0. (Task 2 of the old plan's API tests in
`test_graph_inventory_sync_api.py` now fail — nothing syncs until Task 2 here; that is expected.)

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0093_graph_inventory_sync.up.sql db/migrations/0093_graph_inventory_sync.down.sql db/tests/run.sh \
  docs/migrations.md deploy/README.md services/admin-api/tests/test_graph_inventory_sync_migration.py
git commit -m "refactor(db): 0093 keeps only links, synced edges and a graph version; the sync moves to Python

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `graph_sync.py` and every inventory write calls it

**Files:**
- Create: `services/admin-api/src/knowledge_admin/graph_sync.py`
- Modify: `services/admin-api/src/knowledge_admin/service_api.py` (service save and delete, server save and delete),
  `environments.py` (save, delete), `repositories.py` (`RepositoryStore.save`), `repository_api.py` (archive),
  `app.py` (start-up sync)
- Create: `services/admin-api/tests/test_graph_sync.py`
- Test: `services/admin-api/tests/test_graph_inventory_sync_api.py` (exists, from 448572f; must pass again)

**Interfaces:**
- Produces: `graph_sync.sync_inventory(conn, project: UUID) -> SyncResult` (dataclass with int fields `created`,
  `updated`, `released`, `edges_added`, `edges_removed`); `graph_sync.sync_all(store: RepositoryStore, apps: list[str])
  -> None` (start-up; one transaction per app).

- [ ] **Step 1: Write the failing tests** `services/admin-api/tests/test_graph_sync.py`. Port every behaviour test that
  used to be in `test_graph_inventory_sync_migration.py` before Task 1 (see `git show 448572f:services/admin-api/tests/test_graph_inventory_sync_migration.py`):
  `test_rows_get_linked_nodes`, `test_renames_follow_the_inventory`, `test_deployments_follow_their_server_and_environment`,
  `test_relation_tables_make_synced_edges`, `test_hand_made_edges_are_left_alone`, `test_clash_takes_a_suffix`,
  `test_delete_removes_bare_nodes`, `test_delete_keeps_nodes_with_children_or_documents`,
  `test_archiving_a_code_project_counts_as_deleting_it`, and turn `test_backfill_and_down_up` /
  `test_backfill_skips_archived_code_projects` into `test_the_first_sync_fills_the_graph` (inventory written before any
  sync; one sync creates everything; the clash with a hand-made `admin-api` gives `admin-api (2)`; archived code
  projects get no node). The only change in each: after the inventory SQL that should affect the graph, call
  `sync(conn, project)` before the assertions, where

```python
from knowledge_admin.graph_sync import sync_inventory


def sync(conn, project):
    return sync_inventory(conn, project)
```

  Keep every assertion as it was, with two exceptions: the revision assertion in `test_renames_follow_the_inventory`
  stays (`2` after one rename, unchanged after a hostname change plus sync); and drop the `conn.commit()` calls that were
  added before `store.save()` only if the test still passes without them. Then add:

```python
def test_a_second_sync_changes_nothing(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
        deployment(conn, project, api['id'], host['id'])
        sync(conn, project)
        before = conn.execute('SELECT version FROM ka.kg_graph_version').fetchone()['version']
        revisions = conn.execute('SELECT id,revision FROM ka.kg_nodes ORDER BY id').fetchall()
        assert sync(conn, project) == SyncResult()
        assert conn.execute('SELECT version FROM ka.kg_graph_version').fetchone()['version'] == before
        assert conn.execute('SELECT id,revision FROM ka.kg_nodes ORDER BY id').fetchall() == revisions


def test_the_sync_repairs_what_changed_behind_its_back(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, worker = service(conn, project, 'admin-api'), service(conn, project, 'worker')
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)',
                     (project, api['id'], worker['id']))
        sync(conn, project)
        conn.execute("UPDATE ka.kg_nodes SET name='renamed by hand', parent_id=NULL WHERE service_id=%s", (api['id'],))
        conn.execute('DELETE FROM ka.kg_edges WHERE synced')
        result = sync(conn, project)
        assert (result.updated, result.edges_added) == (1, 1)
        assert node(conn, 'service_id', api['id'])['name'] == 'admin-api'
        assert ('admin-api', 'worker', 'depends_on', True) in edges(conn)
```

  (`SyncResult` is imported from `knowledge_admin.graph_sync`.) And a start-up test:

```python
def test_sync_all_fills_each_app_and_survives_failures(store, caplog):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        server(conn, project)
    sync_all(store, ['shop'])
    with store.connect() as conn:
        assert node(conn, 'server_id', conn.execute('SELECT id FROM ka.servers').fetchone()['id']) is not None
    broken = RepositoryStore('host=127.0.0.1 port=1 dbname=none connect_timeout=1', b'k' * 32, 'http://identity.test/realms/docs')
    sync_all(broken, ['shop'])  # must not raise
    assert 'Graph inventory sync failed' in caplog.text
```

  (Import `sync_all` from `knowledge_admin.graph_sync` and `RepositoryStore` from `knowledge_admin.repositories`.)

  In `test_graph_inventory_sync_api.py` add one test that every page keeps the graph and nothing fails at commit:

```python
def test_every_inventory_page_keeps_the_graph(client, store):
    host = client.post('/api/servers', headers=HEADERS, json={'name': 'h1', 'hostname': 'h1.internal'}).json()
    renamed = client.put(f"/api/servers/{host['id']}", headers=HEADERS, json={'name': 'h2', 'hostname': 'h1.internal', 'revision': 1})
    assert renamed.status_code == 200, renamed.text
    env = client.post('/api/environments', headers=HEADERS, json={'name': 'dev', 'server_ids': [host['id']]}).json()
    assert {'h2', 'dev'} <= set(nodes(client, 'infrastructure'))
    assert client.delete(f"/api/environments/{env['id']}?revision=1&confirmed=true", headers=HEADERS).status_code == 200
    assert client.delete(f"/api/servers/{host['id']}?revision=2&confirmed=true", headers=HEADERS).status_code == 200
    assert nodes(client, 'infrastructure') == {}
    repo = store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', '')
    assert len(nodes(client, 'software')) == 1
    archived = client.post(f'/api/code-projects/{repo}/archive', headers=HEADERS, json={})
    assert archived.status_code in (200, 204), archived.text
    assert nodes(client, 'software') == {}
```

  (If the archive endpoint needs a different body, read `repository_api.py` `archive_project` and send what it expects;
  keep the assertions.)

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_sync.py tests/test_graph_inventory_sync_api.py < /dev/null`
Expected: FAIL (`knowledge_admin.graph_sync` missing).

- [ ] **Step 3: Write `services/admin-api/src/knowledge_admin/graph_sync.py`**

```python
"""Inventory → system graph. Servers, environments, services with their deployments, and code projects keep linked
graph nodes; service dependencies, service code projects, environment members and deployments keep synced edges.
sync_inventory works out the whole wanted picture from the inventory and writes only the difference, inside the
caller's transaction, so the inventory and the graph never disagree and anything changed behind its back is repaired."""
import logging
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

# link column → (layer, kind, label in the delete note); creation order = this order (servers before deployments).
SOURCES = {
    'server_id': ('infrastructure', 'server', 'Servers'),
    'environment_id': ('infrastructure', 'environment', 'Environments'),
    'service_id': ('software', 'service', 'Services'),
    'repository_id': ('software', 'repository', 'Code projects'),
    'deployment_id': ('infrastructure', 'instance', 'Services (deployment)'),
}
LINKS = ','.join(SOURCES)
MAX_NAME, MAX_DESCRIPTION = 160, 4000

Key = tuple[str, UUID]  # (link column, inventory row id)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    released: int = 0
    edges_added: int = 0
    edges_removed: int = 0


def free_name(taken: set[str], wanted: str) -> str:
    """The first of wanted, wanted (2), wanted (3), … not in taken (lower-case names), cut to 160 characters."""
    base = wanted.strip()
    candidate, n = base[:MAX_NAME], 1
    while candidate.lower() in taken:
        n += 1
        suffix = f' ({n})'
        candidate = base[:MAX_NAME - len(suffix)] + suffix
    return candidate


def wanted_nodes(conn, project: UUID) -> dict[Key, dict]:
    """What the inventory says should exist: key → {'name', 'parent': key of the parent node or None}."""
    want: dict[Key, dict] = {}
    for link, table, extra in (('server_id', 'servers', ''), ('environment_id', 'environments', ''),
                               ('service_id', 'services', ''), ('repository_id', 'repositories', 'AND archived_at IS NULL')):
        for row in conn.execute(f'SELECT id,name FROM ka.{table} WHERE project_id=%s {extra} ORDER BY id', (project,)):
            want[(link, row['id'])] = {'name': row['name'], 'parent': None}
    for row in conn.execute("""SELECT d.id,d.server_id,coalesce(nullif(btrim(d.runtime_name),''),s.name) || ' · ' || d.environment AS name
            FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id WHERE d.project_id=%s ORDER BY d.id""", (project,)):
        want[('deployment_id', row['id'])] = {'name': row['name'], 'parent': ('server_id', row['server_id'])}
    return want


def wanted_edges(conn, project: UUID) -> set[tuple[Key, Key, str]]:
    edges: set[tuple[Key, Key, str]] = set()
    for row in conn.execute('SELECT id,service_id FROM ka.service_deployments WHERE project_id=%s', (project,)):
        edges.add((('deployment_id', row['id']), ('service_id', row['service_id']), 'instance_of'))
    for row in conn.execute('SELECT service_id,depends_on_service_id FROM ka.service_dependencies WHERE project_id=%s', (project,)):
        edges.add((('service_id', row['service_id']), ('service_id', row['depends_on_service_id']), 'depends_on'))
    for row in conn.execute("""SELECT l.service_id,l.repository_id FROM ka.service_repositories l
            JOIN ka.repositories r ON r.id=l.repository_id WHERE l.project_id=%s AND r.archived_at IS NULL""", (project,)):
        edges.add((('service_id', row['service_id']), ('repository_id', row['repository_id']), 'uses'))
    for row in conn.execute('SELECT environment_id,server_id FROM ka.environment_servers WHERE project_id=%s', (project,)):
        edges.add((('environment_id', row['environment_id']), ('server_id', row['server_id']), 'includes'))
    for row in conn.execute('SELECT environment_id,service_id FROM ka.environment_services WHERE project_id=%s', (project,)):
        edges.add((('environment_id', row['environment_id']), ('service_id', row['service_id']), 'includes'))
    return edges


def sync_inventory(conn, project: UUID) -> SyncResult:
    result = SyncResult()
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(%s::text))", (project,))
    want = wanted_nodes(conn, project)
    nodes = conn.execute(f'SELECT id,layer,parent_id,name,description,{LINKS} FROM ka.kg_nodes WHERE project_id=%s',
                         (project,)).fetchall()
    stored: dict[Key, dict] = {}
    for node in nodes:
        for link in SOURCES:
            if node[link] is not None:
                stored[(link, node[link])] = node
    taken: dict[tuple[str, UUID | None], set[str]] = {}
    for node in nodes:
        taken.setdefault((node['layer'], node['parent_id']), set()).add(node['name'].lower())

    # 1. Release nodes whose row is gone (deployments first, so their server node has no children left from them).
    gone = sorted((key for key in stored if key not in want), key=lambda key: key[0] != 'deployment_id')
    for key in gone:
        node = stored.pop(key)
        release(conn, node, key[0])
        taken.get((node['layer'], node['parent_id']), set()).discard(node['name'].lower())
        result.released += 1

    # 2. Create and update, parents before children.
    for link in SOURCES:
        layer, kind, _ = SOURCES[link]
        for key, row in want.items():
            if key[0] != link:
                continue
            parent_key = row['parent']
            parent_id = stored[parent_key]['id'] if parent_key in stored else None
            if parent_key is not None and parent_id is None:
                continue  # a deployment whose server has no node cannot be placed; the next sync will
            siblings = taken.setdefault((layer, parent_id), set())
            node = stored.get(key)
            if node is None:
                name = free_name(siblings, row['name'])
                node = conn.execute(f"""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id,{link})
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,layer,parent_id,name,description,{LINKS}""",
                    (project, layer, kind, name, parent_id, key[1])).fetchone()
                stored[key] = node
                siblings.add(name.lower())
                result.created += 1
                continue
            old_siblings = taken.setdefault((layer, node['parent_id']), set())
            old_siblings.discard(node['name'].lower())
            name = free_name(siblings, row['name'])
            if name != node['name'] or parent_id != node['parent_id']:
                node = conn.execute(f"""UPDATE ka.kg_nodes SET name=%s,parent_id=%s,revision=revision+1 WHERE id=%s
                    RETURNING id,layer,parent_id,name,description,{LINKS}""", (name, parent_id, node['id'])).fetchone()
                stored[key] = node
                result.updated += 1
            siblings.add(node['name'].lower())

    # 3. Synced edges: add the missing ones, remove the ones no longer wanted.
    wanted = {(stored[a]['id'], stored[b]['id'], kind) for a, b, kind in wanted_edges(conn, project)
              if a in stored and b in stored and a != b}
    # Every edge counts as present (a hand-made edge with the same ends and type stays hand-made and is never touched);
    # only synced edges can be stale. No statement runs when nothing changed, so the graph version stays put.
    current = {(row['from_id'], row['to_id'], row['type']): row for row in conn.execute(
        'SELECT id,from_id,to_id,type,synced FROM ka.kg_edges WHERE project_id=%s', (project,))}
    for from_id, to_id, kind in sorted(wanted - set(current), key=str):
        conn.execute('INSERT INTO ka.kg_edges(project_id,from_id,to_id,type,synced) VALUES (%s,%s,%s,%s,true)',
                     (project, from_id, to_id, kind))
        result.edges_added += 1
    stale = [row['id'] for key, row in current.items() if row['synced'] and key not in wanted]
    if stale:
        conn.execute('DELETE FROM ka.kg_edges WHERE id=ANY(%s)', (stale,))
        result.edges_removed += len(stale)
    return result


def release(conn, node: dict, link: str) -> None:
    """The node's row is gone: its synced edges go; the node goes too unless something hand-made hangs off it."""
    conn.execute('DELETE FROM ka.kg_edges WHERE synced AND (from_id=%s OR to_id=%s)', (node['id'], node['id']))
    kept = conn.execute("""SELECT EXISTS (SELECT 1 FROM ka.kg_nodes WHERE parent_id=%s)
        OR EXISTS (SELECT 1 FROM ka.kg_node_documents WHERE node_id=%s) AS kept""", (node['id'], node['id'])).fetchone()['kept']
    if not kept:
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (node['id'],))
        return
    note = f'Removed from {SOURCES[link][2]} on {date.today():%Y-%m-%d}.'
    description = (node['description'] + '\n' if node['description'] else '') + note
    conn.execute(f'UPDATE ka.kg_nodes SET {link}=NULL,description=%s,revision=revision+1 WHERE id=%s',
                 (description[-MAX_DESCRIPTION:], node['id']))


def sync_all(store: RepositoryStore, apps: list[str]) -> None:
    """Start-up: bring every app's graph in step with its inventory; a failure is logged and never stops the service."""
    for app in apps:
        try:
            with store.connect() as conn:
                sync_inventory(conn, store.project(conn, app))
        except Exception:  # noqa: BLE001 - a start-up convenience must never take the service down
            logger.exception('Graph inventory sync failed for %s', app)
```

Then call it — always as the last database statement of the transaction that changed the inventory, before the audit
row or the return, with that transaction's `conn` and `project_id`:
- `service_api.py`: in `save()` after the deployments/dependencies writes; in `delete_service()` after
  `DELETE FROM ka.services`; in `save_server()` after its insert/update; in `delete_server()` after
  `DELETE FROM ka.servers`.
- `environments.py`: in `save()` after the membership writes; in `delete_environment()` after `DELETE FROM ka.environments`.
- `repositories.py` `RepositoryStore.save()`: after the repository insert/update (inside its `with self.connect()`).
- `repository_api.py` `archive_project()`: after `UPDATE ka.repositories SET archived_at=now()`.
- `app.py` `lifespan`: start `asyncio.create_task(asyncio.to_thread(graph_sync.sync_all, repository_store, [a.name for a in cfg.apps]))`
  when `repository_store` is set (like the document export backfill), and cancel/await it on shutdown the same way.

Import with `from knowledge_admin import graph_sync` and call `graph_sync.sync_inventory(conn, project_id)`.

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_sync.py tests/test_graph_inventory_sync_api.py tests/test_graph_inventory_sync_migration.py tests/test_system_graph_api.py tests/test_services.py tests/test_servers.py tests/test_environments.py tests/test_service_environments.py tests/test_service_dependencies.py tests/test_service_dependency_api.py tests/test_repositories.py < /dev/null`
Expected: PASS except the known `test_environments` failure. Then the whole suite once (`uv run pytest -q < /dev/null`):
only the known failures. Any test that deletes an inventory row with plain SQL after an API call created its node must
now call `graph_sync.sync_inventory` in the same transaction (or delete through the API) — fix the test that way, never
by removing assertions.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/graph_sync.py services/admin-api/src/knowledge_admin/service_api.py \
  services/admin-api/src/knowledge_admin/environments.py services/admin-api/src/knowledge_admin/repositories.py \
  services/admin-api/src/knowledge_admin/repository_api.py services/admin-api/src/knowledge_admin/app.py \
  services/admin-api/tests/
git commit -m "feat(api): inventory writes reconcile the system graph in their own transaction (graph_sync)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `graph_memory.py` — the graph in memory (networkx)

**Files:**
- Modify: `services/admin-api/pyproject.toml`, `services/admin-api/uv.lock` (`uv add "networkx==3.5"`; if that exact
  version is unavailable, the newest 3.x, pinned with `==`)
- Create: `services/admin-api/src/knowledge_admin/graph_memory.py`
- Modify: `services/admin-api/src/knowledge_admin/system_graph_api.py` (view and search read the snapshot)
- Create: `services/admin-api/tests/test_graph_memory.py`

**Interfaces:**
- Produces: `GraphMemory().get(conn, project) -> Snapshot`; `Snapshot(version: int, graph: networkx.MultiDiGraph,
  documents: dict)` with `nodes() -> list[dict]` (keys `id, parent_id, layer, kind, name, description, source_kind`) and
  `edges() -> list[dict]` (keys `id, from_id, to_id, type, synced`, ordered by `str(id)`), and
  `search(q: str, layer: str | None, limit: int) -> list[dict]` (keys `id, layer, kind, name, path`).

- [ ] **Step 1: Write the failing tests** `services/admin-api/tests/test_graph_memory.py`

```python
"""The graph in memory: built from the database, rebuilt whole when the stored version moves."""
import psycopg

from knowledge_admin.graph_memory import GraphMemory


def add(conn, project, name, layer='software', parent=None, kind='service'):
    return conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id) VALUES (%s,%s,%s,%s,%s)
        RETURNING id""", (project, layer, kind, name, parent)).fetchone()['id']


def test_builds_from_the_database_and_keeps_the_snapshot_while_nothing_changes(store):
    memory = GraphMemory()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api = add(conn, project, 'admin-api')
        events = add(conn, project, 'Event processor', parent=api, kind='module')
        worker = add(conn, project, 'worker')
        edge = conn.execute("INSERT INTO ka.kg_edges(project_id,from_id,to_id,type) VALUES (%s,%s,%s,'calls') RETURNING id",
                            (project, events, worker)).fetchone()['id']
    with store.connect() as conn:
        first = memory.get(conn, project)
        assert memory.get(conn, project) is first  # same version, same object
    assert {row['name'] for row in first.nodes()} == {'admin-api', 'Event processor', 'worker'}
    assert first.edges() == [{'id': edge, 'from_id': events, 'to_id': worker, 'type': 'calls', 'synced': False}]
    assert first.graph.has_edge(events, worker)
    assert [row['name'] for row in first.search('EVENT', None, 50)] == ['Event processor']
    assert first.search('event', None, 50)[0]['path'] == ['admin-api']


def test_views_follow_writes(store):
    memory = GraphMemory()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        add(conn, project, 'admin-api')
    with store.connect() as conn:
        before = memory.get(conn, project)
    with store.connect() as conn:
        add(conn, project, 'worker')
    with store.connect() as conn:
        after = memory.get(conn, project)
    assert after is not before and after.version > before.version
    assert {row['name'] for row in after.nodes()} == {'admin-api', 'worker'}


def test_another_connection_sees_the_new_version(store):
    one, two = GraphMemory(), GraphMemory()  # two processes
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        add(conn, project, 'a')
        one.get(conn, project), two.get(conn, project)
    with store.connect() as conn:
        add(conn, project, 'b')
    with store.connect() as conn:
        assert {row['name'] for row in two.get(conn, project).nodes()} == {'a', 'b'}


def test_search_is_literal_and_limited(store):
    memory = GraphMemory()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        add(conn, project, '100% done')
        add(conn, project, '1000 done')
        add(conn, project, 'Events table', layer='data', kind='table')
        snapshot = memory.get(conn, project)
    assert [row['name'] for row in snapshot.search('%', None, 50)] == ['100% done']
    assert [row['name'] for row in snapshot.search('', 'data', 50)] == ['Events table']
    assert len(snapshot.search('', None, 2)) == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_memory.py < /dev/null` — FAIL (module missing).

- [ ] **Step 3: Add networkx and write `graph_memory.py`**

Run `cd services/admin-api && uv add "networkx==3.5"` (see Files for the fallback).

```python
"""The system graph in memory. Each process keeps one networkx MultiDiGraph per project and rebuilds it whole when the
stored graph version (ka.kg_graph_version, bumped by every graph statement) changes; the old snapshot is dropped. Views
and search read the snapshot; writes go to PostgreSQL first, so the next read sees them."""
import threading
from dataclasses import dataclass
from uuid import UUID

import networkx as nx

SOURCE_KIND = """CASE WHEN server_id IS NOT NULL THEN 'server' WHEN deployment_id IS NOT NULL THEN 'deployment'
    WHEN environment_id IS NOT NULL THEN 'environment' WHEN service_id IS NOT NULL THEN 'service'
    WHEN repository_id IS NOT NULL THEN 'repository' END AS source_kind"""
NODE_FIELDS = ('parent_id', 'layer', 'kind', 'name', 'description', 'source_kind')


@dataclass(frozen=True)
class Snapshot:
    version: int
    graph: nx.MultiDiGraph
    documents: dict

    def nodes(self) -> list[dict]:
        return [{'id': node, **{key: data[key] for key in NODE_FIELDS}} for node, data in self.graph.nodes(data=True)]

    def edges(self) -> list[dict]:
        rows = [{'id': key, 'from_id': a, 'to_id': b, 'type': data['type'], 'synced': data['synced']}
                for a, b, key, data in self.graph.edges(keys=True, data=True)]
        return sorted(rows, key=lambda row: str(row['id']))

    def search(self, q: str, layer: str | None, limit: int) -> list[dict]:
        needle = q.strip().lower()
        hits = [(node, data) for node, data in self.graph.nodes(data=True)
                if (layer is None or data['layer'] == layer) and needle in data['name'].lower()]
        hits.sort(key=lambda hit: (hit[1]['name'].lower(), str(hit[0])))
        return [{'id': node, 'layer': data['layer'], 'kind': data['kind'], 'name': data['name'], 'path': self.path(node)}
                for node, data in hits[:limit]]

    def path(self, node) -> list[str]:
        names, parent = [], self.graph.nodes[node]['parent_id']
        while parent is not None and parent in self.graph:
            names.append(self.graph.nodes[parent]['name'])
            parent = self.graph.nodes[parent]['parent_id']
        return names[::-1]


def load(conn, project: UUID, version: int) -> Snapshot:
    graph = nx.MultiDiGraph()
    for row in conn.execute(f'SELECT id,parent_id,layer,kind,name,description,{SOURCE_KIND} FROM ka.kg_nodes WHERE project_id=%s',
                            (project,)):
        graph.add_node(row['id'], **{key: row[key] for key in NODE_FIELDS})
    for row in conn.execute('SELECT id,from_id,to_id,type,synced FROM ka.kg_edges WHERE project_id=%s', (project,)):
        if row['from_id'] in graph and row['to_id'] in graph:
            graph.add_edge(row['from_id'], row['to_id'], key=row['id'], type=row['type'], synced=row['synced'])
    documents: dict = {}
    for row in conn.execute("""SELECT l.node_id,d.id,d.slug,d.title FROM ka.kg_node_documents l
            JOIN ka.document_details d ON d.id=l.document_id WHERE l.project_id=%s ORDER BY lower(d.title),d.id""", (project,)):
        documents.setdefault(row['node_id'], []).append({'id': row['id'], 'slug': row['slug'], 'title': row['title']})
    return Snapshot(version, graph, documents)


class GraphMemory:
    def __init__(self) -> None:
        self._snapshots: dict[UUID, Snapshot] = {}
        self._lock = threading.Lock()

    def get(self, conn, project: UUID) -> Snapshot:
        version = conn.execute('SELECT version FROM ka.kg_graph_version').fetchone()['version']
        current = self._snapshots.get(project)
        if current is not None and current.version == version:
            return current
        with self._lock:  # one rebuild at a time; the others then find it ready
            current = self._snapshots.get(project)
            if current is None or current.version != version:
                current = self._snapshots[project] = load(conn, project, version)
            return current
```

In `system_graph_api.py`:
- create one `memory = GraphMemory()` inside `build_api` (one per process);
- `view()`: replace the three queries with `snapshot = memory.get(conn, project)` and call
  `system_graph.build_view(snapshot.nodes(), snapshot.edges(), snapshot.documents, layer, root, depth)`;
- `search()`: keep the NUL check and validation; replace both queries and the local `path()` with
  `return {'nodes': memory.get(conn, project).search(q, layer, limit)}`;
- remove `SOURCE_KIND` from `system_graph_api.py` if it is now unused there (it lives in `graph_memory.py`), and the
  `like` import if unused. `documents_of` stays for node detail.

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_memory.py tests/test_system_graph_api.py tests/test_graph_inventory_sync_api.py tests/test_system_graph.py < /dev/null`
Expected: PASS (the API tests' view and search expectations are unchanged).

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/pyproject.toml services/admin-api/uv.lock services/admin-api/src/knowledge_admin/graph_memory.py \
  services/admin-api/src/knowledge_admin/system_graph_api.py services/admin-api/tests/test_graph_memory.py
git commit -m "feat(api): System map views and search read an in-memory networkx graph rebuilt when the graph changes

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: UI — source line, read-only fields, synced badges; user guide

**Files:**
- Modify: `services/admin-ui/app/lib/systemGraph.ts`, `services/admin-ui/app/components/NodeSheet.tsx`,
  `services/admin-ui/app/components/SystemMapView.tsx`, `docs/user-guide.md`
- Test: `services/admin-ui/app/components/__tests__/nodeSheet.test.tsx`, `__tests__/systemMap.test.tsx`

**Interfaces:**
- Consumes: fields from commit 448572f — `source` on node detail, `synced` on edges, `source_kind` on view nodes and boundary nodes; edge type `includes`.

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
  place follow the inventory, and their synced edges are read-only. Archiving a code project counts as deleting it. You can still give them a description and
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

---

## Final check

- Backend: `cd services/admin-api && uv run pytest -q < /dev/null` — only the known failures listed in Global Constraints.
- `sh db/tests/run.sh` exits 0.
- UI: `cd services/admin-ui && npx vitest run && npx tsc --noEmit`.
