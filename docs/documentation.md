# Documentation administration

Docs is a separate admin section with Documents and Prompts. Documents are organized by their resource links, tags
and full-text search; there are no documentation folders (migration 0144).
It supports Markdown CRUD, version history, resource links, versioned prompts and
documents written by agent sessions.

## Storage and versions

Since migration 0104 a document lives in three tables:

- `ka.documents` holds live documents **and** drafts. A live row (`is_draft = false`) has the stable ID, slug,
  optimistic `revision`, its version number `version` and its current content: Markdown, SHA-256 `content_hash`
  (checked against the exact UTF-8 bytes), title, description, purpose, audience, tags, `origin` (manual, generated,
  uploaded), `from_draft`, `session_id` (the run that wrote it), the public resource `source_snapshot`, and who made the
  version live and when. A draft row (`is_draft = true`) is either a **draft of a live document** (`draft_of` = the
  live id, `base_version` = the live version it started from, no slug of its own) or a **brand-new
  document** that has never been live (its own slug, no `draft_of`). Slugs are unique in the project among live
  documents and brand-new drafts (`documents_slug`); brand-new drafts reserve their name.
- `ka.document_archive` holds the earlier live contents of a document (`document_id`, `version`, the same content
  columns, `created_at` = when that version went live, `archived_at`/`archived_by`). Version history is the archive
  rows plus the live row, by version number.
- `ka.document_resources` holds every link between a document and an environment, service, server, code project or
  deployment, one row per link with a real foreign key per resource type, `connection` (`built_for` or `linked`),
  `added_how` (`person`, `agent_run`, `discovery`, `migrated`), `added_by`, `session_id`, `created_at` and, once
  removed, `removed_at`/`removed_by`. A link is active while `removed_at` is NULL; a removed link stays as history.

Readers use views and never see a draft by accident: `ka.live_documents` (live rows), `ka.document_details` (live rows
with their active link arrays and `draft_count`) and `ka.document_drafts_view` (every draft with its document's
title and slug, and the link arrays of its live document or, for a brand-new draft, its own). Triggers refuse
a run's document that points at a draft, and a `built_for` link on a draft of a live document (migration 0130).

**Links.** A brand-new draft (a person's Save, an agent's `new_document` in Write with an agent, a chat or a discovery run) gets its links as `built_for`, with `added_how` person, agent_run or discovery. A built-for link cannot
be removed: unticking it is a 409 "This document was built for
{type} {name}; that link can't be removed." and nothing changes. A person's links never go through drafts: **Save links**
(`PUT /documents/{id}/links`, on a live document or a brand-new draft) applies them directly. It compares the ticked
ids with the active rows: unchanged rows stay as they are, newly ticked ones get a `linked` row, unticked `linked` rows get
`removed_at` and `removed_by`; `revision` goes up, `version` does not. Drafts of a live document use the live
document's links (Save links on a draft of a document is a 404). The one exception is an agent's
`update_document(…, resource)` on a live document: the run's draft holds that `linked` link (migration 0130) and
**publishing the draft adds it** to the document (a link the document already has stays as it is); until then the link does not
show anywhere. Publishing changes no other link. The audit row
(`document.links.updated`) lists `links_added` and `links_removed`. The selected deployment must belong to a selected service and server.

