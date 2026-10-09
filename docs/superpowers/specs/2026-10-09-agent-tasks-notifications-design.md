# Agent tasks and notifications — design

Date: 2026-10-09. Status: approved in conversation. Follows `2026-10-09-event-handlers-design.md` (merged as 42c8d43).
Temporal spike (branch `spike/temporal`, `docs/superpowers/spikes/2026-10-09-temporal-agent-tasks.md`): Temporal works
but is deferred; this design keeps tasks activity-shaped with deterministic ids so a later move stays cheap.

## Why

A "Send to agent" handler starts an interactive session today, and the agent's answer stays inside that session. When an
event triggers an agent, the agent should get one message, answer once without streaming, and the answer should land in
one place everyone can see: a notifications list.

## Decisions

- **Agent tasks** (`ka.agent_tasks`) are the hand-off between the event loop and LLM work. The event processor only
  inserts a task; a task runner in agent-session-worker runs it.
- One-shot, non-streaming: `agents.agent(config, prompt, credentials=..., stream=False)`, no history, no session.
- **Notifications** (`ka.notifications`): one shared list for every user, read-only, no read/unread, no recipients.
- Handlers no longer start `event` sessions. Existing `event` sessions stay viewable (purpose `event` stays allowed).
- Retries: 3 attempts in total (backoff 30 s, then 2 min); a disabled or deleted agent, or an agent that needs per-run
  resources, fails at once without retry.
- Out of scope: read/unread, recipients, Jira/Slack/email delivery, Temporal, manual retry, an answer becoming an event,
  notification retention or delete.

## Data model (`0090_agent_tasks_notifications`)

`ka.agent_tasks`:
- `id text PRIMARY KEY` = `agent-task:<event id>:<handler id>`, `project_id → projects`, `agent_id text` (no FK, like
  handlers), `agent_name text NOT NULL` (snapshot), `prompt text` (1–20000), `event_id → events ON DELETE SET NULL`,
  `handler_id uuid` (no FK), `status text CHECK (status IN ('queued','running','succeeded','failed'))` default
  `queued`, `attempts int ≥ 0` default 0, `claims int ≥ 0` default 0 (every claim, never decreased; names the run record), `next_attempt_at timestamptz NOT NULL DEFAULT now()`, `worker_id text`,
  `lease_expires_at timestamptz`, `error text` (≤ 2000), `notification_id bigint → notifications ON DELETE SET NULL`,
  `created_at`, `started_at`, `finished_at`.
- Checks: `running` ⇔ `lease_expires_at IS NOT NULL`; `finished_at IS NOT NULL` ⇔ status in (`succeeded`,`failed`).
- Index `agent_tasks_due ON (next_attempt_at) WHERE status IN ('queued','running')`.

`ka.notifications`:
- `id bigint identity`, `project_id → projects`, `created_at`, `kind text CHECK (kind IN ('agent.response'))`,
  `status text CHECK (status IN ('succeeded','failed'))`, `title text` (1–500), `body text` (≤ 200000, NUL-free),
  `event_id → events ON DELETE SET NULL`, `handler_id uuid`, `agent_id text`, `agent_task_id text` (no FK; the task
  points at the notification).
- Index `(project_id, id DESC)`.

`ka.event_handler_runs` gains `agent_task_id text` (no FK).

Down: drops the column and both tables.

## Event processor (`event_processor.py`, action `agent`)

- Agent missing → `Agent not found.`; disabled → the Test tab's message; per-run resources → the handlers API's per-run
  message. These fail the handler run as today.
- Render the prompt (`event_templates.render_prompt`), then
  `INSERT INTO ka.agent_tasks(...) VALUES (...) ON CONFLICT (id) DO NOTHING`.
- Run row: `succeeded`, message `Queued for <agent name>.`, `agent_task_id` set (`agent_session_id` stays NULL).
- Everything stays in the event's transaction (insert-only; no outside call).

## Task runner (`agent_tasks.py`, in agent-session-worker)

