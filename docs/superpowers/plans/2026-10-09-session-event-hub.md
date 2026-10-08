# Session Event Hub (Postgres LISTEN/NOTIFY) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live session pages (the Test tab and Generate documents sessions) stop polling the database: database
triggers announce session changes on `ka_sessions`, one listener per admin-api process receives them, reads the new
rows once and hands them to every page watching that session.

**Architecture:**
- **Notices (Task 1):** migration `0083_session_notices` adds three triggers that `pg_notify('ka_sessions', …)`:
  a statement-level `AFTER INSERT` on `ka.agent_run_events` (transition table `new_rows`; one notice per session among
  the inserted `cc:<uuid>:<turn>` rows, `{"s": "<uuid>", "e": <newest id>}`), a row-level `AFTER UPDATE OR DELETE` on
  `ka.agent_sessions_cc` and a row-level `AFTER INSERT OR UPDATE OR DELETE` on `ka.agent_session_inputs`
  (`{"s": "<uuid>"}`). Notices are delivered on commit.
- **The hub (Tasks 2–3):** `knowledge_admin/live/session_hub.py` — `SessionHub(conninfo)` with one `LISTEN` connection,
  `subscribe(session_id, after_id)` (async context manager giving a `Subscription` with a bounded queue), a dirty set
  drained by one reader task (`COALESCE_SECONDS` lets a burst become one read) that reads each dirty watched session
  over one short-lived connection (`STATE` and events after the furthest-behind subscriber, `PAGE` at a time) and gives
  each subscriber only what it is missing. Task 3 adds reconnect with backoff, the 30 s check, `resync` for a full
  queue and `unavailable` once per outage.
