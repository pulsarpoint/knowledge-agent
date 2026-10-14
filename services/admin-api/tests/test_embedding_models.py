"""Shared embedding connections keep credentials private and cannot power chat agents."""
import json

import httpx
import pytest
from openai import AsyncOpenAI
from knowledge_llm import decode_model
from knowledge_admin.llm import api as llm_api
from test_api import _client
from test_llm import HEADERS, OPENAI, create

EMBEDDING = {**OPENAI, 'name': 'Document embeddings', 'api_format': 'embeddings', 'embedding_dimensions': 3, 'embedding_version': '1'}


def test_embedding_credentials_edits_and_agent_assignments(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    model = create(client, EMBEDDING)
    assert model['embedding_dimensions'] == 3 and model['embedding_version'] == '1'
    assert model['has_api_key'] and 'api_key_ciphertext' not in model
    with store.connect() as conn:
        row = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s', (model['id'],)).fetchone()
        assert decode_model(row, store.cipher).api_key == EMBEDDING['api_key']
    assert EMBEDDING['api_key'] not in client.get('/api/llm', headers=HEADERS).text
    endpoint = '/api/llm/models/' + model['id']
    draft = {key: value for key, value in EMBEDDING.items() if key != 'api_key'}
    updated = client.put(endpoint, headers=HEADERS, json={**draft, 'revision': 1, 'embedding_version': '2'})
    assert updated.status_code == 200 and updated.json()['has_api_key']
    assert client.put(endpoint, headers=HEADERS, json={**draft, 'revision': 1}).status_code == 409
    assert client.put(endpoint, headers=HEADERS, json={**OPENAI, 'revision': 2}).status_code == 400
    for role in ('search', 'code', 'operations'):
        assert client.put('/api/llm/assignments', headers=HEADERS, json={'revision': 1, f'{role}_model_id': model['id']}).status_code == 400
    row = client.get('/api/agents/code', headers=HEADERS).json()
    assert client.put('/api/agents/code/model', headers=HEADERS, json={'revision': row['revision'], 'model_id': model['id']}).status_code == 400
    updated = client.put(endpoint, headers=HEADERS, json={**draft, 'revision': 2, 'api_key': None})
    assert updated.status_code == 200 and not updated.json()['has_api_key']


@pytest.mark.parametrize('patch', [
    {'embedding_dimensions': None}, {'embedding_dimensions': 0}, {'embedding_dimensions': 16001},
    {'embedding_dimensions': True}, {'embedding_version': ''}, {'provider': 'bedrock'},
    {'api_format': 'responses'},
])
def test_invalid_embedding_fields(store, tmp_path, patch):
    client = _client(tmp_path, repository_store=store)
    assert client.post('/api/llm/models', headers=HEADERS, json={**EMBEDDING, **patch}).status_code == 400


@pytest.mark.parametrize('dimension,ok', [(3, True), (2, False)])
def test_embedding_probe_uses_saved_endpoint_key_and_checks_dimensions(store, tmp_path, monkeypatch, dimension, ok):
    calls = []
    def embedding(request):
        calls.append(request)
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1.0] * dimension}], 'model': 'test-model', 'usage': {'prompt_tokens': 2, 'total_tokens': 2}})
    monkeypatch.setattr(llm_api, 'AsyncOpenAI', lambda **kwargs: AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(embedding))))
    client = _client(tmp_path, repository_store=store)
    model = create(client, EMBEDDING)
    response = client.post(f"/api/llm/models/{model['id']}/test", headers=HEADERS, json={'revision': 1})
    assert response.status_code == 200 and response.json()['ok'] is ok
    assert calls[0].url == 'https://model.test/v1/embeddings'
    assert calls[0].headers['authorization'] == 'Bearer ' + EMBEDDING['api_key']
    assert json.loads(calls[0].content)['model'] == EMBEDDING['model']
    assert EMBEDDING['api_key'] not in response.text


BEDROCK_EMBEDDING = {**EMBEDDING, 'provider': 'bedrock', 'base_url': None, 'aws_region': 'eu-central-1',
                     'model': 'amazon.titan-embed-text-v2:0', 'embedding_dimensions': 256}


@pytest.mark.parametrize('dimension,ok', [(256, True), (128, False)])
def test_bedrock_embedding_connection_probe(store, tmp_path, monkeypatch, dimension, ok):
    from knowledge_llm.bedrock_embeddings import embed_bedrock
    calls = []
    def invoke(request):
        calls.append(request)
        return httpx.Response(200, json={'embedding': [1.0] * dimension, 'inputTextTokenCount': 2})
    monkeypatch.setattr(llm_api, 'embed_bedrock', lambda model, texts: embed_bedrock(model, texts, httpx.MockTransport(invoke)))
    client = _client(tmp_path, repository_store=store)
    model = create(client, BEDROCK_EMBEDDING)
    result = client.post(f"/api/llm/models/{model['id']}/test", headers=HEADERS, json={'revision': 1})
    assert result.status_code == 200 and result.json()['ok'] is ok
    assert calls[0].url == 'https://bedrock-runtime.eu-central-1.amazonaws.com/model/amazon.titan-embed-text-v2:0/invoke'
    assert calls[0].headers['authorization'] == 'Bearer ' + EMBEDDING['api_key']
    assert json.loads(calls[0].content) == {'inputText': 'Connection test', 'dimensions': 256, 'normalize': True}
    with store.connect() as conn:
        row = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s', (model['id'],)).fetchone()
        assert decode_model(row, store.cipher).api_key == EMBEDDING['api_key']
    assert EMBEDDING['api_key'] not in result.text


@pytest.mark.parametrize('patch', [
    {'model': 'amazon.nova-test'}, {'model': 'eu.anthropic.claude-test'},
    {'embedding_dimensions': 1536}, {'aws_region': None}, {'api_key': None}, {'api_format': 'native'},
])
def test_bedrock_embedding_validation(store, tmp_path, patch):
    client = _client(tmp_path, repository_store=store)
    assert client.post('/api/llm/models', headers=HEADERS, json={**BEDROCK_EMBEDDING, **patch}).status_code == 400
