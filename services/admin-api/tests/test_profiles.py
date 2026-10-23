from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import HTTPException

from knowledge_admin.agents.profiles_api import ProfileInput
from test_api import _client
from test_operations import configured
from test_repositories import HEADERS


def wizard_payload(name, **overrides):
    """A blank wizard proposal (no model, no resources)."""
    return {
        'name': name,
        'description': 'Purpose',
        'when_to_use': 'For tests',
        'system_prompt': f'Instructions for {name}.',
        'instructions': '',
        'model_id': None,
        'enabled': True,
        'access': {'resources': [], 'roles': {'content_editor': False}},
        **overrides,
    }


def create(client, name):
    """Create an agent through the wizard (the only creation path) and return its full profile."""
    response = client.post('/api/agents/wizard', headers=HEADERS, json=wizard_payload(name))
    assert response.status_code == 201, response.text
    agent = client.get('/api/agents/' + response.json()['id'], headers=HEADERS)
    assert agent.status_code == 200, agent.text
    return agent.json()


def update(client, profile, **changes):
    value = {field: profile[field] for field in ProfileInput.model_fields}
    return client.put(f'/api/agents/{profile["id"]}', headers=HEADERS, json={**value, **changes})


def test_catalog_creation_and_independent_prompts(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert client.get('/api/agents').status_code == 403
    original = client.get('/api/agents', headers=HEADERS).json()['agents']
    assert {a['id'] for a in original} == {'search', 'code', 'operations'}
    first = create(client, 'Payments')
    second = create(client, 'Documentation')
    assert first['id'] != second['id'] and 'kind' not in first
    assert first['model_id'] is None and first['instructions'] == ''
    assert first['access'] == {'resources': [], 'roles': {'content_editor': False}}
    assert (first['system_prompt'], second['system_prompt']) == (
        'Instructions for Payments.',
        'Instructions for Documentation.',
    )
    saved = client.put(
        f'/api/agents/{first["id"]}/prompt',
        headers=HEADERS,
        json={'revision': first['revision'], 'system_prompt': 'Payments instructions.'},
    )
    assert saved.status_code == 200, saved.text
    assert client.get(f'/api/agents/{second["id"]}', headers=HEADERS).json()['system_prompt'] == second['system_prompt']
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
    skill = client.post(
        '/api/skills',
        headers=HEADERS,
        json={'name': 'review', 'description': 'Review procedures', 'instructions': 'Inspect retries.'},
    ).json()
    code = {
        'resources': [{'type': 'code_project', 'id': repository['id']}],
        'roles': {'content_editor': False},
    }
    response = update(client, first, access=code)
    assert response.status_code == 200, response.text
    first = response.json()
    assert [(r['type'], r['id']) for r in first['access']['resources']] == [('code_project', repository['id'])]
    skilled = {**code, 'resources': [*code['resources'], {'type': 'skill', 'id': skill['id']}]}
    first = update(client, first, access=skilled).json()
    assert [r['id'] for r in first['access']['resources']] == [repository['id'], skill['id']]
    gone = {**code, 'resources': [{'type': 'code_project', 'id': str(uuid4())}]}
    first = update(client, first, access=gone).json()  # a resource that does not exist is dropped
    assert first['access']['resources'] == []
    assert client.get(f'/api/agents/{second["id"]}', headers=HEADERS).json()['access']['resources'] == []
    model = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'Model',
            'provider': 'openai',
            'model': 'test',
            'api_format': 'responses',
            'base_url': 'http://model.test/v1',
        },
    ).json()
    saved = client.put(
        f'/api/agents/{first["id"]}/model',
        headers=HEADERS,
        json={'revision': first['revision'], 'model_id': model['id']},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()['model_id'] == model['id']
    assert client.get(f'/api/agents/{second["id"]}', headers=HEADERS).json()['model_id'] is None
    assert client.get('/api/llm', headers=HEADERS).json()['assignments']['code_model_id'] is None
    options = client.get(f'/api/agents/{first["id"]}/options', headers=HEADERS)
    assert options.status_code == 200 and options.json()['code_project'][0]['id'] == repository['id']


def test_system_prompt_is_required_by_api_and_database(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    for payload in [
        {'system_prompt': ''},
        {'system_prompt': ' \n\t '},
        {'system_prompt': None},
        {'system_prompt': 'bad\x00prompt'},
    ]:
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


def test_skills_are_referenced_by_many_agents_and_a_deleted_one_shows_missing(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    first, second = create(client, 'First specialist'), create(client, 'Second specialist')
    skills = [
        client.post(
            '/api/skills', headers=HEADERS, json={'name': name, 'description': name, 'instructions': f'Follow {name}.'}
        ).json()['id']
        for name in ('inspect', 'verify')
    ]

    def access(*ids):
        return {
            'resources': [{'type': 'skill', 'id': i} for i in ids],
            'roles': {'content_editor': False},
        }

    first = update(client, first, access=access(*skills)).json()
    second = update(client, second, access=access(skills[0])).json()
    assert [r['id'] for r in first['access']['resources']] == skills
    assert [r['id'] for r in second['access']['resources']] == [skills[0]]
    with store.connect() as conn:
        conn.execute('DELETE FROM ka.agent_skills WHERE id=%s', (UUID(skills[1]),))
    found = client.get(f'/api/agents/{first["id"]}', headers=HEADERS).json()['access']['resources']
    assert [(r['id'], r.get('missing', False)) for r in found] == [(skills[0], False), (skills[1], True)]
    saved = update(client, client.get(f'/api/agents/{first["id"]}', headers=HEADERS).json()).json()
    assert [r['id'] for r in saved['access']['resources']] == [skills[0]]  # saving drops the missing one
    assert [
        r['id'] for r in client.get(f'/api/agents/{second["id"]}', headers=HEADERS).json()['access']['resources']
    ] == [skills[0]]


def test_documents_grounded_is_saved_versioned_and_kept_when_omitted(store, tmp_path, monkeypatch):
    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Grounded docs')
    saved = client.get(f'/api/agents/{agent["id"]}', headers=HEADERS).json()
    assert saved['documents_grounded'] is False
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{}')", (store.project(conn, 'shop'),)
        )
        collection = conn.execute(
            "INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,'runbooks') RETURNING id",
            (store.project(conn, 'shop'),),
        ).fetchone()['id']
    access = {
        'resources': [{'type': 'collection', 'id': str(collection)}],
        'roles': {'content_editor': False},
    }
    payload = {key: saved[key] for key in ('revision', 'name', 'description', 'when_to_use', 'enabled', 'instructions')}
    updated = client.put(
        f'/api/agents/{agent["id"]}', headers=HEADERS, json={**payload, 'access': access, 'documents_grounded': True}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()['documents_grounded'] is True
    kept = client.put(
        f'/api/agents/{agent["id"]}',
        headers=HEADERS,
        json={**payload, 'revision': updated.json()['revision'], 'access': access},
    )
    assert kept.json()['documents_grounded'] is True
    config = client.get(f'/api/agents/{agent["id"]}/config', headers=HEADERS).json()
    assert config['grounded'] is True


def test_plain_create_endpoint_is_removed(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = client.post('/api/agents', headers=HEADERS, json={'name': 'Direct', 'system_prompt': 'x'})
    assert response.status_code == 405


def models(client) -> tuple[str, str]:
    claude = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'Claude',
            'provider': 'bedrock',
            'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
            'api_format': 'native',
            'aws_region': 'eu-central-1',
            'api_key': 'bedrock-secret-token',
        },
    ).json()['id']
    gpt = client.post(
        '/api/llm/models',
        headers=HEADERS,
        json={
            'name': 'GPT',
            'provider': 'openai',
            'model': 'gpt-5',
            'api_format': 'responses',
            'base_url': 'http://model.test/v1',
        },
    ).json()['id']
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
    kept = client.put(f'/api/agents/{agent["id"]}', headers=HEADERS, json={**value, 'description': 'kept'})
    assert kept.status_code == 200 and kept.json()['model_id'] == claude, kept.text
    cleared = update(client, kept.json(), model_id=None)
    assert cleared.status_code == 200 and cleared.json()['model_id'] is None, cleared.text
    with store.connect() as conn:
        details = conn.execute(
            """SELECT details FROM ka.audit_events WHERE action='agent.updated' AND details->>'agent_id'=%s
            ORDER BY created_at DESC,id DESC LIMIT 1""",
            (agent['id'],),
        ).fetchone()['details']
    assert details == {'agent_id': agent['id'], 'model_id': {'from': claude, 'to': None}}
    for bad in (str(uuid4()), 'not-a-uuid'):
        assert update(client, cleared.json(), model_id=bad).status_code == 400, bad
    plain = update(client, cleared.json(), description='plain edit')
    assert plain.status_code == 200, plain.text
    with store.connect() as conn:
        details = conn.execute(
            """SELECT details FROM ka.audit_events WHERE action='agent.updated' AND details->>'agent_id'=%s
            ORDER BY created_at DESC,id DESC LIMIT 1""",
            (agent['id'],),
        ).fetchone()['details']
    assert details == {'agent_id': agent['id']}  # no model or resources change: nothing else is audited


def test_an_agent_without_sessions_is_deleted(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Short lived')
    deleted = client.request(
        'DELETE', f'/api/agents/{agent["id"]}', headers=HEADERS, json={'revision': agent['revision'], 'confirmed': True}
    )
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
    generate = client.post(
        '/api/agents/wizard/generate', headers=HEADERS, json={'role': 'x', 'model_id': gpt, 'runtime': 'openai'}
    )
    assert generate.status_code == 400 and extra in generate.json()['error']['message']
    config = client.get(f'/api/agents/{agent["id"]}/config', headers=HEADERS).json()
    assert 'runtime' not in config and 'claude_code_tools' not in config
