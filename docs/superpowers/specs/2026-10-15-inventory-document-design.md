# One inventory document per resource; no attachments table

Date: 2026-10-15. Status: agreed in conversation (2026-10-15), for review.
Builds on: the document model (0104: `ka.documents` live rows and drafts, `ka.document_resources` links with
`built_for`/`linked`), every-save-is-a-draft (0105) and resource runs (0106).

## Why

A document relates to a resource in two parallel ways: its own resource links (`ka.document_resources`, with
provenance, history and locked built-for links) and **attachments** (`ka.inventory_documents`, 0095, set from the
entry sheet / System map and when a discovery document is published). They disagree: a document built for a service
appears under its Documents filter but not on the service page unless someone also attached it. The person using it:
**remove `ka.inventory_documents`; a document has a boolean `inventory` flag, and that document is what is shown when
the resource (environment, service, server) is opened.**

## Model (migration 0107)

- `ka.documents.inventory boolean NOT NULL DEFAULT false`.
- **Its resource link is automatic and permanent.** An inventory document is always connected to its resource by a
  `built_for` link — created with the document (New inventory document on the resource page, or a discovery proposal
  for that resource) — and built-for links can never be removed (database check, 409 in the API, locked in Linked
  resources). Unmarking the flag does not remove the link either. Mark as inventory document is offered only for a
  document already built for exactly that one resource.
- An inventory document is the inventory document **of the resource it is built for**: it must have exactly one
  active `built_for` link, to a server, a service or an environment (not a code project, not a deployment).
- **One per resource:** among live documents and brand-new drafts, at most one inventory document is built for a given
  server, service or environment. A draft of a live document carries no links of its own and is not counted.
- Enforced in the database (a constraint trigger checked at commit on `documents` and `document_resources`): setting
  `inventory = true` without exactly one built-for server/service/environment link, or a second inventory document for
  the same resource, is refused: `<Type> <name> already has an inventory document.` /
  `An inventory document must be built for one server, service or environment.`
- `ka.inventory_documents` and its trigger are dropped; `/api/inventory/documents` (GET/POST/DELETE) is removed. Stack
  data is test data: existing attachments are not carried over. Down recreates the table empty (as 0095 defines it,
  with the 0104 trigger) and drops the column.

## Where documents of a resource come from

- **Inventory document:** the one with `inventory = true` built for it.
- **Other documents:** every live document with an active link (built-for or linked) to the resource, inventory one
  excluded.
- Readers switched from attachments to this: the resource pages, the System map (`servers/inventory_map.py`), the entry
  sheet (`inventory_links_api.py` attachment parts), `get_resource` (`capabilities/inventory.py`: returns
  `inventory_document {id, title}` and `documents [{id, title}]`), discovery's existing-document lookups
  (`capabilities/discovery.py`, `servers/discovery.py`) and the document tools' read access
  (`capabilities/document_tools.py`: a run may read, read-only, documents linked to resources it was given — replaces
  "attached to inventory entries").
- "Attach a document" in the entry sheet becomes "Link a document": adds a `linked` link through the existing
  `PUT /api/documentation/documents/{id}/links` semantics (`store.save_links`); "remove" removes that linked link
  (built-for links stay locked, as everywhere).

## Setting the flag

- An inventory document is an ordinary document with the flag: it is edited exactly like every other document
  (Save makes a draft, Promote/Publish makes it live, same version history, links and archive). Nothing about editing
  is specific to inventory documents.

- **New inventory document** on a resource page (shown when the resource has none): opens Documents → New built for the
  resource with `inventory` set; Save creates a brand-new draft with the flag, Publish makes it live.
- **Mark as inventory document** on a document whose only built-for link is a server/service/environment that has no
  inventory document yet: `PUT /api/documentation/documents/{id}/inventory {inventory: bool, revision}` (live documents
  and brand-new drafts; refused with the messages above). Unmarking clears it.
- **Discovery never changes an existing inventory document** — not directly and not through a draft of it. An accepted
  document proposal about a server (overview) or a service (one per service) creates a brand-new draft built for that
  resource: flagged `inventory` only when the resource has no inventory document (live or brand-new draft) yet;
  otherwise an ordinary (unflagged) document draft beside it. A person decides whether it replaces the inventory
  document (Unmark the old one, Mark the new one) or merges it by hand.
- Promote and Publish keep the flag (drafts of a live document inherit their document's flag; Publish keeps a brand-new
  draft's flag).

## UI

- **Resource pages** (environment, service, server) — Info shows:
  1. the **inventory document** rendered (title, Markdown as on the document page) with **Open** (to edit through
     drafts as usual); when there is none, "No inventory document yet." with **New inventory document**;
  2. **Other documents** — the list of other linked documents (title, built for / linked).
- **Document page**: an "Inventory document" badge naming the resource; **Mark as inventory document** / **Unmark** in
  the Linked resources tab when allowed.
- **Entry sheet / System map**: documents of an entry come from links; **Link a document** / remove linked link.
- **Documents list**: an `Inventory` badge on inventory documents; a filter "Inventory documents only".

## Out of scope

- Inventory documents for code projects or deployments.
- Generating inventory documents automatically (the Propose wizard already proposes them).

## Testing

- Migration: column added; `inventory_documents` gone; down recreates it empty and drops the column; round trip.
- Constraint: flag without one built-for server/service/environment refused; a second inventory document for the same
  resource refused (live + brand-new); a draft of a live inventory document is fine; unmark works.
- Readers: resource documents = inventory document + other linked documents for each type; System map and entry sheet
  show linked documents; Link/remove adds/removes a `linked` link; `get_resource` returns `inventory_document` and
  `documents`; `get_document` reads a document linked to a given resource and refuses one linked only to a resource
  the run was not given.
- Discovery: overview/service document proposal → inventory brand-new draft when the resource has none; when it has
  one → an unflagged brand-new draft, and the existing inventory document (and its drafts) unchanged; Publish keeps the
  flag; nothing written to a removed table.
- UI: resource page renders the inventory document, empty state with New inventory document, Other documents list;
  Mark/Unmark on the document page; entry sheet Link a document; Documents list badge and filter.
