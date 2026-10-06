from uuid import uuid4

import pytest

from test_api import _client
from test_profiles import create
from test_repositories import HEADERS


def test_unconfigured_run_does_not_execute_or_create_run_records(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    async def forbidden(*args, **kwargs):
        pytest.fail('Execution must not contact a provider or server')
    monkeypatch.setattr('knowledge_admin.ssh.execute', forbidden)
    monkeypatch.setattr('httpx.AsyncClient.send', forbidden)
    with store.connect() as conn:
        before = conn.execute('SELECT count(*) AS n FROM ka.operations_runs').fetchone()['n']
    response = client.post('/api/agents/operations/runs', headers=HEADERS, json={'task': 'Inspect the server'})
    assert response.status_code == 409
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.operations_runs').fetchone()['n'] == before
    assert client.post('/api/agents/operations/runs', json={'task': 'Inspect'}).status_code == 403
    assert client.post('/api/agents/operations/runs', headers=HEADERS, json={'task': ' '}).status_code == 400
    assert client.post('/api/agents/missing/runs', headers=HEADERS, json={'task': 'Inspect'}).status_code == 404


def test_historical_run_can_be_cancelled_and_history_stays_readable(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    run_id = uuid4()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin', 'admin@test')
        conn.execute("INSERT INTO ka.operations_runs(id,project_id,actor_id,task,context) VALUES (%s,%s,%s,'Old task','{}')", (run_id, project, actor))
    response = client.post(f'/api/agents/operations/runs/{run_id}/cancel', headers=HEADERS)
    assert response.status_code == 200 and response.json()['id'] == str(run_id)
    assert client.get(f'/api/agents/operations/runs/{run_id}', headers=HEADERS).json()['status'] == 'cancelled'
    assert client.get('/api/agents/operations/runs', headers=HEADERS).json()['runs'][0]['id'] == str(run_id)
    response = client.request('DELETE', '/api/agents/operations', headers=HEADERS, json={'revision': 1, 'confirmed': True})
    assert response.status_code == 409 and 'history' in response.text


def test_agent_delete_preserves_prompt_invariants_and_other_agents(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    created = create(client, 'Disposable')
    agent_id = created['id']
    path = f'/api/agents/{agent_id}'
    assert client.request('DELETE', path, headers=HEADERS, json={'revision': 1, 'confirmed': False}).status_code == 400
    assert client.request('DELETE', path, headers=HEADERS, json={'revision': 2, 'confirmed': True}).status_code == 409
    response = client.request('DELETE', path, headers=HEADERS, json={'revision': 1, 'confirmed': True})
    assert response.status_code == 204, response.text
    assert client.get(path, headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert conn.execute('SELECT 1 FROM ka.agent_prompts WHERE agent=%s', (agent_id,)).fetchone() is None
        assert conn.execute('SELECT 1 FROM ka.agent_prompt_versions WHERE agent=%s', (agent_id,)).fetchone() is None
        assert conn.execute('SELECT count(*) AS n FROM ka.agents').fetchone()['n'] == 4
    response = client.request('DELETE', '/api/agents/code', headers=HEADERS, json={'revision': 1, 'confirmed': True})
    assert response.status_code == 204, response.text
    assert client.get('/api/llm', headers=HEADERS).status_code == 200


def test_delete_cleans_owned_settings_and_keeps_shared_ssh_key(store, tmp_path):
    from test_operations import setup
    from test_agent_ssh import save
    client = _client(tmp_path, repository_store=store)
    server, _ = setup(client)
    created = create(client, 'Disposable')
    agent_id = created['id']
    settings = client.get(f'/api/operations/settings?agent_id={agent_id}', headers=HEADERS).json()['settings']
    response = client.put(f'/api/operations/settings?agent_id={agent_id}', headers=HEADERS,
        json={'revision': settings['revision'], 'server_ids': [server['id']], 'skill_ids': [], 'instructions': 'Inspect.'})
    assert response.status_code == 200, response.text
    save(client, f"/api/agents/{agent_id}/servers/{server['id']}/ssh", username='specialist')
    with store.connect() as conn:
        before = conn.execute('SELECT id,ciphertext FROM ka.credentials ORDER BY id').fetchall()
        assert conn.execute('SELECT credential_id FROM ka.agent_server_connections WHERE agent_id=%s', (agent_id,)).fetchone()['credential_id'] == before[0]['id']
    current = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    response = client.request('DELETE', f'/api/agents/{agent_id}', headers=HEADERS, json={'revision': current['revision'], 'confirmed': True})
    assert response.status_code == 204, response.text
    with store.connect() as conn:
        assert conn.execute('SELECT id,ciphertext FROM ka.credentials ORDER BY id').fetchall() == before
        for table, column in [('agent_configs', 'agent'), ('operations_settings', 'agent'), ('operations_servers', 'agent'), ('agent_server_connections', 'agent_id')]:
            assert conn.execute(f'SELECT 1 FROM ka.{table} WHERE {column}=%s', (agent_id,)).fetchone() is None
    assert client.get(f"/api/servers/{server['id']}/ssh", headers=HEADERS).json()['has_private_key']


def test_delete_all_resource_joins_preserves_shared_catalogs(store, tmp_path):
    import asyncssh
    from test_operations import setup
    from test_agent_ssh import save
    from test_profiles import update
    client = _client(tmp_path, repository_store=store)
    server, _ = setup(client)
    agent = create(client, 'Disposable')
    agent_id = agent['id']
    repository = client.post('/api/repositories', headers=HEADERS, json={'remote_url': 'example/repo'}).json()
    service = client.post('/api/services', headers=HEADERS, json={'name': 'Shared service'}).json()
    skill = client.post('/api/skills', headers=HEADERS, json={'name': 'shared-skill', 'description': 'Shared procedures', 'instructions': 'Inspect.'}).json()
    model = client.post('/api/llm/models', headers=HEADERS, json={'name': 'Shared model', 'provider': 'openai', 'model': 'test', 'api_format': 'responses', 'base_url': 'http://model.test/v1'}).json()
    mcp = client.post('/api/mcp/servers', headers=HEADERS, json={'name': 'Shared MCP', 'url': 'http://mcp.test/mcp'}).json()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}') ON CONFLICT DO NOTHING", (project,))
        collection = conn.execute("INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,'shared') RETURNING id", (project,)).fetchone()['id']
        conn.execute("UPDATE ka.mcp_servers SET tools='[{\"name\":\"lookup\"}]',last_tested_at=now() WHERE id=%s", (mcp['id'],))
    agent = update(client, agent, collection_ids=[str(collection)], repository_ids=[repository['id']], service_ids=[service['id']], skill_ids=[skill['id']]).json()
    response = client.put(f'/api/agents/{agent_id}/model', headers=HEADERS, json={'revision': agent['revision'], 'model_id': model['id']})
    assert response.status_code == 200, response.text
    settings = client.get(f'/api/operations/settings?agent_id={agent_id}', headers=HEADERS).json()['settings']
    response = client.put(f'/api/operations/settings?agent_id={agent_id}', headers=HEADERS, json={'revision': settings['revision'], 'server_ids': [server['id']], 'skill_ids': [skill['id']], 'mcp_server_ids': [mcp['id']], 'instructions': 'Inspect.'})
    assert response.status_code == 200, response.text
    key = asyncssh.generate_private_key('ssh-ed25519').export_private_key().decode()
    save(client, f"/api/agents/{agent_id}/servers/{server['id']}/ssh", private_key=key)
    with store.connect() as conn:
        owned_credential = conn.execute('SELECT credential_id FROM ka.agent_server_connections WHERE agent_id=%s', (agent_id,)).fetchone()['credential_id']
        config_id = conn.execute('SELECT id FROM ka.agent_configs WHERE agent=%s AND server_id IS NULL', (agent_id,)).fetchone()['id']
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_config_mcp_servers WHERE config_id=%s', (config_id,)).fetchone()['n'] == 1
    agent = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    assert client.request('DELETE', f'/api/agents/{agent_id}', headers=HEADERS, json={'revision': agent['revision'], 'confirmed': True}).status_code == 204
    with store.connect() as conn:
        for table in ['agent_collections', 'agent_repositories', 'agent_services', 'skills_agents', 'agent_server_connections']:
            assert conn.execute(f'SELECT 1 FROM ka.{table} WHERE agent_id=%s', (agent_id,)).fetchone() is None
        assert conn.execute('SELECT 1 FROM ka.credentials WHERE id=%s', (owned_credential,)).fetchone() is None
        assert conn.execute('SELECT 1 FROM ka.agent_config_mcp_servers WHERE config_id=%s', (config_id,)).fetchone() is None
        for table, resource_id in [('rag_collections', collection), ('repositories', repository['id']), ('services', service['id']), ('agent_skills', skill['id']), ('llm_models', model['id']), ('mcp_servers', mcp['id']), ('servers', server['id'])]:
            assert conn.execute(f'SELECT 1 FROM ka.{table} WHERE id=%s', (resource_id,)).fetchone()
        assert conn.execute('SELECT count(*) AS n FROM ka.agents').fetchone()['n'] == 4
        assert conn.execute("SELECT 1 FROM ka.audit_events WHERE action='agent.deleted' AND details->>'agent_id'=%s", (agent_id,)).fetchone()
