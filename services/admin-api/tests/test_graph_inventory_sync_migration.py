"""0093: inventory rows keep linked graph nodes and synced edges, in the same transaction."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from knowledge_admin.repositories import RepositoryInput
from test_api import _client
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def server(conn, project, name='prod-1') -> dict:
    return conn.execute("""INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,%s,%s,false) RETURNING *""",
                        (project, name, f'{uuid4().hex[:8]}.internal')).fetchone()


def service(conn, project, name='admin-api') -> dict:
    return conn.execute('INSERT INTO ka.services(project_id,name) VALUES (%s,%s) RETURNING *', (project, name)).fetchone()


def environment(conn, project, name='production') -> dict:
    return conn.execute('INSERT INTO ka.environments(project_id,name) VALUES (%s,%s) RETURNING *', (project, name)).fetchone()


def deployment(conn, project, service_id, server_id, environment='production', runtime_name='') -> dict:
    return conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,runtime_name)
        VALUES (%s,%s,%s,%s,%s) RETURNING *""", (project, service_id, server_id, environment, runtime_name)).fetchone()


def node(conn, link, row_id) -> dict | None:
    return conn.execute(f'SELECT * FROM ka.kg_nodes WHERE {link}=%s', (row_id,)).fetchone()


def edges(conn) -> set[tuple]:
    rows = conn.execute("""SELECT f.name AS a,t.name AS b,e.type,e.synced FROM ka.kg_edges e
        JOIN ka.kg_nodes f ON f.id=e.from_id JOIN ka.kg_nodes t ON t.id=e.to_id""").fetchall()
    return {(row['a'], row['b'], row['type'], row['synced']) for row in rows}


