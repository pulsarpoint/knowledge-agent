"""Service dependency integrity against disposable PostgreSQL: dependencies are depends_on inventory links."""

from uuid import UUID, uuid4

import psycopg
import pytest

from test_api import _client
from test_repositories import HEADERS


def test_dependency_direction_metadata_and_service_lifecycle(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    services = {}
    for name in ('Orders API', 'PostgreSQL', 'Worker'):
        response = client.post('/api/services', headers=HEADERS, json={'name': name})
        assert response.status_code == 201, response.text
        services[name] = UUID(response.json()['id'])
    api, postgres, worker = (services[name] for name in ('Orders API', 'PostgreSQL', 'Worker'))
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for source, target in ((api, postgres), (api, worker), (worker, postgres)):
            conn.execute(
                """INSERT INTO ka.inventory_links(project_id,from_service_id,to_service_id,kind)
                VALUES (%s,%s,%s,'depends_on')""",
                (project, source, target),
            )
        assert {
            r['to_service_id']
            for r in conn.execute(
                "SELECT to_service_id FROM ka.inventory_links WHERE kind='depends_on' AND from_service_id=%s", (api,)
            )
        } == {postgres, worker}
        assert {
            r['from_service_id']
            for r in conn.execute(
                "SELECT from_service_id FROM ka.inventory_links WHERE kind='depends_on' AND to_service_id=%s",
                (postgres,),
            )
        } == {api, worker}
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM ka.inventory_links WHERE kind='depends_on' AND from_service_id=%s",
                (postgres,),
            ).fetchone()['n']
            == 0
        )
        # An explicit reverse relationship is independent; real inventories can contain cycles.
        original = conn.execute(
            """INSERT INTO ka.inventory_links(project_id,from_service_id,to_service_id,kind,updated_at)
            VALUES (%s,%s,%s,'depends_on','2000-01-01') RETURNING *""",
            (project, postgres, api),
        ).fetchone()
        updated = conn.execute(
            """UPDATE ka.inventory_links SET description='Uses the API for callbacks'
            WHERE kind='depends_on' AND from_service_id=%s AND to_service_id=%s RETURNING *""",
            (postgres, api),
        ).fetchone()
        assert updated['description'] == 'Uses the API for callbacks'
        assert updated['updated_at'] > original['updated_at']
        assert updated['created_at'] == original['created_at']

    saved = client.put(f'/api/services/{api}', headers=HEADERS, json={'name': 'Orders', 'revision': 1})
    assert saved.status_code == 200, saved.text
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.inventory_links WHERE kind='depends_on'").fetchone()['n'] == 4
    deleted = client.delete(f'/api/services/{postgres}', headers=HEADERS, params={'revision': 1, 'confirmed': True})
    assert deleted.status_code == 200, deleted.text
    with store.connect() as conn:
        assert conn.execute(
            "SELECT from_service_id,to_service_id FROM ka.inventory_links WHERE kind='depends_on'"
        ).fetchall() == [{'from_service_id': api, 'to_service_id': worker}]
        assert {r['id'] for r in conn.execute('SELECT id FROM ka.services')} == {api, worker}
        # Removing one dependency alone must also preserve both services.
        conn.execute("DELETE FROM ka.inventory_links WHERE kind='depends_on' AND from_service_id=%s", (api,))
        assert conn.execute('SELECT count(*) AS n FROM ka.services').fetchone()['n'] == 2


def test_dependencies_reject_links_outside_the_project_and_duplicates(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        other_project = store.project(conn, 'other')
        first = conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'API') RETURNING id", (project,)
        ).fetchone()['id']
        second = conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'Database') RETURNING id", (project,)
        ).fetchone()['id']
        foreign = conn.execute(
            "INSERT INTO ka.services(project_id,name) VALUES (%s,'Other API') RETURNING id", (other_project,)
        ).fetchone()['id']
        conn.execute(
            "INSERT INTO ka.inventory_links(project_id,from_service_id,to_service_id,kind) VALUES (%s,%s,%s,'depends_on')",
            (project, first, second),
        )
        cases = [
            (project, first, second, psycopg.errors.UniqueViolation),
            (project, first, foreign, psycopg.errors.ForeignKeyViolation),
            (project, foreign, first, psycopg.errors.ForeignKeyViolation),
            (other_project, second, first, psycopg.errors.ForeignKeyViolation),
            (project, first, uuid4(), psycopg.errors.ForeignKeyViolation),
            (project, uuid4(), first, psycopg.errors.ForeignKeyViolation),
        ]
        for workspace, source, target, error in cases:
            with pytest.raises(error), conn.transaction():
                conn.execute(
                    "INSERT INTO ka.inventory_links(project_id,from_service_id,to_service_id,kind) VALUES (%s,%s,%s,'depends_on')",
                    (workspace, source, target),
                )
        assert conn.execute('SELECT count(*) AS n FROM ka.inventory_links').fetchone()['n'] == 1
