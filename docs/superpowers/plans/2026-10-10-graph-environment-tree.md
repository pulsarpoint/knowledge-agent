# Environments contain their servers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The infrastructure layer becomes a tree — Environment › Server › Deployment — with one server node per
environment the server is in (and a top-level node when it is in none or has deployments without an environment).

**Architecture:** Migration 0094 lets a node carry `server_id` together with `environment_id` and makes "one node per
(server, environment)" the unique rule. `graph_sync` computes server placements, creates environment nodes before server
copies, moves an unwanted copy to a missing placement of the same server instead of releasing it, and drops the
environment → server `includes` edges.

**Tech Stack:** PostgreSQL 17, FastAPI + psycopg3.

**Spec:** `docs/superpowers/specs/2026-10-10-graph-inventory-sync-design.md`, section "Revision 3".

## Global Constraints

- Work only in the worktree `.claude/worktrees/graph-environment-tree` (branch `feature/graph-environment-tree`). Never
  run make targets; never touch `.env`, `.local` or the running Docker stack's database.
- Never `git stash`; never `git add .superpowers`. Every commit ends with exactly
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd services/admin-api && uv run pytest -q tests/<file> < /dev/null` (always `< /dev/null`). Known
  failures to ignore: the 5 code-graph tests (no `services/code-graph/node_modules` in a worktree),
  `test_deploy.py::test_docs_commands_serve_the_docs_folder_inside_the_data_root`,
  `test_environments.py::test_migration_reapply_and_populated_rollback_protection`.
- Placement rule (exact): a server is placed under environment E when `(server, E)` is in `environment_servers` or a
  deployment of that server has `environment_id = E`; it is placed at the top level (environment NULL) when it has no
  placement under any environment or has a deployment with `environment_id IS NULL`.
- Delete notes: a copy whose server row is gone → `Removed from Servers on <YYYY-MM-DD>.`; a copy whose server still
  exists but is no longer placed there → `Removed from Environments on <YYYY-MM-DD>.`
- A server copy shows source "Servers" (the API already prefers `server_id` over `environment_id`; keep it so).

## Review Focus

1. Upgrading an existing graph: each existing one-per-server node keeps its id, children and documents and ends up under
   the server's first environment (alphabetical); a second sync changes nothing (Task 2
   `test_an_existing_server_node_moves_into_its_environment`).
2. A server in two environments gets two nodes and each holds only that environment's deployments (Task 2
   `test_a_server_in_two_environments_has_a_node_in_each`).
3. No inventory save fails because of the tree (sibling names, unique pair index) — the services-page API test still
   passes with deployments in two environments (Task 2).

---

### Task 1: Migration 0094 — server nodes per environment

**Files:**
- Create: `db/migrations/0094_graph_environment_tree.up.sql`, `db/migrations/0094_graph_environment_tree.down.sql`
- Create: `services/admin-api/tests/test_graph_environment_tree_migration.py`
- Modify: `db/tests/run.sh`, `docs/migrations.md`; `services/admin-api/tests/test_graph_inventory_sync_migration.py` and
  `test_system_graph_migration.py` if their down/up tests step past 0094 (run 0094 down first, 0094 up last)

**Interfaces:**
- Produces: a node may have `server_id` + `environment_id` together; unique index `kg_nodes_by_server` on
  `(server_id, environment_id) NULLS NOT DISTINCT WHERE server_id IS NOT NULL`; `kg_nodes_by_environment` on
  `(environment_id) WHERE environment_id IS NOT NULL AND server_id IS NULL`.

- [ ] **Step 1: Write the failing test** `services/admin-api/tests/test_graph_environment_tree_migration.py`

```python
"""0094: a server can have one graph node per environment (server_id + environment_id)."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def rows(conn, project):
    server = conn.execute("INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'prod-1',%s,false) RETURNING id",
                          (project, f'{uuid4().hex[:8]}.internal')).fetchone()['id']
    production = conn.execute("INSERT INTO ka.environments(project_id,name) VALUES (%s,'production') RETURNING id", (project,)).fetchone()['id']
    staging = conn.execute("INSERT INTO ka.environments(project_id,name) VALUES (%s,'staging') RETURNING id", (project,)).fetchone()['id']
    return server, production, staging


def add(conn, project, name, **links):
    columns = ','.join(['project_id', 'layer', 'kind', 'name', *links])
    values = (project, links.pop('layer', 'infrastructure'), 'server', name, *links.values())
    return conn.execute(f"INSERT INTO ka.kg_nodes({columns}) VALUES ({','.join(['%s'] * len(values))}) RETURNING id", values).fetchone()['id']


def test_a_server_has_one_node_per_environment(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        server, production, staging = rows(conn, project)
        add(conn, project, 'prod-1', server_id=server)                            # top level
        add(conn, project, 'prod-1 (p)', server_id=server, environment_id=production)
        add(conn, project, 'prod-1 (s)', server_id=server, environment_id=staging)
        add(conn, project, 'production', environment_id=production)               # the environment's own node
    for links, error in [({'server_id': server}, psycopg.errors.UniqueViolation),                       # second top-level copy
                         ({'server_id': server, 'environment_id': production}, psycopg.errors.UniqueViolation),
                         ({'environment_id': production}, psycopg.errors.UniqueViolation),               # second env node
                         ({'server_id': server, 'service_id': uuid4()}, psycopg.errors.CheckViolation)]:  # other pairs refused
        with pytest.raises(error), store.connect() as conn:
            add(conn, project, f'x-{uuid4().hex[:6]}', **links)


def test_down_and_up(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        server, production, _ = rows(conn, project)
        top = add(conn, project, 'prod-1', server_id=server)
        copy = add(conn, project, 'prod-1 (2)', server_id=server, environment_id=production)
        run(conn, '0094_graph_environment_tree.down.sql')
        links = {row['id']: (row['server_id'], row['environment_id']) for row in
                 conn.execute('SELECT id,server_id,environment_id FROM ka.kg_nodes').fetchall()}
        assert links == {top: (server, None), copy: (None, None)}  # copies with an environment are unlinked, kept
        run(conn, '0094_graph_environment_tree.up.sql')
```

- [ ] **Step 2: Run to verify it fails** — `cd services/admin-api && uv run pytest -q tests/test_graph_environment_tree_migration.py < /dev/null` → FAIL.

- [ ] **Step 3: Write the migration**

`db/migrations/0094_graph_environment_tree.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- Environments contain their servers in the infrastructure graph. A server gets one node per environment it is in
-- (server_id + environment_id), and a top-level node (environment_id NULL) when it is in none.
ALTER TABLE kg_nodes
    DROP CONSTRAINT kg_nodes_one_source,
    ADD CONSTRAINT kg_nodes_one_source CHECK (
        num_nonnulls(server_id, deployment_id, environment_id, service_id, repository_id) <= 1
        OR (num_nonnulls(server_id, deployment_id, environment_id, service_id, repository_id) = 2
            AND server_id IS NOT NULL AND environment_id IS NOT NULL));
DROP INDEX kg_nodes_by_server;
CREATE UNIQUE INDEX kg_nodes_by_server ON kg_nodes (server_id, environment_id) NULLS NOT DISTINCT WHERE server_id IS NOT NULL;
DROP INDEX kg_nodes_by_environment;
CREATE UNIQUE INDEX kg_nodes_by_environment ON kg_nodes (environment_id) WHERE environment_id IS NOT NULL AND server_id IS NULL;

COMMIT;
```

`db/migrations/0094_graph_environment_tree.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Copies of a server inside an environment become hand-made nodes again; the next sync makes one node per server.
UPDATE kg_nodes SET server_id = NULL, environment_id = NULL WHERE server_id IS NOT NULL AND environment_id IS NOT NULL;
DROP INDEX kg_nodes_by_environment;
CREATE UNIQUE INDEX kg_nodes_by_environment ON kg_nodes (environment_id) WHERE environment_id IS NOT NULL;
DROP INDEX kg_nodes_by_server;
CREATE UNIQUE INDEX kg_nodes_by_server ON kg_nodes (server_id) WHERE server_id IS NOT NULL;
ALTER TABLE kg_nodes
    DROP CONSTRAINT kg_nodes_one_source,
    ADD CONSTRAINT kg_nodes_one_source CHECK (num_nonnulls(server_id, deployment_id, environment_id, service_id, repository_id) <= 1);

COMMIT;
```

`db/tests/run.sh`: right after the second `run_migrate postgres up >> "$test_log" …` line add a 0094 `down 1` with a
check that `pg_get_indexdef('ka.kg_nodes_by_server'::regclass)` no longer mentions `environment_id`; after the line
`printf '%s\n' 'PASS: inventory rows sync into the system graph.'` add the up check (the index mentions
`environment_id`) printing `PASS: environments contain their servers in the system graph.` — same shape as the 0093 lines.

`docs/migrations.md`, after the `## 0093_graph_inventory_sync` section:

```markdown
## 0094_graph_environment_tree

Environments contain their servers in the system graph: a server has one linked node per environment it is in (both
`server_id` and `environment_id` set) and a top-level node when it is in none (`environment_id` NULL). The unique rule
becomes one node per (server, environment) pair; an environment's own node is the only node with `environment_id` and no
`server_id`. The down migration turns the per-environment copies into hand-made nodes and restores one node per server.
```

- [ ] **Step 4: Run tests** — `cd services/admin-api && uv run pytest -q tests/test_graph_environment_tree_migration.py tests/test_graph_inventory_sync_migration.py tests/test_system_graph_migration.py < /dev/null` → PASS (adapt only the down/up order in the two older files if needed). Then `sh db/tests/run.sh` from the worktree root → exit 0. (`test_graph_sync.py` still passes: no node uses the pair yet.)

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0094_graph_environment_tree.up.sql db/migrations/0094_graph_environment_tree.down.sql db/tests/run.sh \
  docs/migrations.md services/admin-api/tests/
git commit -m "feat(db): a server can have one graph node per environment (0094)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `graph_sync` builds the Environment › Server › Deployment tree

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/graph_sync.py`
- Modify: `services/admin-api/tests/test_graph_sync.py`, `services/admin-api/tests/test_graph_inventory_sync_api.py`
- Modify: `docs/user-guide.md` (§4.20 "From the inventory" bullet)

**Interfaces:**
- Consumes: Task 1 schema.
- Produces: the same `sync_inventory(conn, project) -> SyncResult` and `sync_all`, with the new placement rule.

- [ ] **Step 1: Write the failing tests.** Add to `tests/test_graph_sync.py` (reuse its helpers `server`, `service`,
  `environment`, `deployment`, `node`, `edges`, `sync`; add the two below if missing):

```python
def member(conn, project, environment_id, server_id):
    conn.execute('INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                 (project, environment_id, server_id))


def copy(conn, server_id, environment_id):
    return conn.execute('SELECT * FROM ka.kg_nodes WHERE server_id=%s AND environment_id IS NOT DISTINCT FROM %s',
                        (server_id, environment_id)).fetchone()


def test_environments_hold_their_servers_and_servers_their_deployments(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prod, api = environment(conn, project, 'production'), service(conn, project, 'admin-api')
        host, spare = server(conn, project, 'prod-1'), server(conn, project, 'spare')
        member(conn, project, prod['id'], host['id'])
        made = conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id)
            VALUES (%s,%s,%s,'production',%s) RETURNING id""", (project, api['id'], host['id'], prod['id'])).fetchone()
        sync(conn, project)
        top = {row['name'] for row in conn.execute(
            "SELECT name FROM ka.kg_nodes WHERE layer='infrastructure' AND parent_id IS NULL").fetchall()}
        assert top == {'production', 'spare'}
        placed = copy(conn, host['id'], prod['id'])
        assert (placed['name'], placed['parent_id']) == ('prod-1', node(conn, 'environment_id', prod['id'])['id'])
        assert copy(conn, host['id'], None) is None
        assert node(conn, 'deployment_id', made['id'])['parent_id'] == placed['id']
        assert not any(kind == 'includes' and b == 'prod-1' for _, b, kind, _ in edges(conn))
        assert sync(conn, project) == SyncResult()


