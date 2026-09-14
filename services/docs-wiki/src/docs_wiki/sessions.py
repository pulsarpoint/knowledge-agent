"""Server-side conversations: capability keys over SQLiteSession storage.

Keys are unguessable capability grants (anyone holding one can read and
continue that conversation — the documented share-by-link semantic).
`sessions.db` sits next to `gaps.jsonl` under the central state directory
(`<state_dir>/<app>/`, see docs_wiki.config.state_root): user data, never
committed, NOT part of the regenerable index cache.
"""
from __future__ import annotations

import re
import json
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

from agents import SQLiteSession

KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
HISTORY_CAP = 40

# Maps persisted function_call names to the source labels the UI badges
# show — must mirror the live labels set in agent.py (state.used). Derived
# at read time: counts attempted calls, where the live event counts only
# successful ones (accepted divergence, see the 2026-08-06 spec).
TOOL_SOURCES = {
    "search_docs": "docs",
    "read_doc": "docs",
    "list_docs": "docs",
    "ask_code": "codex",
}


def new_key() -> str:
    return secrets.token_urlsafe(16)


def db_path(state_app_dir: Path) -> Path:
    state_app_dir.mkdir(parents=True, exist_ok=True)
    return state_app_dir / "sessions.db"


class CappedSession(SQLiteSession):
    """The agent's prompt sees at most HISTORY_CAP messages; storage keeps
    everything (the cap trims what get_items returns by default, which is
    what Runner uses to build the prompt).

    The trimmed window is additionally scrubbed of orphaned tool outputs:
    a suffix slice can begin on (or, with parallel tool calls, contain) a
    function_call_output whose function_call fell outside the window, and
    chat-completions providers reject the replay of a role:"tool" message
    with no preceding tool_calls (field incident 2026-08-05)."""

    async def get_items(self, limit: int | None = None):
        items = await super().get_items(HISTORY_CAP if limit is None else limit)
        seen_calls = set()
        kept = []
        for item in items:
            kind = item.get("type") if isinstance(item, dict) else None
            if kind == "function_call":
                seen_calls.add(item.get("call_id"))
            elif (kind == "function_call_output"
                    and item.get("call_id") not in seen_calls):
                continue
            kept.append(item)
        return kept


def open_session(state_app_dir: Path, key: str) -> CappedSession:
    return CappedSession(key, db_path(state_app_dir))


def _connect(state_app_dir: Path) -> sqlite3.Connection | None:
    db = state_app_dir / "sessions.db"
    if not db.is_file():
        return None
    return sqlite3.connect(db)


def exists(state_app_dir: Path, key: str) -> bool:
    conn = _connect(state_app_dir)
    if conn is None:
        return False
    with closing(conn):
        try:
            row = conn.execute(
                "SELECT 1 FROM agent_sessions WHERE session_id = ?",
                (key,)).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None


def _as_message(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    role = item.get("role")
    if role not in ("user", "assistant"):
        return None
    content = item.get("content")
    if isinstance(content, str):
        return {"role": role, "content": content}
    if isinstance(content, list):
        text = "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict)
            and part.get("type") in ("output_text", "input_text"))
        if text:
            return {"role": role, "content": text}
    return None


async def transcript(state_app_dir: Path, key: str) -> list[dict]:
    """Full stored history for the UI. Bypasses CappedSession's default
    cap (which exists only for the agent's prompt) by calling the base
    class explicitly with limit=None.

    Assistant messages carry "sources_used" derived from the
    function_call items stored since the previous message — the same
    provenance the live `done` event reports (see TOOL_SOURCES)."""
    session = open_session(state_app_dir, key)
    items = await SQLiteSession.get_items(session, None)
    messages: list[dict] = []
    pending: set[str] = set()
    for item in items:
        if isinstance(item, dict) and item.get("type") == "function_call":
            source = TOOL_SOURCES.get(item.get("name"))
            if source:
                pending.add(source)
            continue
        message = _as_message(item)
        if message is None:
            continue
        if message["role"] == "user":
            # a turn that died after tool calls but before an answer must
            # not leak its sources into the next turn's assistant message
            pending = set()
        elif pending:
            message["sources_used"] = sorted(pending)
            pending = set()
        messages.append(message)
    return messages


async def delete(state_app_dir: Path, key: str) -> None:
    await open_session(state_app_dir, key).clear_session()
    with closing(sqlite3.connect(db_path(state_app_dir))) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='search_context'").fetchone():
            conn.execute("DELETE FROM search_context WHERE session_id=?", (key,))
            conn.commit()


def context(state_app_dir: Path, key: str) -> dict | None:
    conn = _connect(state_app_dir)
    if conn is None:
        return None
    with closing(conn):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='search_context'").fetchone():
            return None
        row = conn.execute("SELECT payload FROM search_context WHERE session_id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None


def bind_context(state_app_dir: Path, key: str, payload: dict) -> bool:
    """Atomically bind a context once; concurrent requests cannot change it."""
    encoded = json.dumps(payload, sort_keys=True)
    # Initialize the SDK schema and register even an empty conversation so
    # its issued key remains usable if catalog loading or the model fails.
    open_session(state_app_dir, key).close()
    with closing(sqlite3.connect(db_path(state_app_dir))) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS search_context (session_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        conn.execute("INSERT OR IGNORE INTO agent_sessions (session_id) VALUES (?)", (key,))
        conn.execute("INSERT OR IGNORE INTO search_context VALUES (?,?)", (key, encoded))
        actual = conn.execute("SELECT payload FROM search_context WHERE session_id=?", (key,)).fetchone()[0]
        conn.commit()
        return actual == encoded


def prune(state_app_dir: Path, retention_days: int) -> int:
    """Delete sessions whose newest message (or creation, if empty) is
    older than the retention window. Lazy: called at app startup and on
    chat access. Returns the number of sessions removed."""
    if retention_days <= 0:
        return 0
    conn = _connect(state_app_dir)
    if conn is None:
        return 0
    cutoff = f"-{int(retention_days)} days"
    with closing(conn):
        try:
            stale = [row[0] for row in conn.execute(
                "SELECT s.session_id FROM agent_sessions s "
                "LEFT JOIN agent_messages m ON m.session_id = s.session_id "
                "GROUP BY s.session_id "
                "HAVING COALESCE(MAX(m.created_at), s.created_at) "
                "       < datetime('now', ?)", (cutoff,))]
            for key in stale:
                conn.execute("DELETE FROM agent_messages WHERE session_id = ?",
                             (key,))
                conn.execute("DELETE FROM agent_sessions WHERE session_id = ?",
                             (key,))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='search_context'").fetchone():
                conn.executemany("DELETE FROM search_context WHERE session_id=?", [(key,) for key in stale])
            conn.commit()
        except sqlite3.OperationalError:
            return 0     # tables not created yet — nothing to prune
        return len(stale)
