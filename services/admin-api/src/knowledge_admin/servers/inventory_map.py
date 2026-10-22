"""Environments → System map, computed from the inventory on every request — nothing about the map is stored. Two views:
infrastructure (environment › server › service, from deployments: a server sits in the environments of its
deployments, a service node is its deployments on one server in one environment) and services (services and code
projects, flat). Infrastructure edges are connections between the deployment nodes; the services view draws service
relations and the connections rolled up to the services. A connection to an external host ends on a boundary node for
that host. Each node lists the live documents linked to its entry (ka.document_resources). A view shows one node's
inside down to a depth, folds deeper nodes into the visible node that contains them, and rolls every edge up to the
nodes shown (graph in graph)."""

from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

import networkx as nx

VIEWS = ('infrastructure', 'services')
ENTRY_KINDS = ('service', 'server', 'environment', 'code_project')
EXTERNAL = 'external_host'  # the kind of an external host's node
MAX_DEPTH, MAX_VISIBLE, MAX_EDGE_IDS = 3, 500, 20
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
    entries: dict[Key, dict]  # name, description (archived code projects left out)
    deployments: dict[UUID, tuple[UUID, UUID, UUID]] = field(
        default_factory=dict
    )  # id → (service, server, environment)
    connections: list[dict] = field(default_factory=list)  # id, from (deployment), to (deployment) or host
    relations: list[dict] = field(default_factory=list)  # id, kind, from: Key, to: Key
    documents: dict[Key, list[dict]] = field(default_factory=dict)  # live documents linked to the entry


ENTRY_ROWS = {
    'environment': 'SELECT id,name,description FROM ka.environments WHERE project_id=%s',
    'server': 'SELECT id,name,description FROM ka.servers WHERE project_id=%s',
    'service': 'SELECT id,name,description FROM ka.services WHERE project_id=%s',
    'code_project': 'SELECT id,name,description FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL',
}


def load(conn, project: UUID) -> Inventory:
    entries = {
        (kind, row['id']): {'name': row['name'], 'description': row['description']}
        for kind, sql in ENTRY_ROWS.items()
        for row in conn.execute(sql, (project,))
    }
    documents: dict[Key, list[dict]] = {}
    for row in conn.execute(
        """SELECT r.resource_type AS entry_kind,COALESCE(r.server_id,r.service_id,r.environment_id,r.code_project_id)
            AS entry_id,d.id,d.slug,d.title FROM ka.document_resources r JOIN ka.documents d ON d.id=r.document_id
            WHERE d.project_id=%s AND NOT d.is_draft AND r.removed_at IS NULL AND r.deployment_id IS NULL
            ORDER BY lower(d.title),d.id""",
        (project,),
    ):
        documents.setdefault((row['entry_kind'], row['entry_id']), []).append(
            {'id': row['id'], 'slug': row['slug'], 'title': row['title']}
        )
    return Inventory(
        entries=entries,
        deployments={
            r['id']: (r['service_id'], r['server_id'], r['environment_id'])
            for r in conn.execute(
                'SELECT id,service_id,server_id,environment_id FROM ka.service_deployments WHERE project_id=%s',
                (project,),
            )
        },
        connections=[
            {'id': r['id'], 'from': r['from_deployment_id'], 'to': r['to_deployment_id'], 'host': r['to_host']}
            for r in conn.execute(
                """SELECT id,from_deployment_id,to_deployment_id,to_host FROM ka.connections
                WHERE project_id=%s ORDER BY id""",
                (project,),
            )
        ],
        relations=[
            {
                'id': r['id'],
                'kind': r['kind'],
                'from': ('service', r['from_service_id']),
                'to': ('service', r['to_service_id'])
                if r['to_service_id'] is not None
                else ('code_project', r['to_code_project_id']),
            }
            for r in conn.execute(
                """SELECT id,kind,from_service_id,to_service_id,to_code_project_id FROM ka.service_relations
                WHERE project_id=%s ORDER BY id""",
                (project,),
            )
        ],
        documents=documents,
    )


def node_id(kind: str, entry: UUID, *where: UUID | None) -> str:
    return ':'.join([kind, *(TOP if place is None else str(place) for place in where), str(entry)])


def external_id(host: str) -> str:
    return f'{EXTERNAL}:{host}'