- **The endpoint (Task 4):** `GET /api/claude-code/sessions/{id}/events` subscribes to `request.app.state.session_hub`
  (started and stopped in `create_app`'s lifespan) and forwards messages with the unchanged SSE contract; `resync` ends
  the stream with `retry: 1000`. `STREAM_POLL_SECONDS`, the per-stream `AsyncConnection` and the polling loop go.

**Tech Stack:** FastAPI (`StreamingResponse`), psycopg 3.3.5 (`uv.lock`; `AsyncConnection.notifies(*,
timeout=None, stop_after=None)` async generator, available since 3.2), PostgreSQL 17 (`knowledge-agent-v3-postgres:
17.10-vector0.8.6`), golang-migrate 4.19.1, pytest against the disposable PostgreSQL fixture (`asyncio.run` inside
tests; there is no pytest-asyncio).

**Spec:** `docs/superpowers/specs/2026-10-09-session-event-hub-design.md` (binding).

## Global Constraints

- Implementers and reviewers never run make targets, never read or modify `.env` or anything under `.local`, never
  touch the running Docker stack or its database, and NEVER `git stash` (the stash is shared across worktrees). Tests
  use the disposable PostgreSQL fixture (`services/admin-api/tests/conftest.py`) and `db/tests/run.sh`, which start
  their own throwaway containers.
- Work only inside the worktree `/Users/graovic/fincore/knowledge-agent-v3/.claude/worktrees/session-event-hub`
  (branch `feature/session-event-hub`); never `cd` to `/Users/graovic/fincore/knowledge-agent-v3` itself.
- Stage files explicitly (`git add <paths>`; never `git add -A`, `git add .`, and never add `.superpowers`). Commit
  trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Credentials are never logged: the hub logs only exception class names (a psycopg message can echo connection
  details), never the conninfo.
- Channel `ka_sessions`; payload `{"s": "<session uuid>"}` or `{"s": "<session uuid>", "e": <newest event id>}`.
- Hub limits: queue **500** messages per subscriber (`QUEUE_SIZE`), **500** events per query (`PAGE`), check every
  **30 s** (`SWEEP_SECONDS`), reconnect backoff **1 s doubling to 30 s** (`BACKOFF_START`, `BACKOFF_MAX`).
- Stream contract unchanged: `id:` lines, `event: run_event`, `event: session`, `event: closed`,
  `event: unavailable`, `: keepalive` after `KEEPALIVE_SECONDS` (15 s) of quiet, `Last-Event-ID` / `?after=` resume;
  plus `retry: 1000` ending a stream that fell behind. `GET …/events/history` is unchanged.
- The browser client (`services/admin-ui/app/lib/useSessionEvents.ts`) does not change.
- Python stays compatible with 3.12.
- Test commands (from the worktree root; each task runs the whole suite of every part it touches before its commit;
  Task 5 runs all four):
  - `bash db/tests/run.sh`
  - `cd services/admin-api && uv run --frozen pytest -q`
  - admin-ui (no UI code changes): `if [ -d services/admin-ui/node_modules ]; then (cd services/admin-ui && npx vitest
    run && npx tsc --noEmit); else echo 'admin-ui: node_modules missing in this worktree, UI checks skipped (no UI
    change)'; fi` — do not install packages; report which branch ran.
  - `python3 -m unittest discover -s tests` (repository root)
- Known failures that are not regressions (leave them, mention them in the task report): the `test_environments.py`
  rollback test, and the order-dependent docs-folder test in `test_deploy.py` (passes alone).

## Review Focus

1. **A page that reconnects or joins late duplicates or misses events.** Examples: two pages at different positions;
   a page resuming with `Last-Event-ID` after a `resync`; a page that joins while the hub is reading the session (it
   must not be advanced past events it never got). Pinned in:
   - Task 2: `test_two_subscribers_at_different_positions_get_exactly_their_missing_events`,
     `test_a_page_far_behind_gets_every_event_in_order_page_by_page`;
   - Task 4: `test_a_stream_that_falls_behind_ends_with_retry_and_resumes_from_its_last_id`.
2. **A page silently freezes.** Examples: the listener's connection is killed while the database stays up; a notice is
   lost (sent while nobody listened, or never sent). Expected: the page keeps its stream (keepalives) and gets the
   event after the reconnect or the 30 s check. Pinned in:
   - Task 3: `test_the_listener_reconnects_and_reads_every_watched_session_again`,
     `test_the_periodic_check_finds_an_event_whose_notice_was_suppressed`.
3. **A database outage ends streams, floods them with `unavailable`, or never resumes.** Expected: `unavailable`
   exactly once per stream per outage, the stream stays open, events flow again after recovery. Pinned in:
   - Task 3: `test_an_outage_sends_unavailable_once_and_recovery_resumes`.
4. **Something else on the channel breaks the listener.** Examples: a manual `NOTIFY ka_sessions, 'junk'`, a payload
   without a valid uuid, a JSON array, notices for sessions nobody watches. Expected: ignored (malformed ones logged
   without content), no read, later notices still work. Pinned in:
   - Task 2: `test_malformed_and_unwatched_notices_change_nothing`.
5. **Pages that went away keep costing.** Examples: a stream cancelled by a browser disconnect, a closed or deleted
   session, ten streams on one session. Expected: the subscriber is removed, a session nobody watches is forgotten and
   never read, open streams hold no database connection. Pinned in:
   - Task 2: `test_a_page_that_leaves_is_forgotten_and_never_read`,
     `test_closed_and_deleted_sessions_end_every_subscription_and_are_forgotten`;
   - Task 4: `test_open_streams_hold_no_database_connection`.

## Decisions (where the spec leaves a choice or the code needs one)

- **Row-level triggers for session rows and queued messages.** PostgreSQL refuses transition tables on a trigger with
  more than one event, so `notify_session_change` and `notify_session_input` are `FOR EACH ROW`; PostgreSQL folds
  identical notices (same channel and payload) within one transaction, so a statement touching many rows of one session
  still sends one notice. Events stay statement-level with `new_rows`, as the spec says.
- **`IF TG_OP = 'DELETE'`** picks `OLD` (a removed session or input) in the row triggers.
- **The hub reads only `s`.** `e` is informational (it tells a reader how far the writer got); every notice for a
  watched session marks it dirty.
- **`read_to`** starts at the first subscriber's position and advances to the newest event id read; a read starts after
  `min(subscriber positions, read_to)`.
- **A read serves the subscribers present when it starts.** A page that subscribes during a read is marked dirty and
  gets its own read, so it is never advanced past events it was not given.
- **The last state sent is kept per subscriber**, not per session: a page that joins needs the current state even when
  the other pages already have it.
- **`notifies(timeout=sweep_seconds)` is the 30 s timer.** When it returns, `SELECT 1` (bounded by `CONNECT_TIMEOUT`)
  proves the connection is alive and every watched session is marked dirty. A notice lost in that short gap is covered
  by the read that follows.
- **Loss versus outage.** A listener connection that was established and then lost waits `backoff_start` and
  reconnects without telling anybody; a connect that fails is an outage (`unavailable` once per subscriber; flags reset
  on recovery). A failed read is an outage too.
- **`unavailable` no longer ends the stream** (spec). The existing client shows "The session stream is unavailable.
  Reload the page." and keeps the `EventSource`; events resume underneath after recovery.
- **The endpoint finds the hub at `request.app.state.session_hub`.** Without it (an app whose lifespan did not run) the
  stream answers 503 `LIVE_UNAVAILABLE = 'Live session updates are not running.'`.
- **`STATE` moves** from `claude_code_api.py` to `live/session_hub.py`, its only reader after this change.
  `stream_event` stays in `claude_code_api.py` (history uses it); the hub sends raw event rows.
- **Lease renewals notify.** `session_worker.claim` renews the lease of every active session each poll (1 s), so a
  working session sends a notice a second; a watched one is read once a second per API process (nothing is sent unless
  the state changed), a session nobody watches costs nothing. A lease that expires without an update (a crashed worker)
  shows as `worker_active: false` at the next 30 s check instead of within a second.
- **The `live` package holds only the session hub.** No generic base class until a second hub exists.

## Delivery and test boundaries

Tasks land in order on `feature/session-event-hub`; every touched suite is green after each task. Task 1 (migration)
stands alone. Task 2 (hub core) needs Task 1's notices. Task 3 (resilience) replaces Task 2's `_listen`, `_read_loop`
and `_put` and adds `_outage` / `_recovered`. Task 4 (endpoint and lifespan) needs Tasks 2–3. Task 5 updates the docs
and runs every suite.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `db/migrations/0083_session_notices.up.sql`, `.down.sql` | the three notice triggers and their functions | 1 |
| `db/tests/run.sh` | rollback pin 81 → 82; 0083 down removes the triggers; triggers present after the final up | 1 |
| `services/admin-api/tests/test_session_notices_migration.py` | notice payloads, silence for other run keys, up/down | 1 |
| `services/admin-api/src/knowledge_admin/live/__init__.py` | package marker | 2 |
| `services/admin-api/src/knowledge_admin/live/session_hub.py` | `SessionHub`, `Subscription`, `STATE`, constants (2); resilience (3) | 2, 3 |
| `services/admin-api/tests/test_session_hub.py` | hub behaviour against the real database | 2, 3 |
| `services/admin-api/src/knowledge_admin/app.py` | lifespan starts and stops the hub, `app.state.session_hub` | 4 |
| `services/admin-api/src/knowledge_admin/claude_code_api.py` | the stream on the hub; `STATE`, `STREAM_POLL_SECONDS`, polling removed | 4 |
| `services/admin-api/tests/test_claude_code_sessions.py` | `live` fixture; the two stream tests run with it | 4 |
| `services/admin-api/tests/test_session_stream.py` | keepalive, resync + resume, connection count, worker flush, 503 | 4 |
| `docs/documentation.md`, `docs/agents.md`, `docs/database.md`, `docs/migrations.md`, `deploy/README.md` | documentation | 5 |

---

### Task 1: Migration 0083 — session notices

**Files:**
- Create: `db/migrations/0083_session_notices.up.sql`, `db/migrations/0083_session_notices.down.sql`
- Modify: `db/tests/run.sh` (rollback pin at the `[ "$rolled_back_version" = 81 ]` line; a check after the 0081 check)
- Test: `services/admin-api/tests/test_session_notices_migration.py`

**Interfaces:**
- Produces: channel `ka_sessions`; functions `ka.notify_session_events()`, `ka.notify_session_change()`,
  `ka.notify_session_input()`; triggers of the same names on `ka.agent_run_events`, `ka.agent_sessions_cc`,
  `ka.agent_session_inputs`. Payloads are JSON text built by `json_build_object` (`{"s" : "…", "e" : 12}`; parse,
  never compare strings).

- [ ] **Step 1: Write the failing migration tests**

Create `services/admin-api/tests/test_session_notices_migration.py`:

```python
"""0083: changes to a session are announced on ka_sessions — run events (one notice per statement and session, with
the newest id), session rows (update, delete) and queued messages (insert, update, delete); other run keys are silent
and nothing is sent before commit. Down removes the triggers; up restores them."""
import json
from pathlib import Path

import psycopg

from session_rows import a_model, add_ad_hoc_session

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0083_session_notices.up.sql', '0083_session_notices.down.sql'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def two_sessions(store):
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        model = a_model(conn, actor)
        return project, actor, [add_ad_hoc_session(conn, project, actor, model) for _ in range(2)]


def insert_events(conn, project, keys: list[str]) -> list[dict]:
    """One INSERT statement for all keys (seq 1, 2, … per key)."""
    seqs = [keys[:index + 1].count(key) for index, key in enumerate(keys)]
    return conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        SELECT %s,k,s,'message','ad-hoc','{}' FROM unnest(%s::text[],%s::int[]) AS t(k,s) RETURNING id,run_key''',
        (project, keys, seqs)).fetchall()


def notices(listener) -> list[dict]:
    return [json.loads(n.payload) for n in listener.notifies(timeout=0.5) if n.channel == 'ka_sessions']


def test_event_inserts_notify_once_per_statement_and_session_with_the_newest_id(store):
    project, _, (first, second) = two_sessions(store)
    with psycopg.connect(store.conninfo, autocommit=True) as listener, store.connect() as conn:
        listener.execute('LISTEN ka_sessions')
        rows = insert_events(conn, project, [f'cc:{first}:1', f'cc:{second}:1', f'cc:{first}:1', f'cc:{first}:2',
                                             'run:other', f'cc:{second}:x', 'cc:not-a-session:1'])
        assert notices(listener) == []  # nothing before commit
        conn.commit()
        got = notices(listener)
        assert len(got) == 2
        assert {(n['s'], n['e']) for n in got} == {
            (str(first), max(r['id'] for r in rows if r['run_key'].startswith(f'cc:{first}:'))),
            (str(second), next(r['id'] for r in rows if r['run_key'] == f'cc:{second}:1'))}
        insert_events(conn, project, ['run:quiet', 'cc:also-quiet:1'])
        conn.commit()
        assert notices(listener) == []


def test_session_rows_and_queued_messages_notify_their_session(store):
    _, actor, (first, second) = two_sessions(store)
    with psycopg.connect(store.conninfo, autocommit=True) as listener, store.connect() as conn:
        listener.execute('LISTEN ka_sessions')
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working' WHERE id=%s", (first,))
        conn.execute('UPDATE ka.agent_sessions_cc SET input_tokens=5 WHERE id=%s', (first,))
        conn.commit()
        assert notices(listener) == [{'s': str(first)}]  # identical notices in one transaction are folded
        conn.execute("UPDATE ka.agent_sessions_cc SET status='waiting' WHERE id=%s", (second,))
        conn.rollback()
        assert notices(listener) == []  # only committed changes are announced
        conn.execute("INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,'Hi')",
                     (second, actor))
        conn.commit()
        assert notices(listener) == [{'s': str(second)}]
        conn.execute("UPDATE ka.agent_session_inputs SET status='discarded' WHERE session_id=%s", (second,))
        conn.commit()
        assert notices(listener) == [{'s': str(second)}]
        conn.execute('DELETE FROM ka.agent_session_inputs WHERE session_id=%s', (second,))
        conn.commit()
        assert notices(listener) == [{'s': str(second)}]
        conn.execute('DELETE FROM ka.agent_sessions_cc WHERE id=%s', (first,))
        conn.commit()
        assert notices(listener) == [{'s': str(first)}]


def test_down_removes_the_notices_and_up_restores_them(store):
    project, _, (first, _) = two_sessions(store)
    with psycopg.connect(store.conninfo, autocommit=True) as listener, store.connect() as conn:
        listener.execute('LISTEN ka_sessions')
        run(conn, DOWN)
        conn.commit()
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working' WHERE id=%s", (first,))
        insert_events(conn, project, [f'cc:{first}:1'])
        conn.commit()
        assert notices(listener) == []
        run(conn, UP)
        conn.commit()
        conn.execute("UPDATE ka.agent_sessions_cc SET status='waiting' WHERE id=%s", (first,))
        conn.commit()
        assert notices(listener) == [{'s': str(first)}]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_session_notices_migration.py`
Expected: FAIL — no notices arrive (`assert [] == [...]`) and `run(conn, DOWN)` raises `FileNotFoundError`.

- [ ] **Step 3: Write the migration**

Create `db/migrations/0083_session_notices.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Live session pages: a change to a session is announced on the channel ka_sessions (delivered on commit) and one
-- listener per admin-api process reads the new rows. Payload {"s": "<session id>"}, or for run events
-- {"s": "<session id>", "e": <newest inserted event id>}.
CREATE FUNCTION notify_session_events() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    found record;
BEGIN
    FOR found IN
        SELECT split_part(run_key, ':', 2) AS session_id, max(id) AS newest FROM new_rows
        WHERE run_key ~ '^cc:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9]+$'
        GROUP BY 1
    LOOP
        PERFORM pg_notify('ka_sessions', json_build_object('s', found.session_id, 'e', found.newest)::text);
    END LOOP;
    RETURN NULL;
END $$;
CREATE TRIGGER notify_session_events AFTER INSERT ON agent_run_events
    REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION notify_session_events();

-- Any change to a session row (status, tokens, draft, turns, leases, stop, close) and its removal.
CREATE FUNCTION notify_session_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM pg_notify('ka_sessions', json_build_object('s', OLD.id)::text);
    ELSE
        PERFORM pg_notify('ka_sessions', json_build_object('s', NEW.id)::text);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER notify_session_change AFTER UPDATE OR DELETE ON agent_sessions_cc
    FOR EACH ROW EXECUTE FUNCTION notify_session_change();

-- Queued messages: the queued count is part of the session state.
CREATE FUNCTION notify_session_input() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM pg_notify('ka_sessions', json_build_object('s', OLD.session_id)::text);
    ELSE
        PERFORM pg_notify('ka_sessions', json_build_object('s', NEW.session_id)::text);
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER notify_session_input AFTER INSERT OR UPDATE OR DELETE ON agent_session_inputs
    FOR EACH ROW EXECUTE FUNCTION notify_session_input();

COMMIT;
```

Create `db/migrations/0083_session_notices.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DROP TRIGGER notify_session_input ON agent_session_inputs;
DROP TRIGGER notify_session_change ON agent_sessions_cc;
DROP TRIGGER notify_session_events ON agent_run_events;
DROP FUNCTION notify_session_input();
DROP FUNCTION notify_session_change();
DROP FUNCTION notify_session_events();

COMMIT;
```

- [ ] **Step 4: Run the migration tests to verify they pass**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_session_notices_migration.py`
Expected: 3 passed.

- [ ] **Step 5: Update `db/tests/run.sh`**

Replace

```sh
[ "$rolled_back_version" = 81 ]
```

with

```sh
[ "$rolled_back_version" = 82 ]
notices_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname IN
            ('notify_session_events', 'notify_session_change', 'notify_session_input'))
        AND to_regprocedure('ka.notify_session_events()') IS NULL
        AND to_regprocedure('ka.notify_session_change()') IS NULL
        AND to_regprocedure('ka.notify_session_input()') IS NULL")