def test_a_server_in_two_environments_has_a_node_in_each(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prod, staging = environment(conn, project, 'production'), environment(conn, project, 'staging')
        api, host = service(conn, project), server(conn, project)
        member(conn, project, prod['id'], host['id'])
        made = conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id)
            VALUES (%s,%s,%s,'staging',%s) RETURNING id""", (project, api['id'], host['id'], staging['id'])).fetchone()
        sync(conn, project)
        in_prod, in_staging = copy(conn, host['id'], prod['id']), copy(conn, host['id'], staging['id'])
        assert in_prod['id'] != in_staging['id']  # staging holds it through the deployment, production through membership
        assert node(conn, 'deployment_id', made['id'])['parent_id'] == in_staging['id']
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes WHERE parent_id=%s', (in_prod['id'],)).fetchone()['n'] == 0


def test_a_deployment_without_an_environment_sits_under_the_top_level_copy(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prod, api, host = environment(conn, project, 'production'), service(conn, project), server(conn, project)
        member(conn, project, prod['id'], host['id'])
        legacy = deployment(conn, project, api['id'], host['id'], environment='legacy')  # environment_id NULL
        sync(conn, project)
        top = copy(conn, host['id'], None)
        assert top is not None and top['parent_id'] is None and copy(conn, host['id'], prod['id']) is not None
        assert node(conn, 'deployment_id', legacy['id'])['parent_id'] == top['id']


def test_removing_a_server_from_an_environment_releases_that_copy(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prod, staging, host = environment(conn, project, 'production'), environment(conn, project, 'staging'), server(conn, project)
        member(conn, project, prod['id'], host['id'])
        member(conn, project, staging['id'], host['id'])
        sync(conn, project)
        kept = copy(conn, host['id'], staging['id'])
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id)
            VALUES (%s,'infrastructure','disk','disk',%s)""", (project, kept['id']))
        conn.execute('DELETE FROM ka.environment_servers WHERE environment_id=%s', (staging['id'],))
        sync(conn, project)
        after = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (kept['id'],)).fetchone()
        assert (after['server_id'], after['environment_id']) == (None, None)
        assert 'Removed from Environments on ' in after['description']
        assert copy(conn, host['id'], prod['id']) is not None


