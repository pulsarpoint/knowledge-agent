# LLM Settings

Open **Administration → LLM Settings** (`http://localhost:8183/llm-settings`).
Add a named model connection, save it, and test access here. Select **Chat / agents**
for agent models or **Embeddings** for RAG indexing. Each agent selects its chat
connection on its own page. **RAG → Settings** selects the embedding connection.

## Model connections

| Type / provider | Connection fields |
| --- | --- |
| Chat / OpenAI compatible | Model ID, API base URL, optional API key, Responses or Chat Completions |
| Chat / Anthropic | Model ID, API origin, API key |
| Chat / AWS Bedrock | Model or inference profile ID, AWS region, Bedrock API key |
| Embeddings / OpenAI compatible | Model ID, API base URL, optional API key, expected dimensions, embedding version |
| Embeddings / AWS Bedrock | Titan V2 model ID, AWS region, Bedrock API key, dimensions (256/512/1024), embedding version |

Embedding connections use the OpenAI-compatible `/embeddings` API or native
Bedrock InvokeModel for `amazon.titan-embed-text-v2:0` (Titan Text Embeddings V2).
Bedrock uses the saved region and bearer key; it requests normalized vectors with
the configured dimensions. Other native Bedrock embedding families are not yet
supported. Anthropic connections remain chat-only. A saved connection's
chat/embedding type is fixed; create a separate connection to use another type.

**Test connection** makes one small chat or embedding request with a 35-second
deadline. It can incur provider usage. Embedding tests also validate the returned
vector dimensions. Tests use the saved revision; opening or saving a form does
not call the provider. Failures never expose raw provider responses or keys.

Disabling a connection blocks new requests that use it. It remains editable and
can be re-enabled. A disabled embedding connection can stay selected while RAG
embedding processing is off. Full-text processing/search does not require a model.

## RAG indexing

After creating and testing an embedding connection, select it in **RAG → Settings**,
enable embedding processing, save, and process the desired folders.
Changing the model, endpoint, dimensions, or version makes the next processing run
refresh vectors while reusing unchanged parsed text. Increment the version when
replacing a model behind the same name. Credential rotation does not invalidate
existing vectors. Connection changes during indexing prevent a stale worker from
publishing its results; rerun the folder to use the updated connection.

Migration `0037_embedding_models` moves existing workspace embedding connections
and encrypted keys into `ka.llm_models`. Memory uses the connection selected in
`ka.memory_settings.embedding_model_id` (Memory page; `0132` copied it from the RAG workspace).
Existing vector profiles are preserved.
See [RAG administration](rag.md) for deployment and processing details.

## Credentials

API keys use AES-256-GCM with the existing `CREDENTIAL_ENCRYPTION_KEY`. Each write
uses a random 12-byte nonce stored with the ciphertext and authentication tag.
Authenticated metadata binds each key to its model ID and credential purpose.
Embedding connections retain the `<model UUID>:embedding` binding so migrated
credentials do not need to be decrypted and re-encrypted. Keep the master key
with database backups. APIs return only `has_api_key`, never the saved key.

Omitting `api_key` preserves it. A new value replaces it; explicit `null` removes
it for OpenAI-compatible connections. Changing provider, base URL, or region
requires replacing or removing a saved key. Audit events contain metadata only.

Bedrock uses a **Bedrock API key (bearer token)** entered in the form. IAM access
key pairs and role selection are not exposed here. Use the exact model or
inference profile ID available in your region. Model choices for specialist agents
are filtered for compatible tool support.

Database access, encryption keys, service URLs, and tracing remain in Compose.
Provider credentials for these saved connections are managed in administration.
Standalone collection CLIs retain their separate TOML/env configuration.

## Admin API

Routes require the existing `docs-admins` authorization. Writes require an
authenticated user identity; validation errors never echo input values.

- `GET /api/llm`: model profiles and legacy assignments.
- `POST /api/llm/models`: create a connection.
- `PUT /api/llm/models/{id}`: update with its current `revision`.
- `POST /api/llm/models/{id}/test`: test with `{ "revision": 1 }`.
- `PUT /api/agents/{id}/model`: select an agent's chat connection.
- `GET`/`PUT /api/memory/settings`: the memory embedding model (`embedding_model_id`, an embeddings connection or `null`).
- `PUT /api/rag/settings`: save processing settings.

Embedding models use `api_format: "embeddings"`, `provider: "openai"` or `"bedrock"`,
and require `embedding_dimensions` and a nonempty `embedding_version`. OpenAI
compatible models support dimensions 1–16000; Titan V2 supports 256, 512, or 1024. Chat models
omit those two fields. Agent assignments reject embedding models. Stale writes
return HTTP 409.

Migration `0038_bedrock_embeddings` allows Bedrock embedding connections. Its
rollback rejects existing Bedrock embedding records, preserving their credentials.
See [AWS Titan V2 documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-titan-text-embeddings-v2.html)
and [Bedrock bearer authentication](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys-use.html).
