"""0093: graph nodes can link to inventory rows (deferred foreign keys), edges can be synced, and every graph change
bumps one version number (and notifies ka_graph)."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def server(conn, project, name='prod-1') -> dict:
    return conn.execute("""INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,%s,%s,false) RETURNING *""",
                        (project, name, f'{uuid4().hex[:8]}.internal')).fetchone()


def version(conn) -> int:
    return conn.execute('SELECT version FROM ka.kg_graph_version').fetchone()['version']


def test_link_rules(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host = server(conn, project)
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id)
            VALUES (%s,'infrastructure','server','prod-1',%s)""", (project, host['id']))
    for sql, values, error in [
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id) VALUES (%s,'software','x','x',%s)",
         (project, host['id']), psycopg.errors.CheckViolation),                       # layer must match the link
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id) VALUES (%s,'infrastructure','x','y',%s)",
         (project, host['id']), psycopg.errors.UniqueViolation),                      # one node per row
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id,environment_id) VALUES (%s,'infrastructure','x','z',%s,%s)",
         (project, host['id'], uuid4()), psycopg.errors.CheckViolation)]:             # one link per node
        with pytest.raises(error), store.connect() as conn:
            conn.execute(sql, values)
    with store.connect() as conn:
        includes = conn.execute("""SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint
            WHERE conname='kg_edges_type' AND conrelid='ka.kg_edges'::regclass""").fetchone()['d']
        assert "'includes'" in includes


def test_a_delete_without_a_sync_fails_at_commit(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host = server(conn, project)
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id)
            VALUES (%s,'infrastructure','server','prod-1',%s)""", (project, host['id']))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), store.connect() as conn:
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))  # the link is checked at commit
    with store.connect() as conn:  # unlinking in the same transaction (what the sync does) lets it commit
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))
        conn.execute('UPDATE ka.kg_nodes SET server_id=NULL WHERE server_id=%s', (host['id'],))


def test_every_graph_statement_bumps_the_version_and_notifies(store, tmp_path):
    from test_api import _client
    from test_repositories import HEADERS
    created = _client(tmp_path, repository_store=store).post(
        '/api/documentation/documents', headers=HEADERS, json={'slug': 'runbook', 'title': 'Runbook', 'markdown': '# Runbook\n'})
    document = created.json()['id']
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        start = version(conn)
        node = conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'software','service','a'),
            (%s,'software','service','b') RETURNING id""", (project, project)).fetchall()
        assert version(conn) == start + 1  # one statement, one bump
        conn.execute("INSERT INTO ka.kg_edges(project_id,from_id,to_id,type) VALUES (%s,%s,%s,'calls')",
                     (project, node[0]['id'], node[1]['id']))
        conn.execute("UPDATE ka.kg_nodes SET description='x' WHERE id=%s", (node[0]['id'],))
        assert version(conn) == start + 3
        conn.execute('UPDATE ka.documents SET slug=%s WHERE id=%s', ('notes', document))
        assert version(conn) == start + 3  # editing a document no node uses leaves the graph alone
        conn.execute('INSERT INTO ka.kg_node_documents(project_id,node_id,document_id) VALUES (%s,%s,%s)',
                     (project, node[0]['id'], document))
        assert version(conn) == start + 4
        conn.execute('UPDATE ka.documents SET slug=%s WHERE id=%s', ('runbook', document))
        assert version(conn) == start + 5  # editing an attached document moves the titles the graph shows
    listener = psycopg.connect(store.conninfo, autocommit=True)
    try:
        listener.execute('LISTEN ka_graph')
        with store.connect() as conn:
            conn.execute("UPDATE ka.kg_nodes SET description='y' WHERE id=%s", (node[0]['id'],))
        notice = next(listener.notifies(timeout=5))
        assert (notice.channel, int(notice.payload)) == ('ka_graph', start + 6)
    finally:
        listener.close()


def test_down_and_up(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host = server(conn, project)
        linked = conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id)
            VALUES (%s,'infrastructure','server','prod-1',%s) RETURNING id""", (project, host['id'])).fetchone()['id']
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id)
            VALUES (%s,'infrastructure','disk','disk',%s)""", (project, linked))  # keeps its linked parent
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id)
            VALUES (%s,'infrastructure','server','prod-2',%s)""", (project, server(conn, project, 'prod-2')['id']))
        run(conn, '0093_graph_inventory_sync.down.sql')
        names = {row['name'] for row in conn.execute('SELECT name FROM ka.kg_nodes').fetchall()}
        assert names == {'prod-1', 'disk'}  # the bare linked node went, the one with a child stayed (unlinked)
        assert conn.execute("SELECT to_regclass('ka.kg_graph_version') IS NULL AS gone").fetchone()['gone']
        run(conn, '0093_graph_inventory_sync.up.sql')
        assert version(conn) == 0
