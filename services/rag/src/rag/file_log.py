"""The per-file sync log: what the last published sync decided for every file it found.

`rag_files` and `rag_chunks` stay the search index; the log is the history behind it. A file whose
size, mtime and processing fingerprint equal its log row is not opened again.
"""
from rag.workspace import digest

STATUSES = ("indexed", "skipped_binary", "skipped_too_large", "skipped_unreadable", "failed", "removed")
GROUPS = {"indexed": ("indexed",), "skipped": ("skipped_binary", "skipped_too_large", "skipped_unreadable"),
          "failed": ("failed",), "removed": ("removed",)}
PRESENT = tuple(status for status in STATUSES if status != "removed")
# Fixing permissions does not change a file's mtime, so unreadable files are always tried again.
RETRIED = ("removed", "skipped_unreadable")

UPSERT = """INSERT INTO ka.rag_file_log(collection_id,path_id,path,size,mtime,content_hash,fingerprint,status,error)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (collection_id,path) DO UPDATE SET
    path_id=EXCLUDED.path_id,size=EXCLUDED.size,mtime=EXCLUDED.mtime,content_hash=EXCLUDED.content_hash,
    fingerprint=EXCLUDED.fingerprint,status=EXCLUDED.status,error=EXCLUDED.error,last_seen_at=now(),removed_at=NULL"""


def fingerprint(processing: str, profile: str, include: list[str], exclude: list[str], max_file_bytes: int) -> str:
    """Everything besides a file's content that decides how it is processed."""
    return digest({"processing": processing, "profile": profile, "include": list(include),
                   "exclude": list(exclude), "max_file_bytes": max_file_bytes})


def load(conn, collection_id) -> dict[str, dict]:
    return {row["path"]: row for row in conn.execute(
        "SELECT * FROM ka.rag_file_log WHERE collection_id=%s", (collection_id,)).fetchall()}


def unchanged(entry: dict, row: dict | None, current: str) -> bool:
    """The walk entry matches its log row exactly, so the file need not be opened."""
    return (row is not None and row["status"] not in RETRIED and row["fingerprint"] == current
            and row["size"] == entry["size"] and row["mtime"] == entry["mtime"])


def record(conn, collection_id, path_ids: dict, decisions: dict[str, dict], current: str) -> int:
    """Write a sync's decisions, mark files it did not see removed and copy index times from rag_files.

    Runs in the publishing transaction, so a failed sync leaves the log as it was. Returns how many
    rows became removed.
    """
    if decisions:
        with conn.cursor() as cursor:
            cursor.executemany(UPSERT, [(collection_id, path_ids[d["source"]], path, d["size"], d["mtime"],
                                         d["content_hash"], current, d["status"], d["error"])
                                        for path, d in decisions.items()])
    removed = conn.execute("""UPDATE ka.rag_file_log SET status='removed',removed_at=now(),error=NULL
        WHERE collection_id=%s AND status<>'removed' AND NOT (path = ANY(%s))""", (collection_id, list(decisions))).rowcount
    conn.execute("""UPDATE ka.rag_file_log l SET indexed_at=CASE WHEN l.status='indexed' THEN
        (SELECT f.indexed_at FROM ka.rag_files f WHERE f.collection_id=l.collection_id AND f.path=l.path) END
        WHERE l.collection_id=%s""", (collection_id,))
    return removed