def test_an_existing_server_node_moves_into_its_environment(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        staging, production, host = environment(conn, project, 'staging'), environment(conn, project, 'production'), server(conn, project)
        old = conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id)
            VALUES (%s,'infrastructure','server','prod-1',%s) RETURNING id""", (project, host['id'])).fetchone()['id']  # 0093 era
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id)
            VALUES (%s,'infrastructure','disk','disk',%s)""", (project, old))
        member(conn, project, staging['id'], host['id'])
        member(conn, project, production['id'], host['id'])
        sync(conn, project)
        moved = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (old,)).fetchone()
        assert moved['environment_id'] == production['id']  # the first environment by name
        assert moved['parent_id'] == node(conn, 'environment_id', production['id'])['id']
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes WHERE parent_id=%s', (old,)).fetchone()['n'] == 1
        assert sync(conn, project) == SyncResult()


def test_moving_a_server_to_another_environment_keeps_its_node(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a, b, host = environment(conn, project, 'a'), environment(conn, project, 'b'), server(conn, project)
        member(conn, project, a['id'], host['id'])
        sync(conn, project)
        before = copy(conn, host['id'], a['id'])['id']
        conn.execute('DELETE FROM ka.environment_servers WHERE server_id=%s', (host['id'],))
        member(conn, project, b['id'], host['id'])
        sync(conn, project)
        assert copy(conn, host['id'], b['id'])['id'] == before and copy(conn, host['id'], a['id']) is None


def test_renaming_or_deleting_a_server_reaches_every_copy(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a, b, host = environment(conn, project, 'a'), environment(conn, project, 'b'), server(conn, project)
        member(conn, project, a['id'], host['id'])
        member(conn, project, b['id'], host['id'])
        sync(conn, project)
        conn.execute("UPDATE ka.servers SET name='prod-01' WHERE id=%s", (host['id'],))
        sync(conn, project)
        assert {copy(conn, host['id'], e['id'])['name'] for e in (a, b)} == {'prod-01'}
        conn.execute('DELETE FROM ka.environment_servers WHERE server_id=%s', (host['id'],))
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))
        sync(conn, project)
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes WHERE layer=%s AND kind=%s', ('infrastructure', 'server')).fetchone()['n'] == 0
```

  Update the existing expectations that encoded the flat layout, keeping their intent:
  - `test_graph_sync.py`: the `('production', 'prod-1', 'includes', True)` edge no longer exists — assert instead that
    the server's node is a child of the environment's node (`copy(conn, host['id'], prod['id'])['parent_id'] ==
    node(conn, 'environment_id', prod['id'])['id']`); environment → service `includes` stays as is. In tests that used
    `node(conn, 'server_id', …)` for a server that is in an environment, use `copy(…)`.
  - `test_graph_inventory_sync_api.py`: the infrastructure top level now holds environments and servers in no
    environment; servers in an environment and their deployments are one and two levels down. Rewrite those checks with
    `nodes(client, 'infrastructure', <root id>)` walking down the tree (e.g. production › prod-1 › `api · production`),
    and keep every other check.

- [ ] **Step 2: Run to verify the new tests fail** — `cd services/admin-api && uv run pytest -q tests/test_graph_sync.py < /dev/null`.

- [ ] **Step 3: Change `graph_sync.py`**

Replace `SOURCES`, `Key`, `wanted_nodes`, the environment → server part of `wanted_edges`, and the matching parts of
`sync_inventory` and `release`:

```python
# link column → (layer, kind, label in the delete note); creation order = this order (environments before the server
# copies they hold, servers before deployments).
SOURCES = {
    'environment_id': ('infrastructure', 'environment', 'Environments'),
    'server_id': ('infrastructure', 'server', 'Servers'),
    'service_id': ('software', 'service', 'Services'),
    'repository_id': ('software', 'repository', 'Code projects'),
    'deployment_id': ('infrastructure', 'instance', 'Services (deployment)'),
}
LINKS = ','.join(SOURCES)

