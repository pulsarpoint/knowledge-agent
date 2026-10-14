"""Database model assignments shared by the administration and agent services."""

import base64
import os
from dataclasses import dataclass, field
from uuid import UUID

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.rows import dict_row


class ModelUnavailable(Exception):
    pass


@dataclass(frozen=True)
class RuntimeModel:
    id: UUID
    name: str
    provider: str
    model: str
    api_format: str
    base_url: str | None
    aws_region: str | None
    api_key: str | None = field(repr=False)
    embedding_dimensions: int | None = None
    embedding_version: str | None = None


def managed() -> bool:
    return os.environ.get('LLM_SETTINGS_SOURCE') == 'database'


def credential_aad(model_id: UUID, api_format: str = 'responses') -> bytes:
    if api_format == 'embeddings':
        return f'{model_id}:embedding'.encode()
    return f'llm:{model_id}:v1'.encode()


def decode_model(row: dict, cipher: AESGCM) -> RuntimeModel:
    encrypted = row['api_key_ciphertext']
    key = None
    if encrypted is not None:
        encrypted = bytes(encrypted)
        key = cipher.decrypt(encrypted[:12], encrypted[12:], credential_aad(row['id'], row['api_format'])).decode()
    return RuntimeModel(
        **{name: row[name] for name in ('id', 'name', 'provider', 'model', 'api_format', 'base_url', 'aws_region')},
        api_key=key,
        embedding_dimensions=row.get('embedding_dimensions'),
        embedding_version=row.get('embedding_version'),
    )


def assigned_model(role: str) -> RuntimeModel:
    try:
        with psycopg.connect('', row_factory=dict_row, connect_timeout=5) as conn:
            row = conn.execute(
                """SELECT m.* FROM ka.agents a JOIN ka.llm_models m ON m.id=a.model_id
                WHERE a.id=%s AND a.enabled AND m.enabled""",
                (role,),
            ).fetchone()
        if row is None:
            raise ModelUnavailable('Select an enabled model in this agent’s settings.')
        cipher = AESGCM(base64.b64decode(os.environ['CREDENTIAL_ENCRYPTION_KEY'], altchars=b'-_', validate=True))
        return decode_model(row, cipher)
    except ModelUnavailable:
        raise
    except Exception:
        # Connection strings, ciphertext, and provider credentials must never reach callers.
        raise ModelUnavailable(
            'Model settings are unavailable. Check the administration service configuration.'
        ) from None


def code_compatible(provider: str, api_format: str, model: str) -> bool:
    if api_format == 'embeddings':
        return False
    return (
        provider == 'openai'
        and api_format == 'responses'
        or provider == 'anthropic'
        or provider == 'bedrock'
        and ('anthropic.claude' in model or 'anthropic/claude' in model)
    )


def completion_model(provider: str, model: str) -> str:
    """Claude bearer tokens use Bedrock Invoke, avoiding the Converse IAM path."""
    if provider == 'bedrock' and ('anthropic.claude' in model or 'anthropic/claude' in model):
        return f'bedrock/invoke/{model}'
    return f'{provider}/{model}'
