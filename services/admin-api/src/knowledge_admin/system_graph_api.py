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
                   'implements', 'stores']
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
MAX_PROPERTIES = 16384
NODE = 'id,layer,kind,name,description,parent_id,properties,revision,created_at,updated_at'
EDGE = 'id,from_id,to_id,type,description,revision,created_at,updated_at'


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

    def node_row(conn, project: UUID, node_id: UUID, lock: bool = False) -> dict:
        row = conn.execute(f"SELECT {NODE} FROM ka.kg_nodes WHERE id=%s AND project_id=%s{' FOR UPDATE' if lock else ''}",
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
            return conn.execute(f"""SELECT e.id,e.type,e.description,e.revision,
                    jsonb_build_object('id',n.id,'name',n.name,'kind',n.kind,'layer',n.layer) AS node
                FROM ka.kg_edges e JOIN ka.kg_nodes n ON n.id=e.{other}
                WHERE e.{mine}=%s ORDER BY e.type,lower(n.name),e.id""", (node_id,)).fetchall()
        return {**node, 'breadcrumb': breadcrumb(conn, node_id), 'children': children,
                'outgoing': edges('from_id', 'to_id'), 'incoming': edges('to_id', 'from_id'),
                'documents': documents_of(conn, project, node_id).get(node_id, [])}

    @router.get('/graph/view/{layer}', response_model=None)
    def view(layer: Layer, root: UUID | None = None, depth: int = Query(1, ge=1, le=system_graph.MAX_DEPTH),
             format: Literal['json', 'mermaid', 'd2', 'svg'] = 'json',
             layout: Literal['tala', 'elk', 'dagre'] = 'tala'):
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            nodes = conn.execute('SELECT id,parent_id,layer,kind,name,description FROM ka.kg_nodes WHERE project_id=%s',
                                 (project,)).fetchall()
            edges = conn.execute('SELECT id,from_id,to_id,type FROM ka.kg_edges WHERE project_id=%s ORDER BY id', (project,)).fetchall()
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
            return Response(system_graph_render.svg(text, layout), media_type='image/svg+xml')
        except system_graph_render.RenderError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @router.get('/graph/nodes')
    def search(layer: Layer | None = None, q: str = Query('', max_length=200), limit: int = Query(50, ge=1, le=200)) -> dict:
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
            while parent is not None:
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
            with refused(conn, duplicate_name(fields['name'])):
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
            current = node_row(conn, project, node_id, lock=True)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE_NODE)
            if fields['parent_id'] == node_id:
                raise HTTPException(422, INSIDE_ITSELF)
            check_parent(conn, project, current['layer'], fields['parent_id'])
            with refused(conn, duplicate_name(fields['name'])):
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
            node = node_row(conn, project, node_id, lock=True)
            if conn.execute('SELECT 1 FROM ka.kg_nodes WHERE parent_id=%s LIMIT 1', (node_id,)).fetchone():
                raise HTTPException(409, HAS_CHILDREN)
            with refused(conn, HAS_CHILDREN, (409, HAS_CHILDREN)):  # a child added meanwhile
                conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (node_id,))
            audit(conn, project, request, 'graph.node.deleted', 'graph_node', node_id,
                  {'layer': node['layer'], 'name': node['name']})
        return Response(status_code=204)

    return router
