# Every Save Is a Draft Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** People never write live document content directly: Save always writes a draft, and only Publish (a
brand-new draft) and Promote (a draft of a live document) make content live; discovery accepts become drafts too.

**Architecture:** Backend removes the direct live-write routes, adds a links-only route and lets
`GET /documents/{id}` open a brand-new draft (it keeps its id when published). Discovery accept calls
`store.create_draft` and records `discovery_proposals.draft_id` (migration 0105); publishing a discovery draft attaches
it to its inventory entry. The UI gets one Save, a brand-new draft page at `/documents/{id}`, the "Editing your draft"
flow on live documents, a Drafts badge and Save links.

**Tech Stack:** PostgreSQL 17 migrations, Python 3.14 / FastAPI / psycopg 3 / pytest; React Router 7 / TypeScript /
vitest.

**Spec:** `docs/superpowers/specs/2026-10-14-drafts-only-saves-design.md` (and the model it builds on:
`docs/superpowers/specs/2026-10-14-document-resources-design.md`).

## Global Constraints

- Work only in the worktree `.claude/worktrees/drafts-only` (branch `feature/drafts-only`). Never `make`, never touch
  `.env`/`.local`, never `git stash`, never touch the running Docker stack; pytest starts its own throwaway PostgreSQL
  (its fixture occasionally fails with IndexError at `tests/conftest.py:39` — rerun).
- UI checks need `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once
  (git-ignored; never commit it, never `pnpm install`).
- Format before committing: `uv run --frozen ruff format <files>` (services/admin-api), `pnpm format`
  (services/admin-ui).
- Commits end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`; never `git add`
  `.superpowers` or `node_modules`.
- API paths are under `/api/documentation`. Existing texts stay: built-for refusal `This document was built for {type}
  {name}; that link can't be removed.`, name clash `A document or draft named {slug} already exists in this folder.
  Choose another name.`, promote warning `Promote and delete {n} other draft(s)?`.
- New copy (exact): banner on a brand-new draft `Draft — not published yet`; banner while editing your draft of a live
  document `Editing your draft (created {time}, from version {n}). Save updates it; Promote makes it live.`; list badge
  `Not published`; buttons `Save`, `Publish`, `Discard`, `Save links`; tab `Drafts {n}` (badge with the count).
- Data in the stack is test data (can be wiped); migrations must still be correct up and down.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback; SSH real-server tests can error under load — rerun
  alone); `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm exec react-router routes &&
  pnpm format:check` (long UI tests can time out under load — rerun those files alone).

## Review Focus

1. A person's second Save on a live document must update the draft created by the first Save, not create another
   (the editor must keep the draft id and revision after the first Save).
2. Save links on a page loaded before an agent changed the document's links: stale `revision` → 409, nothing written.
3. A discovery new-document draft that is discarded must leave no inventory attachment; one that is published must
   attach exactly once (republishing is impossible, but a second accepted proposal pointing at the same draft must not
   double-attach).
4. Opening `/documents/{id}` for a draft of a live document must not show it as a document (404 from the API; the UI
   goes to the draft page).
5. Nothing in the UI or API can still write live content directly (grep for `saveDocument`, `POST …/documents`,
   `PUT …/documents/{id}` without `/links`).

---

