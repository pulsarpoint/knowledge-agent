# Server discovery — design

Date: 2026-10-11
Status: approved in conversation. Second of two specs; the first (prompt library, tags, Administration → Prompts) is
merged as 7898277.

## Why

The inventory (servers, services, code projects, environments) is filled in by hand. An administrator should be able to
point an agent at one server, let it look around over SSH, and get back proposed services and documents that they
accept or reject one by one. Agents also need a way to read what the inventory already says, instead of receiving it
only as JSON in the system prompt.

## Overview

1. From a server's page, if SSH is configured and agent SSH is allowed, "Ask agent to collect info" opens a sheet with
   a prompt from the library (tag `server-discovery`), editable for this run, and a model.
2. Starting creates a **discovery run**: an agent session with purpose `discovery`, no saved agent, SSH limited to
   that server, the inventory read tools and the two proposal tools.
3. The agent calls `propose_service` / `propose_document`; each call is validated and stored as a pending proposal at
   once. Its final message is a short summary.
4. On the run page the administrator reviews proposals while the run is going or afterwards, and accepts or rejects
   each. Accepting writes to the inventory.

## Data — migration 0097

### Discovery runs are agent sessions

- `ka.agent_sessions.purpose` allows `discovery` (beside `documents`, `test`, `event`).
- `agent_id IS NULL` is allowed for `documents` and `discovery`.
- New column `server_id uuid REFERENCES ka.servers ON DELETE CASCADE`, with a CHECK: `server_id IS NOT NULL` exactly
  when `purpose = 'discovery'`. The server page lists its runs by it.
- At most one active discovery session per server when a run starts. "Active" means status `starting` or `working`
  (a `waiting` run is idle and can still take follow-up messages). Checked under a `FOR UPDATE` lock on the server row
  when a run starts — not a unique index, because a follow-up to an idle run moves it back to `working` and the worker
  must not fail on that.
- The run's pinned SSH allowlist is exactly that server.

### `ka.discovery_proposals`

| Column | Meaning |
|---|---|
| `id`, `project_id`, `created_at` | |
| `session_id` | the run; `ON DELETE CASCADE` |
| `server_id` | the server; `ON DELETE CASCADE` |
| `kind` | `service` or `document` |
| `payload jsonb` | service: the service fields, the deployment on this server, `depends_on`, `built_from`; document: `title`, `description`, `markdown` |
| `service_id` | service proposal: the existing service it matched (NULL = new); document proposal: the existing service it is about (NULL = server, unless `service_proposal_id`) |
| `service_proposal_id` | document proposal about a service that is itself a proposal in this run |
| `document_id` | document proposal: the existing document it would become a new version of (NULL = new) |
| `status` | `pending`, `accepted`, `rejected` |
| `decided_by`, `decided_at` | who and when |
| `result_id` | the service or document created or updated on accept |
| `revision` | optimistic locking for accept/reject |

`service_id` / `document_id` use `ON DELETE SET NULL` (a match that disappears turns the proposal into "new");
`service_proposal_id` cascades.

### Matching (done when the agent proposes)

- A service proposal matches an existing service of the same name, ignoring case, in the workspace.
- A document proposal matches a document with the same title (ignoring case) already attached to the same server or
  service.
- A second proposal of the same service (same name) or the same document (same title and target) in the same run
  replaces the first while that one is still pending; once decided, the new one is a new proposal.

## Agent tools

Added to the tool catalog (`tools/catalog.py`) as `ToolSpec`s.

### Inventory read tools — new read-only capability `inventory`

Any agent can be given them; a discovery run always has them. Every lookup is limited to the current workspace; a
server is found by hostname or name (case-insensitive).

- `get_server_info_basic(hostname)` — name, hostname, description, tags, environments; counts of services and
  documents.
- `get_server_services(hostname)` — services deployed on the server: name, type, role, short description (first 300
  characters), and the deployment (environment, runtime, runtime name, port, version).
- `get_server_docs(hostname)` — documents attached to the server: id, title, short description.
- `get_service_info(name)` — all service fields, deployments, connections (depends on, built from, connects to, both
  directions) and attached documents (id and title).
- `get_service_docs(name)` — documents attached to the service: id, title, short description.
- `get_project_info(name)` — a code project, the services built from it, and its attached documents.
- `get_document(document_id)` — the current version's title and Markdown of a document attached to any inventory
  entry, capped at 50,000 characters with a note when cut.

Lists stop at 100 items and say so. An unknown hostname/name answers a clear "not found" message (not an exception
that ends the run).

### Proposal tools — capability `discovery`, only in discovery runs

- `propose_service(name, service_type?, service_role?, description?, owner?, operations_notes?, deployment, depends_on?,
  built_from?)`
  - Fields and limits as in `ServiceInput`/`DeploymentInput`.
  - `deployment`: `runtime`, `runtime_name`, `port`, `log_location`, `deployed_version`, `environment?`. The server is
    the run's server. The environment defaults to the server's only environment; when the server is in several
    environments it must be one of them; when in none, the deployment has no environment.
  - `depends_on`: service names; `built_from`: code project names. They must name existing entries; an unknown name
    answers an error listing the valid names.
