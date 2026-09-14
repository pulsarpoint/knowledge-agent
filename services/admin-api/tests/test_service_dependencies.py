"""Service dependency integrity and migrations against disposable PostgreSQL."""

from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from test_api import _client
from test_repositories import HEADERS


MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


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
            conn.execute('''INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id)
                VALUES (%s,%s,%s)''', (project, source, target))
        assert {r['depends_on_service_id'] for r in conn.execute(
            'SELECT depends_on_service_id FROM ka.service_dependencies WHERE service_id=%s', (api,))} == {postgres, worker}
        assert {r['service_id'] for r in conn.execute(
            'SELECT service_id FROM ka.service_dependencies WHERE depends_on_service_id=%s', (postgres,))} == {api, worker}
        assert conn.execute('SELECT count(*) AS n FROM ka.service_dependencies WHERE service_id=%s', (postgres,)).fetchone()['n'] == 0
        # An explicit reverse relationship is independent; real inventories can contain cycles.
        original = conn.execute('''INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id,updated_at)
            VALUES (%s,%s,%s,'2000-01-01') RETURNING *''', (project, postgres, api)).fetchone()
        updated = conn.execute('''UPDATE ka.service_dependencies SET description='Uses the API for callbacks'
            WHERE service_id=%s AND depends_on_service_id=%s RETURNING *''', (postgres, api)).fetchone()
        assert updated['description'] == 'Uses the API for callbacks'
        assert updated['updated_at'] > original['updated_at']
        assert updated['created_at'] == original['created_at']

    saved = client.put(f'/api/services/{api}', headers=HEADERS, json={'name': 'Orders', 'revision': 1})
    assert saved.status_code == 200, saved.text
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.service_dependencies').fetchone()['n'] == 4
    deleted = client.delete(f'/api/services/{postgres}', headers=HEADERS, params={'revision': 1, 'confirmed': True})
    assert deleted.status_code == 200, deleted.text
    with store.connect() as conn:
        assert conn.execute('SELECT service_id,depends_on_service_id FROM ka.service_dependencies').fetchall() == [
            {'service_id': api, 'depends_on_service_id': worker}]
        assert {r['id'] for r in conn.execute('SELECT id FROM ka.services')} == {api, worker}
        # Removing one dependency alone must also preserve both services.
        conn.execute('DELETE FROM ka.service_dependencies WHERE service_id=%s', (api,))
        assert conn.execute('SELECT count(*) AS n FROM ka.services').fetchone()['n'] == 2


def test_dependencies_reject_invalid_links_and_description(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        other_project = store.project(conn, 'other')
        first = conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'API') RETURNING id", (project,)).fetchone()['id']
        second = conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'Database') RETURNING id", (project,)).fetchone()['id']
        foreign = conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'Other API') RETURNING id", (other_project,)).fetchone()['id']
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)', (project, first, second))
        cases = [
            (project, first, second, psycopg.errors.UniqueViolation),
            (project, first, first, psycopg.errors.CheckViolation),
            (project, first, foreign, psycopg.errors.ForeignKeyViolation),
            (project, foreign, first, psycopg.errors.ForeignKeyViolation),
            (other_project, second, first, psycopg.errors.ForeignKeyViolation),
            (project, first, uuid4(), psycopg.errors.ForeignKeyViolation),
            (project, uuid4(), first, psycopg.errors.ForeignKeyViolation),
        ]
        for workspace, source, target, error in cases:
            with pytest.raises(error), conn.transaction():
                conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)',
                             (workspace, source, target))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute('UPDATE ka.service_dependencies SET description=%s', ('x' * 4001,))
        assert conn.execute('SELECT count(*) AS n FROM ka.service_dependencies').fetchone()['n'] == 1


def test_dependency_migration_preserves_services_and_refuses_populated_rollback(store):
    up = (MIGRATIONS / '0042_service_dependencies.up.sql').read_text()
    down = (MIGRATIONS / '0042_service_dependencies.down.sql').read_text()
    with store.connect() as conn:
        conn.execute((MIGRATIONS / '0043_service_dependency_context.down.sql').read_text(), prepare=False)
        conn.execute(down, prepare=False)
        project = store.project(conn, 'shop')
        first = conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'API') RETURNING id", (project,)).fetchone()['id']
        second = conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'Database') RETURNING id", (project,)).fetchone()['id']
        original = conn.execute('SELECT * FROM ka.services ORDER BY id').fetchall()
        conn.commit()
        conn.execute(up, prepare=False)
        assert conn.execute('SELECT * FROM ka.services ORDER BY id').fetchall() == original
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id) VALUES (%s,%s,%s)',
                     (project, first, second))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='Service dependencies exist'):
            conn.execute(down, prepare=False)
        conn.rollback()
        assert conn.execute('SELECT count(*) AS n FROM ka.service_dependencies').fetchone()['n'] == 1
        conn.execute('DELETE FROM ka.service_dependencies')
        conn.commit()
        conn.execute(down, prepare=False)
        assert conn.execute("SELECT to_regclass('ka.service_dependencies') AS table_name").fetchone()['table_name'] is None
        conn.execute(up, prepare=False)
        assert conn.execute('SELECT count(*) AS n FROM ka.service_dependencies').fetchone()['n'] == 0
        assert conn.execute('SELECT * FROM ka.services ORDER BY id').fetchall() == original


def test_dependency_projection_migration_preserves_existing_links(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        first = conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'API') RETURNING id", (project,)).fetchone()['id']
        second = conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'Database') RETURNING id", (project,)).fetchone()['id']
        conn.execute('INSERT INTO ka.service_dependencies(project_id,service_id,depends_on_service_id,description) VALUES (%s,%s,%s,%s)',
                     (project,first,second,'Stores orders'))
        original = conn.execute('SELECT * FROM ka.service_dependencies').fetchall()
        conn.commit()
        conn.execute((MIGRATIONS / '0043_service_dependency_context.down.sql').read_text(), prepare=False)
        assert conn.execute('SELECT * FROM ka.service_dependencies').fetchall() == original
        assert 'dependencies' not in conn.execute('SELECT * FROM ka.service_assets WHERE id=%s', (first,)).fetchone()
        conn.execute((MIGRATIONS / '0043_service_dependency_context.up.sql').read_text(), prepare=False)
        assert conn.execute('SELECT dependencies FROM ka.service_assets WHERE id=%s', (first,)).fetchone()['dependencies'] == [
            {'service_id': str(second), 'name': 'Database', 'description': 'Stores orders'}]
        assert conn.execute('SELECT dependents FROM ka.service_assets WHERE id=%s', (second,)).fetchone()['dependents'] == [
            {'service_id': str(first), 'name': 'API', 'description': 'Stores orders'}]
        assert conn.execute('SELECT * FROM ka.service_dependencies').fetchall() == original
