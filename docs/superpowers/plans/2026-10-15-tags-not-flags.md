# Tags Instead of Special Columns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `services.operations_notes` and `documents.inventory`; the inventory document is the document tagged
`inventory`; documents and drafts filter by tag.

**Architecture:** Migration 0109 converts the flag to a tag and drops both columns, the inventory triggers and the mark
route. Readers use `'inventory' = ANY(tags)`; discovery and `propose_service` drop the notes; the UI removes the notes
section and the Mark/Unmark controls, pre-fills the tag on New inventory document, and adds tag filters with clickable
tag badges.

**Tech Stack:** PostgreSQL 17 migrations, Python 3.14 / FastAPI / psycopg 3 / pytest; React Router 7 / TypeScript /
vitest.

**Spec:** `docs/superpowers/specs/2026-10-15-tags-not-flags-design.md`.

## Global Constraints

- Work only in `.claude/worktrees/tags-not-flags` (branch `feature/tags-not-flags`). Never `make`, never touch
  `.env`/`.local`, never `git stash`, never touch the running Docker stack, never kill processes you did not start.
  `services/admin-ui/node_modules` is a symlink — never commit it, never `pnpm install`.
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5 (1M
  context) <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims; operations notes are not converted; migrations correct up and down.
- The tag is exactly `inventory` (lowercase). Tag filter: `?tag=<tag>`, exact, case-insensitive, one tag.
- Discovery never changes an existing document.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm exec react-router routes && pnpm format:check` (rerun timing-flaky files alone).

## Review Focus

1. Nothing still references `operations_notes` or `documents.inventory` / the inventory triggers / the mark route
   (backend, tools.json, UI, tests, docs) except migrations before 0109.
2. With two live documents tagged `inventory` built for one resource, the page and `get_resource` pick the most recently
   updated one consistently and list the other.
3. A discovery proposal never adds the tag to, or drafts, an existing document.

---

### Task 1: Migration 0109 and backend (documents, services, discovery, agents)

**Files:** `db/migrations/0109_tags_not_flags.up.sql`/`.down.sql`; `documents/store.py`, `documents/api.py`,
`documents/drafts.py` (inventory = tag; resource documents; `?tag=` on both lists; remove mark route; New inventory
draft = tag), `servers/services.py`, `servers/service_api.py`, `servers/discovery.py` (accept_service, accept_document
tag rule), `capabilities/discovery.py`, `capabilities/inventory.py` (`get_resource` tags only), `capabilities/document_tools.py`,
`agents/wizard.py`, `knowledge_agents/config.py`, `knowledge_agents/ephemeral.py`, `sessions/start.py` (new-document
inventory start carries the tag), `tools/tools.json` / `tools/texts.py` (propose_service schema and texts), docs
(`documentation.md`, `agents.md`, `database.md`, `migrations.md`, `user-guide.md` backend parts); tests that use the
flag, the mark route or operations_notes (grep) plus new `tests/test_tags_not_flags.py`.

- [ ] Failing tests per the spec's Testing section (backend parts) and Review Focus 1–3; run; implement; focused files;
  full backend suite; commit `feat: inventory is a tag; services lose operations notes; documents filter by tag`.

### Task 2: UI

**Files:** `services/admin-ui/app/features/servers/ServiceDetails.tsx`, `ServiceForm.tsx` (notes removed),
`features/servers/*` types; `features/documents/DocumentInventory.tsx` (Mark/Unmark removed; badge from tag),
`DocumentEditorView.tsx` (New inventory document pre-fills the tag), `DocumentsView.tsx` (tag filter on documents and
drafts; "Inventory documents only" removed; tag badges link to `?tag=`), `EntryDocuments.tsx` (tag badges link),
`api.ts`, `drafts.ts`, `features/sessions/agentSettings.ts` (inventory start carries the tag); UI tests.

- [ ] Failing UI tests per the spec's Testing section (UI parts); run; implement; all UI checks; commit
  `feat(admin-ui): tags instead of the inventory flag; tag filters; no operations notes`.
