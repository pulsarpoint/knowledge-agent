# Jira Automatic Assignee Handler and Hand-back — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Saving the first Jira user mapping creates one shared "Jira assignee agents" handler; a handler with Reply in
Jira can give the ticket back (previous assignee, else reporter) after the agent's comment is posted.

**Architecture:** Migration 0119 adds `ka.event_handlers.give_back` and hand-back columns on `ka.notifications`. The
notification creator (`sessions/text.py`) decides the hand-back target from the event; the reply worker
(`events/jira_replies.py`) assigns after a successful comment, with the comment's retry schedule. The mapping API creates
the handler in the same transaction when none exists. The mock gains the two Jira endpoints.

**Tech Stack:** PostgreSQL 17, Python 3.14 / FastAPI / psycopg 3 / httpx / pytest; React Router 7 / TypeScript / vitest;
the Jira mock `deploy/jira-mock/server.py`.

**Spec:** `docs/superpowers/specs/2026-10-20-jira-handback-design.md`.

## Global Constraints

- **Simple code, no unnecessary abstractions; decisions testable in isolation** (the hand-back target is a pure
  function of the event payload).
- Work only in `.claude/worktrees/jira-handback` (branch `feature/jira-handback`). Never `make`, never touch
  `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack (including the running
  jira-mock container), never kill processes you did not start. UI checks:
  `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once (never commit it, never
  `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format` (the mock under `deploy/` is excluded from ruff). Commits
  end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims; migrations correct up and down. Next migration: **0119**.
- Jira Data Center: users by `key`; assigning uses the login `name` (`PUT /rest/api/2/issue/<key>/assignee`
  `{"name": …}`), looked up with `GET /rest/api/2/user?key=<key>`.
- Exact texts: handler name `Jira assignee agents`; comment note `Assigned back to <display name>.` (above the agent
  footer); Users and agents line `Handled by: <handler name> (enabled|disabled)` / `No handler runs these agents.`;
  checkbox **Give the ticket back after replying**.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests alone).

## Review Focus

1. The ticket is never reassigned unless the comment was posted; the comment is never posted twice when the
   assignment is retried.
2. The automatic handler is created once per project (also under two mappings saved at the same time) and never
   overwritten or deleted by mapping changes.
3. The hand-back's own update event starts no run (the person is not mapped) and causes no loop.
4. Existing handlers keep their behaviour (`give_back` off).
5. `give_back` is refused without `reply_to_jira`.

---

### Task 1: Jira mock and client — user lookup and assign

**Files:** `deploy/jira-mock/server.py` (`GET /rest/api/2/user?key=`, `PUT /rest/api/2/issue/<key>/assignee` with
`{"name"}`, null unassigns, unknown → 404/400 like Jira, bumps `updated`); `services/admin-api/src/knowledge_admin/jira/client.py`
(`user_by_key(key) -> dict`, `assign(issue_key, name: str | None)`); tests `tests/test_jira_mock_users.py`,
`tests/test_jira_client_assign.py` (httpx.MockTransport).

- [ ] Step 1: failing tests (mock endpoints; client paths, bodies, error mapping like `add_comment`). Step 2: fail.
  Step 3: implement. Step 4: focused + test_jira*.py. Step 5: commit `feat(jira): user lookup and assign in the client
  and the mock`.

### Task 2: Hand-back

**Files:** `db/migrations/0119_jira_give_back.up.sql`/`.down.sql` (`ka.event_handlers.give_back boolean NOT NULL DEFAULT
false` with `CHECK (NOT give_back OR reply_to_jira)`; `ka.notifications.give_back_key text`, `give_back_name text`,
`give_back_status text CHECK (give_back_status IN ('pending','done','failed'))`, `give_back_error text`);
`jira/events.py` (`payload.reporter_id` next to `reporter`); `events/handlers_api.py` (accept/return `give_back`;
validation); a pure `give_back_target(payload) -> (key, name) | None` (previous assignee from
`changes.assignee.from_id/from` when the assignee changed, else `reporter_id/reporter`); `sessions/text.py` (store the
target and `give_back_status='pending'` on the notification when the handler has `give_back`); `events/jira_replies.py`
(the comment gets the note when a target is stored; after a successful post: `user_by_key` → `assign`; set
`give_back_status` `done`/`failed`+error; retry a failed assignment on the same schedule without re-posting — the row
remembers `jira_comment_id`); tests (pure target; worker paths; API validation; end to end with the mock: map `ops-bot`,
create a ticket assigned to Ops Bot reported by Ana → pull → run → comment with the note → ticket assigned to Ana → next
pull records the update and starts nothing).

- [ ] Step 1: failing tests. Step 2: fail. Step 3: implement. Step 4: focused, test_jira*.py, test_event*.py, full
  backend suite. Step 5: commit `feat(jira): give the ticket back after the agent's reply`.

### Task 3: The automatic handler

**Files:** `jira/api.py` (on PUT of a user mapping: if the project has no handler with `agent_from='assignee'`, create
`Jira assignee agents` in the same transaction — fields per the spec, default Jinja prompt, created_by the person,
audit `event_handler.created` `{automatic: true}`; guard against two concurrent first saves with a project-level advisory
lock or a unique partial index — choose the simpler and say why; the mapping list response adds
`handler: {id, name, enabled} | null`; `POST /api/jira/user-agents/handler` creates it on demand (409 if one exists));
the default prompt as one module constant; tests `tests/test_jira_auto_handler.py`.

- [ ] Step 1: failing tests — created once with the first mapping; later mappings reuse; not created when one exists;
  mapping deletes leave it; concurrent first saves create one; the list's `handler`; the create endpoint. Step 2: fail.
  Step 3: implement. Step 4: focused, full backend suite. Step 5: commit `feat(jira): the first mapping creates the
  assignee handler`.

### Task 4: UI and docs

**Files:** `services/admin-ui/app/features/events/HandlerSheet.tsx` + `handlers.ts` (checkbox **Give the ticket back
after replying**, shown when Reply in Jira is on; default on for new handlers; saved as `give_back`);
`features/jira/JiraUserAgents.tsx` + `jira/api.ts` (`Handled by: … (enabled|disabled)` linking to the handler, or
`No handler runs these agents.` with **Create handler**); the reply status shown where Jira reply status is shown today
(hand-back done/failed); tests; docs `docs/jira.md`, `docs/user-guide.md`.

- [ ] Step 1: failing vitest tests. Step 2: fail. Step 3: implement. Step 4: vitest, tsc, format:check. Step 5: commit
  `feat(admin-ui): give back after replying; the assignee handler on Users and agents`.

### Task 5: Earlier runs on the same ticket

**Files:** `services/admin-api/src/knowledge_admin/sessions/rows.py` (or wherever the session detail is built: for a
session whose `event_id` is a Jira event, add `related_runs`: the other sessions whose event has the same `source='jira'`,
`source_id` and `payload->>'issue_id'`, newest first, at most 20: `{session_id, started_at, agent_id, agent_name,
status, event_type}`); the run page in `services/admin-ui/app/features/sessions/` (an **Earlier runs on <KEY>** list
under the event info, links to each run); tests. No migration (the link is `agent_sessions.event_id` → `ka.events`).

- [ ] Step 1: failing tests — two runs on one ticket list each other; a run on another ticket or another connection is
  not listed; a non-Jira or non-event session has no `related_runs`; UI renders the list with links. Step 2: fail.
  Step 3: implement. Step 4: focused, full backend suite, UI checks. Step 5: commit
  `feat(sessions): earlier runs on the same Jira ticket`.
