"""Inventory → system graph. Servers, environments, services with their deployments, and code projects keep linked
graph nodes; service dependencies, service code projects, environment members and deployments keep synced edges.
sync_inventory works out the whole wanted picture from the inventory and writes only the difference, inside the
caller's transaction, so the inventory and the graph never disagree and anything changed behind its back is repaired."""
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

import psycopg

if TYPE_CHECKING:  # repositories calls the sync, so it cannot be imported here at run time
    from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

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
MAX_NAME, MAX_DESCRIPTION, MAX_LEVELS = 160, 4000, 12

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


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    released: int = 0
    edges_added: int = 0
    edges_removed: int = 0


GRAPH_CLASH = 'The system graph already has a node with this name. Rename the other node or try again.'


def graph_clash(exc: psycopg.errors.UniqueViolation) -> bool:
    """True when a unique violation came from a graph table (kg_*), not from the inventory table being saved."""
    return (exc.diag.constraint_name or '').startswith('kg_')


def free_name(taken: set[str], wanted: str) -> str:
    """The first of wanted, wanted (2), wanted (3), … not in taken (lower-case names), cut to 160 characters."""
    base = wanted.strip()
    candidate, n = base[:MAX_NAME], 1
    while candidate.lower() in taken:
        n += 1
        suffix = f' ({n})'
        candidate = base[:MAX_NAME - len(suffix)].rstrip() + suffix
    return candidate


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


def wanted_edges(conn, project: UUID) -> set[tuple[Key, Key, str]]:
    edges: set[tuple[Key, Key, str]] = set()
    for row in conn.execute('SELECT id,service_id FROM ka.service_deployments WHERE project_id=%s', (project,)):
        edges.add((('deployment_id', row['id']), ('service_id', row['service_id']), 'instance_of'))
    for row in conn.execute('SELECT service_id,depends_on_service_id FROM ka.service_dependencies WHERE project_id=%s', (project,)):
        edges.add((('service_id', row['service_id']), ('service_id', row['depends_on_service_id']), 'depends_on'))
    for row in conn.execute("""SELECT l.service_id,l.repository_id FROM ka.service_repositories l
            JOIN ka.repositories r ON r.id=l.repository_id WHERE l.project_id=%s AND r.archived_at IS NULL""", (project,)):
        edges.add((('service_id', row['service_id']), ('repository_id', row['repository_id']), 'uses'))
    for row in conn.execute('SELECT environment_id,service_id FROM ka.environment_services WHERE project_id=%s', (project,)):
        edges.add((('environment_id', row['environment_id']), ('service_id', row['service_id']), 'includes'))
    return edges