- `propose_document(title, markdown, description?, about_service?)` — attaches to the server, or to `about_service`
  (an existing service, or a service proposed earlier in this run).

Validation failures return the forms' wording to the agent as the tool result; the agent can call again. Success
answers e.g. "Proposal 3: update of service nginx" or "Proposal 4: new document".

The final answer is a short report: what was found, what was proposed, what could not be checked.

## API

- `POST /api/servers/{server_id}/discovery` `{prompt, model?, prompt_id?}` → creates the run (400 when the server is not
  SSH-configured or agent SSH is not allowed, with the reason; 409 "A discovery run is already running on this
  server."). The agent receives the server's id, name and hostname in its system prompt.
- `GET /api/servers/{server_id}/discovery` → runs with counts of pending/accepted/rejected proposals.
- `GET /api/discovery/{session_id}/proposals` → proposals; for service updates also the current values of the matched
  service and its deployment on this server, so the UI can show the comparison.
- `POST /api/discovery/proposals/{id}/accept` `{revision, fields?}` — `fields` lists the service fields to apply for an
  update (default: all changed).
- `POST /api/discovery/proposals/{id}/reject` `{revision}`.
- Live transcript: the existing session events stream.

### Accept

- **New service:** created through the same save logic as the Services form, with the deployment and connections.
- **Service update:** only the chosen fields change; the deployment on this server in the same environment is updated,
  or added if missing; missing connections are added (existing ones are kept). If the service's revision changed since
  the proposal was made → 409 "This service changed since the proposal was made. Review it again."
- **New document:** saved with origin `generated` and attached to the server, the matched service, or the service
  created from `service_proposal_id`.
- **Document matching an existing one:** a new version of that document.
- **Document about a pending service proposal:** 400 "Accept the service first."
- **Reject a service:** its pending document proposals are rejected too.
- A decided proposal cannot be decided again (409). Every accept and reject is audited.

## UI

- Servers table rows link to **`/servers/:serverId`**: header (name, hostname, environments, SSH configured / agent SSH
  allowed); Services (its deployments); Documents (attached, attach/detach); Discovery (runs with proposal counts).
- **"Ask agent to collect info"** — enabled only when SSH is configured and agent SSH is allowed; otherwise disabled
  with the reason. Sheet: prompt picker (usable prompts tagged `server-discovery`), the prompt text (filled from the
  chosen prompt, editable, edits not saved to the library), model (default as Generate documents), Start.
- **Run page `/servers/:serverId/discovery/:runId`** — live transcript (as Generate documents) beside a Proposals panel
  that updates as proposals arrive.
  - Service, new: all fields. Service, update: current vs proposed per changed field, each with a "keep" checkbox
    (all ticked). Accept / Reject.
  - Document: Markdown preview; "new" or "new version of <title>"; target. Accept / Reject.
- Proposals are not edited before accepting; edit the service or document afterwards as usual.

## Seeded prompt

Migration 0097 adds a seed function for a "Server discovery" prompt tagged `{server-discovery, ssh}` (explicit tags),
called for existing workspaces and wherever new workspaces get their built-in prompts. It instructs the agent to:
read what is recorded first (`get_server_info_basic`, `get_server_services`, `get_server_docs`); use only read-only
commands (e.g. `systemctl list-units`, `ss -tlnp`, `ps`, `docker ps`, `nginx -T`, package versions, configuration under
`/etc`) and never change, install or restart anything; never copy passwords, keys or tokens into proposals; propose
every service found (as an update when it exists); propose one server overview document and one document per
significant service; end with a short summary.

## Safety

SSH is unchanged: only this server, needs agent SSH allowed, sudo only if the server allows it, bounded time and output,
credentials redacted. Read-only is an instruction, not enforced; the user guide says so.

## Tests

- Migration: up/down; purpose/server_id CHECK; seed for existing and new workspaces.
- API: start a run (gating, one active run); proposal tools (validation errors returned to the agent, matching by name
  and title, a repeat replaces a pending proposal, unknown `depends_on`/`built_from`, environment default); accept
  (new service, update with chosen fields, deployment added/updated, connections added, 409 on a changed service, new
  document attached, new version of a matched document, "Accept the service first"); reject cascade; decided twice
  → 409; read tools (workspace scope, 100-item cap, unknown hostname, document cap).
- UI: server page, the button disabled with the reason, start sheet, live proposals panel, update comparison with keep
  checkboxes, accept and reject.

## Not in scope

Editing proposals before accepting; runs over several servers; scheduled re-discovery; enforcing read-only SSH;
proposing `connects_to` links; a review page across servers; agent-proposed links in the System map.
