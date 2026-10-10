"""The graph in memory: built from the database, rebuilt whole when the stored version moves."""
import psycopg

from knowledge_admin.graph_memory import GraphMemory


def add(conn, project, name, layer='software', parent=None, kind='service'):
    return conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id) VALUES (%s,%s,%s,%s,%s)
        RETURNING id""", (project, layer, kind, name, parent)).fetchone()['id']


def test_builds_from_the_database_and_keeps_the_snapshot_while_nothing_changes(store):
    memory = GraphMemory()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api = add(conn, project, 'admin-api')
        events = add(conn, project, 'Event processor', parent=api, kind='module')
        worker = add(conn, project, 'worker')
        edge = conn.execute("INSERT INTO ka.kg_edges(project_id,from_id,to_id,type) VALUES (%s,%s,%s,'calls') RETURNING id",
                            (project, events, worker)).fetchone()['id']
    with store.connect() as conn:
        first = memory.get(conn, project)
        assert memory.get(conn, project) is first  # same version, same object
    assert {row['name'] for row in first.nodes()} == {'admin-api', 'Event processor', 'worker'}
    assert first.edges() == [{'id': edge, 'from_id': events, 'to_id': worker, 'type': 'calls', 'synced': False}]
    assert first.graph.has_edge(events, worker)
    assert [row['name'] for row in first.search('EVENT', None, 50)] == ['Event processor']
    assert first.search('event', None, 50)[0]['path'] == ['admin-api']


def test_views_follow_writes(store):
    memory = GraphMemory()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        add(conn, project, 'admin-api')
    with store.connect() as conn:
        before = memory.get(conn, project)
    with store.connect() as conn:
        add(conn, project, 'worker')
    with store.connect() as conn:
        after = memory.get(conn, project)
    assert after is not before and after.version > before.version
    assert {row['name'] for row in after.nodes()} == {'admin-api', 'worker'}


def test_another_connection_sees_the_new_version(store):
    one, two = GraphMemory(), GraphMemory()  # two processes
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        add(conn, project, 'a')
        one.get(conn, project), two.get(conn, project)
    with store.connect() as conn:
        add(conn, project, 'b')
    with store.connect() as conn:
        assert {row['name'] for row in two.get(conn, project).nodes()} == {'a', 'b'}


def test_search_is_literal_and_limited(store):
    memory = GraphMemory()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        add(conn, project, '100% done')
        add(conn, project, '1000 done')
        add(conn, project, 'Events table', layer='data', kind='table')
        snapshot = memory.get(conn, project)
    assert [row['name'] for row in snapshot.search('%', None, 50)] == ['100% done']
    assert [row['name'] for row in snapshot.search('', 'data', 50)] == ['Events table']
    assert len(snapshot.search('', None, 2)) == 2
