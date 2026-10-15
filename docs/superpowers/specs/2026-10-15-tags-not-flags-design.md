# Tags instead of special columns

Date: 2026-10-15. Status: agreed in conversation (2026-10-15).
Builds on: inventory document (0107) and the resource document list (b1a3b42).

## Why

Two special columns carry what documents and tags already carry:

- `ka.services.operations_notes` (0003) — free-form operating notes on the service record, written by people and by
  discovery's `propose_service`. Operating knowledge belongs in documents (drafts, versions, review).
- `ka.documents.inventory` (0107) — a boolean with database triggers. The person using it: the inventory document is
  just a document with a tag.

Rule: resources (services, servers, environments) keep a short `description` (a few sentences); everything else is in
documents; documents are classified by tags, and lists filter by tag.

## Changes (migration 0109)

1. **`services.operations_notes` removed**: column dropped (test data — notes are not converted); the "Behavior and
   investigation notes" section and the form field go; `propose_service` loses `operations_notes` (its schema in
   `tools/tools.json` and the proposal/accept code); every reader stops selecting it (`get_resource`, agent wizard,
   run configuration snapshots, ephemeral config, accept_service, service API/models).
2. **`documents.inventory` replaced by the tag `inventory`**: rows with `inventory = true` get `inventory` appended to
   `tags` (no duplicate); the column, its constraint trigger (one per resource), the trigger clearing it on resource
   delete, and `PUT /api/documentation/documents/{id}/inventory` are removed. Down: re-adds the column and triggers and
   sets `inventory = 'inventory' = ANY(tags)` where that satisfies the old constraint (otherwise refuses with
   "… would be lost; …" per convention), re-adds `operations_notes` empty.
3. **What "the inventory document" means now**: the resource's inventory document is the live document tagged
   `inventory` that is built for the resource; if several, the most recently updated; the others appear in the
   resource's Documents list. A brand-new draft tagged `inventory` built for the resource is shown when there is no live
   one (as today). Nothing enforces one per resource.
4. **Tags are content**: edited in the document editor like title and description (Save → draft → Promote/Publish).
   "New inventory document" on a resource page starts the new document with the tag `inventory` and the built-for link;
   "Write with an agent" from there carries the tag (instead of the flag) and the resource as today.
5. **Discovery**: an accepted overview/service document proposal gets the tag `inventory` only when the resource has no
   document tagged `inventory` built for it (live or brand-new draft); it never changes an existing document.
6. **Agents**: `get_resource` keeps returning each document's `tags` (no separate `inventory` field); prompts/tool texts
   that mention the inventory flag say "the document tagged `inventory`".
7. **Filtering by tag**: `GET /api/documentation/documents?tag=<tag>` and `GET /api/documentation/drafts?tag=<tag>`
   (exact, case-insensitive match on one tag); the Documents list and Drafts have a tag filter; every tag badge (lists,
   resource pages, document page) is a link applying that filter. The "Inventory documents only" switch is removed.

## Testing

- Migration: flagged documents get the tag; column/triggers/route gone; `operations_notes` gone; round trip (down
  refuses only when two documents tagged `inventory` are built for one resource).
- Resource page/API: inventory document = live document tagged `inventory` built for it (newest wins; brand-new draft
  shown when no live one); list includes the others.
- Tags: editing tags through a draft and promote; `?tag=` filters documents and drafts.
- Discovery: tag `inventory` only when none exists; existing documents unchanged; `propose_service` without
  `operations_notes`.
- UI: no operations notes section or field; New inventory document pre-fills the tag; tag badges link to the filter;
  tag filter on Documents and Drafts; no "Inventory documents only" switch.