def test_rows_get_linked_nodes(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host, api, prod = server(conn, project), service(conn, project), environment(conn, project)
        conn.commit()  # store.save() writes on its own connection and needs the project lock released
        repo_id = store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', '')
        for link, row_id, layer, kind, name in [('server_id', host['id'], 'infrastructure', 'server', 'prod-1'),
                                                ('service_id', api['id'], 'software', 'service', 'admin-api'),
                                                ('environment_id', prod['id'], 'infrastructure', 'environment', 'production')]:
            found = node(conn, link, row_id)
            assert (found['layer'], found['kind'], found['name'], found['parent_id']) == (layer, kind, name, None)
        repo = node(conn, 'repository_id', repo_id)
        assert (repo['layer'], repo['kind'], repo['parent_id']) == ('software', 'repository', None)


def test_renames_follow_the_inventory(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host = server(conn, project)
        conn.execute("UPDATE ka.servers SET name='prod-01' WHERE id=%s", (host['id'],))
        renamed = node(conn, 'server_id', host['id'])
        assert (renamed['name'], renamed['revision']) == ('prod-01', 2)
        conn.execute("UPDATE ka.servers SET hostname='other.internal' WHERE id=%s", (host['id'],))
        assert node(conn, 'server_id', host['id'])['revision'] == 2  # only name changes touch the node


def test_deployments_follow_their_server_and_environment(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, one, two = service(conn, project), server(conn, project, 'prod-1'), server(conn, project, 'prod-2')
        made = deployment(conn, project, api['id'], one['id'])
        instance = node(conn, 'deployment_id', made['id'])
        assert (instance['layer'], instance['kind'], instance['name'], instance['parent_id']) == (
            'infrastructure', 'instance', 'admin-api · production', node(conn, 'server_id', one['id'])['id'])
        assert ('admin-api · production', 'admin-api', 'instance_of', True) in edges(conn)
        conn.execute("UPDATE ka.service_deployments SET server_id=%s WHERE id=%s", (two['id'], made['id']))
        assert node(conn, 'deployment_id', made['id'])['parent_id'] == node(conn, 'server_id', two['id'])['id']
        conn.execute("UPDATE ka.service_deployments SET environment='staging', runtime_name='api-blue' WHERE id=%s", (made['id'],))
        assert node(conn, 'deployment_id', made['id'])['name'] == 'api-blue · staging'
        conn.execute("UPDATE ka.service_deployments SET runtime_name='' WHERE id=%s", (made['id'],))
        conn.execute("UPDATE ka.services SET name='admin' WHERE id=%s", (api['id'],))
        assert node(conn, 'deployment_id', made['id'])['name'] == 'admin · staging'  # service renames reach deployments


def test_relation_tables_make_synced_edges(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, worker = service(conn, project, 'admin-api'), service(conn, project, 'worker')
        host, prod = server(conn, project), environment(conn, project)
        conn.commit()  # store.save() writes on its own connection and needs the project lock released
        repo_id = store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', '')
        repo_name = node(conn, 'repository_id', repo_id)['name']
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)',
                     (project, api['id'], worker['id']))
        conn.execute('INSERT INTO ka.service_repositories(project_id,service_id,repository_id) VALUES (%s,%s,%s)',
                     (project, api['id'], repo_id))
        conn.execute('INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                     (project, prod['id'], host['id']))
        conn.execute('INSERT INTO ka.environment_services(project_id,environment_id,service_id) VALUES (%s,%s,%s)',
                     (project, prod['id'], api['id']))
        assert edges(conn) == {('admin-api', 'worker', 'depends_on', True), ('admin-api', repo_name, 'uses', True),
                               ('production', 'prod-1', 'includes', True), ('production', 'admin-api', 'includes', True)}
        conn.execute('DELETE FROM ka.service_dependencies WHERE service_id=%s', (api['id'],))
        conn.execute('DELETE FROM ka.environment_servers WHERE server_id=%s', (host['id'],))
        assert edges(conn) == {('admin-api', repo_name, 'uses', True), ('production', 'admin-api', 'includes', True)}
        conn.execute('DELETE FROM ka.environments WHERE id=%s', (prod['id'],))  # cascades its memberships
        assert edges(conn) == {('admin-api', repo_name, 'uses', True)} and node(conn, 'environment_id', prod['id']) is None


def test_hand_made_edges_are_left_alone(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, worker = service(conn, project, 'admin-api'), service(conn, project, 'worker')
        a, w = node(conn, 'service_id', api['id'])['id'], node(conn, 'service_id', worker['id'])['id']
        conn.execute("INSERT INTO ka.kg_edges(project_id,from_id,to_id,type) VALUES (%s,%s,%s,'depends_on')", (project, a, w))
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)',
                     (project, api['id'], worker['id']))
        conn.execute('DELETE FROM ka.service_dependencies WHERE service_id=%s', (api['id'],))
        assert edges(conn) == {('admin-api', 'worker', 'depends_on', False)}


def test_clash_takes_a_suffix(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'infrastructure','server','prod-1'),
            (%s,'infrastructure','server','prod-1 (2)')""", (project, project))
        host = server(conn, project, 'PROD-1')
        assert node(conn, 'server_id', host['id'])['name'] == 'PROD-1 (3)'
        conn.execute("UPDATE ka.servers SET name='unique' WHERE id=%s", (host['id'],))
        assert node(conn, 'server_id', host['id'])['name'] == 'unique'
        long_name = 'x' * 160
        conn.execute("INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'software','service',%s)", (project, long_name))
        assert len(node(conn, 'service_id', service(conn, project, long_name)['id'])['name']) == 160


def test_delete_removes_bare_nodes(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
        made = deployment(conn, project, api['id'], host['id'])
        conn.execute('DELETE FROM ka.service_deployments WHERE id=%s', (made['id'],))
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))
        conn.execute('DELETE FROM ka.services WHERE id=%s', (api['id'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_edges').fetchone()['n'] == 0


def test_delete_keeps_nodes_with_children_or_documents(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    made = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'runbook', 'title': 'Runbook', 'markdown': '# Runbook\n'})
    assert made.status_code == 201, made.text
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
        host_node = node(conn, 'server_id', host['id'])
        conn.execute("UPDATE ka.kg_nodes SET description='Main host' WHERE id=%s", (host_node['id'],))
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id)
            VALUES (%s,'infrastructure','disk','data disk',%s)""", (project, host_node['id']))
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))
        kept = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (host_node['id'],)).fetchone()
        assert kept['server_id'] is None
        assert kept['description'].startswith('Main host\nRemoved from Servers on ')
        api_node = node(conn, 'service_id', api['id'])
        conn.execute('INSERT INTO ka.kg_node_documents(node_id,document_id,project_id) VALUES (%s,%s,%s)',
                     (api_node['id'], made.json()['id'], project))
        conn.execute('DELETE FROM ka.services WHERE id=%s', (api['id'],))
        kept = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (api_node['id'],)).fetchone()
        assert kept['service_id'] is None and kept['description'].startswith('Removed from Services on ')