[ "$notices_removed" = t ] || { echo 'FAIL: 0083 down left session notice triggers' >&2; exit 1; }
```

and after the line `printf '%s\n' 'PASS: Test chats are sessions; the old Test chat tables are gone.'` add:

```sh
# 0083 announces session changes on ka_sessions: run events, session rows and queued messages.
session_notices=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT count(*) = 3 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'ka' AND NOT t.tgisinternal AND (c.relname, t.tgname) IN
            (('agent_run_events', 'notify_session_events'), ('agent_sessions_cc', 'notify_session_change'),
             ('agent_session_inputs', 'notify_session_input'))")
[ "$session_notices" = t ] || { echo 'FAIL: the session notice triggers are missing' >&2; exit 1; }
printf '%s\n' 'PASS: session changes are announced on ka_sessions.'
```

- [ ] **Step 6: Run the database suite**

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line including `PASS: session changes are announced on ka_sessions.`; exit status 0.

- [ ] **Step 7: Run the admin-api suite**

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: all pass except the known failures listed in Global Constraints (the streams still poll; the triggers
only add notices nobody listens to yet).

- [ ] **Step 8: Commit**

```bash
git add db/migrations/0083_session_notices.up.sql db/migrations/0083_session_notices.down.sql db/tests/run.sh \
  services/admin-api/tests/test_session_notices_migration.py
git commit -m "feat(db): 0083 announces session changes on ka_sessions

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The hub core — listener, subscribe, coalesced reads, positions, state, closed

**Files:**
- Create: `services/admin-api/src/knowledge_admin/live/__init__.py`,
  `services/admin-api/src/knowledge_admin/live/session_hub.py`
- Test: `services/admin-api/tests/test_session_hub.py`

**Interfaces:**
- Consumes: Task 1's notices on `ka_sessions`.
- Produces (in `knowledge_admin.live.session_hub`):
  - constants `CHANNEL = 'ka_sessions'`, `QUEUE_SIZE = 500`, `PAGE = 500`, `COALESCE_SECONDS = 0.05`,
    `SWEEP_SECONDS = 30.0`, `BACKOFF_START = 1.0`, `BACKOFF_MAX = 30.0`, `CONNECT_TIMEOUT = 5`; `STATE` (moved
    verbatim from `claude_code_api.STATE`), `EVENTS`;
  - `Message = tuple[str, Any]` — `('event', row: dict)` (keys `id, run_key, seq, kind, payload, created_at`),
    `('session', state: dict)` (`jsonable_encoder` of the `STATE` row), `('closed', None)`, `('unavailable', None)`,
    `('resync', None)`;
  - `class Subscription`: `position: int`, `queue: asyncio.Queue[Message]` (maxsize `QUEUE_SIZE + 1`), `state`,
    `told_unavailable: bool`, `dropped: bool`, `async get() -> Message`;
  - `class SessionHub(conninfo: str, *, sweep_seconds=SWEEP_SECONDS, backoff_start=BACKOFF_START,
    backoff_max=BACKOFF_MAX, coalesce_seconds=COALESCE_SECONDS)`: `async start()`, `async stop()`,
    `subscribe(session_id: UUID, after_id: int)` (async context manager → `Subscription`),
    `subscribers(session_id: UUID) -> int`, attributes `reads: int`, `listener_pid: int | None`,
    `listening: asyncio.Event`.
  - Private methods Task 3 replaces or calls: `_listen()`, `_read_loop()`, `_put(session_id, subscription, message)
    -> bool`, `_leave(session_id, subscription)`, `_mark(session_ids)`.

- [ ] **Step 1: Write the failing hub tests**

Create `services/admin-api/tests/test_session_hub.py`:

