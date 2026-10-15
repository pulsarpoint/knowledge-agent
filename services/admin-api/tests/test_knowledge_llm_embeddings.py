"""knowledge_llm.embeddings: the embedding call and profile shared by RAG and memory."""

import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from knowledge_llm import ModelUnavailable, RuntimeModel
from knowledge_llm.embeddings import embed, embedding_profile


def model(**values) -> RuntimeModel:
    row = {
        'id': uuid4(),
        'name': 'Embed',
        'provider': 'openai',
        'model': 'text-embedding-3-small',
        'api_format': 'embeddings',
        'base_url': 'https://models.internal/v1/',
        'aws_region': None,
        'api_key': 'secret',
        'embedding_dimensions': 3,
        'embedding_version': '1',
        **values,
    }
    return RuntimeModel(**row)


def test_profile_is_the_digest_rag_always_used():
    expected = {
        'url': 'https://models.internal/v1',
        'model': 'text-embedding-3-small',
        'version': '1',
        'dimensions': 3,
        'input_format': 'text-v1',
    }
    text = json.dumps(expected, sort_keys=True, separators=(',', ':'))
    assert embedding_profile(model()) == hashlib.sha256(text.encode()).hexdigest()
    assert embedding_profile(None) == ''


def test_embed_posts_the_texts_and_returns_vectors_in_order():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers['Authorization'], body=json.loads(request.content))
        return httpx.Response(
            200, json={'data': [{'index': 1, 'embedding': [0, 1, 0]}, {'index': 0, 'embedding': [1, 0, 0]}]}
        )

    assert embed(model(), ['a', 'b'], httpx.MockTransport(handler)) == [[1, 0, 0], [0, 1, 0]]
    assert seen == {
        'url': 'https://models.internal/v1/embeddings',
        'auth': 'Bearer secret',
        'body': {'model': 'text-embedding-3-small', 'input': ['a', 'b']},
    }


@pytest.mark.parametrize(
    'response',
    [
        httpx.Response(500),
        httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1, 0]}]}),  # wrong dimensions
        httpx.Response(200, json={'data': [{'index': 0, 'embedding': [0, 0, 0]}]}),  # all zero
        httpx.Response(200, content=b'not json'),
    ],
)
def test_failures_raise_model_unavailable(response):
    with pytest.raises(ModelUnavailable):
        embed(model(), ['a'], httpx.MockTransport(lambda request: response))
