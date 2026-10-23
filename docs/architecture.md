# Architecture and admin workflows

The current deployment is a single workspace. Administration has no application selector or application-prefixed URLs; the API binds all operations to the existing database identity. Every service mounts `DATA_ROOT` read-only at `/work/data`; only `admin-api` and `repository-worker` write the code folders. Administration is the only web application; the former Reader application was removed.


Status: target design, with an independently runnable v2-derived baseline now
ported into v3. GitHub settings, encrypted tokens, repository pull jobs, and service
inventory with repository/server relationships, server metadata, and Jira
settings/recent-ticket retrieval are implemented; the other
management workflows below remain target design.
See the root README for the current runtime. Jira target
confirmed by the user: **Data Center / self-hosted**. PostgreSQL and the sibling
v3 project are implementation choices made for this first phase.

## Runtime structure

Run one React/TypeScript administration application with shadcn, React Router,
and Tailwind, with its own FastAPI process, web container, authentication proxy,
Keycloak client and session cookie. It requires `docs-admins` throughout. Keycloak
is the identity provider; existing administrators keep their accounts. The former
end-user Reader application was removed and will be redesigned later. Repository pulls run in a worker owned by the admin
backend. PostgreSQL supplies durable jobs; no extra queue broker is needed yet.

```mermaid
flowchart LR
    Admin --> AdminLogin[Admin login: Keycloak client]
    AdminLogin --> AdminUI[Admin React app :8183]
    AdminUI --> AdminAPI[Admin FastAPI]
    AdminAPI --> Data[(Project data)]
    AdminAPI --> DB[(PostgreSQL metadata and jobs)]
    Worker[Planned admin worker] --> DB
    Worker --> Jira[Jira Data Center]
    AdminAPI -. traces .-> Phoenix
    Worker -. traces .-> Phoenix
```

Current source layout:

```text
services/admin-ui/             Standalone React administration application
services/admin-api/            Standalone FastAPI administration service
  src/knowledge_admin/         Admin-only app, route definitions and CLI
services/code-graph/           CodeGraph indexing/query scripts bundled into admin-api
db/migrations/                Ordered application migrations
deploy/                       Separate web/API/auth services and shared infrastructure
```

The admin service owns its configuration (`knowledge_admin/config.py`). It currently provides
initialization, audit, repository, service-inventory and server-inventory
APIs, plus Jira settings, connection tests and recent-ticket retrieval. Document and server-access modules will be added there. Keep each workflow direct and avoid a separate service
for every settings page. The frontend apps have no cross-package source imports.

## Admin navigation

The sidebar has a project switcher and these sections.

| Page | Main content and actions |
| --- | --- |
| Projects | Create/edit/archive project, project members |
| Repositories | Repository links, requested branch/ref, local sync status, commit, Sync button |
| Services | Application details, many-to-many repository links, server/environment deployments, operational notes and agent context |
| Documentation | Select documentation set and its downloaded repositories; Generate, Upload, browse/download Markdown, delete files |
| Jira / Tickets | Implemented: fetch 1–200 recently updated tickets, saved snapshot, original Jira links. Planned: custom JQL and incremental sync |
| Jira / Settings | Implemented: Data Center URL, username, encrypted PAT, Test connection |
| Servers | Implemented: register/edit name, hostname/IP, description, tags and search. Planned: SSH port/user/key, host key, Allow sudo, enabled state, Test connection |
| Jobs | Queued/running/completed jobs, persisted progress, actionable plugin questions |
| Agent runs | Agent run records (`/activity/runs`); configuration/job events planned |

Use a Sidebar shell, Table listings, Field/FieldGroup forms, Switch for sudo,
Dialog/Sheet for edits, and Alert/Empty/Spinner for state feedback. Use v2's
existing shadcn components where suitable and install missing components via its
CLI. The file table includes path, origin, revision, changed time, and indexed
state. Generated and uploaded documents appear in the same set.

## Service inventory and agent context

`ka.services` identifies a logical application within a configured project. `ka.service_deployments` records
where each service runs: a server and a required environment (`environment_id`), runtime identifier, port, log
location and reported version; a service or server is in an environment when one of its deployments is.
`ka.connections` are network connections from a deployment to another deployment or to an external host, with port and
protocol; `ka.service_relations` are logical relations (`depends_on`, `part_of` to a service, `built_from` to a code
project, so code projects can be shared). Composite foreign keys prevent cross-project rows. Everything is written
through one module, `knowledge_admin/inventory` (`rules.py` validates, `store.py` is the only writer: revision checks,
delete rules and audit, `api.py` the REST API the UI uses); the UI's forms make one request per item.