```python
"""The session hub (knowledge_admin.live.session_hub) against the disposable PostgreSQL: migration 0083's notices drive
reads, a burst becomes one read, and each subscriber gets exactly what it is missing."""
import asyncio
import time
from uuid import UUID

from psycopg.types.json import Jsonb

from knowledge_admin.live import session_hub
from knowledge_admin.live.session_hub import SessionHub
from session_rows import a_model, add_ad_hoc_session


def a_session(store, *, turns: int = 1) -> UUID:
    """A working Generate documents session with `turns` turns."""
    with store.connect() as conn:
        project, actor = store.project(conn, 'shop'), store.actor(conn, 'admin-id', 'admin@example.test')
        session = add_ad_hoc_session(conn, project, actor, a_model(conn, actor))
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working',turns=%s WHERE id=%s", (turns, session))
    return session


def write(store, *statements) -> None:
    """Run (sql, params) pairs in one transaction."""
    with store.connect() as conn:
        for statement, params in statements:
            conn.execute(statement, params)


def event(session_id, seq: int, turn: int = 1):
    return ('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
        SELECT project_id,%s,%s,'message','ad-hoc',%s FROM ka.agent_sessions_cc WHERE id=%s''',
            (f'cc:{session_id}:{turn}', seq, Jsonb({'text': f'event {seq}'}), session_id))


def event_ids(store, session_id) -> list[int]:
    with store.connect() as conn:
        return [row['id'] for row in conn.execute(
            'SELECT id FROM ka.agent_run_events WHERE run_key LIKE %s ORDER BY id', (f'cc:{session_id}:%',))]


async def db(fn, *args):
    """A blocking database call off the event loop (the hub's listener runs on it)."""
    return await asyncio.to_thread(fn, *args)


async def take(subscription, count: int, timeout: float = 5.0) -> list[tuple]:
    return [await asyncio.wait_for(subscription.get(), timeout) for _ in range(count)]


def summary(messages) -> list[tuple]:
    """('event', id), ('session', status) or (kind, None)."""
    return [(kind, value['id'] if kind == 'event' else value['status'] if kind == 'session' else None)
            for kind, value in messages]


async def nothing_more(subscription, wait: float = 0.3) -> bool:
    await asyncio.sleep(wait)
    return subscription.queue.empty()


async def until(check, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, 'condition not reached'
        await asyncio.sleep(0.02)


async def running(store, **options) -> SessionHub:
    hub = SessionHub(store.conninfo, **options)
    await hub.start()
    await asyncio.wait_for(hub.listening.wait(), 5)
    return hub


def test_two_subscribers_at_different_positions_get_exactly_their_missing_events(store):
    session = a_session(store)
    write(store, event(session, 1), event(session, 2), event(session, 3))
    e1, e2, e3 = event_ids(store, session)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as early, hub.subscribe(session, e2) as late:
                assert summary(await take(early, 4)) == [('event', e1), ('event', e2), ('event', e3),
                                                         ('session', 'working')]
                assert summary(await take(late, 2)) == [('event', e3), ('session', 'working')]
                await db(write, store, event(session, 4))
                e4 = (await db(event_ids, store, session))[-1]
                assert summary(await take(early, 1)) == [('event', e4)]
                assert summary(await take(late, 1)) == [('event', e4)]
                assert await nothing_more(early) and await nothing_more(late)
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_burst_of_notices_for_one_session_is_one_read(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                before = hub.reads
                # 20 statements in one transaction: 20 notices, delivered together at commit
                await db(write, store, *[event(session, seq) for seq in range(1, 21)])
                assert [kind for kind, _ in await take(page, 20)] == ['event'] * 20
                assert await nothing_more(page)
                assert hub.reads - before == 1
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_the_state_is_sent_only_when_it_changed(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                before = hub.reads
                await db(write, store, ('UPDATE ka.agent_sessions_cc SET last_activity_at=now() WHERE id=%s', (session,)))
                await until(lambda: hub.reads > before)
                assert await nothing_more(page)  # read, but nothing the page shows changed
                await db(write, store, ("UPDATE ka.agent_sessions_cc SET status='waiting',draft='# Draft',draft_turn=1 "
                                        'WHERE id=%s', (session,)))
                [(kind, state)] = await take(page, 1)
                assert kind == 'session' and state['status'] == 'waiting' and state['draft'] == '# Draft'
                await db(write, store, ("""INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text)
                    SELECT id,1,actor_id,'More' FROM ka.agent_sessions_cc WHERE id=%s""", (session,)))
                [(kind, state)] = await take(page, 1)
                assert kind == 'session' and state['queued'] == 1
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_closed_and_deleted_sessions_end_every_subscription_and_are_forgotten(store):
    closing, deleted = a_session(store), a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(closing, 0) as a, hub.subscribe(closing, 0) as b, hub.subscribe(deleted, 0) as c:
                for page in (a, b, c):
                    assert summary(await take(page, 1)) == [('session', 'working')]
                await db(write, store, event(closing, 1), (
                    "UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now() WHERE id=%s", (closing,)))
                [last] = await db(event_ids, store, closing)
                for page in (a, b):
                    assert summary(await take(page, 3)) == [('event', last), ('session', 'closed'), ('closed', None)]
                await db(write, store, ('DELETE FROM ka.agent_sessions_cc WHERE id=%s', (deleted,)))
                assert summary(await take(c, 1)) == [('closed', None)]
                assert hub.subscribers(closing) == hub.subscribers(deleted) == 0
            async with hub.subscribe(closing, 0) as late:  # a page opened on a closed session
                assert summary(await take(late, 3)) == [('event', last), ('session', 'closed'), ('closed', None)]
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_page_far_behind_gets_every_event_in_order_page_by_page(store, monkeypatch):
    monkeypatch.setattr(session_hub, 'PAGE', 2)
    session = a_session(store, turns=2)
    write(store, event(session, 1), event(session, 2), event(session, 3), event(session, 1, turn=2),
          event(session, 2, turn=2))
    ids = event_ids(store, session)
    write(store, event(session, 1, turn=3))  # turn 3 has not started (turns = 2): not part of the session yet

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 6)) == [('event', i) for i in ids] + [('session', 'working')]
                assert await nothing_more(page)
                assert hub.reads == 1  # one read, three queries
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_malformed_and_unwatched_notices_change_nothing(store):
    watched, other = a_session(store), a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(watched, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                before = hub.reads
                await db(write, store, *[("SELECT pg_notify('ka_sessions', %s)", (payload,))
                                         for payload in ('nonsense', '{"s": "not-a-uuid"}', '[1]', '{}')],
                         event(other, 1))
                await asyncio.sleep(0.3)
                assert hub.reads == before and page.queue.empty()
                await db(write, store, event(watched, 1))
                assert [kind for kind, _ in await take(page, 1)] == ['event']  # the listener still works
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_page_that_leaves_is_forgotten_and_never_read(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as page:
                await take(page, 1)
            assert hub.subscribers(session) == 0

            async def hold():  # a stream cancelled by a browser disconnect
                async with hub.subscribe(session, 0):
                    await asyncio.Event().wait()
            task = asyncio.create_task(hold())
            await until(lambda: hub.subscribers(session) == 1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert hub.subscribers(session) == 0
            before = hub.reads
            await db(write, store, event(session, 1))
            await asyncio.sleep(0.3)
            assert hub.reads == before
        finally:
            await hub.stop()
    asyncio.run(scenario())
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_session_hub.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'knowledge_admin.live'`.

- [ ] **Step 3: Write the hub**

Create `services/admin-api/src/knowledge_admin/live/__init__.py`:

```python
"""Live updates for pages: database notices fanned out to subscribers, one listener per API process."""
```

Create `services/admin-api/src/knowledge_admin/live/session_hub.py`:

