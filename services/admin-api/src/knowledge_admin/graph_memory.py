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
