"""graph_sync: inventory rows keep linked graph nodes and synced edges, written by one reconcile in the caller's transaction."""
from uuid import UUID, uuid4

from knowledge_admin.graph_sync import SyncResult, sync_all, sync_inventory
from knowledge_admin.repositories import RepositoryInput, RepositoryStore
from test_api import _client
from test_repositories import HEADERS


def sync(conn, project):
    return sync_inventory(conn, project)


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
    # an environment's own node, not the server copies placed in it
    extra = ' AND server_id IS NULL' if link == 'environment_id' else ''
    return conn.execute(f'SELECT * FROM ka.kg_nodes WHERE {link}=%s{extra}', (row_id,)).fetchone()


def edges(conn) -> set[tuple]:
    rows = conn.execute("""SELECT f.name AS a,t.name AS b,e.type,e.synced FROM ka.kg_edges e
        JOIN ka.kg_nodes f ON f.id=e.from_id JOIN ka.kg_nodes t ON t.id=e.to_id""").fetchall()
    return {(row['a'], row['b'], row['type'], row['synced']) for row in rows}


def test_rows_get_linked_nodes(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        host, api, prod = server(conn, project), service(conn, project), environment(conn, project)
        conn.commit()  # store.save() writes on its own connection
        repo_id = store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', '')
        sync(conn, project)
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
        sync(conn, project)
        conn.execute("UPDATE ka.servers SET name='prod-01' WHERE id=%s", (host['id'],))
        sync(conn, project)
        renamed = node(conn, 'server_id', host['id'])
        assert (renamed['name'], renamed['revision']) == ('prod-01', 2)
        conn.execute("UPDATE ka.servers SET hostname='other.internal' WHERE id=%s", (host['id'],))
        sync(conn, project)
        assert node(conn, 'server_id', host['id'])['revision'] == 2  # only name changes touch the node


def test_deployments_follow_their_server_and_environment(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, one, two = service(conn, project), server(conn, project, 'prod-1'), server(conn, project, 'prod-2')
        made = deployment(conn, project, api['id'], one['id'])
        sync(conn, project)
        instance = node(conn, 'deployment_id', made['id'])
        assert (instance['layer'], instance['kind'], instance['name'], instance['parent_id']) == (
            'infrastructure', 'instance', 'admin-api · production', node(conn, 'server_id', one['id'])['id'])
        assert ('admin-api · production', 'admin-api', 'instance_of', True) in edges(conn)
        conn.execute("UPDATE ka.service_deployments SET server_id=%s WHERE id=%s", (two['id'], made['id']))
        sync(conn, project)
        assert node(conn, 'deployment_id', made['id'])['parent_id'] == node(conn, 'server_id', two['id'])['id']
        conn.execute("UPDATE ka.service_deployments SET environment='staging', runtime_name='api-blue' WHERE id=%s", (made['id'],))
        sync(conn, project)
        assert node(conn, 'deployment_id', made['id'])['name'] == 'api-blue · staging'
        conn.execute("UPDATE ka.service_deployments SET runtime_name='' WHERE id=%s", (made['id'],))
        conn.execute("UPDATE ka.services SET name='admin' WHERE id=%s", (api['id'],))
        sync(conn, project)
        assert node(conn, 'deployment_id', made['id'])['name'] == 'admin · staging'  # service renames reach deployments


def test_relation_tables_make_synced_edges(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, worker = service(conn, project, 'admin-api'), service(conn, project, 'worker')
        host, prod = server(conn, project), environment(conn, project)
        conn.commit()  # store.save() writes on its own connection
        repo_id = store.save('shop', RepositoryInput(remote_url='example/api', token='private'), 'admin', '')
        sync(conn, project)
        repo_name = node(conn, 'repository_id', repo_id)['name']
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)',
                     (project, api['id'], worker['id']))
        conn.execute('INSERT INTO ka.service_repositories(project_id,service_id,repository_id) VALUES (%s,%s,%s)',
                     (project, api['id'], repo_id))
        conn.execute('INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                     (project, prod['id'], host['id']))
        conn.execute('INSERT INTO ka.environment_services(project_id,environment_id,service_id) VALUES (%s,%s,%s)',
                     (project, prod['id'], api['id']))
        sync(conn, project)
        assert edges(conn) == {('admin-api', 'worker', 'depends_on', True), ('admin-api', repo_name, 'uses', True),
                               ('production', 'admin-api', 'includes', True)}
        assert copy(conn, host['id'], prod['id'])['parent_id'] == node(conn, 'environment_id', prod['id'])['id']
        conn.execute('DELETE FROM ka.service_dependencies WHERE service_id=%s', (api['id'],))
        conn.execute('DELETE FROM ka.environment_servers WHERE server_id=%s', (host['id'],))
        sync(conn, project)
        assert edges(conn) == {('admin-api', repo_name, 'uses', True), ('production', 'admin-api', 'includes', True)}
        conn.execute('DELETE FROM ka.environments WHERE id=%s', (prod['id'],))  # cascades its memberships
        sync(conn, project)
        assert edges(conn) == {('admin-api', repo_name, 'uses', True)} and node(conn, 'environment_id', prod['id']) is None


def test_hand_made_edges_are_left_alone(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, worker = service(conn, project, 'admin-api'), service(conn, project, 'worker')
        sync(conn, project)
        a, w = node(conn, 'service_id', api['id'])['id'], node(conn, 'service_id', worker['id'])['id']
        conn.execute("INSERT INTO ka.kg_edges(project_id,from_id,to_id,type) VALUES (%s,%s,%s,'depends_on')", (project, a, w))
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)',
                     (project, api['id'], worker['id']))
        sync(conn, project)
        conn.execute('DELETE FROM ka.service_dependencies WHERE service_id=%s', (api['id'],))
        sync(conn, project)
        assert edges(conn) == {('admin-api', 'worker', 'depends_on', False)}


def test_clash_takes_a_suffix(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'infrastructure','server','prod-1'),
            (%s,'infrastructure','server','prod-1 (2)')""", (project, project))
        host = server(conn, project, 'PROD-1')
        sync(conn, project)
        assert node(conn, 'server_id', host['id'])['name'] == 'PROD-1 (3)'
        conn.execute("UPDATE ka.servers SET name='unique' WHERE id=%s", (host['id'],))
        sync(conn, project)
        assert node(conn, 'server_id', host['id'])['name'] == 'unique'
        long_name = 'x' * 160
        conn.execute("INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'software','service',%s)", (project, long_name))
        made = service(conn, project, long_name)
        sync(conn, project)
        assert len(node(conn, 'service_id', made['id'])['name']) == 160


def test_delete_removes_bare_nodes(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
        made = deployment(conn, project, api['id'], host['id'])
        sync(conn, project)
        conn.execute('DELETE FROM ka.service_deployments WHERE id=%s', (made['id'],))
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))
        conn.execute('DELETE FROM ka.services WHERE id=%s', (api['id'],))
        sync(conn, project)
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_edges').fetchone()['n'] == 0


def test_delete_keeps_nodes_with_children_or_documents(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    made = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'runbook', 'title': 'Runbook', 'markdown': '# Runbook\n'})
    assert made.status_code == 201, made.text
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
        sync(conn, project)
        host_node = node(conn, 'server_id', host['id'])
        conn.execute("UPDATE ka.kg_nodes SET description='Main host' WHERE id=%s", (host_node['id'],))
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id)
            VALUES (%s,'infrastructure','disk','data disk',%s)""", (project, host_node['id']))
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))
        sync(conn, project)
        kept = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (host_node['id'],)).fetchone()
        assert kept['server_id'] is None
        assert kept['description'].startswith('Main host\nRemoved from Servers on ')
        api_node = node(conn, 'service_id', api['id'])
        conn.execute('INSERT INTO ka.kg_node_documents(node_id,document_id,project_id) VALUES (%s,%s,%s)',
                     (api_node['id'], made.json()['id'], project))
        conn.execute('DELETE FROM ka.services WHERE id=%s', (api['id'],))
        sync(conn, project)
        kept = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (api_node['id'],)).fetchone()
        assert kept['service_id'] is None and kept['description'].startswith('Removed from Services on ')


