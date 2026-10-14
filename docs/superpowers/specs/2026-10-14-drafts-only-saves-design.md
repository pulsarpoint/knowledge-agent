# Every save is a draft

Date: 2026-10-14. Status: approved in conversation (2026-10-14).
Builds on: `2026-10-14-document-resources-design.md` (live documents and drafts in `ka.documents`, archive, resource
links), merged at 48f5209.

## Why

The document editor offers **Save draft** and **Save new version** ("Create document" on a new document). The second
writes the live document directly, which bypasses drafts: two ways to change a document, and a state machine where a
draft and a live save race. The rule from the person using it: **all saves are drafts, even for a new document.**
Content becomes live only by **Publish** (a document that is only a draft) or **Promote** (a draft of a live
document).

## State machine

| Action | From | Result |
|---|---|---|
| Save on a new document | nothing | a brand-new draft (built-for links = the chosen resources); later Saves update it |
| Save on a live document | live version N | a draft of the document (`base_version = N`); the page switches to editing it; later Saves update it |
| Save while editing a draft | that draft | the same draft is updated (same id, `revision + 1`) — never a draft of a draft |
| A person saves a run's draft | the run's draft | the same draft, now `edited` (the person's): the run stops writing to it and is told so (unchanged rule) |
| Publish | a brand-new draft | live document, version 1, same id (unchanged store `promote_draft`) |
| Promote | a draft of a live document | live version N+1; the other drafts are deleted after the warning (unchanged) |
| Discard | any draft | deleted (unchanged) |
| Save links | a live document or a brand-new draft | links applied directly with history; built-for links locked (unchanged rules) |

There is no history of a draft's own saves; a document's version history starts when it is published. Several drafts of
one document exist only because different people or runs each started one.

## API

- Removed: `POST /api/documentation/documents` (create live) and content changes through
  `PUT /api/documentation/documents/{id}`.
- New: `PUT /api/documentation/documents/{id}/links` `{revision, code_project_ids, service_ids, server_ids,
  deployment_ids, environment_ids}` → links of a live document or a brand-new draft (`store.save_links`, `linked`,
  built-for refusal 409 as today), `revision + 1`, audit `document.links.updated {links_added, links_removed}`.
- `PUT /api/documentation/documents/{id}` is removed (its only remaining use was links).
- New: `GET /api/documentation/documents/{id}` also answers for a **brand-new draft** (same id the document will keep):
  the row with `is_draft: true`, `published: false`, content, `resource_links`, no versions, no drafts. A draft of a
  live document still answers 404 there (it is opened as a draft, `GET /drafts/{id}`).
- `POST /api/documentation/drafts` unchanged (a person's Save): new document (`slug`, `folder`, `resource_ids`) or a
  draft of a live document (`document_id`). `PUT /drafts/{id}` updates it; a brand-new draft's `slug`/`folder` may
  change there (name rule: "A document or draft named … already exists in this folder. Choose another name.").
- `POST /drafts/{id}/promote` serves both Publish and Promote (unchanged).
- Draft list rows (`GET /drafts`, `GET /documents/{id}/drafts`) carry `published` (false for brand-new drafts) so the
  UI can say "Not published".
- `save_document(...)` (the Python function) stays for internal callers that must write live content (none after this
  change except tests) — remove it if nothing calls it.

## Discovery

Accepting a server-discovery proposal no longer writes live content:

- a proposal for a **new** document creates a brand-new draft (`how = 'discovery'`, `origin = 'generated'`, built-for
  links: its service, else its server) and records it on the proposal (`discovery_proposals.draft_id`, new column,
  `REFERENCES documents(id) ON DELETE SET NULL`, not subject to the live-document trigger). The document is attached
  to the inventory entry (`inventory_documents`) when the draft is **published** (publish looks up accepted proposals
  with that `draft_id`); a discarded draft attaches nothing.
- a proposal that **updates** a document creates a draft of it (`base_version` = its live version) and records
  `draft_id`; `document_id` stays the live document. The person promotes it from the document's Drafts tab.
- The proposal's accepted state links to the draft (`/documents/{draft_id}` for a brand-new draft, the draft page for
  a draft of a live document); once published/promoted the same links lead to the live document.
- Migration 0105: `ALTER TABLE ka.discovery_proposals ADD COLUMN draft_id uuid REFERENCES ka.documents(id) ON DELETE SET
  NULL` (down drops it).

## UI

- **Documents → New**: one **Save** (creates the brand-new draft, then opens it at `/documents/{id}`). "Create document"
  is gone.
- **Brand-new draft page** (`/documents/{id}` when `is_draft`): banner "Draft — not published yet"; tabs Write,
  Preview, Linked resources (built-for locked, Save links), Agent runs; buttons **Save**, **Publish**, **Discard**; the
  name (slug, folder) is editable until Publish. No Version history, no Drafts tab. After Publish the page becomes the
  normal document page. `/documents/drafts/{id}` redirects to `/documents/{id}` for a brand-new draft.
- **Live document page**: header "Version N"; tab **Drafts** with a count badge ("Drafts 3"); Write tab **Save**
  creates the person's draft and switches the editor to it, with the banner "Editing your draft (created {time}, from
  version {n}). Save updates it; Promote makes it live."; later Saves update it. "Save new version" is gone.
- **Drafts tab**: one row per draft — author or run, created, base version, "stale" badge — with Edit (open in the
  editor), Promote (warning as today) and Discard.
- **Linked resources** on a live document or a brand-new draft: **Save links** (link changes do not go through drafts).
- **Documents list → Drafts**: brand-new drafts show "Not published" and the actions Open, Publish, Discard; drafts of
  live documents show their document's title and Open.
- Server page (discovery): an accepted proposal links to its draft; the "accepted" copy says the document is waiting
  to be published / promoted.

## Out of scope

- Renaming a published document (slug/folder fixed after Publish; a Rename action can come later).
- History of a draft's own saves.

## Testing

- API: `POST /documents` and `PUT /documents/{id}` are gone (405/404); `PUT /documents/{id}/links` adds/removes linked
  links with history, refuses built-for removal (409) and stale revision (409), works on a brand-new draft and refuses
  a draft of a live document (404); `GET /documents/{id}` returns a brand-new draft with `is_draft: true` and 404 for a
  draft of a live document; a brand-new draft's slug change via `PUT /drafts/{id}` (and the name clash 409).
- Discovery: accepting a new-document proposal creates a brand-new draft with built-for links and `draft_id`, no live
  document, no inventory attachment; publishing it attaches it to the inventory entry; discarding attaches nothing;
  accepting an update proposal creates a draft of the document with `draft_id`, the live content unchanged.
- UI: New → Save opens the brand-new draft page; the brand-new draft page has Save/Publish/Discard and no Version
  history/Drafts tabs; Publish turns it into the live page; on a live document Save creates a draft and shows the
  banner, a second Save updates the same draft; the Drafts tab badge counts drafts; there is no "Save new version" or
  "Create document" anywhere; Save links on a live document; Documents → Drafts shows "Not published" with Publish.