```python
"""Live session pages without polling. Postgres announces session changes on the channel `ka_sessions` (migration
0083's triggers, delivered on commit); one listener per API process receives them, and the hub reads the new rows once
and hands them to every subscriber of that session (the event stream of the Test tab and Generate documents)."""
import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import psycopg
from fastapi.encoders import jsonable_encoder
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

CHANNEL = 'ka_sessions'
QUEUE_SIZE = 500  # messages a subscriber may fall behind before it must resync
PAGE = 500  # events per query
COALESCE_SECONDS = 0.05  # notices arriving together become one read
SWEEP_SECONDS = 30.0  # every watched session is read at least this often (a lost notice)
BACKOFF_START, BACKOFF_MAX = 1.0, 30.0
CONNECT_TIMEOUT = 5

STATE = """SELECT s.status,s.status_message,s.turns,s.input_tokens,s.output_tokens,s.cached_tokens,s.cost_usd,s.model_calls,s.reasoning_tokens,s.draft,
    s.draft_turn,s.document_id,coalesce(s.lease_expires_at > now(), false) AS worker_active,
    (SELECT count(*) FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued
    FROM ka.agent_sessions_cc s WHERE s.id=%s"""
EVENTS = """SELECT id,run_key,seq,kind,payload,created_at FROM ka.agent_run_events
    WHERE run_key=ANY(%s) AND id>%s ORDER BY id LIMIT %s"""

Message = tuple[str, Any]


class Subscription:
    """One page's view of a session: its queue of ('event', row), ('session', state), ('closed', None),
    ('unavailable', None) and ('resync', None) messages, the newest event id it was given and the last state sent."""

    def __init__(self, position: int):
        self.position = position
        self.queue: asyncio.Queue[Message] = asyncio.Queue(QUEUE_SIZE + 1)  # the extra place is for resync
        self.state: Any = None
        self.told_unavailable = False
        self.dropped = False

    async def get(self) -> Message:
        return await self.queue.get()


class Watched:
    """A session somebody watches: its subscribers and the newest event id read for it."""

    def __init__(self, read_to: int):
        self.subscribers: list[Subscription] = []
        self.read_to = read_to


class SessionHub:
    """One per API process (started in the app lifespan). `subscribe` gives a page its messages; a notice marks the
    session dirty and one reader task reads each dirty session once."""

    def __init__(self, conninfo: str, *, sweep_seconds: float = SWEEP_SECONDS, backoff_start: float = BACKOFF_START,
                 backoff_max: float = BACKOFF_MAX, coalesce_seconds: float = COALESCE_SECONDS):
        self.conninfo = conninfo
        self.sweep_seconds, self.coalesce_seconds = sweep_seconds, coalesce_seconds
        self.backoff_start, self.backoff_max = backoff_start, backoff_max
        self._watched: dict[UUID, Watched] = {}
        self._dirty: set[UUID] = set()
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.listening = asyncio.Event()  # set while LISTEN is active
        self.listener_pid: int | None = None
        self.reads = 0  # per-session reads so far (tests and diagnostics)

    async def start(self) -> None:
        """Start the listener and the reader; returns at once (the database may still be unreachable)."""
        self._tasks = [asyncio.create_task(self._listen(), name='session-hub-listener'),
                       asyncio.create_task(self._read_loop(), name='session-hub-reader')]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    @asynccontextmanager
    async def subscribe(self, session_id: UUID, after_id: int) -> AsyncIterator[Subscription]:
        """Watch a session from event id `after_id`: the hub's first read gives the events after it and the state."""
        subscription = Subscription(after_id)
        watched = self._watched.get(session_id)
        if watched is None:
            watched = self._watched[session_id] = Watched(after_id)
        watched.subscribers.append(subscription)
        self._mark([session_id])
        try:
            yield subscription
        finally:
            self._leave(session_id, subscription)

    def subscribers(self, session_id: UUID) -> int:
        watched = self._watched.get(session_id)
        return len(watched.subscribers) if watched else 0

    def _leave(self, session_id: UUID, subscription: Subscription) -> None:
        """Remove a subscriber; a session nobody watches any more is forgotten."""
        watched = self._watched.get(session_id)
        if watched is not None and subscription in watched.subscribers:
            watched.subscribers.remove(subscription)
            if not watched.subscribers:
                del self._watched[session_id]
                self._dirty.discard(session_id)

    def _mark(self, session_ids: Iterable[UUID]) -> None:
        """Mark watched sessions for the reader (unwatched ones are ignored)."""
        self._dirty.update(session_id for session_id in session_ids if session_id in self._watched)
        if self._dirty:
            self._wake.set()

    def _notice(self, payload: str) -> None:
        try:
            session_id = UUID(json.loads(payload)['s'])
        except (ValueError, KeyError, TypeError):  # never log the payload: it is not ours
            logger.warning('Ignoring a malformed %s notice', CHANNEL)
            return
        self._mark([session_id])

    def _put(self, session_id: UUID, subscription: Subscription, message: Message) -> bool:
        subscription.queue.put_nowait(message)
        return True

    async def _listen(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.conninfo, autocommit=True,
                                                         connect_timeout=CONNECT_TIMEOUT) as conn:
            await conn.execute(f'LISTEN {CHANNEL}')
            self.listener_pid = conn.info.backend_pid
            self.listening.set()
            self._mark(list(self._watched))  # anything committed before we listened
            async for notice in conn.notifies():
                self._notice(notice.payload)

    async def _read_loop(self) -> None:
        while True:
            await self._wake.wait()
            await asyncio.sleep(self.coalesce_seconds)  # a burst of notices becomes one read
            self._wake.clear()
            batch, self._dirty = self._dirty, set()
            await self._read_all(batch)

    async def _read_all(self, batch: set[UUID]) -> None:
        """Read the dirty sessions over one short-lived connection."""
        if not batch:
            return
        async with await psycopg.AsyncConnection.connect(self.conninfo, autocommit=True, row_factory=dict_row,
                                                         connect_timeout=CONNECT_TIMEOUT) as conn:
            for session_id in batch:
                await self._read(conn, session_id)

    async def _read(self, conn, session_id: UUID) -> None:
        """Read one watched session once: the state, then the events after the furthest-behind subscriber (PAGE at a
        time) over its turn keys (cc:<session>:<turn>). Each subscriber present when the read starts gets only events
        after its own position, then the state if it changed; a page that subscribes during the read was marked dirty
        and gets its own read. A missing row or a closed session ends every present subscription."""
        watched = self._watched.get(session_id)
        if watched is None:
            return
        self.reads += 1
        present = list(watched.subscribers)
        state = await (await conn.execute(STATE, (session_id,))).fetchone()
        if state is None:  # the session was removed
            self._close(session_id, present)
            return
        keys = [f'cc:{session_id}:{turn}' for turn in range(1, state['turns'] + 1)]
        after = min([subscription.position for subscription in present] + [watched.read_to])
        while keys:
            rows = await (await conn.execute(EVENTS, (keys, after, PAGE))).fetchall()
            for row in rows:
                for subscription in present:
                    if row['id'] > subscription.position and self._put(session_id, subscription, ('event', row)):
                        subscription.position = row['id']
            if rows:
                after = rows[-1]['id']
                watched.read_to = max(watched.read_to, after)
            if len(rows) < PAGE:
                break
        public = jsonable_encoder(state)
        for subscription in present:
            if public != subscription.state and self._put(session_id, subscription, ('session', public)):
                subscription.state = public
        if state['status'] == 'closed':
            self._close(session_id, present)

    def _close(self, session_id: UUID, subscribers: list[Subscription]) -> None:
        for subscription in subscribers:
            self._put(session_id, subscription, ('closed', None))
            self._leave(session_id, subscription)
```

- [ ] **Step 4: Run the hub tests to verify they pass**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_session_hub.py`
Expected: 7 passed.

- [ ] **Step 5: Run the admin-api suite**

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: all pass except the known failures (nothing uses the hub yet).

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/live/__init__.py services/admin-api/src/knowledge_admin/live/session_hub.py \
  services/admin-api/tests/test_session_hub.py
git commit -m "feat(admin-api): session hub reads a session once per burst of notices and fans out by position

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Hub resilience — reconnect, the 30 s check, resync, outage and recovery

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/live/session_hub.py` (replace `_put`, `_listen`, `_read_loop`; add
  `_outage`, `_recovered`)
- Test: `services/admin-api/tests/test_session_hub.py` (append)

**Interfaces:**
- Consumes: Task 2's `SessionHub` internals (`_watched`, `_dirty`, `_wake`, `_mark`, `_leave`, `_notice`,
  `_read_all`), `Subscription.dropped` / `told_unavailable`.
- Produces: the same public interface; `_put` now returns `False` for a dropped subscriber and turns a full queue into
  `('resync', None)` plus removal; `listening` is cleared while disconnected; `listener_pid` changes on reconnect.

- [ ] **Step 1: Write the failing resilience tests**

Append to `services/admin-api/tests/test_session_hub.py` (add `import psycopg` and `from psycopg import sql` to its
imports):

```python
def test_the_listener_reconnects_and_reads_every_watched_session_again(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store, backoff_start=0.5, backoff_max=1.0)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                old = hub.listener_pid
                await db(write, store, ('SELECT pg_terminate_backend(%s)', (old,)))
                await until(lambda: not hub.listening.is_set())
                await db(write, store, event(session, 1))  # its notice reaches nobody
                [(kind, row)] = await take(page, 1)
                assert kind == 'event' and row['seq'] == 1
                assert hub.listener_pid not in (None, old)
                assert await nothing_more(page)  # no 'unavailable': the database was reachable
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_the_periodic_check_finds_an_event_whose_notice_was_suppressed(store):
    session = a_session(store)

    async def scenario():
        hub = await running(store, sweep_seconds=0.3)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                # replica mode skips ordinary triggers: the event commits without a notice
                await db(write, store, ('SET LOCAL session_replication_role = replica', None), event(session, 1))
                [(kind, row)] = await take(page, 1, timeout=3)
                assert kind == 'event' and row['seq'] == 1
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_a_subscriber_that_falls_behind_gets_resync_and_is_dropped(store, monkeypatch):
    monkeypatch.setattr(session_hub, 'QUEUE_SIZE', 3)
    session = a_session(store)
    write(store, *[event(session, seq) for seq in range(1, 6)])
    ids = event_ids(store, session)

    async def scenario():
        hub = await running(store)
        try:
            async with hub.subscribe(session, 0) as slow, hub.subscribe(session, ids[3]) as current:
                assert summary(await take(slow, 4)) == [('event', ids[0]), ('event', ids[1]), ('event', ids[2]),
                                                        ('resync', None)]
                assert summary(await take(current, 2)) == [('event', ids[4]), ('session', 'working')]
                assert hub.subscribers(session) == 1
                await db(write, store, event(session, 6))
                assert [kind for kind, _ in await take(current, 1)] == ['event']
                assert await nothing_more(slow)
        finally:
            await hub.stop()
    asyncio.run(scenario())


def test_an_outage_sends_unavailable_once_and_recovery_resumes(store, postgres):
    session = a_session(store)
    with store.connect() as conn:
        name = conn.info.dbname

    def connections(allowed: bool) -> None:
        with psycopg.connect(postgres, autocommit=True) as admin:
            admin.execute(sql.SQL('ALTER DATABASE {} WITH ALLOW_CONNECTIONS {}').format(
                sql.Identifier(name), sql.SQL('true' if allowed else 'false')))
            if not allowed:
                admin.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s', (name,))

    async def scenario():
        hub = await running(store, backoff_start=0.05, backoff_max=0.2)
        try:
            async with hub.subscribe(session, 0) as page:
                assert summary(await take(page, 1)) == [('session', 'working')]
                await db(connections, False)
                assert summary(await take(page, 1)) == [('unavailable', None)]
                await asyncio.sleep(0.8)  # several failed reconnects
                assert page.queue.empty()  # told once
                await db(connections, True)
                await until(hub.listening.is_set)
                await db(write, store, event(session, 1))
                assert [kind for kind, _ in await take(page, 1)] == ['event']
        finally:
            await hub.stop()
            await db(connections, True)
    asyncio.run(scenario())
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_session_hub.py -k "reconnects or periodic or falls_behind or outage"`
Expected: FAIL — the reconnect, check and outage tests time out in `take` (`TimeoutError`; Task 2's listener ends
for good), and the resync test gets a fourth event instead of `resync` (Task 2's reader then dies with
`asyncio.QueueFull`).

