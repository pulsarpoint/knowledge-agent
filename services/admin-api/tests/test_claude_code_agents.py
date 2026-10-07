"""Claude Code agents are registrations: a name and a description, kept out of every place that runs OpenAI agents."""
from uuid import uuid4

import pytest
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile

from knowledge_admin.claude_code import BUILT_IN_PROMPT_TITLE
from test_api import _client
from test_repositories import HEADERS

FLAGS = ('documents_enabled', 'repositories_enabled', 'ssh_enabled', 'skills_enabled', 'mcp_enabled', 'services_enabled')


@pytest.fixture
def writer(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = client.post('/api/claude-code/agents', headers=HEADERS,
                           json={'name': 'Technical writer', 'description': 'Writes documentation'})
    assert response.status_code == 201, response.text
    return client, response.json()['id']


def test_claude_code_agent_is_created_listed_renamed_and_deleted(writer):
    client, agent_id = writer
    listed = next(a for a in client.get('/api/agents', headers=HEADERS).json()['agents'] if a['id'] == agent_id)
    assert (listed['runtime'], listed['model_id'], listed['prompt_version'], listed['config_version']) == ('claude_code', None, 1, None)
    assert not any(listed[flag] for flag in FLAGS)
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    assert (detail['name'], detail['description'], detail['runtime'], detail['active_prompt']['title']) == (
        'Technical writer', 'Writes documentation', 'claude_code', BUILT_IN_PROMPT_TITLE)
    renamed = client.put(f'/api/claude-code/agents/{agent_id}', headers=HEADERS,
                         json={'name': 'Writer', 'description': '', 'revision': detail['revision']})
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()['name'], renamed.json()['revision']) == ('Writer', detail['revision'] + 1)
    assert client.put(f'/api/claude-code/agents/{agent_id}', headers=HEADERS,
                      json={'name': 'Again', 'revision': detail['revision']}).status_code == 409
    assert client.put('/api/claude-code/agents/search', headers=HEADERS, json={'name': 'Search', 'revision': 1}).status_code == 404
    assert client.post('/api/claude-code/agents', headers=HEADERS, json={'name': 'Writer'}).status_code == 409
    for bad in ({'name': ''}, {'name': 'a\x00b'}, {'name': 'Ok', 'model_id': str(uuid4())},
                {'name': 'Ok', 'instructions': 'Write well'}, {'name': 'Ok', 'revision': 1}):
        assert client.post('/api/claude-code/agents', headers=HEADERS, json=bad).status_code == 400, bad
    assert client.post('/api/claude-code/agents', json={'name': 'Anonymous'}).status_code == 403
    deleted = client.request('DELETE', f'/api/agents/{agent_id}', headers=HEADERS,
                             json={'revision': renamed.json()['revision'], 'confirmed': True})
    assert deleted.status_code == 204, deleted.text


def test_claude_code_agents_are_hidden_from_openai_only_places(store, writer):
    client, agent_id = writer
    assert client.post(f'/api/agents/{agent_id}/sessions', headers=HEADERS, json={'title': 'Test', 'is_test': True}).status_code == 409
    assert client.get(f'/api/agents/{agent_id}/versions/source', headers=HEADERS).status_code == 409
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    model = client.put(f'/api/agents/{agent_id}/model', headers=HEADERS, json={'revision': detail['revision'], 'model_id': None})
    assert model.status_code == 409 and 'when a session starts' in model.json()['error']['message']
    settings = {'revision': detail['revision'], 'name': 'Writer', 'description': '', 'when_to_use': '', 'enabled': True,
                'all_collections': False, 'all_repositories': False, 'collection_ids': [], 'repository_ids': [],
                'service_ids': [], **{flag: False for flag in FLAGS}}
    assert client.put(f'/api/agents/{agent_id}', headers=HEADERS, json=settings).status_code == 409
    board = client.post('/api/agent-board/tasks', headers=HEADERS,
                        json={'id': str(uuid4()), 'task': 'Document payments', 'subagent_ids': [agent_id]})
    assert board.status_code == 400 and 'Claude Code agents' in board.json()['error']['message']
    with store.connect() as conn:
        assert profile(conn, agent_id)['runtime'] == 'claude_code'
        with pytest.raises(ModelUnavailable):
            profile(conn, agent_id, runnable=True)


