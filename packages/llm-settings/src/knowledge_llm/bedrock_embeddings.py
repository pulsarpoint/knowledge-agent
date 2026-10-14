"""Titan embeddings through the Bedrock InvokeModel API with bearer authentication."""

import json
import math

import httpx

from knowledge_llm import ModelUnavailable, RuntimeModel


def embed_bedrock(model: RuntimeModel, texts: list[str], transport=None) -> list[list[float]]:
    if (
        model.provider != 'bedrock'
        or model.api_format != 'embeddings'
        or model.model != 'amazon.titan-embed-text-v2:0'
        or model.embedding_dimensions not in (256, 512, 1024)
        or not model.aws_region
        or not model.api_key
    ):
        raise ModelUnavailable(
            'Configure Titan Text Embeddings V2 with a region, API key, and 256, 512, or 1024 dimensions'
        )
    url = f'https://bedrock-runtime.{model.aws_region}.amazonaws.com/model/{model.model}/invoke'
    vectors = []
    try:
        # Titan accepts one text per request. Reuse the HTTP connection for a batch.
        with httpx.Client(timeout=25, follow_redirects=False, transport=transport) as client:
            for text in texts:
                with client.stream(
                    'POST',
                    url,
                    headers={'Authorization': f'Bearer {model.api_key}'},
                    json={'inputText': text, 'dimensions': model.embedding_dimensions, 'normalize': True},
                ) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 1_000_000:
                            raise ValueError('Oversized response')
                vector = json.loads(data)['embedding']
                if (
                    not isinstance(vector, list)
                    or len(vector) != model.embedding_dimensions
                    or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 3.4e38 for v in vector)
                    or not any(vector)
                ):
                    raise ValueError('Invalid embedding dimensions or values')
                vectors.append(vector)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        # AWS responses and HTTP exceptions can contain request data or credentials.
        raise ModelUnavailable(
            'Bedrock embedding request failed; check the model, region, key, dimensions, and model access'
        ) from None
    return vectors