The Servers page manages name, hostname/IP, description, tags and an SSH allowed switch; Services can
register a host with the same form before credentials exist. Updates require the
current revision. The store reads only a server's public columns (no credentials) for the
service context. Newly registered servers have `enabled=false`, `username=NULL`, and `allow_sudo=false`. The separate
`ssh_allowed` permission defaults false and is included in service context; changing
it does not configure or activate an SSH connection. The database
requires a username before server access can be enabled. No registration or
service-save path opens SSH, executes commands, pulls repositories, or probes URLs.

`inventory/assets.py` builds the service records the UI lists and
`GET /api/services/{id}/context` returns. The context response includes a schema
version, generation timestamp, and explicit operator-maintained provenance.
Only downloaded repositories expose checkout paths; `current_commit` is distinct
from the operator's `deployed_version`. Credentials are not part of these records.
The API requires the same global admin authorization as other management routes.

This resource is ready for a future agent-run adapter; current agents do not
consume it automatically. Live observations, evidence timestamps,
SSH/log gathering, health checks, and monitoring remain separate work. Consumers
must use a trusted authenticated boundary and treat descriptive text as untrusted
data, never as executable commands or grants of access.

## Agent session worker

New-document, Ask agent and Test sessions run in `agent-session-worker` (`knowledge_admin.knowledge_agents.native_sessions`),
claimed with a lease (`knowledge_agents/session_worker.py`:
queued messages delivered once per turn, Stop, idle release after 5 minutes, interrupted turns failed on reclaim). Native
turns have no time or model-call limit (each model request and MCP connection keeps a 300 s timeout); Stop always ends a
turn within about 30 seconds. Networks `default` and `agent-shell`; `DATA_ROOT` read-only;
`agent-workspaces` for tool scratch space shared with `shell-worker`; Agents SDK traces go to Phoenix as for native runs in
`admin-api`. History uses an Agents SDK `Session` (`knowledge_agents/history.py`) backed by `agent_sessions.history`.

## Code projects and Git synchronization

`ka.repositories` now represents code projects: a name and description, optional
Git source, and one or more `ka.code_project_folders` mappings inside `CODE_ROOT`.
Agent/service selections retain their existing project IDs. Administration browses
the code mount read-only; the repository worker has write access.

Saving a Git project specifies an unused relative clone folder and whether to
clone immediately. Manual pulls and optional periodic pulls create the same durable
`repository_sync` jobs. The worker schedules due projects from PostgreSQL and
serializes jobs per project. New clones stage under `.ka-sync/<id>` before moving
to the chosen folder. Later pulls update the same detached checkout after checking
that its HEAD, origin and working files still match the managed clone. Local
changes stop the pull. HTTPS URLs and refs are validated; authentication uses
write-only encrypted token records and transient askpass without token URLs.

Only a valid job lease can publish the successful commit, checkout path, folder
mapping and sync status. Existing snapshot-style projects keep their old folders
and IDs; editing one adopts its current checkout as the fixed clone path. Mapping
changes never remove source files. No automatic cache/snapshot cleanup is added.

## Generate documentation

