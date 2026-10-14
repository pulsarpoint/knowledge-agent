# Chats on servers, services and environments; proposals by wizard; Debug tab

Date: 2026-10-14. Status: v2 — reworked with the person using it (2026-10-14), for review.
Builds on: the sessions API (one `POST /api/sessions`, shared `AgentChat`), server discovery (0097, 0102) and "every
save is a draft" (0105: accepted document proposals create drafts).

## Why

A chat on a server page is always a **discovery** run (`purpose = 'discovery'`, page `/servers/:id/discovery/:runId`)
with the "Server discovery" prompt and the proposal tools. A simple question ("disk usage?", "is it alive?") is treated
as part of an inventory survey, so the agent runs broad scans (`du` over `/` and `/var`) instead of the one command
that answers (`df -h`). Proposals live inside that run page. Services and environments have no chat. Nobody can see
the exact instructions a run sends to the model.

Rules from the person using it:

1. A chat on a resource is just a run **tagged with that resource**; a resource can have many chats, old ones can be
   reopened.
2. The resource page is the resource's information plus its chats; the chat is hidden until asked for.
3. Proposals have their **own page** per resource.
4. Proposing services or documents is a **separate action**: a small wizard whose result is a run request — a prompt
   built from the chosen options, with the proposal tools on. Plain chats have no proposal tools.
5. Servers, services and environments all get this. Service and environment proposals are documents only.
6. A **Debug** tab shows exactly what is sent to the model.

## Sessions carry tags

- `ka.agent_sessions.tags text[] NOT NULL DEFAULT '{}'` with a GIN index (migration 0106). A tag is a short string; the
  resource tags are `servers/<id>`, `services/<id>`, `environments/<id>` (lowercase type, the resource uuid).
- `POST /api/sessions` `started_from` accepts `{server_id}`, `{service_id}` or `{environment_id}`; the run gets the
  resource's tag (and keeps any tags the request adds, validated: ≤ 10 tags, each `^[a-z0-9_-]+(/[a-z0-9_-]+)*$`,
  ≤ 120 chars).
- `GET /api/sessions?tag=servers/<id>` lists a resource's chats (newest activity first; open ones first). It replaces
  the `server_id` + `purpose='discovery'` filter.
- `purpose`: runs started from a resource get `purpose = 'resource'` (one value; the tag says which resource).
  Existing `discovery` rows become `resource` with the tag `servers/<server_id>`; `agent_sessions.server_id` stays as
  the pinned server of server runs (other code uses it) and is set only for server runs.
