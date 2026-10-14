"""Native session history through the Agents SDK `Session` interface, backed by ka.agent_sessions.history.

A turn loads the saved history once (HistorySession.load). The SDK reads it with get_items() and gives the turn's items
to add_items(), which only buffers them. The worker commits the buffer, without U+0000 and redacted with the turn's
credentials, in the transaction that records a successful turn (SessionRunner.finish_turn), and drops it otherwise, so
a failed, stopped or timed-out turn never leaves partial items. SessionSettings(limit) is not used: every turn sees the
whole conversation, including the first message that says what to document."""

from typing import Any

from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

from knowledge_admin.knowledge_agents.session_worker import scrub


class HistorySession:
    """An Agents SDK Session (the agents.memory.Session protocol) for one turn of a native session."""

    session_settings = None

    def __init__(self, session_id, saved: list[Any]):
        self.session_id = str(session_id)
        self.saved = list(saved)
        self.pending: list[Any] = []

    @classmethod
    def load(cls, conn, session_id) -> 'HistorySession':
        row = conn.execute('SELECT history FROM ka.agent_sessions WHERE id=%s', (session_id,)).fetchone()
        if row is None:
            raise LookupError('Session not found.')
        return cls(session_id, row['history'])

    async def get_items(self, limit: int | None = None) -> list[Any]:
        items = [*self.saved, *self.pending]
        if limit is None:
            return items
        return items[-limit:] if limit > 0 else []

    async def add_items(self, items: list[Any]) -> None:
        self.pending.extend(items)

    async def pop_item(self) -> Any | None:
        """The SDK rewinds only what this turn added; the saved history never changes during a turn."""
        return self.pending.pop() if self.pending else None

    async def clear_session(self) -> None:
        """Drops this turn's items only. A session's history ends with the session (Close session), not here."""
        self.pending.clear()

    def commit(self, conn, secrets) -> None:
        """Append this turn's items, scrubbed, to the saved history inside the caller's transaction."""
        if not self.pending:
            return
        items = scrub(jsonable_encoder(self.pending), [secret for secret in secrets if secret])
        conn.execute('UPDATE ka.agent_sessions SET history=history || %s WHERE id=%s', (Jsonb(items), self.session_id))

    def drop(self) -> None:
        self.pending.clear()
