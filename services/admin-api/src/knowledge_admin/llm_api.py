"""Named model connections and agent assignments. Credentials are write-only."""
import asyncio
import math
import os
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from knowledge_llm import code_compatible, completion_model, credential_aad, decode_model
from knowledge_llm.bedrock_embeddings import embed_bedrock
from openai import AsyncOpenAI
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.repositories import RepositoryStore

PUBLIC = 'id,name,provider,model,api_format,base_url,aws_region,embedding_dimensions,embedding_version,enabled,revision,created_at,updated_at,(api_key_ciphertext IS NOT NULL) AS has_api_key'


class ModelInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    provider: Literal['openai', 'anthropic', 'bedrock']
    model: str = Field(min_length=1, max_length=500)
    api_format: Literal['chat_completions', 'responses', 'native', 'embeddings']
    embedding_dimensions: int | None = Field(default=None, ge=1, le=16000, strict=True)
    embedding_version: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2000)
    aws_region: str | None = Field(default=None, pattern=r'^[a-z]{2}(?:-[a-z]+)+-\d+$', max_length=60)
    api_key: SecretStr | None = None
    enabled: bool = Field(default=True, strict=True)
    revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator('name', 'model', 'base_url', 'embedding_version')
    @classmethod
    def no_controls(cls, value: str | None) -> str | None:
        if value is not None and any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Control characters are not allowed')
        return value

    @field_validator('api_key')
    @classmethod
    def valid_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            key = value.get_secret_value()
            if not 1 <= len(key) <= 8192 or any(c.isspace() or ord(c) < 33 for c in key):
                raise ValueError('Enter a key without whitespace')
        return value

    @model_validator(mode='after')
    def provider_fields(self):
        if self.api_format == 'embeddings':
            if self.provider not in ('openai', 'bedrock') or self.embedding_dimensions is None or self.embedding_version is None:
                raise ValueError('Embeddings require OpenAI compatible or Bedrock, dimensions and model version')
            if self.provider == 'bedrock' and (self.model != 'amazon.titan-embed-text-v2:0' or self.embedding_dimensions not in (256, 512, 1024)):
                raise ValueError('Bedrock embeddings support amazon.titan-embed-text-v2:0 with 256, 512, or 1024 dimensions')
        elif self.embedding_dimensions is not None or self.embedding_version is not None:
            raise ValueError('Embedding dimensions and version apply only to embedding models')
        if self.provider == 'bedrock':
            if self.api_format not in ('native', 'embeddings') or self.base_url is not None or self.aws_region is None:
                raise ValueError('Bedrock requires a region and native or embeddings API format, without a base URL')
        else:
            expected = ('chat_completions', 'responses', 'embeddings') if self.provider == 'openai' else ('native',)
            if self.api_format not in expected or self.aws_region is not None or self.base_url is None:
                raise ValueError('Choose the provider API format and enter a base URL, without an AWS region')
            parsed = urlsplit(self.base_url)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment):
                raise ValueError('Use an HTTP(S) base URL without credentials, query, or fragment')
            self.base_url = self.base_url.rstrip('/')
        return self


class AssignmentsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    search_model_id: UUID | None = None
    code_model_id: UUID | None = None
    operations_model_id: UUID | None = None
    revision: int = Field(ge=1, strict=True)


class TestInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


