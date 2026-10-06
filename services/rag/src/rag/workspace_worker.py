"""Durable incremental sync of one collection, published in one transaction.

Every file the walk finds gets a decision in the collection's file log. A file whose size, mtime and
processing fingerprint equal its log row is not opened; one whose content hash and fingerprint match
is not parsed. A file that cannot be read or parsed is logged and the sync continues. Collection-level
problems (a missing folder, limits, a settings change, an unavailable embedding model) fail the sync,
publish nothing and leave the log unchanged.
"""

import json
import logging
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from rag import file_log
from rag.chunking import chunk_sections
from rag.collections import MAX_BYTES, Walk
from rag.structure import Section, parse_structure
from rag.workspace import Settings, Workspace, digest
from rag.workspace_models import embed, embedding_profile

logger = logging.getLogger(__name__)


def claim(workspace: Workspace):
    with workspace.connect() as conn:
        conn.execute("""UPDATE ka.jobs SET status='failed',finished_at=now(),error='Worker lease expired; refresh the collection again',
                     worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                     WHERE kind='rag_collection_sync' AND status='running' AND lease_expires_at < now()""")
        row = conn.execute("""SELECT * FROM ka.jobs WHERE kind='rag_collection_sync' AND status='queued'
                            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if row is None:
            return None
        job = conn.execute("""UPDATE ka.jobs SET status='running',started_at=now(),attempt=attempt+1,
                            worker_id='rag-worker',lease_token=%s,lease_expires_at=now()+interval '2 minutes'
                            WHERE id=%s RETURNING *""", (uuid4(), row["id"])).fetchone()
        # The sync covers the collection settings seen when it starts; a later edit fails it.
        collection = conn.execute("SELECT revision FROM ka.rag_collections WHERE id=%s", (job["rag_collection_id"],)).fetchone()
        return {**job, "collection_revision": collection["revision"]}


def heartbeat(workspace, job, stats):
    with workspace.connect() as conn:
        row = conn.execute("""UPDATE ka.jobs SET lease_expires_at=now()+interval '2 minutes',result=%s
                            WHERE id=%s AND status='running' AND lease_token=%s AND lease_expires_at>now() RETURNING id""",
                           (Jsonb(stats), job["id"], job["lease_token"])).fetchone()
        if row is None:
            raise HTTPException(409, "Processing was cancelled or its lease expired")


class CollectionSettings(BaseModel):
    chunk_size: int
    chunk_overlap: int


STATS = ("hashed_files", "skipped_files", "unopened_files", "parsed_files", "failed_files", "embedded_chunks",
         "reused_vectors", "removed_entries", "skipped_binary", "skipped_large", "skipped_links", "skipped_special",
         "skipped_unreadable", "skipped_invalid_names")
CHANGED = "Files changed while syncing; refresh the collection again"


def decode(data: bytes) -> str | None:
    """UTF-8 text, or None for binary content (PostgreSQL text cannot hold NUL)."""
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeError:
        return None


def parse_source(path: str, text: str, settings: CollectionSettings) -> list[str]:
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except ValueError:
            pass  # invalid JSON is indexed as plain text
    title = ""
    if suffix in {".md", ".markdown"}:
        doc = parse_structure(text)
        title = next((heading.text for heading in doc.headings if heading.level == 1 and heading.is_root), "")
        sections = doc.sections or [Section("", text)]
    else:
        sections = [Section("", text)]
    return [(title + "\n" + (section.heading_path if section.heading_path != "(intro)" else "") + "\n" + section.text).strip()
            for section in chunk_sections(sections, settings.chunk_size, settings.chunk_overlap)
            if section.text.strip()]


def read(workspace: Workspace, path: str, limit: int) -> bytes:
    area, _, rest = path.partition("/")
    return workspace.areas.read(area, rest, limit)


def decide(workspace: Workspace, walk: Walk, log: dict, current: str, limit: int, force: bool,
           stats: dict) -> tuple[dict[str, dict], list[dict]]:
    """The log decision for every walked file, and the text files that need processing.

    A file matching its log row (size, mtime, fingerprint) is not opened; a file whose content hash and
    fingerprint match its row is not parsed. Rebuild (`force`) reads and processes every file.
    """
    decisions, sources, total = {}, [], 0
    for entry in walk.files:
        path, old = entry["path"], log.get(entry["path"])
        decision = {"source": entry["source"], "size": entry["size"], "mtime": entry["mtime"],
                    "content_hash": None, "status": "indexed", "error": None}
        if not force and file_log.unchanged(entry, old, current):
            decision.update(content_hash=old["content_hash"], status=old["status"], error=old["error"])
            decisions[path] = decision
            stats["skipped_files"] += 1
            stats["unopened_files"] += 1
            continue
        if entry["size"] > limit:
            decisions[path] = {**decision, "status": "skipped_too_large"}
            stats["skipped_large"] += 1
            continue
        try:
            data = read(workspace, path, limit)
        except FileNotFoundError:
            continue  # deleted after the walk; the publish re-check fails the sync
        except (OSError, HTTPException) as error:
            message = error.detail if isinstance(error, HTTPException) else f"Cannot read this file: {error.strerror or error}"
            decisions[path] = {**decision, "status": "skipped_unreadable", "error": str(message)[:2000]}
            stats["skipped_unreadable"] += 1
            continue
        total += len(data)
        if total > MAX_BYTES:
            raise HTTPException(400, "A sync is limited to 200 MB of text; narrow the collection's include patterns")
        decision["content_hash"] = digest(data)
        decisions[path] = decision
        stats["hashed_files"] += 1
        text = decode(data)
        if text is None:
            decision["status"] = "skipped_binary"
            stats["skipped_binary"] += 1
        elif (not force and old is not None and old["status"] != "removed"
              and old["content_hash"] == decision["content_hash"] and old["fingerprint"] == current):
            decision.update(status=old["status"], error=old["error"])  # touched, same content: not parsed
            stats["skipped_files"] += 1
        else:
            sources.append({"path": path, "source": entry["source"], "text": text, "content_hash": decision["content_hash"]})
    return decisions, sources


def recheck(workspace: Workspace, collection: dict, sources: list[str], walk: Walk, decisions: dict, limit: int) -> None:
    """Refuse to publish when a file changed after the first walk.

    Sizes and mtimes are compared with the first walk; only files whose size or mtime moved are read
    again and hashed against the hash this sync used (the logged hash for files it did not open).
    """
    again = workspace.areas.walk_paths(sources, collection["include"], collection["exclude"])
    before = {entry["path"]: (entry["size"], entry["mtime"]) for entry in walk.files}
    after = {entry["path"]: (entry["size"], entry["mtime"]) for entry in again.files}
    if before.keys() != after.keys():
        raise HTTPException(409, CHANGED)
    for path, stat in after.items():
        if stat == before[path]:
            continue
        used = decisions.get(path, {}).get("content_hash")
        try:
            now = digest(read(workspace, path, limit))
        except (OSError, HTTPException):
            now = None
        if used is None or now != used:
            raise HTTPException(409, CHANGED)


def process(workspace: Workspace, job: dict, transport=None):
    stats = dict.fromkeys(STATS, 0)
    project = job["project_id"]
    force = bool(job["input"].get("force"))
    try:
        with workspace.connect() as conn:
            config = workspace.settings(conn, project, lock=False)
            limit = Settings(**config["settings"]).max_file_bytes
            try:
                collection = workspace.collection(conn, project, job["rag_collection_id"])
            except HTTPException:
                raise HTTPException(409, "Collection was deleted") from None
            revision = job.get("collection_revision", collection["revision"])
            if collection["revision"] != revision:
                raise HTTPException(409, "Settings changed while syncing; refresh the collection again")
            path_ids = {row["source_path"]: row["id"] for row in workspace.paths(conn, collection["id"])}
            model = workspace.embedding_model(conn, config["embedding_model_id"]) if collection["embeddings_enabled"] else None
            existing = conn.execute("SELECT * FROM ka.rag_files WHERE collection_id=%s", (collection["id"],)).fetchall()
            log = file_log.load(conn, collection["id"])
        settings = CollectionSettings(chunk_size=collection["chunk_size"], chunk_overlap=collection["chunk_overlap"])
        processing = digest({"parser": "markdown-text-json-v3", "chunker": "sections-v1",
                             "size": settings.chunk_size, "overlap": settings.chunk_overlap})
        profile = embedding_profile(model)
        log_fingerprint = file_log.fingerprint(processing, profile, collection["include"], collection["exclude"], limit)
        walk = workspace.areas.walk_paths(list(path_ids), collection["include"], collection["exclude"])  # FolderMissing, limits
        for key in ("links", "special", "unreadable", "invalid_names"):
            stats["skipped_" + key] = walk.skipped[key]
        decisions, sources = decide(workspace, walk, log, log_fingerprint, limit, force, stats)
        previous = {row["path"]: row for row in existing}
        updates, vectors, reused = [], {}, set()
        for source in sources:
            old = previous.get(source["path"])
            same = (old is not None and old["content_hash"] == source["content_hash"]
                    and old["processing_hash"] == processing and not force)
            if same and old["embedding_profile"] == profile:
                stats["skipped_files"] += 1
                continue
            if same:
                with workspace.connect() as conn:
                    texts = [row["text"] for row in conn.execute("SELECT text FROM ka.rag_chunks WHERE file_id=%s ORDER BY position", (old["id"],)).fetchall()]
            else:
                try:
                    texts = parse_source(source["path"], source["text"], settings)
                except Exception as error:  # noqa: BLE001 - one file that cannot be parsed never fails the collection
                    decisions[source["path"]].update(status="failed", error=f"Could not parse this file: {error}"[:2000])
                    stats["failed_files"] += 1
                    continue
                stats["parsed_files"] += 1
            chunks = [{"text": text, "input_hash": digest(text)} for text in texts]
            heartbeat(workspace, job, stats)
            if profile:
                missing = {}
                with workspace.connect() as conn:
                    for chunk in chunks:
                        value = chunk["input_hash"]
                        if value in vectors:
                            stats["reused_vectors"] += 1
                        elif not force and conn.execute("SELECT 1 FROM ka.rag_vectors WHERE project_id=%s AND profile=%s AND input_hash=%s", (project, profile, value)).fetchone():
                            stats["reused_vectors"] += 1
                            reused.add(value)
                        else:
                            missing[value] = chunk["text"]
                pending = list(missing.items())
                # Titan takes one text per request; renew the lease between requests.
                batch_size = 1 if model.provider == 'bedrock' else 32
                for offset in range(0, len(pending), batch_size):
                    batch = pending[offset:offset + batch_size]
                    values = embed(model, [text for _, text in batch], transport)  # a model failure fails the sync
                    vectors.update(zip([value for value, _ in batch], values, strict=True))
                    stats["embedded_chunks"] += len(values)
                    heartbeat(workspace, job, stats)
            updates.append((source, chunks))
        # The collection row lock serializes publication with collection edits and
        # deletion. A second walk catches edits made directly on the host.
        with workspace.connect() as conn:
            current = conn.execute("SELECT revision FROM ka.rag_collections WHERE id=%s AND deleted_at IS NULL FOR UPDATE", (collection["id"],)).fetchone()
            running = conn.execute("SELECT id FROM ka.jobs WHERE id=%s AND status='running' AND lease_token=%s AND lease_expires_at>now() FOR UPDATE", (job["id"], job["lease_token"])).fetchone()
            if running is None:
                raise HTTPException(409, "Sync was cancelled or its lease expired")
            # The workspace revision also moves when the file size limit or the embedding model changes.
            # Every publish and collection deletion takes the workspace lock before
            # vector cleanup, so reused vectors checked here stay until commit.
            settings_revision = workspace.settings(conn, project, lock=True)["revision"]
            if current is None or current["revision"] != revision or settings_revision != config["revision"]:
                raise HTTPException(409, "Settings changed while syncing; refresh the collection again")
            if reused:
                present = conn.execute("SELECT count(*) AS n FROM ka.rag_vectors WHERE project_id=%s AND profile=%s AND input_hash=ANY(%s)",
                                       (project, profile, list(reused))).fetchone()["n"]
                if present != len(reused):
                    raise HTTPException(409, "Embeddings changed while syncing; refresh the collection again")
            recheck(workspace, collection, list(path_ids), walk, decisions, limit)
            for value, vector in vectors.items():
                conn.execute("INSERT INTO ka.rag_vectors(project_id,profile,input_hash,embedding) VALUES (%s,%s,%s,%s::public.vector) ON CONFLICT (project_id,profile,input_hash) DO UPDATE SET embedding=EXCLUDED.embedding", (project, profile, value, json.dumps(vector)))
            for source, chunks in updates:
                row = conn.execute("""INSERT INTO ka.rag_files(project_id,collection_id,path_id,path,content_hash,processing_hash,embedding_profile,job_id)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (collection_id,path) DO UPDATE
                                    SET path_id=EXCLUDED.path_id,content_hash=EXCLUDED.content_hash,processing_hash=EXCLUDED.processing_hash,
                                    embedding_profile=EXCLUDED.embedding_profile,job_id=EXCLUDED.job_id,indexed_at=now() RETURNING id""",
                                   (project, collection["id"], path_ids[source["source"]], source["path"], source["content_hash"],
                                    processing, profile, job["id"])).fetchone()
                conn.execute("DELETE FROM ka.rag_chunks WHERE file_id=%s", (row["id"],))
                for position, chunk in enumerate(chunks):
                    conn.execute("INSERT INTO ka.rag_chunks(project_id,file_id,position,text,input_hash) VALUES (%s,%s,%s,%s,%s)", (project, row["id"], position, chunk["text"], chunk["input_hash"]))
            # Only files whose decision is indexed stay searchable; skipped and failed files leave the index.
            indexed = {path for path, decision in decisions.items() if decision["status"] == "indexed"}
            for old in existing:
                if old["path"] not in indexed:
                    conn.execute("DELETE FROM ka.rag_files WHERE id=%s", (old["id"],))
                    stats["removed_entries"] += 1
            file_log.record(conn, collection["id"], path_ids, decisions, log_fingerprint)
            conn.execute("""DELETE FROM ka.rag_vectors v WHERE v.project_id=%s AND NOT EXISTS (
                            SELECT 1 FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id
                            WHERE c.project_id=v.project_id AND c.input_hash=v.input_hash AND f.embedding_profile=v.profile)""", (project,))
            conn.execute("UPDATE ka.rag_collections SET last_synced_at=now(),synced_revision=%s WHERE id=%s", (revision, collection["id"]))
            conn.execute("""UPDATE ka.jobs SET status='succeeded',finished_at=now(),result=%s,
                         worker_id=NULL,lease_token=NULL,lease_expires_at=NULL WHERE id=%s""", (Jsonb(stats), job["id"]))
        return stats
    except Exception as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else "Sync failed; the previous index remains available. Check the collection's files and service health."
        with workspace.connect() as conn:
            conn.execute("""UPDATE ka.jobs SET status='failed',finished_at=now(),error=%s,result=%s,
                         worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                         WHERE id=%s AND status='running' AND lease_token=%s""", (message, Jsonb(stats), job["id"], job["lease_token"]))
        return None


def main():
    logging.basicConfig(level=logging.INFO)
    workspace = Workspace.from_env()
    while True:
        try:
            job = claim(workspace)
            if job is not None:
                process(workspace, job)
                continue
        except (psycopg.Error, OSError):
            logger.error("RAG worker cannot reach its database or collection folders")
        time.sleep(2)
