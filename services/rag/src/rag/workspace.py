"""Persistent folder workspace and deployment-confined source files."""

import base64
import hashlib
import json
import os
import re
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from knowledge_llm import RuntimeModel, decode_model
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_size: int = Field(2000, ge=200, le=12000)
    chunk_overlap: int = Field(200, ge=0, le=2000)
    max_file_bytes: int = Field(2_000_000, ge=100, le=10_000_000)
    extensions: list[Literal[".md", ".markdown", ".txt", ".json"]] = Field(default_factory=lambda: [".md", ".markdown", ".txt", ".json"], min_length=1)
    embedding_enabled: bool = False

    @model_validator(mode="after")
    def valid_settings(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        return self


def digest(value: bytes | str | dict) -> str:
    if isinstance(value, dict):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def folder_path(value: str, *, root: bool = False) -> str:
    if root and value == "":
        return value
    if len(value) > 500 or not re.fullmatch(r"[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*", value):
        raise HTTPException(400, "Folder names may contain letters, numbers, hyphens, and underscores")
    return value


def file_path(value: str) -> tuple[str, str]:
    parent, _, name = value.rpartition("/")
    folder_path(parent)
    if not name or name in {".", ".."} or len(name) > 200 or any(ord(c) < 32 or c in "/\\" for c in name):
        raise HTTPException(400, "Invalid file name")
    return parent, name


class Workspace:
    def __init__(self, root: Path, conninfo: str, encryption_key: bytes, *, host_root: Path | None = None):
        self.root = root.resolve()
        if host_root is not None and not host_root.is_absolute():
            raise ValueError("RG_DATA must be an absolute Docker host path")
        # Deployment metadata: never resolve or open a host path inside the container.
        self.host_root = host_root
        self.conninfo = conninfo
        self.cipher = AESGCM(encryption_key)

    @classmethod
    def from_env(cls):
        host_path = os.environ.get("RG_DATA")
        return cls(Path(os.environ["RAG_SOURCE_ROOT"]), "", base64.urlsafe_b64decode(os.environ["CREDENTIAL_ENCRYPTION_KEY"]),
                   host_root=Path(host_path) if host_path else None)

    def source_location(self) -> dict[str, str | None]:
        return {
            "container_root": str(self.root),
            "host_root": str(self.host_root) if self.host_root is not None else None,
        }

    def connect(self):
        return psycopg.connect(self.conninfo, row_factory=dict_row, connect_timeout=5)

    def ensure(self, conn, project: UUID):
        if conn.execute("SELECT id FROM ka.projects WHERE id=%s", (project,)).fetchone() is None:
            raise HTTPException(404, "Unknown project")
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,%s) ON CONFLICT DO NOTHING", (project, Jsonb(Settings().model_dump())))
        self.settings(conn, project)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.directory(project, "", create=True):
            pass

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

    @contextmanager
    def directory(self, project: UUID, path: str, *, create: bool = False):
        folder_path(path, root=True)
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in filter(None, path.split("/")):
                if create:
                    try:
                        os.mkdir(part, mode=0o750, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    def read(self, project: UUID, path: str, limit: int) -> bytes:
        parent, name = file_path(path)
        with self.directory(project, parent) as fd:
            leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(leaf, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise HTTPException(400, "Only regular files are supported")
                data = stream.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(400, f"File exceeds the configured size limit: {path}")
        return data

    def ensure_not_deleting(self, conn, project: UUID, path: str) -> None:
        if conn.execute("""SELECT 1 FROM ka.rag_folders WHERE project_id=%s AND source_cleanup_pending
                         AND (path=%s OR starts_with(%s,path || '/'))""", (project, path, path)).fetchone():
            raise HTTPException(409, "Source folder deletion is still being completed; refresh before using this path")

    def cleanup_deleted_folders(self, project: UUID) -> bool:
        """Retry durable source removals; return whether any cleanup remains pending."""
        with self.connect() as conn:
            self.settings(conn, project, lock=True)
            rows = conn.execute("SELECT id,path FROM ka.rag_folders WHERE project_id=%s AND source_cleanup_pending ORDER BY length(path)", (project,)).fetchall()
            for row in rows:
                parent, _, name = folder_path(row["path"]).rpartition("/")
                try:
                    with self.directory(project, parent) as fd:
                        # fd-based rmtree never follows descendant symlinks.
                        # A replaced root symlink is itself removed, not traversed.
                        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        if stat.S_ISDIR(entry.st_mode):
                            shutil.rmtree(name, dir_fd=fd)
                        else:
                            os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    # A parent or descendant may already have been removed.
                    # Retry if the requested root still exists after a partial walk.
                    try:
                        with self.directory(project, parent) as fd:
                            os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        continue
                except OSError:
                    continue
                conn.execute("UPDATE ka.rag_folders SET source_cleanup_pending=false WHERE id=%s", (row["id"],))
            return conn.execute("SELECT 1 FROM ka.rag_folders WHERE project_id=%s AND source_cleanup_pending", (project,)).fetchone() is not None

    def scan(self, project: UUID, excluded: set[str]) -> tuple[list[str], list[dict]]:
        folders, files = [], []

        def visit(path: str):
            with self.directory(project, path) as fd:
                for name in sorted(os.listdir(fd)):
                    child = f"{path}/{name}" if path else name
                    if child in excluded:
                        continue
                    if len(folders) + len(files) >= 5000:
                        raise HTTPException(400, "Workspace exceeds 5000 entries; reduce the number of source entries")
                    entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(entry.st_mode):
                        folder_path(child)
                        folders.append(child)
                        visit(child)
                    elif stat.S_ISREG(entry.st_mode) and path:
                        file_path(child)
                        files.append({"path": child, "folder": path, "size": entry.st_size})
                    else:
                        raise HTTPException(400, "Sources must be regular files inside collection folders; links and root-level files are unsupported")
        visit("")
        return folders, files

    def discover(self, conn, project: UUID):
        pending = conn.execute("SELECT path FROM ka.rag_folders WHERE project_id=%s AND source_cleanup_pending", (project,)).fetchall()
        folders, files = self.scan(project, {row["path"] for row in pending})
        for path in folders:
            conn.execute("INSERT INTO ka.rag_folders(project_id,path) VALUES (%s,%s) ON CONFLICT DO NOTHING", (project, path))
        return folders, files

    def snapshot(self, conn, project: UUID, path: str, recursive: bool, settings: Settings):
        folders, files = self.discover(conn, project)
        selected = lambda value: value == path or (recursive and value.startswith(path + "/"))
        records = conn.execute("SELECT * FROM ka.rag_folders WHERE project_id=%s AND deleted_at IS NULL ORDER BY path", (project,)).fetchall()
        sources = []
        for row in records:
            if selected(row["path"]) and row["path"] in folders:
                sources.append({"folder_id": row["id"], "path": row["path"], "kind": "folder", "data": (row["path"].replace("/", ".") + "\n" + row["description"]).encode()})
        ids = {row["path"]: row["id"] for row in records}
        total = 0
        for entry in files:
            if selected(entry["folder"]) and Path(entry["path"]).suffix.lower() in settings.extensions:
                data = self.read(project, entry["path"], settings.max_file_bytes)
                total += len(data)
                if total > 100_000_000:
                    raise HTTPException(400, "A processing run is limited to 100 MB; process smaller subfolders")
                sources.append({"folder_id": ids[entry["folder"]], "path": entry["path"], "kind": "file", "data": data})
        for source in sources:
            source["content_hash"] = digest(source["data"])
        return sources
