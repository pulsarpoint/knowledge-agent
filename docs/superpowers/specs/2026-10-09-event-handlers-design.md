# Event handlers, event status and chains — design

Date: 2026-10-09. Status: approved in conversation. Follows `2026-10-09-schedules-ui-design.md` (merged as d2ca6ce).
Steps 3 and part of 4 of the events roadmap.

## Why

Events from the Jira pull pile up in `ka.events` with nothing acting on them. A user needs to say "when an event like
this happens, do that": either produce a new, more specific event, or hand the event to an agent. Every event must show
where it came from (parent) and what it led to (chain), and the Events list must show the source and be filterable and
sortable.

## Decisions

- **Handler** = name, enabled, an event type pattern, field conditions (all must hold) and one action:
  **Emit event** (Jinja templates over the matched event) or **Send to agent** (a new agent session per matched event;
  its first message is a Jinja prompt template).
- **Event status** `pending` → `processed` | `failed`. A loop in repository-worker runs every 2 s, claims pending events,
  runs every matching enabled handler, then marks the event. No match → `processed`. A processed or failed event is never
  picked up again; there is no automatic retry.
- **Chains**: every event has `parent_id`, `root_id`, `depth`, `handler_id`. Emitted events enter as `pending`, so chains
  flow through the same loop. Depth cap 5.
- Existing events are marked `processed` by the migration (new handlers do not replay history).
- Matching is a form (type pattern + conditions), not code. Out of scope: retrying failed events, Python handlers, other
  event sources, an agent reporting that it finished handling an event.

## Data model (`0089_event_handlers`)

`ka.events` gains:
- `parent_id bigint REFERENCES events(id) ON DELETE SET NULL`, `root_id bigint` (self for a root, set by the insert),
  `depth int NOT NULL DEFAULT 0 CHECK (depth BETWEEN 0 AND 5)`, `handler_id uuid` (no FK: a deleted handler leaves
  history), `status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processed','failed'))`,
  `processed_at timestamptz`, `CHECK ((status = 'pending') = (processed_at IS NULL))`.
- `source` CHECK becomes `source IN ('jira','handler')`, plus `CHECK (source <> 'handler' OR handler_id IS NOT NULL)`
  and `CHECK (source = 'handler' OR (parent_id IS NULL AND handler_id IS NULL AND depth = 0))`. (A handler event's
  `parent_id` may become NULL if its parent is deleted, so it is not required.)
- `root_id` for existing rows = `id`; new roots get `root_id = id` via a `BEFORE INSERT` trigger when `root_id` is NULL
  and `parent_id` is NULL.
- Backfill: existing rows `status = 'processed'`, `processed_at = created_at`.
- Indexes: `events_pending ON (id) WHERE status = 'pending'`, `events_chain ON (root_id, depth, id)`,
  `events_by_parent ON (parent_id)`.

`ka.event_handlers`:
- `id uuid PK`, `project_id → projects`, `name` (1–120, unique per project), `enabled bool`,
  `type_pattern text` (lowercase dotted segments, `*` allowed as a whole segment or trailing `.*`; ≤ 200),
  `conditions jsonb` (array, ≤ 20 items, each `{field, op, value}`), `action text CHECK (action IN ('emit','agent'))`,
  `emit jsonb` (`{type, subject, title, payload}` templates; required iff `emit`), `agent_id text → agents(id)`
  (required iff `agent`), `prompt text` (≤ 20000; required iff `agent`), `created_by → principals` (the agent session's
  actor), `revision int`, `created_at`, `updated_at`.
- `ka.event_handler_runs`: `id bigint identity`, `event_id → events ON DELETE CASCADE`, `handler_id uuid` (no FK),
  `handler_name text` (snapshot), `status CHECK (status IN ('succeeded','failed'))`, `message text` (≤ 2000),
  `emitted_event_id → events ON DELETE SET NULL`, `agent_session_id → agent_sessions ON DELETE SET NULL`,
  `created_at`. Index `(event_id)`, `(handler_id, id DESC)`.
