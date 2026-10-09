# Jira Auto pull page, New schedule, Jira events — design

Date: 2026-10-09. Status: approved in conversation. Follows `2026-10-09-schedules-design.md` (merged as ce962b8).

## Why

User feedback on the first version: the Auto pull switch hid on Jira → Tickets (and only after a Jira token was saved),
custom cron could not be chosen there, and Administration → Schedules could not create anything. Auto pull must be its
own settings with its own query. The scheduled pull should also put what it finds into a queue of events, in addition
to the tickets table (step 2 of the events roadmap, pulled forward; handling events stays the next step).

## Decisions

- **Jira → Auto pull** is a new page (`/jira/auto-pull`, sidebar entry between Tickets and Settings). It holds the
  pull's own JQL and ticket limit, separate from the manual Fetch tickets query.
- The Auto pull card on Jira → Tickets is removed; Tickets shows one status line linking to Auto pull.
- **Administration → Schedules** gets **New schedule** and **Delete**.
- **Events:** `ka.events` and a read-only **Administration → Events** page. The scheduled pull (and its Run now) records
  `jira.ticket.created` / `jira.ticket.updated` events; the manual Fetch tickets does not.
- The very first scheduled pull records a `created` event for every ticket it pulls (no silent baseline).
- Out of scope: event status/handling, consumers, retention, the Jira mock changes (dropped by the user), new target
  kinds, editing a schedule's query from the Schedules page.

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

## Events (`0088_events`)

`ka.events`: `id bigint identity`, `project_id → projects`, `source text CHECK (source IN ('jira'))`, `source_id uuid`
(the Jira connection), `type text` (lowercase dotted), `subject nonblank` (ticket key), `title text` (summary),
`payload jsonb` object, `dedupe_key nonblank`, `occurred_at timestamptz` (Jira `updated`), `created_at`.
`UNIQUE (project_id, dedupe_key)`; indexes `(project_id, id DESC)`, `(project_id, type, id DESC)` and, for the pull's
memory, `(project_id, source_id, (payload->>'issue_id'), id DESC) WHERE source = 'jira'`. Down drops the table.

**Recording** (`jira_events.py`, called by `jira_sync.pull_tickets(events=True)` inside the same transaction as the
ticket upsert, so a skipped/failed pull records nothing):
- Payload: `issue_id, key, url (<base>/browse/<key>), summary, description, status, issue_type, priority, assignee,
  reporter, labels, created, updated, changes`.
- Each pulled ticket is compared with the **latest event for that ticket from this connection** (not the tickets table,
  which the manual fetch also updates): none → `jira.ticket.created` (`changes: {}`); different `updated` →
  `jira.ticket.updated` with `changes`: `{field: {from, to}}` for summary, status, issue_type, priority, assignee;
  `labels: {added, removed}`; `description: {changed: true}`; same `updated` → nothing.
- `dedupe_key = jira:<connection id>:<issue id>:<updated>`; inserts use `ON CONFLICT DO NOTHING`.
- Run message: `Pulled {fetched} of {total} tickets · {n} new events.` (`1 new event.`; `0 new events.`). The audit
  row of the auto pull carries `events`.

**API** (`events_api.py`, read-only): `GET /api/events?source&type&q&before&limit` (newest first, `limit` ≤ 200,
default 50, `q` matches subject/title case-insensitively, `before` = id cursor) → `{events: [id, source, type,
subject, title, occurred_at, created_at], next_before}`; `GET /api/events/facets` → `{sources, types}`;
`GET /api/events/{id}` → `{event}` with `payload`, `source_id`, `dedupe_key`; 404 `Event not found.`

**UI — Administration → Events** (`/events`, after Schedules): filters **Source**, **Type** (from facets), search
**Ticket or title**; table Time · Type · Subject · Title; **Load more**; **Refresh**. A row opens a sheet: type label
(`Ticket created` / `Ticket updated`), subject and title, happened and recorded times, **Open in Jira**, the changes
(e.g. `Status: Open → In Progress`, `Labels: +agent −old`, `Description changed`) and the raw payload (collapsed).
Empty: `No events yet. Turn on Jira → Auto pull to record ticket changes.`

## Testing

- Migration up/down; auto_pull uses params and falls back; API create/duplicate/no-Jira/delete/params round trip.
- Events: first pull creates one per ticket; repeated pull none; changed ticket an `updated` event with the diff; a
  manual fetch in between does not hide the change; a clashing pull records none; API filters, cursor, 404.
- UI: Auto pull page (no connection, save body, cron error, run now), Tickets status line, New schedule and Delete,
  Events page (filters, load more, detail with changes).

## Docs

`docs/migrations.md` (0087, 0088), `docs/user-guide.md` (4.8 Jira Auto pull page and its events, 4.16 Schedules
New/Delete, 4.17 Events).
