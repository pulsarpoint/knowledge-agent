# Documentation administration

Docs is a separate admin section with Documents, Prompts, Generate documents and Folders.
It supports Markdown CRUD, version history, resource links, versioned prompts and
document generation by Claude Code agents. RAG publication remains a separate next stage.

## Storage and versions

`ka.documents` provides the stable ID, unique slug, optimistic revision, current
version and future published-version pointer. `ka.document_versions` stores the
canonical Markdown, SHA-256 hash, title, description, purpose, audience, tags,
origin, base version, creator, timestamp and public resource snapshot. The database
checks the hash against the exact UTF-8 Markdown bytes and rejects content updates.
Each save creates a complete immutable version. Loading a historical version into
the editor changes only the unsaved draft; saving it creates a new version based
on the current document revision. Stale saves return 409 without losing history.

`document_code_projects`, `document_services`, `document_servers` and
`document_deployments` and `document_environments` are explicit many-to-many foreign-key relationships to the
existing inventory. They represent current associations and support filtering
before list pagination. The selected deployment must belong to a selected service
and server. Historical versions retain inventory names and IDs in `source_snapshot`
even when inventory is subsequently renamed or removed. These are save-time
inventory descriptions, not evidence that a live server was inspected.

Removing an inventory record clears its current document links, not document
history. Editing a service keeps retained deployment IDs and their document links.
Deleting an unpublished document requires confirmation and its current revision;
versions and links cascade, shared inventory remains, and an audit event is kept.
The API refuses deletion of a document with a published version until an unpublish
workflow can clean its derived RAG data.

## Documentation prompts

`documentation_prompts` holds a name, description, input kind (`code`, `server`,
`service`, `mixed`), enabled state, revision, active version and version counter.
`documentation_prompt_versions` holds immutable text and its first activation time.
Changing prompt text creates a new draft; metadata-only edits preserve identical
latest text. Activation is explicit and atomic under the parent row lock. At most
one version is active. Inactive versions can be deleted with confirmation, without
reusing version numbers. Whole-prompt deletion removes its versions. Enabled state
and active version are separate: a disabled prompt may retain its active version.

These prompts are analysis/writing templates. Agent system prompts remain in the
existing agent prompt system. Generate documents does not use them: its agent runs
with its own prompt and instructions.

Migration [41](../db/migrations/0041_app_docs_prompt.up.sql) seeds
**Application documentation (Diátaxis)** as an enabled code-analysis template with
version 1 active. It combines the v2 `plugins/app-docs` skill, authoring guide,
interview workflow and chunk contract into one self-contained prompt. It covers
source citations, tutorials, how-to guides, reference, architecture explanations,
focused requests, incremental updates and retrieval-friendly Markdown. It supports
both Git and non-Git code folders. The v2 branch/commit/index workflow is replaced
by proposed managed document versions for review; the template executes nothing.

The fixture backfills the existing application workspace. An insert trigger seeds
it when a fresh installation first creates that workspace. There is no runtime
fallback that resets edits or recreates a deleted prompt on page load. A template
with the same name is preserved. Edit and activate versions through **Docs →
Prompts**, using the same draft lifecycle as any other template. Rollback removes
only an untouched fixture and refuses to discard a customized one.

## API and pages

All APIs are under `/api/documentation` and require the existing administrator
role. Mutations also require an attributed user. No credential fields are returned.

| API | Behavior |
| --- | --- |
| `GET /resources` | Public code-project, service, server and deployment choices |
| `GET /documents` | List current documents; `q`, `code_project_id`, `service_id`, `server_id`, `deployment_id`; AND across filters; 50 per page with `offset` |
| `POST /documents` | Create document and first version atomically |
| `GET /documents/{id}` | Metadata, current Markdown version, version summaries |
| `PUT /documents/{id}` | Save a new complete version with expected `revision` |
| `GET /documents/{id}/versions/{version_id}` | Read historical Markdown and resource snapshot |
| `DELETE /documents/{id}` | Expected `revision` and `confirmed=true` required |
| `GET, POST /prompts` | List / create analysis prompts; first text is a draft |
| `GET, PUT /prompts/{id}` | Read / save metadata and changed text as a new draft |
| `POST /prompts/{id}/versions/{version_id}/activate` | Activate using expected parent `revision` |
| `DELETE /prompts/{id}/versions/{version_id}` | Remove inactive version; expected `revision`, confirmation |
| `DELETE /prompts/{id}` | Remove prompt and versions; expected `revision`, confirmation |

The UI is at `/documents`, `/documents/new`, `/documents/{id}`,
`/documentation/prompts`, `/documentation/prompts/new`,
`/documentation/prompts/{id}`, `/documentation/generations` and `/documentation/folders`.
The shared Markdown renderer supplies preview; shadcn dialogs handle destructive
confirmation and navigation with unsaved edits.

## Generate documents

**Docs → Generate documents** starts a session with an ad-hoc native agent: no `agents` row, no saved agent. The form picks the
model and system prompt, shows the context generated from what to document (editable), and takes a saved or own prompt.

`POST /api/claude-code/context` takes the five id lists and returns `{text}`: the context the form shows and the agent reads.

`POST /api/claude-code/sessions` takes `model_id`, `code_project_ids`, `collection_ids`, `environment_ids`, `service_ids`,
`server_ids`, `system_prompt` (up to 20,000 characters), `context` (up to 100,000), `prompt` (up to 100,000), `prompt_id`
(the saved prompt it came from, or null), `document_name` and `folder`. At least one code project, collection, environment,
service or server is required. Refusals (400): `NOTHING_CHOSEN`, `NO_MODEL` ("Choose an enabled model.": unknown, disabled or
not a Responses or Claude connection), unknown or unavailable ids ("Select available ..."), and the per-run resource refusals
`<label> <name> is not available: it was removed, archived or disabled.` Servers brought by environments or services that have no
usable connection are left out of the agent's tools.

