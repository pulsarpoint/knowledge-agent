"""Embeddings for any service: one call for a list of texts, and the profile that names which model made a vector
(a vector is only compared with vectors of the same profile). Errors are ModelUnavailable; callers decide what that
means (memory saves without a vector)."""

import hashlib
import json
import math

import httpx

from knowledge_llm import ModelUnavailable, RuntimeModel
from knowledge_llm.bedrock_embeddings import embed_bedrock

MAX_RESPONSE = 8_000_000
REQUEST_FAILED = 'Model request failed; check the saved endpoint, model, and credential'
INVALID = 'Embedding response has invalid dimensions, indexes, or values'


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def embedding_profile(model: RuntimeModel | None) -> str:
    if model is None:
        return ''
    if model.provider == 'bedrock':
        return _digest(
            {
                'provider': model.provider,
                'region': model.aws_region,
                'model': model.model,
                'version': model.embedding_version,
                'dimensions': model.embedding_dimensions,
                'input_format': 'text-v1',
                'normalize': True,
            }
        )
    return _digest(
        {
            'url': model.base_url.rstrip('/'),
            'model': model.model,
            'version': model.embedding_version,
            'dimensions': model.embedding_dimensions,
            'input_format': 'text-v1',
        }
    )


def _request(url: str, key: str | None, path: str, payload: dict, transport=None) -> dict:
    try:
        with httpx.Client(timeout=60, follow_redirects=False, transport=transport) as client:
            with client.stream(
                'POST', url.rstrip('/') + path, headers={'Authorization': f'Bearer {key}'} if key else {}, json=payload
            ) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE:
                        raise ValueError('oversized response')
        return json.loads(data)
    except (httpx.HTTPError, ValueError):
        raise ModelUnavailable(REQUEST_FAILED) from None


def embed(model: RuntimeModel, texts: list[str], transport=None) -> list[list[float]]:
    if model.provider == 'bedrock':
        return embed_bedrock(model, texts, transport)
    result = _request(model.base_url, model.api_key, '/embeddings', {'model': model.model, 'input': texts}, transport)
    try:
        rows = sorted(result['data'], key=lambda row: row['index'])
        if [row['index'] for row in rows] != list(range(len(texts))):
            raise ValueError('wrong indexes')
        vectors = [row['embedding'] for row in rows]
        for vector in vectors:
            if (
                len(vector) != model.embedding_dimensions
                or any(type(v) not in {int, float} or not math.isfinite(v) or abs(v) > 3.4e38 for v in vector)
                or not any(vector)
            ):
                raise ValueError('invalid vector')
        return vectors
    except (KeyError, TypeError, ValueError):
        raise ModelUnavailable(INVALID) from None
