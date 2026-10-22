"""0128: a status (draft or confirmed) on every inventory table, existing rows confirmed; down deletes the drafts and
drops the columns."""

from pathlib import Path

import psycopg
import pytest

from knowledge_admin.inventory import store as inventory

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0128_inventory_drafts.up.sql', '0128_inventory_drafts.down.sql'
TABLES = (
    'environments',
    'servers',
    'services',
    'service_deployments',
    'connections',
    'service_relations',
    'environment_servers',
)


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def status_column(conn, table: str) -> dict | None:
    return conn.execute(
        """SELECT column_default,is_nullable FROM information_schema.columns
        WHERE table_schema='ka' AND table_name=%s AND column_name='status'""",
        (table,),
    ).fetchone()


def count(conn, table: str) -> int:
    return conn.execute(f'SELECT count(*) AS n FROM ka.{table}').fetchone()['n']


@pytest.fixture
def world(store):
    """Confirmed: prod, web, api, api on web in prod, a connection to smtp. Drafts: test, db, pg, pg on db in test (its
    membership too), a connection api → pg and a relation api depends_on pg."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        author = {'actor_id': None}
        w = {'project': project}
        w['prod'] = inventory.create_environment(conn, project, {'name': 'prod'}, author)['id']
        w['web'] = inventory.create_server(conn, project, {'name': 'web', 'hostname': 'web.internal'}, author)['id']
        w['api'] = inventory.create_service(conn, project, {'name': 'api'}, author)['id']
        w['api_web'] = inventory.create_deployment(
            conn, project, {'service_id': w['api'], 'server_id': w['web'], 'environment_id': w['prod']}, author
        )['id']
        inventory.create_connection(
            conn, project, {'from_deployment_id': w['api_web'], 'to_host': 'smtp.example.com', 'port': 25}, author
        )
        w['test'] = inventory.create_environment(conn, project, {'name': 'test'}, author, status='draft')['id']
        w['db'] = inventory.create_server(
            conn, project, {'name': 'db', 'hostname': 'db.internal'}, author, status='draft'
        )['id']
        w['pg'] = inventory.create_service(conn, project, {'name': 'pg'}, author, status='draft')['id']
        w['pg_db'] = inventory.create_deployment(
            conn,
            project,
            {'service_id': w['pg'], 'server_id': w['db'], 'environment_id': w['test']},
            author,
            status='draft',
        )['id']
        inventory.create_connection(
            conn, project, {'from_deployment_id': w['api_web'], 'to_deployment_id': w['pg_db']}, author, status='draft'
        )
        inventory.create_relation(
            conn,
            project,
            {'from_service_id': w['api'], 'kind': 'depends_on', 'to_service_id': w['pg']},
            author,
            status='draft',
        )
    return w


def test_every_inventory_table_has_a_status_that_defaults_to_confirmed(store):
    with store.connect() as conn:
        for table in TABLES:
            column = status_column(conn, table)
            assert column is not None, table
            assert column['is_nullable'] == 'NO' and "'confirmed'" in column['column_default'], table
        project = store.project(conn, 'shop')
        environment = conn.execute(
            "INSERT INTO ka.environments(project_id,name) VALUES (%s,'x') RETURNING status", (project,)
        ).fetchone()
        assert environment['status'] == 'confirmed'
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE ka.environments SET status='pending' WHERE project_id=%s", (project,))


def test_down_deletes_the_drafts_and_drops_the_columns_and_up_confirms_what_is_left(store, world):
    with store.connect() as conn:
        run(conn, DOWN)
        for table in TABLES:
            assert status_column(conn, table) is None, table
        assert [r['name'] for r in conn.execute('SELECT name FROM ka.environments')] == ['prod']
        assert [r['name'] for r in conn.execute('SELECT name FROM ka.servers')] == ['web']
        assert [r['name'] for r in conn.execute('SELECT name FROM ka.services')] == ['api']
        assert count(conn, 'service_deployments') == 1 and count(conn, 'connections') == 1
        assert count(conn, 'service_relations') == 0 and count(conn, 'environment_servers') == 1
        run(conn, UP)
        for table in TABLES:
            assert {r['status'] for r in conn.execute(f'SELECT status FROM ka.{table}')} <= {'confirmed'}, table
