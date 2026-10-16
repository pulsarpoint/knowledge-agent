from test_api import _client
from test_profiles import create
from test_repositories import HEADERS


def test_agent_delete_keeps_other_agents(store, tmp_path):
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
        assert conn.execute('SELECT count(*) AS n FROM ka.agents').fetchone()['n'] == 3
    response = client.request('DELETE', '/api/agents/code', headers=HEADERS, json={'revision': 1, 'confirmed': True})
    assert response.status_code == 204, response.text
    assert client.get('/api/llm', headers=HEADERS).status_code == 200


def test_delete_keeps_every_resource_its_access_referenced(store, tmp_path):
    from test_operations import setup
    from test_profiles import update

    client = _client(tmp_path, repository_store=store)
    server, _ = setup(client)
    agent = create(client, 'Disposable')
    agent_id = agent['id']
    repository = client.post('/api/repositories', headers=HEADERS, json={'remote_url': 'example/repo'}).json()
    service = client.post('/api/services', headers=HEADERS, json={'name': 'Shared service'}).json()
    skill = client.post(
        '/api/skills',
        headers=HEADERS,
        json={'name': 'shared-skill', 'description': 'Shared procedures', 'instructions': 'Inspect.'},
    ).json()
    model = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'Shared model',
            'provider': 'openai',
            'model': 'test',
            'api_format': 'responses',
            'base_url': 'http://model.test/v1',
        },
    ).json()
    mcp = client.post(
        '/api/mcp/servers', headers=HEADERS, json={'name': 'Shared MCP', 'url': 'http://mcp.test/mcp'}
    ).json()
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute(
            "INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}') ON CONFLICT DO NOTHING", (project,)
        )
        collection = conn.execute(
            "INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,'shared') RETURNING id", (project,)
        ).fetchone()['id']
        credentials = conn.execute('SELECT id,ciphertext FROM ka.credentials ORDER BY id').fetchall()
    references = [
        ('server', server['id']),
        ('service', service['id']),
        ('code_project', repository['id']),
        ('collection', str(collection)),
        ('skill', skill['id']),
        ('mcp_server', mcp['id']),
    ]
    access = {'resources': [{'type': t, 'id': i} for t, i in references], 'roles': {'content_editor': False}}
    agent = update(client, agent, access=access, instructions='Inspect.').json()
    assert len(agent['access']['resources']) == len(references)
    response = client.put(
        f'/api/agents/{agent_id}/model', headers=HEADERS, json={'revision': agent['revision'], 'model_id': model['id']}
    )
    assert response.status_code == 200, response.text
    agent = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    assert (
        client.request(
            'DELETE',
            f'/api/agents/{agent_id}',
            headers=HEADERS,
            json={'revision': agent['revision'], 'confirmed': True},
        ).status_code
        == 204
    )
    with store.connect() as conn:
        for table, resource_id in [
            ('rag_collections', collection),
            ('repositories', repository['id']),
            ('services', service['id']),
            ('agent_skills', skill['id']),
            ('llm_models', model['id']),
            ('mcp_servers', mcp['id']),
            ('servers', server['id']),
        ]:
            assert conn.execute(f'SELECT 1 FROM ka.{table} WHERE id=%s', (resource_id,)).fetchone()
        assert conn.execute('SELECT id,ciphertext FROM ka.credentials ORDER BY id').fetchall() == credentials
        assert conn.execute('SELECT count(*) AS n FROM ka.agents').fetchone()['n'] == 3
        assert conn.execute(
            "SELECT 1 FROM ka.audit_events WHERE action='agent.deleted' AND details->>'agent_id'=%s", (agent_id,)
        ).fetchone()
    assert client.get(f'/api/servers/{server["id"]}/ssh', headers=HEADERS).json()['has_private_key']
