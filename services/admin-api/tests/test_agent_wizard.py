"""Wizard proposals use the real config tables, without executing the proposed agent."""
import json
from agents import ModelBehaviorError
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from knowledge_admin.agent_wizard import AgentProposal
from knowledge_admin.agents import get_agent_config
from test_agents import saved_agent, agent_mounts
from test_repositories import HEADERS


@pytest.fixture
def wizard(store, saved_agent):
    client, server, ssh, skill, mcp, repo, model = saved_agent
    with store.connect() as conn:
        conn.execute('UPDATE ka.llm_models SET model=%s WHERE id=%s', ('anthropic.claude-test', UUID(model['id'])))
    response = client.get('/api/agents/wizard/options', headers=HEADERS)
    assert response.status_code == 200, response.text
    catalog = response.json()['catalog']
    proposal = {
        'name': 'Payments investigator', 'description': 'Investigate failed payments.',
        'when_to_use': 'For payment incidents.', 'system_prompt': 'Investigate using evidence.',
        'instructions': 'Check exit codes.', 'model_id': model['id'], 'enabled': True,
        'documents_enabled': True, 'repositories_enabled': True, 'ssh_enabled': True,
        'skills_enabled': True, 'mcp_enabled': True, 'services_enabled': True,
        'all_collections': False, 'all_repositories': False,
        'collection_ids': [next(c['id'] for c in catalog['collections'] if c['name'] == 'payments')],
        'repository_ids': [repo['id']], 'skill_ids': [skill['id']], 'mcp_server_ids': [mcp['id']],
        'service_ids': [catalog['services'][0]['id']],
        'servers': [{'server_id': server['id'], 'instructions': 'Inspect the queue first.',
                     'skill_ids': [], 'mcp_server_ids': None, 'connection': {
                         'username': 'investigator', 'port': 2222, 'allow_sudo': False,
                         'known_hosts': ssh['known_hosts'], 'credential_id': catalog['servers'][0]['credential_id']}}],
    }
    return client, catalog, proposal, ssh


def test_wizard_context_excludes_secret_material_and_is_admin_only(wizard):
    client, catalog, proposal, ssh = wizard
    response = client.get('/api/agents/wizard/options', headers=HEADERS)
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['schema'] == AgentProposal.model_json_schema()
    assert set(response.json()) == {'catalog', 'schema', 'prompt', 'tools'}  # no default model: the UI starts with the first
    assert next(p for p in catalog['code_projects'] if p['id'] == proposal['repository_ids'][0])['folder_paths'] == ['managed/payments']
    assert all(isinstance(p['folder_paths'], list) for p in catalog['code_projects'])
    for secret in (ssh['private_key'], 'test-model-secret', 'test-mcp-secret', 'test-git-secret', 'ciphertext'):
        assert secret not in response.text
    assert client.get('/api/agents/wizard/options').status_code == 403
    assert client.post('/api/agents/wizard/generate', json={'role':'Payments', 'model_id':proposal['model_id']}).status_code == 403
    assert client.post('/api/agents/wizard', json=proposal).status_code == 403


