from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import HTTPException
from psycopg import sql

from test_api import _client
from test_operations import configured
from test_repositories import HEADERS


def configuration(client):
    response = client.get('/api/operations/settings', headers=HEADERS)
    assert response.status_code == 200
    return response.json()


def save_override(client, server_id, *, instructions=None, skill_ids=None):
    data = configuration(client)
    current = next((c for c in data['configs'] if c['server_id'] == server_id), None)
    response = client.put(
        f'/api/operations/configs/{server_id}',
        headers=HEADERS,
        json={
            'settings_revision': data['settings']['revision'],
            'revision': current['revision'] if current else None,
            'instructions': instructions,
            'skill_ids': skill_ids,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_optional_configs_inheritance_empty_values_and_cleanup(store, tmp_path):
    client, server, _, agent = configured(store, tmp_path)
    data = configuration(client)
    assert len(data['configs']) == 1 and data['configs'][0]['server_id'] is None
    global_id = data['configs'][0]['id']
    override = save_override(client, server['id'], instructions='', skill_ids=[])
    assert override['instructions'] == '' and override['skill_ids'] == []

    path = f'/api/operations/configs/{server["id"]}'
    body = {'settings_revision': configuration(client)['settings']['revision'], 'revision': override['revision']}
    assert client.request('DELETE', path, headers=HEADERS, json=body).status_code == 400
    assert client.request('DELETE', path, headers=HEADERS, json={**body, 'confirmed': False}).status_code == 400
    assert client.request('DELETE', path, headers=HEADERS, json={**body, 'confirmed': True}).status_code == 200
    with store.connect() as conn:
        assert conn.execute('SELECT 1 FROM ka.agent_configs WHERE id=%s', (UUID(override['id']),)).fetchone() is None
    assert configuration(client)['configs'][0]['id'] == global_id
    assert len(configuration(client)['configs']) == 1
    assert configuration(client)['settings']['instructions'] == 'Verify every change.'
    ssh = client.get(f'/api/servers/{server["id"]}/ssh', headers=HEADERS).json()
    assert ssh['has_private_key'] and ssh['username'] == server['username'] and ssh['allow_sudo'] is False
    assert (
        client.request(
            'DELETE', '/api/operations/configs/global', headers=HEADERS, json={**body, 'confirmed': True}
        ).status_code
        == 400
    )


def test_scope_validation_conflicts_and_server_deletion(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    server = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'No credentials', 'hostname': 'draft.test', 'ssh_allowed': True}
    ).json()
    disabled = client.post(
        '/api/servers', headers=HEADERS, json={'name': 'Disabled', 'hostname': 'disabled.test', 'ssh_allowed': False}
    ).json()
    path = f'/api/operations/configs/{server["id"]}'
    data = configuration(client)
    body = {
        'settings_revision': data['settings']['revision'],
        'revision': None,
        'instructions': None,
        'skill_ids': None,
    }
    assert client.put(path, json=body).status_code == 403
    assert client.put(f'/api/operations/configs/{disabled["id"]}', headers=HEADERS, json=body).status_code == 400
    assert client.put(f'/api/operations/configs/{uuid4()}', headers=HEADERS, json=body).status_code == 404
    assert client.put(path, headers=HEADERS, json={**body, 'skill_ids': [str(uuid4())]}).status_code == 400
    # An SSH-enabled server can have a runbook before its credentials are entered.
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: client.put(path, headers=HEADERS, json=body).status_code, range(2)))
    assert sorted(statuses) == [200, 409]
    saved = next(c for c in configuration(client)['configs'] if c['server_id'] == server['id'])
    assert client.put(path, headers=HEADERS, json={**body, 'settings_revision': 2}).status_code == 409
    stale_delete = {'settings_revision': 1, 'revision': saved['revision'], 'confirmed': True}
    assert client.request('DELETE', path, headers=HEADERS, json=stale_delete).status_code == 409
    assert client.request('DELETE', path, json=stale_delete).status_code == 403
    assert (
        client.delete(
            f'/api/servers/{server["id"]}', headers=HEADERS, params={'revision': server['revision'], 'confirmed': True}
        ).status_code
        == 200
    )
    assert len(configuration(client)['configs']) == 1


def test_schema_enforces_unique_global_and_scoped_identity(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    configuration(client)
    with store.connect() as conn:
        project = conn.execute('SELECT project_id FROM ka.agent_configs').fetchone()['project_id']
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(
                "INSERT INTO ka.agent_configs(project_id,agent,instructions,inherit_skills) VALUES (%s,'operations','',false)",
                (project,),
            )
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other','Other') RETURNING id").fetchone()[
            'id'
        ]
        server = conn.execute(
            "INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'Elsewhere','other.test',false) RETURNING id",
            (other,),
        ).fetchone()['id']
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute(
                "INSERT INTO ka.agent_configs(project_id,agent,server_id) VALUES (%s,'operations',%s)",
                (project, server),
            )


