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
        assert response.json() == client.get(f'/api/agents/{agent_id}/{path}', headers=HEADERS).json(), path
    started = events(client.post(run_path, headers=HEADERS, json={'task': 'Hi'}))[0]
    assert started['context']['config']['id'] == agent_id


def test_a_follow_up_keeps_the_configuration_its_conversation_started_with(direct):
    client, agent_id, run_path = direct
    first = events(client.post(run_path, headers=HEADERS, json={'task': 'Hi', 'is_test': True}))
    session_id, old = first[0]['session_id'], first[-1]['context']['config']['system_prompt']
    profile = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    changed = client.put(f'/api/agents/{agent_id}/prompt', headers=HEADERS,
                         json={'system_prompt': 'A changed prompt.', 'revision': profile['revision']})
    assert changed.status_code == 200, changed.text
    follow_up = events(client.post(run_path, headers=HEADERS, json={'task': 'Again', 'session_id': session_id}))
    assert follow_up[0]['session_id'] == session_id
    assert follow_up[-1]['context']['config']['system_prompt'] == old and old['content'] != 'A changed prompt.'
    fresh = events(client.post(run_path, headers=HEADERS, json={'task': 'Hi'}))
    assert fresh[-1]['context']['config']['system_prompt']['content'] == 'A changed prompt.'
