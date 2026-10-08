"""Previews run the agent's own configuration (no version is chosen), and the in-request Test chat endpoints are gone:
Test sessions run in agent-session-worker."""
from uuid import uuid4

from test_agents import agent_mounts, saved_agent  # noqa: F401  (fixtures)
from test_repositories import HEADERS


def test_no_preview_chooses_a_version(saved_agent):
    client = saved_agent[0]
    version = str(uuid4())
    for path in ('config', 'run-resources'):
        response = client.get(f'/api/agents/operations/{path}', headers=HEADERS, params={'version_id': version})
        assert response.status_code == 200, (path, response.text)  # the parameter is not read
        assert response.json() == client.get(f'/api/agents/operations/{path}', headers=HEADERS).json(), path


def test_the_old_test_chat_endpoints_are_gone(saved_agent):
    client, session, run = saved_agent[0], str(uuid4()), str(uuid4())
    for method, path in (('POST', '/api/agents/operations/runs'), ('GET', '/api/agents/operations/runs'),
                         ('GET', f'/api/agents/operations/runs/{run}'),
                         ('POST', f'/api/agents/operations/runs/{run}/cancel'),
                         ('GET', f'/api/agents/operations/sessions/{session}'),
                         ('POST', f'/api/agents/operations/sessions/{session}/close')):
        body = {'json': {'task': 'Hi'}} if method == 'POST' else {}
        assert client.request(method, path, headers=HEADERS, **body).status_code == 404, (method, path)
