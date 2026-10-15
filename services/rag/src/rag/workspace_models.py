"""Bounded model calls with redacted failures and validated embedding responses."""

from fastapi import HTTPException
from knowledge_llm import ModelUnavailable, RuntimeModel, embeddings

embedding_profile = embeddings.embedding_profile


def embed(model: RuntimeModel, texts: list[str], transport=None) -> list[list[float]]:
    try:
        return embeddings.embed(model, texts, transport)
    except ModelUnavailable as error:
        raise HTTPException(502, str(error)) from None
