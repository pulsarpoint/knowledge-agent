"""0092: the system graph — nodes nested within one layer, typed edges, links to documents."""
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from test_api import _client
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def node(conn, project, name=None, parent=None, layer='software', kind='service', **extra) -> dict:
    values = {'project_id': project, 'layer': layer, 'kind': kind, 'name': name or f'n-{uuid4().hex[:8]}',
              'parent_id': parent, **extra}
    return conn.execute(f"INSERT INTO ka.kg_nodes({','.join(values)}) VALUES ({','.join(['%s'] * len(values))}) RETURNING *",
                        tuple(values.values())).fetchone()


def edge(conn, project, a, b, type='calls') -> dict:
    return conn.execute("""INSERT INTO ka.kg_edges(project_id,from_id,to_id,type) VALUES (%s,%s,%s,%s) RETURNING *""",
                        (project, a, b, type)).fetchone()


def chain(conn, project, length, top=None) -> list[dict]:
    nodes, parent = [], top
    for _ in range(length):
        parent = node(conn, project, parent=parent)['id']
        nodes.append(parent)
    return nodes


def refused(store, error, message=None):
    """Runs the block in its own connection and expects the database to refuse it."""
    class Block:
        def __enter__(self):
            self.conn = store.connect()
            return self.conn

        def __exit__(self, kind, exc, tb):
            self.conn.close()
            assert kind is not None and issubclass(kind, error), f'expected {error.__name__}, got {kind}'
            if message:
                assert exc.diag.message_primary == message and exc.diag.constraint_name is None
            return True
    return Block()


def test_nodes_nest_within_a_layer(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api = node(conn, project, 'admin-api')
        child = node(conn, project, 'Event processor', parent=api['id'], kind='module')
        assert (child['parent_id'], child['revision'], child['properties'], child['description']) == (api['id'], 1, {}, '')
        node(conn, project, 'admin-api', layer='infrastructure', kind='container')  # same name, other layer
        node(conn, project, 'Event processor', parent=child['id'], kind='module')  # same name, other parent
    with refused(store, psycopg.errors.ForeignKeyViolation) as conn:  # the parent is in another layer
        node(conn, project, 'x', parent=api['id'], layer='data')
    with refused(store, psycopg.errors.UniqueViolation) as conn:  # sibling names are unique, case-insensitive
        node(conn, project, 'EVENT PROCESSOR', parent=api['id'])
    with refused(store, psycopg.errors.UniqueViolation) as conn:  # also at the top level
        node(conn, project, 'Admin-API')
    for bad in [{'layer': 'network'}, {'name': ' '}, {'kind': ''}, {'name': 'x' * 161}, {'kind': 'k' * 61},
                {'description': 'd' * 4001}, {'properties': Jsonb([1])}, {'properties': Jsonb({'x': 'y' * 16400})}]:
        with refused(store, psycopg.errors.CheckViolation) as conn:
            node(conn, project, **{'name': f'ok-{uuid4().hex[:6]}', **bad})


def test_cycles_are_refused(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a, _, c = chain(conn, project, 3)
    with refused(store, psycopg.errors.CheckViolation, 'A node cannot be moved inside itself.') as conn:
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (c, a))
    with refused(store, psycopg.errors.CheckViolation, 'A node cannot be moved inside itself.') as conn:
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (a, a))
    with store.connect() as conn:  # moving to the top level or sideways is fine
        conn.execute('UPDATE ka.kg_nodes SET parent_id=NULL WHERE id=%s', (c,))
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (a, c))


def test_depth_is_capped_at_twelve(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        deep = chain(conn, project, 12)
    with refused(store, psycopg.errors.CheckViolation, 'Too deep: a layer has at most 12 levels.') as conn:
        node(conn, project, parent=deep[-1])
    with store.connect() as conn:
        six = chain(conn, project, 6)
        seven = chain(conn, project, 7)
    with refused(store, psycopg.errors.CheckViolation, 'Too deep: a layer has at most 12 levels.') as conn:
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (six[-1], seven[0]))  # 6 + 7 = 13
    with store.connect() as conn:  # 5 + 7 = 12 is fine
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (six[-2], seven[0]))


def test_a_node_with_children_cannot_be_deleted(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        top, child = chain(conn, project, 2)
    with refused(store, psycopg.errors.ForeignKeyViolation) as conn:
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (top,))
    with store.connect() as conn:
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (child,))
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (top,))


def test_edges(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, events = chain(conn, project, 2)
        worker = node(conn, project, 'repository-worker')['id']
        server = node(conn, project, 'prod-1', layer='infrastructure', kind='server')['id']
        made = edge(conn, project, events, worker)
        assert (made['revision'], made['description']) == (1, '')
        edge(conn, project, server, api, 'instance_of')  # across layers
        edge(conn, project, events, worker, 'uses')  # another type between the same nodes
    with refused(store, psycopg.errors.UniqueViolation) as conn:
        edge(conn, project, events, worker)
    with refused(store, psycopg.errors.CheckViolation) as conn:
        edge(conn, project, worker, worker)
    with refused(store, psycopg.errors.CheckViolation) as conn:
        edge(conn, project, worker, api, 'sends')
    for a, b in [(api, events), (events, api)]:  # a node and the node that contains it
        with refused(store, psycopg.errors.CheckViolation, 'An edge cannot join a node and a node that contains it.') as conn:
            edge(conn, project, a, b, 'depends_on')
    with store.connect() as conn:  # a move may leave such an edge; the view counts it as internal
        conn.execute('UPDATE ka.kg_nodes SET parent_id=%s WHERE id=%s', (events, worker))
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (worker,))
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_edges WHERE %s IN (from_id,to_id)', (worker,)).fetchone()['n'] == 0


def test_document_links_cascade(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    made = client.post('/api/documentation/documents', headers=HEADERS, json={
        'slug': 'runbook', 'title': 'Runbook', 'markdown': '# Runbook\n'})
    assert made.status_code == 201, made.text
    document = made.json()['id']
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a = node(conn, project)['id']
        b = node(conn, project)['id']
        for n in (a, b):
            conn.execute('INSERT INTO ka.kg_node_documents(node_id,document_id,project_id) VALUES (%s,%s,%s)', (n, document, project))
        conn.execute('DELETE FROM ka.kg_nodes WHERE id=%s', (a,))
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_node_documents').fetchone()['n'] == 1
    assert client.delete(f'/api/documentation/documents/{document}?revision=1&confirmed=true', headers=HEADERS).status_code in (200, 204)
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_node_documents').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes WHERE id=%s', (b,)).fetchone()['n'] == 1


def test_down_and_up(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a, _ = chain(conn, project, 2)
        edge(conn, project, a, node(conn, project)['id'])
        run(conn, '0093_graph_inventory_sync.down.sql')  # 0093 builds on 0092
        run(conn, '0092_system_graph.down.sql')
        assert conn.execute("SELECT to_regclass('ka.kg_nodes') IS NULL AND to_regclass('ka.kg_edges') IS NULL "
                            "AND to_regclass('ka.kg_node_documents') IS NULL AS gone").fetchone()['gone']
        assert conn.execute("SELECT count(*) AS n FROM pg_proc WHERE proname IN ('kg_nodes_place','kg_edges_check')").fetchone()['n'] == 0
        run(conn, '0092_system_graph.up.sql')
        run(conn, '0093_graph_inventory_sync.up.sql')
        assert conn.execute("SELECT to_regclass('ka.kg_nodes_sibling_name') IS NOT NULL AS found").fetchone()['found']