def test_the_first_sync_fills_the_graph(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host, prod = service(conn, project), server(conn, project), environment(conn, project)
        deployment(conn, project, api['id'], host['id'])
        conn.execute('INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                     (project, prod['id'], host['id']))
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'software','service','admin-api')""", (project,))
        old = conn.execute("""INSERT INTO ka.repositories(project_id,slug,name,remote_url,archived_at)
            VALUES (%s,'repository-old','example/old','https://github.com/example/old',now()) RETURNING id""", (project,)).fetchone()['id']
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes').fetchone()['n'] == 1  # only the hand-made one
        result = sync(conn, project)
        assert result.created == 5 and result.edges_added == 1  # the server has a copy in production and a top-level one
        assert node(conn, 'service_id', api['id'])['name'] == 'admin-api (2)'
        assert node(conn, 'deployment_id', conn.execute('SELECT id FROM ka.service_deployments').fetchone()['id'])['parent_id'] == \
            copy(conn, host['id'], None)['id']  # the deployment has no environment, so it sits under the top-level copy
        assert copy(conn, host['id'], prod['id'])['parent_id'] == node(conn, 'environment_id', prod['id'])['id']
        assert node(conn, 'repository_id', old) is None


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
        sync(conn, project)
        kept_node = node(conn, 'repository_id', kept)
        conn.execute("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id) VALUES (%s,'software','module','child',%s)",
                     (project, kept_node['id']))
        conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id IN (%s,%s)', (bare, kept))
        sync(conn, project)
        assert node(conn, 'repository_id', bare) is None
        assert {e[1] for e in edges(conn) if e[2] == 'uses'} == set()
        left = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (kept_node['id'],)).fetchone()
        assert left['repository_id'] is None and 'Removed from Code projects on ' in left['description']
        conn.execute('UPDATE ka.repositories SET archived_at=NULL WHERE id=%s', (bare,))
        sync(conn, project)
        assert node(conn, 'repository_id', bare) is not None
        assert len([e for e in edges(conn) if e[2] == 'uses']) == 1


def test_a_second_sync_changes_nothing(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
        deployment(conn, project, api['id'], host['id'])
        sync(conn, project)
        before = conn.execute('SELECT version FROM ka.kg_graph_version').fetchone()['version']
        revisions = conn.execute('SELECT id,revision FROM ka.kg_nodes ORDER BY id').fetchall()
        assert sync(conn, project) == SyncResult()
        assert conn.execute('SELECT version FROM ka.kg_graph_version').fetchone()['version'] == before
        assert conn.execute('SELECT id,revision FROM ka.kg_nodes ORDER BY id').fetchall() == revisions


def test_the_sync_repairs_what_changed_behind_its_back(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, worker = service(conn, project, 'admin-api'), service(conn, project, 'worker')
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)',
                     (project, api['id'], worker['id']))
        sync(conn, project)
        conn.execute("UPDATE ka.kg_nodes SET name='renamed by hand', parent_id=NULL WHERE service_id=%s", (api['id'],))
        conn.execute('DELETE FROM ka.kg_edges WHERE synced')
        result = sync(conn, project)
        assert (result.updated, result.edges_added) == (1, 1)
        assert node(conn, 'service_id', api['id'])['name'] == 'admin-api'
        assert ('admin-api', 'worker', 'depends_on', True) in edges(conn)


def test_sync_all_fills_each_app_and_survives_failures(store, caplog):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        server(conn, project)
    sync_all(store, ['shop'])
    with store.connect() as conn:
        assert node(conn, 'server_id', conn.execute('SELECT id FROM ka.servers').fetchone()['id']) is not None
    broken = RepositoryStore('host=127.0.0.1 port=1 dbname=none connect_timeout=1', b'k' * 32, 'http://identity.test/realms/docs')
    sync_all(broken, ['shop'])  # must not raise
    assert 'Graph inventory sync failed' in caplog.text


def test_a_kept_node_keeps_its_name_for_siblings(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
        first = deployment(conn, project, api['id'], host['id'])
        sync(conn, project)
        kept = node(conn, 'deployment_id', first['id'])
        conn.execute("INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id) VALUES (%s,'infrastructure','disk','d',%s)",
                     (project, kept['id']))
        conn.execute('DELETE FROM ka.service_deployments WHERE id=%s', (first['id'],))
        second = deployment(conn, project, api['id'], host['id'])
        sync(conn, project)
        assert node(conn, 'deployment_id', second['id'])['name'] == 'admin-api · production (2)'
        left = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (kept['id'],)).fetchone()
        assert left['name'] == 'admin-api · production' and left['deployment_id'] is None
        assert 'Removed from Services (deployment) on ' in left['description']


def test_renames_that_swap_names_settle_in_one_sync(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        api, host = service(conn, project), server(conn, project)
        a, b = (conn.execute("""INSERT INTO ka.service_deployments(id,project_id,service_id,server_id,environment,runtime_name)
            VALUES (%s,%s,%s,%s,'production',%s) RETURNING *""", (i, project, api['id'], host['id'], name)).fetchone()
                for i, name in ((UUID(int=1), 'blue'), (UUID(int=2), 'green')))
        sync(conn, project)
        conn.execute("UPDATE ka.service_deployments SET runtime_name='green' WHERE id=%s", (a['id'],))
        conn.execute("UPDATE ka.service_deployments SET runtime_name='blue' WHERE id=%s", (b['id'],))
        sync(conn, project)
        assert node(conn, 'deployment_id', a['id'])['name'] == 'green · production'
        assert node(conn, 'deployment_id', b['id'])['name'] == 'blue · production'
        assert sync(conn, project) == SyncResult()


def test_start_up_sync_retries_once_after_a_deadlock(store, caplog, monkeypatch):
    import psycopg
    from knowledge_admin import graph_sync
    with store.connect() as conn:
        server(conn, store.project(conn, 'shop'))
    real, calls = graph_sync.sync_inventory, []

    def flaky(conn, project):
        calls.append(1)
        if len(calls) == 1:
            raise psycopg.errors.DeadlockDetected()
        return real(conn, project)

    monkeypatch.setattr(graph_sync, 'sync_inventory', flaky)
    sync_all(store, ['shop'])
    assert len(calls) == 2 and 'failed' not in caplog.text
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes').fetchone()['n'] == 1


def test_a_suffix_after_a_cut_name_has_no_double_space():
    from knowledge_admin.graph_sync import MAX_NAME, free_name
    wanted = 'a' * 155 + ' ' + 'b' * 20  # the suffix cut ends on a space
    taken = {wanted[:MAX_NAME].lower()}
    name = free_name(taken, wanted)
    assert name.endswith(' (2)') and '  ' not in name and len(name) <= MAX_NAME
    assert free_name({'x', 'x (2)'}, 'x') == 'x (3)'


def member(conn, project, environment_id, server_id):
    conn.execute('INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)',
                 (project, environment_id, server_id))


def copy(conn, server_id, environment_id):
    return conn.execute('SELECT * FROM ka.kg_nodes WHERE server_id=%s AND environment_id IS NOT DISTINCT FROM %s',
                        (server_id, environment_id)).fetchone()


def test_environments_hold_their_servers_and_servers_their_deployments(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prod, api = environment(conn, project, 'production'), service(conn, project, 'admin-api')
        host, spare = server(conn, project, 'prod-1'), server(conn, project, 'spare')
        member(conn, project, prod['id'], host['id'])
        made = conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id)
            VALUES (%s,%s,%s,'production',%s) RETURNING id""", (project, api['id'], host['id'], prod['id'])).fetchone()
        sync(conn, project)
        top = {row['name'] for row in conn.execute(
            "SELECT name FROM ka.kg_nodes WHERE layer='infrastructure' AND parent_id IS NULL").fetchall()}
        assert top == {'production', 'spare'}
        placed = copy(conn, host['id'], prod['id'])
        assert (placed['name'], placed['parent_id']) == ('prod-1', node(conn, 'environment_id', prod['id'])['id'])
        assert copy(conn, host['id'], None) is None
        assert node(conn, 'deployment_id', made['id'])['parent_id'] == placed['id']
        assert not any(kind == 'includes' and b == 'prod-1' for _, b, kind, _ in edges(conn))
        assert sync(conn, project) == SyncResult()