- [ ] **Step 3: Replace `_put`, `_listen` and `_read_loop`; add `_outage` and `_recovered`**

In `services/admin-api/src/knowledge_admin/live/session_hub.py` replace the three methods with:

```python
    def _put(self, session_id: UUID, subscription: Subscription, message: Message) -> bool:
        """Queue a message. A subscriber whose queue is full gets resync instead and is dropped: its stream ends and
        the browser reconnects from its last event id."""
        if subscription.dropped:
            return False
        if subscription.queue.qsize() >= QUEUE_SIZE:
            subscription.dropped = True
            subscription.queue.put_nowait(('resync', None))
            self._leave(session_id, subscription)
            return False
        subscription.queue.put_nowait(message)
        return True

    def _outage(self) -> None:
        """The database cannot be reached: every subscriber hears it once; everything watched is read on recovery."""
        self._dirty.update(self._watched)
        for session_id, watched in list(self._watched.items()):
            for subscription in list(watched.subscribers):
                if not subscription.told_unavailable:
                    subscription.told_unavailable = True
                    self._put(session_id, subscription, ('unavailable', None))

    def _recovered(self) -> None:
        """Connected again: a later outage is told again, and every watched session is read."""
        for watched in self._watched.values():
            for subscription in watched.subscribers:
                subscription.told_unavailable = False
        self._mark(list(self._watched))

    async def _listen(self) -> None:
        """LISTEN on one connection. A lost connection is reopened after `backoff_start`; a connect that fails is an
        outage, retried with backoff doubling to `backoff_max`. Once listening again every watched session is read.
        Every `sweep_seconds` the connection is checked and every watched session read anyway (a lost notice)."""
        delay = self.backoff_start
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(self.conninfo, autocommit=True,
                                                                 connect_timeout=CONNECT_TIMEOUT) as conn:
                    await conn.execute(f'LISTEN {CHANNEL}')
                    self.listener_pid, delay = conn.info.backend_pid, self.backoff_start
                    self.listening.set()
                    self._recovered()
                    while True:
                        async for notice in conn.notifies(timeout=self.sweep_seconds):
                            self._notice(notice.payload)
                        await asyncio.wait_for(conn.execute('SELECT 1'), CONNECT_TIMEOUT)  # a dead connection raises
                        self._mark(list(self._watched))
            except (psycopg.Error, TimeoutError) as exc:
                lost = self.listening.is_set()
                self.listening.clear()
                self.listener_pid = None
                if lost:
                    logger.warning('Session hub listener lost its connection (%s); reconnecting', type(exc).__name__)
                else:
                    logger.warning('Session hub cannot reach the database (%s); retrying in %.1f s',
                                   type(exc).__name__, delay)
                    self._outage()
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.backoff_max)

    async def _read_loop(self) -> None:
        """Drain dirty sessions; a burst of notices (within coalesce_seconds) becomes one read per session. A failed
        read is an outage: the sessions stay dirty and are retried with backoff."""
        delay = self.backoff_start
        while True:
            await self._wake.wait()
            await asyncio.sleep(self.coalesce_seconds)
            self._wake.clear()
            batch, self._dirty = self._dirty, set()
            try:
                await self._read_all(batch)
            except Exception as exc:  # noqa: BLE001 - the hub must outlive any failed read
                if isinstance(exc, psycopg.Error):
                    logger.warning('Session hub cannot read sessions (%s); retrying in %.1f s', type(exc).__name__, delay)
                else:
                    logger.exception('Session hub read failed')
                self._outage()
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.backoff_max)
                self._wake.set()
            else:
                if delay != self.backoff_start:  # it had failed: recovered
                    delay = self.backoff_start
                    self._recovered()
```

(`_outage` re-marks every watched session, which covers the failed batch; `_read` skips sessions nobody watches any
more.)

- [ ] **Step 4: Run the hub tests to verify they pass**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_session_hub.py`
Expected: 11 passed.

- [ ] **Step 5: Run the admin-api suite**

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: all pass except the known failures.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/live/session_hub.py services/admin-api/tests/test_session_hub.py
git commit -m "feat(admin-api): session hub reconnects, checks every 30 s, resyncs slow pages and reports outages once

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The stream on the hub — endpoint, lifespan, no per-stream connection

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/app.py` (imports; `lifespan` in `create_app`)
- Modify: `services/admin-api/src/knowledge_admin/claude_code_api.py` (imports lines 4–14; constants lines 142–150;
  `events` lines 343–390)
- Modify: `services/admin-api/tests/test_claude_code_sessions.py` (a `live` fixture; the two stream tests at lines
  253 and 387)
- Test: `services/admin-api/tests/test_session_stream.py` (new)

**Interfaces:**
- Consumes: `SessionHub(conninfo)`, `start()`, `stop()`, `subscribe(session_id, after_id)`, `subscribers()`,
  `reads`, `session_hub.QUEUE_SIZE`; messages `('event', row)`, `('session', state)`, `('closed', None)`,
  `('unavailable', None)`, `('resync', None)`.
- Produces: `app.state.session_hub` (only when `repository_store` is given); `claude_code_api.LIVE_UNAVAILABLE =
  'Live session updates are not running.'`; test fixture `live` in `test_claude_code_sessions.py` (yields the running
  hub). Removed: `claude_code_api.STREAM_POLL_SECONDS`, `claude_code_api.STATE` (now `session_hub.STATE`).

- [ ] **Step 1: Give the existing stream tests a running lifespan**

In `services/admin-api/tests/test_claude_code_sessions.py`:

1. Change `from uuid import uuid4` to `from uuid import UUID, uuid4`.
2. After the `sessions` fixture add:

```python
@pytest.fixture
def live(sessions):
    """The sessions client with its lifespan running, so its session hub listens; yields the hub. Stream tests need it:
    a TestClient used without `with` never starts the lifespan."""
    with sessions.client as client:
        yield client.app.state.session_hub
```

3. `test_event_stream_replays_history_and_follows_a_fake_worker(sessions, monkeypatch)` becomes
   `test_event_stream_replays_history_and_follows_a_fake_worker(sessions, live)`; delete its first line
   `monkeypatch.setattr(claude_code_api, 'STREAM_POLL_SECONDS', 0.02)`. The rest of the body is unchanged.
4. Replace `test_event_stream_ends_when_the_session_is_gone_and_keeps_alive_sparingly` with (the stream assertions
   are unchanged; the per-stream connection count becomes "unsubscribed and forgotten"):

```python
def test_event_stream_ends_when_the_session_is_gone_and_keeps_alive_sparingly(sessions, live, monkeypatch):
    monkeypatch.setattr(claude_code_api, 'KEEPALIVE_SECONDS', 60)
    sid = start(sessions)['id']

    def remove():
        time.sleep(0.3)
        with sessions.store.connect() as conn:
            conn.execute('DELETE FROM ka.agent_session_inputs WHERE session_id=%s', (sid,))
            conn.execute('DELETE FROM ka.agent_sessions_cc WHERE id=%s', (sid,))

    thread = threading.Thread(target=remove)
    thread.start()
    response = sessions.client.get(f'/api/claude-code/sessions/{sid}/events', headers=HEADERS)
    thread.join()
    assert parse(response.text)[-1]['event'] == 'closed'
    assert ': keepalive' not in response.text  # well under KEEPALIVE_SECONDS
    assert live.subscribers(UUID(sid)) == 0  # unsubscribed and forgotten
    assert not hasattr(claude_code_api, 'STREAM_POLL_SECONDS')
    assert claude_code_api.KEEPALIVE_SECONDS == 60 and 10 <= claude_code_api.DEFAULT_KEEPALIVE_SECONDS <= 20
```

- [ ] **Step 2: Write the failing endpoint tests**

Create `services/admin-api/tests/test_session_stream.py`:

