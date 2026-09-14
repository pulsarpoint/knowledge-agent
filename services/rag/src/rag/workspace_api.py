"""Authenticated backoffice API; the source root is never accepted as input."""

import hmac
import os
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb

from rag.workspace import Settings, Workspace, digest, file_path, folder_path
from rag.workspace_models import embed
from rag.workspace_search import SearchInput, search


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    settings: Settings
    embedding_model_id: UUID | None = None


class FolderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    description: str = Field("", max_length=10000)
    revision: int


class FileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str = Field(max_length=10_000_000)
    expected_hash: str | None = None


class ProcessInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: UUID
    recursive: bool = True
    force: bool = False
    allow_empty: bool = False


def build_api(workspace: Workspace, key: str, transport=None):
    def authenticate(request: Request):
        if not key or not hmac.compare_digest(request.headers.get("x-rag-key", ""), key):
            raise HTTPException(403, "RAG service authentication required")
    router = APIRouter(dependencies=[Depends(authenticate)])
    prefix = "/backoffice/projects/{project}"

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
            return conn.execute("""SELECT f.path, d.path AS folder,
                left(regexp_replace(coalesce(c.text, f.path), '\\s+', ' ', 'g'), 280) AS description,
                d.description AS folder_description
                FROM ka.rag_files f JOIN ka.rag_folders d ON d.id=f.folder_id
                LEFT JOIN LATERAL (SELECT text FROM ka.rag_chunks WHERE file_id=f.id ORDER BY position LIMIT 1) c ON true
                WHERE f.project_id=%s AND f.kind='file' AND d.deleted_at IS NULL ORDER BY f.path""", (project,)).fetchall()

    def config_response(row):
        return {"revision": row["revision"], "settings": row["settings"],
                "embedding_model_id": row["embedding_model_id"],
                "source_location": workspace.source_location(),
                "source_root": str(workspace.root)}

    def revision_check(row, expected):
        if row["revision"] != expected:
            raise HTTPException(409, "Workspace changed; refresh before saving")

    @router.get(prefix + "/settings")
    def get_settings(project: UUID):
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            return config_response(workspace.settings(conn, project))

    @router.put(prefix + "/settings")
    def save_settings(project: UUID, payload: SettingsInput):
        with workspace.connect() as conn:
            row = workspace.settings(conn, project, lock=True)
            revision_check(row, payload.revision)
            if payload.embedding_model_id is not None:
                model = conn.execute("SELECT enabled FROM ka.llm_models WHERE id=%s AND api_format='embeddings'", (payload.embedding_model_id,)).fetchone()
                if model is None or payload.settings.embedding_enabled and not model['enabled']:
                    raise HTTPException(400, 'Choose an enabled embedding model from LLM Settings')
            elif payload.settings.embedding_enabled:
                raise HTTPException(400, 'Select an embedding model before enabling embeddings')
            conn.execute('UPDATE ka.rag_workspaces SET settings=%s,embedding_model_id=%s WHERE project_id=%s',
                         (Jsonb(payload.settings.model_dump()), payload.embedding_model_id, project))
            workspace.bump(conn, project)
            return config_response(workspace.settings(conn, project))

    @router.post(prefix + '/test/embedding')
    def test_embedding(project: UUID):
        with workspace.connect() as conn:
            row = workspace.settings(conn, project)
            if not row['settings']['embedding_enabled']:
                raise HTTPException(409, 'Enable embeddings and save settings first')
            model = workspace.embedding_model(conn, row['embedding_model_id'])
        embed(model, ['Connection test'], transport)
        return {'message': 'Connection successful'}

    @router.get(prefix + "/tree")
    def tree(project: UUID):
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            config = workspace.settings(conn, project, lock=True)
            present, files = workspace.discover(conn, project)
            folders = conn.execute("""SELECT d.*,count(f.id) FILTER (WHERE f.kind='file') AS indexed_files,
                                   max(f.indexed_at) AS indexed_at FROM ka.rag_folders d
                                   LEFT JOIN ka.rag_files f ON f.folder_id=d.id
                                   WHERE d.project_id=%s AND d.deleted_at IS NULL GROUP BY d.id ORDER BY d.path""", (project,)).fetchall()
            pending = conn.execute("SELECT path FROM ka.rag_folders WHERE project_id=%s AND source_cleanup_pending ORDER BY path", (project,)).fetchall()
            indexed = {row["path"]: row for row in conn.execute("SELECT path,content_hash,indexed_at FROM ka.rag_files WHERE project_id=%s AND kind='file'", (project,)).fetchall()}
        return {"source_root": str(workspace.root), "revision": config["revision"],
                "source_location": workspace.source_location(),
                "pending_deletions": [row["path"] for row in pending],
                "folders": [{**row, "scope": row["path"].replace("/", "."), "present": row["path"] in present,
                             "files": sum(f["folder"] == row["path"] for f in files)} for row in folders],
                "files": [{**row, "supported": Path(row["path"]).suffix.lower() in config["settings"]["extensions"],
                           "indexed_hash": indexed.get(row["path"], {}).get("content_hash"),
                           "indexed_at": indexed.get(row["path"], {}).get("indexed_at")} for row in files]}

    @router.post(prefix + "/folders")
    def create_folder(project: UUID, payload: FolderInput):
        path = folder_path(payload.path)
        with workspace.connect() as conn:
            config = workspace.settings(conn, project, lock=True)
            revision_check(config, payload.revision)
            workspace.ensure_not_deleting(conn, project, path)
            try:
                with workspace.directory(project, path):
                    raise HTTPException(409, "Folder already exists; select it to edit its description")
            except FileNotFoundError:
                pass
            with workspace.directory(project, path, create=True):
                pass
            workspace.discover(conn, project)
            row = conn.execute("UPDATE ka.rag_folders SET description=%s WHERE project_id=%s AND path=%s AND deleted_at IS NULL RETURNING id", (payload.description, project, path)).fetchone()
            workspace.bump(conn, project)
            return row

    @router.put(prefix + "/folders/{folder_id}")
    def update_folder(project: UUID, folder_id: UUID, payload: FolderInput):
        with workspace.connect() as conn:
            revision_check(workspace.settings(conn, project, lock=True), payload.revision)
            row = conn.execute("UPDATE ka.rag_folders SET description=%s WHERE project_id=%s AND id=%s AND path=%s AND deleted_at IS NULL RETURNING id", (payload.description, project, folder_id, payload.path)).fetchone()
            if row is None:
                raise HTTPException(404, "Unknown folder; its path cannot be changed")
            workspace.bump(conn, project)
            return row

    @router.delete(prefix + "/folders/{folder_id}")
    def delete_folder(project: UUID, folder_id: UUID, revision: int, confirmed: bool = False):
        if not confirmed:
            raise HTTPException(400, "Confirm permanent deletion of the folder, source files, and indexed data")
        with workspace.connect() as conn:
            revision_check(workspace.settings(conn, project, lock=True), revision)
            row = conn.execute("SELECT path FROM ka.rag_folders WHERE project_id=%s AND id=%s AND deleted_at IS NULL", (project, folder_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Unknown folder")
            targets = conn.execute("""SELECT id FROM ka.rag_folders WHERE project_id=%s AND deleted_at IS NULL
                                    AND (path=%s OR starts_with(path,%s))""", (project, row["path"], row["path"] + "/")).fetchall()
            ids = [target["id"] for target in targets]
            conn.execute("""UPDATE ka.jobs j SET status='cancelled',finished_at=now(),error='Source folder deleted',
                         worker_id=NULL,lease_token=NULL,lease_expires_at=NULL FROM ka.rag_folders d
                         WHERE j.rag_folder_id=d.id AND j.project_id=%s AND j.kind='rag_folder_index'
                         AND j.status IN ('queued','running','waiting_input') AND
                         (d.id=ANY(%s) OR (COALESCE((j.input->>'recursive')::boolean,false) AND starts_with(%s,d.path || '/')))""",
                         (project, ids, row["path"]))
            removed = conn.execute("DELETE FROM ka.rag_files WHERE project_id=%s AND folder_id=ANY(%s)", (project, ids)).rowcount
            # Chunk/full-text rows cascade with rag_files. Shared vectors remain
            # only while a surviving file or folder description still uses them.
            vectors = conn.execute("""DELETE FROM ka.rag_vectors v WHERE v.project_id=%s AND NOT EXISTS (
                         SELECT 1 FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id
                         WHERE c.project_id=v.project_id AND c.input_hash=v.input_hash AND f.embedding_profile=v.profile)""", (project,)).rowcount
            conn.execute("UPDATE ka.rag_folders SET deleted_at=now(),description='' WHERE project_id=%s AND id=ANY(%s)", (project, ids))
            conn.execute("UPDATE ka.rag_folders SET source_cleanup_pending=true WHERE id=%s", (folder_id,))
            workspace.bump(conn, project)
        # Commit search cleanup before removing source bytes. If a process or
        # filesystem operation fails, the worker resumes the persisted cleanup.
        pending = workspace.cleanup_deleted_folders(project)
        return JSONResponse(status_code=202 if pending else 200, content={
            "cleanup_pending": pending, "deleted_folders": len(ids), "deleted_entries": removed, "deleted_vectors": vectors,
        })

    @router.get(prefix + "/file")
    def get_file(project: UUID, path: str):
        with workspace.connect() as conn:
            settings = Settings(**workspace.settings(conn, project)["settings"])
            workspace.ensure_not_deleting(conn, project, path)
        data = workspace.read(project, path, settings.max_file_bytes)
        try:
            content = data.decode("utf-8-sig")
        except UnicodeError:
            raise HTTPException(400, "File is not UTF-8 text") from None
        return {"path": path, "content": content, "hash": digest(data)}

    @router.put(prefix + "/file")
    def put_file(project: UUID, payload: FileInput):
        parent, name = file_path(payload.path)
        with workspace.connect() as conn:
            settings = Settings(**workspace.settings(conn, project, lock=True)["settings"])
            workspace.ensure_not_deleting(conn, project, payload.path)
            data = payload.content.encode("utf-8")
            if Path(name).suffix.lower() not in settings.extensions or len(data) > settings.max_file_bytes:
                raise HTTPException(400, "Unsupported extension or file exceeds the configured size limit")
            try:
                previous = digest(workspace.read(project, payload.path, settings.max_file_bytes))
            except FileNotFoundError:
                previous = None
            if previous != payload.expected_hash:
                raise HTTPException(409, "File already exists or has changed; open it before replacing it")
            with workspace.directory(project, parent) as fd:
                temporary = f".upload-{uuid4().hex}"
                try:
                    leaf = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=fd)
                    with os.fdopen(leaf, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:
                        pass
            workspace.bump(conn, project)
        return {"path": payload.path, "hash": digest(data)}

    @router.delete(prefix + "/file")
    def delete_file(project: UUID, path: str, expected_hash: str):
        parent, name = file_path(path)
        with workspace.connect() as conn:
            settings = Settings(**workspace.settings(conn, project, lock=True)["settings"])
            workspace.ensure_not_deleting(conn, project, path)
            if digest(workspace.read(project, path, settings.max_file_bytes)) != expected_hash:
                raise HTTPException(409, "File changed; open it again before deleting")
            with workspace.directory(project, parent) as fd:
                os.unlink(name, dir_fd=fd)
            workspace.bump(conn, project)
        return {"message": "Source file deleted. Process the folder to remove indexed chunks."}

    @router.post(prefix + "/jobs", status_code=202)
    def queue(project: UUID, payload: ProcessInput, request: Request):
        with workspace.connect() as conn:
            workspace.settings(conn, project, lock=True)
            if conn.execute("SELECT id FROM ka.rag_folders WHERE project_id=%s AND id=%s AND deleted_at IS NULL", (project, payload.folder_id)).fetchone() is None:
                raise HTTPException(404, "Unknown folder")
            if conn.execute("SELECT id FROM ka.jobs WHERE project_id=%s AND kind='rag_folder_index' AND status IN ('queued','running')", (project,)).fetchone():
                raise HTTPException(409, "A processing job is already active for this workspace")
            actor = request.headers.get("x-rag-actor")
            row = conn.execute("INSERT INTO ka.jobs(project_id,kind,rag_folder_id,requested_by,input) VALUES (%s,'rag_folder_index',%s,%s,%s) RETURNING id", (project, payload.folder_id, UUID(actor) if actor else None, Jsonb(payload.model_dump(mode="json")))).fetchone()
            return row

    @router.get(prefix + "/jobs")
    def jobs(project: UUID):
        with workspace.connect() as conn:
            return {"jobs": conn.execute("""SELECT j.id,j.status,j.input,j.result,j.error,j.created_at,j.started_at,j.finished_at,d.path,d.deleted_at AS folder_deleted_at
                         FROM ka.jobs j JOIN ka.rag_folders d ON d.id=j.rag_folder_id WHERE j.project_id=%s AND j.kind='rag_folder_index'
                         ORDER BY j.created_at DESC LIMIT 100""", (project,)).fetchall()}

    @router.post(prefix + "/jobs/{job_id}/cancel")
    def cancel(project: UUID, job_id: UUID):
        with workspace.connect() as conn:
            row = conn.execute("""UPDATE ka.jobs SET status='cancelled',finished_at=now(),worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                                WHERE id=%s AND project_id=%s AND kind='rag_folder_index' AND status IN ('queued','running') RETURNING id""", (job_id, project)).fetchone()
            if row is None:
                raise HTTPException(409, "Job is already finished or does not exist")
            return row

    @router.post("/search")
    def search_route(payload: SearchInput):
        return search(workspace, payload, transport)


    return router
