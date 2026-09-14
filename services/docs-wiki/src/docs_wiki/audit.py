"""Append-only, user-attributed audit log of every answered question:
`<state_dir>/<app>/audit.db` (user data under the central state
directory, next to sessions.db and gaps.jsonl). Rows are written when a
chat turn completes and when an escalation answer lands; they are never
updated or deleted — a user deleting their session leaves its audit rows
intact, which is what makes the log the admin-preserved record."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    user_email TEXT NOT NULL,
    session_key TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources_used TEXT NOT NULL DEFAULT '[]',
    not_covered INTEGER NOT NULL DEFAULT 0,
    escalated INTEGER NOT NULL DEFAULT 0
)
"""


def _connect(state_app_dir: Path) -> sqlite3.Connection:
    state_app_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state_app_dir / "audit.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    # Admin drill-down (by_session) and per-user lists (sessions_for/users)
    # filter on these columns; the table is append-only-forever, so without
    # indexes those scans grow unboundedly.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_key)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_email)")
    return conn


def append(state_app_dir: Path, *, user_email: str, session_key: str,
           question: str, answer: str, sources_used: list[str],
           not_covered: bool, escalated: bool) -> None:
    with closing(_connect(state_app_dir)) as conn:
        conn.execute(
            "INSERT INTO audit_log (user_email, session_key, question, "
            "answer, sources_used, not_covered, escalated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_email, session_key, question, answer,
             json.dumps(sources_used), int(not_covered), int(escalated)))
        conn.commit()


def _as_row(record: sqlite3.Row) -> dict:
    return {"id": record["id"], "ts": record["ts"],
            "user_email": record["user_email"],
            "session_key": record["session_key"],
            "question": record["question"], "answer": record["answer"],
            "sources_used": json.loads(record["sources_used"]),
            "not_covered": bool(record["not_covered"]),
            "escalated": bool(record["escalated"])}


def query(state_app_dir: Path, *, user: str | None = None,
          before_id: int | None = None, limit: int = 50) -> list[dict]:
    if not (state_app_dir / "audit.db").is_file():
        return []
    sql = "SELECT * FROM audit_log"
    where, params = [], []
    if user is not None:
        where.append("user_email = ?")
        params.append(user)
    if before_id is not None:
        where.append("id < ?")
        params.append(before_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with closing(_connect(state_app_dir)) as conn:
        conn.row_factory = sqlite3.Row
        return [_as_row(r) for r in conn.execute(sql, params)]


def by_session(state_app_dir: Path, session_key: str) -> list[dict]:
    if not (state_app_dir / "audit.db").is_file():
        return []
    with closing(_connect(state_app_dir)) as conn:
        conn.row_factory = sqlite3.Row
        return [_as_row(r) for r in conn.execute(
            "SELECT * FROM audit_log WHERE session_key = ? ORDER BY id",
            (session_key,))]


def users(state_app_dir: Path) -> list[dict]:
    """Per-user activity summary for the admin UI, most recently active
    first."""
    if not (state_app_dir / "audit.db").is_file():
        return []
    with closing(_connect(state_app_dir)) as conn:
        conn.row_factory = sqlite3.Row
        return [{"user_email": r["user_email"], "sessions": r["sessions"],
                 "questions": r["questions"], "last_ts": r["last_ts"]}
                for r in conn.execute(
                    "SELECT user_email, COUNT(DISTINCT session_key) AS sessions, "
                    "COUNT(*) AS questions, MAX(ts) AS last_ts "
                    "FROM audit_log GROUP BY user_email "
                    "ORDER BY MAX(id) DESC")]


def sessions_for(state_app_dir: Path, user_email: str) -> list[dict]:
    """One user's sessions, most recently active first. first_question is
    the question of the session's earliest row (MIN(id) — insertion order,
    same tiebreak the transcript view uses)."""
    if not (state_app_dir / "audit.db").is_file():
        return []
    with closing(_connect(state_app_dir)) as conn:
        conn.row_factory = sqlite3.Row
        return [{"session_key": r["session_key"],
                 "first_question": r["first_question"],
                 "questions": r["questions"],
                 "first_ts": r["first_ts"], "last_ts": r["last_ts"]}
                for r in conn.execute(
                    "SELECT session_key, "
                    "(SELECT question FROM audit_log b "
                    " WHERE b.session_key = a.session_key "
                    "   AND b.user_email = a.user_email "
                    " ORDER BY b.id LIMIT 1) AS first_question, "
                    "COUNT(*) AS questions, MIN(ts) AS first_ts, "
                    "MAX(ts) AS last_ts "
                    "FROM audit_log a WHERE user_email = ? "
                    "GROUP BY session_key ORDER BY MAX(id) DESC",
                    (user_email,))]
