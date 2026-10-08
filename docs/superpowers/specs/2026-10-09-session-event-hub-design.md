# Session event hub (Postgres LISTEN/NOTIFY) — design

Date: 2026-10-09. Status: approved in conversation.

## Goal

Live session pages (the Test tab and Generate documents sessions) stop polling the database. Today every open stream
holds its own database connection and polls the session row and new events every second
(`claude_code_api.py` `GET /sessions/{id}/events`). After this change the database announces changes, one listener per
API process receives them, reads the new rows once and hands them to every page watching that session.

User decisions:
- Sessions only. The hub is generic so RAG jobs, collections and code projects can move onto it later as their own
  step.
- Notices come from database triggers, not from writer code.

Out of scope: other pages' polling; a connection pool for the rest of the API; changing the browser client.

## Database (`0083_session_notices`)

- **Channel:** `ka_sessions`. Payload: JSON `{"s": "<session uuid>"}` or `{"s": "<session uuid>", "e": <newest event
  id>}`; always far below the 8,000-byte NOTIFY limit.
- **Events:** a statement-level `AFTER INSERT` trigger on `ka.agent_run_events` (transition table `new_rows`) sends one
  notice per session among the inserted rows whose `run_key` matches `cc:<uuid>:<turn>`, with the highest inserted id
  for that session. Other run keys send nothing.
- **Session rows:** an `AFTER UPDATE` trigger on `ka.agent_sessions_cc` sends `{"s": id}` for each changed row (status,
  tokens, draft, turns, leases, stop, close — any update). `AFTER DELETE` sends it too, so streams of a removed session
  end.
- **Queued messages:** `AFTER INSERT OR UPDATE OR DELETE` on `ka.agent_session_inputs` sends `{"s": session_id}` (the
  queued count is part of the session state).
- Notices are delivered on commit, so readers never see an id before its row is visible.
- **Down:** drops the triggers and functions.

## The hub (`knowledge_admin/live/session_hub.py`)

- **One per API process**, started in the app lifespan next to the existing background task and stopped on shutdown.
- **Listener:** one async connection, autocommit, `LISTEN ka_sessions`; on connection loss it reconnects with backoff
  (1 s doubling to 30 s) and, once listening again, re-reads every watched session.
- **Subscribe:** `hub.subscribe(session_id, after_id) -> Subscription` (async context manager) gives a bounded
  `asyncio.Queue` (500 items) of messages: `('event', row)`, `('session', state)`, `('closed', None)`,
  `('unavailable', None)`, `('resync', None)`. Unsubscribe on exit; a session with no subscribers is forgotten.
- **Per watched session** the hub keeps its subscribers, each with its own last event id and the last state sent to it.
- **On a notice** for a watched session, the hub marks it dirty; a single reader task drains dirty sessions: for each,
  over one short-lived connection, it reads events `id > min(subscriber positions)` for the session's turn
  keys (500 per query, repeated until fewer come back) and the `STATE` row; it gives each subscriber only events with
  `id >` its own position (advancing that position) and the state when it changed; a missing row or `closed` status
  sends `closed`. A burst of notices for one session results in one read.
- **Safety nets:** every 30 s each watched session is read anyway (catches a lost notice); after the listener
  reconnects all watched sessions are read. Events in one session are written by one worker at a time (lease) and in
  order (recorder lock), so "after the last id" never skips a committed event.
- **Back-pressure:** if a subscriber's queue is full, it gets `resync` and is dropped; the stream ends with a
  `resync` instruction (below) and the browser reconnects from its last id.
- **Database unreachable:** subscribers get `unavailable` once; the hub keeps retrying; when it recovers it reads all
  watched sessions.

## The stream endpoint

- `GET /api/claude-code/sessions/{id}/events` keeps its contract: `id:` lines, `event: run_event`, `event: session`,
  `event: closed`, `event: unavailable`, `: keepalive` every 15 s of quiet, `Last-Event-ID` / `?after=` resume.
- It checks the session exists (as today), subscribes to the hub with the resume id, and forwards messages. The first
  read for a new subscriber (events after its id, and the state) is done by the hub, so a page that joined late gets
  exactly what it missed.
- `resync` ends the response with `retry: 1000` so the browser's EventSource reconnects with its `Last-Event-ID`.
- No per-stream database connection and no polling loop remain. `STREAM_POLL_SECONDS` and the per-subscriber
  `AsyncConnection` are removed.
- History (`/events/history`) is unchanged.

## Errors

| Problem | Behaviour |
| --- | --- |
| Listener connection lost | Reconnect with backoff; re-read watched sessions; streams stay open (keepalives) |
| Database down while streams are open | `unavailable` once per stream; recovery re-reads and resumes |
| Slow browser | `resync`: stream ends, browser reconnects from its last id |
| Session deleted / closed | `closed` to every subscriber |
| Lost notice | Picked up by the 30 s check |

## Testing

- **Migration:** notices for event inserts (one per statement per session, newest id), other run keys silent, session
  update and delete, input insert/update/delete; up/down; rollback pin.
- **Hub (real database fixture):** two subscribers at different positions get exactly their missing events; a burst of
  inserts results in one read (count queries); state only when changed; closed and deleted sessions; listener
  reconnect re-reads; the 30 s check finds an event whose notice was suppressed; full queue → resync; database outage
  → unavailable then recovery.
- **Endpoint:** contract unchanged (existing stream tests pass), resume from `Last-Event-ID`, keepalive, no
  per-stream connection (connection count stays flat with ten open streams), end-to-end: a worker-side
  `RunRecorder.flush` reaches an open stream without polling.