### Task 1: Backend — remove direct live writes, links route, brand-new draft reads

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/documents/api.py` (remove `POST /documents` and
  `PUT /documents/{id}`; add `PUT /documents/{id}/links`; `GET /documents/{id}` for brand-new drafts)
- Modify: `services/admin-api/src/knowledge_admin/documents/drafts.py` (`published` on draft rows; `PUT /drafts/{id}`
  may change a brand-new draft's `slug`/`folder`)
- Modify: `services/admin-api/src/knowledge_admin/documents/store.py` only if `update_draft` needs a name change path
- Modify: tests that create live documents through the removed routes (use `store.write_live` / a test helper, or
  drafts + promote, whichever the test is about)
- Modify: `docs/documentation.md` (routes, Save always drafts, links route)
- Test: `tests/test_documents.py`, `tests/test_document_resources_api.py`, `tests/test_document_drafts.py`,
  new `tests/test_drafts_only.py`

**Interfaces:**
- Produces:
  - `PUT /api/documentation/documents/{id}/links` body `{revision: int, code_project_ids, service_ids, server_ids,
    deployment_ids, environment_ids}` → `{id, revision, resource_links}`; works on a live document or a brand-new
    draft; 404 for a draft of a live document or another project's id; 409 stale revision (today's text); 409 built-for
    refusal; audit `document.links.updated` `{links_added, links_removed}`.
  - `GET /api/documentation/documents/{id}`: live document as today plus `is_draft: false, published: true`; a
    brand-new draft → `{id, slug, folder, title, description, purpose, audience, tags, markdown, revision, is_draft:
    true, published: false, origin ('agent'|'manual'), session_id, resource_links, versions: [], drafts: [],
    created_at, updated_at}`; a draft of a live document → 404 `Document not found`.
  - Draft rows (`GET /drafts`, `GET /documents/{id}/drafts`, `GET /drafts/{id}`) gain `published: bool` (false when
    `document_id` is null).
  - `PUT /drafts/{id}` accepts optional `slug` and `folder` for a brand-new draft (ignored/refused 400 `A draft of a
    published document keeps its document's name.` for a draft of a live document); name clash → 409 name text;
    `export_conflict` check as for a new document.
  - Removed: `POST /api/documentation/documents`, `PUT /api/documentation/documents/{id}`.

- [ ] **Step 1: Write failing tests** in `tests/test_drafts_only.py`:
  - `POST /api/documentation/documents` → 405; `PUT /api/documentation/documents/{id}` → 405.
  - links route: add a service then remove it → `resource_links` and `resource-history` reflect both with
    `removed_by`; unticking a built-for link → 409 built-for text, nothing written; stale revision → 409; on a
    brand-new draft → works; on a draft of a live document → 404.
  - `GET /documents/{id}` on a brand-new draft → `is_draft: true`, `published: false`, its markdown and links; on a
    draft of a live document → 404.
  - `PUT /drafts/{id}` with a new slug for a brand-new draft → renamed; to a taken name → 409 name text; with a slug on
    a draft of a live document → 400.
  - draft rows carry `published`.
- [ ] **Step 2: Run** `uv run --frozen pytest -q -p no:warnings tests/test_drafts_only.py` — expected failures.
- [ ] **Step 3: Implement.** Remove the two routes and their models' route use (keep `DocumentInput` only if other code
  needs it); add the links route calling `store.save_links` under the document's `FOR UPDATE` and the revision check;
  extend `get_document`; add `published`; extend `DraftUpdate` with optional `slug`/`folder` (validated like
  `DraftCreate`). Update every test that used the removed routes (grep `"/api/documentation/documents"` with `POST`
  /`PUT`) to create live documents through `store.write_live` or draft + promote.
- [ ] **Step 4: Run** the four test files above and then the full backend suite — only the known failures.
- [ ] **Step 5: Commit** `feat(documents): every save is a draft — links route, brand-new draft reads, no direct live writes`

---

### Task 2: Discovery accepts create drafts (backend + server page)

**Files:**
- Create: `db/migrations/0105_discovery_draft.up.sql`, `.down.sql`
- Modify: `services/admin-api/src/knowledge_admin/servers/discovery.py` (`accept_document` → `store.create_draft`;
  proposal `draft_id`; detail/list JSON carries `draft_id` and `draft_published`)
- Modify: `services/admin-api/src/knowledge_admin/documents/store.py` (`promote_draft` of a brand-new draft attaches it
  to the inventory entry of accepted proposals with `draft_id = draft` — `inventory_documents` insert, once)
