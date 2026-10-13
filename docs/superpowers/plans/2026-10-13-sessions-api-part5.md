# One Sessions API, Part 5 (Event Handlers Start Ordinary Runs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Send to agent" handler starts an ordinary agent run through the shared start; agent tasks go; notifications link to the run.

**Architecture:** The start route's logic becomes `sessions.start.start_run()`, called by `POST /api/sessions` and the event processor. Migration 0103 adds the run's `event_id`/`handler_id` (unique pair) and `notifications.session_id`, and fails open agent tasks. The worker writes the notification when an event run's first turn ends. `agent_tasks.py` and its loop are removed; the Events and Notifications pages link to the run.

**Tech Stack:** FastAPI + psycopg 3 (pytest with a throwaway Postgres), golang-migrate SQL, React Router 7 + shadcn + vitest.

**Spec:** `docs/superpowers/specs/2026-10-13-sessions-api-part5-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/sessions-part5` (branch `feature/sessions-part5`). Never run `make` targets (incl. `db/tests/run.sh` — edit only), never touch `.env` or `.local`, never `git stash`, never touch the running Docker stack or its database.
- Backend tests: `cd services/admin-api && uv run --frozen pytest -q <files>`. Known failures to ignore: code-graph tests in worktrees (test_code_graph_sdk errors, test_code_graphs_config::test_a_generated_graph_answers_code_queries_end_to_end, test_code_processing::test_go_module_root_…), `test_deploy::test_docs_commands_serve_the_docs_folder_inside_the_data_root`, `test_environments::test_migration_reapply_and_populated_rollback_protection`.
- UI: `cd services/admin-ui && pnpm exec vitest run`, `pnpm exec tsc --noEmit`, `pnpm exec react-router routes`. Once, from the worktree root: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` (never commit; never `pnpm install`). skills/agentTabs tests may flake under load (rerun alone).
- Texts, verbatim: `Started a run of {agent}.`; `Not run: event handlers start agent runs now. Send the event again.`; today's refusals for a missing, disabled, per-run or no-model agent (keep their constants).
- Behaviour comes from the prompt: the rendered handler prompt is the run's first message; nothing is appended.
- Commit after each task; messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Never `git add` `.superpowers` or `node_modules`.

## Review Focus

1. Processing the same event twice must start one run per handler, not two — Task 1 pins it.
2. An event run's notification is written exactly once, at the end of the first turn (finished, failed or stopped) — Task 2 pins all three.
3. `POST /api/sessions` (Ask agent, new documents, saved agents, discovery) behaves exactly as before after the extraction into `start_run` — Task 1 runs those suites.
4. Events handled before the update keep their task status and notification link on the Events page — Task 3 pins it.
5. A handler whose agent is per-run, disabled, missing or without a model fails with today's message and starts nothing — Task 1 pins each.

---

### Task 1: `start_run`; the event processor starts runs; migration 0103 (columns)

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/sessions/start.py` (new `start_run`), `sessions/api.py` (the start route calls it), `event_processor.py` (`send` → start a run), `agent_sessions.py` (delete `start_session`, `SessionRefused` if unused)
- Create: `db/migrations/0103_event_runs.up.sql`, `.down.sql` (columns part; Task 2 adds the task-failing part to the same file), `tests/test_event_runs.py`
- Modify tests: delete `start_session` tests (`tests/test_event_sessions.py` and others calling it); update tests of the event processor's agent action (`grep -rln "agent_tasks\|agent_task_id\|Queued for" services/admin-api/tests`)

**Interfaces:**
- `start_run(conn, project, actor, value: StartInput, conninfo: str, *, purpose: str | None = None, event_id: int | None = None, handler_id: UUID | None = None) -> UUID`: everything the start route did after resolving the actor (build, title, insert per starting point, first input, audit). With `event_id`/`handler_id` (agent starts only): purpose `event`, those columns set. The route becomes `start_run(...)` + `session_detail(...)`.
- 0103 up (columns): `ALTER TABLE agent_sessions ADD COLUMN event_id bigint REFERENCES events(id) ON DELETE SET NULL, ADD COLUMN handler_id uuid REFERENCES event_handlers(id) ON DELETE SET NULL; CREATE UNIQUE INDEX agent_sessions_event_handler ON agent_sessions(event_id, handler_id) WHERE event_id IS NOT NULL AND handler_id IS NOT NULL; ALTER TABLE notifications ADD COLUMN session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL;` plus comments. Check `agent_sessions_purpose` allows `event` (it does since 0081) and the ad-hoc constraint (event runs have an agent). Down: refuse while `agent_sessions.event_id IS NOT NULL` rows exist (`'Event runs exist; delete them before rollback'`), then drop index and columns.
- `event_processor.send(conn, store, handler, event)`: keep today's checks and messages (missing, disabled, per-run); render the prompt as today; if a run already exists for `(event['id'], handler['id'])` return its message and links; else build `StartInput(agent_id=handler['agent_id'], message=prompt, tools=<the agent's tools that are on — use start.agent_options' tools list>, expert_ids=<all offered>, subagents=<allowed>)` and call `start_run(conn, event['project_id'], handler['created_by'], value, store.conninfo, purpose='event', event_id=event['id'], handler_id=handler['id'])`; an `HTTPException` from the start becomes `HandlerFailed(detail)`. Return `(f"Started a run of {agent['name']}.", {'agent_session_id': session_id})` so the existing `event_handler_runs.agent_session_id` column records it.

