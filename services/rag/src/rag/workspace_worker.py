"""Durable incremental processing; publish the selected scope in one transaction."""

import json
import logging
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from rag.ingest import chunk_sections
from rag.structure import Section, parse_structure
from rag.workspace import Settings, Workspace, digest
from rag.workspace_models import embed, embedding_profile

logger = logging.getLogger(__name__)


def claim(workspace: Workspace):
    with workspace.connect() as conn:
        conn.execute("""UPDATE ka.jobs SET status='failed',finished_at=now(),error='Worker lease expired; retry this scope',
                     worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                     WHERE kind='rag_folder_index' AND status='running' AND lease_expires_at < now()""")
        row = conn.execute("""SELECT * FROM ka.jobs WHERE kind='rag_folder_index' AND status='queued'
                            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if row is None:
            return None
        return conn.execute("""UPDATE ka.jobs SET status='running',started_at=now(),attempt=attempt+1,
                            worker_id='rag-worker',lease_token=%s,lease_expires_at=now()+interval '2 minutes'
                            WHERE id=%s RETURNING *""", (uuid4(), row["id"])).fetchone()


def heartbeat(workspace, job, stats):
    with workspace.connect() as conn:
        row = conn.execute("""UPDATE ka.jobs SET lease_expires_at=now()+interval '2 minutes',result=%s
                            WHERE id=%s AND status='running' AND lease_token=%s AND lease_expires_at>now() RETURNING id""",
                           (Jsonb(stats), job["id"], job["lease_token"])).fetchone()
        if row is None:
            raise HTTPException(409, "Processing was cancelled or its lease expired")


def parse_source(source, settings):
    try:
        text = source["data"].decode("utf-8-sig")
        if source["kind"] == "file" and Path(source["path"]).suffix.lower() == ".json":
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except (UnicodeError, ValueError):
        raise HTTPException(400, f"Invalid UTF-8 text or JSON: {source['path']}") from None
    title = ""
    if source["kind"] == "file" and Path(source["path"]).suffix.lower() in {".md", ".markdown"}:
        doc = parse_structure(text)
        title = next((heading.text for heading in doc.headings if heading.level == 1 and heading.is_root), "")
        sections = doc.sections or [Section("", text)]
    else:
        sections = [Section("", text)]
    return [(title + "\n" + (section.heading_path if section.heading_path != "(intro)" else "") + "\n" + section.text).strip()
            for section in chunk_sections(sections, settings.chunk_size, settings.chunk_overlap)
            if section.text.strip()]


def process(workspace: Workspace, job: dict, transport=None):
    stats = {"hashed_files": 0, "skipped_files": 0, "parsed_files": 0, "updated_folders": 0,
             "embedded_chunks": 0, "reused_vectors": 0, "removed_entries": 0}
    project = job["project_id"]
    try:
        with workspace.connect() as conn:
            config = workspace.settings(conn, project, lock=True)
            settings = Settings(**config["settings"])
            model = workspace.embedding_model(conn, config['embedding_model_id']) if settings.embedding_enabled else None
            folder_row = conn.execute("SELECT path FROM ka.rag_folders WHERE id=%s AND project_id=%s AND deleted_at IS NULL", (job["rag_folder_id"], project)).fetchone()
            if folder_row is None:
                raise HTTPException(409, "Source folder was deleted")
            folder = folder_row["path"]
            sources = workspace.snapshot(conn, project, folder, job["input"]["recursive"], settings)
            existing = conn.execute("""SELECT f.* FROM ka.rag_files f JOIN ka.rag_folders d ON d.id=f.folder_id
                                    WHERE f.project_id=%s AND (d.path=%s OR (%s AND starts_with(d.path,%s)))""",
                                    (project, folder, job["input"]["recursive"], folder + "/")).fetchall()
        stats["hashed_files"] = sum(s["kind"] == "file" for s in sources)
        if not stats["hashed_files"] and any(row["kind"] == "file" for row in existing) and not job["input"]["allow_empty"]:
            raise HTTPException(409, "This run would remove every indexed file in the scope; enable Allow empty replacement to continue")
        previous = {(row["path"], row["kind"]): row for row in existing}
        fingerprint = digest({"parser": "markdown-it-text-json-v2", "chunker": "sections-v1", "size": settings.chunk_size, "overlap": settings.chunk_overlap})
        profile = embedding_profile(model)
        updates, vectors = [], {}
        for source in sources:
            old = previous.get((source["path"], source["kind"]))
            same = old is not None and old["content_hash"] == source["content_hash"] and old["processing_hash"] == fingerprint and not job["input"]["force"]
            if same and old["embedding_profile"] == profile:
                stats["skipped_files"] += int(source["kind"] == "file")
                continue
            if same:
                with workspace.connect() as conn:
                    texts = [row["text"] for row in conn.execute("SELECT text FROM ka.rag_chunks WHERE file_id=%s ORDER BY position", (old["id"],)).fetchall()]
            else:
                texts = parse_source(source, settings)
                stats["parsed_files" if source["kind"] == "file" else "updated_folders"] += 1
            chunks = [{"text": text, "input_hash": digest(text)} for text in texts]
            heartbeat(workspace, job, stats)
            if profile:
                missing = {}
                with workspace.connect() as conn:
                    for chunk in chunks:
                        value = chunk["input_hash"]
                        if value in vectors:
                            stats["reused_vectors"] += 1
                        elif not job["input"]["force"] and conn.execute("SELECT 1 FROM ka.rag_vectors WHERE project_id=%s AND profile=%s AND input_hash=%s", (project, profile, value)).fetchone():
                            stats["reused_vectors"] += 1
                        else:
                            missing[value] = chunk["text"]
                pending = list(missing.items())
                # Titan takes one text per request; renew the lease between requests.
                batch_size = 1 if model.provider == 'bedrock' else 32
                for offset in range(0, len(pending), batch_size):
                    batch = pending[offset:offset + batch_size]
                    values = embed(model, [text for _, text in batch], transport)
                    vectors.update(zip([value for value, _ in batch], values, strict=True))
                    stats["embedded_chunks"] += len(values)
                    heartbeat(workspace, job, stats)
            updates.append((source, chunks))
        # The workspace lock serializes source mutations and publication. A second
        # disk manifest also catches edits made directly to the Docker bind mount.
        with workspace.connect() as conn:
            current = workspace.settings(conn, project, lock=True)
            running = conn.execute("SELECT id FROM ka.jobs WHERE id=%s AND status='running' AND lease_token=%s AND lease_expires_at>now() FOR UPDATE", (job["id"], job["lease_token"])).fetchone()
            if running is None or current["revision"] != config["revision"]:
                raise HTTPException(409, "Sources or settings changed while processing; retry this scope")
            check = workspace.snapshot(conn, project, folder, job["input"]["recursive"], settings)
            manifest = lambda rows: sorted((s["path"], s["kind"], s["content_hash"]) for s in rows)
            if manifest(check) != manifest(sources):
                raise HTTPException(409, "Source files changed while processing; retry this scope")
            for value, vector in vectors.items():
                conn.execute("INSERT INTO ka.rag_vectors(project_id,profile,input_hash,embedding) VALUES (%s,%s,%s,%s::public.vector) ON CONFLICT (project_id,profile,input_hash) DO UPDATE SET embedding=EXCLUDED.embedding", (project, profile, value, json.dumps(vector)))
            for source, chunks in updates:
                row = conn.execute("""INSERT INTO ka.rag_files(project_id,folder_id,path,kind,content_hash,processing_hash,embedding_profile,job_id)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id,path,kind) DO UPDATE
                                    SET content_hash=EXCLUDED.content_hash,processing_hash=EXCLUDED.processing_hash,
                                    embedding_profile=EXCLUDED.embedding_profile,job_id=EXCLUDED.job_id,indexed_at=now() RETURNING id""",
                                   (project, source["folder_id"], source["path"], source["kind"], source["content_hash"], fingerprint, profile, job["id"])).fetchone()
                conn.execute("DELETE FROM ka.rag_chunks WHERE file_id=%s", (row["id"],))
                for position, chunk in enumerate(chunks):
                    conn.execute("INSERT INTO ka.rag_chunks(project_id,file_id,position,text,input_hash) VALUES (%s,%s,%s,%s,%s)", (project, row["id"], position, chunk["text"], chunk["input_hash"]))
            retained = {(s["path"], s["kind"]) for s in sources}
            for old in existing:
                if (old["path"], old["kind"]) not in retained:
                    conn.execute("DELETE FROM ka.rag_files WHERE id=%s", (old["id"],))
                    stats["removed_entries"] += 1
            conn.execute("""DELETE FROM ka.rag_vectors v WHERE v.project_id=%s AND NOT EXISTS (
                            SELECT 1 FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id
                            WHERE c.project_id=v.project_id AND c.input_hash=v.input_hash AND f.embedding_profile=v.profile)""", (project,))
            conn.execute("""UPDATE ka.jobs SET status='succeeded',finished_at=now(),result=%s,
                         worker_id=NULL,lease_token=NULL,lease_expires_at=NULL WHERE id=%s""", (Jsonb(stats), job["id"]))
        return stats
    except Exception as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else "Processing failed; the previous index remains available. Check source files and service health."
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
            with workspace.connect() as conn:
                projects = conn.execute("SELECT DISTINCT project_id FROM ka.rag_folders WHERE source_cleanup_pending").fetchall()
            for project in projects:
                if workspace.cleanup_deleted_folders(project["project_id"]):
                    logger.warning("RAG source deletion is pending; check source directory permissions")
            job = claim(workspace)
            if job is not None:
                process(workspace, job)
                continue
        except (psycopg.Error, OSError):
            logger.error("RAG worker cannot reach its database or source root")
        time.sleep(2)
