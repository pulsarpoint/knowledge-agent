# Documentation administration

Docs is a separate admin section with Documents, Prompts and Folders.
It supports Markdown CRUD, version history, resource links, versioned prompts and
documents written by agent sessions. RAG publication remains a separate next stage.

## Storage and versions

Since migration 0104 a document lives in three tables:

- `ka.documents` holds live documents **and** drafts. A live row (`is_draft = false`) has the stable ID, slug, folder,
  optimistic `revision`, its version number `version` and its current content: Markdown, SHA-256 `content_hash`
  (checked against the exact UTF-8 bytes), title, description, purpose, audience, tags, `origin` (manual, generated,
  uploaded), `from_draft`, `session_id` (the run that wrote it), the public resource `source_snapshot`, and who made the
  version live and when. A draft row (`is_draft = true`) is either a **draft of a live document** (`draft_of` = the
  live id, `base_version` = the live version it started from, no slug or folder of its own) or a **brand-new
  document** that has never been live (its own slug and folder, no `draft_of`). Slugs are unique per folder among live
  documents and brand-new drafts; brand-new drafts reserve their name.
- `ka.document_archive` holds the earlier live contents of a document (`document_id`, `version`, the same content
  columns, `created_at` = when that version went live, `archived_at`/`archived_by`). Version history is the archive
  rows plus the live row, by version number.
- `ka.document_resources` holds every link between a document and an environment, service, server, code project or
  deployment, one row per link with a real foreign key per resource type, `connection` (`built_for` or `linked`),
  `added_how` (`person`, `agent_run`, `discovery`, `migrated`), `added_by`, `session_id`, `created_at` and, once
  removed, `removed_at`/`removed_by`. A link is active while `removed_at` is NULL; a removed link stays as history.

Readers use views and never see a draft by accident: `ka.live_documents` (live rows), `ka.document_details` (live rows
with their active link arrays and `draft_count`) and `ka.document_drafts_view` (every draft with its document's
title, slug and folder, and the link arrays of its live document or, for a brand-new draft, its own). Triggers refuse
a run's document that points at a draft, and a `built_for` link on a draft of a live document (migration 0130).