- Its own loop beside the session serve loop, with its own concurrency `AGENT_TASK_MAX` (default 2), polling every 2 s.
- **Claim** (one transaction): `SELECT … FROM ka.agent_tasks WHERE (status='queued' AND next_attempt_at <= now()) OR
  (status='running' AND lease_expires_at < now()) ORDER BY next_attempt_at, id LIMIT n FOR UPDATE SKIP LOCKED`, then
  `status='running'`, `worker_id`, `lease_expires_at = now() + 15 min`, `started_at = coalesce(started_at, now())`,
  `attempts = attempts + 1`, `claims = claims + 1`.
- **Run**: load the agent's current config (`get_agent_config`); missing/disabled/per-run/no usable model → final
  failure without retry. Load credentials (`load_credentials`), call `agent(config, prompt, credentials=…, stream=False,
  turn_timeout_seconds=600)`, recorded as an Agent runs record of kind `event_task` (run key `task:<task id>:<claim number>`, so a released attempt that runs again never reuses a key), the way
  Test sessions record theirs.
- **Finish** only if the row still has this `worker_id` and the same `lease_expires_at` (a stale worker records
  nothing):
  - success → insert notification (`succeeded`, title `<agent name> · <event type> <event subject>`, body = final answer,
    NUL stripped, capped at 200000) and set the task `succeeded`, `notification_id`, `finished_at`, lease cleared — one
    transaction.
  - error with `attempts < 3` → `status='queued'`, `next_attempt_at = now() + (30 s if attempts = 1 else 2 min)`,
    `error` = message, lease cleared.
  - error with `attempts = 3`, or a no-retry error → notification `failed` (body = the error) and task `failed`.
- Error text is redacted (the runtime's redaction) and NUL-stripped, ≤ 2000 chars in the task.
- The loop never dies: unexpected errors are logged, the loop sleeps until the next tick.

## API

- `GET /api/notifications?status&q&offset&limit` → `{notifications, next_offset}`, newest first; `limit` ≤ 200 (50);
  `q` matches title (case-insensitive, LIKE-escaped); rows `id, created_at, kind, status, title, preview (first 200
  chars of body), event_id, agent_id, agent_name`.
- `GET /api/notifications/{id}` → `{notification}` with `body`, `event: {id, type, subject} | null`,
  `task: {id, attempts, created_at, finished_at} | null`; 404 `Notification not found.`
- `GET /api/events/{id}` runs gain `agent_task: {id, status, attempts, notification_id} | null`.

## UI

- **Administration → Notifications** (`/notifications`, after Events): table Time · Status (Succeeded/Failed badge) ·
  Title · Preview; filters Status and search; Load more; Refresh. Row → sheet: title, status, time, agent, full answer
  (preformatted, wraps), **Open event** link (to `/events` with the event's sheet), attempts.
  Empty: `No notifications yet. A handler that sends events to an agent puts the agent's answers here.`
- Events → event sheet → Handler runs: a Send-to-agent run shows the task status (Queued, Running, Succeeded, Failed)
  and **Open notification** when there is one (to `/notifications` with that notification open).
- Handler form: Send to agent help text `The agent answers once, without a conversation. The answer goes to
  Notifications.`

## Testing

- Migration up/down and checks.
- Processor: agent action inserts one task (re-processing inserts none), run row links it, no session created; agent
  problems still fail the run.
- Runner (stub `agent()`): success → notification + task succeeded; failure → requeued with backoff, third failure →
  failed notification; no-retry errors; expired lease is reclaimed; stale worker's finish records nothing; two runners
  never run the same task; NUL in the answer is stripped.
- API: list filters/paging/search escaping, detail, 404; event detail carries `agent_task`.
- UI: Notifications table, filters, sheet, links; handler run task status and link.

## Docs

`docs/user-guide.md` (Events/handlers update, new Notifications section), `docs/agents.md` (event-triggered tasks run
once with no session), `docs/migrations.md` (0090).