async def probe_model(model) -> None:
    """A bounded inference request verifies model access, not just account credentials."""
    if model.provider == 'bedrock' and model.api_format == 'embeddings':
        await asyncio.to_thread(embed_bedrock, model, ['Connection test'])
    elif model.provider == 'openai':
        async with AsyncOpenAI(base_url=model.base_url, api_key=model.api_key or 'unused', timeout=25, max_retries=0) as client:
            if model.api_format == 'embeddings':
                result = await client.embeddings.create(model=model.model, input=['Connection test'], encoding_format='float')
                if len(result.data) != 1 or result.data[0].index != 0:
                    raise ValueError('Invalid embedding response')
                vector = result.data[0].embedding
                if len(vector) != model.embedding_dimensions or not any(vector) or any(not math.isfinite(v) or abs(v) > 3.4e38 for v in vector):
                    raise ValueError('Embedding dimensions or values do not match the connection')
            elif model.api_format == 'responses':
                await client.responses.create(model=model.model, input='Reply with OK.', max_output_tokens=32)
            else:
                await client.chat.completions.create(model=model.model, messages=[{'role': 'user', 'content': 'Reply with OK.'}], max_completion_tokens=32)
    else:
        from litellm import acompletion
        await acompletion(model=completion_model(model.provider, model.model), api_key=model.api_key,
            base_url=model.base_url, messages=[{'role': 'user', 'content': 'Reply with OK.'}],
            max_tokens=32, timeout=25, num_retries=0,
            **({'aws_region_name': model.aws_region} if model.provider == 'bedrock' else {}))


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/llm')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Model storage is not configured')
        return store

    def audit(conn, db, request, action, resource_id, details):
        subject, email = request_actor(request)
        actor = db.actor(conn, subject, email)
        conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,'llm_model',%s,%s)''',
            (db.project(conn, app_name), actor, action, resource_id, Jsonb(details)))
        return actor

    @router.get('')
    def settings():
        with database().connect() as conn:
            return {'models': conn.execute(f'SELECT {PUBLIC} FROM ka.llm_models ORDER BY name,id').fetchall(),
                    'assignments': conn.execute('SELECT search_model_id,code_model_id,operations_model_id,revision,updated_at FROM ka.llm_assignments').fetchone()}

    @router.put('/assignments')
    def assignments(value: AssignmentsInput, request: Request):
        changes = value.model_dump(exclude_unset=True, exclude={'revision'})
        if not changes:
            raise HTTPException(400, 'Choose an agent model to update.')
        db = database()
        with db.connect() as conn:
            conn.execute('SELECT singleton FROM ka.llm_assignment_revision FOR UPDATE')
            current = conn.execute('SELECT * FROM ka.llm_assignments').fetchone()
            if current['revision'] != value.revision:
                raise HTTPException(409, 'Assignments changed. Refresh before saving.')
            for field, model_id in changes.items():
                if model_id is None:
                    continue
                model = conn.execute('SELECT provider,api_format,model,enabled FROM ka.llm_models WHERE id=%s', (model_id,)).fetchone()
                if model is None or not model['enabled'] or model['api_format'] == 'embeddings':
                    raise HTTPException(400, 'Choose an enabled model')
                if field != 'search_model_id' and not code_compatible(model['provider'], model['api_format'], model['model']):
                    raise HTTPException(400, 'These agents require OpenAI Responses or Claude through Anthropic / Bedrock')
            actor = audit(conn, db, request, 'llm.assignments.updated', None, value.model_dump(mode='json', exclude_unset=True))
            current.update(changes)
            return conn.execute('''UPDATE ka.llm_assignments SET search_model_id=%s,code_model_id=%s,operations_model_id=%s,revision=revision+1,updated_by=%s
                RETURNING search_model_id,code_model_id,operations_model_id,revision,updated_at''', (current['search_model_id'], current['code_model_id'], current['operations_model_id'], actor)).fetchone()

    def save(value: ModelInput, request: Request, model_id: UUID | None):
        db = database()
        creating = model_id is None
        model_id = model_id or uuid4()
        try:
            with db.connect() as conn:
                # Serialize assignment and connection edits so compatibility validation cannot race.
                conn.execute('SELECT singleton FROM ka.llm_assignment_revision FOR UPDATE')
                old = None if creating else conn.execute('SELECT * FROM ka.llm_models WHERE id=%s FOR UPDATE', (model_id,)).fetchone()
                if not creating and old is None:
                    raise HTTPException(404, 'Model not found')
                if (creating and value.revision is not None) or (old is not None and old['revision'] != value.revision):
                    raise HTTPException(409, 'This model changed. Cancel and reopen it before saving.')
                if old is not None and (old['api_format'] == 'embeddings') != (value.api_format == 'embeddings'):
                    raise HTTPException(400, 'Create a separate connection when changing between chat and embeddings.')
                encrypted = old['api_key_ciphertext'] if old is not None else None
                if old is not None and encrypted is not None and 'api_key' not in value.model_fields_set:
                    if any(old[key] != getattr(value, key) for key in ('provider', 'base_url', 'aws_region')):
                        raise HTTPException(400, 'Replace or remove the saved key when changing its provider or endpoint.')
                if 'api_key' in value.model_fields_set:
                    nonce = os.urandom(12)
                    encrypted = None if value.api_key is None else nonce + db.cipher.encrypt(nonce, value.api_key.get_secret_value().encode(), credential_aad(model_id, value.api_format))
                if value.provider != 'openai' and encrypted is None:
                    raise HTTPException(400, 'An API key is required for this provider')
                if conn.execute("SELECT 1 FROM ka.agents WHERE model_id=%s LIMIT 1", (model_id,)).fetchone() and not code_compatible(value.provider, value.api_format, value.model):
                    raise HTTPException(400, 'Choose a compatible code model or remove its agent assignment first.')
                actor = audit(conn, db, request, 'llm.created' if creating else 'llm.updated', model_id,
                              {'name': value.name, 'provider': value.provider, 'enabled': value.enabled})
                values = (value.name, value.provider, value.model, value.api_format, value.base_url, value.aws_region,
                          value.embedding_dimensions, value.embedding_version, encrypted, value.enabled, actor)
                if creating:
                    row = conn.execute(f'''INSERT INTO ka.llm_models(name,provider,model,api_format,base_url,aws_region,embedding_dimensions,embedding_version,api_key_ciphertext,enabled,updated_by,created_by,id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {PUBLIC}''', (*values, actor, model_id)).fetchone()
                else:
                    row = conn.execute(f'''UPDATE ka.llm_models SET name=%s,provider=%s,model=%s,api_format=%s,base_url=%s,aws_region=%s,embedding_dimensions=%s,embedding_version=%s,api_key_ciphertext=%s,enabled=%s,updated_by=%s,revision=revision+1
                        WHERE id=%s RETURNING {PUBLIC}''', (*values, model_id)).fetchone()
                return row
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, 'A model with this name already exists') from None

    @router.post('/models', status_code=201)
    def create(value: ModelInput, request: Request):
        return save(value, request, None)

    @router.put('/models/{model_id}')
    def update(model_id: UUID, value: ModelInput, request: Request):
        return save(value, request, model_id)

    @router.post('/models/{model_id}/test')
    async def test(model_id: UUID, value: TestInput, request: Request):
        db = database()
        with db.connect() as conn:
            row = conn.execute('SELECT * FROM ka.llm_models WHERE id=%s', (model_id,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Model not found')
            if row['revision'] != value.revision:
                raise HTTPException(409, 'This model changed. Refresh before testing.')
            if not row['enabled']:
                raise HTTPException(400, 'Enable the model before testing')
            try:
                model = decode_model(row, db.cipher)
            except Exception:
                raise HTTPException(503, 'Could not decrypt this model credential') from None
            audit(conn, db, request, 'llm.tested', model_id, {'revision': value.revision})
        try:
            await asyncio.wait_for(probe_model(model), timeout=35)
        except Exception:
            # Provider errors may contain credentials, signed requests, or account details.
            return {'ok': False, 'message': ('Embedding connection failed. Check the endpoint or region, model ID, credentials, and embedding dimensions.'
                if model.api_format == 'embeddings' else 'Connection failed. Check the endpoint, model ID, credentials, region, and model access.')}
        return {'ok': True, 'message': 'Embedding model responded with the configured dimensions.' if model.api_format == 'embeddings' else 'Model responded successfully.'}

    return router
