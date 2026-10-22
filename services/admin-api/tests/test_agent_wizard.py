"""Wizard proposals use the real config tables, without executing the proposed agent."""

import json
from access_helpers import of
from agents import ModelBehaviorError
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from knowledge_admin.agents.wizard import AgentProposal
from knowledge_admin.knowledge_agents.config import get_agent_config
from test_agents import saved_agent, agent_mounts
from test_repositories import HEADERS


def echoed(proposal: dict) -> dict:
    """A proposal as generate returns it (every access entry with its editable flag)."""
    return AgentProposal.model_validate(proposal).model_dump(mode='json')


@pytest.fixture
def wizard(store, saved_agent):
    client, server, ssh, skill, mcp, repo, model = saved_agent
    with store.connect() as conn:
        conn.execute('UPDATE ka.llm_models SET model=%s WHERE id=%s', ('anthropic.claude-test', UUID(model['id'])))
    response = client.get('/api/agents/wizard/options', headers=HEADERS)
    assert response.status_code == 200, response.text
    catalog = response.json()['catalog']
    proposal = {
        'name': 'Payments investigator',
        'description': 'Investigate failed payments.',
        'when_to_use': 'For payment incidents.',
        'system_prompt': 'Investigate using evidence.',
        'instructions': 'Check exit codes.',
        'model_id': model['id'],
        'enabled': True,
        'access': {
            'resources': [
                {'type': 'server', 'id': server['id']},
                {'type': 'service', 'id': catalog['services'][0]['id']},
                {'type': 'code_project', 'id': repo['id']},
                {'type': 'collection', 'id': next(c['id'] for c in catalog['collections'] if c['name'] == 'payments')},
                {'type': 'skill', 'id': skill['id']},
                {'type': 'mcp_server', 'id': mcp['id']},
            ],
            'roles': {'content_editor': False, 'inventory_editor': False},
        },
    }
    return client, catalog, proposal, ssh


def test_wizard_context_excludes_secret_material_and_is_admin_only(wizard):
    client, catalog, proposal, ssh = wizard
    response = client.get('/api/agents/wizard/options', headers=HEADERS)
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['schema'] == AgentProposal.model_json_schema()
    assert set(response.json()) == {
        'catalog',
        'schema',
        'prompt',
    }  # no default model: the UI starts with the first
    repository = next(r['id'] for r in proposal['access']['resources'] if r['type'] == 'code_project')
    assert next(p for p in catalog['code_projects'] if p['id'] == repository)['folder_paths'] == ['managed/payments']
    assert all(isinstance(p['folder_paths'], list) for p in catalog['code_projects'])
    for secret in (ssh['private_key'], 'test-model-secret', 'test-mcp-secret', 'test-git-secret', 'ciphertext'):
        assert secret not in response.text
    assert client.get('/api/agents/wizard/options').status_code == 403
    assert (
        client.post(
            '/api/agents/wizard/generate', json={'role': 'Payments', 'model_id': proposal['model_id']}
        ).status_code
        == 403
    )
    assert client.post('/api/agents/wizard', json=proposal).status_code == 403


def test_wizard_roundtrip_every_resource(store, wizard):
    client, catalog, proposal, ssh = wizard
    response = client.post('/api/agents/wizard', headers=HEADERS, json=proposal)
    assert response.status_code == 201, response.text
    agent_id = response.json()['id']
    config = get_agent_config(agent_id, conninfo=store.conninfo)
    assert config['system_prompt']['content'] == proposal['system_prompt']
    assert config['instructions'] == proposal['instructions']
    assert config['model']['api_key_ref']['id'] == proposal['model_id']
    held = {(item['type'], item['id']) for item in config['access']['resources']}
    assert held == {(item['type'], item['id']) for item in proposal['access']['resources']}
    assert config['access']['roles'] == proposal['access']['roles']
    assert [c['name'] for c in of(config, 'collection')] == ['payments']
    server = of(config, 'server')[0]
    assert server['username'] == 'operator'  # the server's own SSH settings
    assert ssh['private_key'] not in json.dumps(config)
    assert client.post('/api/agents/wizard', headers=HEADERS, json=proposal).status_code == 409
    with store.connect() as conn:
        assert (
            conn.execute('SELECT count(*) AS n FROM ka.agents WHERE name=%s', (proposal['name'],)).fetchone()['n'] == 1
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE details->>'source'='wizard'").fetchone()['n']
            == 1
        )
    # Existing editing/deletion APIs work for wizard agents and preserve shared credentials.
    assert (
        client.request(
            'DELETE', '/api/agents/' + agent_id, headers=HEADERS, json={'revision': 1, 'confirmed': True}
        ).status_code
        == 204
    )
    with store.connect() as conn:
        assert conn.execute(
            'SELECT id FROM ka.credentials WHERE id=%s', (UUID(server['private_key_ref']['id']),)
        ).fetchone()


