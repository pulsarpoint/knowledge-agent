# Document Tools Without Proposals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents create and update documents only as drafts in the real documents (`new_document`, `update_document`,
`edit_document`, `list_documents`, `get_document`); the Content editor role, the editable pick and every proposal piece
are removed.

**Architecture:** The draft flow already exists (`documents/store.py` create_draft / update_draft / promote_draft,
`capabilities/document_tools.py`). New tools sit on it; access checks become "usable = readable"; migration 0130 drops
`ka.discovery_proposals`; the UI loses the proposal pages and the editable picks.

**Tech Stack:** PostgreSQL 17; Python 3.14 / FastAPI / psycopg 3 / pydantic / pytest; React Router 7 / TypeScript /
vitest.

**Spec:** `docs/superpowers/specs/2026-10-23-document-drafts-tools-design.md`

## Global Constraints

- **Simple code, no unnecessary abstractions;** checks are pure functions over the access object; the store stays the
  writer of documents and drafts.
- `read_document` stays (it reads knowledge-base files from `search_documents` results, with citations) — its and
  `get_document`'s descriptions say the difference.
- Exact texts:
  - duplicate title: "A document titled \"<title>\" is already linked to this <type> (<id>); update it with
    update_document instead."
  - every write reply ends: "This is a draft: a person publishes it on the document's page."
- No `content_editor`, `is_content_editor`, document `editable`, `propose_document`, `discovery_proposals` or
  proposals routes left in `services/admin-api/src`, `services/admin-ui/app`, `docs/user-guide.md`, `docs/agents.md`
  (grep) — migrations and old specs/plans excepted. Stored access objects with `content_editor` / `editable` load.
- Subagents and experts keep the read tools only.
- Migration `0130_proposals_gone` (up: drop the table and what only served it; seeded "Server discovery" prompt gets a
  new version only where it equals the 0129 text; down: recreate the table empty in its 0129 shape).
- Work only in `.claude/worktrees/document-drafts` (branch `feature/document-drafts-tools`). Never `make`, never touch
  `.env`/`.local`, never `git stash` in any form, never touch the running Docker stack, never kill processes you did not
  start, never spawn subagents. UI: `node_modules` is a symlink (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees incl. test_code_processing, test_deploy docs command, test_environments rollback);
  `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm format:check` (documentSaves and
  skills are slow: rerun alone).

## Review Focus

1. An environment chat calls `new_document` for a service in its environment, then `edit_document` twice → one draft,
   linked to the service, visible on the service's Documents tab as a draft (Task 1).
2. `update_document(id, …, resource=server)` → the link appears only after the draft is published (Task 1).
3. A document page's Ask agent run (its document in access, no editable flag) still updates the document as a draft
   (Task 1).
4. A run without the document in access and not linked to a resource in access → refused, with the documents it may use
   (Task 1).
5. Old agents whose stored access has `roles.content_editor` and `editable` load and run (Task 1).

---

### Task 1: The document tools and access without the role

**Files:**
- Modify: `capabilities/document_tools.py` (new `list_documents`, `new_document`; `save_document` → `update_document`
  with `resource`; `edit_document` unchanged in behaviour; checks: usable = readable; replies per Global Constraints),
  `documents/store.py` / `documents/drafts.py` (a draft carries added links; publish adds them — read
  `promote_draft` and `save_links` first), `access.py` (remove `is_content_editor`, `can_write_document`,
  `can_create_document` uses of the role, `editable`; `ROLES` empty or removed; unknown keys ignored),
  `sessions/start.py`, `sessions/run_access.py`, `sessions/review.py`, `knowledge_agents/config.py`, `experts.py`,
  `subagents.py`, `agents/profiles_api.py`, `agents/wizard.py`, `tools/tools.json` (descriptions with formats and an
  example), `tools/texts.py`, `tools/README.md`, schema golden, `docs/agents.md`
- Modify (UI): `features/agents/AgentProfileSettings.tsx` (no Content editor switch), `features/agents/api.ts`,
  `wizard.ts`, `features/sessions/AgentSettingsSheet.tsx` (no editable picks), `DebugPanel.tsx`, their tests
- Test: `tests/test_document_tools.py` (or the existing document tool tests), `tests/test_tool_access_matrix.py`,
  `tests/test_access.py`, run-start / experts / profiles tests, UI tests

- [ ] Step 1: failing tests for Review Focus 1–5, `list_documents` per resource with draft flags, duplicate title text,
  grep test for removed names.
- [ ] Step 2: run; fail. Step 3: implement. Step 4: full backend + UI suites.
- [ ] Step 5: commit `feat(documents): agents draft documents with new_document and update_document; no content editor role`.

### Task 2: Proposals go

**Files:**
- Create: `db/migrations/0130_proposals_gone.up.sql` / `.down.sql`, `tests/test_proposals_gone_migration.py`
- Modify: `capabilities/discovery.py` (remove `propose_document` and what only it used; if the module is then empty,
  delete it and its catalog import), `servers/discovery.py` (remove the proposal API; keep what discovery runs still
  need, if anything), `tools/tools.json`, schema golden, matrix, `features/servers/ProposalsPage.tsx`,
  `features/sessions/ProposalSheet.tsx`, `features/servers/discovery.ts` (delete or trim), `routes.ts` and
  `routes/resourceProposals.tsx`, the Proposals links on `EnvironmentPage.tsx` / `ServerPage.tsx` / `ServicePage.tsx`,
  `features/sessions/ProposeWizard.tsx` → **Discover…** (starts the run from the chosen prompt; proposal-only steps
  gone), `ResourceChats.tsx` / `AgentChat.tsx` / `sessions/api.ts` proposal bits (the `proposals` run tag), docs
  (`docs/user-guide.md`, `docs/agents.md`, `deploy/README.md`, `docs/migrations.md`)
- Test: migration test (prompt rewritten only when unchanged; down recreates the table), backend proposal tests
  deleted or turned into "gone" assertions, UI wizard and page tests

- [ ] Step 1: failing tests (migration; grep test; Discover… wizard starts a run; pages have no Proposals link).
- [ ] Step 2: run; fail. Step 3: remove. Step 4: full backend + UI suites.
- [ ] Step 5: commit `refactor: proposals are gone — documents and inventory are drafted`.

### Task 3: Document drafts in the review count

**Files:**
- Modify: the environment model or a small endpoint for the count (`inventory/model.py` drafts count gains document
  drafts linked to the environment, its servers and services — or a separate `document_drafts` number; pick the
  simplest), `EnvironmentPage.tsx` (header "N drafts to review": links to the workspace Drafts filter, or to the
  Documents tab when only document drafts wait), resource Documents tabs (`EntryDocuments.tsx`: draft new documents
  listed with "Draft — not published" — verify, add if missing), `docs/user-guide.md`
- Test: backend count test; UI header and Documents tab tests

- [ ] Step 1: failing tests. Step 2: run; fail. Step 3: implement. Step 4: full suites.
- [ ] Step 5: commit `feat(documents): document drafts in the review count and the Documents tabs`.
