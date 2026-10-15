# Inventory Document Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each server, service and environment has at most one inventory document (`ka.documents.inventory`), shown
when the resource is opened; `ka.inventory_documents` is removed and every reader uses the documents' resource links.

**Architecture:** Migration 0107 adds the flag with a commit-time constraint trigger (one built-for
server/service/environment link; one inventory document per resource) and drops `ka.inventory_documents`. Backend
readers (resource documents, System map, entry sheet, `get_resource`, `get_document` access, discovery lookups) read
`document_resources`; a new route marks/unmarks; discovery creates an inventory draft only when the resource has none
and never touches an existing one. The UI renders the inventory document on resource pages, adds Mark/Unmark, Link a
document, a badge and a filter.

**Tech Stack:** PostgreSQL 17 migrations, Python 3.14 / FastAPI / psycopg 3 / pytest; React Router 7 / TypeScript /
vitest.

**Spec:** `docs/superpowers/specs/2026-10-15-inventory-document-design.md`.

## Global Constraints

- Work only in the worktree `.claude/worktrees/inventory-document` (branch `feature/inventory-document`). Never `make`,
  never touch `.env`/`.local`, never `git stash`, never touch the running Docker stack; never kill processes you did
  not start. pytest starts its own throwaway PostgreSQL.
- UI checks need `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once (git-ignored;
  never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5 (1M
  context) <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims, no carrying over of attachments; migrations correct up and down.
- Messages (exact): `<Type> <name> already has an inventory document.` (`<Type>` = Server | Service | Environment),
  `An inventory document must be built for one server, service or environment.`
- UI copy (exact): `No inventory document yet.`, `New inventory document`, `Other documents`, `Mark as inventory
  document`, `Unmark`, `Inventory` (badge), `Inventory documents only` (filter), `Link a document`.
- Discovery never changes an existing inventory document — not directly, not through a draft of it.
- An inventory document's built-for link is permanent (built-for links can never be removed).
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm exec react-router routes && pnpm format:check` (rerun timing-flaky files alone).

## Review Focus

1. Two concurrent creations of an inventory document for one resource (two discovery accepts, or New + Mark): exactly
   one succeeds; the other gets the "already has an inventory document" message, never two flagged documents.
2. A discovery proposal for a resource that has an inventory document (live, or as a brand-new draft) must leave that
   document, its content and its drafts untouched.
3. `get_document` must not read a document linked only to resources the run was not given.
4. Unmarking keeps the built-for link; removing the built-for link of any document stays refused.
5. Nothing still reads or writes `ka.inventory_documents` (grep backend, UI, tests, docs).

---

### Task 1: Migration 0107, readers, mark/unmark route

**Files:**
- Create: `db/migrations/0107_inventory_document.up.sql`, `.down.sql`; `services/admin-api/tests/test_inventory_document.py`
- Modify: `services/admin-api/src/knowledge_admin/documents/store.py` (resource documents query; create with
  `inventory`; promote/publish keep the flag; drafts of a live document report their document's flag),
  `documents/api.py` (`inventory` in document JSON and list; filter `inventory=true`; `PUT
  /api/documentation/documents/{id}/inventory {inventory, revision}`), `documents/drafts.py` (`inventory` on a
  person's new-document draft create), `servers/inventory_links_api.py` (remove `/inventory/documents`; entry documents
  from links; link/unlink through `store.save_links`), `servers/inventory_map.py` (documents from links), the resource
  routes that feed the resource pages (a `GET /api/inventory/<type>/<id>/documents` → `{inventory_document, documents}`
  or the existing entry endpoints — reuse what the pages call), tests touching `inventory_documents`
  (`test_inventory_documents_api.py` → rewrite or delete, `test_inventory_links_migration.py`, `test_scoped_reads.py`,
  `discovery_world.py` helpers), `docs/documentation.md`, `docs/database.md`, `docs/migrations.md`
- Interfaces produced: `documents.inventory`; resource documents JSON `{inventory_document: {id, title, markdown,
  version} | null, documents: [{id, title, connection}]}`; the mark route; the two exact messages (409).

- [ ] Step 1: failing tests (migration round trip; constraint: flag without one built-for server/service/environment →
  refused; second inventory document for a resource (live or brand-new draft) → refused, including two concurrent
  transactions (Review Focus 1); draft of a live inventory document fine; unmark keeps the link (Review Focus 4);
  resource documents per type; System map and entry sheet documents from links; link/unlink; Documents list filter and
  `inventory` field; mark route errors).
- [ ] Step 2: run — fail. Step 3: implement. Step 4: focused files, then full backend suite.
- [ ] Step 5: commit `feat(documents): one inventory document per resource; inventory_documents removed`

### Task 2: Discovery and agent tools

**Files:**
- Modify: `servers/discovery.py` (accept: inventory brand-new draft only when the resource has no inventory document;
  otherwise an unflagged brand-new draft; never a draft of the inventory document; no attachment on publish),
  `documents/store.py` (remove the publish-attach code), `capabilities/discovery.py` (existing-document lookups from
  links/inventory flag), `capabilities/inventory.py` (`get_resource` returns `inventory_document {id, title}` and
  `documents [{id, title}]` from links), `capabilities/document_tools.py` (read access: documents linked to resources
  the run was given — replaces attachments), `docs/agents.md`
- Tests: `test_discovery_review.py`, `test_resource_proposals.py`, `test_get_resource.py`, `test_document_tools*.py` /
  scoped reads, new cases in `test_inventory_document.py`.

- [ ] Step 1: failing tests (proposal for a resource without inventory document → inventory brand-new draft; with one
  (live or brand-new draft) → unflagged brand-new draft and the existing one, its content and drafts unchanged (Review
  Focus 2); publish keeps the flag; `get_resource` fields; `get_document` reads a linked document of a given resource
  and refuses one linked only to a resource not given (Review Focus 3); grep: no `inventory_documents` anywhere
  (Review Focus 5)).
- [ ] Step 2: run — fail. Step 3: implement. Step 4: focused files, then full backend suite.
- [ ] Step 5: commit `feat(discovery): proposals never change an existing inventory document; agents read documents through links`

### Task 3: UI

**Files:**
- Modify: `services/admin-ui/app/features/servers/EntryDocuments.tsx` (inventory document rendered + Open; empty state
  with New inventory document; Other documents), `ServerPage.tsx`, `ServicePage.tsx`, `EnvironmentPage.tsx`,
  `EntrySheet.tsx` (documents from links; Link a document; remove linked link), `inventoryMap.ts` (API calls),
  `features/documents/*` (Inventory badge on the document page naming the resource; Mark as inventory document /
  Unmark in Linked resources when allowed; New inventory document route parameter → Documents → New built for the
  resource with `inventory`; Documents list badge and `Inventory documents only` filter)
- Tests: `entrySheet.test.tsx`, `serverPage.test.tsx`, `resourceChats.test.tsx` (fixtures), documents tests, new
  `inventoryDocument.test.tsx`.

- [ ] Step 1: failing UI tests (resource page renders the inventory document and Open; empty state and New inventory
  document opens New built for the resource with the flag; Other documents list; Mark/Unmark visible only when allowed
  and errors shown; entry sheet Link a document adds a linked link; Documents list badge and filter).
- [ ] Step 2: run — fail. Step 3: implement. Step 4: all UI checks.
- [ ] Step 5: commit `feat(admin-ui): resource pages show their inventory document; Mark/Unmark; Link a document`
