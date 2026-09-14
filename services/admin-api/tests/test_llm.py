"""Model credentials, assignments, and probes against isolated PostgreSQL."""
import asyncio
import base64
import json
from uuid import UUID

import pytest
from cryptography.exceptions import InvalidTag
from knowledge_llm import ModelUnavailable, assigned_model, decode_model
from knowledge_admin import llm_api
from test_api import _client

HEADERS = {'X-Forwarded-Groups': 'docs-admins', 'X-Forwarded-User': 'admin', 'X-Forwarded-Email': 'admin@example.test'}
OPENAI = {'name': 'Search', 'provider': 'openai', 'model': 'test-model', 'api_format': 'responses', 'base_url': 'https://model.test/v1', 'api_key': 'secret-test-key'}
BEDROCK = {'name': 'Bedrock', 'provider': 'bedrock', 'model': 'eu.anthropic.claude-test-v1:0', 'api_format': 'native', 'aws_region': 'eu-central-1', 'api_key': 'bedrock-test-key'}


def create(client, value=OPENAI):
    response = client.post('/api/llm/models', headers=HEADERS, json=value)
    assert response.status_code == 201, response.text
    return response.json()


def use_database(monkeypatch, store):
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_KEY', base64.b64encode(b'k' * 32).decode())
    monkeypatch.setenv('LLM_SETTINGS_SOURCE', 'database')
    # Preserve the production connection/decryption/query path with an isolated DB.
    import knowledge_llm
    real_connect = knowledge_llm.psycopg.connect
    monkeypatch.setattr(knowledge_llm.psycopg, 'connect', lambda conninfo='', **kwargs: real_connect(conninfo or store.conninfo, **kwargs))


