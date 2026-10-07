"""Claude Code agents are created and configured exactly like native agents; only the runtime differs."""
from uuid import uuid4

import pytest

from knowledge_admin.claude_code import SYSTEM_PROMPT
from knowledge_admin.knowledge_agents.config import CHOOSE_CLAUDE
from test_api import _client
from test_profiles import create, update, wizard_payload
from test_repositories import HEADERS

ALL_TOOLS = ['Read', 'Grep', 'Glob', 'Bash']


def claude_code_agent(client, name: str = 'Technical writer', **change) -> str:
    """Create a Claude Code agent as New agent → Agent type: Claude Code does."""
    body = wizard_payload(name, description='', when_to_use='', system_prompt=SYSTEM_PROMPT, runtime='claude_code')
    response = client.post('/api/agents/wizard', headers=HEADERS, json={**body, **change})
    assert response.status_code == 201, response.text
    return response.json()['id']


def models(client) -> tuple[str, str]:
    claude = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'Claude', 'provider': 'bedrock', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
        'api_format': 'native', 'aws_region': 'eu-central-1', 'api_key': 'bedrock-secret-token'}).json()['id']
    gpt = client.post('/api/llm/models', headers=HEADERS, json={
        'name': 'GPT', 'provider': 'openai', 'model': 'gpt-5', 'api_format': 'responses',
        'base_url': 'http://model.test/v1'}).json()['id']
    return claude, gpt