def test_claude_code_agents_have_no_ssh_operations_or_mcp_settings(store, writer):
    from test_operations import setup
    client, agent_id = writer
    server, value = setup(client)
    ssh = f"/api/agents/{agent_id}/servers/{server['id']}/ssh"
    agent_ssh = {key: value[key] for key in ('username', 'port', 'allow_sudo', 'known_hosts')}
    calls = [
        ('GET', f'/api/operations/settings?agent_id={agent_id}', None),
        ('PUT', f'/api/operations/settings?agent_id={agent_id}',
         {'revision': 1, 'instructions': 'Verify', 'server_ids': [server['id']], 'skill_ids': []}),
        ('PUT', f"/api/operations/configs/{server['id']}?agent_id={agent_id}",
         {'settings_revision': 1, 'revision': None, 'instructions': 'Only here', 'skill_ids': None}),
        ('DELETE', f"/api/operations/configs/{server['id']}?agent_id={agent_id}",
         {'settings_revision': 1, 'revision': 1, 'confirmed': True}),
        ('GET', ssh, None),
        ('PUT', ssh, {'revision': 0, 'server_revision': server['revision'], **agent_ssh}),
        ('DELETE', ssh, {'revision': 1, 'server_revision': server['revision'], 'confirmed': True}),
        ('POST', ssh + '/test', {'revision': 0, 'server_revision': server['revision'], **agent_ssh}),
        ('GET', f'/api/mcp/code?agent_id={agent_id}', None),
        ('PUT', f'/api/mcp/code?agent_id={agent_id}', {'revision': 1, 'mcp_server_ids': []}),
    ]
    for method, path, body in calls:
        response = client.request(method, path, headers=HEADERS, json=body)
        assert response.status_code == 409, (method, path, response.text)
        assert 'documentation sessions only' in response.json()['error']['message'], (method, path)
    with store.connect() as conn:
        for table, column in (('operations_settings', 'agent'), ('operations_servers', 'agent'), ('agent_configs', 'agent'),
                              ('agent_server_connections', 'agent_id'), ('skills_agents', 'agent_id')):
            assert conn.execute(f'SELECT count(*) AS n FROM ka.{table} WHERE {column}=%s', (agent_id,)).fetchone()['n'] == 0, table


def test_claude_code_agent_with_retained_sessions_cannot_be_deleted(store, writer):
    client, agent_id = writer
    model = client.post('/api/llm/models', headers=HEADERS, json={'name': 'test', 'provider': 'openai', 'model': 'test',
                        'api_format': 'responses', 'base_url': 'http://model.test/v1'}).json()
    with store.connect() as conn:
        conn.execute('''INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,document_name,folder) SELECT %s,p.id,%s,(SELECT id FROM ka.principals LIMIT 1),%s,'Payments',
            '{}','{}','Payments','payments' FROM ka.projects p WHERE p.archived_at IS NULL''', (uuid4(), agent_id, model['id']))
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    deleted = client.request('DELETE', f'/api/agents/{agent_id}', headers=HEADERS,
                             json={'revision': detail['revision'], 'confirmed': True})
    assert deleted.status_code == 409 and 'retained sessions' in deleted.json()['error']['message']
    with store.connect() as conn:
        assert conn.execute('SELECT 1 FROM ka.agents WHERE id=%s', (agent_id,)).fetchone() is not None
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc WHERE agent_id=%s', (agent_id,)).fetchone()['n'] == 1


def test_claude_code_agent_can_be_disabled_and_enabled(writer):
    client, agent_id = writer
    revision = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()['revision']
    disabled = client.put(f'/api/claude-code/agents/{agent_id}', headers=HEADERS,
                          json={'name': 'Technical writer', 'description': '', 'enabled': False, 'revision': revision})
    assert disabled.status_code == 200, disabled.text
    assert (disabled.json()['enabled'], disabled.json()['revision']) == (False, revision + 1)
    listed = next(a for a in client.get('/api/agents', headers=HEADERS).json()['agents'] if a['id'] == agent_id)
    assert listed['enabled'] is False
    kept = client.put(f'/api/claude-code/agents/{agent_id}', headers=HEADERS,
                      json={'name': 'Writer', 'description': '', 'revision': revision + 1})  # no `enabled`: unchanged
    assert kept.json()['enabled'] is False
    enabled = client.put(f'/api/claude-code/agents/{agent_id}', headers=HEADERS,
                         json={'name': 'Writer', 'description': '', 'enabled': True, 'revision': revision + 2})
    assert enabled.json()['enabled'] is True
    assert client.put(f'/api/claude-code/agents/{agent_id}', headers=HEADERS,
                      json={'name': 'Writer', 'enabled': 'no', 'revision': revision + 3}).status_code == 400
