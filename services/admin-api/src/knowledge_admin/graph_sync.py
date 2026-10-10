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
        if release(conn, node, key[0]):  # a kept node keeps its name and place, so its name stays taken
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


def release(conn, node: dict, link: str) -> bool:
    """The node's row is gone: its synced edges go; the node goes too unless something hand-made hangs off it."""
    conn.execute('DELETE FROM ka.kg_edges WHERE synced AND (from_id=%s OR to_id=%s)', (node['id'], node['id']))
    kept = conn.execute("""SELECT EXISTS (SELECT 1 FROM ka.kg_nodes WHERE parent_id=%s)
        OR EXISTS (SELECT 1 FROM ka.kg_node_documents WHERE node_id=%s) AS kept""", (node['id'], node['id'])).fetchone()['kept']
    if not kept:
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (node['id'],))
        return True
    note = f'Removed from {SOURCES[link][2]} on {date.today():%Y-%m-%d}.'
    description = (node['description'] + '\n' if node['description'] else '') + note
    conn.execute(f'UPDATE ka.kg_nodes SET {link}=NULL,description=%s,revision=revision+1 WHERE id=%s',
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
