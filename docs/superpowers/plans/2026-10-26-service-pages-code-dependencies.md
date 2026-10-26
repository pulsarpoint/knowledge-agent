# Service Pages and Code Project Dependencies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Code projects can depend on code projects (libraries), with derived used-by; the service page becomes routed
sub-pages with one tab per relation; the code project page gets a Dependencies sub-page.

**Architecture:** One new table and its store/API/agent-tool support (Task 1); the service page restructure with the
side-sheet form removed (Task 2); the code project Dependencies sub-page and the map edges (Task 3).

**Tech Stack:** PostgreSQL 17; Python 3.14 / FastAPI / psycopg 3 / pytest; React Router 7 / TypeScript / shadcn/ui /
vitest.

**Spec:** `docs/superpowers/specs/2026-10-26-service-pages-code-dependencies-design.md`

## Global Constraints

- **Simple code;** rules pure; the inventory store is the only writer.
- Migration `0143_code_project_dependencies`; drafts like the other inventory tables (status draft/confirmed).
- UI follows the user's preferences: routed sub-pages under a shared header (reuse `SectionLinks`, `LeaveGuard` from
  the agent / code project pages), record pickers in right-side Sheets with search, removals confirmed with an
  AlertDialog, Draft badges with Confirm / Discard.
- Exact texts: duplicate dependency "This code project already depends on <name>."; self "A code project cannot depend
  on itself."
- Work only in `.claude/worktrees/service-pages` (branch `feature/service-pages`). Never `make`, never touch
  `.env`/`.local`, never `git stash` in any form, never touch the running Docker stack, never kill processes you did
  not start, never spawn subagents. UI: `node_modules` is a symlink (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees incl. test_code_processing and test_code_graph_sdk/config, test_deploy docs command, test_environments
  rollback; run to completion, ~8 min); `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit &&
  pnpm format:check` (documentSaves and skills are slow: rerun alone).

## Review Focus

1. "Used by" on a service adds `depends_on` from the other service to this one (not the reverse) (Task 2).
2. "Parts" adds `part_of` from the chosen service to this one (Task 2).
3. A library's Used by lists the dependent code projects and the services built from them, nothing else (Task 1, 3).
4. Leaving Edit with unsaved changes asks Leave / Stay (Task 2).
5. An agent-drafted code dependency shows as a draft and can be confirmed / discarded (Task 1, 3).

---

### Task 1: Code project dependencies (backend)

**Files:** `db/migrations/0143_code_project_dependencies.{up,down}.sql` + migration test; `inventory/rules.py`,
`inventory/store.py` (create / update / delete / confirm / discard for the new kind; reach rules: nothing else),
`inventory/api.py` or `code_projects/api.py` (`GET /api/code-projects/{id}/dependencies` with derived used-by services;
create; delete; confirm/discard via the existing `/inventory/{kind}/{id}/confirm|discard` with kind
`code_dependency`), `capabilities/inventory_tools.py` (`save_relation` kind `code_depends_on`; remove; descriptions),
`capabilities/inventory.py` (`get_resource` for a code project: depends_on and used_by), `servers/inventory_map.py`
(services view: code-project nodes with depends-on edges; the captured `map_models_expected.json` must stay unchanged —
use a new fixture), tool golden + matrix, tests, `docs/agents.md`, `docs/migrations.md`.

- [ ] Failing tests (Review Focus 3, 5; duplicates; self) → implement → full backend suite → commit
  `feat(inventory): code projects can depend on code projects`.

### Task 2: The service page as sub-pages

**Files:** routes (`services/:serviceId` layout + children `view` (index), `edit`, `code`, `depends-on`, `used-by`,
`part-of`, `parts`, `connections`, `documents`; `services/new`), `features/servers/ServicePage.tsx` → a layout + one
file per sub-page (reuse ServicePanel pieces where they fit; Connections from the panel's connections part),
`ServicesView.tsx` (row → view; Edit → edit; New service → /services/new), remove `ServiceForm.tsx` /
`ServiceDependencyFields.tsx` and what only they used, redirects from old links, in-app links, tests (Review Focus 1,
2, 4; each tab; new service; redirects), `docs/user-guide.md`.

- [ ] Failing tests → implement → UI suite (+ backend if touched) → commit `feat(services): the service page becomes sub-pages with one tab per relation`.

### Task 3: Code project Dependencies sub-page

**Files:** routes (`code-projects/:codeProjectId/dependencies`), a `CodeProjectDependencies.tsx` sub-page (Depends on:
add via a Sheet with search, remove with confirmation, description, Draft badge + Confirm/Discard; Used by: code
projects and services, linked), the service page's Code tab libraries (from the same API), tests, `docs/user-guide.md`.

- [ ] Failing tests (Review Focus 3, 5 in the UI) → implement → UI suite → commit `feat(code-projects): a Dependencies sub-page`.