**Lifecycle.** Every save a person makes is a draft: **Save** on a new document creates a brand-new draft, Save on a
live document creates the person's draft of it (later Saves update that draft), and no route writes live content.
Content goes live only by **Publish**, for a brand-new draft and a draft of a live document alike (both
`POST /drafts/{id}/promote`, the API's name for it). A brand-new draft keeps the ID it will have as a document, opens at `/documents/{id}`
(`GET /documents/{id}` answers with `is_draft: true`, `published: false`) and can be renamed (`slug` on
`PUT /drafts/{id}`) until it is published; a draft of a live document keeps its document's name. There is no history of
a draft's own saves: version history starts when it is published. **Discovery** follows the same rule: a run's `new_document`
creates a brand-new draft (the run's `session_id`, built for the resource it is for) and `update_document` creates a
draft of an existing document (`base_version` = its live version). Its built-for link is what shows it on its resource
once published. There are no proposals (migration 0131). A document can have any
number of drafts; they are listed by `created_at`, newest first. **Publishing** (the API's promote) draft D of live document L (one
transaction locking L) archives L's content, updates L in place with D's content (`version + 1`, `from_draft`, the
same ID) and **deletes every draft of L**, D included; when D's `base_version` is older than L's `version`, promote
answers 409 `STALE_BASE` until confirmed. Publishing a brand-new draft makes that row live as version 1 with its ID,
slug, sources and built-for links. **Discarding** a draft deletes it (a brand-new draft with its links: the
document never existed). Deleting a live document deletes its drafts, archive rows and links. Stale saves (wrong
`revision`) return 409 without losing anything. Publishing audits `document.draft.promoted` for D,
`document.draft.replaced` for each other draft it deleted, and `document.updated` (`document.created` for a brand-new
draft) for the document.

**Tags and inventory documents.** Documents are classified by tags (`tags`, content like the title: edited in a draft
and made live by Publish). `GET /documents?tag=<tag>` and `GET /drafts?tag=<tag>` keep the documents or
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
| `POST /drafts` | A draft of a live document (`document_id`; text defaults to the live version) or a brand-new document (`slug`, `title`, `markdown`, optional `resource_ids`, which become built-for links); a slug naming a live document is a 409 |
| `GET /drafts` | Drafts (each row has `published`: false for a brand-new draft); `new=true` keeps brand-new documents; `environment_id`, `service_id`, `server_id`, `code_project_id`, `deployment_id` keep the drafts linked to that resource (brand-new drafts by their own links, drafts of a document by its links) |
| `GET /drafts/{id}` | A draft with its Markdown, sources, base and live version numbers, `stale` and the live Markdown |
| `PUT /drafts/{id}` | Save a draft's text with its `revision` (marks a run's draft `edited`); a brand-new draft may also send `slug` to rename it (409 when the name is taken); a draft of a document that sends one is a 400 "A draft of a published document keeps its document's name." |
| `POST /drafts/{id}/promote` | `{revision, confirm_stale?}`: Publish (a brand-new draft or a draft of a live document): make it the live version and delete the document's other drafts; returns `document_id`, `version`, `revision`, `deleted_drafts`; 409 `STALE_BASE` when the live version moved on |
| `POST /drafts/{id}/discard` | `{revision}`: delete the draft |
| `GET /documents/{id}/drafts` | Drafts of a document, newest first (no Markdown) |
| `GET /documents/{id}/connections` | `depends_on`, `used_by` and `built_from` (the live version's sources) |
| `POST /documents/{id}/links`, `DELETE /documents/{id}/links/{to_id}` | Add or remove a `depends_on` link (live documents only) |
| `GET, POST /prompts` | List / create analysis prompts; first text is a draft |
| `GET, PUT /prompts/{id}` | Read / save metadata and changed text as a new draft |
| `POST /prompts/{id}/versions/{version_id}/activate` | Activate using expected parent `revision` |
| `DELETE /prompts/{id}/versions/{version_id}` | Remove inactive version; expected `revision`, confirmation |
| `DELETE /prompts/{id}` | Remove prompt and versions; expected `revision`, confirmation |

The UI is at `/documents` (with `/documents/drafts`), `/documents/new`, `/documents/{id}` (with the sub-pages
`history`, `drafts`, `connections` and `agent-runs`; old `?tab=` links redirect),
`/prompts`, `/prompts/new`, `/prompts/{id}` (Administration → Prompts),
and `/sessions/:id` (the run page for every run); the old `/documentation/folders` pages redirect to `/documents`. `input_kind` is retired: the column remains only for the
per-workspace seed functions, and the API neither accepts nor returns it.
The shared Markdown renderer supplies preview; shadcn dialogs handle destructive
confirmation and navigation with unsaved edits.

Ask agent runs (agent sessions with purpose `revision`, started through `POST /api/sessions` from the chat pane on the
document page, with the document in the run's access, `resources.documents[{id}]`; the first message is the
instruction, and `/documents/{id}?run={run}` opens the page with the pane on that run; an old `/documents/{id}/agent/{run}`
link redirects there) change a document only
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
| `new_document(resource, title, content, description?, tags?)` | Creates a new document as a brand-new draft built for the resource (`built_for`, `added_how = 'agent_run'`, the run's `session_id`); its slug is the first free one in the project (`-2`, `-3`, ...) from the resource's name and the title. A title already used by a document linked to the same resource is refused with that document's id. Returns the id the document keeps once published. |
| `update_document(document_id, content, title?, description?, resource?)` | Saves the run's draft of the document with its complete content. With `resource`, the document is linked to that server, service or environment: a new document's draft gets the link at once, a draft of a live document holds it and publishing adds it (see **Links**). |
| `edit_document(document_id, old_text, new_text)` | Replaces one exact passage (it must occur once) in the run's draft, started from the live text when there is none yet; an empty `new_text` removes it. |

A run keeps one draft per document; every `update_document` and `edit_document` changes it. Every reply that writes
says the document is a draft waiting for a person to publish it on the document's page. Nothing goes live until a
person publishes or promotes the draft. The environment page's header counts the document drafts of the environment,
its servers and its services (also by a draft's own pending link), and its Documents tab lists them under **Drafts to
review** (`GET /api/environments/{id}/document-drafts`).

## Write a new document with an agent

**Documents → New** has two sub-pages with tab links: **Write** (`/documents/new`, the form) and **Write with an agent**
(`/documents/new/agent`). Both keep a resource's New document query (`?resource=<type>&resource_id=<id>`). Write with an
agent is a chat with no title or name up front: the agent creates documents with `new_document` (built for one of the
run's resources) and changes them, or existing documents, with `update_document` and `edit_document`. The run is started
by `POST /api/sessions` with `started_from: {new_document: true}`, on at most one resource
(`{new_document: true, server_id | service_id | environment_id | code_project_id}`: the chat is on that resource,
tagged with it like a chat on it; a server without SSH is held as the run's own resource, without SSH) and never with
a document. Without a resource the request must pick at least one (`resources.server_ids`, …): "Pick at least one
resource for the agent to write about." (400). Such runs have `purpose = 'documents'` and `context_snapshot`
`{new_document: true}` plus the resource's id. The old form-bound start (`started_from.new_document {name}` and
`resources.documents[{new: …}]`) is gone (400), and so is `agent_sessions.document_name` (migration 0145).

`GET /api/sessions/options?new_document=1` (with one resource id, or none) says what the chat may offer: the models,
the resources (the start's resource marked linked), the experts, and the prompts tagged `documentation` (no discovery
prompts). The default prompt is **Write a document** (tags `documentation` and `new-document`; 0145 gave it a new
version): look for an existing document first, create documents with `new_document`, change them with
`update_document` or `edit_document`, use the ids of mentioned drafts, and never say a document is published. The run
gets every tool that is not switched off (Advanced → Tools), including `run_subagent`; the run's access decides what
each may do.

**Documents in this chat.** `GET /api/documentation/drafts?session_id=<uuid>` (no other filter: 400) lists every draft
the conversation created (its `document.draft.created` audit events, oldest first; migration 0145 indexes them): `id`,
`title` (the open draft's, else the published document's; null for a discarded new document), `draft_of` and
`draft_of_title` (the original of a draft of an existing document), `state` (`open`, `published`, `discarded`,
`replaced`, or null when its document was deleted) and `updated_at`. The page lists them on the left (the title opens
the draft, or the published document, in a new tab; **New** or **Edit of <document>**; the state) and reloads them
when a conversation is shown, after each of its turns and whenever a result of `new_document`, `update_document` or
`edit_document` arrives in its event stream. **Mention** puts `[Draft "<title>" · <id>]` into the message box; the
document tools take the run's own draft id. **Conversations** lists the earlier Write with an agent runs
(`GET /api/sessions?purpose=documents`, with the resource's `tag` when there is one). A resource's chats
(`?tag=…&purpose=resource`) leave them out.

**Removed.** The Docs → Generate documents form and list pages (`/documentation/generations`), `POST /api/generations/sessions`,
`POST /api/generations/context` and the rule that a run's final answer becomes the draft are gone (the routes now return 404).
Migration 0101 closed the open Generate documents runs with "Closed by an update; start a new chat." Their conversations
stay readable.

The session stores two snapshots. `prompt_snapshot` holds the system prompt, the context and `prompt_id` (the saved prompt it
came from, or null); `agent_config` is the ad-hoc configuration (credential references only) with its access object (also
`access`): what the run was picked, each checked and loaded, and what that brings, built as a saved agent's run is
(`sessions/run_access.run_access`). `history` holds the Agents SDK items of successful turns, redacted; listing and detail
never return `agent_config` or `history`. These sessions have `agent_id` NULL (migration 0082), and their run records carry
the agent `ad-hoc`. `context_snapshot` holds where the run started (`new_document: true` with its resource's id,
`document_id`, or the resource's id; `{}` for a saved agent's run). The session detail returns `given_resources`, the
access entries the run was given (`[{type, id}]`: what the person chose, an environment rather than what it brings);
Start new chat with these settings restores them. Credentials are never included. Migration 0116 dropped the ids-only
`run_resources` column. A saved agent's chat sessions (its Chat tab) share the table with `purpose = 'chat'`.

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

The content lives in PostgreSQL only: no file is written when a version goes live or a document is deleted (the file export, its startup backfill and `POST /documents/{id}/export` were removed). `GET /api/documentation/documents/{id}/bundle` downloads a live document and its diagram sources as a ZIP folder; it is 404 for a brand-new draft.