- `ka.agent_sessions` purpose CHECK widens to `('documents','test','event')`; the native-runtime check applies to
  `event` as to `test`.

Down: drops the tables and columns, restores the source and purpose checks (deleting `handler` events and `event`
sessions first).

## Matching (`event_matching.py`)

- **Type pattern**: `jira.ticket.updated` exact; `jira.ticket.*` any one-or-more trailing segments; `*` alone matches
  everything; `jira.*.created` a single segment wildcard.
- **Fields**: `source`, `type`, `subject`, `title`, `payload.<dotted path>` (list indexes not supported).
- **Operators**: `equals`, `not_equals` (string comparison of scalars; numbers/booleans compared as JSON values),
  `contains` (substring for strings, element for lists), `in` (value is a list), `matches` (regex via the `regex`
  module with a 0.1 s timeout; pattern ≤ 500 chars), `exists` (field present and not null; no value).
- A missing field fails every operator except `not_equals` (true) and `exists` (false).
- `explain(handler, event)` returns `None` on a match or the first failing check as text, e.g.
  `payload.status is "Done", not "In Progress"`, for Preview.

## Templates (`event_templates.py`)

- Jinja `SandboxedEnvironment`, `StrictUndefined`, autoescape off; context `event` = `{id, source, type, subject,
  title, occurred_at (ISO), payload, parent_id, root_id, depth}`; filter `tojson` available.
- Emit: `type` rendered then must match the event type regex; `subject` (default `{{ event.subject }}`) rendered,
  nonblank, ≤ 500; `title` (default `{{ event.title }}`) ≤ 500; `payload` (default `{{ event.payload | tojson }}`)
  rendered, parsed as JSON, must be an object. Any rendered output > 20000 chars fails.
- Agent prompt: rendered, nonblank, ≤ 20000.
- Template syntax is checked on save (`env.parse`); render errors surface as `Template error: <message>`.

## The loop (`event_processor.py`, thread in repository-worker)

Every 2 s: one transaction per event, up to 50 per tick, oldest first:
`SELECT … FROM ka.events WHERE status='pending' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED`. For the claimed event,
load enabled handlers of the project (ordered by name), and for each matching handler run its action inside a
savepoint:
- **emit**: if `depth = 5` → failed `Chain too deep (5 levels).`; else render and insert the child
  (`source='handler'`, `source_id = handler id`, `parent_id`, `root_id` of the parent, `depth+1`, `handler_id`,
  `occurred_at = now()`, `dedupe_key = handler:<handler id>:<parent id>`, ON CONFLICT DO NOTHING). Run row
  `succeeded`, message `Emitted <type>.`
- **agent**: render the prompt, then `start_event_session(conn, project, agent_id, actor=created_by, message)` — the
  same checks and inserts as the Test tab's create (`agent_sessions.py`), extracted into one function, with purpose
  `event` and title `<event type> <subject>`. Agents that need per-run resources, are disabled, or have no usable model
  fail the run with the same messages as the Test tab. Run row `succeeded`, message `Started a session with <agent>.`
- A failing action rolls back its savepoint and records a `failed` run with the message; other handlers still run.
- The event becomes `failed` if any run failed, else `processed`; `processed_at = now()`.
- Unexpected errors outside a handler are logged and the loop sleeps until the next tick (same pattern as the
  scheduler). A crash mid-transaction leaves the event `pending`.

## API

**Events** (`events_api.py`):
- `GET /api/events?source&type&status&handler_id&root_id&q&sort&order&offset&limit` — `sort` in
  `time|source|type|subject|status` (default `time`), `order` `asc|desc` (default `desc`), ties by id in the same order;
  `offset` ≥ 0, `limit` ≤ 200 (default 50). Response `{events, next_offset}` (`null` at the end). Rows add `source`,
  `status`, `depth`, `parent_id`, `root_id`, `handler_id`.
- `GET /api/events/facets` adds `statuses` and `handlers` (`[{id, name}]`).
- `GET /api/events/{id}` adds `parent` (summary row or null), `chain` (every event with the same `root_id`, ordered by
  depth then id, ≤ 200, summary rows), and `runs` (`[{handler_id, handler_name, status, message, emitted_event_id,
  agent_session_id, agent_id, created_at}]`).