def test_a_server_in_two_environments_has_a_node_in_each(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prod, staging = environment(conn, project, 'production'), environment(conn, project, 'staging')
        api, host = service(conn, project), server(conn, project)
        member(conn, project, prod['id'], host['id'])
        made = conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id)
            VALUES (%s,%s,%s,'staging',%s) RETURNING id""", (project, api['id'], host['id'], staging['id'])).fetchone()
        sync(conn, project)
        in_prod, in_staging = copy(conn, host['id'], prod['id']), copy(conn, host['id'], staging['id'])
        assert in_prod['id'] != in_staging['id']  # staging holds it through the deployment, production through membership
        assert node(conn, 'deployment_id', made['id'])['parent_id'] == in_staging['id']
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes WHERE parent_id=%s', (in_prod['id'],)).fetchone()['n'] == 0


def test_a_deployment_without_an_environment_sits_under_the_top_level_copy(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prod, api, host = environment(conn, project, 'production'), service(conn, project), server(conn, project)
        member(conn, project, prod['id'], host['id'])
        legacy = deployment(conn, project, api['id'], host['id'], environment='legacy')  # environment_id NULL
        sync(conn, project)
        top = copy(conn, host['id'], None)
        assert top is not None and top['parent_id'] is None and copy(conn, host['id'], prod['id']) is not None
        assert node(conn, 'deployment_id', legacy['id'])['parent_id'] == top['id']


def test_removing_a_server_from_an_environment_releases_that_copy(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prod, staging, host = environment(conn, project, 'production'), environment(conn, project, 'staging'), server(conn, project)
        member(conn, project, prod['id'], host['id'])
        member(conn, project, staging['id'], host['id'])
        sync(conn, project)
        kept = copy(conn, host['id'], staging['id'])
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id)
            VALUES (%s,'infrastructure','disk','disk',%s)""", (project, kept['id']))
        conn.execute('DELETE FROM ka.environment_servers WHERE environment_id=%s', (staging['id'],))
        sync(conn, project)
        after = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (kept['id'],)).fetchone()
        assert (after['server_id'], after['environment_id']) == (None, None)
        assert 'Removed from Environments on ' in after['description']
        assert copy(conn, host['id'], prod['id']) is not None