def sync_inventory(conn, project: UUID) -> SyncResult:
    result = SyncResult()
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(%s::text))", (project,))
    want = wanted_nodes(conn, project)
    nodes = conn.execute(f'SELECT id,layer,parent_id,name,description,{LINKS} FROM ka.kg_nodes WHERE project_id=%s',
                         (project,)).fetchall()
    stored = {key: node for node in nodes if (key := key_of(node)) is not None}
    taken: dict[tuple[str, UUID | None], set[str]] = {}
    for node in nodes:
        taken.setdefault((node['layer'], node['parent_id']), set()).add(node['name'].lower())

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
        old = stored.pop(key)
        if target[1][1] and subtree_height(conn, old['id']) + 1 > MAX_LEVELS:
            # Under an environment the copy sits one level lower and its subtree would pass the depth limit: keep the
            # old copy unlinked with its nodes and let step 2 make a fresh one.
            park_wanted_children(conn, old, stored, taken)
            release(conn, old, key, 'Environments')
            result.released += 1
            continue
        missing[key[1][0]].remove(target)
        parent = stored.get(('environment_id', target[1][1])) if target[1][1] else None
        taken.get((old['layer'], old['parent_id']), set()).discard(old['name'].lower())
        # Re-parented now (to nothing if the environment has no node yet), so a deleted old environment is not kept by it.
        # The name is parked (the node's id) because it may clash at the new level; step 2 gives it a free name.
        stored[target] = conn.execute(f"""UPDATE ka.kg_nodes SET environment_id=%s,parent_id=%s,name=%s,revision=revision+1
            WHERE id=%s RETURNING id,layer,parent_id,name,description,{LINKS}""",
            (target[1][1], parent['id'] if parent else None, str(old['id']), old['id'])).fetchone()
        result.updated += 1

    # 1. Release nodes whose row is gone (deployments, then server copies, then the rest, so a parent has no children left from them).
    depth = {'deployment_id': 0, 'server_id': 1}
    gone = sorted((key for key in stored if key not in want), key=lambda key: depth.get(key[0], 2))
    wanted_servers = {key[1][0] for key in want if key[0] == 'server_id'}
    for key in gone:
        node = stored.pop(key)
        park_wanted_children(conn, node, stored, taken)
        label = 'Environments' if key[0] == 'server_id' and key[1][0] in wanted_servers else SOURCES[key[0]][2]
        if release(conn, node, key, label):  # a kept node keeps its name and place, so its name stays taken
            taken.get((node['layer'], node['parent_id']), set()).discard(node['name'].lower())
        result.released += 1

    # Nodes that are certainly renamed or moved give up their names first: the names are free before any is assigned,
    # and a parked name (the node's id) lets swaps pass the unique sibling-name rule. One sync then settles rename
    # chains and swaps, and a second sync has nothing left to do. A suffixed name ("X (2)" for X) is not a rename.
    for key, node in stored.items():
        row = want[key]
        base = row['name'].strip()[:MAX_NAME]
        parent_id = stored[row['parent']]['id'] if row['parent'] in stored else None
        suffixed = node['name'] == base or re.fullmatch(re.escape(base[:MAX_NAME - 12]) + r'.* \(\d+\)', node['name']) is not None
        if parent_id != node['parent_id'] or not suffixed:
            taken.get((node['layer'], node['parent_id']), set()).discard(node['name'].lower())
            node['name'] = conn.execute('UPDATE ka.kg_nodes SET name=%s WHERE id=%s RETURNING name',
                                        (str(node['id']), node['id'])).fetchone()['name']

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
                values = link_values(key)
                node = conn.execute(f"""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id,{','.join(values)})
                    VALUES (%s,%s,%s,%s,%s,{','.join(['%s'] * len(values))}) RETURNING id,layer,parent_id,name,description,{LINKS}""",
                    (project, layer, kind, name, parent_id, *values.values())).fetchone()
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


def subtree_height(conn, node_id: UUID) -> int:
    """Levels in the subtree under a node, the node itself counted as one."""
    return conn.execute("""WITH RECURSIVE down(id, level) AS (SELECT %s::uuid, 1
        UNION ALL SELECT n.id, down.level + 1 FROM ka.kg_nodes n JOIN down ON n.parent_id = down.id)
        SELECT max(level) AS height FROM down""", (node_id,)).fetchone()['height']


def park_wanted_children(conn, node: dict, stored: dict[Key, dict], taken: dict) -> None:
    """Before a node is released, its children that the inventory still wants leave it (name parked as the node's id,
    no parent; step 2 places them), so only hand-made children decide whether the released node is kept."""
    for child in stored.values():
        if child['parent_id'] != node['id']:
            continue
        taken.get((child['layer'], node['id']), set()).discard(child['name'].lower())
        child['name'] = conn.execute('UPDATE ka.kg_nodes SET parent_id=NULL,name=%s,revision=revision+1 WHERE id=%s RETURNING name',
                                     (str(child['id']), child['id'])).fetchone()['name']
        child['parent_id'] = None


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


def sync_all(store: 'RepositoryStore', apps: list[str]) -> None:
    """Start-up: bring every app's graph in step with its inventory; a failure is logged and never stops the service."""
    for app in apps:
        try:
            try:
                with store.connect() as conn:
                    sync_inventory(conn, store.project(conn, app))
            except (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure):
                # The first sync creates nodes for rows that already exist; at commit their deferred foreign keys take
                # key-share locks on those rows while the graph lock is held, so a concurrent inventory save holding
                # the row and waiting for the graph lock can deadlock with it. Once the graph is filled this cannot recur.
                with store.connect() as conn:
                    sync_inventory(conn, store.project(conn, app))
        except Exception:  # noqa: BLE001 - a start-up convenience must never take the service down
            logger.exception('Graph inventory sync failed for %s', app)
