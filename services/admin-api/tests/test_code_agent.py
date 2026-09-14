import asyncio
from types import SimpleNamespace

import httpx
import pytest

from knowledge_admin import agent_api, code_api
from knowledge_llm import ModelUnavailable
from test_api import _client
from test_repositories import HEADERS


class CodeService:
    def repos(self):
        return [{'name': 'repo-1', 'label': 'Payments', 'path_exists': True, 'backend': 'codex'}]

    def close(self):
        pass


@pytest.fixture
def configured_code(monkeypatch):
    monkeypatch.setenv('DOCS_WIKI_CODE_AGENT_URL', 'http://code.test')
    monkeypatch.setattr(agent_api, 'CodeAgentClient', lambda _: CodeService())
    monkeypatch.setattr(code_api, 'load_profile', lambda agent_id: {
        'id': agent_id, 'repositories_enabled': True, 'all_repositories': False, 'repository_ids': ['repo-1']})


def test_code_placeholder_preserves_access_and_does_not_proxy(tmp_path, configured_code, monkeypatch):
    client = _client(tmp_path)
    async def forbidden(*args, **kwargs):
        pytest.fail('Placeholder must not call another service')
    monkeypatch.setattr(httpx.AsyncClient, 'send', forbidden)
    assert client.get('/api/agents/code/status').status_code == 403
    status = client.get('/api/agents/code/status', headers=HEADERS).json()
    assert status['ask_enabled'] and status['model'] is None
    for question in ['Inspect', 'Inspect again']:
        response = client.post('/api/agents/code/ask', headers=HEADERS, json={'repository': 'repo-1', 'question': question})
        assert response.status_code == 200 and response.text == 'TODO'
    assert client.post('/api/agents/code/ask', json={'repository': 'repo-1', 'question': 'Inspect'}).status_code == 403
    assert client.post('/api/agents/code/ask', headers=HEADERS, json={'repository': 'outside', 'question': 'Inspect'}).status_code == 403
    assert client.post('/api/agents/code/ask', headers=HEADERS, json={'repository': 'repo-1', 'question': ' '}).status_code == 400