**Handlers** (`event_handlers_api.py`):
- `GET /api/event-handlers` → `{handlers}` with `last_run` (`{status, created_at}` or null).
- `POST /api/event-handlers` → 201 `{handler}`; `PUT /api/event-handlers/{id}` (with `revision`; 409
  `This handler was changed by someone else. Reload and try again.`); `DELETE` → 204; 404 `Handler not found.`;
  409 `A handler with this name already exists.`
- Validation (400, field-specific messages): pattern syntax, condition field and operator, `value` present except for
  `exists` and a list for `in`, regex compiles, template syntax, action fields, agent exists and is a native agent.
- Audit `event_handler.created|updated|deleted`.
- `POST /api/event-handlers/preview` `{handler: <draft>, event_id}` → `{matches, reason, output}` where `output` is
  `{type, subject, title, payload}` or `{prompt}` (or `{error}` for a render error). Saves nothing.
- `GET /api/event-handlers/agents` → agents a handler can send to (`[{id, name}]`: native, enabled).

**Agent sessions**: `GET /agents/{id}/sessions` returns `test` and `event` sessions with `purpose`; the session view
accepts `event` sessions.

## UI

**Administration → Events** gets tabs **Events** and **Handlers** (`/events`, `/events/handlers`).

Events tab:
- Columns Time, Source, Type, Subject, Title, Status; clicking a sortable header (Time, Source, Type, Subject, Status)
  sorts it, clicking again flips the order; the arrow shows the direction. Sort and filters reset paging.
- Filters: Source, Type, Status, Handler, search **Ticket or title**. Load more (offset), Refresh.
- Status badges: Pending (outline), Processed (secondary), Failed (destructive).
- Detail sheet: **Chain** as an indented tree (depth), current event highlighted, other nodes clickable; **Handler runs**
  (handler, result, message, link to the emitted event or to the agent session `/agents/<id>?view=test&session_id=…`);
  changes and raw payload as today.

Handlers tab:
- Table: On (switch), Name, When (pattern + `n conditions`), Action (`Emit <type>` / `Send to <agent>`), Last run
  (badge + time), Edit, Delete (confirmation: `Delete handler <name>? Its run history stays on the events.`).
- Empty: `No handlers yet. Create one to turn events into new events or agent sessions.`
- **New handler** / Edit sheet: Name, Enabled, Event type (with suggestions from known types), Conditions (rows field ·
  operator · value, Add condition, remove), Action radio **Emit event** (Type, Subject, Title, Payload JSON template)
  or **Send to agent** (agent select, Prompt template), **Template help** (the `event.*` fields and a `tojson`
  example), **Preview** (pick one of the 50 newest events → `Matches` or `Doesn't match: <reason>`, then the rendered
  output or the error), Save.

Agent Test tab: event sessions appear in the session list with an `Event` badge and open like test sessions.

## Testing

- Migration up/down/backfill, checks (source/handler, depth, status/processed_at), root trigger.
- Matcher: each operator, dotted paths, missing fields, patterns, regex timeout/length.
- Templates: defaults, strict undefined, sandbox (no attribute access to internals), non-object payload, size limit.
- Loop: no handler → processed; emit child (parent/root/depth/handler, pending, processed next tick); depth cap; one
  failing handler + one succeeding → event failed, both runs; two workers never process the same event; agent action
  creates a session (purpose event, first input = prompt, actor = created_by); disabled agent → failed run.
- API: handler CRUD, revision 409, name 409, validation, preview; events sort/filter/offset, facets, detail chain/runs.
- UI: Events sorting and filters, Source/Status columns, chain tree, runs links; Handlers list, form both actions,
  conditions rows, preview, delete; Test tab shows event sessions.

## Docs

`docs/migrations.md` (0089), `docs/user-guide.md` (4.17 Events: status, chain, sorting; new 4.18 Event handlers).
