"""System graph views — graph in graph: a view shows one node's inside (its descendants down to a depth), collapses
deeper nodes into the visible node that contains them, and rolls every stored edge up to the nodes shown. Pure
functions over rows that system_graph_api.py loads."""
from collections import defaultdict

LAYERS = ('business', 'software', 'data', 'infrastructure')
EDGE_TYPES = ('calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes', 'subscribes', 'instance_of', 'runs_on',
              'implements', 'stores', 'includes')
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
                          'source_kind': node.get('source_kind'), 'documents': list(documents.get(node['id'], []))})
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
        if start not in by_id or end not in by_id:  # created after the nodes were read
            continue
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
                boundary[end_id] = {'id': end_id, 'name': node['name'], 'kind': node['kind'], 'layer': node['layer'],
                                    'source_kind': node.get('source_kind')}
        group = grouped.setdefault((a, b, edge['type']), {'from': a, 'to': b, 'type': edge['type'], 'count': 0, 'edge_ids': [],
                                                          'synced': True})
        group['synced'] = group['synced'] and bool(edge.get('synced'))
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
