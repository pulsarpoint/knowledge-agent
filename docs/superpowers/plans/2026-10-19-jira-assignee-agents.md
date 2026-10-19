# Jira Assignee → Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Map Jira users to agents; Jira events say when the assignee has something new (`for_assignee`); a handler can
target "the agent mapped to the assignee"; the Jira mock can assign tickets and search users so the flow is testable.

**Architecture:** A table `ka.jira_user_agents` per Jira connection. The pull's pure payload code adds `assignee_id` and
`for_assignee`. Handlers gain `agent_from` (`fixed` | `assignee`); one pure function picks the agent for an event from
the mapping rows. Everything else (conditions, prompt, run start, reply to Jira, loop protection) is unchanged.

**Tech Stack:** PostgreSQL 17, Python 3.14 / FastAPI / psycopg 3 / httpx / pytest; React Router 7 / TypeScript / vitest;
the Jira mock `deploy/jira-mock/server.py` (Python standard library).

**Spec:** `docs/superpowers/specs/2026-10-19-jira-assignee-agents-design.md`.

## Global Constraints

- **Simple code, no unnecessary abstractions; decisions testable in isolation** (`for_assignee` and the agent choice are
  pure functions).
- Work only in `.claude/worktrees/jira-assignee-agents` (branch `feature/jira-assignee-agents`). Never `make`, never
  touch `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack (including the
  running jira-mock container), never kill processes you did not start. UI checks:
  `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once (never commit it, never
  `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims; migrations correct up and down. Next migrations: **0117**, **0118**.
- Jira is Data Center: users are identified by `key` (stable) and `name` (login); `displayName` is for showing only.
  Mapping and matching always use the key.
- Exact texts: `No agent is mapped to Jira user <display name> (<key>).`; `The ticket is not assigned.`; a disabled
  mapped agent uses the processor's existing disabled-agent text.
- Testing uses the Jira mock only (no real Jira).
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests alone).

## Review Focus

1. Our own posted replies never set `for_assignee` (the loop protection decides which comments are ours), while a
   person's comment on an assigned ticket does.
2. A ticket reassigned away from a mapped user (to someone unmapped or to nobody) starts no run for the old agent.
3. Two handlers matching the same event (one fixed, one assignee) behave as today: each fires independently.
4. Deleting an agent or a Jira connection removes its mappings; a handler targeting the assignee keeps working for other
   users.
5. The user search never sends the PAT anywhere but the configured Jira and never returns more than display data
   (key, name, display name).

---

### Task 1: Jira mock — users, assignment, user search

**Files:** `deploy/jira-mock/server.py`; tests that exercise the mock in-process (follow how `tests/test_jira.py` /
`tests/test_jira_export.py` load it, e.g. `runpy.run_path`), new `services/admin-api/tests/test_jira_mock_users.py`.

- Seeded users `mock-admin` (Mock Administrator — the connection user), `ops-bot` (Ops Bot), `docs-bot` (Docs Bot),
  `ana` (Ana Petrović), `marko` (Marko Ilić); some seeded tickets assigned to the bots and the people (keep the existing
  `mock-admin` assignments for tickets whose number is not divisible by 4 unless a test needs otherwise; assign a few of
  the others).
- `GET /rest/api/2/user/search?username=<q>`: users whose key, name or display name contains `q`
  (case-insensitive), Jira DC shape `{key, name, displayName, emailAddress?, active}`; max 50.
- Control page: assignee select (users + unassigned) on the edit form; "comment as" select on add comment (default
  "Jira User" as today). Changing assignee or adding a comment updates `updated` as today's edits do.
- JQL `assignee = <key>` keeps working; add `assignee is EMPTY` if it is a few lines.
- [ ] Step 1: failing tests for user search, reassigning through the control endpoint, comment author, `updated`
  bump. Step 2: fail. Step 3: implement. Step 4: focused + Jira tests. Step 5: commit
  `feat(jira-mock): users, assignment and user search`.

### Task 2: Jira events carry the assignee key and `for_assignee`

**Files:** `services/admin-api/src/knowledge_admin/jira/events.py` (`ticket()`, the changes, `record()`), tests
`tests/test_jira_events.py` (+ a new pure test file if clearer), docs `docs/jira.md` (payload fields).

- `payload.assignee_id` (Jira key or null) next to `payload.assignee`; `changes.assignee` = `{from, to, from_id, to_id}`.
- `for_assignee(ticket, changes, first_seen, own_comment_ids, connection_user) -> bool` — pure: true when assigned and
  (first seen, or assignee changed, or a new comment not ours). "Ours" is exactly what the loop protection already
  uses (recorded posted comment ids; pending posts matched by author = connection user and body).
- [ ] Step 1: failing pure tests — created assigned / created unassigned / reassigned / now unassigned / person comment
  / own reply (posted and pending) / status-only change / reassigned plus own reply. Step 2: fail. Step 3: implement.
  Step 4: Jira and event tests, full backend suite. Step 5: commit
  `feat(jira): events carry the assignee key and for_assignee`.

### Task 3: Jira users → agents (table, API, user search)

**Files:** `db/migrations/0117_jira_user_agents.up.sql`/`.down.sql`; `jira/client.py` (`search_users(query)` →
`/rest/api/2/user/search?username=`); `jira/api.py` (routes under the connection: `GET/PUT/DELETE
…/user-agents[/{key}]`, `GET …/users?query=`); tests `tests/test_jira_user_agents.py`.

- Table per the spec (`connection_id` cascade, `jira_user_key`, `jira_user_name`, `agent_id` → `ka.agents` cascade,
  audit columns, `UNIQUE (connection_id, jira_user_key)`).
- PUT validates: the agent exists; key non-empty, ≤ 255; name ≤ 255. List returns rows with the agent's name and
  `enabled`.
- User search: Jira results merged with distinct assignees seen in `ka.jira_tickets` for the connection (dedupe by
  key), returning `{key, name, display_name}` only; Jira errors return the seen assignees plus a short `warning`.
- [ ] Step 1: failing tests — CRUD, unique per connection, cascade on agent delete and connection delete, search merge
  and dedupe, Jira down → seen assignees + warning, nothing beyond key/name/display_name returned. Step 2: fail. Step 3:
  implement. Step 4: focused, full backend suite. Step 5: commit `feat(jira): map Jira users to agents`.

### Task 4: Handlers can target the assignee's agent

**Files:** `db/migrations/0118_handler_agent_from.up.sql`/`.down.sql` (`ka.event_handlers.agent_from text NOT NULL
DEFAULT 'fixed' CHECK (agent_from IN ('fixed','assignee'))`; `agent_id` may be null only for `assignee`);
`events/handlers_api.py` (validation: `assignee` only with the `agent` action and a type pattern that can match
`jira.*`; preview explains the target), `events/processor.py` (`send()` picks the agent), a pure
`events/targets.py` (or a function in the processor module if smaller): `pick_agent(handler, event, mapping_rows) ->
agent_id | refusal text`; the event records the refusal as today's skipped handlers do (follow the existing outcome
recording); tests `tests/test_handler_assignee_target.py`, `tests/test_event_runs.py` additions, an end-to-end test with
the mock (reassign to `ops-bot` → pull → event → mapped agent's run → reply recorded for posting; a person's comment →
another run; our reply → none).

- [ ] Step 1: failing tests — the pure picker (mapped, unmapped, unassigned, disabled agent, fixed handler unchanged);
  API validation; preview text; processor outcome; the end-to-end flow. Step 2: fail. Step 3: implement. Step 4:
  focused, full backend suite. Step 5: commit `feat(events): a handler can target the agent mapped to the assignee`.

### Task 5: UI and docs

**Files:** `services/admin-ui/app/features/jira/JiraSettingsView.tsx` (+ a small `JiraUserAgents.tsx`), `jira/api.ts`;
`features/events/HandlerSheet.tsx` and `handlers.ts` (agent field option **Agent mapped to the assignee**; field
suggestions `payload.for_assignee`, `payload.assignee_id`); tests under `components/__tests__`; docs `docs/jira.md`,
`docs/user-guide.md` (Jira and handlers sections).

- **Users and agents** section: rows "Jira user → agent" (display name and key, agent name, a "disabled" badge when the
  agent is off); Add with a search-as-you-type user picker (a typed key is accepted) and an agent select; change agent;
  remove.
- [ ] Step 1: failing vitest tests — section add/change/remove and request bodies; picker search; handler sheet option
  and saved body (`agent_from: 'assignee'`, no `agent_id`). Step 2: fail. Step 3: implement. Step 4: vitest, tsc,
  format:check. Step 5: commit `feat(admin-ui): Jira users and agents; handler target the assignee's agent`.