The session stores two snapshots and `runtime = 'openai'`. `prompt_snapshot` holds `system_prompt`, `context` and `prompt_id`;
the pinned agent's system prompt is the system prompt followed by the context. `agent_config` is the ad-hoc configuration
narrowed to the form's sources (`run_resources.resolve`; credential references only), and `history` holds the Agents SDK items
of successful turns, redacted; listing and detail return `runtime`, never `agent_config` or `history`. Migration 0082 lets these
sessions have `agent_id` NULL, and their run records carry the agent `ad-hoc`.
`context_snapshot` holds the chosen `code_projects`, `collections`, `environments`, `services`, `servers`, plus
`folders` (the working folders, read-only, the first being the working directory) and `code_project_ids`. Credentials are
never included. Sessions also store `run_resources` (ids only). Generate documents sessions have `purpose = 'documents'`; the agent Test tab's sessions share the table with `purpose = 'test'` (no document name, folder or draft) and are not listed in Generate documents.

The first message is the prompt followed by the final-answer instruction, queued as the session's first input:

```
<prompt>

When you finish, reply with the complete document in Markdown as your final answer: it becomes the draft. After each
later message, reply again with the complete updated document.
```

**Native turns.** `agent-session-worker` claims native sessions (`session_worker.claim(…, 'openai')`), decrypts the pinned
config's credentials per turn, runs `agents.agent(config, text, session=HistorySession, hooks=SessionEvents, stream=False,
max_turns=None, turn_timeout_seconds=None, call_model_input_filter=Comments)` and writes the same events as Claude Code
turns under `cc:<session>:<turn>` (`start.kind = native_session`). There is no turn time or model-call limit; each model request and MCP connection keeps its
300 s timeout. Comments queued during a turn are delivered inside it before the next model call (an `input` event). Stop
ends the turn as stopped within about 30 seconds after it is pressed; after that the turn starts no new model request or tool
call (a request or command already running cannot be recalled), and comments delivered to a stopped turn are discarded;
a message sent after Stop is not delivered to the stopped turn and starts the next one.
After a failure or a lost worker they go to the next turn. The turn's history is committed in the transaction that records
the turn; failed and stopped turns drop it. `SessionSettings(limit)` is not used (every turn sees the whole conversation).

`GET /api/claude-code/sessions/{id}/events/history` returns `{events, has_more}`: the latest page of events, oldest first,
or the page before an event id with `before` (`limit` 1 to 500, default 100). Events are redacted like the live stream; a
tool result keeps its preview and the full output stays in the run record.

`GET /api/claude-code/sessions/{id}/events` is the live stream (Server-Sent Events): `run_event` events with their id
after `Last-Event-ID` (or `?after=`), `session` with the state when it changes, `closed` at the end, `unavailable` once
while the database cannot be reached (the stream stays open and resumes; the browser shows "The session stream is
unavailable. Reconnecting…" and clears it with the next event), `: keepalive` after 15 s of quiet, and `retry: 1000`
ending a stream that fell more than 500 messages behind (the browser reconnects from its last id, a `resync`). The
stream does not poll and holds no database connection: migration 0083's triggers on `ka.agent_run_events`,
`ka.agent_sessions_cc` and `ka.agent_session_inputs` announce every committed change to a session's events, row or
queued messages on the PostgreSQL channel `ka_sessions`. One `SessionHub` per admin-api process
(`knowledge_admin/live/session_hub.py`, started in the app lifespan) keeps the only listener (one connection), reads
each changed session once and hands the new events and state to every open stream of that session (per-session
fan-out). It reconnects with backoff (1 s doubling to 30 s), re-reads every watched session after a reconnect, and
checks every watched session every 30 s anyway, so a lost notice delays an update by at most 30 s. When the database is
down the hub sends `unavailable` once to every stream, retries, and reads everything again on recovery. On shutdown the
hub ends its streams with `resync` and the server waits at most 5 s (`timeout_graceful_shutdown`) for open streams
before cutting them; browsers reconnect.

Migration 0073 dropped `documentation_generations`, `documentation_workflows` and `documentation_workflow_jobs`, the
`documentation_generate` job kind and the `doc:*` run records, and added `documents.phase_metadata` (the service phase
export layout, read by `export_input`). Saved documents, versions and exports stay.

The canonical content stays in PostgreSQL. Every save also exports the current version to `DOCUMENT_OUTPUT_ROOT`, at `/work/data/<GENERATED_DOCS_FOLDER>` (default `generated-documents`) inside `DATA_ROOT`, replacing the previous export in place: `<document-slug>/README.md` (or `<folder>/<document-slug>/README.md` for a document in a lowercase documentation folder), `architecture-N.d2` and `metadata.json` (source commits), or `services/<service>-<id8>/<phase>/<phase>.md` for a document written by a code documentation phase of the removed one-shot generation (its layout is kept in `documents.phase_metadata`). Renaming a slug removes the old folder's files; deleting a document removes its export. On startup admin-api re-exports any document whose current export is missing (for example after upgrading from the old `<slug>/v<version>/` layout, whose folders are left on disk but not indexed). File export failure does not discard database versions and is shown in the save or job result. `POST /api/documentation/documents/{id}/export` retries export of the current saved version. `GET /api/documentation/documents/{id}/bundle` downloads the same document and diagram sources as a ZIP folder. The collection that holds `generated-documents` (`documents` on upgraded installs; create it on a fresh install) indexes this folder, so documents become searchable after **Refresh** in RAG → Collections; there is no separate publish step.