- [ ] **Step 1: Write the failing tests** (`tests/test_event_runs.py`, building on the fixtures the existing event-handler tests use — find them with `grep -rln "Send to agent\|action.*agent" services/admin-api/tests`): a matching handler starts one run (purpose `event`, `agent_id`, the rendered prompt as input 1, the creator as actor, `event_id`/`handler_id`, the event handler run's `agent_session_id`); processing the event again starts nothing new; missing, disabled, per-run and no-model agents fail the handler with today's messages and start nothing; 0103 columns exist and the down refusal works.
- [ ] **Step 2: fail**, **Step 3: implement**, **Step 4: run** `tests/test_event_runs.py`, the event processor tests, `tests/test_sessions_start.py tests/test_sessions_saved_agent.py tests/test_new_document_runs.py tests/test_discovery_runs.py tests/test_sessions_api.py`, then the full suite — PASS apart from known failures. **Step 5: commit** `feat(events): a Send-to-agent handler starts an ordinary run (0103)`.

---

### Task 2: The first turn's notification; agent tasks go

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py` / `native_sessions.py` (write the notification when an event run's first turn ends), `notifications_api.py` (rows carry `session_id`; drop the agent_tasks join or keep it for old rows), `events_api.py` (handler runs carry the run's status for new runs; keep `agent_task` for old rows)
- Delete: `services/admin-api/src/knowledge_admin/agent_tasks.py` and its worker wiring in `native_sessions.main` (`supervise('Agent task', …)`), its tests
- Modify: `db/migrations/0103_event_runs.up.sql` (task-failing part), `tests/test_event_runs_migration.py` (create), `db/tests/run.sh`, `docs/migrations.md`

**Interfaces:**
- Notification: in the same transaction as the turn's finish, when the session's purpose is `event` and the finished turn is number 1: `INSERT INTO ka.notifications(project_id, kind, status, title, body, event_id, handler_id, agent_id, session_id)` with `kind 'agent.response'`, `status` `succeeded` when the turn finished with an answer (`waiting`), else `failed`; `title` = the session title; `body` = the turn's final answer (succeeded) or the status message (failed/stopped), cut to the column limit; `event_id`, `handler_id` from the session row. Exactly once per run (turn 1 only).
- 0103 up (task part): `UPDATE agent_tasks SET status='failed', error='Not run: event handlers start agent runs now. Send the event again.', worker_id=NULL, lease_expires_at=NULL, finished_at=now() WHERE status IN ('queued','running')` and insert one failed notification per such task (title = the task title, body = that message, `event_id`, `handler_id`, `agent_id`, `agent_task_id`), linking `agent_tasks.notification_id`. Down for this part: nothing to undo (the tasks stay failed).
- APIs: `GET /api/notifications` rows gain `session_id`; event detail's `runs` rows gain `run` = `{id, status, agent_id}` when `agent_session_id` is set (for new and pre-0090 runs), keeping `agent_task` for old rows.

- [ ] **Step 1: tests** — event run's first turn finished → one succeeded notification with the answer and `session_id`; first turn failed → one failed notification with the status message; stopped → failed notification; a second turn writes none; Test runs write none; 0103 task part (open tasks failed with the message and a notification each; finished tasks untouched); notifications and event detail APIs carry the run fields; the worker no longer runs agent tasks.
- [ ] **Step 2: fail**, **Step 3: implement** (delete `agent_tasks.py`; `grep -rn "agent_tasks" services/admin-api/src` leaves only SQL reads of the kept table), **Step 4: run** the full backend suite — PASS apart from known failures. **Step 5: document** 0103 in `docs/migrations.md`. **Step 6: commit** `feat(events): event runs notify when their first turn ends; agent tasks go`.

---

### Task 3: Events and Notifications pages link to the run

**Files:**
- Modify: `services/admin-ui/app/lib/events.ts` (handler run row gains `run: { id, status, agent_id } | null`), `components/EventsView.tsx` (for a run: `statusLabels[status]` badge and **Open run** → `/agents/{agent_id}?view=test&session_id={id}`; keep the old `agent_task` branch for older events), `lib/notifications.ts` (`session_id`), `components/NotificationsView.tsx` (**Open run** next to **Open event** when `session_id` and the notification's `agent_id` are set)
- Test: `components/__tests__/events.test.tsx` (or the file testing EventsView), `notifications.test.tsx`

- [ ] **Step 1: tests** — an event whose handler run has `run` shows the status badge and Open run with the right href; an older event with `agent_task` still shows its task status and Open notification; a notification with `session_id` shows Open run. **Step 2: fail**, **Step 3: implement**, **Step 4: run** the UI suite, tsc, routes — PASS. **Step 5: commit** `feat(admin-ui): events and notifications link to the agent run`.

---

### Task 4: Docs and the final check

**Files:** `docs/user-guide.md` (event handlers: an event starts an ordinary agent run, shown in the agent's Earlier runs, continuable; no automatic retries; notifications link to the run), `docs/agents.md`, any `docs/*events*` file, `deploy/README.md` (agent-session-worker no longer runs agent tasks; upgrade note), `services/admin-api/README.md` if it describes agent tasks.

- [ ] **Step 1: update the docs.** **Step 2: final search**

```bash
git grep -n "agent_tasks.py\|agent_tasks\.serve\|start_session\|Queued for\|SessionRefused" -- . ':!docs/superpowers' ':!db/migrations'
```

Expected: only history notes (list them). **Step 3: run everything** — PASS apart from known failures. **Step 4: commit** `docs: event handlers start ordinary agent runs`.
