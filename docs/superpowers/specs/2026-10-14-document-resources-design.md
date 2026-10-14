# Documents, drafts, archive and resource links

Date: 2026-10-14. Status: draft for review (v2 — replaces the link-table-only draft of the same date).
Builds on: 7c28610 (a run records `chosen_resources`; a new document is linked to what its run was started for).

## Why

Today a document is spread over three tables plus five link tables:

- `ka.documents` — identity (slug, folder, revision) and `current_version_id`;
- `ka.document_versions` — every version, numbered and immutable;
- `ka.document_drafts` — drafts, kept apart; a draft of a *new* document has no document yet, so it carries slug,
  folder and its links-to-be as JSON (`resource_ids`), which no foreign key checks and no index can query;
- `ka.document_environments` / `_services` / `_servers` / `_code_projects` / `_deployments` — links with no record of
  why, when or by whom; a save deletes and re-inserts them all.

Rules from the person using it:

1. A document built for a resource is connected to it automatically, and that connection cannot be removed. Other links
   can be added and removed.
2. A document can have as many drafts as people and runs make, listed by `created_at`.
3. Promoting a draft makes it the document's live version, archives the previous live content and deletes every other
   draft — one clean document remains.
4. Drafts for a resource are as easy to find as documents for it.

## Outcome

- `ka.documents` holds live documents **and** drafts. A draft row is either a draft of a live document (`draft_of`) or a
  brand-new document that has never been live.