@pytest.mark.parametrize(
    'change',
    [
        {
            'access': {
                'resources': [{'type': 'collection', 'id': str(uuid4())}],
                'roles': {'content_editor': False, 'inventory_editor': False},
            }
        },
        {
            'access': {
                'resources': [{'type': 'printer', 'id': str(uuid4())}],
                'roles': {'content_editor': False, 'inventory_editor': False},
            }
        },
        {'model_id': str(uuid4())},
        {'system_prompt': ' '},
        {'all_repositories': True},
        {'servers': []},
        {'unknown_field': True},
        {'private_key': 'not allowed'},
    ],
)
def test_wizard_invalid_proposal_writes_nothing(store, wizard, change):
    client, _, proposal, _ = wizard
    before = client.get('/api/agents', headers=HEADERS).json()
    result = client.post('/api/agents/wizard', headers=HEADERS, json={**proposal, **change})
    assert result.status_code == 400, result.text
    assert client.get('/api/agents', headers=HEADERS).json() == before


def test_wizard_rechecks_disabled_resources_before_saving(store, wizard):
    client, _, proposal, _ = wizard
    with store.connect() as conn:
        conn.execute("UPDATE ka.rag_collections SET deleted_at=now() WHERE name='payments'")
    result = client.post('/api/agents/wizard', headers=HEADERS, json=proposal)
    assert result.status_code == 400 and 'unavailable' in result.text
    with store.connect() as conn:
        assert conn.execute('SELECT id FROM ka.agents WHERE name=%s', (proposal['name'],)).fetchone() is None


def test_generation_uses_no_tools_and_does_not_save(store, wizard, monkeypatch):
    client, catalog, proposal, ssh = wizard
    calls = []

    async def run(config, task, **kwargs):
        calls.append((config, json.loads(task), kwargs))
        return SimpleNamespace(final_output=AgentProposal.model_validate(proposal))

    monkeypatch.setattr('knowledge_admin.agents.wizard.agent', run)
    before = client.get('/api/agents', headers=HEADERS).json()
    result = client.post(
        '/api/agents/wizard/generate',
        headers=HEADERS,
        json={'role': 'Investigate payments without sudo.', 'model_id': proposal['model_id']},
    )
    assert result.status_code == 200, result.text
    assert result.json()['proposal'] == echoed(proposal)
    assert result.headers['cache-control'] == 'no-store'
    assert client.get('/api/agents', headers=HEADERS).json() == before
    config, task, kwargs = calls[0]
    assert config['access']['resources'] == [] and not config['grounded']
    assert task['catalog'] == catalog
    assert task['role'] == 'Investigate payments without sudo.'
    assert kwargs['output_type'] is AgentProposal
    assert kwargs['tools'] is False  # an internal LLM call: no tools
    assert task['output_schema'] == kwargs['output_type'].model_json_schema()
    assert kwargs['credentials'] == {proposal['model_id']: 'test-model-secret'}
    for secret in (ssh['private_key'], 'test-model-secret', 'test-mcp-secret', 'test-git-secret'):
        assert secret not in json.dumps(config) + json.dumps(task)


def test_invalid_model_output_gets_one_correction_attempt(wizard, monkeypatch):
    client, _, proposal, _ = wizard
    calls = []

    async def run(config, task, **kwargs):
        calls.append(json.loads(task))
        output = {**proposal, 'system_prompt': ''} if len(calls) == 1 else proposal
        if len(calls) == 1:
            raise ModelBehaviorError('Invalid model output')
        return SimpleNamespace(final_output=AgentProposal.model_validate(output))

    monkeypatch.setattr('knowledge_admin.agents.wizard.agent', run)
    result = client.post(
        '/api/agents/wizard/generate', headers=HEADERS, json={'role': 'Payments', 'model_id': proposal['model_id']}
    )
    assert result.status_code == 200 and result.json()['proposal'] == echoed(proposal)
    assert len(calls) == 2
    assert calls[1]['correction']['errors']
    assert calls[1]['role'] == calls[0]['role']
    assert calls[1]['output_schema'] == calls[0]['output_schema']


def test_model_annotations_cannot_add_unsupported_settings(wizard, monkeypatch):
    client, _, proposal, _ = wizard

    async def run(*args, **kwargs):
        return SimpleNamespace(final_output=AgentProposal.model_validate(proposal))

    monkeypatch.setattr('knowledge_admin.agents.wizard.agent', run)
    result = client.post(
        '/api/agents/wizard/generate', headers=HEADERS, json={'role': 'Payments', 'model_id': proposal['model_id']}
    )
    assert result.status_code == 200 and result.json()['proposal'] == echoed(proposal)
    assert (
        client.post('/api/agents/wizard', headers=HEADERS, json={**proposal, 'unknown_field': True}).status_code == 400
    )


