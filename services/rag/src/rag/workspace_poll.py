"""Automatic sync: the RAG worker polls collections with auto-sync on.

A due collection is walked with sizes and mtimes only (no reads) and compared with its file log. A
sync is queued when something changed and the newest change is at least QUIET_SECONDS old, so a
folder that a source is still writing is left alone until it settles.
"""
import logging
import time

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from rag import file_log
from rag.collections import MAX_FILES, MAX_SYNC_ENTRIES

logger = logging.getLogger(__name__)

QUIET_SECONDS = 120
ACTIVE = ["queued", "running", "waiting_input"]


def due(workspace) -> list[dict]:
    """Claim the collections whose poll interval has passed: record last_polled_at and return them.

    SKIP LOCKED and the committed timestamp keep two workers from checking one collection twice.
    """
    with workspace.connect() as conn:
        return conn.execute("""UPDATE ka.rag_collections c SET last_polled_at=now() WHERE c.id IN (
            SELECT id FROM ka.rag_collections WHERE deleted_at IS NULL AND auto_sync
              AND (last_polled_at IS NULL OR last_polled_at <= now() - make_interval(mins => poll_interval_minutes))
            ORDER BY last_polled_at NULLS FIRST, id FOR UPDATE SKIP LOCKED LIMIT 20)
            RETURNING c.*""").fetchall()


def needs_sync(row: dict, walk, log: dict) -> bool:
    if row["last_synced_at"] is None or row["synced_revision"] != row["revision"]:
        return True
    return walk.truncated or file_log.differs(walk, log)


def check(workspace, row: dict, now: float) -> str:
    """Queue a sync for one due collection when it needs one."""
    with workspace.connect() as conn:
        if conn.execute("""SELECT 1 FROM ka.jobs WHERE rag_collection_id=%s AND kind='rag_collection_sync'
                AND status = ANY(%s)""", (row["id"], ACTIVE)).fetchone():
            return "active"
        sources = [path["source_path"] for path in workspace.paths(conn, row["id"])]
        log = file_log.load(conn, row["id"])
    if not sources:
        return "empty"
    if not all(workspace.areas.present(source) for source in sources):
        return "missing"  # never synced while a folder is absent; the status shows Folder missing
    walk = workspace.areas.walk_paths(sources, row["include"], row["exclude"], limit=MAX_FILES, truncate=True,
                                      max_entries=MAX_SYNC_ENTRIES)
    if not needs_sync(row, walk, log):
        return "fresh"
    if walk.newest_mtime is not None and walk.newest_mtime > now - QUIET_SECONDS:
        return "changing"
    with workspace.connect() as conn:
        # The partial unique index rag_collection_one_active_job lets a concurrent manual Refresh win.
        queued = conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,requested_by,input)
            VALUES (%s,'rag_collection_sync',%s,NULL,%s) ON CONFLICT DO NOTHING RETURNING id""",
            (row["project_id"], row["id"], Jsonb({"auto": True}))).fetchone()
    return "queued" if queued else "active"


def poll(workspace, now: float | None = None) -> dict[str, str]:
    """Check every due collection; returns each one's outcome by name."""
    now = time.time() if now is None else now
    outcomes = {}
    for row in due(workspace):
        try:
            outcomes[row["name"]] = check(workspace, row, now)
        except (HTTPException, OSError) as error:
            logger.warning("Could not poll collection %s: %s", row["name"], getattr(error, "detail", error))
            outcomes[row["name"]] = "error"
    return outcomes
