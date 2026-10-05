"""Authenticated backoffice API; area roots come from the deployment, never from input."""

import hmac
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.collections import AREAS, FolderMissing, Walk, relative_path, source_path
from rag.workspace import Settings, Workspace, digest
from rag.workspace_models import embed
from rag.workspace_search import SearchInput, search


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    settings: Settings
    embedding_model_id: UUID | None = None


GlobPattern = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^/\\\x00][^\\\x00]*$")]


class CollectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=60)
    description: str = Field("", max_length=2000)
    source_path: str
    search_mode: Literal["full_text", "embedding", "hybrid"] = "full_text"
    embeddings_enabled: bool = False
    include: list[GlobPattern] = Field(default_factory=lambda: ["**/*.md", "**/*.markdown", "**/*.txt"], min_length=1, max_length=50)
    exclude: list[GlobPattern] = Field(default_factory=list, max_length=50)
    chunk_size: int = Field(2000, ge=200, le=12000)
    chunk_overlap: int = Field(200, ge=0, le=2000)
    revision: int | None = None

    @model_validator(mode="after")
    def valid(self):
        source_path(self.source_path)
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.search_mode == "embedding" and not self.embeddings_enabled:
            raise ValueError("Turn on embeddings for semantic search, or choose keyword or hybrid search")
        return self


class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False


class FileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection: str
    path: str
    content: str = Field(max_length=10_000_000)
    expected_hash: str | None = None


STATUS_SQL = """SELECT count(DISTINCT f.id) AS indexed_files, count(c.id) AS chunks,
    count(v.input_hash) AS embedded_chunks
    FROM ka.rag_files f LEFT JOIN ka.rag_chunks c ON c.file_id=f.id
    LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile
        AND v.input_hash=c.input_hash AND f.embedding_profile<>''
    WHERE f.collection_id=%s"""

ACTIVE = ("queued", "running", "waiting_input")


def collection_status(workspace: Workspace, conn, row: dict) -> dict:
    counts = conn.execute(STATUS_SQL, (row["id"],)).fetchone()
    job = conn.execute("""SELECT id,status,error,created_at,finished_at FROM ka.jobs
        WHERE rag_collection_id=%s ORDER BY created_at DESC LIMIT 1""", (row["id"],)).fetchone()
    try:
        walk = workspace.areas.walk(row["source_path"], row["include"], row["exclude"], truncate=True)
        present = True
    except FolderMissing:
        walk, present = Walk(), False
    changed = datetime.fromtimestamp(walk.newest_mtime, timezone.utc) if walk.newest_mtime is not None else None
    synced = row["last_synced_at"]
    needs = present and (synced is None or row["synced_revision"] != row["revision"] or (changed is not None and changed > synced))
    if not present:
        state = "folder_missing"
    elif job is not None and job["status"] in ACTIVE:
        state = "syncing"
    elif job is not None and job["status"] == "failed" and (synced is None or job["created_at"] > synced):
        state = "failed"
    else:
        state = "needs_refresh" if needs else "up_to_date"
    return {**row, **counts, "job": job, "host_path": workspace.areas.host_path(row["source_path"]),
            "container_path": str(workspace.areas.container_path(row["source_path"])),
            "writable": workspace.areas.writable(row["source_path"]), "folder_present": present,
            "folder_changed_at": changed, "file_count": len(walk.files), "truncated": walk.truncated,
            "skipped": walk.skipped, "needs_refresh": bool(needs), "status": state}


