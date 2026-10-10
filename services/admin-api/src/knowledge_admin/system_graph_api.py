"""Environments → System map: the system graph's nodes, edges and attached documents, and views of one node's inside
(graph in graph) as JSON, Mermaid, D2 or SVG. system_graph.py computes views; system_graph_render.py draws them."""
import json
from contextlib import contextmanager
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin import system_graph, system_graph_render
from knowledge_admin.access import request_actor
from knowledge_admin.event_templates import has_nul
from knowledge_admin.events_api import like
from knowledge_admin.repositories import RepositoryStore

Layer = Literal['business', 'software', 'data', 'infrastructure']
EdgeType = Literal['calls', 'uses', 'depends_on', 'reads', 'writes', 'publishes', 'subscribes', 'instance_of', 'runs_on',
                   'implements', 'stores', 'includes']
NO_NODE = 'Node not found.'
NO_PARENT = 'Parent not found.'
NO_EDGE = 'Edge not found.'
NO_DOCUMENT = 'Document not found.'
STALE_NODE = 'This node was changed by someone else. Reload and try again.'
STALE_EDGE = 'This edge was changed by someone else. Reload and try again.'
OTHER_LAYER = 'Choose a parent in the same layer.'
HAS_CHILDREN = 'Move or delete its children first.'
SAME_NODE = 'An edge needs two different nodes.'
DUPLICATE_EDGE = 'This edge already exists.'
INSIDE_ITSELF = 'A node cannot be moved inside itself.'
REFUSED = 'The graph refused this change.'
CONCURRENT = 'Someone changed the graph at the same time. Try again.'
SVG_HEADERS = {'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
               'X-Content-Type-Options': 'nosniff'}
MAX_PROPERTIES = 16384
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
LOCK_PROJECT = "SELECT pg_advisory_xact_lock(hashtext('ka.kg_nodes'), hashtext(%s::text))"


def source_of(row: dict) -> dict | None:
    """Where a node comes from, or None for a hand-made node."""
    for link, (kind, page, label) in SOURCES.items():
        if row.get(link):
            return {'kind': kind, 'id': row[link], 'page': page, 'label': label}
    return None


def public(row: dict) -> dict:
    """A node row without the five link columns, with its source."""
    return {**{key: value for key, value in row.items() if key not in SOURCES}, 'source': source_of(row)}


class NodeFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(max_length=60)
    name: str = Field(max_length=160)
    parent_id: UUID | None = None
    description: str = Field(default='', max_length=4000)
    properties: dict = Field(default_factory=dict)


class NodeDraft(NodeFields):
    layer: Layer


class NodeChange(NodeFields):
    revision: int = Field(ge=1, strict=True)


class EdgeDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    from_id: UUID
    to_id: UUID
    type: EdgeType
    description: str = Field(default='', max_length=4000)


class EdgeChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    description: str = Field(default='', max_length=4000)
    revision: int = Field(ge=1, strict=True)


def bad(message: str) -> HTTPException:
    return HTTPException(400, message)


def duplicate_name(name: str) -> str:
    return f'A node named "{name}" already exists here.'