def test_wizard_roundtrip_every_resource_and_ssh_override(store, wizard):
    client, catalog, proposal, ssh = wizard
    response = client.post('/api/agents/wizard', headers=HEADERS, json=proposal)
    assert response.status_code == 201, response.text
    agent_id = response.json()['id']
    config = get_agent_config(agent_id, conninfo=store.conninfo)
    assert config['system_prompt']['content'] == proposal['system_prompt']
    assert config['instructions'] == proposal['instructions']
    assert config['model']['api_key_ref']['id'] == proposal['model_id']
    assert [p['id'] for p in config['code']['projects']] == proposal['repository_ids']
    assert [c['name'] for c in config['documents']['collections']] == ['payments']
    assert [s['id'] for s in config['skills']['items']] == proposal['skill_ids']
    assert [s['id'] for s in config['mcp']['servers']] == proposal['mcp_server_ids']
    assert [s['id'] for s in config['services']['items']] == proposal['service_ids']
    server = config['ssh']['servers'][0]
    assert (server['username'], server['port'], server['allow_sudo']) == ('investigator', 2222, False)
    assert server['instructions'] == 'Inspect the queue first.'
    assert server['skills'] == [] and server['mcp_servers'] == config['mcp']['servers']
    assert server['private_key_ref']['id'] == catalog['servers'][0]['credential_id']
    assert ssh['private_key'] not in json.dumps(config)
    assert client.post('/api/agents/wizard', headers=HEADERS, json=proposal).status_code == 409
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agents WHERE name=%s', (proposal['name'],)).fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE details->>'source'='wizard'").fetchone()['n'] == 1
    # Existing editing/deletion APIs work for wizard agents and preserve shared credentials.
    assert client.request('DELETE', '/api/agents/' + agent_id, headers=HEADERS, json={'revision':1,'confirmed':True}).status_code == 204
    with store.connect() as conn:
        assert conn.execute('SELECT id FROM ka.credentials WHERE id=%s', (UUID(server['private_key_ref']['id']),)).fetchone()


def test_wizard_server_defaults_and_all_resources(store, wizard):
    client, _, proposal, _ = wizard
    proposal.update(all_collections=True, collection_ids=[], all_repositories=True, repository_ids=[])
    proposal['servers'][0].update(instructions=None, skill_ids=None, mcp_server_ids=[], connection=None)
    response = client.post('/api/agents/wizard', headers=HEADERS, json=proposal)
    assert response.status_code == 201, response.text
    config = get_agent_config(response.json()['id'], conninfo=store.conninfo)
    assert len(config['code']['projects']) == 2 and len(config['documents']['collections']) == 4
    server = config['ssh']['servers'][0]
    assert server['connection_source'] == 'server' and server['username'] == 'operator'
    assert server['instructions'] == proposal['instructions'] and server['skills'] == config['skills']['items']
    assert server['mcp_servers'] == []


@pytest.mark.parametrize('change', [
    {'collection_ids':[str(uuid4())]}, {'model_id':str(uuid4())}, {'system_prompt':' '},
    {'all_repositories':True}, {'unknown_field':True}, {'private_key':'not allowed'},
])
def test_wizard_invalid_proposal_writes_nothing(store, wizard, change):
    client, _, proposal, _ = wizard
    before = client.get('/api/agents', headers=HEADERS).json()
    result = client.post('/api/agents/wizard', headers=HEADERS, json={**proposal, **change})
    assert result.status_code == 400, result.text
    assert client.get('/api/agents', headers=HEADERS).json() == before


def test_wizard_rechecks_disabled_resources_before_saving(store, wizard):
    client, _, proposal, _ = wizard
    with store.connect() as conn:
        conn.execute('UPDATE ka.servers SET ssh_allowed=false WHERE id=%s', (UUID(proposal['servers'][0]['server_id']),))
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
    monkeypatch.setattr('knowledge_admin.agent_wizard.agent', run)
    before = client.get('/api/agents', headers=HEADERS).json()
    result = client.post('/api/agents/wizard/generate', headers=HEADERS,
                         json={'role':'Investigate payments without sudo.', 'model_id':proposal['model_id']})
    assert result.status_code == 200, result.text
    assert result.json()['proposal'] == proposal
    assert result.headers['cache-control'] == 'no-store'
    assert client.get('/api/agents', headers=HEADERS).json() == before
    config, task, kwargs = calls[0]
    assert all(config[capability]['enabled'] is False for capability in ('code','documents','ssh','mcp','skills','services'))
    assert task['catalog'] == catalog
    assert task['role'] == 'Investigate payments without sudo.'
    assert kwargs['output_type'] is AgentProposal
    assert task['output_schema'] == kwargs['output_type'].model_json_schema()
    assert kwargs['credentials'] == {proposal['model_id']:'test-model-secret'}
    for secret in (ssh['private_key'], 'test-model-secret', 'test-mcp-secret', 'test-git-secret'):
        assert secret not in json.dumps(config) + json.dumps(task)