```python
"""The session event stream on the hub: keepalive, resync and resume, no database connection per open stream, a
worker's flush reaching an open stream without polling, and 503 when the hub is not running."""
import threading
import time
from uuid import UUID

from knowledge_admin import claude_code_api
from knowledge_admin.live import session_hub
from knowledge_admin.run_record import RunRecorder
from test_claude_code_sessions import live, parse, record, sessions, start  # noqa: F401  (fixtures)
from test_repositories import HEADERS


def wait_until(check, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, 'condition not reached'
        time.sleep(0.02)


def open_stream(client, sid: str, headers=HEADERS):
    """GET the stream in a thread (TestClient returns only when the stream ends); the returned function joins it and
    gives the response."""
    result = {}
    thread = threading.Thread(target=lambda: result.update(
        response=client.get(f'/api/claude-code/sessions/{sid}/events', headers=headers)))
    thread.start()

    def finish(timeout: float = 10.0):
        thread.join(timeout)
        assert not thread.is_alive(), 'the stream did not end'
        return result['response']
    return finish


def working(store, sid: str) -> None:
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='working',turns=1 WHERE id=%s", (sid,))


def close(store, sid: str) -> None:
    with store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions_cc SET status='closed',closed_at=now() WHERE id=%s", (sid,))


def test_a_quiet_stream_sends_keepalives(sessions, live, monkeypatch):
    monkeypatch.setattr(claude_code_api, 'KEEPALIVE_SECONDS', 0.1)
    sid = start(sessions)['id']
    finish = open_stream(sessions.client, sid)
    wait_until(lambda: live.subscribers(UUID(sid)) == 1)
    time.sleep(0.5)
    close(sessions.store, sid)
    response = finish()
    assert response.text.count(': keepalive') >= 2
    assert parse(response.text)[-1]['event'] == 'closed'


def test_a_stream_that_falls_behind_ends_with_retry_and_resumes_from_its_last_id(sessions, live, monkeypatch):
    monkeypatch.setattr(session_hub, 'QUEUE_SIZE', 2)
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    working(store, sid)
    with store.connect() as conn:
        for seq in range(1, 6):
            record(conn, sid, 1, seq, 'message', {'text': f'part {seq}'})
    first = client.get(f'/api/claude-code/sessions/{sid}/events', headers=HEADERS)
    runs = [e for e in parse(first.text) if e['event'] == 'run_event']
    assert [e['data']['seq'] for e in runs] == [1, 2]
    assert first.text.endswith('retry: 1000\n\n')  # the browser reconnects with Last-Event-ID
    monkeypatch.setattr(session_hub, 'QUEUE_SIZE', 500)
    close(store, sid)
    resumed = client.get(f'/api/claude-code/sessions/{sid}/events',
                         headers={**HEADERS, 'Last-Event-ID': str(runs[-1]['id'])})
    events = parse(resumed.text)
    assert [e['data']['seq'] for e in events if e['event'] == 'run_event'] == [3, 4, 5]
    assert events[-1]['event'] == 'closed'


def test_open_streams_hold_no_database_connection(sessions, live):
    store = sessions.store
    sid = start(sessions)['id']
    finishers = [open_stream(sessions.client, sid) for _ in range(10)]
    wait_until(lambda: live.subscribers(UUID(sid)) == 10)

    def connections() -> int:
        with store.connect() as conn:
            return conn.execute('''SELECT count(*) AS n FROM pg_stat_activity
                WHERE datname=current_database() AND pid<>pg_backend_pid()''').fetchone()['n']
    wait_until(lambda: connections() <= 2)  # the listener, and the reader's only while it reads
    for _ in range(5):
        assert connections() <= 2
        time.sleep(0.1)
    close(store, sid)
    for finish in finishers:
        assert parse(finish().text)[-1]['event'] == 'closed'
    assert live.subscribers(UUID(sid)) == 0


def test_a_worker_flush_reaches_an_open_stream_without_polling(sessions, live):
    store = sessions.store
    sid = start(sessions)['id']
    working(store, sid)
    finish = open_stream(sessions.client, sid)
    wait_until(lambda: live.subscribers(UUID(sid)) == 1)
    time.sleep(0.3)  # the opening read is done
    reads = live.reads
    recorder = RunRecorder(run_key=f'cc:{sid}:1', project_id=None, agent_id='ad-hoc', actor_id=None, secrets=[])
    recorder.add('message', {'text': 'Flushed by the worker'})
    recorder.flush()
    wait_until(lambda: live.reads > reads, timeout=2)  # the notice: the 30 s check is far away
    close(store, sid)
    events = parse(finish().text)
    assert [e['data']['payload']['text'] for e in events if e['event'] == 'run_event'] == ['Flushed by the worker']
    assert events[-1]['event'] == 'closed'


def test_the_stream_answers_503_when_the_hub_is_not_running(sessions):
    sid = start(sessions)['id']  # the client's lifespan never ran
    response = sessions.client.get(f'/api/claude-code/sessions/{sid}/events', headers=HEADERS)
    assert response.status_code == 503
    assert response.json()['error']['message'] == claude_code_api.LIVE_UNAVAILABLE
```

- [ ] **Step 3: Run the stream tests to verify they fail**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_session_stream.py tests/test_claude_code_sessions.py -k "stream"`
Expected: FAIL — the `live` fixture raises `AttributeError: 'State' object has no attribute 'session_hub'`, and the
503 test fails on `claude_code_api.LIVE_UNAVAILABLE`.

- [ ] **Step 4: Start the hub in the lifespan**

In `services/admin-api/src/knowledge_admin/app.py` add `from knowledge_admin.live.session_hub import SessionHub`
after the `knowledge_admin.documents` import, and replace `lifespan` with:

```python
    @asynccontextmanager
    async def lifespan(app):
        backfill = asyncio.create_task(backfill_document_exports(repository_store))
        hub = None
        if repository_store is not None:  # live session pages: one database listener per process
            hub = app.state.session_hub = SessionHub(repository_store.conninfo)
            await hub.start()
        try:
            yield
        finally:
            backfill.cancel()
            await asyncio.gather(backfill, return_exceptions=True)
            if hub is not None:
                await hub.stop()
```

- [ ] **Step 5: Put the endpoint on the hub**

In `services/admin-api/src/knowledge_admin/claude_code_api.py`:

1. Remove `import time`, `import psycopg` and `from psycopg.rows import dict_row` (only the old stream used them; keep
   `import asyncio`, `import json` and `from psycopg.types.json import Jsonb`).
2. Replace the block

```python
STREAM_POLL_SECONDS = 1.0
DEFAULT_KEEPALIVE_SECONDS = 15.0
KEEPALIVE_SECONDS = DEFAULT_KEEPALIVE_SECONDS
HISTORY_LIMIT = 100  # events per page; the session page opens with the latest page
STATE = """SELECT s.status,…
    FROM ka.agent_sessions_cc s WHERE s.id=%s"""
```

   with

```python
DEFAULT_KEEPALIVE_SECONDS = 15.0
KEEPALIVE_SECONDS = DEFAULT_KEEPALIVE_SECONDS
HISTORY_LIMIT = 100  # events per page; the session page opens with the latest page
LIVE_UNAVAILABLE = 'Live session updates are not running.'
```

3. Replace the `events` endpoint (from `@router.get('/sessions/{session_id}/events')` to the `return
   StreamingResponse(…)` that ends it) with:

```python
    @router.get('/sessions/{session_id}/events')
    async def events(session_id: UUID, request: Request, after: int = Query(0, ge=0)):
        """The session's live stream: run events after Last-Event-ID (or `after`), the state when it changes, `closed`
        at the end, `unavailable` once while the database cannot be reached, `: keepalive` after KEEPALIVE_SECONDS of
        quiet, and `retry: 1000` ending a stream that fell behind (the browser reconnects from its last id). The
        process's SessionHub reads the database; the stream holds no connection."""
        db = database()
        with db.connect() as conn:
            if conn.execute('SELECT 1 FROM ka.agent_sessions_cc WHERE id=%s AND project_id=%s',
                            (session_id, db.project(conn, app_name))).fetchone() is None:
                raise HTTPException(404, 'Session not found.')
        hub = getattr(request.app.state, 'session_hub', None)
        if hub is None:
            raise HTTPException(503, LIVE_UNAVAILABLE)
        last = request.headers.get('last-event-id', '')
        start_after = int(last) if last.isdigit() else after

        async def stream():
            # Leaving the page cancels this generator, which unsubscribes only this page.
            async with hub.subscribe(session_id, start_after) as subscription:
                while True:
                    try:
                        kind, value = await asyncio.wait_for(subscription.get(), KEEPALIVE_SECONDS)
                    except TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ': keepalive\n\n'
                        continue
                    if kind == 'event':
                        yield (f"id: {value['id']}\nevent: run_event\n"
                               f'data: {json.dumps(jsonable_encoder(stream_event(value)))}\n\n')
                    elif kind == 'session':
                        yield f'event: session\ndata: {json.dumps(value)}\n\n'
                    elif kind == 'unavailable':
                        yield 'event: unavailable\ndata: {}\n\n'
                    elif kind == 'resync':
                        yield 'retry: 1000\n\n'
                        return
                    else:  # closed
                        yield 'event: closed\ndata: {}\n\n'
                        return

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
```

- [ ] **Step 6: Run the stream tests to verify they pass**

Run: `cd services/admin-api && uv run --frozen pytest -q tests/test_session_stream.py tests/test_claude_code_sessions.py`
Expected: all passed.

- [ ] **Step 7: Run the admin-api suite**

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: all pass except the known failures. (`grep -rn "STREAM_POLL_SECONDS\|claude_code_api.STATE" services/admin-api`
finds nothing.)

- [ ] **Step 8: Commit**

```bash
git add services/admin-api/src/knowledge_admin/app.py services/admin-api/src/knowledge_admin/claude_code_api.py \
  services/admin-api/tests/test_claude_code_sessions.py services/admin-api/tests/test_session_stream.py
