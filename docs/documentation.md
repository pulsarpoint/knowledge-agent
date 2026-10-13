# Documentation administration

Docs is a separate admin section with Documents, Prompts, Generate documents and Folders.
It supports Markdown CRUD, version history, resource links, versioned prompts and
document generation by agent sessions. RAG publication remains a separate next stage.

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

`documentation_prompts` holds a name, description, `tags` (lowercase letters, digits and `-`;
at most 10; the `documentation` tag plus the old kind on existing and seeded prompts),
enabled state, revision, active version and version counter.
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
| `GET /documents` | List current documents; `q`, `code_project_id`, `service_id`, `server_id`, `deployment_id`; AND across filters; 50 per page with `offset`; `has_drafts=true` keeps documents with open drafts; each row has `draft_count` |
| `POST /documents` | Create document and first version atomically |
| `GET /documents/{id}` | Metadata, current Markdown version, version summaries; versions say `from_draft` |
| `PUT /documents/{id}` | Save a new complete version with expected `revision` |
| `GET /documents/{id}/versions/{version_id}` | Read historical Markdown and resource snapshot |
| `DELETE /documents/{id}` | Expected `revision` and `confirmed=true` required |
| `POST /drafts` | A draft of a document (`document_id`; text defaults to the live version) or of a new document (`slug`, optional `folder`, `title`, `markdown`, optional `resource_ids`) |
| `GET /drafts?new=true` | Open drafts of new documents (without `new`: all open drafts) |
| `GET /drafts/{id}` | A draft with its Markdown, sources, base and live version numbers, `stale` and the live Markdown |
| `PUT /drafts/{id}` | Save an open draft's text with its `revision` (marks it `edited`) |
| `POST /drafts/{id}/promote` | `{revision, confirm_stale?}`: save as the live version; 409 `STALE_BASE` when the live version moved on |
| `POST /drafts/{id}/discard` | `{revision}`: discard (kept for audit, never listed) |
| `GET /documents/{id}/drafts` | Open drafts of a document (no Markdown) |
| `GET /documents/{id}/connections` | `depends_on`, `used_by` and `built_from` (the live version's sources) |
| `POST /documents/{id}/links`, `DELETE /documents/{id}/links/{to_id}` | Add or remove a `depends_on` link |
| `GET, POST /prompts` | List / create analysis prompts; first text is a draft |
| `GET, PUT /prompts/{id}` | Read / save metadata and changed text as a new draft |
| `POST /prompts/{id}/versions/{version_id}/activate` | Activate using expected parent `revision` |
| `DELETE /prompts/{id}/versions/{version_id}` | Remove inactive version; expected `revision`, confirmation |
| `DELETE /prompts/{id}` | Remove prompt and versions; expected `revision`, confirmation |

The UI is at `/documents`, `/documents/new`, `/documents/{id}`,
`/prompts`, `/prompts/new`, `/prompts/{id}` (Administration → Prompts), `/documentation/generations`,
`/sessions/:id` (the run page for every run) and `/documentation/folders`. `input_kind` is retired: the column remains only for the
per-workspace seed functions, and the API neither accepts nor returns it.
The shared Markdown renderer supplies preview; shadcn dialogs handle destructive
confirmation and navigation with unsaved edits.

Ask agent runs (agent sessions with purpose `revision`, started through `POST /api/sessions` from the chat pane on the
document page, with the document as an editable resource; the first message is the instruction, and
`/documents/{id}/agent/{run}` opens the page with the pane on that run) change a document only through the
`get_document`, `save_document` and `edit_document` tools (each takes `document_id`), which write the run's draft. The
editing rules are the **Revise a document** prompt (tag `document-revision`, seeded by migration 0100);
Generate documents runs still turn their final answer into a draft.

## Generate documents

**Docs → Generate documents** starts a session with an ad-hoc native agent: no `agents` row, no saved agent. The form picks the
model and system prompt, shows the context generated from what to document (editable), and takes a saved or own prompt.

`POST /api/generations/context` takes the five id lists and returns `{text}`: the context the form shows and the agent reads.

`POST /api/generations/sessions` takes `model_id`, `code_project_ids`, `collection_ids`, `environment_ids`, `service_ids`,
`server_ids`, `system_prompt` (up to 20,000 characters), `context` (up to 100,000), `prompt` (up to 100,000), `prompt_id`
(the saved prompt it came from, or null), `document_name` and `folder`. At least one code project, collection, environment,
service or server is required. Refusals (400): `NOTHING_CHOSEN`, `NO_MODEL` ("Choose an enabled model.": unknown, disabled or
not a Responses or Claude connection), unknown or unavailable ids ("Select available ..."), and the per-run resource refusals
`<label> <name> is not available: it was removed, archived or disabled.` Servers brought by environments or services that have no
usable connection are left out of the agent's tools.

The session stores two snapshots. `prompt_snapshot` holds `system_prompt`, `context` and `prompt_id`;
the pinned agent's system prompt is the system prompt followed by the context. `agent_config` is the ad-hoc configuration
narrowed to the form's sources (`run_resources.resolve`; credential references only), and `history` holds the Agents SDK items
of successful turns, redacted; listing and detail never return `agent_config` or `history`. Migration 0082 lets these
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

**Turns.** `agent-session-worker` claims sessions (`session_worker.claim`), decrypts the pinned
config's credentials per turn, runs `agents.agent(config, text, session=HistorySession, hooks=SessionEvents, stream=False,
max_turns=None, turn_timeout_seconds=None, call_model_input_filter=Comments)` and writes events under `cc:<session>:<turn>` (the `cc:` prefix is historic) (`start.kind = native_session`). There is no turn time or model-call limit; each model request and MCP connection keeps its
300 s timeout. Comments queued during a turn are delivered inside it before the next model call (an `input` event). Stop
ends the turn as stopped within about 30 seconds after it is pressed; after that the turn starts no new model request or tool
call (a request or command already running cannot be recalled), and comments delivered to a stopped turn are discarded;
a message sent after Stop is not delivered to the stopped turn and starts the next one.
After a failure or a lost worker they go to the next turn. The turn's history is committed in the transaction that records
the turn; failed and stopped turns drop it. `SessionSettings(limit)` is not used (every turn sees the whole conversation).
Each finished turn also writes the run's document draft in the same transaction (`save_run_draft_safely`, in a
savepoint: a draft that cannot be saved is logged and never fails the turn). The first finished turn creates the draft — a
draft of the document the run's folder and name already name, or of a new document there that links the run's code
projects, environments, services and servers on promote — and later turns update it until a person saves (edits),
promotes or discards it; the next finished turn then starts a new draft. The draft's sources are recomputed every turn
from the documents the run read (`get_document`, or `read_document` of an export path), at the version live at the first
read. Nothing goes live until a person promotes the draft.

`GET /api/sessions` and `GET /api/sessions/{id}` rows, and the live stream's `session` state,
carry `document_draft`: `{id, document_id, status}` of the run's latest document draft of any status (`open`, `promoted`
or `discarded`), or null when the run never had one. The run page shows **Open draft** for an open draft and **Open document** for a promoted one.

`GET /api/sessions/{id}/events/history` returns `{events, has_more}`: the latest page of events, oldest first,
or the page before an event id with `before` (`limit` 1 to 500, default 100). Events are redacted like the live stream; a
tool result keeps its preview and the full output stays in the run record.

`GET /api/sessions/{id}/events` is the live stream (Server-Sent Events): `run_event` events with their id
after `Last-Event-ID` (or `?after=`), `session` with the state when it changes, `closed` at the end, `unavailable` once
while the database cannot be reached (the stream stays open and resumes; the browser shows "The session stream is
unavailable. Reconnecting…" and clears it with the next event or state, which the hub sends again on recovery),
`: keepalive` after 15 s of quiet, and `retry: 1000` ending a stream that fell more than 500 messages behind (the
browser reconnects from its last id, a `resync`). The stream does not poll and holds no database connection: migration
0083's triggers on `ka.agent_run_events`, `ka.agent_sessions` and `ka.agent_session_inputs` announce every committed
change to a session's events, row or queued messages on the PostgreSQL channel `ka_sessions` (an update that only moves
a worker's lease or the last activity is not announced; a lease that runs out shows within the 30 s check). One
`SessionHub` per admin-api process (`knowledge_admin/live/session_hub.py`, started in the app lifespan) keeps the only
listener (one connection), reads each changed session once and hands the new events and state to every open stream of
that session (per-session fan-out). It reconnects with backoff (1 s doubling to 30 s), re-reads every watched session
after a reconnect, and checks every watched session every 30 s anyway, so a lost notice delays an update by at most 30
s. When the database is down the hub sends `unavailable` once to every stream, retries, and reads everything again on
recovery. On shutdown the server waits at most 5 s (`timeout_graceful_shutdown`) for open streams, then closes them;
browsers reconnect.

Migration 0073 dropped `documentation_generations`, `documentation_workflows` and `documentation_workflow_jobs`, the
`documentation_generate` job kind and the `doc:*` run records, and added `documents.phase_metadata` (the service phase
export layout, read by `export_input`). Saved documents, versions and exports stay.

The canonical content stays in PostgreSQL. Every save also exports the current version to `DOCUMENT_OUTPUT_ROOT`, at `/work/data/<GENERATED_DOCS_FOLDER>` (default `generated-documents`) inside `DATA_ROOT`, replacing the previous export in place: `<document-slug>/README.md` (or `<folder>/<document-slug>/README.md` for a document in a lowercase documentation folder), `architecture-N.d2` and `metadata.json` (source commits), or `services/<service>-<id8>/<phase>/<phase>.md` for a document written by a code documentation phase of the removed one-shot generation (its layout is kept in `documents.phase_metadata`). Renaming a slug removes the old folder's files; deleting a document removes its export. On startup admin-api re-exports any document whose current export is missing (for example after upgrading from the old `<slug>/v<version>/` layout, whose folders are left on disk but not indexed). File export failure does not discard database versions and is shown in the save or job result. `POST /api/documentation/documents/{id}/export` retries export of the current saved version. `GET /api/documentation/documents/{id}/bundle` downloads the same document and diagram sources as a ZIP folder. The collection that holds `generated-documents` (`documents` on upgraded installs; create it on a fresh install) indexes this folder, so documents become searchable after **Refresh** in RAG → Collections; there is no separate publish step.
