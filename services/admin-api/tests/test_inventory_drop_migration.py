"""0126: the old inventory structures are gone (membership tables, inventory links, the *_assets views, the free-text
deployment environment); down recreates them backfilled from deployments, relations and connections."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from knowledge_admin.inventory import store as inventory

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0126_inventory_drop_old.up.sql', '0126_inventory_drop_old.down.sql'
OLD = ('environment_services', 'environment_servers', 'inventory_links', 'inventory_entries', 'service_assets')
OLD += ('server_assets', 'environment_assets')


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def present(conn) -> set[str]:
    return {name for name in OLD if conn.execute('SELECT to_regclass(%s) AS t', (f'ka.{name}',)).fetchone()['t']}


def has_label(conn) -> bool:
    return bool(
        conn.execute(
            """SELECT 1 FROM information_schema.columns
            WHERE table_schema='ka' AND table_name='service_deployments' AND column_name='environment'"""
        ).fetchone()
    )


@pytest.fixture
def world(store):
    """prod: api on web (8080) and pg on db (5432); api depends on pg, is built from shop, has part worker, connects to
    pg twice (postgres and tcp) and to smtp.example.com:587."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        author = {'actor_id': None}
        w = {'project': project}
        w['prod'] = inventory.create_environment(conn, project, {'name': 'prod'}, author)['id']
        w['web'], w['db'] = (
            inventory.create_server(conn, project, {'name': n, 'hostname': f'{n}.internal'}, author)['id']
            for n in ('web', 'db')
        )
        w['api'], w['pg'], w['worker'] = (
            inventory.create_service(conn, project, {'name': n}, author)['id'] for n in ('api', 'pg', 'worker')
        )
        w['shop'] = uuid4()
        conn.execute(
            "INSERT INTO ka.repositories(id,project_id,slug,name) VALUES (%s,%s,'shop','shop')", (w['shop'], project)
        )

        def deploy(service, server, port):
            values = {'service_id': service, 'server_id': server, 'environment_id': w['prod'], 'port': port}
            return inventory.create_deployment(conn, project, values, author)['id']

        w['api_web'], w['pg_db'] = deploy(w['api'], w['web'], 8080), deploy(w['pg'], w['db'], 5432)
        for values in (
            {'from_service_id': w['api'], 'kind': 'depends_on', 'to_service_id': w['pg'], 'description': 'orders'},
            {'from_service_id': w['api'], 'kind': 'built_from', 'to_code_project_id': w['shop']},
            {'from_service_id': w['worker'], 'kind': 'part_of', 'to_service_id': w['api']},
        ):
            inventory.create_relation(conn, project, values, author)
        for values in (
            {'to_deployment_id': w['pg_db'], 'protocol': 'postgres', 'description': 'SQL'},
            {'to_host': 'smtp.example.com', 'port': 587, 'protocol': 'smtp'},
        ):
            inventory.create_connection(conn, project, {'from_deployment_id': w['api_web'], **values}, author)
    with store.connect() as conn:  # a later transaction: created after the first
        later = {'from_deployment_id': w['api_web'], 'to_deployment_id': w['pg_db'], 'description': 'later'}
        inventory.create_connection(conn, project, later, author)
    return w


def test_up_drops_the_old_structures_and_the_deployment_identity_is_checked_at_once(store, world):
    with store.connect() as conn:
        assert present(conn) == {'environment_servers'}  # made again by 0127, as explicit membership
        assert not has_label(conn)
        with pytest.raises(psycopg.errors.UniqueViolation, match='service_deployments_environment_identity'):
            conn.execute(
                """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment_id,port)
                VALUES (%s,%s,%s,%s,9999)""",
                (world['project'], world['api'], world['web'], world['prod']),
            )


def test_down_backfills_the_old_structures_and_up_drops_them_again(store, world):
    w = world
    with store.connect() as conn:
        run(conn, '0127_environment_workspace.down.sql')  # 0127 makes environment_servers, which 0126 down makes again
        run(conn, DOWN)
        assert present(conn) == set(OLD)
        assert {r['environment'] for r in conn.execute('SELECT environment FROM ka.service_deployments')} == {'prod'}
        assert {r['service_id'] for r in conn.execute('SELECT service_id FROM ka.environment_services')} == {
            w['api'],
            w['pg'],
        }
        assert {r['server_id'] for r in conn.execute('SELECT server_id FROM ka.environment_servers')} == {
            w['web'],
            w['db'],
        }
        links = {
            (r['kind'], r['from_id'], r['to_id'], r['description'], tuple(sorted(r['attributes'].items())))
            for r in conn.execute('SELECT * FROM ka.inventory_links')
        }
        assert links == {
            ('depends_on', w['api'], w['pg'], 'orders', ()),
            ('built_from', w['api'], w['shop'], '', ()),
            ('connects_to', w['api'], w['pg'], 'SQL', (('port', '5432'), ('protocol', 'postgres'))),
        }  # the part_of relation, the second connection and the external host are lost
        row = conn.execute('SELECT links,environments FROM ka.service_assets WHERE id=%s', (w['api'],)).fetchone()
        assert [link['name'] for link in row['links']] == ['pg']
        assert row['environments'] == [{'id': str(w['prod']), 'name': 'prod'}]
        run(conn, UP)
        assert present(conn) == set()
        assert not has_label(conn)
