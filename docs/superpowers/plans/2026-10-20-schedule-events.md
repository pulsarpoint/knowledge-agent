# Schedules That Start Agents (Through Events) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A schedule with the new target **Emit event** records a `schedule.fired` event at each firing; a fixed-agent
handler bound to that schedule runs the agent; the Schedules page shows which handler runs a schedule and offers
**Add handler**.

**Architecture:** Migration 0120 widens `ka.schedules.target_kind`. A new scheduler target `event.emit` inserts one event
(the same insert shape as the other event sources) and returns. Everything after that is the existing event processor
and handlers. The schedules list returns the handlers bound to each emit schedule; the UI adds the target, the
Handled by line and a Schedule picker in the handler sheet.

**Tech Stack:** PostgreSQL 17, Python 3.14 / FastAPI / psycopg 3 / pytest; React Router 7 / TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-20-schedule-events-design.md`.

## Global Constraints

- **Simple code, no unnecessary abstractions.** No new handler fields; a handler is "bound" to a schedule only through
  its type pattern and the condition `payload.schedule_id equals <id>`.
- Work only in `.claude/worktrees/schedule-events` (branch `feature/schedule-events`). Never `make`, never touch
  `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack, never kill processes you
  did not start. UI checks: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once
  (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims; migrations correct up and down (down refuses or removes `event.emit`
  schedules — removing is fine for test data). Next migration: **0120**.
- Exact texts and values: target label `Emit event`; event `source='schedule'`, `type='schedule.fired'`,
  `subject`/`title` = schedule name, payload `{schedule_id, name, fired_at, last_run_at, trigger}` (`trigger` `scheduled`
  | `manual`), `dedupe_key = schedule:<id>:<fired_at>`; run message `Recorded event <id>.`; Schedules row
  `Handled by: <name>` / `Handled by: <n> handlers` / `No handler runs this schedule.`; button **Add handler**; picker
  label **Schedule: <name>**.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests alone).

## Review Focus

1. A firing records exactly one event even when the target runs twice for the same firing (lease expiry, retry).
2. A handler bound to schedule A never runs for schedule B's events.
3. `last_run_at` is the previous successful firing (not the current one, which the claim has already stamped).
4. Jira pull schedules behave exactly as before.
5. Deleting a schedule or a handler never deletes the other.

---

### Task 1: The Emit event target (backend)

**Files:** `db/migrations/0120_schedule_emit_event.up.sql`/`.down.sql` (widen the `target_kind` CHECK to
`('jira.pull','event.emit')`; down deletes `event.emit` schedules and restores the CHECK); `events/scheduler.py`
(`TARGETS['event.emit']`); the target function in `events/schedules.py` or a small new module — records the event with
the shared insert shape (`events/api.py:144`, `processor.py:54`), `trigger` from `schedule['run_requested']`,
`last_run_at` from the schedule row's previous successful finish (`last_finished_at` when `last_status='succeeded'`, read
before this run records), `fired_at` = the claim's `last_run_at`, `ON CONFLICT (project_id,dedupe_key) DO NOTHING`;
`events/schedules_api.py` (create/edit accepts `target_kind` `event.emit` with no target row: `target_id` = the new
schedule's id generated in Python; the list adds, for emit schedules, `handlers: [{id, name, enabled}]` — handlers of the
project whose type pattern matches `schedule.fired` (use `events/matching.py`'s pattern match) and that have a condition
`{field: 'payload.schedule_id', op: 'equals', value: <id>}`); tests `tests/test_schedule_emit_event.py` (+ scheduler and
schedules API tests as needed).

- [ ] Step 1: failing tests — one event per firing with the payload; Run now → `trigger: manual`; `last_run_at` from the
  previous successful firing (null on the first); a repeated target call for the same firing records nothing new; run
  message; a fixed-agent handler with the schedule condition starts one run, another schedule's event does not match;
  create/edit/delete an emit schedule (target_id = id); the list's `handlers` (none, one, several, a disabled one);
  Jira pull unchanged. Step 2: fail. Step 3: implement. Step 4: focused, test_event*.py, test_schedule*.py, full backend
  suite. Step 5: commit `feat(schedules): an Emit event target records schedule.fired`.

### Task 2: Schedules page and handler sheet (UI) and docs

**Files:** `services/admin-ui/app/features/events/SchedulesView.tsx`, `schedules.ts` (`TARGETS` gains
`"event.emit": "Emit event"`; New schedule offers it with name and timing only; the row shows the Handled by line with
links (`/events/handlers?handler=<id>` — check the real Handlers route) or the empty text with **Add handler**, which opens
the handler sheet prefilled: name = schedule name, type pattern `schedule.fired`, the condition, action Send to agent,
fixed agent unset); `features/events/HandlerSheet.tsx` + `handlers.ts` (event type field offers **Schedule: <name>** for
each emit schedule — choosing one sets the type pattern and adds/replaces the `payload.schedule_id` condition; field
suggestions `payload.fired_at`, `payload.last_run_at`, `payload.trigger`); delete confirm texts on both sides; tests under
`components/__tests__`; docs `docs/user-guide.md` (Schedules, Handlers), `docs/architecture.md` or the events doc if it
lists schedule targets.

- [ ] Step 1: failing vitest tests — New schedule with Emit event (request body); Handled by (none/one/several) and Add
  handler (prefilled sheet, saved body); the handler sheet's Schedule picker. Step 2: fail. Step 3: implement. Step 4:
  vitest, tsc, format:check. Step 5: commit `feat(admin-ui): Emit event schedules with Add handler`.
