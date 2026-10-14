"""The agent's prompt is one text field: saved with the agent's revision, read by every runtime."""

from knowledge_llm.prompts import DEFAULT_PROMPTS, active_prompt

from knowledge_admin.knowledge_agents.config import get_agent_config
from test_api import _client
from test_llm import use_database
from test_profiles import create
from test_repositories import HEADERS


def put_prompt(client, agent_id, text, revision=None):
    if revision is None:
        revision = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()['revision']
    return client.put(
        f'/api/agents/{agent_id}/prompt', headers=HEADERS, json={'revision': revision, 'system_prompt': text}
    )


def test_seeded_agents_keep_their_prompt_text(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    use_database(monkeypatch, store)
    for agent_id in ('search', 'code', 'operations'):
        assert active_prompt(agent_id) == DEFAULT_PROMPTS[agent_id]
        assert (
            client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()['system_prompt'] == DEFAULT_PROMPTS[agent_id]
        )


def test_the_prompt_is_saved_with_the_agents_revision(store, tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    monkeypatch.setenv('DATA_ROOT_DIR', '/work/data')
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Prompted')
    assert agent['system_prompt'] == 'Instructions for Prompted.' and 'active_prompt' not in agent
    saved = put_prompt(client, agent['id'], 'Answer with evidence.', agent['revision'])
    assert saved.status_code == 200, saved.text
    assert (saved.json()['system_prompt'], saved.json()['revision']) == ('Answer with evidence.', agent['revision'] + 1)
    assert get_agent_config(agent['id'], conninfo=store.conninfo)['system_prompt'] == {
        'content': 'Answer with evidence.'
    }
    use_database(monkeypatch, store)
    assert active_prompt(agent['id']) == 'Answer with evidence.'
    stale = put_prompt(client, agent['id'], 'Older edit.', agent['revision'])
    assert stale.status_code == 409 and stale.json()['error']['message'] == 'Agent changed. Reload before saving.'
    for bad in ('', '  \n', 'bad\x00prompt', 'x' * 100001):
        assert put_prompt(client, agent['id'], bad).status_code == 400, repr(bad[:10])
    assert put_prompt(client, 'missing', 'Text', 1).status_code == 404
    assert (
        client.put(f'/api/agents/{agent["id"]}/prompt', json={'revision': 2, 'system_prompt': 'x'}).status_code == 403
    )
    with store.connect() as conn:
        audit = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent.prompt.updated'").fetchall()
    assert [row['details'] for row in audit] == [{'agent_id': agent['id']}]


def test_the_prompts_api_is_gone(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert client.get('/api/llm/prompts', headers=HEADERS).status_code == 404
    listed = client.get('/api/agents', headers=HEADERS).json()['agents']
    assert not {'prompt_title', 'prompt_version', 'system_prompt'} & set(listed[0])
