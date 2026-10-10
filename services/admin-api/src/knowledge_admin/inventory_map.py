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

    def ancestor_of(upper: str, lower: str) -> bool:
        while lower is not None and lower in rows:
            lower = rows[lower]['parent_id']
            if lower == upper:
                return True
        return False

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
        if ancestor_of(a, b) or ancestor_of(b, a):  # a box and something inside it: no renderer can draw that line
            rows[a if ancestor_of(a, b) else b]['internal_edges'] += 1
            continue
        for end_id in (a, b):
            if end_id not in rows and end_id not in boundary:
                boundary[end_id] = about(end_id)
        # a link drawn between several placements rolls up once: count distinct links, not drawn pairs
        grouped.setdefault((a, b, edge['type']), set()).add(edge['id'])
    if len(rows) + len(boundary) > MAX_VISIBLE:
        raise ViewError(422, TOO_MANY)
    rolled = [{'from': a, 'to': b, 'type': kind, 'count': len(ids), 'link_ids': sorted(ids, key=str)[:MAX_LINK_IDS]}
              for (a, b, kind), ids in grouped.items()]
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
        'edges': sorted(rolled, key=lambda edge: (edge['from'], edge['to'], edge['type'])),
    }


def search(inventory: Inventory, q: str, kind: str | None, limit: int) -> list[dict]:
    needle = q.strip().lower()
    hits = [(key, data) for key, data in inventory.entries.items()
            if (kind is None or key[0] == kind) and needle in data['name'].lower()]
    hits.sort(key=lambda hit: (hit[1]['name'].lower(), hit[0][0], str(hit[0][1])))
    return [{'entry_kind': key[0], 'entry_id': key[1], 'name': data['name']} for key, data in hits[:limit]]