git commit -m "feat(admin-api): session streams subscribe to the hub instead of polling

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Documentation and full verification

**Files:**
- Modify: `docs/documentation.md` (after the `GET /api/claude-code/sessions/{id}/events/history` paragraph, line 134)
- Modify: `docs/agents.md` (the `useSessionEvents` sentence, line 33)
- Modify: `docs/database.md` (the `agent_sessions_cc` row of the table, line 35)
- Modify: `docs/migrations.md` (append `## 0083_session_notices`)
- Modify: `deploy/README.md` (`## Service boundaries`, after the `migrate` bullet)

**Interfaces:**
- Consumes: everything above; no code changes.

- [ ] **Step 1: Describe the live stream in `docs/documentation.md`**

After the paragraph that starts ``GET /api/claude-code/sessions/{id}/events/history` returns `{events, has_more}``
add:

```markdown
`GET /api/claude-code/sessions/{id}/events` is the live stream (Server-Sent Events): `run_event` events with their id
after `Last-Event-ID` (or `?after=`), `session` with the state when it changes, `closed` at the end, `unavailable` once
while the database cannot be reached (the stream stays open and resumes), `: keepalive` after 15 s of quiet, and
`retry: 1000` ending a stream that fell more than 500 messages behind (the browser reconnects from its last id). The
stream does not poll and holds no database connection: migration 0083's triggers announce every committed change to a
session's events, row or queued messages on the PostgreSQL channel `ka_sessions`, and one `SessionHub` per admin-api
process (`knowledge_admin/live/session_hub.py`, started in the app lifespan) listens, reads each changed session once
and hands the new events and state to every open stream of it. It reconnects with backoff (1 s doubling to 30 s) and
reads every watched session every 30 s anyway, so a lost notice delays an update by at most 30 s.
```

- [ ] **Step 2: Point to it from `docs/agents.md`**

Replace ``useSessionEvents` loads the latest page, earlier pages and the live stream;` with
``useSessionEvents` loads the latest page, earlier pages and the live stream (fed by the admin-api session hub, see
[documentation](documentation.md));`.

- [ ] **Step 3: Note the notices in `docs/database.md`**

Replace the row

```markdown
| `agent_sessions_cc`, `agent_session_inputs`, `agent_session_reviews` | Generate documents and Test sessions (`purpose`), their queued messages and tuning suggestions |
```

with

```markdown
| `agent_sessions_cc`, `agent_session_inputs`, `agent_session_reviews` | Generate documents and Test sessions (`purpose`), their queued messages and tuning suggestions; changes to a session, its queued messages and its `cc:<session>:<turn>` run events are announced on the channel `ka_sessions` (0083) |
```

- [ ] **Step 4: Add the migration to `docs/migrations.md`**

Append:

```markdown
## 0083_session_notices

Live session pages stop polling. Triggers send `pg_notify('ka_sessions', …)`, delivered on commit: a statement-level
`AFTER INSERT` on `ka.agent_run_events` sends one notice per session among the inserted `cc:<session uuid>:<turn>`
rows, `{"s": "<session uuid>", "e": <newest id>}` (other run keys send nothing); `AFTER UPDATE OR DELETE` on
`ka.agent_sessions_cc` and `AFTER INSERT OR UPDATE OR DELETE` on `ka.agent_session_inputs` send `{"s": "<session
uuid>"}` per row (identical notices in one transaction are folded). The down migration drops the triggers and their
functions; the stream then never hears of changes, so roll back admin-api with it.
```

- [ ] **Step 5: Note the listener in `deploy/README.md`**

In `## Service boundaries`, after the `migrate` bullet (it ends with `tokens and pull jobs use PostgreSQL.`), add the
bullet:

```markdown
- Each `admin-api` process keeps one PostgreSQL connection open for `LISTEN ka_sessions` (live session pages, see
  [documentation](../docs/documentation.md)); it must reach PostgreSQL directly, not through a transaction-pooling
  proxy, which does not deliver notifications.
```

- [ ] **Step 6: Run every suite**

Run: `bash db/tests/run.sh`
Expected: every `PASS:` line, exit 0.

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: all pass except the known failures (`test_environments.py` rollback test; the order-dependent docs-folder
test in `test_deploy.py`, which passes alone: `uv run --frozen pytest -q tests/test_deploy.py`).

Run: `if [ -d services/admin-ui/node_modules ]; then (cd services/admin-ui && npx vitest run && npx tsc --noEmit); else echo 'admin-ui: node_modules missing in this worktree, UI checks skipped (no UI change)'; fi`
Expected: PASS, or the skip line (report which).

Run: `python3 -m unittest discover -s tests`
Expected: OK.

Run: `git diff --stat main -- services/admin-ui`
Expected: no output (the client is unchanged).

- [ ] **Step 7: Commit**

```bash
git add docs/documentation.md docs/agents.md docs/database.md docs/migrations.md deploy/README.md
git commit -m "docs: session streams run on the LISTEN/NOTIFY session hub (0083)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage.**
- Database (`0083_session_notices`: channel, payloads, statement-level events trigger with `new_rows` and newest id,
  other run keys silent, session update/delete, inputs insert/update/delete, delivered on commit, down) → Task 1.
- The hub (one per process in the lifespan, listener with backoff 1 s → 30 s and re-read after reconnect, `subscribe`
  with a bounded 500 queue and the five message kinds, unsubscribe and forget, per-session subscribers / `read_to` /
  last state, dirty marking and one reader, one short-lived connection, 500 per query repeated, per-subscriber
  positions, state when changed, `closed` for a missing or closed row, a burst → one read) → Tasks 2 and 4.
- Safety nets (30 s check, re-read after reconnect, ordering by lease and recorder lock), back-pressure (`resync`,
  dropped), database unreachable (`unavailable` once, retry, recovery reads all) → Task 3.
- The stream endpoint (contract, exists check, subscribe with the resume id, the hub's first read, `retry: 1000`, no
  connection or polling, `STREAM_POLL_SECONDS` and the `AsyncConnection` removed, history unchanged) → Task 4.
- Errors table → Tasks 3 (listener lost, database down, slow browser, lost notice) and 2 (deleted / closed).
- Testing section → Task 1 (migration), Tasks 2–3 (hub), Task 4 (endpoint, ten streams, worker flush); docs → Task 5.

**Contradictions with the code, and how this plan resolves them.**
- "Existing stream tests pass unchanged": they patch `STREAM_POLL_SECONDS` (removed), count per-stream
  `AsyncConnection.connect` calls (`connected == [5]`), and use a `TestClient` without `with`, which never runs the
  lifespan that starts the hub. Task 4 adds a `live` fixture that enters the client's lifespan, drops the two
  `STREAM_POLL_SECONDS` patches and replaces the connection count with "unsubscribed and forgotten"; every assertion on
  the stream's content stays.
- Transition tables are not allowed on a trigger with several events, so the session-row and queued-message triggers
  are row-level (PostgreSQL folds identical notices per transaction); events stay statement-level as specified.
- "The last state sent" per session would leave a joining page without a state; it is kept per subscriber.
- `read_to` from zero would make `min(positions, read_to)` re-read the whole session every time; it starts at the
  first subscriber's position.
- Today `unavailable` ends the stream; the spec keeps it open and resumes. The client is unchanged, so its "Reload the
  page" message stays visible while events resume.
- `session_worker.claim` renews leases every second, so a working session sends a notice a second ("any update"); a
  watched one is read once a second per process, and a lease that expires without an update shows at the 30 s check.
