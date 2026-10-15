"""The embedding model memory uses: the project's RAG embedding model (RAG Settings), decoded with the process's
credential key. embed_text never raises: no model, a disabled one or a failing call gives no vector, and the entry is
found by its words until it is embedded again."""

import base64
import os
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from knowledge_llm import ModelUnavailable, RuntimeModel, decode_model, embeddings


def current_model(conn, project_id: UUID) -> RuntimeModel | None:
    row = conn.execute(
        """SELECT m.* FROM ka.rag_workspaces w JOIN ka.llm_models m ON m.id=w.embedding_model_id
        WHERE w.project_id=%s AND m.enabled AND m.api_format='embeddings'""",
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        cipher = AESGCM(base64.b64decode(os.environ['CREDENTIAL_ENCRYPTION_KEY'], altchars=b'-_', validate=True))
        return decode_model(row, cipher)
    except Exception:  # noqa: BLE001 - a missing key or a credential that does not decrypt: treated as no model
        return None


def embed_text(model: RuntimeModel | None, text: str, transport=None) -> tuple[list[float] | None, str | None]:
    if model is None:
        return None, None
    try:
        return embeddings.embed(model, [text], transport)[0], embeddings.embedding_profile(model)
    except ModelUnavailable:
        return None, None
