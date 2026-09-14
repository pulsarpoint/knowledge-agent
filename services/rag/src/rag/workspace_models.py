"""Bounded model calls with redacted failures and validated embedding responses."""

import json
import math

import httpx
from fastapi import HTTPException
from knowledge_llm import ModelUnavailable, RuntimeModel
from knowledge_llm.bedrock_embeddings import embed_bedrock

from rag.workspace import digest


def embedding_profile(model: RuntimeModel | None) -> str:
    if model is None:
        return ""
    if model.provider == 'bedrock':
        return digest({'provider': model.provider, 'region': model.aws_region, 'model': model.model,
                       'version': model.embedding_version, 'dimensions': model.embedding_dimensions,
                       'input_format': 'text-v1', 'normalize': True})
    return digest({"url": model.base_url.rstrip("/"), "model": model.model,
                   "version": model.embedding_version, "dimensions": model.embedding_dimensions,
                   "input_format": "text-v1"})


def model_request(url: str, key: str | None, path: str, payload: dict, transport=None):
    try:
        with httpx.Client(timeout=60, follow_redirects=False, transport=transport) as client:
            with client.stream("POST", url.rstrip("/") + path, headers={"Authorization": f"Bearer {key}"} if key else {}, json=payload) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 8_000_000:
                        raise ValueError("oversized response")
        return json.loads(data)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Model request failed; check the saved endpoint, model, and credential") from None


def embed(model: RuntimeModel, texts: list[str], transport=None) -> list[list[float]]:
    if model.provider == 'bedrock':
        try:
            return embed_bedrock(model, texts, transport)
        except ModelUnavailable as error:
            raise HTTPException(502, str(error)) from None
    result = model_request(model.base_url, model.api_key, "/embeddings",
                           {"model": model.model, "input": texts}, transport)
    try:
        rows = sorted(result["data"], key=lambda row: row["index"])
        if [row["index"] for row in rows] != list(range(len(texts))):
            raise ValueError("wrong indexes")
        vectors = [row["embedding"] for row in rows]
        for vector in vectors:
            if len(vector) != model.embedding_dimensions or any(type(v) not in {int, float} or not math.isfinite(v) or abs(v) > 3.4e38 for v in vector) or not any(vector):
                raise ValueError("invalid vector")
        return vectors
    except (KeyError, TypeError, ValueError):
        raise HTTPException(502, "Embedding response has invalid dimensions, indexes, or values") from None
