# Schedules (cron) and Jira auto pull — design

Date: 2026-10-09. Status: approved in conversation (step 1 of the events roadmap below).

## Goal

The system can run work on a timetable. A **schedule** is a cron expression in a time zone that runs one **target**,
a named job with its own code. The first target is the **Jira auto pull**: the same ticket fetch as the Fetch tickets
button on Jira → Tickets, run on a schedule.

User decisions:
- Our own small scheduler on PostgreSQL plus the `croniter` package. No APScheduler, no Temporal for now (Temporal gets
  a spike at the handling step, see Roadmap).
- Schedules call their target directly; they do not write events.
- Smaller steps: this step is schedules plus Jira auto pull only.

Out of scope: the events table and its page, raw Jira events, event handlers, consumers (agents watching events),
schedules that run agents, creating schedules by hand on the Schedules page, paging through every changed ticket.

## Roadmap (for context, not built here)

1. **This spec:** schedules and Jira auto pull.
2. `ka.events` and Administration → Events. The auto pull compares tickets with the stored snapshot and emits raw
   events `jira.ticket.created` / `jira.ticket.updated` with `changes` (status from/to, labels added/removed); the first
   pull is a baseline. The pull then pages through every ticket changed since the last pull instead of "latest N".
3. Event handlers: conditions in a form (input event types, conditions on fields, output event type) that turn events
   into new events; each handler keeps its place in the event stream; chains are capped. Handler `kind` leaves room for
   sandboxed Python later.
4. Handling: agents (or people) consume events. A short Temporal spike decides between Temporal and our own handling.

## Data model (`0086_schedules`)

`ka.schedules` — one row per scheduled target:

| column | type | notes |
|---|---|---|
| `id` | uuid PK | |
| `project_id` | uuid NOT NULL → projects | |
| `name` | nonblank | e.g. "Jira auto pull" |
| `target_kind` | text NOT NULL | `CHECK (target_kind IN ('jira.pull'))`; later steps widen the check |
| `target_id` | uuid NOT NULL | for `jira.pull`, the `jira_connections.id`; no foreign key (targets live in different tables) |
| `cron` | text NOT NULL | five-field cron expression |
| `timezone` | text NOT NULL DEFAULT 'UTC' | IANA name |
| `enabled` | boolean NOT NULL DEFAULT true | |
| `next_run_at` | timestamptz | NULL while paused |
| `lease_expires_at` | timestamptz | set while a run is in progress |
| `last_run_at`, `last_finished_at` | timestamptz | |
| `last_status` | text | `CHECK (last_status IN ('succeeded','failed','skipped'))` |
| `last_message` | text | short result ("Pulled 25 of 140 tickets") or the error |
| `revision` | integer NOT NULL DEFAULT 1 | optimistic locking for edits |
| `created_at`, `updated_at` | timestamptz | |

`UNIQUE (project_id, target_kind, target_id)` — one schedule per target. Index on `next_run_at WHERE enabled`.

`ka.schedule_runs` — history: `id bigint identity`, `schedule_id → schedules ON DELETE CASCADE`, `started_at`,
`finished_at`, `status` (`succeeded`/`failed`/`skipped`), `message`, `trigger` (`schedule`/`manual`). The worker keeps
the newest 100 runs per schedule and deletes older ones after each run.

Down migration drops both tables.

## Cron rules (`knowledge_admin/schedules.py`)

- `croniter` added to admin-api dependencies.
- Valid: exactly five fields, `croniter.is_valid`, and the time zone is in `zoneinfo.available_timezones()`.
- Minimum interval 5 minutes: the next 10 fire times may be no closer than 5 minutes apart. Error:
  `Schedules may run at most every 5 minutes.`
- `next_after(cron, timezone, after)` returns the next fire time in UTC, computed in the schedule's time zone (so "9:00
  daily" follows daylight saving time).
- `describe(cron)` gives plain words for the presets (`Every 15 minutes`, `Every 30 minutes`, `Every hour`,
  `Every 6 hours`, `Every day at 06:00`); anything else shows the expression.
- Presets: `*/15 * * * *`, `*/30 * * * *`, `0 * * * *`, `0 */6 * * *`, `0 6 * * *`.

## Scheduler loop

Runs as a second thread in **repository-worker** (`knowledge-repo-worker`), which already pulls from outside systems
and has `CREDENTIAL_ENCRYPTION_KEY`. No new container.

Every 5 seconds:
1. **Claim** one due schedule in one statement: `enabled AND next_run_at <= now() AND (lease_expires_at IS NULL OR
   lease_expires_at < now())`, `ORDER BY next_run_at FOR UPDATE SKIP LOCKED LIMIT 1`; set
   `lease_expires_at = now() + 15 minutes`, `last_run_at = now()`. Several workers never run the same schedule.
2. **Run** the target outside that transaction: `TARGETS[target_kind](store, schedule) -> str` returns the message;
   an exception means failed. Unknown or missing targets fail with `Target no longer exists.`