def placements(inventory: Inventory, view: str) -> nx.DiGraph:
    """The view's containment tree: edges parent → child; node attributes entry, environment, name, description."""
    tree = nx.DiGraph()

    def add(node: str, entry: Key, parent: str | None, environment: UUID | None) -> None:
        tree.add_node(node, entry=entry, environment=environment, **inventory.entries[entry])
        if parent is not None:
            tree.add_edge(parent, node)

    if view == 'services':
        for kind, entry in inventory.entries:
            if kind in ('service', 'code_project'):
                add(node_id(kind, entry), (kind, entry), None, None)
        return tree

    known = inventory.entries  # rows are read a moment apart: skip anything whose entry was not read
    for kind, entry in inventory.entries:
        if kind == 'environment':
            add(node_id('environment', entry), ('environment', entry), None, entry)
    deployments = sorted(
        {
            d
            for d in inventory.deployments.values()
            if ('service', d[0]) in known and ('server', d[1]) in known and ('environment', d[2]) in known
        },
        key=str,
    )
    homes: dict[UUID, set] = defaultdict(set)  # server → the environments of its deployments
    for _, server, environment in deployments:
        homes[server].add(environment)
    for kind, server in inventory.entries:
        if kind == 'server':
            for environment in homes[server] or {None}:  # a server with no deployment sits at the top level
                parent = None if environment is None else node_id('environment', environment)
                add(node_id('server', server, environment), ('server', server), parent, environment)
    for service, server, environment in deployments:
        node = node_id('service', service, environment, server)
        if node not in tree:  # several runtimes of one service on one server are one node
            add(node, ('service', service), node_id('server', server, environment), environment)
    return tree


def view_edges(inventory: Inventory, view: str, tree: nx.DiGraph) -> list[dict]:
    """The view's edges {id, from_id, to_id, type}: infrastructure, each connection between the nodes of its
    deployments; services, each relation and each connection between the services of its deployments. A connection
    to an external host ends on that host's node (not in the tree)."""

    def deployment_node(deployment: UUID | None) -> str | None:
        if deployment not in inventory.deployments:
            return None
        service, server, environment = inventory.deployments[deployment]
        if view == 'services':
            return node_id('service', service)
        return node_id('service', service, environment, server)

    edges = []
    for connection in inventory.connections:
        start = deployment_node(connection['from'])
        end = deployment_node(connection['to']) if connection['host'] is None else external_id(connection['host'])
        if start in tree and (end in tree or connection['host'] is not None):
            edges.append({'id': connection['id'], 'from_id': start, 'to_id': end, 'type': 'connects_to'})
    if view == 'services':
        for relation in inventory.relations:
            start, end = node_id(*relation['from']), node_id(*relation['to'])
            if start in tree and end in tree:
                edges.append({'id': relation['id'], 'from_id': start, 'to_id': end, 'type': relation['kind']})
    return edges


def build_view(inventory: Inventory, view: str, root: str | None, depth: int) -> dict:
    tree = placements(inventory, view)
    edges = view_edges(inventory, view, tree)

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
        if node not in tree:  # an external host
            host = node.removeprefix(f'{EXTERNAL}:')
            return {'id': node, 'name': host, 'kind': EXTERNAL, 'entry_kind': EXTERNAL, 'entry_id': host}
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
            rows[node] = {
                **about(node),
                'parent_id': visible_parent,
                'description': data['description'],
                'child_count': len(inner),
                'expandable': bool(inner) and level == depth,
                'internal_edges': 0,
                'documents': list(inventory.documents.get(data['entry'], [])),
            }
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

    def drawn_as(node: str) -> str | None:
        """The visible node an edge end is drawn as: its own or folding node, else outside (an external host always)."""
        if node in representative:
            return representative[node]
        return node if node not in tree else outside(node)

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
        a, b = drawn_as(start), drawn_as(end)
        if a is None or b is None or a == b:
            rows[a if a is not None and a in rows else b]['internal_edges'] += 1
            continue
        if ancestor_of(a, b) or ancestor_of(b, a):  # a box and something inside it: no renderer can draw that line
            rows[a if ancestor_of(a, b) else b]['internal_edges'] += 1
            continue
        for end_id in (a, b):
            if end_id not in rows and end_id not in boundary:
                boundary[end_id] = about(end_id)
        # an edge drawn between several placements rolls up once: count distinct connections and relations
        grouped.setdefault((a, b, edge['type']), set()).add(edge['id'])
    if len(rows) + len(boundary) > MAX_VISIBLE:
        raise ViewError(422, TOO_MANY)
    rolled = [
        {'from': a, 'to': b, 'type': kind, 'count': len(ids), 'ids': sorted(ids, key=str)[:MAX_EDGE_IDS]}
        for (a, b, kind), ids in grouped.items()
    ]
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
    hits = [
        (key, data)
        for key, data in inventory.entries.items()
        if (kind is None or key[0] == kind) and needle in data['name'].lower()
    ]
    hits.sort(key=lambda hit: (hit[1]['name'].lower(), hit[0][0], str(hit[0][1])))
    return [{'entry_kind': key[0], 'entry_id': key[1], 'name': data['name']} for key, data in hits[:limit]]