def build_api(workspace: Workspace, key: str, transport=None):
    def authenticate(request: Request):
        if not key or not hmac.compare_digest(request.headers.get("x-rag-key", ""), key):
            raise HTTPException(403, "RAG service authentication required")
    router = APIRouter(dependencies=[Depends(authenticate)])
    prefix = "/backoffice/projects/{project}"
    COLUMNS = ("name", "description", "source_path", "search_mode", "embeddings_enabled", "include", "exclude", "chunk_size", "chunk_overlap")

    @router.get('/backoffice/workspace')
    def get_workspace():
        with workspace.connect() as conn:
            row = conn.execute('SELECT project_id FROM ka.rag_workspaces').fetchone()
        if row is None:
            raise HTTPException(409, 'Open RAG Settings to initialize the workspace')
        return {'project_id': str(row['project_id'])}

    @router.get(prefix + '/documents')
    def document_catalog(project: UUID):
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            return conn.execute("""SELECT f.path, k.name AS collection,
                left(regexp_replace(coalesce(c.text, f.path), '\\s+', ' ', 'g'), 280) AS description,
                k.description AS collection_description
                FROM ka.rag_files f JOIN ka.rag_collections k ON k.id=f.collection_id AND k.deleted_at IS NULL
                LEFT JOIN LATERAL (SELECT text FROM ka.rag_chunks WHERE file_id=f.id ORDER BY position LIMIT 1) c ON true
                WHERE f.project_id=%s ORDER BY k.name, f.path""", (project,)).fetchall()

    def config_response(row):
        return {"revision": row["revision"], "settings": row["settings"],
                "embedding_model_id": row["embedding_model_id"],
                "areas": [{"area": area, "container_root": str(workspace.areas.roots.get(area, "")),
                           "host_root": workspace.areas.host_roots.get(area)} for area in AREAS]}

    @router.get(prefix + "/settings")
    def get_settings(project: UUID):
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            return config_response(workspace.settings(conn, project))

    @router.put(prefix + "/settings")
    def save_settings(project: UUID, payload: SettingsInput):
        with workspace.connect() as conn:
            row = workspace.settings(conn, project, lock=True)
            if row["revision"] != payload.revision:
                raise HTTPException(409, "Workspace changed; refresh before saving")
            if payload.embedding_model_id is not None and conn.execute(
                    "SELECT 1 FROM ka.llm_models WHERE id=%s AND api_format='embeddings'", (payload.embedding_model_id,)).fetchone() is None:
                raise HTTPException(400, 'Choose an embedding model from LLM Settings')
            conn.execute('UPDATE ka.rag_workspaces SET settings=%s,embedding_model_id=%s WHERE project_id=%s',
                         (Jsonb(payload.settings.model_dump()), payload.embedding_model_id, project))
            workspace.bump(conn, project)
            return config_response(workspace.settings(conn, project))

    @router.post(prefix + '/test/embedding')
    def test_embedding(project: UUID):
        with workspace.connect() as conn:
            row = workspace.settings(conn, project)
            model = workspace.embedding_model(conn, row['embedding_model_id'])
        embed(model, ['Connection test'], transport)
        return {'message': 'Connection successful'}

    @router.get(prefix + "/collections")
    def list_collections(project: UUID):
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            rows = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name", (project,)).fetchall()
            return {"collections": [collection_status(workspace, conn, row) for row in rows]}

    @router.post(prefix + "/collections")
    def create_collection(project: UUID, payload: CollectionInput):
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            try:
                row = conn.execute(f"INSERT INTO ka.rag_collections(project_id,{','.join(COLUMNS)}) VALUES (%s{',%s' * len(COLUMNS)}) RETURNING id",
                                   (project, *(getattr(payload, c) for c in COLUMNS))).fetchone()
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, "A collection with this name already exists") from None
            return row

    @router.get(prefix + "/collections/{collection_id}")
    def get_collection(project: UUID, collection_id: UUID):
        with workspace.connect() as conn:
            return collection_status(workspace, conn, workspace.collection(conn, project, collection_id))

    @router.put(prefix + "/collections/{collection_id}")
    def update_collection(project: UUID, collection_id: UUID, payload: CollectionInput):
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)
            if payload.revision != row["revision"]:
                raise HTTPException(409, "Collection changed; refresh before saving")
            try:
                row = conn.execute(f"UPDATE ka.rag_collections SET {','.join(c + '=%s' for c in COLUMNS)},revision=revision+1,updated_at=now() WHERE id=%s RETURNING *",
                                   (*(getattr(payload, c) for c in COLUMNS), collection_id)).fetchone()
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, "A collection with this name already exists") from None
            return collection_status(workspace, conn, row)

    @router.delete(prefix + "/collections/{collection_id}")
    def delete_collection(project: UUID, collection_id: UUID, revision: int, confirmed: bool = False):
        if not confirmed:
            raise HTTPException(400, "Confirm removing this collection from search; its files stay on disk")
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)
            if revision != row["revision"]:
                raise HTTPException(409, "Collection changed; refresh before deleting")
            # Lock order collection -> workspace matches the worker's publish, which
            # re-checks reused vectors under the workspace lock.
            workspace.settings(conn, project, lock=True)
            conn.execute("""UPDATE ka.jobs SET status='cancelled',finished_at=now(),error='Collection deleted',
                worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                WHERE rag_collection_id=%s AND status IN ('queued','running','waiting_input')""", (collection_id,))
            removed = conn.execute("DELETE FROM ka.rag_files WHERE collection_id=%s", (collection_id,)).rowcount
            conn.execute("DELETE FROM ka.agent_collections WHERE collection_id=%s", (collection_id,))
            # Shared vectors remain only while a surviving chunk still uses them.
            conn.execute("""DELETE FROM ka.rag_vectors v WHERE v.project_id=%s AND NOT EXISTS (
                SELECT 1 FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id
                WHERE c.project_id=v.project_id AND c.input_hash=v.input_hash AND f.embedding_profile=v.profile)""", (project,))
            conn.execute("UPDATE ka.rag_collections SET deleted_at=now(),updated_at=now() WHERE id=%s", (collection_id,))
        return {"deleted_files": removed}

    def queue_sync(conn, project: UUID, row: dict, actor: str | None, force: bool):
        # A missing folder raises FolderMissing (409) before anything is queued.
        workspace.areas.walk(row["source_path"], row["include"], row["exclude"], truncate=True)
        try:
            with conn.transaction():
                return conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,requested_by,input)
                    VALUES (%s,'rag_collection_sync',%s,%s,%s) RETURNING id""",
                    (project, row["id"], UUID(actor) if actor else None, Jsonb({"force": force}))).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "A sync is already queued or running for this collection") from None

    @router.post(prefix + "/collections/{collection_id}/sync", status_code=202)
    def sync_collection(project: UUID, collection_id: UUID, payload: SyncInput, request: Request):
        with workspace.connect() as conn:
            return queue_sync(conn, project, workspace.collection(conn, project, collection_id), request.headers.get("x-rag-actor"), payload.force)

    @router.post(prefix + "/collections/sync-stale")
    def sync_stale(project: UUID, request: Request):
        queued = []
        with workspace.connect() as conn:
            for row in conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name", (project,)).fetchall():
                if collection_status(workspace, conn, row)["status"] in ("needs_refresh", "failed"):
                    try:
                        queue_sync(conn, project, row, request.headers.get("x-rag-actor"), False)
                        queued.append(row["name"])
                    except HTTPException:
                        pass
        return {"queued": queued}

    @router.get(prefix + "/collections/{collection_id}/files")
    def collection_files(project: UUID, collection_id: UUID):
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id)
            indexed = {r["path"]: r for r in conn.execute("""SELECT f.path,f.indexed_at,f.content_hash,count(c.id) AS chunks,
                count(v.input_hash) AS embedded_chunks FROM ka.rag_files f LEFT JOIN ka.rag_chunks c ON c.file_id=f.id
                LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile AND v.input_hash=c.input_hash AND f.embedding_profile<>''
                WHERE f.collection_id=%s GROUP BY f.id""", (collection_id,)).fetchall()}
        walk = workspace.areas.walk(row["source_path"], row["include"], row["exclude"], truncate=True)
        files = []
        for entry in walk.files:
            known = indexed.pop(entry["path"], None)
            changed = datetime.fromtimestamp(entry["mtime"], timezone.utc)
            files.append({**entry, "indexed_at": known and known["indexed_at"], "content_hash": known and known["content_hash"],
                          "chunks": known["chunks"] if known else 0, "embedded_chunks": known["embedded_chunks"] if known else 0,
                          "stale": known is None or changed > known["indexed_at"]})
        files += [{"path": path, "size": None, "mtime": None, **{k: known[k] for k in ("indexed_at", "content_hash", "chunks", "embedded_chunks")},
                   "stale": True} for path, known in indexed.items()]  # indexed but gone from disk
        return {"files": sorted(files, key=lambda f: f["path"]), "truncated": walk.truncated, "skipped": walk.skipped}

    @router.get(prefix + "/collections/{collection_id}/chunks")
    def collection_chunks(project: UUID, collection_id: UUID, path: str):
        with workspace.connect() as conn:
            workspace.collection(conn, project, collection_id)
            return {"chunks": conn.execute("""SELECT c.position,c.text,(v.input_hash IS NOT NULL) AS embedded
                FROM ka.rag_files f JOIN ka.rag_chunks c ON c.file_id=f.id
                LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile AND v.input_hash=c.input_hash AND f.embedding_profile<>''
                WHERE f.collection_id=%s AND f.path=%s ORDER BY c.position""", (collection_id, relative_path(path))).fetchall()}

    @router.get(prefix + "/file")
    def get_file(project: UUID, collection: str, path: str):
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            row = workspace.collection_by_name(conn, project, collection)
        data = workspace.areas.read(row["source_path"], path, limit)
        try:
            content = data.decode("utf-8-sig")
        except UnicodeError:
            raise HTTPException(400, "File is not UTF-8 text") from None
        return {"collection": collection, "path": path, "content": content, "hash": digest(data)}

    @router.put(prefix + "/file")
    def put_file(project: UUID, payload: FileInput):
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            row = workspace.collection_by_name(conn, project, payload.collection)
            data = payload.content.encode("utf-8")
            if len(data) > limit:
                raise HTTPException(400, "File exceeds the configured size limit")
            try:
                previous = digest(workspace.areas.read(row["source_path"], payload.path, limit))
            except FileNotFoundError:
                previous = None
            if previous != payload.expected_hash:
                raise HTTPException(409, "File already exists or has changed; open it before replacing it")
            workspace.areas.write(row["source_path"], payload.path, data)
        return {"path": payload.path, "hash": digest(data)}

    @router.delete(prefix + "/file")
    def delete_file(project: UUID, collection: str, path: str, expected_hash: str):
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            row = workspace.collection_by_name(conn, project, collection)
            if digest(workspace.areas.read(row["source_path"], path, limit)) != expected_hash:
                raise HTTPException(409, "File changed; open it again before deleting")
            workspace.areas.delete(row["source_path"], path)
        return {"message": "File deleted. Refresh the collection to remove it from search."}

    @router.get(prefix + "/jobs")
    def jobs(project: UUID):
        with workspace.connect() as conn:
            return {"jobs": conn.execute("""SELECT j.id,j.status,j.input,j.result,j.error,j.created_at,j.started_at,j.finished_at,
                j.rag_collection_id AS collection_id, coalesce(k.name, d.path) AS collection
                FROM ka.jobs j LEFT JOIN ka.rag_collections k ON k.id=j.rag_collection_id
                LEFT JOIN ka.rag_folders d ON d.id=j.rag_folder_id
                WHERE j.project_id=%s AND j.kind IN ('rag_collection_sync','rag_folder_index')
                ORDER BY j.created_at DESC LIMIT 100""", (project,)).fetchall()}

    @router.post(prefix + "/jobs/{job_id}/cancel")
    def cancel(project: UUID, job_id: UUID):
        with workspace.connect() as conn:
            row = conn.execute("""UPDATE ka.jobs SET status='cancelled',finished_at=now(),worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                WHERE id=%s AND project_id=%s AND kind IN ('rag_collection_sync','rag_folder_index') AND status IN ('queued','running') RETURNING id""",
                (job_id, project)).fetchone()
            if row is None:
                raise HTTPException(409, "Job is already finished or does not exist")
            return row

    @router.post("/search")
    def search_route(payload: SearchInput):
        return search(workspace, payload, transport)

    return router