def test_invalid_model_output_gets_one_correction_attempt(wizard, monkeypatch):
    client, _, proposal, _ = wizard
    calls = []
    async def run(config, task, **kwargs):
        calls.append(json.loads(task))
        output = {**proposal, 'system_prompt': ''} if len(calls) == 1 else proposal
        if len(calls)==1:
            raise ModelBehaviorError('Invalid model output')
        return SimpleNamespace(final_output=AgentProposal.model_validate(output))
    monkeypatch.setattr('knowledge_admin.agent_wizard.agent', run)
    result = client.post('/api/agents/wizard/generate', headers=HEADERS, json={'role':'Payments', 'model_id':proposal['model_id']})
    assert result.status_code == 200 and result.json()['proposal'] == proposal
    assert len(calls) == 2
    assert calls[1]['correction']['errors']
    assert calls[1]['role'] == calls[0]['role']
    assert calls[1]['output_schema'] == calls[0]['output_schema']


def test_model_annotations_cannot_add_unsupported_settings(wizard, monkeypatch):
    client, _, proposal, _ = wizard
    async def run(*args, **kwargs):
        return SimpleNamespace(final_output=AgentProposal.model_validate(proposal))
    monkeypatch.setattr('knowledge_admin.agent_wizard.agent', run)
    result = client.post('/api/agents/wizard/generate', headers=HEADERS, json={'role':'Payments', 'model_id':proposal['model_id']})
    assert result.status_code == 200 and result.json()['proposal'] == proposal
    assert client.post('/api/agents/wizard', headers=HEADERS, json={**proposal, 'unknown_field':True}).status_code == 400


@pytest.mark.parametrize('failure', ['exception','invalid','failed'])
def test_generation_failure_is_safe_and_leaves_no_agent(wizard, monkeypatch, failure):
    client, _, proposal, _ = wizard
    async def run(*args, **kwargs):
        if failure == 'exception':
            raise RuntimeError('Authorization: test-model-secret')
        raise ModelBehaviorError('Invalid model output')
    monkeypatch.setattr('knowledge_admin.agent_wizard.agent', run)
    before = client.get('/api/agents', headers=HEADERS).json()
    result = client.post('/api/agents/wizard/generate', headers=HEADERS, json={'role':'Payments', 'model_id':proposal['model_id']})
    assert result.status_code == 502 and 'test-model-secret' not in result.text
    assert client.get('/api/agents', headers=HEADERS).json() == before


BLANK = {'name': 'Blank agent', 'description': '', 'when_to_use': '', 'system_prompt': 'Answer carefully.',
         'instructions': '', 'model_id': None, 'enabled': True,
         'documents_enabled': False, 'repositories_enabled': False, 'ssh_enabled': False,
         'skills_enabled': False, 'mcp_enabled': False, 'services_enabled': False,
         'all_collections': False, 'all_repositories': False,
         'collection_ids': [], 'repository_ids': [], 'skill_ids': [], 'mcp_server_ids': [], 'service_ids': [], 'servers': []}


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
    assert client.post('/api/agents/wizard', headers=HEADERS, json={**BLANK, 'name': 'Other', 'system_prompt': ' '}).status_code == 400