@pytest.mark.parametrize('failure', ['exception', 'invalid', 'failed'])
def test_generation_failure_is_safe_and_leaves_no_agent(wizard, monkeypatch, failure):
    client, _, proposal, _ = wizard

    async def run(*args, **kwargs):
        if failure == 'exception':
            raise RuntimeError('Authorization: test-model-secret')
        raise ModelBehaviorError('Invalid model output')

    monkeypatch.setattr('knowledge_admin.agents.wizard.agent', run)
    before = client.get('/api/agents', headers=HEADERS).json()
    result = client.post(
        '/api/agents/wizard/generate', headers=HEADERS, json={'role': 'Payments', 'model_id': proposal['model_id']}
    )
    assert result.status_code == 502 and 'test-model-secret' not in result.text
    assert client.get('/api/agents', headers=HEADERS).json() == before


BLANK = {
    'name': 'Blank agent',
    'description': '',
    'when_to_use': '',
    'system_prompt': 'Answer carefully.',
    'instructions': '',
    'model_id': None,
    'enabled': True,
    'access': {'resources': [], 'roles': {'content_editor': False, 'inventory_editor': False}},
}


def test_blank_proposal_without_model_creates_agent(store, tmp_path, monkeypatch):
    from test_api import _client

    monkeypatch.setenv('CODE_ROOT', str(tmp_path.resolve()))
    monkeypatch.setenv('DATA_ROOT_DIR', str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    response = client.post('/api/agents/wizard', headers=HEADERS, json=BLANK)
    assert response.status_code == 201, response.text
    agent = client.get('/api/agents/' + response.json()['id'], headers=HEADERS).json()
    assert agent['name'] == 'Blank agent' and agent['model_id'] is None
    assert agent['system_prompt'] == BLANK['system_prompt']
    assert (
        client.post(
            '/api/agents/wizard', headers=HEADERS, json={**BLANK, 'name': 'Other', 'system_prompt': ' '}
        ).status_code
        == 400
    )
    long = 'p' * 100000
    response = client.post(
        '/api/agents/wizard', headers=HEADERS, json={**BLANK, 'name': 'Long prompt', 'system_prompt': long}
    )
    assert response.status_code == 201, response.text
    assert client.get('/api/agents/' + response.json()['id'], headers=HEADERS).json()['system_prompt'] == long
    assert (
        client.post(
            '/api/agents/wizard', headers=HEADERS, json={**BLANK, 'name': 'Too long', 'system_prompt': long + 'p'}
        ).status_code
        == 400
    )


def test_proposal_rejects_an_unknown_resource_type(wizard):
    _, _, proposal, _ = wizard
    from pydantic import ValidationError

    unknown = {
        'resources': [{'type': 'printer', 'id': str(uuid4())}],
        'roles': {'content_editor': False, 'inventory_editor': False},
    }
    with pytest.raises(ValidationError, match="'server'"):
        AgentProposal.model_validate({**proposal, 'access': unknown})


def test_inconsistent_model_output_gets_one_correction_attempt(wizard, monkeypatch):
    client, _, proposal, _ = wizard
    calls = []

    async def run(config, task, **kwargs):
        calls.append(json.loads(task))
        invented = {
            'resources': [{'type': 'server', 'id': str(uuid4())}],
            'roles': {'content_editor': False, 'inventory_editor': False},
        }
        output = {**proposal, 'access': invented} if len(calls) == 1 else proposal
        return SimpleNamespace(final_output=AgentProposal.model_validate(output))

    monkeypatch.setattr('knowledge_admin.agents.wizard.agent', run)
    result = client.post(
        '/api/agents/wizard/generate', headers=HEADERS, json={'role': 'Payments', 'model_id': proposal['model_id']}
    )
    assert result.status_code == 200 and result.json()['proposal'] == echoed(proposal)
    assert len(calls) == 2
    assert any('unavailable' in e for e in calls[1]['correction']['errors'])


def test_generation_offers_the_models_that_fit_an_agent(store, wizard, monkeypatch):
    client, catalog, proposal, _ = wizard
    with store.connect() as conn:
        actor = conn.execute('SELECT created_by FROM ka.llm_models LIMIT 1').fetchone()['created_by']
        gpt = str(
            conn.execute(
                """INSERT INTO ka.llm_models(name,provider,model,api_format,base_url,api_key_ciphertext,created_by,updated_by)
            VALUES ('GPT','openai','gpt-test','responses','https://api.openai.com/v1',%s,%s,%s) RETURNING id""",
                (b'\x00', actor, actor),
            ).fetchone()['id']
        )
    tasks = []

    async def run(config, task, **kwargs):
        tasks.append(json.loads(task))
        return SimpleNamespace(final_output=AgentProposal.model_validate({**proposal, 'model_id': gpt}))

    monkeypatch.setattr('knowledge_admin.agents.wizard.agent', run)

    native = client.post(
        '/api/agents/wizard/generate', headers=HEADERS, json={'role': 'Payments', 'model_id': proposal['model_id']}
    )
    assert native.status_code == 200, native.text
    assert gpt in {m['id'] for m in tasks[0]['catalog']['models']}
    assert native.json()['proposal']['model_id'] == gpt
    assert 'agent_type' not in tasks[0]  # the designer is not told an agent type