def test_backfill_and_down_up(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        run(conn, '0093_graph_inventory_sync.down.sql')
        api, host, prod = service(conn, project), server(conn, project), environment(conn, project)
        deployment(conn, project, api['id'], host['id'])
        conn.execute('INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                     (project, prod['id'], host['id']))
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'software','service','admin-api')""", (project,))
        run(conn, '0093_graph_inventory_sync.up.sql')
        assert node(conn, 'service_id', api['id'])['name'] == 'admin-api (2)'
        assert node(conn, 'deployment_id', conn.execute('SELECT id FROM ka.service_deployments').fetchone()['id'])['parent_id'] == \
            node(conn, 'server_id', host['id'])['id']
        assert ('production', 'prod-1', 'includes', True) in edges(conn)
        run(conn, '0093_graph_inventory_sync.down.sql')
        names = {row['name'] for row in conn.execute('SELECT name FROM ka.kg_nodes').fetchall()}
        assert names == {'admin-api'}  # synced nodes with nothing attached are gone, the hand-made one stays
        assert conn.execute("SELECT count(*) AS n FROM information_schema.columns WHERE table_schema='ka' "
                            "AND table_name='kg_nodes' AND column_name='server_id'").fetchone()['n'] == 0
        run(conn, '0093_graph_inventory_sync.up.sql')


def test_link_rules(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
    for sql, values, error in [
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id) VALUES (%s,'software','x','x',%s)",
         (project, host['id']), psycopg.errors.CheckViolation),                       # layer must match the link
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id) VALUES (%s,'infrastructure','x','y',%s)",
         (project, host['id']), psycopg.errors.UniqueViolation),                      # one node per row
        ("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id,service_id) VALUES (%s,'infrastructure','x','z',%s,%s)",
         (project, uuid4(), api['id']), psycopg.errors.CheckViolation)]:              # one link per node
        with pytest.raises(error), store.connect() as conn:
            conn.execute(sql, values)
    with store.connect() as conn:
        includes = conn.execute("""SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint
            WHERE conname='kg_edges_type' AND conrelid='ka.kg_edges'::regclass""").fetchone()['d']
        assert "'includes'" in includes


def test_archiving_a_code_project_counts_as_deleting_it(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api = service(conn, project)
        conn.commit()
        bare = store.save('shop', RepositoryInput(remote_url='example/bare', token='private'), 'admin', '')
        kept = store.save('shop', RepositoryInput(remote_url='example/kept', token='private'), 'admin', '')
        for repo_id in (bare, kept):
            conn.execute('INSERT INTO ka.service_repositories(project_id,service_id,repository_id) VALUES (%s,%s,%s)',
                         (project, api['id'], repo_id))
        kept_node = node(conn, 'repository_id', kept)
        conn.execute("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id) VALUES (%s,'software','module','child',%s)",
                     (project, kept_node['id']))
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id IN (%s,%s)', (bare, kept))
        assert node(conn, 'repository_id', bare) is None
        assert {e[1] for e in edges(conn) if e[2] == 'uses'} == set()
        left = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (kept_node['id'],)).fetchone()
        assert left['repository_id'] is None and 'Removed from Code projects on ' in left['description']
        conn.execute('UPDATE ka.repositories SET archived_at=NULL WHERE id=%s', (bare,))
        assert node(conn, 'repository_id', bare) is not None
        assert len([e for e in edges(conn) if e[2] == 'uses']) == 1


def test_backfill_skips_archived_code_projects(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.commit()
        repo_id = store.save('shop', RepositoryInput(remote_url='example/old', token='private'), 'admin', '')
        run(conn, '0093_graph_inventory_sync.down.sql')
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (repo_id,))
        run(conn, '0093_graph_inventory_sync.up.sql')
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes').fetchone()['n'] == 0
