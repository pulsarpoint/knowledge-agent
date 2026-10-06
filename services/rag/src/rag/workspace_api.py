"""Authenticated backoffice API; area roots come from the deployment, never from input.

RAG owns the configuration: administrators and external tools create collections and connect
folders under DATA_ROOT to them. Sources only write files into their folders.
"""

import hmac
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag import file_log
from rag.collections import (AREAS, WRITABLE_AREA, FolderMissing, indexable, overlaps, refuse_hidden, relative_path,
                             source_path, visible)
from rag.workspace import Settings, Workspace, digest
from rag.workspace_models import embed
from rag.workspace_search import SearchInput, search


JobStatus = Literal["queued", "running", "waiting_input", "succeeded", "failed", "cancelled"]


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    settings: Settings
    embedding_model_id: UUID | None = None


GlobPattern = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^/\\\x00][^\\\x00]*$")]


CollectionName = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=60)]


class CollectionFields(BaseModel):
    """Settings shared by create and edit. A collection's name never changes."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field("", max_length=2000)
    search_mode: Literal["full_text", "embedding", "hybrid"] = "full_text"
    embeddings_enabled: bool = False
    include: list[GlobPattern] = Field(default_factory=lambda: ["**/*.md", "**/*.markdown", "**/*.txt"], min_length=1, max_length=50)
    exclude: list[GlobPattern] = Field(default_factory=list, max_length=50)
    chunk_size: int = Field(2000, ge=200, le=12000)
    chunk_overlap: int = Field(200, ge=0, le=2000)

    @model_validator(mode="after")
    def valid(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.search_mode == "embedding" and not self.embeddings_enabled:
            raise ValueError("Turn on embeddings for semantic search, or choose keyword or hybrid search")
        return self


class NewCollection(CollectionFields):
    name: CollectionName


class CollectionSettings(CollectionFields):
    revision: int


class FolderInput(BaseModel):
    """A folder under DATA_ROOT to connect; validated with source_path() in the route."""
    model_config = ConfigDict(extra="forbid")
    source_path: str = Field(max_length=500)


class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False


class FileInput(BaseModel):
    """A file under rag-data, addressed by its full DATA_ROOT path (the Jira export writes here)."""
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str = Field(max_length=10_000_000)
    expected_hash: str | None = None


STATUS_SQL = """SELECT count(DISTINCT f.id) AS indexed_files, count(c.id) AS chunks,
    count(v.input_hash) AS embedded_chunks
    FROM ka.rag_files f LEFT JOIN ka.rag_chunks c ON c.file_id=f.id
    LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile
        AND v.input_hash=c.input_hash AND f.embedding_profile<>''
    WHERE f.collection_id=%s"""
ORPHAN_VECTORS_SQL = """DELETE FROM ka.rag_vectors v WHERE v.project_id=%s AND NOT EXISTS (
    SELECT 1 FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id
    WHERE c.project_id=v.project_id AND c.input_hash=v.input_hash AND f.embedding_profile=v.profile)"""
LOG_COUNTS_SQL = """SELECT count(*) FILTER (WHERE status='indexed') AS indexed,
    count(*) FILTER (WHERE status IN ('skipped_binary','skipped_too_large','skipped_unreadable')) AS skipped,
    count(*) FILTER (WHERE status='failed') AS failed
    FROM ka.rag_file_log WHERE collection_id=%s"""
SETTINGS_COLUMNS = ("description", "search_mode", "embeddings_enabled", "include", "exclude", "chunk_size", "chunk_overlap")
ACTIVE = ("queued", "running", "waiting_input")


def contains(text: str) -> str:
    """A LIKE pattern matching `text` anywhere, with LIKE wildcards escaped so they match literally."""
    return "%" + text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def bump(conn, collection_ids: set) -> None:
    """A path change makes the collection show Needs refresh and fails a sync that is running."""
    if collection_ids:
        conn.execute("UPDATE ka.rag_collections SET revision=revision+1,updated_at=now() WHERE id=ANY(%s)", (list(collection_ids),))


def path_rows(workspace: Workspace, conn, collection_id: UUID) -> list[dict]:
    """The collection's paths with indexed file counts, host paths and presence; no walk."""
    rows = conn.execute("""SELECT p.*, count(f.id) AS files FROM ka.rag_collection_paths p
        LEFT JOIN ka.rag_files f ON f.path_id=p.id WHERE p.collection_id=%s
        GROUP BY p.id ORDER BY p.source_path""", (collection_id,)).fetchall()
    return [{**row, "host_path": workspace.areas.host_path(row["source_path"]),
             "container_path": str(workspace.areas.container_path(row["source_path"])),
             "present": workspace.areas.present(row["source_path"])} for row in rows]


