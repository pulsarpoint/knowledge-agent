from fastapi.testclient import TestClient
from knowledge_llm import ModelUnavailable

from code_agent import api
from code_agent.config import Config, Repo


def test_todo_preserves_repository_scope_and_disabled_agent(repo, monkeypatch):
    monkeypatch.setenv('LLM_SETTINGS_SOURCE', 'database')
    selected = []
    profile = {'repositories_enabled': True, 'all_repositories': False, 'repository_ids': ['allowed']}
    def load(agent_id):
        selected.append(agent_id)
        return profile
    monkeypatch.setattr(api, 'load_profile', load)
    client = TestClient(api.create_app(Config([Repo('allowed', repo), Repo('outside', repo)])))
    assert client.post('/repos/outside/ask', json={'question': 'Read', 'agent_id': 'specialist'}).status_code == 403
    response = client.post('/repos/allowed/ask', json={'question': 'Read', 'agent_id': 'specialist'})
    assert response.status_code == 200 and response.text == 'TODO'
    assert selected == ['specialist', 'specialist']
    def disabled(*args, **kwargs):
        raise ModelUnavailable('Agent is disabled')
    monkeypatch.setattr(api, 'load_profile', disabled)
    assert client.post('/repos/allowed/ask', json={'question': 'Read', 'agent_id': 'specialist'}).status_code == 409
