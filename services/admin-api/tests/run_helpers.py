"""Helpers shared by the session-worker tests: post to the API, read a session row and its run events, wait for a state."""
import asyncio
import time

from test_repositories import HEADERS


def post(sessions, path: str, body: dict | None = None):
    return asyncio.to_thread(sessions.client.post, path, headers=HEADERS, json=body)


def session_row(store, session_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.agent_sessions WHERE id=%s', (session_id,)).fetchone()


async def wait_for(store, session_id, predicate, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        row = session_row(store, session_id)
        if predicate(row):
            return row
        if time.monotonic() > deadline:
            raise AssertionError(f"Session state not reached: {row['status']}, turn {row['turns']}")
        await asyncio.sleep(0.02)


def events(store, session_id) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('''SELECT run_key,seq,kind,payload FROM ka.agent_run_events WHERE starts_with(run_key,%s)
            ORDER BY id''', (f'cc:{session_id}:',)).fetchall()
