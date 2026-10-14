from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import HTTPException

from knowledge_admin.profiles_api import ProfileInput
from knowledge_admin.servers.operations_store import OperationsStore
from test_api import _client
from test_operations import configured
from test_repositories import HEADERS


def wizard_payload(name, **overrides):
    """A blank wizard proposal (no model, no resources)."""
    return {'name': name, 'description': 'Purpose', 'when_to_use': 'For tests', 'system_prompt': f'Instructions for {name}.',
            'instructions': '', 'model_id': None, 'enabled': True,
            'documents_enabled': False, 'repositories_enabled': False, 'ssh_enabled': False,
            'skills_enabled': False, 'mcp_enabled': False, 'services_enabled': False,
            'all_collections': False, 'all_repositories': False,
            'collection_ids': [], 'repository_ids': [], 'skill_ids': [], 'mcp_server_ids': [], 'service_ids': [], 'servers': [],
            **overrides}


def create(client, name):
    """Create an agent through the wizard (the only creation path) and return its full profile."""
    response = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload(name))
    assert response.status_code == 201, response.text
    agent = client.get('/api/agents/' + response.json()['id'], headers=HEADERS)
    assert agent.status_code == 200, agent.text
    return agent.json()


def update(client, profile, **changes):
    value = {field: profile[field] for field in ProfileInput.model_fields}
    return client.put(f"/api/agents/{profile['id']}", headers=HEADERS, json={**value, **changes})


