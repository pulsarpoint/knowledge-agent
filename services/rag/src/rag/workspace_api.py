"""Authenticated backoffice API; area roots come from the deployment, never from input.

Sources own their folders: they add paths to the collection they choose and remove them again.
Administrators change search settings, refresh, detach unowned paths and remove unowned collections.
"""

import hmac
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.collections import AREAS, WRITABLE_AREA, FolderMissing, indexable, overlaps, refuse_hidden, relative_path, source_path
from rag.workspace import Settings, Workspace, digest
from rag.workspace_models import embed
from rag.workspace_search import SearchInput, search


JobStatus = Literal["queued", "running", "waiting_input", "succeeded", "failed", "cancelled"]
OwnerKind = Literal["documents", "jira", "code_project"]
CollectionName = Annotated[str, Path(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=60)]
OWNER_LABELS = {"documents": "Documents", "jira": "Jira", "code_project": "Code project"}


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    settings: Settings
    embedding_model_id: UUID | None = None


GlobPattern = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^/\\\x00][^\\\x00]*$")]


class CollectionSettings(BaseModel):
    """Search settings. A collection's name never changes, and its paths come from sources."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field("", max_length=2000)
    search_mode: Literal["full_text", "embedding", "hybrid"] = "full_text"
    embeddings_enabled: bool = False
    include: list[GlobPattern] = Field(default_factory=lambda: ["**/*.md", "**/*.markdown", "**/*.txt"], min_length=1, max_length=50)
    exclude: list[GlobPattern] = Field(default_factory=list, max_length=50)
    chunk_size: int = Field(2000, ge=200, le=12000)
    chunk_overlap: int = Field(200, ge=0, le=2000)
    revision: int

    @model_validator(mode="after")
    def valid(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.search_mode == "embedding" and not self.embeddings_enabled:
            raise ValueError("Turn on embeddings for semantic search, or choose keyword or hybrid search")
        return self


class EnsureInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str | None = Field(None, max_length=2000)


class PathInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_path: str
    owner_kind: OwnerKind | None = None
    owner_id: UUID | None = None
    owner_key: str | None = Field(None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def valid(self):
        source_path(self.source_path)
        if self.owner_kind is None and (self.owner_id is not None or self.owner_key is not None):
            raise ValueError("owner_id and owner_key need an owner_kind")
        return self


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
SETTINGS_COLUMNS = ("description", "search_mode", "embeddings_enabled", "include", "exclude", "chunk_size", "chunk_overlap")
ACTIVE = ("queued", "running", "waiting_input")


def owner_label(row: dict) -> str:
    label = OWNER_LABELS[row["owner_kind"]]
    return f"{label} ({row['owner_key']})" if row["owner_key"] else label


def owner_match(alias: str, kind: str, owner_id: UUID | None, key: str | None, *, every_key: bool = False) -> tuple[str, list]:
    """SQL matching one owner's path; `every_key` matches all keys of that owner (a whole code project)."""
    sql = f"{alias}.owner_kind=%s AND {alias}.owner_id IS NOT DISTINCT FROM %s"
    params: list = [kind, owner_id]
    if not every_key:
        sql += f" AND {alias}.owner_key IS NOT DISTINCT FROM %s"
        params.append(key)
    return sql, params


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
    return {**row, **counts, "paths": paths, "folder_present": not missing, "missing_paths": missing}


