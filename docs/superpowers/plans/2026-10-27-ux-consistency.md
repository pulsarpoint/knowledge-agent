# UX Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every admin UI page onto the same patterns: headers and back links, confirmed removals, one leave guard,
toasts, consistent words, one row menu, sheets for create/edit, DataTable lists and routed sub-pages.

**Architecture:** Shared components in `services/admin-ui/app/components/` (BackLink, ConfirmRemove, LeaveGuard with a
sheet guard, RowMenu, DataTable extensions, SectionLinks with help) and page-by-page adoption. UI only, except where a
list needs server-side paging that an API already supports (no new backend behaviour unless a task says so).

**Tech Stack:** React Router 7, TypeScript, shadcn/ui, TanStack Table, sonner, vitest.

**Spec:** `docs/superpowers/specs/2026-10-27-ux-consistency-design.md` — the audit; each task implements the named
sections' **Standard** paragraphs, and the user rulings at the top of the spec override the audit.

## Global Constraints

- Words: documents go live with **Publish** (rename Promote → Publish); inventory drafts with **Confirm**; **Chat** is a
  conversation with an agent, **run** one execution; **Delete** destroys a record, **Remove** drops a link or
  membership, **Discard** drops a draft or unsaved edits; **Code project** everywhere; **Enabled / Disabled**;
  create buttons read **New <thing>**; leave dialog **Stay / Leave**.
- Removal dialogs: title `Delete “<name>”?` / `Remove …?` / `Discard …?`, action button destructive, wording as above.
- Success → `toast.success`; errors needing reading → inline destructive `Alert` near the form/list.
- Reuse and extend shared components; never duplicate a pattern per page. Delete replaced components
  (`UnsavedDocumentChanges`, page-local pagers, page-local confirm dialogs) completely.
- Old URLs (`?view=`, `?tab=`) redirect to the new sub-pages, as `ServiceIndex` does.
- Work only in `.claude/worktrees/ux-consistency` (branch `feat/ux-consistency`). Never `make`, never touch `.env`/
  `.local`, never `git stash` in any form, never touch the running Docker stack, never kill processes you did not start,
  never spawn subagents. `node_modules` is a symlink: never commit it, never `pnpm install`.
- Tests: update tests for changed labels and flows; add tests for new shared components and for each convention a page
  adopts (a removal asks first; a dirty form asks Stay/Leave; a success shows a toast; a row menu holds the actions; a
  sub-page route and its redirect). Write failing tests first where behaviour changes.
- Format `pnpm format`; commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; never `git add`
  `.superpowers` or `node_modules`.
- Suite: `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm format:check` (documentSaves,
  skills, codeProjectPage, schedules are slow under load: rerun alone). Backend only if touched:
  `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in worktrees,
  test_deploy docs command, test_environments rollback).
- `docs/user-guide.md`: update names, buttons and page paths that change.

## Review Focus

1. Every removal in the app asks first, with the right word (Delete / Remove / Discard) and a red button — including the
   four that did not ask (spec §3) (Task 1).
2. A dirty form or sheet asks Stay / Leave on navigation and on close; a clean one never asks (Task 1).
3. Documents say Publish everywhere they go live; inventory says Confirm; no "Promote" left in the UI (Task 2).
4. Row menus keep each row's one primary quick action inline and every other action in the menu (Task 3).
5. Every old `?view=` / `?tab=` link still lands on the same content after the sub-page move (Task 5).

---

### Task 1: Headers, removals, leave guard (spec §1, §3, §7)

**Files:** `components/AdminLayout.tsx` (title by section prefix; sidebar highlight; label "Code projects"), new
`components/BackLink.tsx`, `components/ConfirmRemove.tsx` (moved from its feature folder; `children`, `busy`, `error`),
`components/LeaveGuard.tsx` (`samePage` predicate) + `useSheetGuard(dirty)`, delete `UnsavedDocumentChanges`; adopt on
every page the spec lists for §1, §3, §7; tests.

- [ ] Failing tests (Review Focus 1, 2; heading/sidebar on detail pages) → implement → UI suite → commit
  `fix(ui): one page header and back link, every removal confirmed, one leave guard`.

### Task 2: Toasts and words (spec §9, §10 with the user rulings)

**Files:** replace success notices with `toast.success` on the pages §9 lists (errors stay inline Alerts); rename per
Global Constraints (Promote → Publish, session → chat where user-facing, "Projects" → "Code projects", Enabled/Disabled,
New <thing>, prompt version "Inactive version"); tests; user guide.

- [ ] Failing tests (Review Focus 3; a toast after a save on a sample of pages) → implement → UI suite → commit
  `fix(ui): success toasts everywhere; one word for each thing`.

### Task 3: One row menu (spec §2)

**Files:** new `components/RowMenu.tsx` (lifted from `SkillActions` in `features/agents/SkillsView.tsx`; skills then
uses it), adopt on every list §2 lists; keep at most one inline primary action per row; tests.

- [ ] Failing tests (Review Focus 4) → implement → UI suite → commit `fix(ui): one row actions menu on every list`.

### Task 4: Create and edit in sheets; DataTable lists (spec §6, §4)

**Files:** §6: create/edit of small records in a right Sheet with Save / Cancel (agents wizard, MCP servers, models,
environments, servers labels, output schemas header Save); §4: extend `components/DataTable.tsx` (sortHeader helper,
search toolbar slot, Skeleton loading, Empty), adopt on every list §4 lists, server-side `total` paging where the API
has it, drop "Load more" and page-local pagers; tests.

- [ ] Failing tests → implement → UI suite (+ backend if an API changes) → commit
  `fix(ui): create and edit in sheets; every list is a DataTable`.

### Task 5: Sub-pages instead of query switches (spec §5)

**Files:** `components/SectionLinks.tsx` takes an optional `help` per section and renders the line; convert
`/servers/:id` (overview|documents), `/environments/:id` (workspace|documents), `/documents` (+ `/documents/drafts`),
`/events` (parent with SectionLinks), `/documents/:id` (History, Drafts, Connections, Agent runs as sub-pages; Write/
Preview stays the in-page editor toggle), environment Details card → link, graph explorer on its own route or Sheet;
help lines on every tabbed page; redirects for old links; tests; user guide.

- [ ] Failing tests (Review Focus 5) → implement → UI suite → commit `fix(ui): routed sub-pages with descriptions instead of query switches`.