def test_catalog_creation_and_independent_prompts(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert client.get('/api/agents').status_code == 403
    original = client.get('/api/agents', headers=HEADERS).json()['agents']
    assert {a['id'] for a in original} == {'search', 'code', 'operations'}
    first = create(client, 'Payments')
    second = create(client, 'Documentation')
    assert first['id'] != second['id'] and 'kind' not in first
    assert not any(first[name + '_enabled'] for name in ('documents','repositories','ssh','skills','mcp','services'))
    assert first['model_id'] is None and first['collection_ids'] == first['repository_ids'] == first['skill_ids'] == []
    assert not first['all_collections'] and not first['all_repositories']
    assert (first['system_prompt'], second['system_prompt']) == ('Instructions for Payments.', 'Instructions for Documentation.')
    saved = client.put(f"/api/agents/{first['id']}/prompt", headers=HEADERS,
                       json={'revision': first['revision'], 'system_prompt': 'Payments instructions.'})
    assert saved.status_code == 200, saved.text
    assert client.get(f"/api/agents/{second['id']}", headers=HEADERS).json()['system_prompt'] == second['system_prompt']
    first = saved.json()
    assert client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Payments')).status_code == 409
    assert client.get('/api/agents/missing', headers=HEADERS).status_code == 404
    assert update(client, first, name='Renamed').status_code == 200
    assert update(client, first, name='Stale').status_code == 409


def test_resource_validation_and_per_profile_model_assignments(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    first = create(client, 'Code one')
    second = create(client, 'Code two')
    repository = client.post('/api/repositories', headers=HEADERS, json={'remote_url': 'example/repo'}).json()
    skill = client.post('/api/skills', headers=HEADERS, json={'name': 'review', 'description': 'Review procedures', 'instructions': 'Inspect retries.'}).json()
    response = update(client, first, repositories_enabled=True, repository_ids=[repository['id']])
    assert response.status_code == 200, response.text
    first = response.json()
    assert first['repository_ids'] == [repository['id']]
    saved = client.put('/api/operations/settings', headers=HEADERS, params={'agent_id': first['id']}, json={'revision': 1, 'instructions': '', 'server_ids': [], 'skill_ids': [skill['id']]})
    assert saved.status_code == 200, saved.text
    assert client.get(f"/api/agents/{first['id']}", headers=HEADERS).json()['skill_ids'] == [skill['id']]
    first = client.get(f"/api/agents/{first['id']}", headers=HEADERS).json()
    assert update(client, first, repository_ids=[str(uuid4())]).status_code == 400
    assert update(client, first, all_repositories=True).status_code == 400
    assert update(client, first, all_collections=True).status_code == 200
    first = client.get(f"/api/agents/{first['id']}", headers=HEADERS).json()
    assert client.get(f"/api/agents/{second['id']}", headers=HEADERS).json()['repository_ids'] == []
    model = client.post('/api/llm/models', headers=HEADERS, json={'name': 'Model', 'provider': 'openai', 'model': 'test', 'api_format': 'responses', 'base_url': 'http://model.test/v1'}).json()
    saved = client.put(f"/api/agents/{first['id']}/model", headers=HEADERS, json={'revision': first['revision'], 'model_id': model['id']})
    assert saved.status_code == 200, saved.text
    assert saved.json()['model_id'] == model['id']
    assert client.get(f"/api/agents/{second['id']}", headers=HEADERS).json()['model_id'] is None
    assert client.get('/api/llm', headers=HEADERS).json()['assignments']['code_model_id'] is None
    options = client.get(f"/api/agents/{first['id']}/options", headers=HEADERS)
    assert options.status_code == 200 and options.json()['repositories'][0]['id'] == repository['id']


def test_mcp_selections_are_independent_and_available_to_every_agent(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    first = create(client, 'First code')
    second = create(client, 'Second code')
    server = client.post('/api/mcp/servers', headers=HEADERS, json={'name': 'Mock', 'url': 'http://mock.test/mcp'}).json()
    with store.connect() as conn:
        conn.execute("UPDATE ka.mcp_servers SET tools='[{\"name\":\"lookup\"}]',last_tested_at=now() WHERE id=%s", (UUID(server['id']),))
    response = client.put('/api/mcp/code', headers=HEADERS, params={'agent_id': first['id']}, json={'revision': 1, 'mcp_server_ids': [server['id']]})
    assert response.status_code == 200, response.text
    assert client.get('/api/mcp/code', headers=HEADERS, params={'agent_id': first['id']}).json()['mcp_server_ids'] == [server['id']]
    assert client.get('/api/mcp/code', headers=HEADERS, params={'agent_id': second['id']}).json()['mcp_server_ids'] == []
    assert client.get('/api/mcp/code', headers=HEADERS).json()['mcp_server_ids'] == []
    assert client.get('/api/mcp/code', headers=HEADERS, params={'agent_id': 'operations'}).status_code == 200


def test_system_prompt_is_required_by_api_and_database(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    for payload in [{'system_prompt': ''}, {'system_prompt': ' \n\t '}, {'system_prompt': None}, {'system_prompt': 'bad\x00prompt'}]:
        response = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload('Incomplete', **payload))
        assert response.status_code == 400, response.text
    missing = wizard_payload('Incomplete')
    del missing['system_prompt']
    for payload in [missing]:
        response = client.post('/api/agents/wizard', headers=HEADERS, json=payload)
        assert response.status_code == 400, response.text
    assert len(client.get('/api/agents', headers=HEADERS).json()['agents']) == 3
    with pytest.raises(psycopg.errors.NotNullViolation):
        with store.connect() as conn:
            conn.execute("INSERT INTO ka.agents(id,name,system_prompt) VALUES ('missing-prompt','Missing prompt',NULL)")


def test_agent_skill_assignments_are_many_to_many_and_atomic(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    first, second = create(client, 'First specialist'), create(client, 'Second specialist')
    skills = [client.post('/api/skills', headers=HEADERS, json={
        'name': name, 'description': name, 'instructions': f'Follow {name}.'
    }).json()['id'] for name in ('inspect', 'verify')]
    first = update(client, first, skills_enabled=True, skill_ids=skills).json()
    second = update(client, second, skills_enabled=True, skill_ids=[skills[0]]).json()
    assert set(first['skill_ids']) == set(skills)
    assert second['skill_ids'] == [skills[0]]
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.skills_agents').fetchone()['n'] == 3
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_config_skills').fetchone()['n'] == 0
        for selected in (first, second):
            configs = OperationsStore(store, 'shop', selected['id']).configs(conn)
            assert set(next(c for c in configs if c['server_id'] is None)['skill_ids']) == {UUID(s) for s in selected['skill_ids']}
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute('INSERT INTO ka.skills_agents SELECT * FROM ka.skills_agents LIMIT 1')
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute("INSERT INTO ka.skills_agents(agent_id,skill_id,project_id) SELECT 'missing',skill_id,project_id FROM ka.skills_agents LIMIT 1")
    disabled = update(client, first, skills_enabled=False, skill_ids=skills)
    assert disabled.status_code == 200, disabled.text
    assert set(disabled.json()['skill_ids']) == set(skills)
    assert update(client, first, skill_ids=[]).status_code == 409
    first = disabled.json()
    invalid = update(client, first, skills_enabled=True, skill_ids=[str(uuid4())])
    assert invalid.status_code == 400
    assert client.get(f"/api/agents/{first['id']}", headers=HEADERS).json() == first
    assert update(client, first, skill_ids=[skills[0], skills[0]]).status_code == 400
    first = update(client, first, skill_ids=[skills[1]]).json()
    assert first['skill_ids'] == [skills[1]]
    assert client.get(f"/api/agents/{second['id']}", headers=HEADERS).json()['skill_ids'] == [skills[0]]
    with store.connect() as conn:
        conn.execute('DELETE FROM ka.agent_skills WHERE id=%s', (UUID(skills[1]),))
        assert conn.execute('SELECT count(*) AS n FROM ka.skills_agents WHERE agent_id=%s', (first['id'],)).fetchone()['n'] == 0
    assert client.get(f"/api/agents/{second['id']}", headers=HEADERS).json()['skill_ids'] == [skills[0]]


def test_documents_grounded_is_saved_versioned_and_kept_when_omitted(store, tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Grounded docs')
    saved = client.get(f"/api/agents/{agent['id']}", headers=HEADERS).json()
    assert saved['documents_grounded'] is False
    payload = {key: saved[key] for key in ('revision', 'name', 'description', 'when_to_use', 'enabled', 'all_collections',
        'all_repositories', 'collection_ids', 'repository_ids', 'service_ids', 'documents_enabled',
        'repositories_enabled', 'ssh_enabled', 'skills_enabled', 'mcp_enabled', 'services_enabled')}
    updated = client.put(f"/api/agents/{agent['id']}", headers=HEADERS,
                         json={**payload, 'documents_enabled': True, 'documents_grounded': True})
    assert updated.status_code == 200, updated.text
    assert updated.json()['documents_grounded'] is True
    kept = client.put(f"/api/agents/{agent['id']}", headers=HEADERS,
                      json={**payload, 'revision': updated.json()['revision'], 'documents_enabled': True})
    assert kept.json()['documents_grounded'] is True
    config = client.get(f"/api/agents/{agent['id']}/config", headers=HEADERS).json()
    assert config['documents']['grounded'] is True


def test_plain_create_endpoint_is_removed(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = client.post('/api/agents', headers=HEADERS, json={'name': 'Direct', 'system_prompt': 'x'})
    assert response.status_code == 405


def models(client) -> tuple[str, str]:
    claude = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Claude', 'provider': 'bedrock', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
        'api_format': 'native', 'aws_region': 'eu-central-1', 'api_key': 'bedrock-secret-token'}).json()['id']
    gpt = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'GPT', 'provider': 'openai', 'model': 'gpt-5', 'api_format': 'responses',
        'base_url': 'http://model.test/v1'}).json()['id']
    return claude, gpt


def test_save_details_sets_the_model(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    claude, gpt = models(client)
    agent = create(client, 'One save')
    with_gpt = update(client, agent, model_id=gpt)
    assert with_gpt.status_code == 200 and with_gpt.json()['model_id'] == gpt, with_gpt.text
    with_claude = update(client, with_gpt.json(), model_id=claude)
    assert with_claude.status_code == 200 and with_claude.json()['model_id'] == claude, with_claude.text
    value = {field: with_claude.json()[field] for field in ProfileInput.model_fields if field != 'model_id'}
    kept = client.put(f"/api/agents/{agent['id']}", headers=HEADERS, json={**value, 'description': 'kept'})
    assert kept.status_code == 200 and kept.json()['model_id'] == claude, kept.text
    cleared = update(client, kept.json(), model_id=None)
    assert cleared.status_code == 200 and cleared.json()['model_id'] is None, cleared.text
    with store.connect() as conn:
        details = conn.execute("""SELECT details FROM ka.audit_events WHERE action='agent.updated' AND details->>'agent_id'=%s
            ORDER BY created_at DESC,id DESC LIMIT 1""", (agent['id'],)).fetchone()['details']
    assert details == {'agent_id': agent['id'], 'model_id': {'from': claude, 'to': None}}
    for bad in (str(uuid4()), 'not-a-uuid'):
        assert update(client, cleared.json(), model_id=bad).status_code == 400, bad
    plain = update(client, cleared.json(), description='plain edit')
    assert plain.status_code == 200, plain.text
    with store.connect() as conn:
        details = conn.execute("""SELECT details FROM ka.audit_events WHERE action='agent.updated' AND details->>'agent_id'=%s
            ORDER BY created_at DESC,id DESC LIMIT 1""", (agent['id'],)).fetchone()['details']
    assert details == {'agent_id': agent['id']}  # no model or resources change: nothing else is audited


def test_an_agent_without_sessions_is_deleted(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Short lived')
    deleted = client.request('DELETE', f"/api/agents/{agent['id']}", headers=HEADERS,
                             json={'revision': agent['revision'], 'confirmed': True})
    assert deleted.status_code == 204, deleted.text
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agents WHERE id=%s', (agent['id'],)).fetchone()['n'] == 0


def test_agents_have_no_type(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Plain')
    assert 'runtime' not in agent and 'claude_code_tools' not in agent
    listed = next(a for a in client.get('/api/agents', headers=HEADERS).json()['agents'] if a['id'] == agent['id'])
    assert 'runtime' not in listed and 'claude_code_tools' not in listed
    extra = 'Extra inputs are not permitted'  # the validation handler answers 400 with the pydantic messages
    for bad in ({'runtime': 'openai'}, {'claude_code_tools': ['Read']}):
        refused = update(client, agent, **bad)
        assert refused.status_code == 400 and extra in refused.json()['error']['message'], bad
    wizard = {**wizard_payload('Typed'), 'runtime': 'openai'}
    refused = client.post('/api/agents/wizard', headers=HEADERS, json=wizard)
    assert refused.status_code == 400 and extra in refused.json()['error']['message']
    _, gpt = models(client)  # a real model, so only the extra field can refuse the request
    generate = client.post('/api/agents/wizard/generate', headers=HEADERS,
                           json={'role': 'x', 'model_id': gpt, 'runtime': 'openai'})
    assert generate.status_code == 400 and extra in generate.json()['error']['message']
    config = client.get(f"/api/agents/{agent['id']}/config", headers=HEADERS).json()
    assert 'runtime' not in config and 'claude_code_tools' not in config