def collection_status(workspace: Workspace, conn, row: dict) -> dict:
    counts = conn.execute(STATUS_SQL, (row["id"],)).fetchone()
    job = conn.execute("""SELECT id,status,error,created_at,finished_at FROM ka.jobs
        WHERE rag_collection_id=%s ORDER BY created_at DESC LIMIT 1""", (row["id"],)).fetchone()
    paths = path_rows(workspace, conn, row["id"])
    walk = workspace.areas.walk_paths([p["source_path"] for p in paths], row["include"], row["exclude"], truncate=True)
    for path in paths:
        path["present"] = path["source_path"] not in walk.missing
    present = not walk.missing
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
    return {**row, **counts, "job": job, "paths": paths, "folder_present": present, "missing_paths": walk.missing,
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

    @router.put(prefix + "/collections/by-name/{name}")
    def ensure_collection(project: UUID, name: CollectionName, payload: EnsureInput):
        """Create the collection with default settings when missing; never changes an existing one."""
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            return collection_summary(workspace, conn, workspace.ensure_collection(conn, project, name, payload.description or ""))

    @router.put(prefix + "/collections/by-name/{name}/paths")
    def add_path(project: UUID, name: CollectionName, payload: PathInput):
        """Add a folder to the collection, creating the collection if missing. Idempotent.

        An owned path is re-pointed: the owner's previous path, here or in another collection, is
        replaced, so a source owns one path. An identical unowned path is adopted with its files.
        """
        owner = (payload.owner_kind, payload.owner_id, payload.owner_key)
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            collection = workspace.ensure_collection(conn, project, name)
            paths = workspace.paths(conn, collection["id"])
            same = next((p for p in paths if p["source_path"] == payload.source_path), None)
            if same is not None and (same["owner_kind"], same["owner_id"], same["owner_key"]) == owner:
                return collection_summary(workspace, conn, collection)
            if same is not None and (same["owner_kind"] is not None or payload.owner_kind is None):
                raise HTTPException(409, f"Path overlaps {same['source_path']} in collection {name}")
            owned = payload.owner_kind is not None
            for path in paths:
                if path is same or (owned and (path["owner_kind"], path["owner_id"], path["owner_key"]) == owner):
                    continue  # adopted below, or re-pointed below
                if overlaps(path["source_path"], payload.source_path):
                    raise HTTPException(409, f"Path overlaps {path['source_path']} in collection {name}")
            changed, removed = set(), False
            if owned:
                where, params = owner_match("p", *owner)
                rows = conn.execute(f"""DELETE FROM ka.rag_collection_paths p USING ka.rag_collections c
                    WHERE c.id=p.collection_id AND c.project_id=%s AND {where} RETURNING p.collection_id""",
                                    [project, *params]).fetchall()
                changed |= {row["collection_id"] for row in rows}
                removed = bool(rows)
            if same is not None:
                conn.execute("UPDATE ka.rag_collection_paths SET owner_kind=%s,owner_id=%s,owner_key=%s WHERE id=%s",
                             (*owner, same["id"]))
            else:
                conn.execute("""INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind,owner_id,owner_key)
                    VALUES (%s,%s,%s,%s,%s)""", (collection["id"], payload.source_path, *owner))
                changed.add(collection["id"])
            bump(conn, changed)
            if removed:
                drop_orphan_vectors(conn, project)
            workspace.areas.ensure_folder(payload.source_path)  # rag-data only; a failure rolls everything back
            return collection_summary(workspace, conn, workspace.collection(conn, project, collection["id"]))

    @router.delete(prefix + "/collections/by-name/{name}/paths")
    def remove_path(project: UUID, name: CollectionName, source_path: str | None = Query(None, max_length=500),
                    owner_kind: OwnerKind | None = None, owner_id: UUID | None = None,
                    owner_key: str | None = Query(None, max_length=500)):
        """Remove one path and its indexed files; files on disk stay. Idempotent."""
        if (source_path is None) == (owner_kind is None):
            raise HTTPException(400, "Give either source_path or owner_kind")
        with workspace.connect() as conn:
            row = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND name=%s AND deleted_at IS NULL FOR UPDATE",
                               (project, name)).fetchone()
            if row is None:
                return {"removed": 0}
            if source_path is not None:
                target = conn.execute("SELECT * FROM ka.rag_collection_paths WHERE collection_id=%s AND source_path=%s",
                                      (row["id"], source_path)).fetchone()
                if target is None:
                    return {"removed": 0}
                if target["owner_kind"] is not None:
                    raise HTTPException(409, f"Path {source_path} belongs to {owner_label(target)}; switch it off there")
                where, params = "p.id=%s", [target["id"]]
            else:
                where, params = owner_match("p", owner_kind, owner_id, owner_key, every_key=owner_key is None)
            removed = conn.execute(f"DELETE FROM ka.rag_collection_paths p WHERE p.collection_id=%s AND {where}",
                                   [row["id"], *params]).rowcount
            if removed:
                bump(conn, {row["id"]})
                drop_orphan_vectors(conn, project)
            return {"removed": removed}

    @router.delete(prefix + "/paths")
    def remove_owner_paths(project: UUID, owner_kind: OwnerKind, owner_id: UUID | None = None,
                           owner_key: str | None = Query(None, max_length=500)):
        """Remove a source's paths from every collection, e.g. when a code project stops being searchable."""
        where, params = owner_match("p", owner_kind, owner_id, owner_key, every_key=owner_key is None)
        with workspace.connect() as conn:
            rows = conn.execute(f"""DELETE FROM ka.rag_collection_paths p USING ka.rag_collections c
                WHERE c.id=p.collection_id AND c.project_id=%s AND {where} RETURNING p.collection_id""",
                                [project, *params]).fetchall()
            if rows:
                bump(conn, {row["collection_id"] for row in rows})
                drop_orphan_vectors(conn, project)
        return {"removed": len(rows)}

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
            owned = conn.execute("""SELECT * FROM ka.rag_collection_paths WHERE collection_id=%s AND owner_kind IS NOT NULL
                ORDER BY owner_kind, owner_key""", (collection_id,)).fetchall()
            if owned:
                raise HTTPException(409, "Remove this collection's sources first: " + ", ".join(dict.fromkeys(owner_label(r) for r in owned)))
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
        sources = [p["source_path"] for p in workspace.paths(conn, row["id"])]
        missing = workspace.areas.walk_paths(sources, row["include"], row["exclude"], truncate=True).missing
        if missing:
            raise FolderMissing(missing[0])  # 409 before anything is queued
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

    @router.get(prefix + "/file")
    def get_file(project: UUID, collection: str, path: str):
        path = relative_path(path)
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            row = workspace.collection_by_name(conn, project, collection)
            roots = [p["source_path"] for p in workspace.paths(conn, row["id"])]
        root = next((r for r in roots if path.startswith(r + "/")), None)
        # Serve only what sync would index: under one of the collection's paths, never .env*, hidden or
        # skipped folders, or files its patterns (matched relative to that path) leave out.
        if root is None or not indexable(path[len(root) + 1:], row["include"], row["exclude"]):
            raise HTTPException(404, "Document not found")
        area, _, rest = path.partition("/")
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
            # Escape LIKE wildcards so the search text matches literally.
            pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
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
