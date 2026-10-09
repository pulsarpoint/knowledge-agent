# Jira Auto pull page, New schedule, live Jira mock — design

Date: 2026-10-09. Status: approved in conversation. Follows `2026-10-09-schedules-design.md` (merged as ce962b8).

## Why

User feedback on the first version: the Auto pull switch hid on Jira → Tickets (and only after a Jira token was saved),
custom cron could not be chosen there, and Administration → Schedules could not create anything. Auto pull must be its
own settings with its own query. Testing needs a Jira mock whose tickets change.

## Decisions

- **Jira → Auto pull** is a new page (`/jira/auto-pull`, sidebar entry between Tickets and Settings). It holds the
  pull's own JQL and ticket limit, separate from the manual Fetch tickets query.
- The Auto pull card on Jira → Tickets is removed; Tickets shows one status line linking to Auto pull.
- **Administration → Schedules** gets **New schedule** and **Delete**.
- The Jira mock gets write endpoints, optional simulated activity and a reset target.
- Out of scope: events, new target kinds, editing a schedule's query from the Schedules page.

## Data model (`0087_schedule_params`)

`ka.schedules.params jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(params) = 'object')` — target-specific settings.
For `jira.pull`: `{"jql": str, "limit": int}`. Down drops the column.

## Backend

- `jira_sync.auto_pull` reads `schedule['params']`: `jql` (else the connection's `jql`) and `limit` (else
  `last_fetch_limit`, else 25). The manual fetch no longer affects a schedule that has its own params.
- `PUT /api/jira/auto-pull` takes `{enabled, cron, timezone, jql, limit}` (`jql` 1–4000 chars, `limit` 1–200 strict) and
  stores `jql`/`limit` in `params`. `GET /api/jira/auto-pull` returns `{schedule, jql, limit, connected}`: the schedule's
  params, or for no schedule the defaults (connection `jql`, `last_fetch_limit` or 25); `connected` is false with no
  saved connection (`jql`/`limit` null then).
- `POST /api/schedules` `{target_kind, cron, timezone, enabled}` → 201 `{schedule}`. `jira.pull`: the project's primary
  connection (400 `Save Jira settings first.`), name `Jira auto pull`, params from the connection defaults. 409
  `This target already has a schedule.` Audit `schedule.created`. Unknown kind → 422 (pydantic `Literal`).
- `DELETE /api/schedules/{id}` → 204; 404 `Schedule not found.`; audit `schedule.deleted`. A run in progress finishes
  and records nothing (the scheduler's `finish` already returns when the row is gone).
- `GET /api/schedules` rows include `params`.

## UI

**Jira → Auto pull** (`JiraAutoPullView`):
- No saved connection → empty state "Connect Jira first" with a link to Settings.
- Form: switch **Pull tickets automatically**; **Jira query (JQL)**; **Number of tickets** (1–200); timetable fields
  (preset or Custom cron, time zone, preview of the next 3 runs); **Save**. A new schedule's time zone defaults to the
  browser's.
- Status: last result badge + message, last and next pull, **Run now** (disabled while running or before the first
  save), link to Schedules.
- Validation errors from the server show under the form; a cron error shows under the timetable fields.

**Jira → Tickets**: one status line above the fetch form: `Auto pull: <description> · next <time>` or
`Auto pull is off`, with link **Auto pull settings**. Hidden when the request fails or there is no connection.

**Administration → Schedules**:
- **New schedule** button opens a sheet: **What to run** (targets without a schedule; today only "Jira tickets"; if
  none are free: "Every target already has a schedule."), timetable fields, **Enabled** switch (on), **Create**.
- Row action **Delete** with a confirmation dialog ("Delete schedule <name>? Its history is deleted too.").
- Empty state: "No schedules yet. Create one with New schedule, or set up Jira → Auto pull."
- The timetable fields (preset/custom, time zone, preview) are one shared component used by Edit, New schedule and the
  Auto pull page.

## Jira mock (`deploy/jira-mock/server.py`)

In memory, thread-safe (one lock), same auth as reads, JSON bodies ≤ 64 KB, max 1000 issues:
- `POST /jira/rest/api/2/issue` `{fields: {summary, description?, issuetype?: {name}, priority?: {name}, labels?}}` →
  201 `{id, key, self}`; project DEMO, status Open, key `DEMO-<next>`.
- `PUT /jira/rest/api/2/issue/{idOrKey}` `{fields: {...same...}}` → 204.
- `GET /jira/rest/api/2/issue/{idOrKey}/transitions` → `{transitions: [{id, name, to: {id, name}}]}` (Open=1,
  In Progress=2, Done=3); `POST .../transitions` `{transition: {id}}` → 204.
- `POST /jira/rest/api/2/issue/{idOrKey}/comment` `{body}` → 201 comment.
- Every write sets `updated` to now. Search orders by `updated` desc, then key number desc.
- `JIRA_MOCK_ACTIVITY_SECONDS` (default 0 = off): a background thread alternately creates `Simulated ticket <n>` and
  moves the oldest-updated non-Done ticket to its next status.
- `make jira-mock-reset` restarts the container (state is in memory). Compose passes
  `JIRA_MOCK_ACTIVITY_SECONDS: ${JIRA_MOCK_ACTIVITY_SECONDS:-0}`.
- Bad JSON/fields → 400 `{errorMessages: [...]}`; unknown issue → 404; unknown transition → 400.

## Testing

- Migration up/down; auto_pull uses params and falls back; API create/duplicate/no-Jira/delete/params round trip.
- Mock: a test starts the server on a free port in a thread and covers create, edit, transition, comment, ordering,
  auth on writes, body limit, activity tick (call the tick function directly).
- UI: Auto pull page (no connection, save body, cron error, run now), Tickets status line, New schedule and Delete.

## Docs

`docs/migrations.md` (0087), `docs/user-guide.md` (4.8 Jira Auto pull page, 4.16 Schedules New/Delete, mock writes and
activity), `deploy/README.md` (mock activity variable and reset).