- Modify: `services/admin-ui/app/features/servers/*` (accepted proposal links to its draft; copy "Waiting to be
  published" / "Waiting to be promoted"; "Open document" once live)
- Modify: `docs/documentation.md` / `docs/agents.md` discovery sections; `docs/migrations.md` (0105)
- Test: `tests/test_discovery_review.py`, `tests/test_discovery_migration.py` (0105 round trip), new cases in
  `tests/test_drafts_only.py`; UI `app/components/__tests__/discoveryRun.test.tsx` / `serverPage.test.tsx`

**Interfaces:**
- Consumes: Task 1 (`published` on draft rows; `GET /documents/{id}` for brand-new drafts).
- Produces: `discovery_proposals.draft_id uuid REFERENCES ka.documents(id) ON DELETE SET NULL`; proposal JSON
  `draft_id`, `draft_published` (true once the brand-new draft was published or the draft of a live document was
  promoted — i.e. the draft row is gone or no longer `is_draft`).

- [ ] **Step 1: Failing tests:** accepting a new-document proposal creates a brand-new draft (`origin='generated'`,
  `session_id` of the discovery run, built-for links: service, else server, `added_how='discovery'`), sets `draft_id`,
  creates no live document and no `inventory_documents` row; promoting (publishing) it attaches it to the proposal's
  inventory entry exactly once (Review Focus 3, incl. two accepted proposals pointing at one draft); discarding attaches
  nothing; accepting an update proposal creates a draft of the document (`base_version` = live version), live content
  unchanged; migration 0105 up/down round trip.
- [ ] **Step 2: Run** them — expected failures.
- [ ] **Step 3: Implement** backend; then the server page: an accepted document proposal links to
  `/documents/{draft_id}` (brand-new) or `/documents/drafts/{draft_id}` (draft of a live document) while it waits, with
  the copy above, and to the live document after.
- [ ] **Step 4: Run** the discovery test files, `tests/test_drafts_only.py`, the full backend suite, and the UI checks.
- [ ] **Step 5: Commit** `feat(discovery): accepting a document proposal creates a draft; publishing attaches it`

---

### Task 3: Document UI — one Save, brand-new draft page, editing your draft, Drafts badge, Save links

**Files:**
- Modify: `services/admin-ui/app/features/documents/api.ts` (remove `saveDocument`; add `saveDocumentLinks`;
  `DocumentDetail` with `is_draft`/`published`), `drafts.ts` (`published`; `updateDraft` with optional slug/folder)
- Modify: `DocumentEditorView.tsx` (one **Save**; live document: first Save creates your draft and switches the editor
  to it — keep its id and revision — later Saves update it; banner text; brand-new draft mode with Save/Publish/
  Discard, editable name, no Version history/Drafts tabs; `Drafts {n}` tab badge; remove "Save new version",
  "Create document" and the old editing-draft alert), `DocumentResourceSelection.tsx` / Linked resources (Save links
  via the links route; built-for lock unchanged), `DocumentsView.tsx` (Drafts tab: `Not published` badge with Open /
  Publish / Discard for brand-new drafts; document title + Open for drafts of live documents), `DraftPageView.tsx` /
  `routes/documentDraft.tsx` (a brand-new draft redirects to `/documents/{id}`), `routes/documentNew.tsx` (Save →
  navigate to `/documents/{id}`), `DraftEditForm.tsx` (button `Save`)
- Test: UI tests touching these screens (documents, documentDrafts, documentPageDrafts, documentNewAgent,
  documentAgentPane, documentLinks) + new `documentSaves.test.tsx`

**Interfaces:**
- Consumes: Task 1 API shapes; Task 2 proposal fields (only for links pointing at drafts).

- [ ] **Step 1: Failing UI tests:** New → Save calls `POST /drafts` and opens `/documents/{id}`; the brand-new draft
  page shows the banner, Save/Publish/Discard and no Version history/Drafts tabs; Publish calls promote and the page
  shows the live document; on a live document, Save creates a draft (banner with time and version), a second Save
  calls `PUT /drafts/{id}` with the new revision (Review Focus 1); the Drafts tab shows `Drafts 3`; Save links calls
  `PUT /documents/{id}/links` and a 409 shows its message under Linked resources; Documents → Drafts shows `Not
  published` with Publish; no element named "Save new version" or "Create document" exists (Review Focus 5).
- [ ] **Step 2: Run** them — expected failures.
- [ ] **Step 3: Implement**, then update the existing tests' fixtures and expectations to the new flow.
- [ ] **Step 4: Run** all UI checks — pass (rerun timing-flaky files alone).
- [ ] **Step 5: Commit** `feat(admin-ui): one Save that always drafts; Publish for new documents; Drafts badge; Save links`