# (link column, row id); a server copy's id is (server id, environment id or None).
Key = tuple[str, object]


def key_of(node: dict) -> Key | None:
    """The inventory key a stored node is linked to (a server copy also carries its environment)."""
    if node['server_id'] is not None:
        return ('server_id', (node['server_id'], node['environment_id']))
    for link in SOURCES:
        if node[link] is not None:
            return (link, node[link])
    return None


def link_values(key: Key) -> dict:
    link, ident = key
    if link == 'server_id':
        return {'server_id': ident[0], 'environment_id': ident[1]}
    return {link: ident}


def wanted_nodes(conn, project: UUID) -> dict[Key, dict]:
    """What the inventory says should exist: key → {'name', 'parent': key of the parent node or None}."""
    want: dict[Key, dict] = {}
    for link, table, extra in (('environment_id', 'environments', ''), ('service_id', 'services', ''),
                               ('repository_id', 'repositories', 'AND archived_at IS NULL')):
        for row in conn.execute(f'SELECT id,name FROM ka.{table} WHERE project_id=%s {extra} ORDER BY id', (project,)):
            want[(link, row['id'])] = {'name': row['name'], 'parent': None}
    servers = {row['id']: row['name'] for row in conn.execute('SELECT id,name FROM ka.servers WHERE project_id=%s', (project,))}
    deployments = conn.execute("""SELECT d.id,d.server_id,d.environment_id,
            coalesce(nullif(btrim(d.runtime_name),''),s.name) || ' · ' || coalesce(e.name,d.environment) AS name
            FROM ka.service_deployments d JOIN ka.services s ON s.id=d.service_id
            LEFT JOIN ka.environments e ON e.id=d.environment_id WHERE d.project_id=%s ORDER BY d.id""", (project,)).fetchall()
    # Where each server is placed: under every environment it is a member of or deployed in; at the top level when it is
    # in no environment or has a deployment without one.
    placements = {(row['server_id'], row['environment_id']) for row in conn.execute(
        'SELECT server_id,environment_id FROM ka.environment_servers WHERE project_id=%s', (project,))}
    placements |= {(row['server_id'], row['environment_id']) for row in deployments}
    placed = {server for server, _ in placements}
    placements |= {(server, None) for server in servers if server not in placed}
    for server, environment in sorted(placements, key=str):
        want[('server_id', (server, environment))] = {
            'name': servers[server], 'parent': ('environment_id', environment) if environment else None}
    for row in deployments:
        want[('deployment_id', row['id'])] = {'name': row['name'],
                                              'parent': ('server_id', (row['server_id'], row['environment_id']))}
    return want
