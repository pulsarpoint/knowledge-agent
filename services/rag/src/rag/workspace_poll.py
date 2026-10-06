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
from rag.collections import too_large
from rag.workspace import Settings

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
    """The one definition of fresh, shared with the collection status: never synced, settings changed
    since the last sync, or the walk differs from the file log (a new, changed or deleted file)."""
    if row["last_synced_at"] is None or row["synced_revision"] != row["revision"]:
        return True
    return file_log.differs(walk, log)


def check(workspace, row: dict, now: float) -> str:
    """Queue a sync for one due collection when it needs one."""
    with workspace.connect() as conn:
        if conn.execute("""SELECT 1 FROM ka.jobs WHERE rag_collection_id=%s AND kind='rag_collection_sync'
                AND status = ANY(%s)""", (row["id"], ACTIVE)).fetchone():
            return "active"
        sources = [path["source_path"] for path in workspace.paths(conn, row["id"])]
        log = file_log.load(conn, row["id"])
        limit = Settings(**workspace.settings(conn, row["project_id"])["settings"]).max_file_bytes
    if not sources:
        return "empty"
    if not all(workspace.areas.present(source) for source in sources):
        return "missing"  # never synced while a folder is absent; the status shows Folder missing
    walk = workspace.areas.walk_paths(sources, row["include"], row["exclude"], truncate=True)  # stops at the sync caps
    if walk.missing:
        return "missing"  # a folder vanished between the presence check and the walk
    if too_large(walk, limit):
        return "too_large"  # a sync would fail on a limit; never queue one
    if not needs_sync(row, walk, log):
        return "fresh"
    if walk.newest_mtime is not None and walk.newest_mtime > now - QUIET_SECONDS:
        with workspace.connect() as conn:  # check again once the quiet period has passed, not a full interval later
            conn.execute("""UPDATE ka.rag_collections SET last_polled_at=now()
                - make_interval(mins => poll_interval_minutes) + make_interval(secs => %s) WHERE id=%s""",
                         (QUIET_SECONDS, row["id"]))
        return "changing"
    with workspace.connect() as conn:
        # The partial unique index rag_collection_one_active_job lets a concurrent manual Refresh win.
        # Only while the collection still exists: it may have been deleted since due() claimed it.
        queued = conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,requested_by,input)
            SELECT %s,'rag_collection_sync',%s,NULL,%s WHERE EXISTS (
                SELECT 1 FROM ka.rag_collections WHERE id=%s AND deleted_at IS NULL FOR SHARE)
            ON CONFLICT DO NOTHING RETURNING id""",
            (row["project_id"], row["id"], Jsonb({"auto": True}), row["id"])).fetchone()
        if queued is None and conn.execute("""SELECT 1 FROM ka.rag_collections WHERE id=%s AND deleted_at IS NULL""",
                                           (row["id"],)).fetchone() is None:
            return "deleted"
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
        except Exception:  # one collection never stops the others or the worker
            logger.exception("Could not poll collection %s", row["name"])
            outcomes[row["name"]] = "error"
    return outcomes