def clean(value: NodeFields) -> dict:
    """The node's columns, validated: 400 with a message for the form when something is wrong."""
    try:
        for label, text in (('Kind', value.kind), ('Name', value.name), ('Description', value.description),
                            ('Properties', value.properties)):
            if has_nul(text):
                raise bad(f'Remove the NUL character from {label}.')
        kind, name = value.kind.strip(), value.name.strip()
        if not kind:
            raise bad('Enter a kind.')
        if not name:
            raise bad('Enter a name.')
        size = len(json.dumps(value.properties, ensure_ascii=False, allow_nan=False))
    except RecursionError:
        raise bad('Properties are nested too deeply.') from None
    except ValueError:
        raise bad('Properties must be valid JSON (no NaN or Infinity).') from None
    if size > MAX_PROPERTIES:
        raise bad('Properties are too large (16 KB at most).')
    return {'kind': kind, 'name': name, 'description': value.description, 'properties': value.properties,
            'parent_id': value.parent_id}


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Graph storage is not configured')
        return store

    def audit(conn, project: UUID, request: Request, action: str, resource_type: str, resource_id: UUID, details: dict) -> None:
        actor = database().actor(conn, *request_actor(request))
        conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,%s,%s,%s)""", (project, actor, action, resource_type, resource_id, Jsonb(details)))

    @contextmanager
    def refused(conn, duplicate: str, missing: tuple[int, str] = (422, OTHER_LAYER)):
        """Runs a write in a savepoint and turns the database's refusals into answers for the form."""
        try:
            with conn.transaction():
                yield
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, duplicate) from None
        except psycopg.errors.CheckViolation as exc:
            # The graph's triggers raise without a constraint name and with a sentence meant for people.
            if exc.diag.constraint_name is None and exc.diag.message_primary:
                raise HTTPException(422, exc.diag.message_primary) from None
            raise bad(REFUSED) from None
        except psycopg.errors.ForeignKeyViolation:
            raise HTTPException(*missing) from None
        except (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure):
            raise HTTPException(409, CONCURRENT) from None

    def lock_project(conn, project: UUID) -> None:
        # The inventory triggers take this advisory lock before they touch graph rows. A graph write that locked its row
        # first and then fired kg_nodes_place would wait on the lock while an inventory save, holding it, waited for the
        # row: a deadlock. Everyone takes the advisory lock first, then rows.
        conn.execute(LOCK_PROJECT, (project,))

    def node_row(conn, project: UUID, node_id: UUID, lock: bool = False) -> dict:
        # NO KEY UPDATE: the node's UPDATE waits for the project lock that a concurrent create holds while its foreign key
        # check wants a share lock on this row; FOR UPDATE would conflict with that share lock and deadlock.
        row = conn.execute(f"SELECT {NODE} FROM ka.kg_nodes WHERE id=%s AND project_id=%s{' FOR NO KEY UPDATE' if lock else ''}",
                           (node_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NO_NODE)
        return row

    def check_parent(conn, project: UUID, layer: str, parent_id: UUID | None) -> None:
        if parent_id is None:
            return
        parent = conn.execute('SELECT layer FROM ka.kg_nodes WHERE id=%s AND project_id=%s', (parent_id, project)).fetchone()
        if parent is None:
            raise HTTPException(404, NO_PARENT)
        if parent['layer'] != layer:
            raise HTTPException(422, OTHER_LAYER)

    def breadcrumb(conn, node_id: UUID) -> list[dict]:
        return conn.execute("""WITH RECURSIVE up(id,parent_id,name,kind,steps) AS (
                SELECT id,parent_id,name,kind,0 FROM ka.kg_nodes WHERE id=%s
                UNION ALL
                SELECT p.id,p.parent_id,p.name,p.kind,up.steps+1 FROM ka.kg_nodes p JOIN up ON p.id=up.parent_id)
            SELECT id,name,kind FROM up ORDER BY steps DESC""", (node_id,)).fetchall()

    def documents_of(conn, project: UUID, node_id: UUID | None = None) -> dict:
        rows = conn.execute("""SELECT l.node_id,d.id,d.slug,d.title FROM ka.kg_node_documents l
            JOIN ka.document_details d ON d.id=l.document_id
            WHERE l.project_id=%s AND (%s::uuid IS NULL OR l.node_id=%s) ORDER BY lower(d.title),d.id""",
            (project, node_id, node_id)).fetchall()
        attached: dict = {}
        for row in rows:
            attached.setdefault(row['node_id'], []).append({'id': row['id'], 'slug': row['slug'], 'title': row['title']})
        return attached

    def detail(conn, project: UUID, node_id: UUID) -> dict:
        node = node_row(conn, project, node_id)
        children = conn.execute("""SELECT c.id,c.name,c.kind,(SELECT count(*) FROM ka.kg_nodes g WHERE g.parent_id=c.id) AS child_count
            FROM ka.kg_nodes c WHERE c.parent_id=%s ORDER BY lower(c.name),c.id""", (node_id,)).fetchall()

        def edges(mine: str, other: str) -> list[dict]:
            return conn.execute(f"""SELECT e.id,e.type,e.description,e.revision,e.synced,
                    jsonb_build_object('id',n.id,'name',n.name,'kind',n.kind,'layer',n.layer) AS node
                FROM ka.kg_edges e JOIN ka.kg_nodes n ON n.id=e.{other}
                WHERE e.{mine}=%s ORDER BY e.type,lower(n.name),e.id""", (node_id,)).fetchall()
        return {**public(node), 'breadcrumb': breadcrumb(conn, node_id), 'children': children,
                'outgoing': edges('from_id', 'to_id'), 'incoming': edges('to_id', 'from_id'),
                'documents': documents_of(conn, project, node_id).get(node_id, [])}

    @router.get('/graph/view/{layer}', response_model=None)
    def view(layer: Layer, root: UUID | None = None, depth: int = Query(1, ge=1, le=system_graph.MAX_DEPTH),
             format: Literal['json', 'mermaid', 'd2', 'svg'] = 'json',
             layout: Literal['tala', 'elk', 'dagre'] = 'tala'):
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            nodes = conn.execute(f'SELECT id,parent_id,layer,kind,name,description,{SOURCE_KIND} FROM ka.kg_nodes WHERE project_id=%s',
                                 (project,)).fetchall()
            edges = conn.execute('SELECT id,from_id,to_id,type,synced FROM ka.kg_edges WHERE project_id=%s ORDER BY id', (project,)).fetchall()
            documents = documents_of(conn, project)
        try:
            graph = system_graph.build_view(nodes, edges, documents, layer, root, depth)
        except system_graph.ViewError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        if format == 'json':
            return graph
        if format == 'mermaid':
            return PlainTextResponse(system_graph_render.mermaid(graph))
        text = system_graph_render.d2(graph)
        if format == 'd2':
            return PlainTextResponse(text)
        try:
            return Response(system_graph_render.svg(text, layout), media_type='image/svg+xml', headers=SVG_HEADERS)
        except system_graph_render.RenderError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @router.get('/graph/nodes')
    def search(layer: Layer | None = None, q: str = Query('', max_length=200), limit: int = Query(50, ge=1, le=200)) -> dict:
        if has_nul(q):
            raise bad('Remove the NUL character from the search.')
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            rows = conn.execute("""SELECT id,layer,kind,name FROM ka.kg_nodes
                WHERE project_id=%s AND (%s::text IS NULL OR layer=%s) AND name ILIKE %s ESCAPE '\\'
                ORDER BY lower(name),id LIMIT %s""", (project, layer, layer, like(q.strip()), limit)).fetchall()
            everything = {row['id']: row for row in conn.execute(
                'SELECT id,parent_id,name FROM ka.kg_nodes WHERE project_id=%s', (project,)).fetchall()}

        def path(node_id) -> list[str]:
            names, parent = [], everything[node_id]['parent_id']
            while parent in everything:  # a parent deleted between the two queries ends the path
                names.append(everything[parent]['name'])
                parent = everything[parent]['parent_id']
            return names[::-1]
        return {'nodes': [{**row, 'path': path(row['id'])} for row in rows]}

    @router.get('/graph/nodes/{node_id}')
    def one(node_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            return {'node': detail(conn, db.project(conn, app), node_id)}

    @router.post('/graph/nodes', status_code=201)
    def create(value: NodeDraft, request: Request) -> dict:
        fields = clean(value)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            check_parent(conn, project, value.layer, fields['parent_id'])
            actor = db.actor(conn, *request_actor(request))
            with refused(conn, duplicate_name(fields['name']), (404, NO_PARENT)):  # the parent was deleted meanwhile
                node_id = conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,description,parent_id,properties,created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (project, value.layer, fields['kind'], fields['name'], fields['description'], fields['parent_id'],
                     Jsonb(fields['properties']), actor)).fetchone()['id']
            audit(conn, project, request, 'graph.node.created', 'graph_node', node_id,
                  {'layer': value.layer, 'name': fields['name']})
            return {'node': detail(conn, project, node_id)}

    @router.put('/graph/nodes/{node_id}')
    def save(node_id: UUID, value: NodeChange, request: Request) -> dict:
        fields = clean(value)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            lock_project(conn, project)
            current = node_row(conn, project, node_id, lock=True)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE_NODE)
            source = source_of(current)
            if source and (fields['kind'] != current['kind'] or fields['name'] != current['name']):
                raise HTTPException(409, f"This node comes from {source['label']}. Change it there.")
            if source and fields['parent_id'] != current['parent_id']:
                raise HTTPException(409, f"This node comes from {source['label']}. Its place follows the inventory.")
            if fields['parent_id'] == node_id:
                raise HTTPException(422, INSIDE_ITSELF)
            check_parent(conn, project, current['layer'], fields['parent_id'])
            with refused(conn, duplicate_name(fields['name']), (404, NO_PARENT)):  # the parent was deleted meanwhile
                conn.execute("""UPDATE ka.kg_nodes SET kind=%s,name=%s,description=%s,parent_id=%s,properties=%s,
                    revision=revision+1 WHERE id=%s""",
                    (fields['kind'], fields['name'], fields['description'], fields['parent_id'], Jsonb(fields['properties']),
                     node_id))
            moved = fields['parent_id'] != current['parent_id']
            audit(conn, project, request, 'graph.node.moved' if moved else 'graph.node.updated', 'graph_node', node_id,
                  {'name': fields['name'], 'parent_id': str(fields['parent_id']) if fields['parent_id'] else None})
            return {'node': detail(conn, project, node_id)}

    @router.delete('/graph/nodes/{node_id}', status_code=204)
    def delete(node_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            lock_project(conn, project)
            node = node_row(conn, project, node_id, lock=True)
            if source := source_of(node):
                raise HTTPException(409, f"This node comes from {source['label']}. Delete it there.")
            if conn.execute('SELECT 1 FROM ka.kg_nodes WHERE parent_id=%s LIMIT 1', (node_id,)).fetchone():
                raise HTTPException(409, HAS_CHILDREN)
            with refused(conn, HAS_CHILDREN, (409, HAS_CHILDREN)):  # a child added meanwhile
                conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (node_id,))
            audit(conn, project, request, 'graph.node.deleted', 'graph_node', node_id,
                  {'layer': node['layer'], 'name': node['name']})
        return Response(status_code=204)

    def edge_row(conn, project: UUID, edge_id: UUID, lock: bool = False) -> dict:
        row = conn.execute(f"SELECT {EDGE} FROM ka.kg_edges WHERE id=%s AND project_id=%s{' FOR UPDATE' if lock else ''}",
                           (edge_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NO_EDGE)
        return row

    def refuse_synced(conn, project: UUID, edge: dict) -> None:
        if not edge['synced']:
            return
        start = conn.execute(f'SELECT {LINKS} FROM ka.kg_nodes WHERE id=%s AND project_id=%s', (edge['from_id'], project)).fetchone()
        source = source_of(start) if start else None
        raise HTTPException(409, f"This edge comes from the inventory. Change it in {source['label'] if source else 'the inventory pages'}.")

    @router.post('/graph/edges', status_code=201)
    def create_edge(value: EdgeDraft, request: Request) -> dict:
        if has_nul(value.description):
            raise bad('Remove the NUL character from Description.')
        if value.from_id == value.to_id:
            raise HTTPException(422, SAME_NODE)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            for node_id in (value.from_id, value.to_id):
                node_row(conn, project, node_id)
            actor = db.actor(conn, *request_actor(request))
            with refused(conn, DUPLICATE_EDGE, (404, NO_NODE)):
                edge_id = conn.execute("""INSERT INTO ka.kg_edges(project_id,from_id,to_id,type,description,created_by)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (project, value.from_id, value.to_id, value.type, value.description, actor)).fetchone()['id']
            audit(conn, project, request, 'graph.edge.created', 'graph_edge', edge_id,
                  {'type': value.type, 'from_id': str(value.from_id), 'to_id': str(value.to_id)})
            return {'edge': edge_row(conn, project, edge_id)}

    @router.put('/graph/edges/{edge_id}')
    def save_edge(edge_id: UUID, value: EdgeChange, request: Request) -> dict:
        if has_nul(value.description):
            raise bad('Remove the NUL character from Description.')
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            lock_project(conn, project)
            current = edge_row(conn, project, edge_id, lock=True)
            refuse_synced(conn, project, current)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE_EDGE)
            conn.execute('UPDATE ka.kg_edges SET description=%s,revision=revision+1 WHERE id=%s', (value.description, edge_id))
            audit(conn, project, request, 'graph.edge.updated', 'graph_edge', edge_id, {'type': current['type']})
            return {'edge': edge_row(conn, project, edge_id)}

    @router.delete('/graph/edges/{edge_id}', status_code=204)
    def delete_edge(edge_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            lock_project(conn, project)
            row = edge_row(conn, project, edge_id, lock=True)
            refuse_synced(conn, project, row)
            conn.execute('DELETE FROM ka.kg_edges WHERE id=%s', (edge_id,))
            audit(conn, project, request, 'graph.edge.deleted', 'graph_edge', edge_id,
                  {'type': row['type'], 'from_id': str(row['from_id']), 'to_id': str(row['to_id'])})
        return Response(status_code=204)

    @router.put('/graph/nodes/{node_id}/documents/{document_id}', status_code=204)
    def attach(node_id: UUID, document_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            node_row(conn, project, node_id)
            if conn.execute('SELECT 1 FROM ka.documents WHERE id=%s AND project_id=%s', (document_id, project)).fetchone() is None:
                raise HTTPException(404, NO_DOCUMENT)
            with refused(conn, '', (404, NO_DOCUMENT)):  # the document was deleted meanwhile
                added = conn.execute("""INSERT INTO ka.kg_node_documents(node_id,document_id,project_id) VALUES (%s,%s,%s)
                    ON CONFLICT DO NOTHING RETURNING node_id""", (node_id, document_id, project)).fetchone()
            if added:
                audit(conn, project, request, 'graph.document.attached', 'graph_node', node_id, {'document_id': str(document_id)})
        return Response(status_code=204)

    @router.delete('/graph/nodes/{node_id}/documents/{document_id}', status_code=204)
    def detach(node_id: UUID, document_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            node_row(conn, project, node_id)
            removed = conn.execute('DELETE FROM ka.kg_node_documents WHERE node_id=%s AND document_id=%s RETURNING node_id',
                                   (node_id, document_id)).fetchone()
            if removed:
                audit(conn, project, request, 'graph.document.detached', 'graph_node', node_id, {'document_id': str(document_id)})
        return Response(status_code=204)

    return router