def test_wizard_creates_claude_code_agents_with_claude_models_only(store, wizard):
    client, catalog, proposal, _ = wizard
    assert 'runtime' not in client.get('/api/agents/wizard/options', headers=HEADERS).json()['schema']['properties']
    created = client.post('/api/agents/wizard', headers=HEADERS, json={
        **proposal, 'name': 'Payments writer', 'runtime': 'claude_code', 'claude_code_tools': ['Bash', 'Read', 'Read']})
    assert created.status_code == 201, created.text
    config = get_agent_config(created.json()['id'], conninfo=store.conninfo)
    assert (config['runtime'], config['claude_code_tools'], config['system_prompt']['content'], config['instructions']) == (
        'claude_code', ['Read', 'Bash'], proposal['system_prompt'], proposal['instructions'])
    assert config['ssh']['enabled'] and config['skills']['items'] and config['code']['projects']  # saved like native
    gpt = client.post('/api/llm/models', headers=HEADERS, json={'name': 'GPT', 'provider': 'openai', 'model': 'gpt-5',
                      'api_format': 'responses', 'base_url': 'http://model.test/v1'}).json()['id']
    refused = client.post('/api/agents/wizard', headers=HEADERS, json={
        **proposal, 'name': 'GPT writer', 'model_id': gpt, 'runtime': 'claude_code'})
    assert refused.status_code == 400 and refused.json()['error']['message'] == 'Choose a Claude model for Claude Code agents'
    native = client.post('/api/agents/wizard', headers=HEADERS, json={**proposal, 'name': 'GPT helper', 'model_id': gpt})
    assert native.status_code == 201, native.text
    assert get_agent_config(native.json()['id'], conninfo=store.conninfo)['runtime'] == 'openai'
    for bad in ({'runtime': 'other'}, {'claude_code_tools': ['Edit']}, {'claude_code_tools': 'Read'}):
        assert client.post('/api/agents/wizard', headers=HEADERS, json={**proposal, 'name': 'Bad', **bad}).status_code == 400
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.agents WHERE name IN ('GPT writer','Bad')").fetchone()['n'] == 0


def test_proposal_rejects_all_and_specific_collections(wizard):
    _, _, proposal, _ = wizard
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match='not both'):
        AgentProposal.model_validate({**proposal, 'all_collections': True})


def test_inconsistent_model_output_gets_one_correction_attempt(wizard, monkeypatch):
    client, _, proposal, _ = wizard
    calls = []
    async def run(config, task, **kwargs):
        calls.append(json.loads(task))
        output = {**proposal, 'all_collections': True} if len(calls) == 1 else proposal
        return SimpleNamespace(final_output=AgentProposal.model_validate(output))
    monkeypatch.setattr('knowledge_admin.agent_wizard.agent', run)
    result = client.post('/api/agents/wizard/generate', headers=HEADERS, json={'role':'Payments', 'model_id':proposal['model_id']})
    assert result.status_code == 200 and result.json()['proposal'] == proposal
    assert len(calls) == 2
    assert any('not both' in e['message'] for e in calls[1]['correction']['errors'])


def test_claude_code_generation_offers_only_claude_models_and_clears_another(store, wizard, monkeypatch):
    client, catalog, proposal, _ = wizard
    with store.connect() as conn:
        actor = conn.execute('SELECT created_by FROM ka.llm_models LIMIT 1').fetchone()['created_by']
        gpt = str(conn.execute("""INSERT INTO ka.llm_models(name,provider,model,api_format,base_url,api_key_ciphertext,created_by,updated_by)
            VALUES ('GPT','openai','gpt-test','responses','https://api.openai.com/v1',%s,%s,%s) RETURNING id""", (b'\x00', actor, actor)).fetchone()['id'])
    tasks = []
    async def run(config, task, **kwargs):
        tasks.append(json.loads(task))
        return SimpleNamespace(final_output=AgentProposal.model_validate({**proposal, 'model_id': gpt}))
    monkeypatch.setattr('knowledge_admin.agent_wizard.agent', run)

    native = client.post('/api/agents/wizard/generate', headers=HEADERS,
                         json={'role': 'Payments', 'model_id': proposal['model_id']})
    assert native.status_code == 200, native.text
    assert gpt in {m['id'] for m in tasks[0]['catalog']['models']}
    assert native.json()['proposal']['model_id'] == gpt

    claude = client.post('/api/agents/wizard/generate', headers=HEADERS,
                         json={'role': 'Payments', 'model_id': proposal['model_id'], 'runtime': 'claude_code'})
    assert claude.status_code == 200, claude.text
    assert {m['id'] for m in tasks[1]['catalog']['models']} == {proposal['model_id']}
    assert tasks[1]['agent_type'] == 'claude_code'
    assert claude.json()['proposal']['model_id'] is None  # a non-Claude proposal is cleared, not refused

    assert client.post('/api/agents/wizard/generate', headers=HEADERS,
                       json={'role': 'Payments', 'model_id': proposal['model_id'], 'runtime': 'other'}).status_code in (400, 422)
