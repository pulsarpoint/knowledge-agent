# Runs on servers, services and environments, and a Debug tab

Date: 2026-10-14. Status: agreed in conversation (2026-10-14), written for review.
Builds on: the sessions API (one `POST /api/sessions`, shared `AgentChat`), server discovery (0097, 0102) and the
"every save is a draft" change (discovery proposals create drafts).

## Why

A chat on a server page is always a **discovery** run (`purpose = 'discovery'`, URL `/servers/:id/discovery/:runId`)
with the "Server discovery" prompt, a full inventory survey. A simple question ("disk usage?", "is it alive?") is
treated as part of exploring the server: the agent runs broad scans (`du` over `/` and `/var`) instead of the one
command that answers (`df -h`). Services and environments have no chat at all. And nobody can see the exact
instructions a run sends to the model before sending a message.

Rules from the person using it:

1. A conversation on a resource is just a run **associated with that resource**; discovery is one job you can ask for,
   not the type of every conversation.
2. A resource can have **many open runs**.
3. Servers, services and environments all get this.
4. Service and environment runs may propose **documents**; proposing services stays a server-run tool.
5. A **Debug** tab shows what is sent to the model (prompt, instructions, tools) before and during a run.

## Runs started from a resource

- `POST /api/sessions` `started_from` accepts `{server_id}` (today), `{service_id}` or `{environment_id}`.
- `ka.agent_sessions`: `purpose` values `server`, `service`, `environment` replace `discovery` (migration renames
  existing rows); new columns `service_id` (FK services, ON DELETE CASCADE) and `environment_id` (FK environments,
  ON DELETE CASCADE) beside `server_id`; constraint: `purpose = 'server'` ⇔ `server_id` set, `'service'` ⇔
  `service_id`, `'environment'` ⇔ `environment_id`; ad-hoc runs (no `agent_id`) allowed for these purposes as for
  discovery today.
- Pinned resources: the starting resource, plus what it brings as today (`run_resources.bring`): a server brings the
  services deployed on it; a service brings its code projects and the servers it is deployed on; an environment brings
  its services and servers. `chosen_resources` = the starting resource (so a document proposed and published from the
  run is built for it).
- Readiness: a server run needs a usable SSH connection as today ("Set up SSH for this server first (SSH settings)."); a
  service or environment run starts without SSH when nothing it brings is reachable (it can still read the inventory
  and documents).
- Title: the first message, as for other runs.

## Prompts

- New seeded prompts (Administration → Prompts, editable, versioned like the others), `input_kind` = the resource type:
  - **Server assistant** — "Answer the person's question about the server in the context. Use the smallest read-only
    command that answers it with ssh_run (for example `df -h` for disk usage, `free -h` for memory, `uptime` for load,
    `systemctl is-active <unit>` for a service) and show the result. Read what is recorded (get_server_info_basic,
    get_server_services, get_server_docs) when it helps. Do not explore beyond the question and do not propose
    services or documents unless the person asks. Never show passwords, keys or tokens."
  - **Service assistant** and **Environment assistant** — the same shape for a service (get_service_info,
    get_service_docs, its deployments' servers) and for an environment (its services and servers).
- The default prompt of a new run is the resource type's assistant prompt. **Server discovery** stays (tag
  `server-discovery`) and is chosen in the chat's settings ("Prompt") when a survey is wanted; the settings list the
  prompts whose `input_kind` matches the resource type.
- Migration seeds the three prompts for every workspace (and for new workspaces via the existing seeding trigger
  pattern); down removes them when unedited (repo convention: refuse when customized).

## Proposals

- `propose_service`: server runs only (need `started_from_server`, unchanged).
- `propose_document`: server, service and environment runs (need: started from a resource). A proposal from a service
  run is about that service; from an environment run about that environment.
- `ka.discovery_proposals`: `server_id` becomes nullable; new `service_run_id`-free columns are not needed — the
  proposal's resource is recorded as `server_id` / `service_id` (existing column, now also "the run's service") /
  `environment_id` (new, FK environments ON DELETE CASCADE); exactly one of `server_id`, (run) `service_id`,
  `environment_id` identifies where it was proposed (constraint), the existing `service_id` meaning "the service a
  document is about" stays for server runs.
- Accepting a document proposal (creates a draft, unchanged) builds it for: its service, else the run's environment,
  else its server. Publishing attaches it to that inventory entry (`inventory_documents`, existing columns).
- The proposals list and accept/reject routes are unchanged in shape; `GET /api/discovery/{session_id}/proposals`
  works for any resource run.

## URLs and pages

- `/servers/:id/runs/:runId`, `/services/:id/runs/:runId`, `/environments/:id/runs/:runId`. The old
  `/servers/:id/discovery/:runId` redirects to the new server URL.
- New pages `/services/:id` and `/environments/:id` (the lists link to them): the resource's summary (as the list and
  its edit form show it today), its documents, its **runs** and the shared `AgentChat`. The server page keeps its
  layout and gets the same runs list.
- **Runs list** on each resource page: open runs first, then recent closed ones; title, status, last activity,
  proposals count (when any); **New chat**. `GET /api/sessions?server_id=|service_id=|environment_id=` lists them
  (replacing the `purpose='discovery'` filter).
- The run page shows a **Proposals** panel only when the run has proposals.

## Debug tab

- The shared `AgentChat` gets a tab **Debug** beside the conversation, everywhere it is used (resource runs, Ask agent,
  new documents, the agent Test tab).
- It shows, exactly as the model receives them:
  - **Instructions** — the assembled system prompt (prompt + agent instructions + skills + tool notes + context), with
    Copy;
  - **Prompt** — which prompt and version it came from, linking to it in Administration → Prompts; the agent's
    instructions and skills, labelled;
  - **Tools** — each tool's name and description as the model sees them;
  - **Resources** and **Model** — what the run is pinned to.
- Before the first message it comes from `POST /api/sessions/preview` (which gains `instructions` and `tools`); for an
  existing run from `GET /api/sessions/{id}/debug` (the pinned configuration, built the same way).
- One backend function builds the instructions and tool list from a configuration; the runner, the preview and the
  debug route all use it, so Debug cannot drift from what runs.
- Secrets never appear (the same redaction as run records).

## Out of scope

- Running discovery as a scheduled or event-driven job.
- Proposing services from service/environment runs.
- Changing what an Ask agent or new-document run does.

## Testing

- Start: `started_from {service_id}` / `{environment_id}` creates a run with that purpose and resource, pinned brought
  resources, `chosen_resources` = the resource; server runs unchanged; old discovery rows become `purpose='server'`.
- Prompts: the three assistant prompts are seeded; a new run's default prompt is its resource type's assistant; choosing
  Server discovery works; migration down refuses when an assistant prompt was edited.
- Tools: `propose_service` only in server runs; `propose_document` in all three; a document proposal from a service or
  environment run, accepted and published, is built for and attached to that service/environment.
- Lists: `GET /api/sessions?service_id=` / `environment_id=` / `server_id=` return that resource's runs.
- Debug: preview returns `instructions` equal to what the runner passes the SDK for the same configuration (assert by
  building both); `GET /sessions/{id}/debug` matches the pinned configuration; no secret values in either.
- UI: service and environment pages with runs list and New chat; run URLs and the old discovery redirect; Debug tab
  before and after the first message (instructions, prompt link, tools); Proposals panel only when present.