- `ka.document_archive` holds earlier live contents of a document (`document_id` = the live document's id).
- `ka.document_resources` holds every document ↔ resource link, with provenance, history and locked built-for links.
- A live document's id never changes, so everything pointing at it keeps working.

## Data model (migration 0104)

### `ka.documents` — live documents and drafts

```sql
-- existing identity columns stay: id, project_id, slug, folder, revision, created_at, updated_at, phase_metadata
ALTER TABLE ka.documents
  ADD COLUMN is_draft     boolean NOT NULL DEFAULT false,
  ADD COLUMN draft_of     uuid,          -- FK (draft_of, project_id) → documents(id, project_id) ON DELETE CASCADE
  ADD COLUMN version      integer,       -- live: its version number; draft: NULL
  ADD COLUMN base_version integer,       -- draft of a live document: the live version it started from
  -- content, moved here from document_versions / document_drafts
  ADD COLUMN title text, description text, purpose text, audience text, tags text[], markdown text,
             content_hash sha256, source_snapshot jsonb,
  -- provenance
  ADD COLUMN origin text,                -- 'manual' | 'agent' | 'uploaded' | 'discovery'
  ADD COLUMN session_id uuid,            -- the run that wrote it (drafts by agents; kept on the live row after promote)
  ADD COLUMN created_by uuid, updated_by uuid;  -- REFERENCES ka.principals(id)
-- same CHECKs on content as today (nonblank title ≤ 240, markdown ≤ 1 MB, content_hash = sha256(markdown), …)
CHECK (NOT is_draft OR version IS NULL)
CHECK (is_draft OR version IS NOT NULL)
CHECK (draft_of IS NULL OR is_draft)                 -- only drafts point at a live document
CHECK (draft_of IS NULL OR (slug IS NULL AND folder IS NULL))  -- a draft of a live document uses its document's slug/folder
CHECK (is_draft OR slug IS NOT NULL)
-- slugs are unique among live documents and brand-new drafts, not among drafts of a live document
CREATE UNIQUE INDEX documents_slug ON ka.documents(project_id, slug) WHERE slug IS NOT NULL;
CREATE INDEX documents_drafts ON ka.documents(draft_of, created_at) WHERE is_draft;
-- a draft of a live document must point at a live row (trigger: draft_of → is_draft = false)
```

`current_version_id`, `published_version_id` and their deferred foreign keys are dropped.

Kinds of row:

| Row | `is_draft` | `draft_of` | `slug`/`folder` | `version` |
|---|---|---|---|---|
| Live document | false | NULL | set | 1, 2, … |
| Draft of a live document | true | live id | NULL (uses the live row's) | NULL |
| Brand-new document (never live) | true | NULL | set | NULL |

### `ka.document_archive` — earlier live contents

```sql
CREATE TABLE ka.document_archive (
  id           uuid PRIMARY KEY,         -- new per archived version; migrated rows keep their document_versions id
  project_id   uuid NOT NULL,
  document_id  uuid NOT NULL,            -- the live document; FK → documents(id, project_id) ON DELETE CASCADE
  version      integer NOT NULL,
  title, description, purpose, audience, tags, markdown, content_hash, source_snapshot,   -- as on documents
  origin text NOT NULL, session_id uuid, created_by uuid,
  created_at   timestamptz NOT NULL,     -- when that version went live
  archived_at  timestamptz NOT NULL DEFAULT now(),
  archived_by  uuid,                     -- who promoted the version that replaced it
  UNIQUE (document_id, version)
);
```

### `ka.document_resources` — links with provenance and history

```sql
CREATE TABLE ka.document_resources (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      uuid NOT NULL,
  document_id     uuid NOT NULL,   -- FK (document_id, project_id)     → documents(id, project_id)           ON DELETE CASCADE
  environment_id  uuid,            -- FK (environment_id, project_id)  → environments(id, project_id)        ON DELETE CASCADE
  service_id      uuid,            -- FK (service_id, project_id)      → services(id, project_id)            ON DELETE CASCADE
  server_id       uuid,            -- FK (server_id, project_id)       → servers(id, project_id)             ON DELETE CASCADE
  code_project_id uuid,            -- FK (code_project_id, project_id) → repositories(id, project_id)        ON DELETE CASCADE
  deployment_id   uuid,            -- FK (deployment_id, project_id)   → service_deployments(id, project_id) ON DELETE CASCADE
  resource_type   text NOT NULL GENERATED ALWAYS AS (CASE WHEN environment_id IS NOT NULL THEN 'environment'
                    WHEN service_id IS NOT NULL THEN 'service' WHEN server_id IS NOT NULL THEN 'server'
                    WHEN code_project_id IS NOT NULL THEN 'code_project' ELSE 'deployment' END) STORED,
  connection      text NOT NULL CHECK (connection IN ('built_for', 'linked')),
  added_how       text NOT NULL CHECK (added_how IN ('person', 'agent_run', 'discovery', 'migrated')),
  added_by        uuid REFERENCES ka.principals(id),
  session_id      uuid,            -- the run that added it (agent_run)
  created_at      timestamptz NOT NULL DEFAULT now(),
  removed_at      timestamptz,
  removed_by      uuid REFERENCES ka.principals(id),
  CHECK (num_nonnulls(environment_id, service_id, server_id, code_project_id, deployment_id) = 1),
  CHECK (connection <> 'built_for' OR removed_at IS NULL),   -- a built-for link is never removed
  CHECK ((removed_at IS NULL) = (removed_by IS NULL))
);
-- one active link per document and resource (a removed link can be added again as a new row)
CREATE UNIQUE INDEX document_resources_active_environment ON ka.document_resources(document_id, environment_id)
  WHERE removed_at IS NULL AND environment_id IS NOT NULL;            -- … and for the other four columns
CREATE INDEX document_resources_environment ON ka.document_resources(environment_id) WHERE removed_at IS NULL;
                                                                       -- … and for the other four columns
-- trigger: document_id must be a live document or a brand-new draft (draft_of IS NULL), never a draft of a live one
```

Decisions:

- **A live document's id is stable.** Promote updates the live row in place; it never makes the draft row the live row
  (except for a brand-new document, whose draft row is the document). About ten tables point at `documents(id)`
  (resource links, document links, inventory links, system graph, discovery proposals, a run's target document, …) and
  keep working.
- **Real foreign keys, one nullable column per resource type** (not a bare `resource_id` + `resource_type`): the
  database checks the resource exists in the same workspace, and deleting a resource removes its links. `resource_type`
  is generated so it cannot disagree.
- **No `active` column**: a link is active exactly when `removed_at IS NULL`.
- **Readers never see drafts by accident**: `ka.live_documents` (view: `NOT is_draft`) and `ka.document_details` (live
  rows with their link arrays) are what every reader uses. Triggers refuse links between documents, inventory links and
  system-graph rows that point at a draft.
- **References to a specific version** become `(document_id, version)` — a version is either the live row or an
  archive row, and one foreign key cannot point into two tables. The document keeps its real foreign key.

## Lifecycle

- **New document** (Documents → New run, discovery, `POST /api/documents`): insert a brand-new draft row (slug, folder,
  content, `origin`, `session_id`) and its links as `built_for` (`added_how` agent_run / discovery / person). A run's
  links are its `chosen_resources`.
- **Draft of a live document** (Ask agent run, a person's edit): insert a draft row with `draft_of`, `base_version` =
  the live version, content. A document can have any number of drafts; the document page lists them by `created_at`.
  A run keeps updating its own draft row (found by `session_id` + `draft_of`), as today.
- **Promote draft D of live document L** (one transaction, locking L):
  1. copy L's current content into `document_archive` (`document_id` = L, `version`, `created_at` = when it went live,
     `archived_by` = actor);
  2. update L in place with D's content, `version = version + 1`, `origin`/`session_id`/`created_by` from D;
  3. delete all drafts of L (D included);
  4. `revision` bumps as today; stale check as today (D's `base_version` < L's `version` → confirm, as `confirm_stale`).
- **Promote a brand-new draft D**: set `is_draft = false`, `version = 1`. Its id, slug, folder and built-for links stay.
- **Discard a draft of a live document**: delete the row.
- **Discard a brand-new draft**: delete the row (and its links): the document never existed.
- **Delete a live document**: deletes its drafts, archive rows and links (cascade), as today.
- **Edit a live document directly** (the document editor's Save without a draft): same as promote from an implicit
  draft — current content is archived, the row is updated, other drafts stay (only promote clears drafts).
- A run whose draft was deleted by another promote starts a new draft (of the new live version) on its next save.

## Links

- Writes go through one function (`documents/api.py` `save_links`), called by create, edit and promote with the
  provenance of the save (`how`, `actor`, `session_id`).
- **Creating** (brand-new draft): every link → `built_for`.
- **Saving links of a live document or brand-new draft** (Linked resources): compare requested ids with active rows —
  unchanged rows stay (provenance and `created_at` kept); newly ticked → new `linked` row; unticked `linked` →
  `removed_at`/`removed_by`; unticked `built_for` → 409, nothing changes: "This document was built for {type} {name};
  that link can't be removed."
- **Drafts of a live document** use the live document's links (no rows of their own).
- **Promote** never changes links (a brand-new draft keeps its rows; a draft of a live document has none).
- The save's audit row gains `links_added` / `links_removed`.

## Reading

- `ka.document_details` — live documents only, same columns as today (content now from the row itself, link arrays from
  active `document_resources` rows), plus `draft_count`. Filters, the documents API, agents' document tools and RAG
  export read it unchanged.
- `ka.document_drafts_view` — every draft row with its document's title/slug/folder (brand-new: its own), `created_at`,
  `origin`, `session_id`, and the link arrays of the live document or of the brand-new draft.
- Version history (`GET /api/documents/{id}/versions`) = archive rows + the live row, by version.
- `GET /api/documents/{id}` adds `drafts` (newest first) and `resource_links` (active links with type, name, connection,
  added_how, added_by, session_id, created_at). `GET /api/documents/{id}/resource-history` returns all link rows.
- `GET /api/drafts?environment_id=…|service_id=…|server_id=…|code_project_id=…|deployment_id=…` — drafts for a
  resource: brand-new drafts linked to it, and drafts of live documents linked to it.
- `documents/folders.py` statistics count live documents only.

## UI

- **Document page**: a Drafts list (newest first; who/which run, when, base version, Open / Promote / Discard). Promote
  warns that the other drafts will be deleted ("Promote and delete 2 other drafts?").
- **Draft page**: as today; Promote and Discard follow the lifecycle above.
- **Documents list**: live documents; a "Drafts" filter/tab lists draft rows (brand-new and drafts of live documents),
  filterable by resource like documents.
- **Linked resources**: built-for links ticked and locked ("Built for this document"); others toggle; a collapsible
  "Link history" lists added/removed links with how/who/when and a link to the run.
- **Version history**: reads archive + live, unchanged in look.

## Migration 0104 (up)

1. Add the new columns to `ka.documents`; copy each document's current version content into its row
   (`version`, `title`, …, `origin`, `created_by` from that version).
2. Create `ka.document_archive`; copy every non-current version, keeping its id (`document_id`, `version`, content, `created_at`,
   `created_by`; `archived_at` = the next version's `created_at`, `archived_by` NULL).
3. Turn every **open** `ka.document_drafts` row into a draft row: of a live document → `draft_of`, `base_version`
   from its base version; of a new document → brand-new draft (slug, folder, content, `session_id`) and its
   `resource_ids` JSON → `built_for` link rows (ids that no longer exist are dropped, as promote does today).
   Promoted and discarded drafts are not carried over.
4. Create `ka.document_resources`; copy every row of the five link tables as `linked` / `migrated`.
5. `document_sources`: rows owned by a draft move to the new draft row id; rows owned by a version become
   `(document_id, version)`; `source_version_id` → `(source_document_id, source_version)` (the snapshot columns
   already hold title and version).
6. Repoint `agent_sessions` draft references (a run's draft is found by `session_id`) and drop `ka.document_drafts`,
   `ka.document_versions` and the five link tables; recreate `ka.document_details`; create `ka.live_documents` and
   `ka.document_drafts_view`.

Down: rebuild `document_versions` (archive + live rows), `document_drafts` (open draft rows), the five link tables
(active rows) and the previous views. Link provenance/history and archive timestamps are lost on rollback (stated in
the down file).

## Out of scope

- Links between documents (`ka.document_links`, "depends on") — unchanged.
- Marking links of existing documents as built-for (nothing records what they were built for).
- Restoring an archived version as live (could later be "promote from archive").

## Testing

Backend (disposable PostgreSQL):

- migration: documents keep id, slug, folder, current content and version; archive holds every earlier version with
  its number; open drafts become draft rows (brand-new ones with built-for links); the five link tables' rows become
  `linked`/`migrated`; `document_details` returns the same rows and arrays as before for the same data; down restores
  the old tables.
- constraints/triggers: slug unique among live + brand-new rows only; a draft of a live document has no slug; a link
  or document link to a draft of a live document refused; one active link per resource; a built-for link can't be
  removed; links across workspaces refused.
- lifecycle: many drafts per document listed by `created_at`; promote archives the live content, updates the row in
  place (same id), bumps version, deletes every draft; promote of a brand-new draft keeps its id and links; discard
  deletes; a run whose draft was deleted starts a new one; stale promote asks for confirmation as today.
- links: create → `built_for` with `added_how`/`session_id` (run: exactly `chosen_resources`, environment included);
  edit → `linked` added, unticked rows get `removed_at`; unticking built-for → 409, no change; deleting a resource
  removes its rows.
- reading: document details and filters see live rows only; `GET /api/drafts?environment_id=` finds brand-new drafts
  linked to it and drafts of documents linked to it; version history = archive + live; agents' document tools and RAG
  export unchanged.

UI: drafts list and promote warning; drafts filter by resource; locked built-for links; link history; version history.