def collection_summary(workspace: Workspace, conn, row: dict) -> dict:
    """Collection row, index counts and paths with presence; no folder walk."""
    counts = conn.execute(STATUS_SQL, (row["id"],)).fetchone()
    paths = path_rows(workspace, conn, row["id"])
    missing = [p["source_path"] for p in paths if not p["present"]]
    return {**row, **counts, "paths": paths, "folder_present": not missing, "missing_paths": missing,
            "log_counts": conn.execute(LOG_COUNTS_SQL, (row["id"],)).fetchone()}


def collection_status(workspace: Workspace, conn, row: dict) -> dict:
    counts = conn.execute(STATUS_SQL, (row["id"],)).fetchone()
    job = conn.execute("""SELECT id,status,error,created_at,finished_at FROM ka.jobs
        WHERE rag_collection_id=%s ORDER BY created_at DESC LIMIT 1""", (row["id"],)).fetchone()
    paths = path_rows(workspace, conn, row["id"])  # presence per path, never from the capped walk below
    missing = [p["source_path"] for p in paths if not p["present"]]
    present = not missing
    walk = workspace.areas.walk_paths([p["source_path"] for p in paths if p["present"]], row["include"], row["exclude"], truncate=True)
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
    return {**row, **counts, "job": job, "paths": paths, "folder_present": present, "missing_paths": missing,
            "log_counts": conn.execute(LOG_COUNTS_SQL, (row["id"],)).fetchone(),
            "folder_changed_at": changed, "file_count": len(walk.files), "truncated": walk.truncated,
            "skipped": walk.skipped, "needs_refresh": bool(needs), "status": state}


