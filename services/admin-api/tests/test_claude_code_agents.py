"""Claude Code agents are registrations: a name and a description, kept out of every place that runs OpenAI agents."""
from uuid import uuid4

import pytest
from knowledge_llm import ModelUnavailable
from knowledge_llm.profiles import profile

from knowledge_admin.agents import get_agent_config
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
    assert (listed['runtime'], listed['model_id'], listed['prompt_version'], listed['config_version']) == ('claude_code', None, None, None)
    assert not any(listed[flag] for flag in FLAGS)
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    assert (detail['name'], detail['description'], detail['runtime'], detail['active_prompt']) == (
        'Technical writer', 'Writes documentation', 'claude_code', None)
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
    with pytest.raises(ValueError, match='documentation sessions only'):
        get_agent_config(agent_id, conninfo=store.conninfo)
    assert client.post(f'/api/agents/{agent_id}/sessions', headers=HEADERS, json={'title': 'Test', 'is_test': True}).status_code == 409
    assert client.get(f'/api/agents/{agent_id}/versions/source', headers=HEADERS).status_code == 409
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    model = client.put(f'/api/agents/{agent_id}/model', headers=HEADERS, json={'revision': detail['revision'], 'model_id': None})
    assert model.status_code == 409 and 'when a session starts' in model.json()['error']['message']
    settings = {'revision': detail['revision'], 'name': 'Writer', 'description': '', 'when_to_use': '', 'enabled': True,
                'all_collections': False, 'all_repositories': False, 'collection_ids': [], 'repository_ids': [],
                'service_ids': [], **{flag: False for flag in FLAGS}}
    assert client.put(f'/api/agents/{agent_id}', headers=HEADERS, json=settings).status_code == 409
    candidates = client.get('/api/reader/settings', headers=HEADERS).json()['candidates']
    assert agent_id not in [candidate['id'] for candidate in candidates] and 'search' in [c['id'] for c in candidates]
    board = client.post('/api/agent-board/tasks', headers=HEADERS,
                        json={'id': str(uuid4()), 'task': 'Document payments', 'subagent_ids': [agent_id]})
    assert board.status_code == 400 and 'Claude Code agents' in board.json()['error']['message']
    with store.connect() as conn:
        assert profile(conn, agent_id)['runtime'] == 'claude_code'
        with pytest.raises(ModelUnavailable):
            profile(conn, agent_id, runnable=True)