```

In `wanted_edges` delete the `environment_servers` loop (environment → server `includes`); keep the
`environment_services` loop.

In `sync_inventory`:
- build `stored` with `key_of`: `stored = {key: node for node in nodes if (key := key_of(node)) is not None}`;
- **before** step 1 (release), move unwanted server copies to missing placements of the same server:

```python
    # A server copy that is no longer wanted where it is moves to a placement of the same server that has no node yet
    # (the first environment by name), keeping its children and documents, instead of being released while a new one
    # is made. This also moves each one-per-server node from before 0094 into the server's first environment.
    names = {key[1]: row['name'].lower() for key, row in want.items() if key[0] == 'environment_id'}
    missing: dict[UUID, list[Key]] = {}
    for key in want:
        if key[0] == 'server_id' and key not in stored:
            missing.setdefault(key[1][0], []).append(key)
    for key in [key for key in stored if key[0] == 'server_id' and key not in want]:
        options = sorted(missing.get(key[1][0], []), key=lambda k: (names.get(k[1][1], ''), str(k[1][1])))
        if not options:
            continue
        target = options[0]
        missing[key[1][0]].remove(target)
        stored[target] = conn.execute(f"""UPDATE ka.kg_nodes SET environment_id=%s,revision=revision+1 WHERE id=%s
            RETURNING id,layer,parent_id,name,description,{LINKS}""", (target[1][1], stored.pop(key)['id'])).fetchone()
        result.updated += 1