def build_api(workspace: Workspace, key: str, transport=None):
    def authenticate(request: Request):
        if not key or not hmac.compare_digest(request.headers.get("x-rag-key", ""), key):
            raise HTTPException(403, "RAG service authentication required")
    router = APIRouter(dependencies=[Depends(authenticate)])
    prefix = "/backoffice/projects/{project}"

    def drop_orphan_vectors(conn, project: UUID) -> None:
        # Lock order collection -> workspace matches the worker's publish, which
        # re-checks reused vectors under the workspace lock.
        workspace.settings(conn, project, lock=True)
        conn.execute(ORPHAN_VECTORS_SQL, (project,))

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
    def list_collections(project: UUID, status: bool = True):
        """status=false skips folder walks: for callers that only need names, folders and index counts."""
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            rows = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name", (project,)).fetchall()
            describe = collection_status if status else collection_summary
            return {"collections": [describe(workspace, conn, row) for row in rows]}

    @router.post(prefix + "/collections", status_code=201)
    def create_collection(project: UUID, payload: NewCollection):
        """Create an empty collection with its settings; folders are connected with POST collections/{id}/paths."""
        columns = ["project_id", "name", *SETTINGS_COLUMNS]
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            row = conn.execute(f"""INSERT INTO ka.rag_collections({','.join(columns)})
                VALUES ({','.join(['%s'] * len(columns))})
                ON CONFLICT (project_id,name) WHERE deleted_at IS NULL DO NOTHING RETURNING *""",
                [project, payload.name, *(getattr(payload, c) for c in SETTINGS_COLUMNS)]).fetchone()
            if row is None:
                raise HTTPException(409, f"Collection {payload.name} already exists")
            return collection_summary(workspace, conn, row)

    @router.post(prefix + "/collections/{collection_id}/paths")
    def add_folder(project: UUID, collection_id: UUID, payload: FolderInput):
        """Connect a folder. A missing folder under rag-data is created; elsewhere it must exist."""
        new = source_path(payload.source_path)
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)  # serializes with edits and syncs
            for path in workspace.paths(conn, collection_id):
                if overlaps(path["source_path"], new):
                    raise HTTPException(409, f"Folder overlaps {path['source_path']} in collection {row['name']}")
            if not workspace.areas.present(new):
                if not workspace.areas.writable(new):
                    raise HTTPException(409, "Folder does not exist")
                workspace.areas.ensure_folder(new)
            conn.execute("INSERT INTO ka.rag_collection_paths(collection_id,source_path) VALUES (%s,%s)", (collection_id, new))
            bump(conn, {collection_id})
            return collection_summary(workspace, conn, workspace.collection(conn, project, collection_id))

    @router.delete(prefix + "/collections/{collection_id}/paths/{path_id}")
    def remove_folder(project: UUID, collection_id: UUID, path_id: UUID):
        """Disconnect a folder: its indexed files (and file log rows) go; files on disk stay."""
        with workspace.connect() as conn:
            # Lock order collection -> workspace, as a publishing sync.
            workspace.collection(conn, project, collection_id, lock=True)
            removed = conn.execute("DELETE FROM ka.rag_collection_paths WHERE id=%s AND collection_id=%s",
                                   (path_id, collection_id)).rowcount
            if not removed:
                raise HTTPException(404, "Unknown folder")
            bump(conn, {collection_id})
            drop_orphan_vectors(conn, project)
            return collection_summary(workspace, conn, workspace.collection(conn, project, collection_id))

    @router.get(prefix + "/folders")
    def folders(project: UUID, path: str | None = Query(None, max_length=500)):
        """Subfolders of a DATA_ROOT folder for the folder browser; without `path`, the mounted areas."""
        if path is None:
            return {"path": None, "truncated": False,
                    "folders": [{"name": area, "path": area} for area in AREAS if area in workspace.areas.roots]}
        names, truncated = workspace.areas.subfolders(source_path(path))
        return {"path": path, "truncated": truncated, "folders": [{"name": name, "path": f"{path}/{name}"} for name in names]}

    @router.get(prefix + "/collections/{collection_id}")
    def get_collection(project: UUID, collection_id: UUID):
        with workspace.connect() as conn:
            return collection_status(workspace, conn, workspace.collection(conn, project, collection_id))

    @router.put(prefix + "/collections/{collection_id}")
    def update_collection(project: UUID, collection_id: UUID, payload: CollectionSettings):
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)
            if payload.revision != row["revision"]:
                raise HTTPException(409, "Collection changed; refresh before saving")
            row = conn.execute(f"""UPDATE ka.rag_collections SET {','.join(c + '=%s' for c in SETTINGS_COLUMNS)},
                revision=revision+1,updated_at=now() WHERE id=%s RETURNING *""",
                               (*(getattr(payload, c) for c in SETTINGS_COLUMNS), collection_id)).fetchone()
            return collection_status(workspace, conn, row)

    @router.delete(prefix + "/collections/{collection_id}")
    def delete_collection(project: UUID, collection_id: UUID, revision: int, confirmed: bool = False):
        if not confirmed:
            raise HTTPException(400, "Confirm removing this collection from search; its files stay on disk")
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)
            if revision != row["revision"]:
                raise HTTPException(409, "Collection changed; refresh before deleting")
            workspace.settings(conn, project, lock=True)
            conn.execute("""UPDATE ka.jobs SET status='cancelled',finished_at=now(),error='Collection deleted',
                worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                WHERE rag_collection_id=%s AND status IN ('queued','running','waiting_input')""", (collection_id,))
            removed = conn.execute("DELETE FROM ka.rag_files WHERE collection_id=%s", (collection_id,)).rowcount
            conn.execute("DELETE FROM ka.rag_collection_paths WHERE collection_id=%s", (collection_id,))
            conn.execute("DELETE FROM ka.agent_collections WHERE collection_id=%s", (collection_id,))
            conn.execute(ORPHAN_VECTORS_SQL, (project,))
            conn.execute("UPDATE ka.rag_collections SET deleted_at=now(),updated_at=now() WHERE id=%s", (collection_id,))
        return {"deleted_files": removed}

    def queue_sync(conn, project: UUID, row: dict, actor: str | None, force: bool):
        missing = [p["source_path"] for p in workspace.paths(conn, row["id"]) if not workspace.areas.present(p["source_path"])]
        if missing:
            raise FolderMissing(missing[0])  # 409 before anything is queued; no walk
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
            sources = [p["source_path"] for p in workspace.paths(conn, collection_id)]
            indexed = {r["path"]: r for r in conn.execute("""SELECT f.path,f.indexed_at,f.content_hash,count(c.id) AS chunks,
                count(v.input_hash) AS embedded_chunks FROM ka.rag_files f LEFT JOIN ka.rag_chunks c ON c.file_id=f.id
                LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile AND v.input_hash=c.input_hash AND f.embedding_profile<>''
                WHERE f.collection_id=%s GROUP BY f.id""", (collection_id,)).fetchall()}
        walk = workspace.areas.walk_paths(sources, row["include"], row["exclude"], truncate=True)
        files = []
        for entry in walk.files:
            known = indexed.pop(entry["path"], None)
            changed = datetime.fromtimestamp(entry["mtime"], timezone.utc)
            files.append({**entry, "indexed_at": known and known["indexed_at"], "content_hash": known and known["content_hash"],
                          "chunks": known["chunks"] if known else 0, "embedded_chunks": known["embedded_chunks"] if known else 0,
                          "stale": known is None or changed > known["indexed_at"]})
        files += [{"path": path, "source": None, "size": None, "mtime": None,
                   **{k: known[k] for k in ("indexed_at", "content_hash", "chunks", "embedded_chunks")},
                   "stale": True} for path, known in indexed.items()]  # indexed but gone from disk
        return {"files": sorted(files, key=lambda f: f["path"]), "truncated": walk.truncated, "skipped": walk.skipped,
                "missing_paths": walk.missing}

    @router.get(prefix + "/collections/{collection_id}/chunks")
    def collection_chunks(project: UUID, collection_id: UUID, path: str):
        with workspace.connect() as conn:
            workspace.collection(conn, project, collection_id)
            return {"chunks": conn.execute("""SELECT c.position,c.text,(v.input_hash IS NOT NULL) AS embedded
                FROM ka.rag_files f JOIN ka.rag_chunks c ON c.file_id=f.id
                LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile AND v.input_hash=c.input_hash AND f.embedding_profile<>''
                WHERE f.collection_id=%s AND f.path=%s ORDER BY c.position""", (collection_id, relative_path(path))).fetchall()}

    @router.get(prefix + "/collections/{collection_id}/log")
    def collection_log(project: UUID, collection_id: UUID, status: Literal["indexed", "skipped", "failed", "removed"] | None = None,
                       q: str | None = Query(None, max_length=200), limit: int = Query(25, ge=1, le=100),
                       offset: int = Query(0, ge=0)):
        """The collection's file log by path; without `status`, every file except removed ones."""
        where = ["l.collection_id=%s", "l.status = ANY(%s)"]
        params: list = [collection_id, list(file_log.GROUPS[status] if status else file_log.PRESENT)]
        if q and q.strip():
            where.append("l.path ILIKE %s")
            params.append(contains(q))
        scope = "FROM ka.rag_file_log l WHERE " + " AND ".join(where)
        with workspace.connect() as conn:
            workspace.collection(conn, project, collection_id)
            total = conn.execute("SELECT count(*) AS n " + scope, params).fetchone()["n"]
            files = conn.execute("""SELECT l.path,l.status,l.size,l.mtime,l.error,l.indexed_at,l.last_seen_at,l.removed_at,
                (SELECT count(*) FROM ka.rag_files f JOIN ka.rag_chunks c ON c.file_id=f.id
                 WHERE f.collection_id=l.collection_id AND f.path=l.path) AS chunks """ + scope
                + ' ORDER BY l.path COLLATE "C" LIMIT %s OFFSET %s', [*params, limit, offset]).fetchall()
        return {"files": files, "total": total}

    @router.get(prefix + "/file")
    def get_file(project: UUID, path: str, collection: str | None = None):
        """An indexable file of `collection` by its full path. Without `collection`, a visible file under
        rag-data/<folder>/ (the Jira export reads its previous ticket file this way)."""
        path = relative_path(path)
        area, _, rest = path.partition("/")
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            if collection is None:
                allowed = area == WRITABLE_AREA and "/" in rest and visible(rest)
            else:
                row = workspace.collection_by_name(conn, project, collection)
                roots = [p["source_path"] for p in workspace.paths(conn, row["id"])]
                root = next((r for r in roots if path.startswith(r + "/")), None)
                # Only what sync would index: under one of the collection's folders, never .env*, hidden or
                # skipped folders, or files its patterns (matched relative to that folder) leave out.
                allowed = root is not None and indexable(path[len(root) + 1:], row["include"], row["exclude"])
        if not allowed:
            raise HTTPException(404, "Document not found")
        try:
            data = workspace.areas.read(area, rest, limit)
        except FileNotFoundError:
            raise HTTPException(404, "Document not found") from None
        try:
            content = data.decode("utf-8-sig")
        except UnicodeError:
            raise HTTPException(400, "File is not UTF-8 text") from None
        return {"collection": collection, "path": path, "content": content, "hash": digest(data)}

    @router.put(prefix + "/file")
    def put_file(project: UUID, payload: FileInput):
        path = relative_path(payload.path)
        area, _, rest = path.partition("/")
        if area != WRITABLE_AREA or "/" not in rest:
            raise HTTPException(400, "Only files inside a rag-data folder can be written; other folders are read-only")
        refuse_hidden(rest)
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
        data = payload.content.encode("utf-8")
        if len(data) > limit:
            raise HTTPException(400, "File exceeds the configured size limit")
        try:
            previous = digest(workspace.areas.read(area, rest, limit))
        except FileNotFoundError:
            previous = None
        if previous != payload.expected_hash:
            raise HTTPException(409, "File already exists or has changed; open it before replacing it")
        workspace.areas.write(area, rest, data)
        return {"path": path, "hash": digest(data)}

    @router.get(prefix + "/jobs")
    def jobs(project: UUID, status: JobStatus | None = None, collection: str | None = Query(None, max_length=500),
             q: str | None = Query(None, max_length=200), limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
        where = ["j.project_id=%s", "j.kind IN ('rag_collection_sync','rag_folder_index')"]
        params: list = [project]
        if status is not None:
            where.append("j.status=%s")
            params.append(status)
        if collection:
            where.append("coalesce(k.name, d.path)=%s")
            params.append(collection)
        if q and q.strip():
            pattern = contains(q)
            where.append("(coalesce(k.name, d.path) ILIKE %s OR coalesce(j.error,'') ILIKE %s)")
            params.extend([pattern, pattern])
        scope = """FROM ka.jobs j LEFT JOIN ka.rag_collections k ON k.id=j.rag_collection_id
                LEFT JOIN ka.rag_folders d ON d.id=j.rag_folder_id WHERE """ + " AND ".join(where)
        with workspace.connect() as conn:
            total = conn.execute("SELECT count(*) AS n " + scope, params).fetchone()["n"]
            rows = conn.execute("""SELECT j.id,j.status,j.input,j.result,j.error,j.created_at,j.started_at,j.finished_at,
                j.rag_collection_id AS collection_id, coalesce(k.name, d.path) AS collection """ + scope
                + " ORDER BY j.created_at DESC, j.id LIMIT %s OFFSET %s", [*params, limit, offset]).fetchall()
        return {"jobs": rows, "total": total}

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
