# Document resources — one link table with provenance and history

Date: 2026-10-14. Status: draft for review.
Depends on: `fix/new-document-links` (runs record `chosen_resources`; a new document's draft links exactly what the run
was started for).

## Why

A document is linked to environments, services, servers, code projects and deployments through five join tables
(`ka.document_environments`, `document_services`, `document_servers`, `document_code_projects`,
`document_deployments`), each `(document_id, project_id, <resource>_id)` and nothing else. Saving a document deletes all
its link rows and inserts what the request sent. So:

- nothing records why a link exists — the resource a document was built for looks the same as one ticked later;
- a link a document was built for can be unticked, and the document then claims nothing about what it describes;
- unlinking leaves no trace (when, who, how).

Rule from the person using it: **a document built for a resource is connected to it automatically, and that connection
cannot be removed.** Other links can still be added and removed.

## Outcome

1. One table, `ka.document_resources`, holds every document ↔ resource link, with how it was added (person, agent run,
   discovery), what kind of connection it is (`built_for` or `linked`), when and by whom it was added and removed.
2. Links present when a document is created are `built_for` and can never be removed (enforced by the database).
3. Unlinking sets `removed_at` instead of deleting, so a document's link history is kept.
4. Readers do not change: `ka.document_details` keeps returning `environment_ids`, `service_ids`, `server_ids`,
   `code_project_ids`, `deployment_ids` (active links), so the Documents filters, the API shape, agents' document tools
   and version snapshots see the same arrays.
5. Linked resources shows built-for links ticked and locked ("Built for this document"), and the document page shows the
   link history.

## Data model (migration 0104)

```sql
CREATE TABLE ka.document_resources (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      uuid NOT NULL,
  document_id     uuid NOT NULL,
  environment_id  uuid,
  service_id      uuid,
  server_id       uuid,
  code_project_id uuid,
  deployment_id   uuid,
  resource_type   text NOT NULL GENERATED ALWAYS AS (CASE
                    WHEN environment_id  IS NOT NULL THEN 'environment'
                    WHEN service_id      IS NOT NULL THEN 'service'
                    WHEN server_id       IS NOT NULL THEN 'server'
                    WHEN code_project_id IS NOT NULL THEN 'code_project'
                    ELSE 'deployment' END) STORED,
  connection      text NOT NULL CHECK (connection IN ('built_for', 'linked')),
  added_how       text NOT NULL CHECK (added_how IN ('person', 'agent_run', 'discovery', 'migrated')),
  added_by        uuid REFERENCES ka.principals(id),        -- the person who saved or promoted it; NULL when migrated
  session_id      uuid,                                 -- the run that wrote it (agent_run), else NULL
  created_at      timestamptz NOT NULL DEFAULT now(),
  removed_at      timestamptz,
  removed_by      uuid REFERENCES ka.principals(id),
  FOREIGN KEY (document_id, project_id)     REFERENCES ka.documents(id, project_id)           ON DELETE CASCADE,
  FOREIGN KEY (environment_id, project_id)  REFERENCES ka.environments(id, project_id)        ON DELETE CASCADE,
  FOREIGN KEY (service_id, project_id)      REFERENCES ka.services(id, project_id)            ON DELETE CASCADE,
  FOREIGN KEY (server_id, project_id)       REFERENCES ka.servers(id, project_id)             ON DELETE CASCADE,
  FOREIGN KEY (code_project_id, project_id) REFERENCES ka.repositories(id, project_id)        ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, project_id)   REFERENCES ka.service_deployments(id, project_id) ON DELETE CASCADE,
  CHECK (num_nonnulls(environment_id, service_id, server_id, code_project_id, deployment_id) = 1),
  CHECK (connection <> 'built_for' OR removed_at IS NULL),
  CHECK ((removed_at IS NULL) = (removed_by IS NULL))
);
-- One active link per document and resource (a removed link can be added again as a new row).
CREATE UNIQUE INDEX document_resources_active_environment ON ka.document_resources(document_id, environment_id)
  WHERE removed_at IS NULL AND environment_id IS NOT NULL;
-- … the same for service_id, server_id, code_project_id, deployment_id.
-- Lookups by resource ("documents about this server"), active links only.
CREATE INDEX document_resources_environment ON ka.document_resources(environment_id) WHERE removed_at IS NULL;
-- … the same for the other four columns.
```

Decisions:

- **Real foreign keys, one nullable column per resource type** (exclusive arc), not a bare `resource_id` +
  `resource_type`: the database keeps checking that a link points at an existing resource in the same workspace, and
  deleting a resource still removes its links. `resource_type` is generated, so it cannot disagree with the columns.
- **No `active` column**: a link is active exactly when `removed_at IS NULL`.
- **Deleting a resource deletes its link rows** (history included), as today; the audit log keeps the record. Deleting a
  document deletes its rows.
- **`built_for` cannot be removed**: the check constraint makes it impossible from any code path. The only ways a
  built-for link disappears are deleting the document or deleting the resource.
- **Adding a resource type later** = one column, one unique index, one lookup index, one CASE arm.