def test_an_existing_server_node_moves_into_its_environment(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        staging, production, host = environment(conn, project, 'staging'), environment(conn, project, 'production'), server(conn, project)
        old = conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,server_id)
            VALUES (%s,'infrastructure','server','prod-1',%s) RETURNING id""", (project, host['id'])).fetchone()['id']  # 0093 era
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id)
            VALUES (%s,'infrastructure','disk','disk',%s)""", (project, old))
        member(conn, project, staging['id'], host['id'])
        member(conn, project, production['id'], host['id'])
        sync(conn, project)
        moved = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (old,)).fetchone()
        assert moved['environment_id'] == production['id']  # the first environment by name
        assert moved['parent_id'] == node(conn, 'environment_id', production['id'])['id']
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes WHERE parent_id=%s', (old,)).fetchone()['n'] == 1
        assert sync(conn, project) == SyncResult()


def test_moving_a_server_to_another_environment_keeps_its_node(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a, b, host = environment(conn, project, 'a'), environment(conn, project, 'b'), server(conn, project)
        member(conn, project, a['id'], host['id'])
        sync(conn, project)
        before = copy(conn, host['id'], a['id'])['id']
        conn.execute('DELETE FROM ka.environment_servers WHERE server_id=%s', (host['id'],))
        member(conn, project, b['id'], host['id'])
        sync(conn, project)
        assert copy(conn, host['id'], b['id'])['id'] == before and copy(conn, host['id'], a['id']) is None


def test_renaming_or_deleting_a_server_reaches_every_copy(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a, b, host = environment(conn, project, 'a'), environment(conn, project, 'b'), server(conn, project)
        member(conn, project, a['id'], host['id'])
        member(conn, project, b['id'], host['id'])
        sync(conn, project)
        conn.execute("UPDATE ka.servers SET name='prod-01' WHERE id=%s", (host['id'],))
        sync(conn, project)
        assert {copy(conn, host['id'], e['id'])['name'] for e in (a, b)} == {'prod-01'}
        conn.execute('DELETE FROM ka.environment_servers WHERE server_id=%s', (host['id'],))
        conn.execute('DELETE FROM ka.servers WHERE id=%s', (host['id'],))
        sync(conn, project)
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes WHERE layer=%s AND kind=%s', ('infrastructure', 'server')).fetchone()['n'] == 0


def test_a_moved_server_copy_gets_a_free_name_when_its_name_is_taken_at_the_new_level(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        a, host = environment(conn, project, 'a'), server(conn, project, 'prod-1')
        member(conn, project, a['id'], host['id'])
        conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name) VALUES (%s,'infrastructure','server','prod-1')""", (project,))
        sync(conn, project)
        before = copy(conn, host['id'], a['id'])['id']
        conn.execute('DELETE FROM ka.environment_servers WHERE server_id=%s', (host['id'],))
        sync(conn, project)
        moved = copy(conn, host['id'], None)
        assert (moved['id'], moved['name'], moved['parent_id']) == (before, 'prod-1 (2)', None)
        assert sync(conn, project) == SyncResult()


def test_a_moved_server_copy_gets_a_free_name_when_its_environment_is_deleted(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        qa, clash, host = environment(conn, project, 'qa'), environment(conn, project, 'staging'), server(conn, project, 'staging')
        member(conn, project, qa['id'], host['id'])
        sync(conn, project)
        before = copy(conn, host['id'], qa['id'])['id']
        conn.execute('DELETE FROM ka.environments WHERE id=%s', (qa['id'],))
        sync(conn, project)
        moved = copy(conn, host['id'], None)
        assert (moved['id'], moved['name'], moved['parent_id']) == (before, 'staging (2)', None)
        assert node(conn, 'environment_id', clash['id'])['name'] == 'staging'
        assert sync(conn, project) == SyncResult()


def shells(conn):
    """Infrastructure nodes that are not linked to anything and hold nothing: empty leftovers."""
    return [row['name'] for row in conn.execute("""SELECT name FROM ka.kg_nodes n WHERE layer='infrastructure'
        AND server_id IS NULL AND environment_id IS NULL AND deployment_id IS NULL
        AND NOT EXISTS (SELECT 1 FROM ka.kg_nodes c WHERE c.parent_id=n.id)""")]


def deployment_in(conn, project, service_id, server_id, environment_id, label, runtime_name=''):
    return conn.execute("""INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id,runtime_name)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""", (project, service_id, server_id, label, environment_id, runtime_name)).fetchone()


def test_a_released_server_copy_whose_deployment_moves_away_leaves_no_empty_node(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        e1, e2 = environment(conn, project, 'e1'), environment(conn, project, 'e2')
        api, host = service(conn, project), server(conn, project, 's')
        member(conn, project, e2['id'], host['id'])
        made = deployment_in(conn, project, api['id'], host['id'], e1['id'], 'e1')
        sync(conn, project)
        conn.execute("UPDATE ka.service_deployments SET environment_id=%s,environment='e2' WHERE id=%s", (e2['id'], made['id']))
        sync(conn, project)
        assert shells(conn) == []
        assert copy(conn, host['id'], e1['id']) is None
        assert node(conn, 'deployment_id', made['id'])['parent_id'] == copy(conn, host['id'], e2['id'])['id']
        assert sync(conn, project) == SyncResult()


def test_deleting_an_environment_does_not_leave_two_empty_nodes_kept_by_each_other(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        e1 = environment(conn, project, 'e1')
        api, host = service(conn, project), server(conn, project, 's')
        deployment_in(conn, project, api['id'], host['id'], e1['id'], 'e1')
        deployment_in(conn, project, api['id'], host['id'], None, 'legacy', 'r2')
        sync(conn, project)
        conn.execute('DELETE FROM ka.environments WHERE id=%s', (e1['id'],))
        sync(conn, project)
        assert shells(conn) == []
        assert conn.execute("SELECT count(*) AS n FROM ka.kg_nodes WHERE server_id=%s", (host['id'],)).fetchone()['n'] == 1
        assert sync(conn, project) == SyncResult()


def test_a_server_copy_too_deep_to_move_stays_unlinked_and_a_new_copy_is_made(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        e1, host = environment(conn, project, 'e1'), server(conn, project, 's')
        sync(conn, project)
        old = copy(conn, host['id'], None)['id']
        parent = old
        for i in range(11):
            parent = conn.execute("""INSERT INTO ka.kg_nodes(project_id,layer,kind,name,parent_id)
                VALUES (%s,'infrastructure','x',%s,%s) RETURNING id""", (project, f'n{i}', parent)).fetchone()['id']
        member(conn, project, e1['id'], host['id'])
        sync(conn, project)
        kept = conn.execute('SELECT * FROM ka.kg_nodes WHERE id=%s', (old,)).fetchone()
        assert (kept['parent_id'], kept['server_id'], kept['environment_id']) == (None, None, None)
        assert 'Removed from Environments' in kept['description']
        assert conn.execute('SELECT count(*) AS n FROM ka.kg_nodes WHERE parent_id=%s', (old,)).fetchone()['n'] == 1
        fresh = copy(conn, host['id'], e1['id'])
        assert fresh['id'] != old and fresh['parent_id'] == node(conn, 'environment_id', e1['id'])['id']
        assert sync(conn, project) == SyncResult()
