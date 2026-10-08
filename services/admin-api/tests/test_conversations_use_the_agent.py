"""A conversation runs its agent's configuration: no version is chosen per conversation, run or preview."""
from uuid import uuid4

from test_agent_runs import direct, events  # noqa: F401  (fixture)
from test_repositories import HEADERS
from test_sdk_agent import responses_api  # noqa: F401  (fixture)


def test_no_conversation_chooses_a_version(direct):
    client, agent_id, run_path = direct
    version = str(uuid4())
    assert client.post(run_path, headers=HEADERS, json={'task': 'Hi', 'version_id': version}).status_code == 400
    assert client.post(f'/api/agents/{agent_id}/sessions', headers=HEADERS,
                       json={'version_id': version, 'is_test': True}).status_code == 400
    for path in ('config', 'run-resources'):
        response = client.get(f'/api/agents/{agent_id}/{path}', headers=HEADERS, params={'version_id': version})
        assert response.status_code == 200, (path, response.text)  # the parameter is not read
    started = events(client.post(run_path, headers=HEADERS, json={'task': 'Hi'}))[0]
    assert started['context']['config']['id'] == agent_id
