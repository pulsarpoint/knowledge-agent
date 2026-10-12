# Document drafts — design

Date: 2026-10-12
Status: approved in conversation. First of two specs; the second ("Ask agent" on a document: a start panel with
instruction, model, SSH servers, tools, optional experts and subagents, and a `save_draft` tool) builds on this one.

## Why

Agents should be able to write documents freely, but a person decides what becomes the live document. Today every
save is immediately the live version, and a Generate documents run's result only becomes a document when someone
clicks "Save document", which overwrites the document of the same name. We want:

- drafts: unpublished versions of a document, any number per document, promoted to live or discarded by a person;
- where a draft came from: the document it revises and the documents its author read (sources);
- links between documents that people add by hand ("depends on");
- the documents list showing only live documents, each with its number of drafts; the document page showing its
  drafts and connections.

An external wiki (XWiki change requests, BookStack, Wiki.js, Outline, Docmost, Markdown in Git) was considered and
rejected: the hard part is the link to our inventory, export folders, agents and runs, not the editor.

## Decisions

- A draft is conceptually a version of the same document, stored in its own table (`ka.document_drafts`) so that
  version history, `document_details`, exports, folder statistics and every reader of `ka.document_versions` keep
  seeing live versions only, unchanged.
- Anything an agent writes is a draft. A version becomes live only when a person saves it in the editor ("Save new
  version"), promotes a draft, or accepts a discovery document proposal.
- Promoting a draft whose base is no longer the live version warns, shows the diff against the live version, and
  promotes only after explicit confirmation.
- Accepting a discovery proposal that is a new version of an existing document asks for confirmation in the UI first.

## Data — migration 0098

### `ka.document_drafts`

| Column | Meaning |
|---|---|
| `id`, `project_id`, `created_at`, `updated_at` | |
| `document_id` | the document it revises (`ON DELETE CASCADE`); NULL = a new document |
| `slug`, `folder` | new documents only: where the document will be created (same rules as `ka.documents`); NULL when `document_id` is set |
| `resource_ids jsonb` | new documents only: `{code_project_ids, service_ids, server_ids, deployment_ids, environment_ids}` to link on promote; `{}` otherwise |
| `base_version_id` | the live version the draft started from (NULL for a new document); FK to `document_versions`, `ON DELETE SET NULL` |
| `title`, `description`, `purpose`, `audience`, `tags`, `markdown` | the content, with the same limits as `DocumentInput` |
| `origin` | `manual` or `agent` |
| `session_id` | the agent run that wrote it (`ON DELETE SET NULL`) |
| `edited` | true once a person saved it (an agent run then starts a new draft instead of overwriting it) |
| `status` | `open`, `promoted`, `discarded` |
| `promoted_version_id` | the live version created on promote |
| `created_by`, `updated_by`, `decided_by`, `decided_at` | |
| `revision` | optimistic locking |

Constraints: `document_id` and (`slug`, `folder` slug) are exclusive (`document_id IS NULL` ⇔ `slug IS NOT NULL`);
`status='promoted'` ⇔ `promoted_version_id IS NOT NULL`; a promoted or discarded draft is frozen (a trigger refuses
any update of a row whose status is not `open`). Index on `(document_id) WHERE status='open'` and on
`(project_id) WHERE status='open' AND document_id IS NULL`.

### `ka.document_sources` — what a draft or version was built from (automatic)

| Column | Meaning |
|---|---|
| `id`, `project_id`, `created_at` | |
| `draft_id` | the draft (`ON DELETE CASCADE`) — or |
| `version_id` | the live version (`ON DELETE CASCADE`); exactly one of the two is set |
| `kind` | `parent` (the document being revised) or `read` (a document the author read) |
| `source_document_id` | `ON DELETE SET NULL` |
| `source_version_id` | the version that was live when it was read, `ON DELETE SET NULL` |
| `source_title`, `source_version` | snapshot, so a deleted source still shows "(deleted)" with its title and version |

Unique per (draft or version, source document). On promote the draft's sources are copied to the new version.

### `ka.document_links` — dependencies people add

`id`, `project_id`, `from_document_id`, `to_document_id` (both `ON DELETE CASCADE`), `created_by`, `created_at`;
`kind` is `depends_on` (the only kind for now); unique (`from`, `to`, `kind`); `from <> to`.

### Down

Refuses while open drafts, sources or links exist (the data would be lost).

## Behaviour

### Saving and editing a draft

- `POST /drafts` creates a draft: of a document (`document_id`; the base is its live version; the content defaults to
  the live version's) or of a new document (`slug`, `folder`, `title`, `markdown`, optional `resource_ids`). Origin
  `manual`. Sources: for a draft of a document, one `parent` source.
- `PUT /drafts/{id}` `{revision, title, description, purpose, audience, tags, markdown}` updates an open draft; sets
  `edited=true` and `updated_by`. A stale revision → 409 "This draft changed. Reload it before saving; your text has
  not been overwritten."
- A promoted or discarded draft → 409 "This draft was already promoted or discarded."

### Promote

`POST /drafts/{id}/promote` `{revision, confirm_stale?}`:

- **Draft of a document:** if the document's live version is not `base_version_id` and `confirm_stale` is not true →
  409 with code `STALE_BASE`, message "This draft was based on version {base}; the live version is now {live}.
  Promoting replaces version {live}'s text." The UI shows the diff against the live version and a Promote anyway
  button. Otherwise the draft's content is saved through `save_document` as a new live version, keeping the document's
  slug, folder and linked resources (as in the editor), origin `generated` for agent drafts and `manual` otherwise.
- **New document:** if a document with that folder and slug now exists, the draft becomes a draft of it (its
  `document_id` and `base_version_id` are set, `slug`/`folder` cleared, revision bumped, committed) and promote
  answers 409 "A document named {slug} was created in this folder meanwhile. The draft is now a draft of it. Review it
  again." Otherwise `save_document` creates it with `resource_ids`.
- The draft becomes `promoted` with `promoted_version_id`; its sources are copied to the version; the document is
  exported (export failures answer as the editor does: saved, export failed).
- Locks: the project (`FOR NO KEY UPDATE`, as other document saves), then the draft `FOR UPDATE`.

### Discard

`POST /drafts/{id}/discard` `{revision}` → `discarded`. Discarded drafts are kept (audit) but never listed.

### Links

`POST /documents/{id}/links` `{to_document_id}`; `DELETE /documents/{id}/links/{to_document_id}`. Linking a document
to itself or twice → 400 / 409 with a clear message.

### Reads

- `GET /documents` adds `draft_count` (open drafts) to each row and a `has_drafts=true` filter.
- `GET /drafts?new=true` → open drafts of new documents (title, folder, slug, origin, session, author, updated).
- `GET /documents/{id}/drafts` → open drafts of the document (without Markdown).
- `GET /drafts/{id}` → the draft with Markdown, its sources, its base version number and the document's live version
  number (so the UI knows whether it is stale) and the live Markdown for the diff.
- `GET /documents/{id}/connections` → `depends_on` (links from it), `used_by` (links to it), `built_from` (the live
  version's sources).
- `GET /documents/{id}` marks versions created from a draft (`from_draft: true`).

### Generate documents

- When a documents-run turn ends with a draft (the worker's existing `agent_sessions.draft` write), the worker also
  saves it as a document draft: it updates the run's latest open, not `edited` draft, or creates one. The target is the
  document at the run's folder and name if one exists (draft of it, base = its live version), else a new document
  (slug from the name, the run's folder, `resource_ids` from the run's selection). Origin `agent`, `session_id` the run.
- Sources are recomputed on every save from the run's tool calls (`agent_run_events`): `get_document` calls (document
  id) and `read_document` calls whose path is a document's export (`<folder>/<slug>/README.md`), each with the version
  live at the time of the call; plus the `parent` source.
- The run page replaces "Save document" with **Open draft**, linking to the draft on its document's Drafts tab (or the
  new-document draft). Runs from before this change, which have a run draft but no document draft, show **Save as
  draft**: `POST /generations/sessions/{id}/document` now creates the document draft from the run draft instead of
  saving a live version.

### Server discovery

Backend unchanged. The proposal panel asks "This replaces the live version {n} of “{title}”. Continue?" before
accepting a document proposal that is a new version.

### Deleting a document

Its drafts and links are deleted (cascade); sources pointing at it keep their snapshot and show "(deleted)".

### Never seen as documents

Drafts are not exported, not in the folder statistics, not in `document_details`, not returned by `read_document`,
`search_documents`, `get_document` or the inventory tools, and not in version history.

## UI

- **Documents list:** a Drafts column ("2 drafts", links to the document's Drafts tab); a "Has drafts" filter; a
  **New drafts** section above the table when new-document drafts exist (title, folder, written by, updated; Open,
  Discard).
- **Document page:**
  - **Save draft** beside "Save new version": saves the editor's text as a new draft, or updates the draft being
    edited.
  - **Drafts** tab: open drafts (written by a person or "Agent run …" linking to the run, updated, based on version
    n, "stale" badge when the live version moved on). Opening one shows the diff against the live version / preview,
    sources, Edit (loads it into the editor; Save draft then updates it), Promote (stale warning and confirmation),
    Discard.
  - **Connections** tab: Depends on (add with a document picker, remove), Used by, Built from.
  - Version history: "from draft" note on versions created by promoting.
- **Draft page for a new document** (`/documents/drafts/:draftId`): the same draft view, with Promote creating the
  document.
- **Generate documents run page:** Open draft / Save as draft as described.
- **Discovery proposal panel:** the confirmation before replacing a live version.

## Audit

`document.draft.created`, `document.draft.updated`, `document.draft.promoted`, `document.draft.discarded`,
`document.link.added`, `document.link.removed`.

## Tests

- Migration: up/down; constraints (exclusive target, promoted ⇔ version, frozen rows, link self/duplicate); down
  refuses with data.
- API: create (of a document, new), update with stale revision, promote (fresh, stale → 409 STALE_BASE, confirmed,
  new document, new document whose name was taken meanwhile → converted + 409), discard, decided twice → 409; sources
  copied on promote; list `draft_count` and `has_drafts`; drafts never in `document_details`, exports, history or
  `get_document`; links add/remove/self/duplicate; connections; cascade on document delete; audit.
- Worker: a documents run's turn creates then updates its draft; an edited draft makes the next turn create a new one;
  target existing document vs new; sources from `get_document` and `read_document` events.
- UI: list column and filter and New drafts section; Save draft; Drafts tab with stale warning and Promote anyway;
  Connections add/remove; run page Open draft / Save as draft; discovery confirmation.

## Not in scope

"Ask agent" on a document and the `save_draft` tool (spec 2); choosing tools/SSH/experts per run (spec 2); drafts and
document links on the System map; merging two drafts; comments on drafts.