The earlier job-based design with the Claude Agent SDK plugin was superseded by agent sessions (migrations 0073 and 0084); see [Documentation administration](documentation.md#write-a-new-document-with-an-agent).

## File uploads and directory consistency

`<upload root>/docs/<project-id>/<set-id>/` is the human-readable Markdown folder.
Immutable bytes live at `<upload root>/blobs/<project-id>/<revision-id>`. Database
file pointers are authoritative; the docs folder is a materialized view and is
repairable from those pointers after a crash.

Initial upload support is UTF-8 `.md`/`.markdown`, matching v2's actual ingestion
format. PDF/Word conversion is a later explicit feature, not a silent promise
that the Markdown index can consume arbitrary binaries.

Upload writes a temporary blob, validates size/type/path, durably renames it,
then locks the set and atomically creates the immutable revision, changes the
file pointer, increments the set revision, and appends an audit event. Batches
increment once and succeed or fail together. Resolve paths under the managed
root and reject symlinks, reserved internal directories, and traversal after
URL decoding; the SQL path domain is only a second check. File replacement is
an explicit upload option; conflicting names return a useful conflict response.

Deletion marks the file deleted and increments the set revision. Files omitted
from an upload remain present. Out-of-band filesystem changes require an explicit import/reconcile
operation before they become current database revisions.

## Document processing and search

Documents and their versions live in PostgreSQL. Agents find them with `search_documents`, a PostgreSQL full-text
search (title weighted above description above content) over the published documents the run may read, and read one
with `get_document`; see [documentation](documentation.md#document-tools). Memory search merges full text with vector
distance when a memory embedding model is set ([LLM Settings](llm-settings.md#memory-embeddings)). Source-code questions use read-only analysis of checkouts published by the repository worker. Administrators try agents in the agent Test page; conversations are scoped to the authenticated user. Phoenix tracks the agent execution.

## Jira Data Center

The implemented panel stores a base URL (including any Jira context path), token
owner username and encrypted PAT. It calls `/rest/api/2/myself` and
`/rest/api/2/search` with a Bearer PAT, preserves paths such as `/jira`, verifies
the username, and does not follow redirects. The ticket list fetches 1–200 issues
with `ORDER BY updated DESC, key DESC`, handling server page-size limits. See
[Jira setup, mock isolation and API contract](jira.md) for the current adapter.

Tickets are imported from Jira into the local admin list. No Jira issue creation,
comments, or remote edits are implied. The current fetch paginates `startAt/maxResults`,
returns retryable rate-limit errors, and upserts on `(connection_id, remote_id)`; issue
keys can change. Keep display columns alongside the original JSON. Jira comment
or attachment subresources require separate paginated fetches if later added;
`raw_payload` is not a guarantee that every subresource was downloaded.

Future incremental synchronization should advance its watermark only after all pages succeed, using the
run-start timestamp and an overlap window for late changes. Partial failures
must not mark unseen tickets deleted. Full reconciliation can mark tickets out
of the configured scope after all pages succeed; absence does not prove remote
deletion. Changes to JQL reset the incremental cursor and require full sync.
Current reads require the global admin group and the configured application scope.
Future non-admin access needs an explicit project-membership policy. Per-user Jira
permission replication is a separate capability.

The schema also permits Cloud settings without requiring Cloud support in the
first adapter. Cloud uses distinct token authentication and
[enhanced JQL search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/);
do not reuse the Data Center pagination contract there.

## Server access settings

Store name, hostname/IP, port, username, SSH credential or explicitly configured
agent, trusted host keys, enabled state, and `allow_sudo` (default off). A draft
can be saved before its key is supplied; connection testing requires complete
configuration. Private keys are encrypted credentials, never ordinary form
response fields. Return `credential_configured` and masked metadata in the UI.

This phase models access settings and a future connection test. Arbitrary remote
command execution is not part of a settings save. Later server tools must read
the current enabled/sudo policy at execution time; enabling sudo is a permission
ceiling, not approval for all privileged actions.

## Authentication, tracing, and secrets

Preserve Keycloak + oauth2-proxy and the `docs-admins` global admin group. Trust
forwarded identity only at the private API boundary behind the authenticated
gateway, stripping incoming identity headers. Map stable `(issuer, subject)` to
`principals`; email changes must not create new users. If the v2 proxy identity
is not a stable subject, update the claim wiring during the auth port.

Global admins manage projects and membership. Project admins manage connections,
credentials, servers, and members; editors manage repositories/docs and jobs;
viewers read project content/chat. Enforce permissions server-side, including
SSE, downloads, MCP tool filters, and chat sharing. Composite foreign
keys prevent accidental cross-project references; they do not implement API
authorization. Direct MCP endpoints stay internal.

Application secrets use authenticated encryption with key version and associated
data `(project_id, credential_id, kind)`. Keep the master key outside PostgreSQL
and backups in a separate secret store. No decrypted secret or ciphertext goes
to list/detail responses, job JSON, logs, events, or traces. Runtime deployment
credentials for Keycloak, Phoenix, and model providers stay in deployment secret
files; they are not project connection rows.

Keep Phoenix/OpenInference tracing and extend it to jobs and code generation.
Use project/job/session identifiers as attributes and retain `trace_id` on jobs,
chat turns, and audit events. Phoenix owns span payloads and retention; do not
build a second trace database. A Phoenix outage must not block chat or jobs.
