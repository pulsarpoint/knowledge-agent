# One sessions API, part 5: event handlers start ordinary agent runs — design

Date: 2026-10-13
Status: approved in conversation, section by section. Builds on parts 1–4 (`2026-10-13-sessions-api-design.md` …
`…-part4-design.md`; merged up to 386f855).

## Why

Event handlers are the last place that runs an agent another way: a matching "Send to agent" handler queues a row in
`ka.agent_tasks`, and the agent-session-worker runs it as a one-shot call (no session, no conversation, no run page,
3 attempts with backoff, a 10-minute limit) and writes the answer to Notifications. Meanwhile the internal
`agent_sessions.start_session` that part 2 rebuilt for event handlers has no production caller. Part 5 makes a handler's
call an ordinary run started through the same start as everything else, and removes the second path.

## Decisions

- A handler's agent call is an ordinary run (purpose `event`), visible in the agent's Earlier runs and on
  `/sessions/:id`, continuable by a person; no automatic retries.
- A handler keeps its agent and prompt template; the run uses the saved agent with everything it has on. Per-run agents
  stay refused.

## Backend

- **One start function.** The start route's logic moves into `sessions.start.start_run(conn, project, actor, value,
  conninfo, *, purpose=None, event_id=None, handler_id=None) -> UUID` (build, insert the session row and its first input,
  audit). `POST /api/sessions` calls it; so does the event processor. `agent_sessions.start_session` and its tests are
  deleted (the tuning review stays in `agent_sessions.py`).
- **A handler starts a run.** For a matching "Send to agent" handler, the event processor calls `start_run` with the
  handler's agent (`agent_id`), the rendered prompt as `message`, the agent's tools that are on, all its offered experts,
  subagents as the agent allows, nothing else — purpose `event` — and the handler's creator as actor. An agent that is
  missing, disabled, per-run or without a usable model fails the handler with today's messages (`Agent not found.`,
  `Agent is disabled.`, the per-run message, the no-model message). The handler's result line reads
  `Started a run of {agent}.` and its links carry `session_id`.
- **One run per event and handler.** `ka.agent_sessions` gains `event_id` and `handler_id` (nullable, FKs with `ON DELETE
  SET NULL`) and a unique constraint on `(event_id, handler_id)`; processing an event again starts nothing new.
- **Notifications link to the run.** When an event run's first turn ends (finished, failed or stopped), the worker writes
  one notification: the answer (succeeded) or why it failed (failed), with `event_id`, `handler_id` and the new
  `session_id` column. Later turns write none.
- **Removed:** `agent_tasks.py`, the worker's task loop and its wiring (claim, lease, backoff, turn limit), and the
  event processor's `send` queueing.
- **Migration 0103:** adds `agent_sessions.event_id`, `agent_sessions.handler_id` (+ unique) and
  `notifications.session_id` (FK, `ON DELETE SET NULL`); marks queued or running agent tasks `failed` with `Not run:
  event handlers start agent runs now. Send the event again.` and writes a failed notification for each; keeps
  `ka.agent_tasks` (read-only history). Down: drops the new columns (refuses while event runs with an event exist, as
  0099 refuses for revision runs).

## UI

- **Events page:** a "Send to agent" handler line shows the run's status badge and **Open run**
  (`/agents/{agent}?view=test&session_id={run}`), plus **Open notification** once it exists. Events handled before the
  update keep their task status and notification link (from the kept `agent_tasks` rows).
- **Notifications page:** a notification with a run shows **Open run** next to **Open event**.
- **Test tab:** event runs already appear in Earlier runs; opening one shows the conversation and it can be continued;
  Add feedback works as today.
- **Handler editor:** unchanged.

## Edge cases

- The event is processed again (retry of the event processor): the unique constraint keeps one run; the handler line
  shows the same run.
- The run is stopped or the worker loses it before the first turn ends: the notification says why (the run's status
  message).
- The agent is deleted later: its runs go with it as today; the notification keeps its text and its run link resolves to
  "Session not found".

## Tests

- Backend: a matching handler starts one run (purpose event, the agent's config, the rendered prompt, the creator as
  actor, `event_id`/`handler_id`); processing again starts nothing new; missing, disabled, per-run and no-model agents
  fail the handler; the first turn's end writes one notification with the run linked (succeeded and failed); later turns
  write none; 0103 fails open tasks and writes their notifications, then round-trips; `POST /api/sessions` behaves as
  before through `start_run`.
- UI: an event's handler line shows the run status and Open run; an older event's task line still shows; a
  notification shows Open run.

## Not in scope

Narrowing per handler (tools, experts, resources); per-run agents in handlers; automatic retries; dropping
`ka.agent_tasks`.