- Pinned resources: the starting resource plus what it brings (today's `run_resources.bring`): a server brings the
  services deployed on it; a service its code projects and the servers it is deployed on; an environment its services
  and servers. `chosen_resources` = the starting resource.
- Readiness: a server chat needs a usable SSH connection as today; service and environment chats start without SSH
  when nothing they bring is reachable (inventory and documents still work).

## Prompts

- Seeded, editable, versioned prompts (Administration → Prompts), `input_kind` = resource type:
  - **Server assistant** — answer the person's question about the server in the context with the smallest read-only
    command that answers it (ssh_run: `df -h` for disk usage, `free -h` for memory, `uptime` for load,
    `systemctl is-active <unit>` for a service), show the result; read what is recorded (get_server_info_basic,
    get_server_services, get_server_docs) when it helps; do not explore beyond the question; never show passwords,
    keys or tokens.
  - **Service assistant**, **Environment assistant** — the same for a service (get_service_info, get_service_docs, its
    deployments' servers) and for an environment (its services and servers).
- A new chat's prompt is its resource type's assistant prompt. The settings sheet still lets a person pick another
  prompt of that `input_kind`.
- **Server discovery** stays in the library as the base text the wizard builds from (see below); it is no longer the
  default of any chat.
- Migration down removes the seeded assistant prompts when unedited (repo convention: refuse when customized).

## Proposals wizard

- A **Propose…** button on each resource page opens a small wizard (a dialog with steps):
  1. **What** — server: "Services on this server" and/or "Documents"; service and environment: "Documents".
  2. **Options** —
     - services: update the services already recorded (on/off), look for new ones (on/off);
     - documents: an overview document of the resource, one document per service (server/environment), which aspects
       to cover (configuration, logs, health checks, ports and paths — checkboxes, all on by default);
     - extra instructions (free text, optional).
  3. **Review** — the generated prompt (editable text) and the tools that will be on (`propose_service` when services
     were chosen, `propose_document` when documents were chosen, plus the resource tools); **Start**.
- Start calls `POST /api/sessions` with `started_from` the resource, the generated prompt as the run's system prompt,
  the selected tools on (proposal tools only here), the tag of the resource and the extra tag `proposals`; the page
  opens that chat (`?chat=<id>`) so the person can watch it.
- The generated prompt is built in the UI from fixed text blocks (the Server discovery prompt's steps, one block per
  chosen option) so the person sees and can edit exactly what will run; the backend accepts it like any run's prompt.
- Tools: `propose_service` is offered only to server runs whose request turns it on; `propose_document` to resource runs
  whose request turns it on. A plain chat never has them.

## Proposals page

- `/servers/:id/proposals`, `/services/:id/proposals`, `/environments/:id/proposals`: every proposal made in runs
  tagged with the resource, newest first, grouped by run (run title, when, who started it, link to open the run as a
  chat), with status filter (pending / accepted / rejected) and the existing Accept / Reject actions and their
  results (accepted documents link to their draft, then to the live document).
- `GET /api/discovery/proposals?tag=servers/<id>&status=` (new list across runs); accept/reject routes unchanged.
- `ka.discovery_proposals`: proposals from service/environment runs are allowed — `server_id` becomes nullable; the
  run's tag says where it was proposed; accepted documents are built for: the service the proposal is about, else the
  run's service/environment, else its server (as today for servers). Publishing attaches the document to that
  inventory entry (unchanged mechanism).
- The run page no longer shows proposals inline.

## Resource pages and URLs

- `/servers/:id`, `/services/:id` (new), `/environments/:id` (new). `?chat=<sessionId>` opens that chat; no `chat`
  parameter = no chat open. `/servers/:id/discovery/:runId` redirects to `/servers/:id?chat=:runId`.
- Page content: the resource's **Info** (what the list and edit forms show today, its documents, its deployments /
  services / servers) and the **Chats** list (open first, then recent; title, status, last activity, a `proposals`
  badge for wizard runs) with **New chat** and **Propose…**, and a link **Proposals** to the proposals page.
- The chat panel is hidden until **Chat** (or New chat / an item in the Chats list) is clicked.
- Layout: on extra-large screens (≥ 1536 px, Tailwind `2xl`) Info on the left, the chat on the right; below that, one
  column with tabs **Info** and **Chat** (the Chat tab appears once a chat is open or New chat is clicked).
- The services and environments lists link to their pages.

## Debug tab

- The shared `AgentChat` gets a tab **Debug** beside the conversation, everywhere it is used (resource chats, Ask
  agent, new documents, the agent Test tab).
- It shows, exactly as the model receives them:
  - **Instructions** — the assembled system prompt (prompt + agent instructions + skills + tool notes + context), with
    Copy;
  - **Prompt** — which prompt and version it came from (or "Written in the Propose wizard"), linking to it in
    Administration → Prompts; the agent's instructions and skills, labelled;
  - **Tools** — each tool's name and description as the model sees them;
  - **Resources** and **Model** — what the run is pinned to.
- Before the first message from `POST /api/sessions/preview` (gains `instructions` and `tools`); for an existing run
  from `GET /api/sessions/{id}/debug` (the pinned configuration, built the same way).
- One backend function builds the instructions and tool list from a configuration; the runner, preview and debug all
  use it, so Debug cannot drift from what runs. Secrets never appear (run-record redaction).

## Out of scope

- Scheduled or event-driven proposal runs.
- Proposing services from service or environment runs.
- Tags on runs other than resource tags and `proposals` (the column allows more later).

## Testing

- Sessions: start from a server/service/environment → `purpose='resource'`, the resource tag, pinned brought
  resources, `chosen_resources` = the resource; `GET /api/sessions?tag=` lists them; tag validation; migration turns
  discovery rows into `resource` with the server tag, round trip down/up.
- Prompts: the three assistant prompts seeded; a new chat uses its type's assistant prompt; down refuses when edited.
- Tools: a plain chat has no proposal tools; a run whose request turns them on has exactly those; `propose_service`
  refused for service/environment runs.
- Proposals: list across runs by tag and status; a document proposal from a service/environment run, accepted and
  published, is built for and attached to that resource.
- Debug: preview `instructions` equal what the runner passes the SDK for the same configuration (build both and
  compare); `GET /sessions/{id}/debug` matches the pinned configuration; no secrets.
- UI: resource pages (Info, Chats list, chat hidden until Chat, `?chat=` opens a chat, 2xl two columns vs tabs),
  old discovery URL redirect, Propose wizard (steps, generated prompt editable, tools listed, Start opens the chat),
  proposals page (grouping, filter, accept/reject), Debug tab before/after the first message.