def test_migration_preserves_existing_defaults_and_server_runbooks(postgres):
    database = f'migration_{uuid4().hex}'
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}') as conn:
            for migration in sorted(migrations.glob('*.up.sql')):
                if migration.name < '0017':
                    conn.execute(migration.read_text(), prepare=False)
            project = conn.execute(
                "INSERT INTO ka.projects(slug,name) VALUES ('existing','Existing') RETURNING id"
            ).fetchone()[0]
            actor = conn.execute(
                "INSERT INTO ka.principals(issuer,subject,email) VALUES ('test','admin','admin@test') RETURNING id"
            ).fetchone()[0]
            server = conn.execute(
                "INSERT INTO ka.servers(project_id,name,hostname,operation_instructions,enabled) VALUES (%s,'Existing host','host.test','Existing runbook',false) RETURNING id",
                (project,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ka.operations_settings(project_id,instructions,revision) VALUES (%s,'Existing defaults',7)",
                (project,),
            )
            skill = conn.execute(
                "INSERT INTO ka.agent_skills(project_id,name,description,instructions,created_by,updated_by) VALUES (%s,'checks','Checks','Inspect first',%s,%s) RETURNING id",
                (project, actor, actor),
            ).fetchone()[0]
            conn.execute('INSERT INTO ka.operations_skills VALUES (%s,%s)', (project, skill))
            conn.execute((migrations / '0017_agent_server_configs.up.sql').read_text(), prepare=False)
            rows = conn.execute(
                'SELECT server_id,instructions,inherit_skills FROM ka.agent_configs ORDER BY server_id NULLS FIRST'
            ).fetchall()
            assert rows == [(None, 'Existing defaults', False), (server, 'Existing runbook', True)]
            assert conn.execute('SELECT skill_id FROM ka.agent_config_skills').fetchall() == [(skill,)]
            assert conn.execute('SELECT revision FROM ka.operations_settings').fetchone()[0] == 7
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


def test_skills_agents_migration_preserves_shared_assignments_and_server_overrides(postgres):
    database = f'migration_{uuid4().hex}'
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    try:
        with psycopg.connect(f'{postgres} dbname={database}') as conn:
            for migration in sorted(migrations.glob('*.up.sql')):
                if migration.name < '0023':
                    conn.execute(migration.read_text(encoding='utf-8'), prepare=False)
            project = conn.execute(
                "INSERT INTO ka.projects(slug,name) VALUES ('existing','Existing') RETURNING id"
            ).fetchone()[0]
            actor = conn.execute(
                "INSERT INTO ka.principals(issuer,subject,email) VALUES ('test','admin','admin@test') RETURNING id"
            ).fetchone()[0]
            skill = conn.execute(
                "INSERT INTO ka.agent_skills(project_id,name,description,instructions,created_by,updated_by) VALUES (%s,'checks','Checks','Inspect first',%s,%s) RETURNING id",
                (project, actor, actor),
            ).fetchone()[0]
            server = conn.execute(
                "INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'Existing host','host.test',false) RETURNING id",
                (project,),
            ).fetchone()[0]
            for agent in ('code', 'operations'):
                config = conn.execute(
                    "INSERT INTO ka.agent_configs(project_id,agent,instructions,inherit_skills) VALUES (%s,%s,'Agent instructions',false) RETURNING id",
                    (project, agent),
                ).fetchone()[0]
                conn.execute('INSERT INTO ka.agent_config_skills VALUES (%s,%s,%s)', (config, project, skill))
            override = conn.execute(
                "INSERT INTO ka.agent_configs(project_id,agent,server_id,instructions,inherit_skills) VALUES (%s,'operations',%s,'Server instructions',false) RETURNING id",
                (project, server),
            ).fetchone()[0]
            conn.execute('INSERT INTO ka.agent_config_skills VALUES (%s,%s,%s)', (override, project, skill))
            before = conn.execute('SELECT * FROM ka.agent_configs ORDER BY id').fetchall()
            conn.execute((migrations / '0023_skills_agents.up.sql').read_text(encoding='utf-8'), prepare=False)
            assert conn.execute(
                'SELECT agent_id,skill_id,project_id FROM ka.skills_agents ORDER BY agent_id'
            ).fetchall() == [('code', skill, project), ('operations', skill, project)]
            assert conn.execute('SELECT * FROM ka.agent_config_skills').fetchall() == [(override, project, skill)]
            assert conn.execute('SELECT * FROM ka.agent_configs ORDER BY id').fetchall() == before
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