@pytest.fixture
def writer(store, tmp_path, monkeypatch):
    for variable in ('CODE_ROOT', 'DATA_ROOT_DIR'):  # the agent config resolves both mounted roots
        monkeypatch.setenv(variable, str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    return client, claude_code_agent(client)


def test_claude_code_agent_is_listed_with_its_type_prompt_and_tools(writer):
    client, agent_id = writer
    listed = next(a for a in client.get('/api/agents', headers=HEADERS).json()['agents'] if a['id'] == agent_id)
    assert (listed['runtime'], listed['claude_code_tools'], listed['prompt_version']) == ('claude_code', ALL_TOOLS, 1)
    config = client.get(f'/api/agents/{agent_id}/config', headers=HEADERS)
    assert config.status_code == 200 and config.json()['system_prompt']['content'] == SYSTEM_PROMPT
    # One way to create and edit agents: the registration routes are gone.
    assert client.post('/api/claude-code/agents', headers=HEADERS, json={'name': 'Other'}).status_code in (404, 405)
    assert client.put(f'/api/claude-code/agents/{agent_id}', headers=HEADERS, json={'name': 'Other'}).status_code in (404, 405)


def test_claude_code_agents_are_configured_like_native_agents(store, writer):
    from test_operations import setup
    client, agent_id = writer
    server, _ = setup(client)
    claude, _ = models(client)
    profile = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    saved = client.put(f'/api/agents/{agent_id}/model', headers=HEADERS, json={'revision': profile['revision'], 'model_id': claude})
    assert saved.status_code == 200, saved.text
    saved = update(client, saved.json(), ssh_enabled=True, skills_enabled=True, mcp_enabled=True,
                   claude_code_tools=['Bash', 'Read', 'Read'])
    assert saved.status_code == 200, saved.text
    assert (saved.json()['claude_code_tools'], saved.json()['ssh_enabled']) == (['Read', 'Bash'], True)
    settings = client.get(f'/api/operations/settings?agent_id={agent_id}', headers=HEADERS)
    assert settings.status_code == 200, settings.text
    put = client.put(f'/api/operations/settings?agent_id={agent_id}', headers=HEADERS, json={
        'revision': settings.json()['settings']['revision'], 'instructions': 'Write for operators.', 'server_ids': [server['id']], 'skill_ids': []})
    assert put.status_code == 200, put.text
    for path in (f"/api/agents/{agent_id}/servers/{server['id']}/ssh", f'/api/mcp/code?agent_id={agent_id}'):
        assert client.get(path, headers=HEADERS).status_code == 200, path
    source = client.get(f'/api/agents/{agent_id}/versions/source', headers=HEADERS)
    assert source.status_code == 200, source.text
    config = source.json()['config']
    assert (config['runtime'], config['claude_code_tools'], config['instructions']) == ('claude_code', ['Read', 'Bash'], 'Write for operators.')
    assert [s['id'] for s in config['ssh']['servers']] == [server['id']]
    draft = client.post(f'/api/agents/{agent_id}/versions', headers=HEADERS, json={
        'label': 'First', 'source_hash': source.json()['source_hash'],
        'changes': {'system_prompt': None, 'instructions': None, 'server_instructions': []}})
    assert draft.status_code == 201, draft.text


def test_switching_the_type_keeps_the_config_and_needs_a_claude_model(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    claude, gpt = models(client)
    native = create(client, 'Payments helper')
    with_gpt = client.put(f"/api/agents/{native['id']}/model", headers=HEADERS,
                          json={'revision': native['revision'], 'model_id': gpt}).json()
    refused = update(client, with_gpt, runtime='claude_code')
    assert refused.status_code == 400 and refused.json()['error']['message'] == CHOOSE_CLAUDE
    with_claude = client.put(f"/api/agents/{native['id']}/model", headers=HEADERS,
                             json={'revision': with_gpt['revision'], 'model_id': claude}).json()
    switched = update(client, with_claude, runtime='claude_code', description='Same agent, other runtime')
    assert switched.status_code == 200, switched.text
    assert (switched.json()['runtime'], switched.json()['description'], switched.json()['active_prompt']['title']) == (
        'claude_code', 'Same agent, other runtime', native['active_prompt']['title'])
    model = client.put(f"/api/agents/{native['id']}/model", headers=HEADERS,
                       json={'revision': switched.json()['revision'], 'model_id': gpt})
    assert model.status_code == 400 and model.json()['error']['message'] == CHOOSE_CLAUDE
    back = update(client, switched.json(), runtime='openai')
    assert back.status_code == 200 and (back.json()['runtime'], back.json()['model_id']) == ('openai', claude)
    coordinator = client.get('/api/agents/coordinator', headers=HEADERS).json()
    refused = update(client, coordinator, runtime='claude_code')
    assert refused.status_code == 400 and 'native runtime' in refused.json()['error']['message']
    for bad in ({'runtime': 'other'}, {'claude_code_tools': ['Edit']}):
        assert update(client, back.json(), **bad).status_code == 400, bad


def test_claude_code_agent_with_retained_sessions_cannot_be_deleted(store, writer):
    client, agent_id = writer
    _, gpt = models(client)
    with store.connect() as conn:
        conn.execute('''INSERT INTO ka.agent_sessions_cc(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,document_name,folder) SELECT %s,p.id,%s,(SELECT id FROM ka.principals LIMIT 1),%s,'Payments',
            '{}','{}','Payments','payments' FROM ka.projects p WHERE p.archived_at IS NULL''', (uuid4(), agent_id, gpt))
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    deleted = client.request('DELETE', f'/api/agents/{agent_id}', headers=HEADERS,
                             json={'revision': detail['revision'], 'confirmed': True})
    assert deleted.status_code == 409 and 'retained sessions' in deleted.json()['error']['message']
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions_cc WHERE agent_id=%s', (agent_id,)).fetchone()['n'] == 1


def test_claude_code_agent_without_sessions_is_deleted_with_its_prompt(store, writer):
    client, agent_id = writer
    detail = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    deleted = client.request('DELETE', f'/api/agents/{agent_id}', headers=HEADERS,
                             json={'revision': detail['revision'], 'confirmed': True})
    assert deleted.status_code == 204, deleted.text
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_prompt_versions WHERE agent=%s', (agent_id,)).fetchone()['n'] == 0


def test_the_version_model_must_fit_the_agent_type(store, tmp_path, monkeypatch):
    for variable in ('CODE_ROOT', 'DATA_ROOT_DIR'):
        monkeypatch.setenv(variable, str(tmp_path.resolve()))
    client = _client(tmp_path, repository_store=store)
    claude, gpt = models(client)
    native = create(client, 'Versioned helper')
    path = f"/api/agents/{native['id']}/versions"

    def set_model(model_id):
        profile = client.get(f"/api/agents/{native['id']}", headers=HEADERS).json()
        saved = client.put(f"/api/agents/{native['id']}/model", headers=HEADERS,
                           json={'revision': profile['revision'], 'model_id': model_id})
        assert saved.status_code == 200, saved.text
        return saved.json()

    def draft(label):
        source = client.get(path + '/source', headers=HEADERS).json()
        created = client.post(path, headers=HEADERS, json={'label': label, 'source_hash': source['source_hash'],
            'changes': {'system_prompt': None, 'instructions': None, 'server_instructions': []}})
        assert created.status_code == 201, created.text
        return created.json()['id']

    def activate(version_id):
        active = client.get(path, headers=HEADERS).json()['active_id']
        return client.post(f'{path}/{version_id}/activate', headers=HEADERS, json={'expected_active_id': active})

    set_model(gpt)
    draft('GPT draft')  # the initial configuration (GPT) becomes the active version
    gpt_version = client.get(path, headers=HEADERS).json()['active_id']
    profile = set_model(claude)
    # The working model is Claude, but the active version still runs GPT.
    refused = update(client, profile, runtime='claude_code')
    assert refused.status_code == 400 and refused.json()['error']['message'] == CHOOSE_CLAUDE
    claude_version = draft('Claude draft')
    assert activate(claude_version).status_code == 200
    switched = update(client, client.get(f"/api/agents/{native['id']}", headers=HEADERS).json(), runtime='claude_code')
    assert switched.status_code == 200, switched.text
    refused = activate(gpt_version)
    assert refused.status_code == 400 and refused.json()['error']['message'] == CHOOSE_CLAUDE
    assert client.get(path, headers=HEADERS).json()['active_id'] == claude_version


def test_a_model_used_by_a_claude_code_agent_stays_a_claude_model(store, writer):
    client, agent_id = writer
    claude, _ = models(client)
    profile = client.get(f'/api/agents/{agent_id}', headers=HEADERS).json()
    saved = client.put(f'/api/agents/{agent_id}/model', headers=HEADERS, json={'revision': profile['revision'], 'model_id': claude})
    assert saved.status_code == 200, saved.text
    with store.connect() as conn:
        revision = conn.execute('SELECT revision FROM ka.llm_models WHERE id=%s', (claude,)).fetchone()['revision']
    gpt = {'name': 'Claude', 'provider': 'openai', 'model': 'gpt-5', 'api_format': 'responses',
           'base_url': 'http://model.test/v1', 'api_key': 'openai-secret', 'revision': revision}
    refused = client.put(f'/api/llm/models/{claude}', headers=HEADERS, json=gpt)
    assert refused.status_code == 400 and refused.json()['error']['message'] == CHOOSE_CLAUDE
    renamed = client.put(f'/api/llm/models/{claude}', headers=HEADERS, json={
        'name': 'Claude Sonnet', 'provider': 'bedrock', 'model': 'eu.anthropic.claude-sonnet-4-5-20250929-v1:0',
        'api_format': 'native', 'aws_region': 'eu-central-1', 'revision': revision})
    assert renamed.status_code == 200, renamed.text