Migration up:

1. Create the table and indexes.
2. Copy every row of the five tables as `connection = 'linked'`, `added_how = 'migrated'`, `created_at = now()`,
   `added_by = NULL`. Existing documents get no `built_for` links (nothing records what they were built for).
3. Recreate `ka.document_details` reading the arrays from active rows of `ka.document_resources` (same column names,
   same `ORDER BY` per array, same `'{}'` default).
4. Drop the five tables.

Migration down: recreate the five tables from active rows, recreate the previous `document_details`, drop
`ka.document_resources`. Provenance and history are lost on rollback; the down migration says so in a comment.

## Writing links

All writes go through `documents/api.py` `save_document` (the only writer today). It gains the provenance of the save:
`how` (`person` | `agent_run` | `discovery`) and `session_id` (agent runs).

- **Creating a document** (`document_id is None`): every link in the request is inserted as `built_for`, with `how`,
  `added_by = actor`, `session_id`. Paths:
  - promoting a draft of a new document (`documents/drafts.py` `promote_draft`): `agent_run` with the draft's
    `session_id` when the draft came from a run, else `person`; its links are the draft's `resource_ids`, which a run
    sets to the resources it was started for (`chosen_resources`, see dependency);
  - accepting a discovery proposal for a new document (`servers/discovery.py`): `discovery`; links its service, else its
    server, as today;
  - `POST /api/documents`: `person`.
- **Saving an existing document**: compare the request's ids per type with the active rows.
  - unchanged links keep their row (provenance and `created_at` stay);
  - a newly ticked resource → new row, `linked`, `how`, `added_by`;
  - an unticked `linked` row → `removed_at = now()`, `removed_by = actor`;
  - an unticked `built_for` row → the save is refused with 409 and nothing changes:
    "This document was built for {type} {name}; that link can't be removed."
  - promoting a draft of an existing document keeps the document's links (as today: it passes the active links back).
- **Archived code projects** keep today's rule on promote (`available_resources` drops a code project archived since
  the draft was made) — that applies before insert, so it never removes a built-for row.
- The audit row of the save stays as today, plus `links_added` / `links_removed` lists in its details.

## Reading links

- `ka.document_details` is the only reader for arrays (filters, API, agent document tools, version snapshots) —
  unchanged shape.
- `documents/folders.py` (the one direct join, on code projects) reads `ka.document_resources` active rows.
- `GET /api/documents/{id}` adds `resource_links`: active links with `resource_type`, `id`, `name`, `connection`,
  `added_how`, `added_by` (display name), `session_id`, `created_at`.
- New `GET /api/documents/{id}/resource-history`: all rows (active and removed), newest first, with names and who/how.

## UI

- **Linked resources** (`features/documents/DocumentResourceSelection.tsx` via `ResourcePicker`): a `built_for` link is
  ticked and locked with the note "Built for this document" (the picker's existing locked state). Other links behave
  as today. The deployment cascade (ticking a deployment ticks its service and server) is unchanged; on a new
  document, all three become `built_for`.
- **Link history**: a collapsible "Link history" section under Linked resources on the document page — one line per
  change: "Added Prod (environment) · built for · agent run · 14 Oct 10:42 · Goran", "Removed api-1 (server) · Goran ·
  …", linking to the run when `session_id` is set.
- A 409 from a save that unticked a built-for link (only possible from an outdated page) shows the message under the
  Linked resources section; the page reloads the links.

## Out of scope

- Marking links of existing documents as built-for (no record exists; a person can't promote a link to built-for in
  this change).
- Links between documents (`ka.document_links`) — unchanged.
- Resource snapshots in document versions — unchanged (they read `document_details`).

## Testing

Backend (against the disposable PostgreSQL):

- migration: rows of all five tables copied as `linked`/`migrated`; `document_details` arrays identical before and
  after for the same data; down migration restores the five tables from active rows.
- constraints: two active links to the same resource refused; a removed link can be added again; exactly one resource
  column set; a `built_for` row cannot get `removed_at`; a link to a resource of another workspace refused.
- save paths: create (POST) → `built_for`/`person`; promote of a run's new-document draft → `built_for`/`agent_run`
  with `session_id`, linking exactly the run's chosen resources (environment included); discovery accept → `built_for`
  /`discovery`; edit → added `linked`, removed rows get `removed_at`/`removed_by`, unchanged rows keep `created_at`;
  unticking a `built_for` link → 409 and no change; promote of an existing document's draft leaves links untouched.
- deleting a resource removes its link rows; deleting a document removes all its rows.
- `GET /documents/{id}` `resource_links` and `GET /documents/{id}/resource-history` content and order.
- `folders.py` statistics count active code-project links only.

UI:

- Linked resources shows built-for links ticked and locked with the note; other links toggle as today.
- Link history lists added/removed entries with how/who/when and a run link.
- A 409 on save shows the message and reloads links.

## Open question for review

- Can a person add more links to a document next to its built-for links? This spec says **yes** (they are `linked` and
  removable).