3. **Finish:** write `last_*`, insert a `schedule_runs` row, clear the lease, and set `next_run_at = next_after(cron,
   timezone, now())`. Computing from now means downtime causes **one** catch-up run, not one per missed slot.
4. Loop again immediately when a schedule ran, otherwise wait 5 seconds.

A run that outlives its 15-minute lease (crashed worker) becomes claimable again. Database errors are logged and the
loop retries, like the repository loop. Stopping the worker (SIGTERM) lets a running target finish its current call.

## Jira auto pull (target `jira.pull`)

- The fetch code in `jira_api.fetch_tickets` moves to `knowledge_admin/jira_sync.py`:
  `pull_tickets(store, project_id, jql, limit, actor_id | None, transport=None) -> dict` (credentials, Jira call,
  snapshot upsert, `fetch_revision` check, audit row). The endpoint and the target both call it; behaviour of the
  button is unchanged. It raises a plain `JiraSyncError(message)`; the endpoint maps it to the same HTTP status and
  text as today.
- The target uses the connection's saved `jql` and `coalesce(last_fetch_limit, 25)` — what the last manual fetch used.
- Audit action `jira.tickets_auto_pulled` with an empty actor; details `{requested, fetched, total}`.
- Message on success: `Pulled {fetched} of {total} tickets.`
- Fails (recorded, retried at the next fire, never faster): connection missing, no token, disabled connection, Jira
  error, unreadable token — with the same texts the button shows.
- A clash with a manual fetch (`fetch_revision` changed) ends the run as `skipped` with
  `A manual fetch ran at the same time.`
- Changing Jira URL, username or token keeps the schedule.

## API

- `GET /api/schedules` → `{schedules: [...]}` each with `id,name,target_kind,target_id,cron,description,timezone,
  enabled,next_run_at,running,last_run_at,last_finished_at,last_status,last_message,revision` (`running` = lease
  active).
- `GET /api/schedules/{id}/runs` → newest 20 runs.
- `PUT /api/schedules/{id}` `{cron, timezone, enabled, revision}` → validates, recomputes `next_run_at` (NULL when
  disabled), bumps `revision`; 409 `Schedule changed. Reload and try again.` on a stale revision; audit
  `schedule.saved`.
- `POST /api/schedules/{id}/run` → sets `next_run_at = now()` (also for a paused schedule: one run, it stays paused
  afterwards); 409 `This schedule is already running.` while leased; audit `schedule.run_requested`. A manual run's
  history row has trigger `manual`.
- `GET /api/jira/auto-pull` → `{schedule | null, jql, limit}`.
- `PUT /api/jira/auto-pull` `{enabled, cron, timezone}` → creates the connection's schedule on first enable (name
  `Jira auto pull`), otherwise updates it; 400 `Save Jira settings first.` without a connection.

## UI

**Administration → Schedules** (`/schedules`, sidebar entry after Agent runs, `CalendarClock` icon):
- Table: name, target ("Jira tickets"), when (plain words + time zone), next run, last run (status badge + message),
  and a switch for enabled.
- Row actions: Run now, Edit. Edit dialog: preset select plus "Custom" with a cron field, time-zone select, a preview of
  the next 3 runs (computed by the API from the unsaved values: `POST /api/schedules/preview {cron, timezone}` →
  `{runs: [...]}` or the validation error).
- Expanding a row shows its newest 20 runs.
- Empty state: "No schedules yet. Turn on Auto pull on Jira → Tickets."
- The page refreshes every 10 seconds while open.

**Jira → Tickets → Auto pull card** (above the fetch form):
- Switch "Pull tickets automatically", preset select, "Uses the query and limit of the last fetch: `<JQL>` · up to N
  tickets", and the last result + next run.
- A schedule with a custom cron shows its words and links to Schedules for editing.
- The time zone of a new schedule is the browser's (`Intl.DateTimeFormat().resolvedOptions().timeZone`), falling back
  to UTC.

## Errors and limits

- Bad cron / time zone / interval: 400 with the message; the dialog shows it under the field.
- A run never blocks the API: everything runs in the worker.
- "Latest N tickets" can miss changes when more than N tickets change between pulls; acceptable until step 2 adds
  paging.

## Testing

- Migration up/down (db/tests/run.sh) and a migration test for the check constraints and unique key.
- `schedules.py`: validation (fields, time zone, 5-minute minimum), `next_after` across a DST change, `describe`.
- Scheduler: claim is exclusive (two claimers, one row), lease expiry makes a row claimable, missed slots run once,
  failure records the message and still advances, history keeps 100 rows, unknown target fails.
- Jira: the button still behaves as before (existing tests), the target pulls with the saved JQL/limit, clash →
  skipped, no token → failed; audit rows.
- API: list, update with stale revision, run now while running (409), paused run-now stays paused, auto-pull create
  and update, preview.
- UI (Vitest): Schedules table and edit dialog with preview and errors; Auto pull card on/off and custom-cron link.

## Docs

`docs/migrations.md` (0086), `docs/user-guide.md` (Schedules, Auto pull), `deploy/README.md` (repository-worker now also
runs schedules).