def test_encrypted_credentials_preserved_rotated_and_bound_to_model(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    first = create(client)
    second = create(client, {**OPENAI, 'name': 'Other'})
    assert first['has_api_key'] and 'api_key' not in first and 'api_key_ciphertext' not in first
    with store.connect() as conn:
        a = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s', (first['id'],)).fetchone()
        b = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s', (second['id'],)).fetchone()
        assert a['api_key_ciphertext'] != b['api_key_ciphertext']
        assert OPENAI['api_key'].encode() not in a['api_key_ciphertext']
        assert decode_model(a, store.cipher).api_key == OPENAI['api_key']
        assert OPENAI['api_key'] not in repr(decode_model(a, store.cipher))
        with pytest.raises(InvalidTag):
            decode_model({**a, 'id': UUID(second['id'])}, store.cipher)
    endpoint = '/api/llm/models/' + first['id']
    value = {key: value for key, value in OPENAI.items() if key != 'api_key'}
    assert client.put(endpoint, headers=HEADERS, json={**value, 'revision': 1, 'name': 'Renamed'}).status_code == 200
    assert client.put(endpoint, headers=HEADERS, json={**value, 'revision': 1}).status_code == 409
    changed = {**value, 'revision': 2, 'base_url': 'https://new.test/v1'}
    assert client.put(endpoint, headers=HEADERS, json=changed).status_code == 400
    removed = client.put(endpoint, headers=HEADERS, json={**changed, 'api_key': None})
    assert removed.status_code == 200 and not removed.json()['has_api_key']
    with store.connect() as conn:
        events = conn.execute('SELECT details FROM ka.audit_events').fetchall()
    assert OPENAI['api_key'] not in json.dumps(events)
    assert OPENAI['api_key'] not in client.get('/api/llm', headers=HEADERS).text


def test_assignments_live_changes_disable_and_no_env_fallback(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    openai = create(client)
    bedrock = create(client, BEDROCK)
    selection = {'search_model_id': openai['id'], 'code_model_id': bedrock['id'], 'revision': 1}
    assert client.put('/api/llm/assignments', headers=HEADERS, json=selection).status_code == 200
    assert client.put('/api/llm/assignments', headers=HEADERS, json=selection).status_code == 409
    use_database(monkeypatch, store)
    assert assigned_model('code').api_key == BEDROCK['api_key']
    assert assigned_model('search').model == 'test-model'
    assert client.put('/api/llm/models/' + openai['id'], headers=HEADERS, json={**OPENAI, 'revision': 1, 'model': 'updated-model'}).status_code == 200
    assert assigned_model('search').model == 'updated-model'
    client.put('/api/llm/models/' + openai['id'], headers=HEADERS, json={**OPENAI, 'revision': 2, 'enabled': False})
    monkeypatch.setenv('DOCS_WIKI_CHAT_MODEL', 'must-not-fallback')
    with pytest.raises(ModelUnavailable, match='enabled model'):
        assigned_model('search')
    # The live chat status reads the same saved settings, not the old env configuration.
    status = client.get('/api/agents/search/status', headers=HEADERS).json()
    assert status['ask_enabled'] and status['ask_reason'] is None
    assert client.put('/api/llm/assignments', headers=HEADERS, json={**selection, 'revision': 2}).status_code == 400
    incompatible = {**BEDROCK, 'model': 'amazon.nova-test', 'revision': 1}
    assert client.put('/api/llm/models/' + bedrock['id'], headers=HEADERS, json=incompatible).status_code == 400


def test_individual_agent_assignment_preserves_other_agents_and_rejects_stale_writes(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    openai = create(client)
    bedrock = create(client, BEDROCK)
    path = '/api/llm/assignments'
    initial = {f'{role}_model_id': bedrock['id'] for role in ('search', 'code', 'operations')}
    assert client.put(path, headers=HEADERS, json={**initial, 'revision': 1}).status_code == 200
    assert client.put('/api/llm/models/' + bedrock['id'], headers=HEADERS, json={**BEDROCK, 'revision': 1, 'enabled': False}).status_code == 200

    change = {'search_model_id': openai['id'], 'revision': 2}
    assert client.put(path, json=change).status_code == 403
    response = client.put(path, headers=HEADERS, json=change)
    assert response.status_code == 200
    assert response.json().items() >= {**initial, 'search_model_id': openai['id'], 'revision': 3}.items()
    assert client.put(path, headers=HEADERS, json={'operations_model_id': openai['id'], 'revision': 2}).status_code == 409
    response = client.put(path, headers=HEADERS, json={'code_model_id': None, 'revision': 3})
    assert response.status_code == 200
    assert response.json().items() >= {'search_model_id': openai['id'], 'code_model_id': None, 'operations_model_id': bedrock['id'], 'revision': 4}.items()
    assert client.put(path, headers=HEADERS, json={'revision': 4}).status_code == 400
    assert client.put(path, headers=HEADERS, json={'code_model_id': bedrock['id'], 'revision': 4}).status_code == 400
    chat = create(client, {**OPENAI, 'name': 'Chat only', 'api_format': 'chat_completions'})
    assert client.put(path, headers=HEADERS, json={'operations_model_id': chat['id'], 'revision': 4}).status_code == 400
    with store.connect() as conn:
        details = conn.execute("SELECT details FROM ka.audit_events WHERE action='llm.assignments.updated' AND details->>'revision'='2'").fetchone()['details']
    assert details == change


@pytest.mark.parametrize('patch', [
    {'base_url': 'https://user:password@model.test/v1'}, {'base_url': 'https://model.test/v1?api_key=secret'},
    {'base_url': 'file:///tmp/test'}, {'provider': 'bedrock'}, {'api_format': 'native'},
    {'api_key': 'bad key'}, {'enabled': 'false'}, {'name': '\x00'}, {'revision': 0},
])
def test_invalid_settings_rejected(store, tmp_path, patch):
    client = _client(tmp_path, repository_store=store)
    assert client.post('/api/llm/models', headers=HEADERS, json={**OPENAI, **patch}).status_code == 400
    assert client.get('/api/llm', headers=HEADERS).json()['models'] == []


def test_admin_only_and_probe_does_not_expose_provider_errors(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    calls = []
    async def probe(model):
        calls.append(model)
    monkeypatch.setattr(llm_api, 'probe_model', probe)
    row = create(client, BEDROCK)
    path = '/api/llm/models/' + row['id'] + '/test'
    assert calls == []
    assert client.get('/api/llm').status_code == 403
    assert client.post(path, json={'revision': 1}).status_code == 403
    response = client.post(path, headers=HEADERS, json={'revision': 1})
    assert response.json()['ok'] and calls[0].api_key == BEDROCK['api_key']
    assert client.post(path, headers=HEADERS, json={'revision': 2}).status_code == 409
    async def fail(model):
        raise RuntimeError('provider echoed ' + model.api_key)
    monkeypatch.setattr(llm_api, 'probe_model', fail)
    response = client.post(path, headers=HEADERS, json={'revision': 1})
    assert not response.json()['ok'] and BEDROCK['api_key'] not in response.text


def test_native_provider_probe_uses_saved_bedrock_region_and_bearer(store, tmp_path, monkeypatch):
    client = _client(tmp_path, repository_store=store)
    row = create(client, BEDROCK)
    import litellm
    calls = []
    async def completion(**kwargs):
        calls.append(kwargs)
    monkeypatch.setattr(litellm, 'acompletion', completion)
    response = client.post('/api/llm/models/' + row['id'] + '/test', headers=HEADERS, json={'revision': 1})
    assert response.json()['ok']
    assert calls[0]['model'] == 'bedrock/invoke/' + BEDROCK['model']
    assert calls[0]['api_key'] == BEDROCK['api_key'] and calls[0]['aws_region_name'] == BEDROCK['aws_region']
    assert calls[0]['max_tokens'] == 32 and calls[0]['num_retries'] == 0
