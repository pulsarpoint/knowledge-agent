"""0127: explicit server membership (backfilled from deployments), services.address and connections to an external
service; down restores 0126's shape."""

from pathlib import Path

import psycopg
import pytest

from knowledge_admin.inventory import store as inventory

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0127_environment_workspace.up.sql', '0127_environment_workspace.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def columns(conn, table: str) -> set[str]:
    return {
        row['column_name']
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name=%s", (table,)
        )
    }


def members(conn) -> set[tuple]:
    return {(r['environment_id'], r['server_id']) for r in conn.execute('SELECT * FROM ka.environment_servers')}


@pytest.fixture
def world(store):
    """prod: api on web twice (two runtimes) and pg on db; test: api on web. A connection api → smtp.example.com."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        author = {'actor_id': None}
        w = {'project': project}
        w['prod'], w['test'] = (
            inventory.create_environment(conn, project, {'name': n}, author)['id'] for n in ('prod', 'test')
        )
        w['web'], w['db'], w['spare'] = (
            inventory.create_server(conn, project, {'name': n, 'hostname': f'{n}.internal'}, author)['id']
            for n in ('web', 'db', 'spare')
        )
        w['api'], w['pg'] = (inventory.create_service(conn, project, {'name': n}, author)['id'] for n in ('api', 'pg'))

        def deploy(service, server, environment, runtime_name=''):
            values = {
                'service_id': service,
                'server_id': server,
                'environment_id': environment,
                'runtime_name': runtime_name,
            }
            return inventory.create_deployment(conn, project, values, author)['id']

        w['api_web'] = deploy(w['api'], w['web'], w['prod'])
        deploy(w['api'], w['web'], w['prod'], 'second')
        deploy(w['pg'], w['db'], w['prod'])
        deploy(w['api'], w['web'], w['test'])
        values = {'from_deployment_id': w['api_web'], 'to_host': 'smtp.example.com', 'port': 587}
        inventory.create_connection(conn, project, values, author)
    return w


def test_down_then_up_backfills_one_membership_per_environment_and_server(store, world):
    w = world
    with store.connect() as conn:
        run(conn, DOWN)
        assert conn.execute("SELECT to_regclass('ka.environment_servers') AS t").fetchone()['t'] is None
        assert 'address' not in columns(conn, 'services') and 'to_service_id' not in columns(conn, 'connections')
        run(conn, UP)
        assert members(conn) == {(w['prod'], w['web']), (w['prod'], w['db']), (w['test'], w['web'])}
        assert 'address' in columns(conn, 'services') and 'to_service_id' in columns(conn, 'connections')


def test_a_connection_has_one_target_and_down_drops_connections_to_a_service(store, world):
    w = world
    with store.connect() as conn:
        external = inventory.create_service(
            conn, w['project'], {'name': 'mail', 'service_type': 'external', 'address': 'mail.example.com'}, {}
        )
        conn.execute(
            """INSERT INTO ka.connections(project_id,from_deployment_id,to_service_id,port)
            VALUES (%s,%s,%s,25)""",
            (w['project'], w['api_web'], external['id']),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match='connections_one_target'), conn.transaction():
            conn.execute(
                """INSERT INTO ka.connections(project_id,from_deployment_id,to_service_id,to_host,port)
                VALUES (%s,%s,%s,'x.example.com',25)""",
                (w['project'], w['api_web'], external['id']),
            )
        with pytest.raises(psycopg.errors.UniqueViolation, match='connections_once'), conn.transaction():
            conn.execute(
                """INSERT INTO ka.connections(project_id,from_deployment_id,to_service_id,port)
                VALUES (%s,%s,%s,25)""",
                (w['project'], w['api_web'], external['id']),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match='services_address'), conn.transaction():
            conn.execute("UPDATE ka.services SET address='http://x' WHERE id=%s", (external['id'],))
        run(conn, DOWN)
        assert [r['to_host'] for r in conn.execute('SELECT to_host FROM ka.connections')] == ['smtp.example.com']
        run(conn, UP)
