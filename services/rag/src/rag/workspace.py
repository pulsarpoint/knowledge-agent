"""Persistent RAG workspace: settings, the shared embedding model and collections."""

import base64
import hashlib
import json
import os
from uuid import UUID

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from knowledge_llm import RuntimeModel, decode_model
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from rag.collections import Areas


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_file_bytes: int = Field(2_000_000, ge=100, le=10_000_000)


def digest(value: bytes | str | dict) -> str:
    if isinstance(value, dict):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


class Workspace:
    def __init__(self, areas: Areas, conninfo: str, encryption_key: bytes):
        self.areas = areas
        self.conninfo = conninfo
        self.cipher = AESGCM(encryption_key)

    @classmethod
    def from_env(cls):
        return cls(Areas.from_env(), "", base64.urlsafe_b64decode(os.environ["CREDENTIAL_ENCRYPTION_KEY"]))

    def connect(self):
        return psycopg.connect(self.conninfo, row_factory=dict_row, connect_timeout=5)

    def ensure(self, conn, project: UUID):
        if conn.execute("SELECT id FROM ka.projects WHERE id=%s", (project,)).fetchone() is None:
            raise HTTPException(404, "Unknown project")
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                     (project, Jsonb(Settings().model_dump())))
        self.settings(conn, project)

    def settings(self, conn, project: UUID, *, lock: bool = False):
        row = conn.execute("SELECT * FROM ka.rag_workspaces WHERE project_id=%s" + (" FOR UPDATE" if lock else ""), (project,)).fetchone()
        if row is None:
            raise HTTPException(404, "RAG workspace has not been initialized")
        return row

    def embedding_model(self, conn, model_id: UUID | None) -> RuntimeModel:
        row = conn.execute("SELECT * FROM ka.llm_models WHERE id=%s AND api_format='embeddings' AND enabled", (model_id,)).fetchone()
        if row is None:
            raise HTTPException(409, "Select an enabled embedding model from LLM Settings in RAG Settings")
        try:
            return decode_model(row, self.cipher)
        except Exception:
            raise HTTPException(503, "Stored model credential cannot be decrypted") from None

    def bump(self, conn, project: UUID):
        conn.execute("UPDATE ka.rag_workspaces SET revision=revision+1,updated_at=now() WHERE project_id=%s", (project,))

    def collection(self, conn, project: UUID, collection_id: UUID, *, lock: bool = False) -> dict:
        row = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND id=%s AND deleted_at IS NULL"
                           + (" FOR UPDATE" if lock else ""), (project, collection_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown collection")
        return row

    def collection_by_name(self, conn, project: UUID, name: str) -> dict:
        row = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND name=%s AND deleted_at IS NULL", (project, name)).fetchone()
        if row is None:
            raise HTTPException(404, f"Unknown collection: {name}")
        return row

    def paths(self, conn, collection_id: UUID) -> list[dict]:
        return conn.execute("SELECT * FROM ka.rag_collection_paths WHERE collection_id=%s ORDER BY source_path",
                            (collection_id,)).fetchall()

    def create_collection(self, conn, project: UUID, name: str, create: dict | None = None) -> None:
        """Insert the named collection unless it exists. `create` (description, include, exclude) applies
        only to the new row; omitted or None values keep the column defaults. Nothing is locked."""
        values = {key: value for key, value in (create or {}).items() if value is not None}
        assert set(values) <= {"description", "include", "exclude"}, values  # column names are never input
        columns = ["project_id", "name", *values]
        conn.execute(f"""INSERT INTO ka.rag_collections({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))})
            ON CONFLICT (project_id,name) WHERE deleted_at IS NULL DO NOTHING""", [project, name, *values.values()])

    def ensure_collection(self, conn, project: UUID, name: str, create: dict | None = None) -> dict:
        """The named collection, created with `create` settings when missing; never changes an existing one.

        The row is locked, so path changes to one collection are serialized with its edits and syncs.
        """
        self.create_collection(conn, project, name, create)
        return conn.execute("""SELECT * FROM ka.rag_collections WHERE project_id=%s AND name=%s AND deleted_at IS NULL
            FOR UPDATE""", (project, name)).fetchone()