```

- step 1 (release): pass a label — for a server copy, `'Servers'` when the server row is gone (no wanted key has that
  server id) and `'Environments'` otherwise; for other links `SOURCES[link][2]`:

```python
    wanted_servers = {key[1][0] for key in want if key[0] == 'server_id'}
    ...
        label = ('Environments' if key[0] == 'server_id' and key[1][0] in wanted_servers else SOURCES[key[0]][2])
        if release(conn, node, key, label):
```

- step 2 (create): insert with every link column of the key:

```python
                values = link_values(key)
                node = conn.execute(f"""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id,{','.join(values)})
                    VALUES (%s,%s,%s,%s,%s,{','.join(['%s'] * len(values))}) RETURNING id,layer,parent_id,name,description,{LINKS}""",
                    (project, layer, kind, name, parent_id, *values.values())).fetchone()
```

`release(conn, node, key, label)` clears every link column of the key (a server copy clears `server_id` and
`environment_id`) and writes `Removed from {label} on …`:

```python
def release(conn, node: dict, key: Key, label: str) -> bool:
    """The node's row is gone: its synced edges go; the node goes too unless something hand-made hangs off it."""
    conn.execute('DELETE FROM ka.kg_edges WHERE synced AND (from_id=%s OR to_id=%s)', (node['id'], node['id']))
    kept = conn.execute("""SELECT EXISTS (SELECT 1 FROM ka.kg_nodes WHERE parent_id=%s)
        OR EXISTS (SELECT 1 FROM ka.kg_node_documents WHERE node_id=%s) AS kept""", (node['id'], node['id'])).fetchone()['kept']
    if not kept:
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (node['id'],))
        return True
    note = f'Removed from {label} on {date.today():%Y-%m-%d}.'
    description = (node['description'] + '\n' if node['description'] else '') + note
    cleared = ','.join(f'{column}=NULL' for column in link_values(key))
    conn.execute(f'UPDATE ka.kg_nodes SET {cleared},description=%s,revision=revision+1 WHERE id=%s',
                 (description[-MAX_DESCRIPTION:], node['id']))
    return False
```

Everything else in `sync_inventory` (parking pre-pass, step 2 updates, step 3 edges) works on keys and stays.

`docs/user-guide.md` §4.20, "From the inventory" bullet: replace the sentence describing servers and deployments with:
"Infrastructure is a tree: each environment holds its servers and each server holds its deployments
(`<runtime or service> · <environment>`); a server in several environments appears under each of them, and a server in
no environment (or with deployments without one) sits at the top level." Keep the rest of the bullet; remove "an
environment *includes* its servers and services" in favour of "an environment *includes* its services (in the Software
view)". Keep lines under ~120 characters.

- [ ] **Step 4: Run tests**

Run: `cd services/admin-api && uv run pytest -q tests/test_graph_sync.py tests/test_graph_inventory_sync_api.py tests/test_graph_memory.py tests/test_system_graph_api.py tests/test_graph_environment_tree_migration.py tests/test_environments.py tests/test_services.py tests/test_servers.py < /dev/null`
Expected: PASS except the known `test_environments` failure. Then the whole suite once.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/graph_sync.py services/admin-api/tests/test_graph_sync.py \
  services/admin-api/tests/test_graph_inventory_sync_api.py docs/user-guide.md
git commit -m "feat(api): the infrastructure graph is a tree — environments hold their servers, servers their deployments

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Final check

- Backend: `cd services/admin-api && uv run pytest -q < /dev/null` — only the known failures.
- `sh db/tests/run.sh` exits 0.
- UI unchanged; `cd services/admin-ui && npx vitest run` still green.
