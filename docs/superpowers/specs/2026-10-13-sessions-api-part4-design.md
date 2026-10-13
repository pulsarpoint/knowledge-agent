# One sessions API, part 4: server discovery, and reads that follow the allowlist — design

Date: 2026-10-13
Status: approved in conversation, section by section. Builds on parts 1–3 (`2026-10-13-sessions-api-design.md`,
`…-part2-design.md`, `…-part3-design.md`; merged up to a4eebe3).

## Why

Server discovery is the last start with its own endpoint (`POST /api/servers/{id}/discovery`) and page
(`/servers/:id/discovery/:runId`). Code adds behaviour to it — a built-in system prompt (`DISCOVERY_SYSTEM_PROMPT`, the
server's facts plus instructions) and an appended "reply with a short report" line — and its proposal tools find the run
through a pinned `discovery` key. Separately, reads are not on the allowlist: the inventory tools and `get_document` can
read any server, service, code project or attached document in the workspace. Part 4 makes discovery an ordinary run on
the server page's shared chat, and makes reads follow the run's allowlist like everything else.

## Decisions

- A discovery run lives in the same chat and settings sheet as Ask agent, on the server page; its proposals show in a
  panel above the server's details.
- Reads follow the run: inventory tools and `get_document` answer only about what the run was given (and documents
  attached to it). This applies to every run.

## Backend

**Discovery is an ordinary start.**
- `started_from` gains `{server_id}` (exactly one starting point). The run records purpose `discovery` and `server_id`
  (existing columns). The server must be ready (today's messages: `Set up SSH for this server first (SSH settings).`,
  `Turn on SSH allowed for this server first.`, `Server not found.`), and it is pinned as one of the run's SSH servers
  (the request's `resources.server_ids` must include it; the start adds it when missing).
- `propose_service` / `propose_document` find their run through `config['session_id']` (set by the worker) and the
  session's own `server_id` (purpose `discovery`), not a pinned `discovery` key. Their need becomes
  `'started_from_server'`: met when the pinned config records a started-from server (`config['started_from_server']`
  = `{id, name}`, pinned at start); they are on by default for such runs and never offered elsewhere. Refusal outside a
  server run: `The proposal tools work only in a run started from a server.`
- `GET /api/sessions?server_id=…` lists a server's discovery runs (newest first, with pending/accepted/rejected
  proposal counts as today). `GET /api/sessions/options?server_id=…`: prompts tagged `server-discovery` (default
  "Server discovery"), resources with the server linked (and its readiness reason), tools (propose tools included).
- **Removed:** `DISCOVERY_SYSTEM_PROMPT`, `DISCOVERY_FINAL`, `discovery_config`, the pinned `discovery` key,
  `POST /api/servers/{id}/discovery` and `GET /api/servers/{id}/discovery`. Proposal review routes
  (`/api/discovery/{session_id}/proposals`, accept, reject) stay.

**Migration 0102.**
- Rewrites the seeded "Server discovery" prompt to version 2 when it is not customized (one version, revision 1 —
  0097's guard), carrying what code used to add: read what the inventory records first (get_server_info_basic,
  get_server_services, get_server_docs); explore with read-only commands with ssh_run on the server named in the context;
  propose with propose_service and propose_document; when a proposal is refused, correct it and call again; never copy
  secrets; finish with a short report of what you found, proposed and could not check. A customized prompt is left
  alone (the docs say what to add). Down restores version 1 when version 2 is untouched.
- Closes open discovery runs with `Closed by an update; start a new chat.` and discards their queued messages; their
  proposals stay reviewable.
- The start's `context` for a discovery run is facts: `Server: {name} (hostname {hostname}, id {id}).`

**Reads follow the allowlist.**
- `allowed()` gains kinds `service` (pinned `config['services']['items']` when services are enabled) and `code_project`
  (pinned `config['code']['projects']` when code is enabled); `server` stays the pinned SSH servers.
- Inventory tools: `get_server_info_basic`, `get_server_services`, `get_server_docs` answer only for a server the run
  was given (by hostname or name); `get_service_info`, `get_service_docs` only for its services; `get_project_info` only
  for its code projects. Anything else: `This {kind} is not available in this run.` Lists inside an allowed answer (e.g.
  the services on a server) still name what the inventory records.
- `get_document` reads documents the run was given plus documents attached (ka.inventory_documents) to the run's servers,
  services and code projects; the workspace-wide fallback goes.
- Applies to every run: Ask agent, new documents, saved agents (their configured servers, services and projects) and
  discovery.

## UI

- **Server page:** an **Ask agent** header button toggles the shared chat beside the server (full-width 2:1), replacing
  "Ask agent to collect info". Scope `{kind: 'server', id, name}`; one open conversation per server (the latest open
  run), New chat, Earlier runs (`GET /api/sessions?server_id=`).
- **Settings sheet:** the server under Resources locked on (with its readiness reason when SSH is not ready); other
  servers, services and code projects may be added; prompts tagged `server-discovery` (default "Server discovery");
  propose tools on by default. Remembered under `agent-settings:server:{id}`.
- **Proposals panel** (where the draft bar sits for documents): above the server's details, the open run's proposals as
  they arrive, Accept / Reject as today (today's discovery page panel, moved into its own component), with a pending
  count.
- **Run links:** `/servers/:id/discovery/:runId` becomes a child of the server page and opens it with the chat on that
  run. `DiscoveryRunView` and its fallback go.
- `/sessions/:id` for a discovery run: "Started from the server" links to the server page on that run.

## Edge cases

- The server's SSH stops being ready during a run: the run keeps its pinned server; the next start is refused with the
  readiness message.
- A proposal accepted from the panel while the agent works: as today (the agent's later proposal for the same service
  replaces only pending ones).
- A saved agent's inventory reads: only its configured servers, services and projects (a fixed agent with
  "all collections"-style settings keeps what its config pins).

## Tests

- Backend: a discovery start with `started_from.server_id` (purpose, server pinned, context facts); readiness
  refusals; the propose tools work without the `discovery` key and refuse outside a server run; inventory tools and
  `get_document` refuse what the run was not given and allow attached documents — for an ad-hoc run, a saved agent and a
  discovery run; 0102 (prompt rewritten only when not customized, open runs closed, down restores v1); the removed
  routes answer 404.
- UI: Ask agent on the server page opens the chat; the request body (`started_from.server_id`, the server in
  resources); proposals appear and Accept/Reject work; the run link opens the chat on that run; Earlier runs.

## Not in scope

Event handlers (part 5); scoping RAG collection reads (already pinned); proposals on `/sessions/:id`.