**Links.** A brand-new draft (a person's Save, a new-document run's first save, a discovery run's `new_document`) gets its links as `built_for`, with `added_how` person, agent_run or discovery. A built-for link cannot
be removed: unticking it is a 409 "This document was built for
{type} {name}; that link can't be removed." and nothing changes. A person's links never go through drafts: **Save links**
(`PUT /documents/{id}/links`, on a live document or a brand-new draft) applies them directly. It compares the ticked
ids with the active rows: unchanged rows stay as they are, newly ticked ones get a `linked` row, unticked `linked` rows get
`removed_at` and `removed_by`; `revision` goes up, `version` does not. Drafts of a live document use the live
document's links (Save links on a draft of a document is a 404). The one exception is an agent's
`update_document(…, resource)` on a live document: the run's draft holds that `linked` link (migration 0130) and
**Promote adds it** to the document (a link the document already has stays as it is); until then the link does not
show anywhere. Promote changes no other link. The audit row
(`document.links.updated`) lists `links_added` and `links_removed`. The selected deployment must belong to a selected service and server.

**Lifecycle.** Every save a person makes is a draft: **Save** on a new document creates a brand-new draft, Save on a
live document creates the person's draft of it (later Saves update that draft), and no route writes live content.
Content goes live only by **Publish** (a brand-new draft) or **Promote** (a draft of a live document), both
`POST /drafts/{id}/promote`. A brand-new draft keeps the ID it will have as a document, opens at `/documents/{id}`
(`GET /documents/{id}` answers with `is_draft: true`, `published: false`) and can be renamed (`slug`, `folder` on
`PUT /drafts/{id}`) until it is published; a draft of a live document keeps its document's name. There is no history of
a draft's own saves: version history starts when it is published. **Discovery** follows the same rule: a run's `new_document`
creates a brand-new draft (the run's `session_id`, built for the resource it is for) and `update_document` creates a
draft of an existing document (`base_version` = its live version). Its built-for link is what shows it on its resource
once published. There are no proposals (migration 0131). A document can have any
number of drafts; they are listed by `created_at`, newest first. **Promoting** draft D of live document L (one
transaction locking L) archives L's content, updates L in place with D's content (`version + 1`, `from_draft`, the
same ID) and **deletes every draft of L**, D included; when D's `base_version` is older than L's `version`, promote
answers 409 `STALE_BASE` until confirmed. Promoting a brand-new draft makes that row live as version 1 with its ID,
slug, folder, sources and built-for links. **Discarding** a draft deletes it (a brand-new draft with its links: the
document never existed). Deleting a live document deletes its drafts, archive rows and links. Stale saves (wrong
`revision`) return 409 without losing anything. Promote audits `document.draft.promoted` for D,
`document.draft.replaced` for each other draft it deleted, and `document.updated` (`document.created` for a brand-new
draft) for the document.

**Tags and inventory documents.** Documents are classified by tags (`tags`, content like the title: edited in a draft
and made live by Promote / Publish). `GET /documents?tag=<tag>` and `GET /drafts?tag=<tag>` keep the documents or
drafts with that one tag (exact, case-insensitive; a draft of a document carries its own copy of the tags). The
inventory document of a server, a service or an environment is the live document tagged `inventory` built for it, the
most recently updated one when several (the others are in its Documents list); when there is no live one, a brand-new
draft tagged `inventory` built for it. Nothing enforces one per resource (migration 0109 replaced the 0107 flag and its
checks with the tag). A person creates one with `tags: ["inventory"]` and the resource in `resource_ids` on
`POST /drafts`. The documents of an inventory entry come from links: `GET /api/inventory/{kind}/{id}/documents`
(kind `server`, `service`, `environment` or `code_project`) answers `{inventory_document: {id, title, markdown,
version, is_draft, published} | null, documents: [{id, title, tags, description_short, connection, resource}]}` (the
inventory document may be a brand-new draft: `version` null, `published` false; the documents are live ones, the live
inventory document first); `POST` there with `{document_id}` adds a `linked` link (Link a document) and
`DELETE .../documents/{document_id}` removes it (a built-for link stays: 409). The System map lists the live documents
linked to each entry. Deleting a resource removes its links; the documents and their tags stay.

Historical versions retain inventory names and IDs in `source_snapshot` even when inventory is subsequently renamed or
removed. These are save-time inventory descriptions, not evidence that a live server was inspected. Removing an
inventory record removes its link rows, not document history. Deleting a document requires confirmation and its
current revision; an audit event is kept.

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
existing agent prompt system. The agent chat on Documents → New uses the prompts tagged
`documentation` (see below).

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
| `GET /documents` | List live documents; `q`, `code_project_id`, `service_id`, `server_id`, `deployment_id`, `environment_id`; AND across filters; 50 per page with `offset`; `has_drafts=true` keeps documents with drafts; each row has `draft_count` |
| `GET /documents/{id}` | A live document (`is_draft: false`, `published: true`): metadata, current Markdown and `source_snapshot`, `versions[]` (`version, title, origin, created_at, from_draft, live`), `drafts` (newest first) and `resource_links` (active links: type, id, name, connection, added_how, added_by, session_id, created_at). A brand-new draft (`is_draft: true`, `published: false`): its name, text, `revision`, `origin` (`agent`/`manual`), `session_id`, link arrays and `resource_links`, `versions: []`, `drafts: []`. A draft of a live document is 404 (open it with `GET /drafts/{id}`) |
| `PUT /documents/{id}/links` | `{revision, code_project_ids, service_ids, server_ids, deployment_ids, environment_ids}` (a missing kind means none): the links of a live document or a brand-new draft; returns `id`, `revision`, `resource_links`; 409 on a stale `revision` or an unticked built-for link (nothing written); 404 for a draft of a document |
| `GET /documents/{id}/versions/{version}` | One version by number (archive or live): Markdown and resource snapshot |
| `GET /documents/{id}/resource-history` | Every link row, active and removed |
| `DELETE /documents/{id}` | Expected `revision` and `confirmed=true` required |
| `POST /drafts` | A draft of a live document (`document_id`; text defaults to the live version) or a brand-new document (`slug`, optional `folder`, `title`, `markdown`, optional `resource_ids`, which become built-for links); a folder+slug naming a live document makes a draft of it |
| `GET /drafts` | Drafts (each row has `published`: false for a brand-new draft); `new=true` keeps brand-new documents; `environment_id`, `service_id`, `server_id`, `code_project_id`, `deployment_id` keep the drafts linked to that resource (brand-new drafts by their own links, drafts of a document by its links) |
| `GET /drafts/{id}` | A draft with its Markdown, sources, base and live version numbers, `stale` and the live Markdown |
| `PUT /drafts/{id}` | Save a draft's text with its `revision` (marks a run's draft `edited`); a brand-new draft may also send `slug` and/or `folder` (null: no folder) to rename it (409 when the name or its export folder is taken); a draft of a document that sends either is a 400 "A draft of a published document keeps its document's name." |
| `POST /drafts/{id}/promote` | `{revision, confirm_stale?}`: Publish (a brand-new draft) or Promote: make it the live version and delete the document's other drafts; returns `document_id`, `version`, `revision`, `deleted_drafts`; 409 `STALE_BASE` when the live version moved on |
| `POST /drafts/{id}/discard` | `{revision}`: delete the draft |
| `GET /documents/{id}/drafts` | Drafts of a document, newest first (no Markdown) |
| `GET /documents/{id}/connections` | `depends_on`, `used_by` and `built_from` (the live version's sources) |
| `POST /documents/{id}/links`, `DELETE /documents/{id}/links/{to_id}` | Add or remove a `depends_on` link (live documents only) |
| `GET, POST /prompts` | List / create analysis prompts; first text is a draft |
| `GET, PUT /prompts/{id}` | Read / save metadata and changed text as a new draft |
| `POST /prompts/{id}/versions/{version_id}/activate` | Activate using expected parent `revision` |
| `DELETE /prompts/{id}/versions/{version_id}` | Remove inactive version; expected `revision`, confirmation |
| `DELETE /prompts/{id}` | Remove prompt and versions; expected `revision`, confirmation |

The UI is at `/documents`, `/documents/new`, `/documents/{id}`,
`/prompts`, `/prompts/new`, `/prompts/{id}` (Administration → Prompts),
`/sessions/:id` (the run page for every run) and `/documentation/folders`. `input_kind` is retired: the column remains only for the
per-workspace seed functions, and the API neither accepts nor returns it.
The shared Markdown renderer supplies preview; shadcn dialogs handle destructive
confirmation and navigation with unsaved edits.

Ask agent runs (agent sessions with purpose `revision`, started through `POST /api/sessions` from the chat pane on the
document page, with the document in the run's access, `resources.documents[{id}]`; the first message is the
instruction, and `/documents/{id}/agent/{run}` opens the page with the pane on that run) change a document only
through the document tools below, which write the run's draft. The editing rules are the **Revise a document** prompt
(tag `document-revision`, seeded by migration 0100; 0131 replaced `save_document` with `update_document` in it).

## Document tools

Every agent run has the document tools; what it may do with them is decided by its access object, not by a role (there
is no Content editor role and no `editable` flag on a document entry; stored access objects that still carry them load,
the keys ignored).
A document is **usable** when it is in the run's access or linked to a server, service, environment or code project in
it; a run may read and draft every usable document, and create new documents for any server, service or environment in its access.
Subagents and experts get the read tools only (`search_documents`, `list_documents`, `get_document`).

| Tool | What it does |
|---|---|
| `search_documents(query, limit?)` | Full-text search over the **published** usable documents (drafts are never searched): `websearch_to_tsquery('english', query)` (stemmed words, `"a phrase"`, `or`, `-word`), ranked with `ts_rank_cd`, a title match above a description match above a text match; `limit` 1–25 (10). Each result: `document_id`, title, `description_short`, tags, `resources` (its links to the run's own servers, services, environments and code projects: type, id, name) and `snippet` (`ts_headline`, about 30 words, matches in `**bold**`). Nothing found says so and points to `list_documents`. The index is the partial GIN expression index `documents_search` on live rows (migration 0133); the query repeats its expression (`document_tools.SEARCH_VECTOR`) and its scope is the run's access as SQL parameters (`search_scope`, the `get_document` rule). |
| `list_documents(resource)` | The documents linked to a server, service or environment of the run (`resource`: `{type, id}` or `{type, name}`) and to what belongs to it: id, title, short description, tags, `published` (false for a new document the run drafted) and `your_draft`; the inventory document first. |
| `get_document(document_id, section?, start_line?, end_line?)` | The run's draft of the document when it has one, else the live version: outline and text, or the asked lines numbered. |
| `new_document(resource, title, content, description?, tags?)` | Creates a new document as a brand-new draft built for the resource (`built_for`, `added_how = 'agent_run'`, the run's `session_id`); folder and slug come from the resource and the title. A title already used by a document linked to the same resource is refused with that document's id. Returns the id the document keeps once published. |
| `update_document(document_id, content, title?, description?, resource?)` | Saves the run's draft of the document with its complete content. With `resource`, the document is linked to that server, service or environment: a new document's draft gets the link at once, a draft of a live document holds it and Promote adds it (see **Links**). |
| `edit_document(document_id, old_text, new_text)` | Replaces one exact passage (it must occur once) in the run's draft, started from the live text when there is none yet; an empty `new_text` removes it. |

A run keeps one draft per document; every `update_document` and `edit_document` changes it. Every reply that writes
says the document is a draft waiting for a person to publish it on the document's page. Nothing goes live until a
person publishes or promotes the draft. The environment page's header counts the document drafts of the environment,
its servers and its services (also by a draft's own pending link), and its Documents tab lists them under **Drafts to
review** (`GET /api/environments/{id}/document-drafts`).

## Write a new document with an agent

**Documents → New → Write with an agent** opens the same chat pane as Ask agent, on a document that does not exist yet.
The page asks for a **Title** and a **Folder** (the Folder field appears with the pane). The run is started by
`POST /api/sessions` with `started_from.new_document {folder, name}` and the new document in the run's access,
`resources.documents[{new: {folder, name}}]` (an `editable` key is refused: 422). Such runs have `purpose = 'documents'`. A run starts from a document or
from a new document, not both (`GET /api/sessions/options` takes `new_document=1` without `agent_id` or `document_id`).

`GET /api/sessions/options?new_document=1` says what the pane may offer: the models, the resources (code projects, document
collections, environments, services, servers), the experts, and the prompts tagged
`documentation`. The default prompt is **Write a document** (tags `documentation` and `new-document`, seeded by migration
0101; 0131 gave it a new version): read what you were given, write the document with `update_document`, change a
passage with `edit_document`, and never say it is published. Custom prompts tagged `documentation` should tell the agent
to write with `update_document`. No
resource is chosen until it is ticked; experts are offered only when ticked (`expert_ids`). The run gets every tool,
including `run_subagent`; the run's access (what was chosen, and the new document) decides what each may do.

The agent writes the document with `update_document(document_id, content, title?, description?, resource?)` and
`edit_document(document_id, old_text, new_text)`, using the new document's id from its context. Both write the run's draft. The agent's final answer is not turned into a
draft. If a document with the title's slug already exists in the folder, the saves become a draft of that document.
Otherwise the first save creates a brand-new draft whose links are built-for (`added_how = 'agent_run'`, the run's
`session_id`): the environments, services, servers and code projects chosen (for a run started from a server page, that
server). What they bring along for the agent (an environment's services and servers, a service's code projects and the
servers it is deployed on) is not linked; the agent may use it, but the document is not about it. A save that drafts an
existing document never changes that document's links. Later saves update the same draft until a person saves (edits), promotes or discards it,
or another draft of the document is promoted (which deletes this one); the next save then starts a new draft of the
live version, and the tool result says what happened to the earlier draft. A person's edit of a run's brand-new draft
makes it theirs: the run's next save is refused until they promote or discard it. The draft's sources are the documents the run read with `get_document`,
at the version live at the first read. Nothing goes live until a person promotes the draft.

There is one conversation per page visit. Earlier runs stay readable at `/sessions/{id}` and in Activity → Agent runs, including the runs of the
removed Docs → Generate documents.

**Removed.** The Docs → Generate documents form and list pages (`/documentation/generations`), `POST /api/generations/sessions`,
`POST /api/generations/context` and the rule that a run's final answer becomes the draft are gone (the routes now return 404).
Migration 0101 closed the open Generate documents runs with "Closed by an update; start a new chat." Their conversations
stay readable.

The session stores two snapshots. `prompt_snapshot` holds the system prompt, the context and `prompt_id` (the saved prompt it
came from, or null); `agent_config` is the ad-hoc configuration (credential references only) with its access object (also
`access`): what the run was picked, each checked and loaded, and what that brings, built as a saved agent's run is
(`sessions/run_access.run_access`). `history` holds the Agents SDK items of successful turns, redacted; listing and detail
never return `agent_config` or `history`. These sessions have `agent_id` NULL (migration 0082), and their run records carry
the agent `ad-hoc`. `context_snapshot` holds where the run started (`new_document`, `document_id`, or the resource's id;
`{}` for a saved agent's run). The session detail returns `given_resources`, the access entries the run was given
(`[{type, id}]`: what the person chose, an environment rather than what it brings); Start new chat with these settings
restores them. A new document is built for the same choice; one that names a single server, service or environment (an inventory
document always does) is built for that resource alone (its entry's `built_for`). Credentials are never included. Migration 0116 dropped the ids-only `run_resources` column.
The agent Test tab's sessions share the table with `purpose = 'test'` (no document name, folder or draft).

**Turns.** `agent-session-worker` claims sessions (`session_worker.claim`), decrypts the pinned
config's credentials per turn, runs `agents.agent(config, text, session=HistorySession, hooks=SessionEvents, stream=False,
max_turns=None, turn_timeout_seconds=None, call_model_input_filter=Comments)` and writes events under `cc:<session>:<turn>` (the `cc:` prefix is historic) (`start.kind = native_session`). There is no turn time or model-call limit; each model request and MCP connection keeps its
300 s timeout. Comments queued during a turn are delivered inside it before the next model call (an `input` event). Stop
ends the turn as stopped within about 30 seconds after it is pressed; after that the turn starts no new model request or tool
call (a request or command already running cannot be recalled), and comments delivered to a stopped turn are discarded;
a message sent after Stop is not delivered to the stopped turn and starts the next one.
After a failure or a lost worker they go to the next turn. The turn's history is committed in the transaction that records
the turn; failed and stopped turns drop it. `SessionSettings(limit)` is not used (every turn sees the whole conversation).
The run's document draft is written by the document tools during the turn, not by the worker when the turn ends.

`GET /api/sessions` and `GET /api/sessions/{id}` rows, and the live stream's `session` state,
carry `document_draft`: `{id, document_id, status}` of the run's last draft (`agent_sessions.last_draft_id`), or null
when the run never had one. The status is `open` while the draft row exists; after it is gone it comes from the audit
log: `promoted` (this draft went live), `discarded`, or `replaced` (another draft of the document was promoted and
deleted it). The run page shows **Open draft** for an open draft and **Open document** for a promoted one.

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
`SessionHub` per admin-api process (`knowledge_admin/sessions/hub.py`, started in the app lifespan) keeps the only
listener (one connection), reads each changed session once and hands the new events and state to every open stream of
that session (per-session fan-out). It reconnects with backoff (1 s doubling to 30 s), re-reads every watched session
after a reconnect, and checks every watched session every 30 s anyway, so a lost notice delays an update by at most 30
s. When the database is down the hub sends `unavailable` once to every stream, retries, and reads everything again on
recovery. On shutdown the server waits at most 5 s (`timeout_graceful_shutdown`) for open streams, then closes them;
browsers reconnect.

Migration 0073 dropped `documentation_generations`, `documentation_workflows` and `documentation_workflow_jobs`, the
`documentation_generate` job kind and the `doc:*` run records, and added `documents.phase_metadata` (the service phase
export layout). Saved documents and versions stay.

The content lives in PostgreSQL only: no file is written when a version goes live or a document is deleted (the file export, its startup backfill and `POST /documents/{id}/export` were removed with RAG). `GET /api/documentation/documents/{id}/bundle` downloads a live document and its diagram sources as a ZIP folder; it is 404 for a brand-new draft.
