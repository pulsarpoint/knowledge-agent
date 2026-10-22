"""0125: deployments always have an environment; inventory links become connections and service relations."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0125_inventory_model.up.sql', '0125_inventory_model.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def service(conn, project, name):
    return conn.execute(
        'INSERT INTO ka.services(project_id,name) VALUES (%s,%s) RETURNING id', (project, name)
    ).fetchone()['id']


def server(conn, project, name):
    return conn.execute(
        'INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,%s,%s,false) RETURNING id',
        (project, name, f'{name}.internal'),
    ).fetchone()['id']


def environment(conn, project, name):
    return conn.execute(
        'INSERT INTO ka.environments(project_id,name) VALUES (%s,%s) RETURNING id', (project, name)
    ).fetchone()['id']


def deploy(conn, project, service_id, server_id, label, environment_id=None, port=None):
    return conn.execute(
        """INSERT INTO ka.service_deployments(project_id,service_id,server_id,environment,environment_id,port)
        VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
        (project, service_id, server_id, label, environment_id, port),
    ).fetchone()['id']


def link(conn, project, kind, start, end, description='', attributes=None):
    conn.execute(
        f"""INSERT INTO ka.inventory_links(project_id,kind,from_{start[0]}_id,to_{end[0]}_id,description,attributes)
        VALUES (%s,%s,%s,%s,%s,%s)""",
        (project, kind, start[1], end[1], description, Jsonb(attributes or {})),
    )


def repository(conn, project):
    return conn.execute(
        """INSERT INTO ka.repositories(project_id,slug,name,remote_url,requested_ref,checkout_path)
        VALUES (%s,%s,'api-code','https://example.test/api.git','main',%s) RETURNING id""",
        (project, f'api-code-{uuid4().hex[:6]}', f'r-{uuid4().hex[:8]}'),
    ).fetchone()['id']


def relations(conn):
    return {
        (r['from_service_id'], r['to_service_id'] or r['to_code_project_id'], r['kind'], r['description'])
        for r in conn.execute('SELECT * FROM ka.service_relations')
    }


def connections(conn):
    return {
        (c['from_deployment_id'], c['to_deployment_id'], c['port'], c['protocol'], c['description'])
        for c in conn.execute('SELECT * FROM ka.connections')
    }


def test_free_text_environments_become_environments(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        run(conn, DOWN)
        api, web = service(conn, project, 'api'), service(conn, project, 'web')
        host = server(conn, project, 'prod-1')
        production = environment(conn, project, 'Production')
        kept = deploy(conn, project, api, host, 'old label', production)
        by_name = deploy(conn, project, web, host, ' production ')
        created = deploy(conn, project, api, host, 'staging')
        created_too = deploy(conn, project, web, host, 'Staging')
        run(conn, UP)
        rows = {
            r['id']: r['environment_id'] for r in conn.execute('SELECT id,environment_id FROM ka.service_deployments')
        }
        staging = conn.execute("SELECT id FROM ka.environments WHERE lower(name)='staging'").fetchall()
        assert len(staging) == 1
        assert rows == {kept: production, by_name: production, created: staging[0]['id'], created_too: staging[0]['id']}
    with pytest.raises(psycopg.errors.NotNullViolation), store.connect() as conn:
        deploy(conn, project, api, host, 'nowhere')
    with pytest.raises(psycopg.errors.ForeignKeyViolation), store.connect() as conn:
        conn.execute('DELETE FROM ka.environments WHERE id=%s', (production,))


def test_links_become_relations_and_connections(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        run(conn, DOWN)
        api, db, cache, lonely = (service(conn, project, n) for n in ('api', 'db', 'cache', 'lonely'))
        repo = repository(conn, project)
        a, b = server(conn, project, 'a'), server(conn, project, 'b')
        prod, test = environment(conn, project, 'prod'), environment(conn, project, 'test')
        api_prod = deploy(conn, project, api, a, 'prod', prod)
        api_test = deploy(conn, project, api, a, 'test', test)
        db_prod = deploy(conn, project, db, b, 'prod', prod, port=5432)
        db_test = deploy(conn, project, db, b, 'test', test, port=5433)
        deploy(conn, project, cache, b, 'test', test)
        link(conn, project, 'depends_on', ('service', api), ('service', cache), 'reads cache')
        link(conn, project, 'built_from', ('service', api), ('code_project', repo))
        link(conn, project, 'connects_to', ('service', api), ('service', db), 'orders', {'protocol': 'Postgres'})
        link(conn, project, 'connects_to', ('service', db), ('service', api), 'callback', {'port': '8080'})
        link(conn, project, 'connects_to', ('service', api), ('service', lonely), 'undeployed')
        link(conn, project, 'connects_to', ('service', cache), ('service', api), 'cache calls api')
        link(conn, project, 'connects_to', ('service', lonely), ('service', cache), 'lonely to cache')
        link(conn, project, 'connects_to', ('service', api), ('server', b), 'dropped')
        link(conn, project, 'connects_to', ('environment', prod), ('service', db), 'dropped')
        run(conn, UP)
        cache_test = conn.execute('SELECT id FROM ka.service_deployments WHERE service_id=%s', (cache,)).fetchone()[
            'id'
        ]
        assert relations(conn) == {
            (api, cache, 'depends_on', 'reads cache'),
            (api, repo, 'built_from', ''),
            (api, lonely, 'depends_on', 'undeployed'),
            (lonely, cache, 'depends_on', 'lonely to cache'),
        }
        assert connections(conn) == {
            (api_prod, db_prod, 5432, 'postgres', 'orders'),
            (api_test, db_test, 5433, 'postgres', 'orders'),
            (db_prod, api_prod, 8080, 'tcp', 'callback'),
            (db_test, api_test, 8080, 'tcp', 'callback'),
            (cache_test, api_test, None, 'tcp', 'cache calls api'),
        }


def test_deployments_in_no_shared_environment_keep_a_depends_on(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        run(conn, DOWN)
        api, db = service(conn, project, 'api'), service(conn, project, 'db')
        host = server(conn, project, 'a')
        deploy(conn, project, api, host, 'prod', environment(conn, project, 'prod'))
        deploy(conn, project, db, host, 'test', environment(conn, project, 'test'))
        link(conn, project, 'connects_to', ('service', api), ('service', db), 'apart')
        run(conn, UP)
        assert relations(conn) == {(api, db, 'depends_on', 'apart')}
        assert connections(conn) == set()


def test_down_drops_the_new_tables_and_allows_unlinked_deployments_again(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        run(conn, DOWN)
        tables = conn.execute(
            """SELECT count(*) AS n FROM information_schema.tables
            WHERE table_schema='ka' AND table_name IN ('connections','service_relations')"""
        ).fetchone()['n']
        assert tables == 0
        api, host = service(conn, project, 'api'), server(conn, project, 'a')
        deploy(conn, project, api, host, 'nowhere')
        run(conn, UP)
        assert conn.execute('SELECT count(*) AS n FROM ka.connections').fetchone()['n'] == 0
