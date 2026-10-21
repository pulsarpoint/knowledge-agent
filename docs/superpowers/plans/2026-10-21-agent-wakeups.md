# Agent Wake-ups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An agent can schedule a message to its own session for later (`schedule_wakeup`), list and cancel them; the
session worker delivers a due wake-up as the session's next turn; sessions with pending wake-ups stay open; the session
page shows and cancels them.

**Architecture:** Migration 0123 adds `not_before` and `kind` to `ka.agent_session_inputs`. Three tools in a new
`capabilities/wakeups.py` insert, list and discard wake-up inputs for `config['session_id']`. The worker's claim
ignores future inputs and never delivers a wake-up mid-turn; idle close skips sessions with pending wake-ups; Close
discards them. The session API returns pending wake-ups and cancels one; the session page lists them.

**Tech Stack:** PostgreSQL 17, Python 3.14 / FastAPI / psycopg 3 / openai-agents 0.19 / pytest; React Router 7 /
TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-21-agent-wakeups-design.md`.

## Global Constraints

- **Simple code, no unnecessary abstractions; checks testable in isolation** (each tool has a pure `check_<tool>`; the
  access matrix covers every catalog tool).
- Work only in `.claude/worktrees/agent-wakeups` (branch `feature/agent-wakeups`). Never `make`, never touch
  `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack, never kill processes you
  did not start. UI checks: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once
  (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims; migrations correct up and down. Next migration: **0123**.
- Exact values: limits 3 pending per session, delay 1 minute … 30 days (`in_minutes` 1 … 43200), message 1 … 2000
  characters; delivered input text `Scheduled wake-up (set <ISO time>): <message>`; session page line
  `Wakes up at <time>: <message>` with **Cancel**. Refusal texts (exact): `A session can have at most 3 pending
  wake-ups.`, `Choose a time between 1 minute and 30 days from now.`, `Give either in_minutes or at.`,
  `Wake-ups need a session.`, `This wake-up is not pending in this session.`
- Tools only for runs with a session (`config['session_id']`); subagents, experts and internal LLM calls do not get them
  (follow how `run_subagent` is withheld from subagents).
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests alone).

## Review Focus

1. A future wake-up never makes the worker claim (or lease) the session early, and never starves a person's message.
2. A wake-up is never delivered as a mid-turn comment, and never twice.
3. Idle close never closes a session with a pending wake-up; Close discards them; Stop keeps them.
4. A tool can only touch its own session's wake-ups.
5. Person messages behave exactly as before.

---

### Task 1: Storage, tools and delivery (backend)

**Files:** `db/migrations/0123_session_wakeups.up.sql`/`.down.sql` (`not_before timestamptz`, `kind text NOT NULL DEFAULT
'message' CHECK (kind IN ('message','wakeup'))`, `CHECK (kind <> 'wakeup' OR not_before IS NOT NULL)`, index on queued
wake-ups by `not_before`; down discards/removes wake-up rows and drops the columns); new
`services/admin-api/src/knowledge_admin/capabilities/wakeups.py` (`schedule_wakeup`, `list_wakeups`, `cancel_wakeup` with
pure checks; actor = the session's starting person; seq allocation like `POST /sessions/{id}/messages`); `tools/tools.json`
entries; `tools/catalog.py` import; withheld from subagents/experts/internal calls; `knowledge_agents/session_worker.py`
(claim: a queued input counts only when `not_before IS NULL OR not_before <= now()`; inputs delivered mid-turn as
comments exclude wake-ups; a due wake-up starts a turn with the `Scheduled wake-up (set …): …` text — find where queued
inputs become a turn's task); `sessions/text.py` `close_idle` skips sessions with pending wake-ups; Close
(`sessions/api.py`) discards them; tool schema snapshot regenerated for the three new tools only; tests
`tests/test_wakeups.py` (+ matrix rows).

- [ ] Step 1: failing tests — tool checks and limits (count, range, either/or, no session), list, cancel (own / other
  session / delivered); a future wake-up is not claimed; a due one starts a turn with the text; due during a working
  turn waits; delivered once; person messages unchanged; idle close skip and later close; Close discards, Stop keeps;
  chaining from a woken turn; subagent/expert tool lists exclude them; migration up/down. Step 2: fail. Step 3:
  implement. Step 4: focused, matrix, full backend suite. Step 5: commit `feat(agents): wake-ups — an agent continues
  its own session later`.

### Task 2: Session page and docs (UI)

**Files:** `sessions/api.py` (session detail returns `wakeups: [{id, due_at, message}]`; `DELETE
/api/sessions/{id}/wakeups/{wakeup_id}` cancels — same rule as the tool), `services/admin-ui/app/features/sessions/`
(SessionView/AgentChat: the pending list above the message box with **Cancel**, refreshed with the session's live
updates), tests; docs `docs/agents.md` (the tools), `docs/user-guide.md` (sessions), `docs/migrations.md` (0123).

- [ ] Step 1: failing tests (API detail + cancel; vitest list and Cancel). Step 2: fail. Step 3: implement. Step 4:
  backend focused + full UI checks. Step 5: commit `feat(sessions): pending wake-ups on the session page`.
