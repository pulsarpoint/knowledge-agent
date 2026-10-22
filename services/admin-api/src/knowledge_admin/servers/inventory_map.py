"""Environments → System map, drawn on every request from the environment models (inventory/store.py
load_project_models: every environment's model, the servers and services with no deployment, the code projects and the
documents on each entry) — nothing about the map is stored. Two views:
infrastructure (environment › server › service, from deployments: a server sits in the environments of its
deployments, a service node is its deployments on one server in one environment) and services (services and code
projects, flat). Infrastructure edges are connections between the deployment nodes; the services view draws service
relations and the connections rolled up to the services. A connection to an external host ends on a boundary node for
that host. Each node lists the live documents linked to its entry (ka.document_resources). A view shows one node's
inside down to a depth, folds deeper nodes into the visible node that contains them, and rolls every edge up to the
nodes shown (graph in graph)."""

from uuid import UUID

import networkx as nx

VIEWS = ('infrastructure', 'services')
ENTRY_KINDS = ('service', 'server', 'environment', 'code_project')
EXTERNAL = 'external_host'  # the kind of an external host's node
MAX_DEPTH, MAX_VISIBLE, MAX_EDGE_IDS, MAX_LABELS = 3, 500, 20, 3
TOP = 'top'
NOT_FOUND = 'Node not found.'
TOO_MANY = 'Too many nodes; choose a deeper root or a smaller depth.'


class ViewError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def node_id(kind: str, entry: UUID, *where: UUID | None) -> str:
    return ':'.join([kind, *(TOP if place is None else str(place) for place in where), str(entry)])


def external_id(host: str) -> str:
    return f'{EXTERNAL}:{host}'


def connection_label(connection: dict) -> str:
    """`protocol:port`, or just the part there is; empty when neither."""
    port = '' if connection['port'] is None else f':{connection["port"]}'
    return f'{connection["protocol"] or ""}{port}'


def rolled_label(labels: set[str]) -> str:
    """The distinct labels sorted, at most three, then ` +N` for the rest."""
    found = sorted(label for label in labels if label)
    shown = ', '.join(found[:MAX_LABELS])
    return shown + (f' +{len(found) - MAX_LABELS}' if len(found) > MAX_LABELS else '')


def deployment_places(inventory: dict) -> dict[UUID, tuple[UUID, UUID, UUID]]:
    """Deployment id → (service, server, environment), from the models."""
    return {
        deployment['id']: (deployment['service_id'], server['id'], model['environment']['id'])
        for model in inventory['models']
        for server in model['servers']
        for deployment in server['deployments']
    }


def placements(inventory: dict, view: str) -> nx.DiGraph:
    """The view's containment tree: edges parent → child; node attributes entry, environment, name, description."""
    tree = nx.DiGraph()

    def add(node: str, kind: str, item: dict, parent: str | None, environment: UUID | None) -> None:
        tree.add_node(
            node,
            entry=(kind, item['id']),
            environment=environment,
            name=item['name'],
            description=item['description'],
        )
        if parent is not None:
            tree.add_edge(parent, node)

    unplaced = inventory['unplaced']
    if view == 'services':
        for model in inventory['models']:
            for service in model['services']:
                add(node_id('service', service['id']), 'service', service, None, None)
        for service in unplaced['services']:
            add(node_id('service', service['id']), 'service', service, None, None)
        for code_project in inventory['code_projects']:
            add(node_id('code_project', code_project['id']), 'code_project', code_project, None, None)
        return tree

    for model in inventory['models']:
        environment = model['environment']
        add(node_id('environment', environment['id']), 'environment', environment, None, environment['id'])
        services = {service['id']: service for service in model['services']}
        for server in model['servers']:
            server_node = node_id('server', server['id'], environment['id'])
            add(server_node, 'server', server, node_id('environment', environment['id']), environment['id'])
            for deployment in server['deployments']:
                node = node_id('service', deployment['service_id'], environment['id'], server['id'])
                if node not in tree:  # several runtimes of one service on one server are one node
                    add(node, 'service', services[deployment['service_id']], server_node, environment['id'])
    for server in unplaced['servers']:  # a server with no deployment sits at the top level
        add(node_id('server', server['id'], None), 'server', server, None, None)
    return tree


def view_edges(inventory: dict, view: str, tree: nx.DiGraph) -> list[dict]:
    """The view's edges {id, from_id, to_id, type}: infrastructure, each connection between the nodes of its
    deployments; services, each relation and each connection between the services of its deployments. A connection
    to an external host ends on that host's node (not in the tree)."""
    places = deployment_places(inventory)

    def deployment_node(deployment: UUID | None) -> str | None:
        if deployment not in places:
            return None
        service, server, environment = places[deployment]
        if view == 'services':
            return node_id('service', service)
        return node_id('service', service, environment, server)

    models = inventory['models']
    connections = {c['id']: c for model in models for c in model['connections']}  # one across environments
    edges = []
    for connection in connections.values():
        host = connection['to_host']
        start = deployment_node(connection['from_deployment_id'])
        end = deployment_node(connection['to_deployment_id']) if host is None else external_id(host)
        if start in tree and (end in tree or host is not None):
            edges.append(
                {
                    'id': connection['id'],
                    'from_id': start,
                    'to_id': end,
                    'type': 'connects_to',
                    'label': connection_label(connection),
                }
            )
    if view == 'services':
        relations = [r for model in models for r in model['relations']] + inventory['unplaced']['relations']
        for relation in {r['id']: r for r in relations}.values():  # one across environments
            start = node_id('service', relation['from_service_id'])
            if relation['to_service_id'] is not None:
                end = node_id('service', relation['to_service_id'])
            else:
                end = node_id('code_project', relation['to_code_project_id'])
            if start in tree and end in tree:
                edges.append({'id': relation['id'], 'from_id': start, 'to_id': end, 'type': relation['kind']})
    return edges


def build_view(inventory: dict, view: str, root: str | None, depth: int) -> dict:
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
                'documents': list(inventory['documents'].get(data['entry'], [])),
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
        ids, labels = grouped.setdefault((a, b, edge['type']), (set(), set()))
        ids.add(edge['id'])
        labels.add(edge.get('label', ''))
    if len(rows) + len(boundary) > MAX_VISIBLE:
        raise ViewError(422, TOO_MANY)
    rolled = []
    for (a, b, kind), (ids, labels) in grouped.items():
        edge = {'from': a, 'to': b, 'type': kind, 'count': len(ids), 'ids': sorted(ids, key=str)[:MAX_EDGE_IDS]}
        if rolled_label(labels):
            edge['label'] = rolled_label(labels)
        rolled.append(edge)
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
