"""inventory/store.py: the one write path for servers, services, environments, deployments, connections and relations."""

from uuid import uuid4

import pytest
from knowledge_admin.inventory import store as inventory
from knowledge_admin.inventory.rules import Invalid


@pytest.fixture
def world(store):
    """A project with a person, an agent, two servers, two services, an environment and a deployment of each service."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'ops', 'ops@example.test')
        conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES ('ops-agent','Ops agent','')")
        author = {'actor_id': actor}
        w = {'project': project, 'actor': actor, 'author': author}
        w['a'] = inventory.create_server(conn, project, {'name': 'a', 'hostname': 'a.internal'}, author)
        w['b'] = inventory.create_server(conn, project, {'name': 'b', 'hostname': 'b.internal'}, author)
        w['api'] = inventory.create_service(conn, project, {'name': 'api', 'service_role': 'application'}, author)
        w['db'] = inventory.create_service(conn, project, {'name': 'db', 'service_role': 'database'}, author)
        w['prod'] = inventory.create_environment(conn, project, {'name': 'prod'}, author)
        w['api_a'] = inventory.create_deployment(
            conn,
            project,
            {'service_id': w['api']['id'], 'server_id': w['a']['id'], 'environment_id': w['prod']['id'], 'port': 8080},
            author,
        )
        w['db_b'] = inventory.create_deployment(
            conn,
            project,
            {'service_id': w['db']['id'], 'server_id': w['b']['id'], 'environment_id': w['prod']['id'], 'port': 5432},
            author,
        )
    return w


def audit(conn, resource_id):
    return conn.execute(
        'SELECT actor_id,action,resource_type,details FROM ka.audit_events WHERE resource_id=%s ORDER BY id',
        (resource_id,),
    ).fetchall()


def test_items_are_created_as_plain_dicts_with_audit(store, world):
    assert world['a']['name'] == 'a' and world['a']['revision'] == 1 and world['a']['ssh_allowed'] is False
    assert 'credential_id' not in world['a']
    assert (world['api']['service_role'], world['api']['revision']) == ('application', 1)
    deployment = world['api_a']
    assert (deployment['environment_id'], deployment['port'], deployment['revision']) == (world['prod']['id'], 8080, 1)
    with store.connect() as conn:
        for item, kind in [(world['a'], 'server'), (world['api'], 'service'), (world['prod'], 'environment')]:
            [row] = audit(conn, item['id'])
            assert (row['actor_id'], row['action'], row['resource_type']) == (world['actor'], f'{kind}.created', kind)
        [row] = audit(conn, deployment['id'])
        assert (row['action'], row['details']['revision'], row['details']['agent_id']) == (
            'deployment.created',
            1,
            None,
        )


def test_update_checks_the_revision_and_merges_changes(store, world):
    p, author = world['project'], world['author']
    with store.connect() as conn:
        server = inventory.update_server(conn, p, world['a']['id'], 1, {'description': 'front', 'tags': ['eu']}, author)
        assert (server['name'], server['description'], server['tags'], server['revision']) == ('a', 'front', ['eu'], 2)
        service = inventory.update_service(conn, p, world['api']['id'], 1, {'owner': 'team'}, author)
        assert (service['owner'], service['service_role'], service['revision']) == ('team', 'application', 2)
        environment = inventory.update_environment(conn, p, world['prod']['id'], 1, {'name': 'production'}, author)
        assert (environment['name'], environment['revision']) == ('production', 2)
        deployment = inventory.update_deployment(conn, p, world['api_a']['id'], 1, {'deployed_version': '1.2'}, author)
        assert (deployment['deployed_version'], deployment['port'], deployment['revision']) == ('1.2', 8080, 2)
        assert audit(conn, world['a']['id'])[-1]['action'] == 'server.updated'
    for update, item in [
        (inventory.update_server, world['a']),
        (inventory.update_service, world['api']),
        (inventory.update_environment, world['prod']),
        (inventory.update_deployment, world['api_a']),
    ]:
        with pytest.raises(inventory.Conflict), store.connect() as conn:
            update(conn, p, item['id'], 1, {'description': 'stale'} if 'deployed_version' not in item else {}, author)
    with pytest.raises(Invalid), store.connect() as conn:
        inventory.update_service(conn, p, world['api']['id'], 2, {'service_role': 'nope'}, author)


def test_names_are_unique_and_ids_stay_in_their_project(store, world):
    p, author = world['project'], world['author']
    for create, values in [
        (inventory.create_server, {'name': 'a', 'hostname': 'other'}),
        (inventory.create_service, {'name': 'API'}),
        (inventory.create_environment, {'name': 'Prod'}),
    ]:
        with pytest.raises(inventory.Conflict), store.connect() as conn:
            create(conn, p, values, author)
    with store.connect() as conn:
        other = store.project(conn, 'other-shop')
        with pytest.raises(inventory.NotFound):
            inventory.update_server(conn, other, world['a']['id'], 1, {'description': 'x'}, author)
        with pytest.raises(inventory.NotFound):
            inventory.delete_service(conn, other, world['api']['id'], 1, author)
        assert inventory.list_servers(conn, other) == []
        mine = inventory.create_environment(conn, other, {'name': 'prod'}, author)
        with pytest.raises(Invalid, match='service'):
            inventory.create_deployment(
                conn,
                other,
                {'service_id': world['api']['id'], 'server_id': world['a']['id'], 'environment_id': mine['id']},
                author,
            )


def test_a_duplicate_deployment_is_a_conflict(store, world):
    with pytest.raises(inventory.Conflict), store.connect() as conn:
        inventory.create_deployment(
            conn,
            world['project'],
            {'service_id': world['api']['id'], 'server_id': world['a']['id'], 'environment_id': world['prod']['id']},
            world['author'],
        )


def test_lists_filter_by_environment_server_and_service(store, world):
    p, author = world['project'], world['author']
    with store.connect() as conn:
        inventory.create_server(conn, p, {'name': 'c', 'hostname': 'c.internal'}, author)
        test = inventory.create_environment(conn, p, {'name': 'test'}, author)
        assert [s['name'] for s in inventory.list_servers(conn, p)] == ['a', 'b', 'c']
        assert [s['name'] for s in inventory.list_servers(conn, p, environment_id=world['prod']['id'])] == ['a', 'b']
        assert inventory.list_servers(conn, p, environment_id=test['id']) == []
        assert [s['name'] for s in inventory.list_services(conn, p)] == ['api', 'db']
        assert [e['name'] for e in inventory.list_environments(conn, p)] == ['prod', 'test']
        assert [d['id'] for d in inventory.list_deployments(conn, p, server_id=world['a']['id'])] == [
            world['api_a']['id']
        ]
        assert [d['id'] for d in inventory.list_deployments(conn, p, service_id=world['db']['id'])] == [
            world['db_b']['id']
        ]
        assert len(inventory.list_deployments(conn, p, environment_id=world['prod']['id'])) == 2


def test_connections_default_the_port_and_list_both_directions(store, world):
    p, author = world['project'], world['author']
    agent = {**author, 'agent_id': 'ops-agent', 'session_id': uuid4()}
    with store.connect() as conn:
        to_db = inventory.create_connection(
            conn,
            p,
            {
                'from_deployment_id': world['api_a']['id'],
                'to_deployment_id': world['db_b']['id'],
                'protocol': 'postgres',
            },
            agent,
        )
        assert (to_db['port'], to_db['protocol'], to_db['revision']) == (5432, 'postgres', 1)
        assert (to_db['created_by'], to_db['created_by_agent'], to_db['session_id']) == (
            world['actor'],
            'ops-agent',
            agent['session_id'],
        )
        external = inventory.create_connection(
            conn,
            p,
            {'from_deployment_id': world['api_a']['id'], 'to_host': 'smtp.example.com', 'port': 587},
            author,
        )
        assert (external['to_host'], external['port'], external['protocol']) == ('smtp.example.com', 587, 'tcp')
        [row] = audit(conn, to_db['id'])
        assert (row['action'], row['details']['agent_id'], row['details']['session_id']) == (
            'connection.created',
            'ops-agent',
            str(agent['session_id']),
        )
        assert {c['id'] for c in inventory.list_connections(conn, p, deployment_id=world['db_b']['id'])} == {
            to_db['id']
        }
        assert {c['id'] for c in inventory.list_connections(conn, p, service_id=world['api']['id'])} == {
            to_db['id'],
            external['id'],
        }
        updated = inventory.update_connection(conn, p, to_db['id'], 1, {'description': 'orders'}, author)
        assert (updated['description'], updated['port'], updated['revision']) == ('orders', 5432, 2)
    with pytest.raises(inventory.Conflict), store.connect() as conn:
        inventory.create_connection(
            conn,
            p,
            {
                'from_deployment_id': world['api_a']['id'],
                'to_deployment_id': world['db_b']['id'],
                'protocol': 'postgres',
            },
            author,
        )
    with pytest.raises(inventory.Conflict), store.connect() as conn:
        inventory.update_connection(conn, p, to_db['id'], 1, {'description': 'stale'}, author)
    with pytest.raises(Invalid, match='deployment'), store.connect() as conn:
        inventory.create_connection(
            conn, p, {'from_deployment_id': uuid4(), 'to_deployment_id': world['db_b']['id']}, author
        )


def test_relations(store, world):
    p, author = world['project'], world['author']
    with store.connect() as conn:
        repo = conn.execute(
            """INSERT INTO ka.repositories(project_id,slug,name,remote_url,requested_ref,checkout_path)
            VALUES (%s,'api-code','api-code','https://example.test/api.git','main','r-api') RETURNING id""",
            (p,),
        ).fetchone()['id']
        depends = inventory.create_relation(
            conn,
            p,
            {'from_service_id': world['api']['id'], 'kind': 'depends_on', 'to_service_id': world['db']['id']},
            author,
        )
        built = inventory.create_relation(
            conn, p, {'from_service_id': world['api']['id'], 'kind': 'built_from', 'to_code_project_id': repo}, author
        )
        assert {r['id'] for r in inventory.list_relations(conn, p, service_id=world['db']['id'])} == {depends['id']}
        assert {r['id'] for r in inventory.list_relations(conn, p, service_id=world['api']['id'])} == {
            depends['id'],
            built['id'],
        }
        updated = inventory.update_relation(conn, p, depends['id'], 1, {'kind': 'part_of'}, author)
        assert (updated['kind'], updated['revision']) == ('part_of', 2)
        inventory.delete_relation(conn, p, built['id'], 1, author)
        assert [r['id'] for r in inventory.list_relations(conn, p)] == [depends['id']]
        assert audit(conn, built['id'])[-1]['action'] == 'relation.deleted'
    with pytest.raises(inventory.Conflict), store.connect() as conn:
        inventory.create_relation(
            conn,
            p,
            {'from_service_id': world['api']['id'], 'kind': 'part_of', 'to_service_id': world['db']['id']},
            author,
        )
    with pytest.raises(Invalid, match='code project'), store.connect() as conn:
        inventory.create_relation(
            conn,
            p,
            {'from_service_id': world['api']['id'], 'kind': 'built_from', 'to_code_project_id': uuid4()},
            author,
        )


def test_deletes_are_refused_while_deployments_use_the_item(store, world):
    p, author = world['project'], world['author']
    for delete, item in [
        (inventory.delete_server, world['a']),
        (inventory.delete_service, world['api']),
        (inventory.delete_environment, world['prod']),
    ]:
        with pytest.raises(inventory.Conflict, match='deployment'), store.connect() as conn:
            delete(conn, p, item['id'], item['revision'], author)


def test_deleting_a_deployment_removes_its_connections_then_the_rest_can_go(store, world):
    p, author = world['project'], world['author']
    with store.connect() as conn:
        connection = inventory.create_connection(
            conn, p, {'from_deployment_id': world['api_a']['id'], 'to_deployment_id': world['db_b']['id']}, author
        )
        with pytest.raises(inventory.Conflict):
            inventory.delete_deployment(conn, p, world['api_a']['id'], 2, author)
    with store.connect() as conn:
        inventory.delete_deployment(conn, p, world['db_b']['id'], 1, author)
        assert inventory.list_connections(conn, p) == []
        [*_, row] = audit(conn, world['db_b']['id'])
        assert (row['action'], row['details']['connection_ids']) == ('deployment.deleted', [str(connection['id'])])
        inventory.delete_deployment(conn, p, world['api_a']['id'], 1, author)
        inventory.delete_server(conn, p, world['a']['id'], 1, author)
        inventory.delete_service(conn, p, world['api']['id'], 1, author)
        inventory.delete_environment(conn, p, world['prod']['id'], 1, author)
        assert [s['name'] for s in inventory.list_servers(conn, p)] == ['b']
        assert [s['name'] for s in inventory.list_services(conn, p)] == ['db']
        assert inventory.list_environments(conn, p) == []
        assert [r['action'] for r in audit(conn, world['a']['id'])] == ['server.created', 'server.deleted']
    with pytest.raises(inventory.NotFound), store.connect() as conn:
        inventory.delete_server(conn, p, world['a']['id'], 1, author)
