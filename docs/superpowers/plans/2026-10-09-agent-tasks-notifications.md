# Agent tasks and notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Send-to-agent handler queues an agent task instead of starting an interactive session; agent-session-worker
runs each task once without streaming and writes the answer (or why it failed) to a shared notifications list shown in
Administration → Notifications.

**Architecture:** Migration 0090 adds `ka.agent_tasks` (the hand-off) and `ka.notifications` (the answers). The event
processor (repository-worker) only inserts a task, in the event's transaction. `agent_tasks.py` (a loop beside the
session loop in agent-session-worker) claims due tasks with a lease, calls `agents.agent(..., stream=False)` once and
finishes the task with a notification, a retry with backoff, or a final failure. `notifications_api.py` serves the list;
the admin UI gets a Notifications page and task status on the event sheet.

**Tech Stack:** FastAPI, psycopg3 (dict rows), golang-migrate SQL, asyncio, OpenAI Agents SDK via `agents.agent`,
React Router 7, shadcn/ui, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-09-agent-tasks-notifications-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/agent-tasks` (branch `feature/agent-tasks`). Never run make targets;
  never touch `.env`, `.local` or the running Docker stack's database (tests use their own disposable DBs).
- Never `git stash`; never `git add .superpowers`. Commit trailer exactly:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Never call a real LLM: tests pass a stand-in for `agents.agent`.
- Backend tests: `cd services/admin-api && uv run pytest -q tests/<file>`. UI: `cd services/admin-ui && npx vitest run
  <file>`; UI tasks also run `npx react-router typegen && npx tsc --noEmit`.
- The app maps request validation errors to **400** (not 422); error body `{"error": {"message": ...}}`.
- Migrations: `BEGIN; SET search_path = ka, public; … COMMIT;`, up and down files.
- Task id: `agent-task:<event id>:<handler id>`. Task status `queued | running | succeeded | failed`. Notification kind
  `agent.response`, status `succeeded | failed`.
- Retries: 3 attempts in total; backoff 30 s after attempt 1, 2 min after attempt 2. Lease 15 min. Turn limit 600 s.
  Runner concurrency `AGENT_TASK_MAX` (default 2), poll 2 s.
- No-retry failures: missing agent `Agent not found.`; disabled `Agent is disabled.`; per-run
  `This agent gets its resources per run; handlers can only send to agents with fixed resources.`; no usable model
  `Select an enabled model in this agent’s General tab.`
- Notification title `<agent name> · <event type> <event subject>` (≤ 500); body ≤ 200000, NUL-free; task `error` ≤ 2000.
- Run message for a queued task: `Queued for <agent name>.`
- Notifications API: `GET /api/notifications?status&q&offset&limit` (`limit` ≤ 200, default 50) →
  `{notifications, next_offset}`; `GET /api/notifications/{id}` → `{notification}`; 404 `Notification not found.`
- Empty Notifications page: `No notifications yet. A handler that sends events to an agent puts the agent's answers here.`
- Handler form help: `The agent answers once, without a conversation. The answer goes to Notifications.`

## Review Focus

1. A task that fails and is retried must not collide with its own earlier run record (`agent_run_events` is unique by
   `(run_key, seq)`): each attempt records under its own key `task:<task id>:<attempt>`
   (Task 3, `test_each_attempt_records_under_its_own_run_key`).
2. A worker that dies during the third attempt leaves a `running` task whose lease runs out; the next claim makes it
   attempt 4 — it must fail with a notification, not run again or stay running forever
   (Task 3, `test_a_task_lost_on_its_last_attempt_fails`).
3. A worker that lost its lease (another worker reclaimed the task) and then finishes must record nothing — exactly one
   notification (Task 3, `test_an_expired_lease_is_claimed_again_and_the_old_worker_records_nothing`).
4. An answer containing NUL or longer than 200000 characters, or quoting a credential, must still be stored: NUL removed,
   capped, secrets redacted (Task 3, `test_answers_are_cleaned_capped_and_redacted`).
5. Searching notifications for `100%` or `a_b` must match literally, not as LIKE wildcards
   (Task 5, `test_search_matches_literally`).

## File Structure

- `db/migrations/0090_agent_tasks_notifications.{up,down}.sql` — tables `ka.notifications`, `ka.agent_tasks`; column
  `ka.event_handler_runs.agent_task_id`.
- `db/tests/run.sh` — 0090 down step in the rollback chain and an up check.
- `services/admin-api/src/knowledge_admin/event_processor.py` — action `agent` inserts a task (no session).
- `services/admin-api/src/knowledge_admin/events_api.py` — handler runs carry `agent_task`.
- `services/admin-api/src/knowledge_admin/agent_tasks.py` (new) — claim, load, run, finish, release, serve.
- `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py` — `main()` also serves agent tasks.
- `services/admin-api/src/knowledge_admin/notifications_api.py` (new) — list and detail; `api.py` wires it.
- `services/admin-ui/app/lib/notifications.ts` (new), `components/NotificationsView.tsx` (new),
  `routes/notifications.tsx` (new), `routes.ts`, `components/AdminLayout.tsx`.
- `services/admin-ui/app/lib/events.ts`, `components/EventsView.tsx`, `components/HandlerSheet.tsx`,
  `components/RunsView.tsx` — task status, links, help text, run kind.
- Docs: `docs/user-guide.md`, `docs/agents.md`, `docs/migrations.md`.

---

### Task 1: Migration 0090 — agent tasks and notifications

**Files:**
- Create: `db/migrations/0090_agent_tasks_notifications.up.sql`, `db/migrations/0090_agent_tasks_notifications.down.sql`
- Create: `services/admin-api/tests/test_agent_tasks_migration.py`
- Modify: `services/admin-api/tests/test_events_migration.py` (`test_down_and_up` steps over 0090 first)
- Modify: `db/tests/run.sh`

**Interfaces:**
- Produces: `ka.notifications(id bigint, project_id, created_at, kind, status, title, body, event_id, handler_id,
  agent_id, agent_task_id)`; `ka.agent_tasks(id text, project_id, agent_id, agent_name, title, prompt, event_id,
  handler_id, status, attempts, next_attempt_at, worker_id, lease_expires_at, error, notification_id, created_at,
  started_at, finished_at)`; `ka.event_handler_runs.agent_task_id text`.

- [ ] **Step 1: Write the failing tests**

`services/admin-api/tests/test_agent_tasks_migration.py`:

```python
"""0090: agent tasks (an agent call a handler queued for an event) and notifications (the answers, one list for all)."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from test_events_migration import an_event

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_task(conn, event, **values) -> dict:
    row = {'id': f"agent-task:{event['id']}:{uuid4()}", 'project_id': event['project_id'], 'agent_id': 'triage',
           'agent_name': 'Triage', 'title': 'Triage · jira.ticket.created DEMO-1', 'prompt': 'Triage DEMO-1',
           'event_id': event['id'], 'handler_id': uuid4(), **values}
    return conn.execute(f"INSERT INTO ka.agent_tasks({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *",
                        tuple(row.values())).fetchone()


def a_notification(conn, event, **values) -> dict:
    row = {'project_id': event['project_id'], 'kind': 'agent.response', 'status': 'succeeded',
           'title': 'Triage · jira.ticket.created DEMO-1', 'body': 'Looks like a timeout.', 'event_id': event['id'], **values}
    return conn.execute(f"INSERT INTO ka.notifications({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *",
                        tuple(row.values())).fetchone()


def test_a_new_task_is_queued_and_due_now(store):
    with store.connect() as conn:
        task = a_task(conn, an_event(conn))
        now = conn.execute('SELECT now() AS now').fetchone()['now']
    assert (task['status'], task['attempts'], task['lease_expires_at'], task['finished_at'], task['worker_id']) == (
        'queued', 0, None, None, None)
    assert task['next_attempt_at'] <= now


@pytest.mark.parametrize('bad', [
    {'id': 'task-1'}, {'prompt': ''}, {'prompt': 'x' * 20001}, {'title': ' '}, {'status': 'done'},
    {'status': 'running'},  # running needs a lease
    {'status': 'succeeded'},  # finished needs finished_at
    {'attempts': -1}, {'error': 'x' * 2001}])
def test_task_checks(store, bad):
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        a_task(conn, an_event(conn), **bad)


@pytest.mark.parametrize('bad', [{'kind': 'mail'}, {'status': 'queued'}, {'title': ' '}, {'body': 'x' * 200001}])
def test_notification_checks(store, bad):
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        a_notification(conn, an_event(conn), **bad)


def test_a_running_and_a_finished_task_are_valid(store):
    with store.connect() as conn:
        event = an_event(conn)
        running = a_task(conn, event, status='running', attempts=1, worker_id='w1', lease_expires_at='2026-10-09T10:15:00Z')
        done = a_task(conn, event, status='failed', attempts=3, finished_at='2026-10-09T10:20:00Z', error='boom')
    assert (running['status'], done['status']) == ('running', 'failed')


def test_deleting_the_event_keeps_task_and_notification(store):
    with store.connect() as conn:
        event = an_event(conn)
        task = a_task(conn, event)
        notification = a_notification(conn, event, agent_task_id=task['id'])
        conn.execute('UPDATE ka.agent_tasks SET notification_id=%s WHERE id=%s', (notification['id'], task['id']))
        conn.execute('DELETE FROM ka.events WHERE id=%s', (event['id'],))
        assert conn.execute('SELECT event_id,notification_id FROM ka.agent_tasks WHERE id=%s', (task['id'],)).fetchone() == {
            'event_id': None, 'notification_id': notification['id']}
        assert conn.execute('SELECT event_id FROM ka.notifications WHERE id=%s', (notification['id'],)).fetchone()['event_id'] is None


def test_handler_runs_can_point_at_a_task(store):
    with store.connect() as conn:
        event = an_event(conn)
        task = a_task(conn, event)
        conn.execute("""INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message,agent_task_id)
            VALUES (%s,%s,'Triage','succeeded','Queued for Triage.',%s)""", (event['id'], task['handler_id'], task['id']))


def test_down_and_up(store):
    with store.connect() as conn:
        event = an_event(conn)
        a_notification(conn, event, agent_task_id=a_task(conn, event)['id'])
        run(conn, '0090_agent_tasks_notifications.down.sql')
        gone = conn.execute("""SELECT to_regclass('ka.agent_tasks') IS NULL AND to_regclass('ka.notifications') IS NULL
            AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='ka'
                AND table_name='event_handler_runs' AND column_name='agent_task_id') AS gone""").fetchone()['gone']
        assert gone
        run(conn, '0090_agent_tasks_notifications.up.sql')
        assert conn.execute("SELECT to_regclass('ka.agent_tasks_due') IS NOT NULL AS found").fetchone()['found']
```

In `services/admin-api/tests/test_events_migration.py`, `test_down_and_up` must first undo 0090 (its tables reference
`ka.events`) and re-apply it at the end. Replace the function with:

```python
def test_down_and_up(store):
    with store.connect() as conn:
        an_event(conn)
        conn.execute((MIGRATIONS / '0090_agent_tasks_notifications.down.sql').read_text(), prepare=False)  # 0090 references events
        conn.execute((MIGRATIONS / '0089_event_handlers.down.sql').read_text(), prepare=False)  # 0089 builds on 0088
        conn.execute((MIGRATIONS / '0088_events.down.sql').read_text(), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.events') IS NULL AS gone").fetchone()['gone']
        conn.execute((MIGRATIONS / '0088_events.up.sql').read_text(), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.events_jira_issues') IS NOT NULL AS found").fetchone()['found']
        conn.execute((MIGRATIONS / '0089_event_handlers.up.sql').read_text(), prepare=False)
        conn.execute((MIGRATIONS / '0090_agent_tasks_notifications.up.sql').read_text(), prepare=False)
```

(Keep any assertions that follow the 0089 up line in the current file after the new 0090 up line.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_agent_tasks_migration.py tests/test_events_migration.py`
Expected: FAIL — `ka.agent_tasks` does not exist / the 0090 files are missing.

- [ ] **Step 3: Write the migration**

`db/migrations/0090_agent_tasks_notifications.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- What event-triggered agent calls produced: the agent's answer, or why the call failed. One list for every user
-- (no recipients, no read state); agent_tasks.py in agent-session-worker writes it.
CREATE TABLE notifications (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL CHECK (kind IN ('agent.response')),
    status text NOT NULL CHECK (status IN ('succeeded', 'failed')),
    title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 500),
    body text NOT NULL CHECK (char_length(body) <= 200000),
    event_id bigint REFERENCES events(id) ON DELETE SET NULL,
    handler_id uuid,
    agent_id text,
    agent_task_id text
);
CREATE INDEX notifications_newest ON notifications(project_id, id DESC);
COMMENT ON COLUMN notifications.agent_task_id IS 'The agent task that wrote it (no foreign key: the task points at its notification).';

-- One agent call a Send-to-agent handler queued for an event. The event processor inserts it (queued);
-- agent-session-worker claims it with a lease, runs the agent once without streaming and finishes it with a
-- notification. 3 attempts in all; a task whose lease ran out is claimed again.
CREATE TABLE agent_tasks (
    id text PRIMARY KEY CHECK (id ~ '^agent-task:[0-9]+:[0-9a-f-]{36}$'),
    project_id uuid NOT NULL REFERENCES projects(id),
    agent_id text NOT NULL,
    agent_name text NOT NULL,
    title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 500),
    prompt text NOT NULL CHECK (char_length(prompt) BETWEEN 1 AND 20000),
    event_id bigint REFERENCES events(id) ON DELETE SET NULL,
    handler_id uuid,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    worker_id text,
    lease_expires_at timestamptz,
    error text CHECK (char_length(error) <= 2000),
    notification_id bigint REFERENCES notifications(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    CONSTRAINT agent_tasks_lease CHECK ((status = 'running') = (lease_expires_at IS NOT NULL)),
    CONSTRAINT agent_tasks_finished CHECK ((status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL))
);
COMMENT ON COLUMN agent_tasks.id IS 'agent-task:<event id>:<handler id> — processing an event again queues nothing new.';
COMMENT ON COLUMN agent_tasks.agent_id IS 'No foreign key, like event_handlers.agent_id: a deleted agent fails the task.';
CREATE INDEX agent_tasks_due ON agent_tasks(next_attempt_at, id) WHERE status IN ('queued', 'running');

ALTER TABLE event_handler_runs ADD COLUMN agent_task_id text;
COMMENT ON COLUMN event_handler_runs.agent_task_id IS 'Send to agent: the task it queued (agent_session_id is for runs before 0090).';

COMMIT;
```

`db/migrations/0090_agent_tasks_notifications.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

ALTER TABLE event_handler_runs DROP COLUMN agent_task_id;
DROP TABLE agent_tasks;
DROP TABLE notifications;

COMMIT;
```

- [ ] **Step 4: Add the db test steps**

In `db/tests/run.sh`, the rollback chain starts with `run_migrate postgres down 1` followed by the 0089 check
(`handlers_removed=…`). Insert a 0090 down step and check **before** that first `down 1`, so the chain still ends at
version 83:

```sh
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
tasks_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.agent_tasks') IS NULL AND to_regclass('ka.notifications') IS NULL")
[ "$tasks_removed" = t ] || { echo 'FAIL: 0090 down left agent tasks or notifications' >&2; exit 1; }
```

After the `PASS: events have a status and a chain; handlers and their runs exist.` line, add:

```sh
tasks=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.agent_tasks') IS NOT NULL AND to_regclass('ka.notifications') IS NOT NULL
        AND to_regclass('ka.agent_tasks_due') IS NOT NULL")
[ "$tasks" = t ] || { echo 'FAIL: 0090 agent tasks or notifications missing' >&2; exit 1; }
printf '%s\n' 'PASS: agent tasks and notifications exist.'
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_agent_tasks_migration.py tests/test_events_migration.py tests/test_event_handlers_migration.py`
Expected: PASS.
Run: `sh db/tests/run.sh` (from the worktree root; it uses its own disposable container)
Expected: exit 0, including `PASS: agent tasks and notifications exist.`

- [ ] **Step 6: Commit**

```bash
git add db/migrations/0090_agent_tasks_notifications.up.sql db/migrations/0090_agent_tasks_notifications.down.sql \
  db/tests/run.sh services/admin-api/tests/test_agent_tasks_migration.py services/admin-api/tests/test_events_migration.py
git commit -m "feat(db): agent tasks and notifications (0090)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The event processor queues agent tasks; event detail shows them

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/event_processor.py` (`send`, run insert, imports, docstring)
- Modify: `services/admin-api/src/knowledge_admin/events_api.py` (detail `runs`)
- Modify: `services/admin-api/tests/test_event_processor.py` (agent tests)
- Modify: `services/admin-api/tests/test_events_api.py` (runs carry `agent_task`)

**Interfaces:**
- Consumes: `ka.agent_tasks`, `ka.event_handler_runs.agent_task_id` (Task 1); `event_handlers_api.PER_RUN`.
- Produces: `event_processor.task_id(event: dict, handler: dict) -> str`; constant `event_processor.DISABLED`;
  run rows with `agent_task_id`; `GET /api/events/{id}` runs gain
  `agent_task: {id, status, attempts, notification_id} | null` and `agent_id` = the session's or the task's agent.

- [ ] **Step 1: Write the failing tests**

In `services/admin-api/tests/test_event_processor.py`, replace `test_send_to_agent_starts_an_event_session` and
`test_a_disabled_agent_fails_the_run` with:

```python
def test_send_to_agent_queues_one_task(tester):
    t = tester
    handler = a_handler(t.store, action='agent', emit=None, agent_id=t.agent,
                        prompt='Triage {{ event.subject }} ({{ event.payload.status }})')
    first = an_event(t.store)
    assert event_processor.run_once(t.store) is True
    [run] = runs(t.store, first['id'])
    key = f"agent-task:{first['id']}:{handler['id']}"
    assert (run['status'], run['message'], run['agent_task_id'], run['agent_session_id']) == (
        'succeeded', 'Queued for Tester.', key, None)
    with t.store.connect() as conn:
        task = conn.execute('SELECT * FROM ka.agent_tasks WHERE id=%s', (key,)).fetchone()
        sessions = conn.execute('SELECT count(*) AS n FROM ka.agent_sessions').fetchone()['n']
    assert (task['status'], task['attempts'], task['agent_id'], task['agent_name'], task['prompt'], task['title'],
            task['event_id'], task['handler_id'], task['project_id']) == (
        'queued', 0, t.agent, 'Tester', 'Triage DEMO-1 (In Progress)', 'Tester · jira.ticket.updated DEMO-1',
        first['id'], handler['id'], first['project_id'])
    assert sessions == 0  # no interactive session any more
    assert event(t.store, first['id'])['status'] == 'processed'


def test_processing_an_event_again_queues_no_second_task(tester):
    t = tester
    a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt='Triage {{ event.subject }}')
    first = an_event(t.store)
    event_processor.run_once(t.store)
    with t.store.connect() as conn:  # as if the first pass had been rolled back after queueing
        conn.execute("UPDATE ka.events SET status='pending',processed_at=NULL WHERE id=%s", (first['id'],))
    event_processor.run_once(t.store)
    with t.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_tasks').fetchone()['n'] == 1
    assert [run['status'] for run in runs(t.store, first['id'])] == ['succeeded', 'succeeded']


@pytest.mark.parametrize('change, message', [
    ('UPDATE ka.agents SET enabled=false WHERE id=%s', 'Agent is disabled.'),
    ("UPDATE ka.agents SET resources='per_run' WHERE id=%s",
     'This agent gets its resources per run; handlers can only send to agents with fixed resources.')])
def test_an_unusable_agent_fails_the_run(tester, change, message):
    t = tester
    a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt='Triage {{ event.subject }}')
    with t.store.connect() as conn:
        conn.execute(change, (t.agent,))
    first = an_event(t.store)
    event_processor.run_once(t.store)
    [run] = runs(t.store, first['id'])
    assert (run['status'], run['message'], run['agent_task_id']) == ('failed', message, None)
    assert event(t.store, first['id'])['status'] == 'failed'
    with t.store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_tasks').fetchone()['n'] == 0
```

(Keep `test_a_missing_agent_fails_the_run` as it is.)

In `services/admin-api/tests/test_events_api.py`, in `test_one_event_has_its_parent_chain_and_runs` change the runs
assertion to include the task:

```python
    assert [(r['handler_name'], r['status'], r['emitted_event_id'], r['agent_session_id'], r['agent_id'], r['agent_task'])
            for r in event['runs']] == [('Triage', 'succeeded', grandchild, None, None, None)]
```

and add:

```python
def test_a_send_to_agent_run_shows_its_task(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [root] = seed(store, 1)
    handler = uuid4()
    key = f'agent-task:{root}:{handler}'
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("""INSERT INTO ka.notifications(project_id,kind,status,title,body,event_id,agent_task_id)
            VALUES (%s,'agent.response','succeeded','Triage · x DEMO-1','Done.',%s,%s)""", (project, root, key))
        notification = conn.execute('SELECT max(id) AS id FROM ka.notifications').fetchone()['id']
        conn.execute("""INSERT INTO ka.agent_tasks(id,project_id,agent_id,agent_name,title,prompt,event_id,handler_id,status,
            attempts,finished_at,notification_id) VALUES (%s,%s,'triage','Triage','Triage · x DEMO-1','Hi',%s,%s,'succeeded',2,
            now(),%s)""", (key, project, root, handler, notification))
        conn.execute("""INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message,agent_task_id)
            VALUES (%s,%s,'Triage','succeeded','Queued for Triage.',%s)""", (root, handler, key))
    [run] = client.get(f'/api/events/{root}', headers=HEADERS).json()['event']['runs']
    assert (run['agent_id'], run['agent_session_id'], run['agent_task']) == (
        'triage', None, {'id': key, 'status': 'succeeded', 'attempts': 2, 'notification_id': notification})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_processor.py tests/test_events_api.py`
Expected: FAIL — `Started a session with Tester.` instead of `Queued for Tester.`; runs have no `agent_task`.

- [ ] **Step 3: Implement**

In `event_processor.py`:
- Replace `from knowledge_admin.agent_sessions import SessionRefused, start_session` with
  `from knowledge_admin.event_handlers_api import PER_RUN`.
- Add `DISABLED = 'Agent is disabled.'` next to `NO_AGENT`.
- Replace `send` with:

```python
def task_id(event: dict, handler: dict) -> str:
    """One task per event and handler: processing the event again queues nothing new."""
    return f"agent-task:{event['id']}:{handler['id']}"


def send(conn, store: RepositoryStore, handler: dict, event: dict) -> tuple[str, dict]:
    """Queue the agent call for agent-session-worker (agent_tasks.py); the answer goes to Notifications."""
    agent = conn.execute('SELECT name,enabled,resources FROM ka.agents WHERE id=%s', (handler['agent_id'],)).fetchone()
    if agent is None:
        raise HandlerFailed(NO_AGENT)
    if not agent['enabled']:
        raise HandlerFailed(DISABLED)
    if agent['resources'] != 'fixed':
        raise HandlerFailed(PER_RUN)
    prompt = event_templates.render_prompt(handler['prompt'], event)
    key = task_id(event, handler)
    title = f"{agent['name']} · {event['type']} {event['subject']}"[:500]
    conn.execute("""INSERT INTO ka.agent_tasks(id,project_id,agent_id,agent_name,title,prompt,event_id,handler_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
        (key, event['project_id'], handler['agent_id'], agent['name'], title, prompt, event['id'], handler['id']))
    return f"Queued for {agent['name']}.", {'agent_task_id': key}
```

- In `process`, record the task on the run:

```python
        conn.execute("""INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message,emitted_event_id,
            agent_session_id,agent_task_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (event['id'], handler['id'], handler['name'], status, run_message(message),
             links.get('emitted_event_id'), links.get('agent_session_id'), links.get('agent_task_id')))
```

- In the module docstring, add one sentence: `Send to agent queues an agent task (agent_tasks.py runs it in
  agent-session-worker).`

In `events_api.py`, replace the `row['runs'] = …` query with:

```python
            row['runs'] = conn.execute("""SELECT r.handler_id,r.handler_name,r.status,r.message,r.emitted_event_id,
                r.agent_session_id,coalesce(s.agent_id,t.agent_id) AS agent_id,r.created_at,
                CASE WHEN t.id IS NULL THEN NULL ELSE jsonb_build_object('id',t.id,'status',t.status,'attempts',t.attempts,
                    'notification_id',t.notification_id) END AS agent_task
                FROM ka.event_handler_runs r LEFT JOIN ka.agent_sessions s ON s.id=r.agent_session_id
                LEFT JOIN ka.agent_tasks t ON t.id=r.agent_task_id WHERE r.event_id=%s ORDER BY r.id""", (event_id,)).fetchall()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_processor.py tests/test_events_api.py tests/test_event_handlers_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/event_processor.py services/admin-api/src/knowledge_admin/events_api.py \
  services/admin-api/tests/test_event_processor.py services/admin-api/tests/test_events_api.py
git commit -m "feat(events): Send to agent queues an agent task instead of starting a session

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `agent_tasks.py` — claim, run once, finish, retry, lease

**Files:**
- Create: `services/admin-api/src/knowledge_admin/agent_tasks.py`
- Create: `services/admin-api/tests/test_agent_tasks.py`

**Interfaces:**
- Consumes: `ka.agent_tasks`, `ka.notifications` (Task 1); `agents.agent`, `agents.load_credentials`;
  `knowledge_agents.config.get_agent_config/model_fits/per_run`; `knowledge_agents.native_sessions.failure`;
  `knowledge_agents.session_worker.clean_text`; `agent_sessions.NO_MODEL`; `event_handlers_api.PER_RUN`.
- Produces: `claim(store, worker_id: str, limit: int) -> list[dict]`; `load(store, task: dict) -> tuple[dict, dict[str, str]]`;
  `record_key(task: dict) -> str`; `async run_task(store, task: dict, run_agent=agent) -> None`;
  `finish(store, task: dict, outcome: str, text: str) -> bool` (`outcome` in `succeeded | failed | retry`);
  `release(store, task: dict) -> None`; `class NoRetry(Exception)`; constants `POLL_SECONDS = 2`, `LEASE_MINUTES = 15`,
  `TURN_SECONDS = 600`, `MAX_ATTEMPTS = 3`, `BACKOFF_SECONDS = {1: 30, 2: 120}`, `MAX_ERROR = 2000`,
  `MAX_BODY = 200_000`, `NO_AGENT`, `DISABLED`, `LOST`.

- [ ] **Step 1: Write the failing tests**

`services/admin-api/tests/test_agent_tasks.py`:

```python
"""Agent tasks: a queued agent call runs once without streaming and ends as a notification; failures are retried
twice with backoff; problems retrying cannot fix fail at once; only the worker holding the lease may finish a task."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_admin import agent_tasks
from test_event_processor import an_event
from test_test_sessions import tester  # noqa: F401  (fixture)


def queue(t, **values) -> dict:
    event = an_event(t.store)
    handler = uuid4()
    with t.store.connect() as conn:
        row = {'id': f"agent-task:{event['id']}:{handler}", 'project_id': event['project_id'], 'agent_id': t.agent,
               'agent_name': 'Tester', 'title': 'Tester · jira.ticket.updated DEMO-1', 'prompt': 'Triage DEMO-1',
               'event_id': event['id'], 'handler_id': handler, **values}
        return conn.execute(f"INSERT INTO ka.agent_tasks({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *",
                            tuple(row.values())).fetchone()


def task(store, task_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.agent_tasks WHERE id=%s', (task_id,)).fetchone()


def notifications(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.notifications ORDER BY id').fetchall()


def due_now(store, task_id) -> None:
    with store.connect() as conn:
        conn.execute('UPDATE ka.agent_tasks SET next_attempt_at=now() WHERE id=%s', (task_id,))


class Agent:
    """A stand-in for agents.agent: records each call, then answers or raises the next outcome."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    async def __call__(self, config, task, **options):
        self.calls.append((config, task, options))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(final_output=outcome)


def run_due(store, fake, worker='w1') -> list[dict]:
    claimed = agent_tasks.claim(store, worker, 10)
    for item in claimed:
        asyncio.run(agent_tasks.run_task(store, item, fake))
    return claimed


def test_a_task_runs_once_and_its_answer_becomes_a_notification(tester):
    t = tester
    queued = queue(t)
    fake = Agent('The login times out after the proxy change.')
    [claimed] = run_due(t.store, fake)
    assert (claimed['status'], claimed['attempts'], claimed['worker_id']) == ('running', 1, 'w1')
    [(config, text, options)] = fake.calls
    assert text == 'Triage DEMO-1'
    assert (options['stream'], options['turn_timeout_seconds']) == (False, 600)
    assert 'tester-model-secret' in options['credentials'].values()
    assert config['id'] == t.agent and config['experts'] == []
    assert config['record'] == {'run_key': f"task:{queued['id']}:1", 'project_id': str(queued['project_id']),
                                'kind': 'event_task', 'title': 'Tester · jira.ticket.updated DEMO-1'}
    [note] = notifications(t.store)
    assert (note['kind'], note['status'], note['title'], note['body'], note['event_id'], note['handler_id'],
            note['agent_id'], note['agent_task_id'], note['project_id']) == (
        'agent.response', 'succeeded', 'Tester · jira.ticket.updated DEMO-1', 'The login times out after the proxy change.',
        queued['event_id'], queued['handler_id'], t.agent, queued['id'], queued['project_id'])
    done = task(t.store, queued['id'])
    assert (done['status'], done['notification_id'], done['attempts'], done['worker_id'], done['lease_expires_at'],
            done['error']) == ('succeeded', note['id'], 1, None, None, None)
    assert done['finished_at'] is not None and done['started_at'] is not None
    assert agent_tasks.claim(t.store, 'w1', 10) == []


def test_a_failing_call_is_retried_with_backoff_then_fails(tester):
    t = tester
    queued = queue(t)
    fake = Agent(RuntimeError('boom'), RuntimeError('boom'), RuntimeError('boom'))
    message = 'Agent execution failed (RuntimeError). Check the model connection and agent resources.'
    for attempt, wait in ((1, 30), (2, 120)):
        run_due(t.store, fake)
        with t.store.connect() as conn:
            row = conn.execute("""SELECT status,attempts,error,worker_id,lease_expires_at,
                extract(epoch FROM next_attempt_at - now()) AS wait FROM ka.agent_tasks WHERE id=%s""", (queued['id'],)).fetchone()
        assert (row['status'], row['attempts'], row['error'], row['worker_id'], row['lease_expires_at']) == (
            'queued', attempt, message, None, None)
        assert wait - 5 < row['wait'] <= wait
        assert agent_tasks.claim(t.store, 'w1', 10) == []  # not due yet
        due_now(t.store, queued['id'])
    run_due(t.store, fake)
    [note] = notifications(t.store)
    assert (note['status'], note['body']) == ('failed', message)
    done = task(t.store, queued['id'])
    assert (done['status'], done['attempts'], done['error'], done['notification_id']) == ('failed', 3, message, note['id'])
    assert len(fake.calls) == 3


def test_each_attempt_records_under_its_own_run_key(tester):
    t = tester
    queued = queue(t)
    fake = Agent(TimeoutError(), 'Done.')
    run_due(t.store, fake)
    due_now(t.store, queued['id'])
    run_due(t.store, fake)
    assert [config['record']['run_key'] for config, _, _ in fake.calls] == [
        f"task:{queued['id']}:1", f"task:{queued['id']}:2"]
    assert [note['status'] for note in notifications(t.store)] == ['succeeded']


@pytest.mark.parametrize('change, agent_id, message', [
    ('UPDATE ka.agents SET enabled=false WHERE id=%s', None, 'Agent is disabled.'),
    ("UPDATE ka.agents SET resources='per_run' WHERE id=%s", None,
     'This agent gets its resources per run; handlers can only send to agents with fixed resources.'),
    ('UPDATE ka.llm_models SET enabled=false WHERE id=(SELECT model_id FROM ka.agents WHERE id=%s)', None,
     'Select an enabled model in this agent’s General tab.'),
    (None, 'gone', 'Agent not found.')])
def test_problems_that_retrying_cannot_fix_fail_at_once(tester, change, agent_id, message):
    t = tester
    if change:
        with t.store.connect() as conn:
            conn.execute(change, (t.agent,))
    queued = queue(t, **({'agent_id': agent_id} if agent_id else {}))
    fake = Agent('never')
    run_due(t.store, fake)
    assert fake.calls == []
    [note] = notifications(t.store)
    assert (note['status'], note['body']) == ('failed', message)
    assert (task(t.store, queued['id'])['status'], task(t.store, queued['id'])['attempts']) == ('failed', 1)


def test_an_expired_lease_is_claimed_again_and_the_old_worker_records_nothing(tester):
    t = tester
    queued = queue(t)
    [first] = agent_tasks.claim(t.store, 'w1', 10)
    with t.store.connect() as conn:  # w1 stalled past its lease
        conn.execute("UPDATE ka.agent_tasks SET lease_expires_at=now() - interval '1 second' WHERE id=%s", (queued['id'],))
    [second] = agent_tasks.claim(t.store, 'w2', 10)
    assert (second['worker_id'], second['attempts']) == ('w2', 2)
    assert agent_tasks.finish(t.store, first, 'succeeded', 'late answer') is False
    asyncio.run(agent_tasks.run_task(t.store, second, Agent('fresh answer')))
    assert [note['body'] for note in notifications(t.store)] == ['fresh answer']


def test_a_task_lost_on_its_last_attempt_fails(tester):
    t = tester
    queued = queue(t, status='running', attempts=3, worker_id='gone', started_at='2026-10-09T10:00:00Z',
                   lease_expires_at='2026-10-09T10:15:00Z')
    fake = Agent('never')
    [claimed] = run_due(t.store, fake)
    assert claimed['attempts'] == 4 and fake.calls == []
    [note] = notifications(t.store)
    assert (note['status'], note['body']) == ('failed', agent_tasks.LOST)
    assert task(t.store, queued['id'])['status'] == 'failed'


def test_two_workers_never_claim_the_same_task(tester):
    t = tester
    queue(t)
    with t.store.connect() as conn:
        conn.execute('SELECT id FROM ka.agent_tasks FOR UPDATE')  # another worker is claiming it right now
        assert agent_tasks.claim(t.store, 'w2', 10) == []
    assert len(agent_tasks.claim(t.store, 'w1', 10)) == 1
    assert agent_tasks.claim(t.store, 'w2', 10) == []  # running under a live lease


def test_answers_are_cleaned_capped_and_redacted(tester):
    t = tester
    queue(t)
    run_due(t.store, Agent('a\x00b key=tester-model-secret ' + 'x' * 300_000))
    [note] = notifications(t.store)
    assert note['body'].startswith('ab key=[redacted] ')
    assert len(note['body']) == agent_tasks.MAX_BODY and '\x00' not in note['body']


def test_a_deleted_event_still_gets_its_notification(tester):
    t = tester
    queued = queue(t)
    [claimed] = agent_tasks.claim(t.store, 'w1', 10)
    with t.store.connect() as conn:
        conn.execute('DELETE FROM ka.events WHERE id=%s', (queued['event_id'],))
    asyncio.run(agent_tasks.run_task(t.store, claimed, Agent('Done.')))
    [note] = notifications(t.store)
    assert (note['status'], note['event_id']) == ('succeeded', None)


def test_release_gives_a_task_back_without_counting_the_attempt(tester):
    t = tester
    queued = queue(t)
    [claimed] = agent_tasks.claim(t.store, 'w1', 10)
    agent_tasks.release(t.store, claimed)
    back = task(t.store, queued['id'])
    assert (back['status'], back['attempts'], back['worker_id'], back['lease_expires_at']) == ('queued', 0, None, None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_agent_tasks.py`
Expected: FAIL — `ImportError: cannot import name 'agent_tasks'`.

- [ ] **Step 3: Implement**

`services/admin-api/src/knowledge_admin/agent_tasks.py`:

```python
"""Agent tasks: one agent call a Send-to-agent handler queued for an event (ka.agent_tasks, inserted by the event
processor). agent-session-worker runs them here: it claims due tasks (FOR UPDATE SKIP LOCKED, a 15-minute lease), runs
the agent once with the rendered prompt — no conversation, no streaming, no session — and writes the answer, or why it
failed, to ka.notifications. A failing call is tried 3 times in all (again 30 s, then 2 minutes later); a task whose
agent is gone, disabled, per-run or without a usable model fails at once. Only the worker that still holds a task's
lease may finish it, so a worker that lost the lease records nothing. The turn limit (10 minutes) is shorter than the
lease, so a running call never outlives it."""
import asyncio
import logging
import os
import socket
from typing import Any

import psycopg

from knowledge_admin.agent_sessions import NO_MODEL
from knowledge_admin.agents import agent, load_credentials
from knowledge_admin.event_handlers_api import PER_RUN
from knowledge_admin.knowledge_agents.config import get_agent_config, model_fits, per_run
from knowledge_admin.knowledge_agents.native_sessions import failure
from knowledge_admin.knowledge_agents.session_worker import clean_text
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

POLL_SECONDS = 2
LEASE_MINUTES = 15
TURN_SECONDS = 600
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = {1: 30, 2: 120}  # before the second and the third attempt
MAX_ERROR = 2000
MAX_BODY = 200_000
NO_AGENT = 'Agent not found.'
DISABLED = 'Agent is disabled.'
LOST = 'The worker stopped while running this task, and it has no attempts left.'

CLAIM = f"""UPDATE ka.agent_tasks t SET status='running', worker_id=%(worker)s,
    lease_expires_at=now() + interval '{LEASE_MINUTES} minutes', started_at=coalesce(t.started_at, now()),
    attempts=t.attempts + 1
    FROM (SELECT id FROM ka.agent_tasks
          WHERE (status='queued' AND next_attempt_at <= now()) OR (status='running' AND lease_expires_at < now())
          ORDER BY next_attempt_at, id LIMIT %(limit)s FOR UPDATE SKIP LOCKED) due
    WHERE t.id=due.id RETURNING t.*"""
HELD = """SELECT id FROM ka.agent_tasks WHERE id=%s AND status='running' AND worker_id=%s AND lease_expires_at=%s
    FOR UPDATE"""


class NoRetry(Exception):
    """A task that trying again cannot fix; the message becomes its failed notification."""


def claim(store: RepositoryStore, worker_id: str, limit: int) -> list[dict[str, Any]]:
    """Up to `limit` due tasks — queued and due, or running with an expired lease — now running for this worker."""
    if limit < 1:
        return []
    with store.connect() as conn:
        return conn.execute(CLAIM, {'worker': worker_id, 'limit': limit}).fetchall()


def record_key(task: dict[str, Any]) -> str:
    """One run record per attempt (Agent runs keys are unique per key and sequence)."""
    return f"task:{task['id']}:{task['attempts']}"


def load(store: RepositoryStore, task: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The agent's current configuration for one call — no experts (a task runs alone) and recorded in Agent runs as
    an event task — and its decrypted credentials. NoRetry: a problem that trying again cannot fix."""
    try:
        config = get_agent_config(task['agent_id'], conninfo=store.conninfo)
    except LookupError:
        raise NoRetry(NO_AGENT) from None
    except ValueError as exc:
        raise NoRetry(str(exc)) from None
    if per_run(config):
        raise NoRetry(PER_RUN)
    if not config['enabled']:
        raise NoRetry(DISABLED)
    model = config['model']
    if model is None or not model['enabled'] or not model_fits(model):
        raise NoRetry(NO_MODEL)
    config['experts'] = []
    config['record'] = {'run_key': record_key(task), 'project_id': str(task['project_id']), 'kind': 'event_task',
                        'title': task['title']}
    try:
        return config, load_credentials(store, config)
    except ValueError as exc:  # a disabled model, SSH server or MCP server, or a missing credential
        raise NoRetry(str(exc)) from None


def finish(store: RepositoryStore, task: dict[str, Any], outcome: str, text: str) -> bool:
    """End the attempt while this worker still holds the task (same worker and lease): `retry` queues it again after
    the backoff; `succeeded`/`failed` write the notification. False: the lease was lost and nothing is recorded."""
    text = text.replace('\x00', '')
    with store.connect() as conn:
        if conn.execute(HELD, (task['id'], task['worker_id'], task['lease_expires_at'])).fetchone() is None:
            return False
        if outcome == 'retry':
            conn.execute("""UPDATE ka.agent_tasks SET status='queued', worker_id=NULL, lease_expires_at=NULL, error=%s,
                next_attempt_at=now() + make_interval(secs => %s) WHERE id=%s""",
                (text[:MAX_ERROR], BACKOFF_SECONDS[task['attempts']], task['id']))
            return True
        notification = conn.execute("""INSERT INTO ka.notifications(project_id,kind,status,title,body,event_id,handler_id,
            agent_id,agent_task_id) VALUES (%s,'agent.response',%s,%s,%s,(SELECT id FROM ka.events WHERE id=%s),%s,%s,%s)
            RETURNING id""", (task['project_id'], outcome, task['title'], text[:MAX_BODY], task['event_id'],
                              task['handler_id'], task['agent_id'], task['id'])).fetchone()['id']
        conn.execute("""UPDATE ka.agent_tasks SET status=%s, worker_id=NULL, lease_expires_at=NULL, finished_at=now(),
            notification_id=%s, error=%s WHERE id=%s""",
            (outcome, notification, None if outcome == 'succeeded' else text[:MAX_ERROR], task['id']))
    return True


def release(store: RepositoryStore, task: dict[str, Any]) -> None:
    """Give a task back when the worker stops during an attempt: queued again, the attempt not counted."""
    with store.connect() as conn:
        conn.execute("""UPDATE ka.agent_tasks SET status='queued', attempts=attempts - 1, worker_id=NULL,
            lease_expires_at=NULL WHERE id=%s AND status='running' AND worker_id=%s AND lease_expires_at=%s""",
            (task['id'], task['worker_id'], task['lease_expires_at']))


async def run_task(store: RepositoryStore, task: dict[str, Any], run_agent=agent) -> None:
    """Run one claimed task to the end of its attempt: a notification, or queued again for another attempt."""
    if task['attempts'] > MAX_ATTEMPTS:  # its lease ran out during the last attempt
        await asyncio.to_thread(finish, store, task, 'failed', LOST)
        return
    secrets: list[str] = []
    try:
        config, credentials = await asyncio.to_thread(load, store, task)
        secrets[:] = [value for value in credentials.values() if value]
        result = await run_agent(config, task['prompt'], credentials=credentials, stream=False,
                                 turn_timeout_seconds=TURN_SECONDS)
        outcome, text = 'succeeded', clean_text(str(result.final_output), secrets)
    except NoRetry as exc:
        outcome, text = 'failed', str(exc)
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(release, store, task))
        raise
    except Exception as exc:
        text = failure(exc, secrets)
        outcome = 'retry' if task['attempts'] < MAX_ATTEMPTS else 'failed'
        logger.warning('Agent task %s attempt %s failed (%s)', task['id'], task['attempts'], type(exc).__name__)
    await asyncio.to_thread(finish, store, task, outcome, text)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_agent_tasks.py`
Expected: PASS (12 tests including the parametrized ones).

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/agent_tasks.py services/admin-api/tests/test_agent_tasks.py
git commit -m "feat(agents): agent tasks run once without streaming and end as notifications

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The task runner loop in agent-session-worker

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agent_tasks.py` (add `serve`, `guarded`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py` (`main()` runs both loops)
- Modify: `services/admin-api/tests/test_agent_tasks.py` (loop tests)

**Interfaces:**
- Consumes: Task 3's `claim`, `run_task`, `release`.
- Produces: `async serve(store, run_agent=agent, *, stopped: asyncio.Event, worker_id: str | None = None,
  limit: int | None = None, poll: float = POLL_SECONDS) -> None` — limit from `AGENT_TASK_MAX` (default 2).

- [ ] **Step 1: Write the failing tests**

Append to `services/admin-api/tests/test_agent_tasks.py`:

```python
def serve_until(t, fake, *, ready, limit=2):
    """Run the loop until `ready()` holds (checked every 50 ms, at most 10 s), then stop it."""
    async def scenario():
        stopped = asyncio.Event()
        runner = asyncio.create_task(agent_tasks.serve(t.store, fake, stopped=stopped, worker_id='w1', limit=limit, poll=0.05))
        for _ in range(200):
            if await asyncio.to_thread(ready):
                break
            await asyncio.sleep(0.05)
        stopped.set()
        await asyncio.wait_for(runner, 10)
    asyncio.run(scenario())


class Slow:
    """A stand-in agent that never answers; counts how many calls are running."""

    def __init__(self):
        self.running = 0

    async def __call__(self, config, task, **options):
        self.running += 1
        await asyncio.Event().wait()


def test_the_loop_runs_due_tasks(tester):
    t = tester
    queued = queue(t)
    serve_until(t, Agent('Done.'), ready=lambda: task(t.store, queued['id'])['status'] == 'succeeded')
    assert task(t.store, queued['id'])['status'] == 'succeeded'


def test_the_loop_runs_at_most_limit_tasks_and_gives_them_back_when_stopped(tester):
    t = tester
    ids = [queue(t)['id'] for _ in range(3)]
    slow = Slow()
    serve_until(t, slow, limit=2, ready=lambda: slow.running == 2)
    assert slow.running == 2
    rows = [task(t.store, task_id) for task_id in ids]
    assert sorted((row['status'], row['attempts']) for row in rows) == [('queued', 0)] * 3  # released on stop


def test_the_loop_survives_database_errors(tester, monkeypatch):
    t = tester
    queued = queue(t)
    real, calls = agent_tasks.claim, []

    def flaky(*args):
        calls.append(1)
        if len(calls) == 1:
            raise psycopg.OperationalError('database down')
        return real(*args)
    monkeypatch.setattr(agent_tasks, 'claim', flaky)
    serve_until(t, Agent('Done.'), ready=lambda: task(t.store, queued['id'])['status'] == 'succeeded')
    assert len(calls) >= 2 and task(t.store, queued['id'])['status'] == 'succeeded'


def test_the_limit_comes_from_the_environment(tester, monkeypatch):
    t = tester
    monkeypatch.setenv('AGENT_TASK_MAX', '1')
    ids = [queue(t)['id'] for _ in range(2)]
    slow = Slow()

    async def scenario():
        stopped = asyncio.Event()
        runner = asyncio.create_task(agent_tasks.serve(t.store, slow, stopped=stopped, worker_id='w1', poll=0.05))
        await asyncio.sleep(0.5)
        stopped.set()
        await asyncio.wait_for(runner, 10)
    asyncio.run(scenario())
    assert slow.running == 1
    assert all(task(t.store, task_id)['status'] == 'queued' for task_id in ids)
```

Add `import psycopg` to the test module's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_agent_tasks.py -k loop or limit`
Expected: FAIL — `AttributeError: module 'knowledge_admin.agent_tasks' has no attribute 'serve'`.

- [ ] **Step 3: Implement**

Append to `agent_tasks.py`:

```python
async def guarded(store: RepositoryStore, task: dict[str, Any], run_agent) -> None:
    """run_task that never takes the loop down: a task that cannot be finished keeps its lease and is claimed again
    when the lease runs out."""
    try:
        await run_task(store, task, run_agent)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('Agent task %s could not be finished', task['id'])


async def serve(store: RepositoryStore, run_agent=agent, *, stopped: asyncio.Event, worker_id: str | None = None,
                limit: int | None = None, poll: float = POLL_SECONDS) -> None:
    """Run due tasks, AGENT_TASK_MAX of them at once (default 2), until stopped; running ones are then given back."""
    worker_id = worker_id or f'{socket.gethostname()}:{os.getpid()}:tasks'
    limit = limit or int(os.environ.get('AGENT_TASK_MAX', '2'))
    running: set[asyncio.Task] = set()
    while not stopped.is_set():
        try:
            for task in await asyncio.to_thread(claim, store, worker_id, limit - len(running)):
                job = asyncio.create_task(guarded(store, task, run_agent))
                running.add(job)
                job.add_done_callback(running.discard)
        except psycopg.Error:
            print('Agent task runner database unavailable; retrying', flush=True)
        except Exception:
            logger.exception('Agent task runner loop failed; retrying')
        try:
            await asyncio.wait_for(stopped.wait(), poll)
        except asyncio.TimeoutError:
            pass
    for job in list(running):
        job.cancel()
    await asyncio.gather(*running, return_exceptions=True)
```

In `knowledge_agents/native_sessions.py` `main()`, change the inner `run()` so it serves sessions and tasks together
(import inside the function: `agent_tasks` imports this module):

```python
    async def run() -> None:
        from knowledge_admin import agent_tasks  # it builds on this module (failure)
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stopped.set)
        await asyncio.gather(serve(store, stopped=stopped), agent_tasks.serve(store, stopped=stopped))
```

Extend `main()`'s docstring with: `It also runs event handlers' agent tasks (agent_tasks.serve).`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_agent_tasks.py tests/test_test_session_worker.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/agent_tasks.py \
  services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py services/admin-api/tests/test_agent_tasks.py
git commit -m "feat(agents): agent-session-worker runs agent tasks beside sessions

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Notifications API

**Files:**
- Create: `services/admin-api/src/knowledge_admin/notifications_api.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py` (import and include after the event handlers router)
- Create: `services/admin-api/tests/test_notifications_api.py`

**Interfaces:**
- Consumes: `ka.notifications`, `ka.agent_tasks`, `ka.events`; `events_api.like`.
- Produces: `GET /api/notifications?status&q&offset&limit` → `{notifications: [{id, created_at, kind, status, title,
  preview, event_id, agent_id, agent_name}], next_offset}`; `GET /api/notifications/{id}` → `{notification: {…row,
  body, handler_id, event: {id, type, subject} | null, task: {id, attempts, created_at, finished_at} | null}}`.

- [ ] **Step 1: Write the failing tests**

`services/admin-api/tests/test_notifications_api.py`:

```python
"""Administration → Notifications: newest first, status filter, literal search, offset paging, one notification."""
from uuid import uuid4

from test_api import _client
from test_events_api import seed
from test_repositories import HEADERS


def add(store, title='Triage · jira.ticket.updated DEMO-1', body='Done.', status='succeeded', task=False, **values) -> int:
    with store.connect() as conn:
        project = values.pop('project_id', None) or store.project(conn, 'shop')
        key = None
        if task:
            key = f'agent-task:{values.get("event_id") or 1}:{uuid4()}'
            conn.execute("""INSERT INTO ka.agent_tasks(id,project_id,agent_id,agent_name,title,prompt,event_id,status,attempts,
                finished_at) VALUES (%s,%s,'triage','Triage',%s,'Hi',%s,'succeeded',2,now())""",
                (key, project, title, values.get('event_id')))
        return conn.execute("""INSERT INTO ka.notifications(project_id,kind,status,title,body,event_id,agent_id,agent_task_id)
            VALUES (%s,'agent.response',%s,%s,%s,%s,'triage',%s) RETURNING id""",
            (project, status, title, body, values.get('event_id'), key)).fetchone()['id']


def test_newest_first_with_offsets(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = [add(store, title=f'T{n}') for n in range(3)]
    first = client.get('/api/notifications', headers=HEADERS, params={'limit': 2}).json()
    assert [row['id'] for row in first['notifications']] == [ids[2], ids[1]] and first['next_offset'] == 2
    rest = client.get('/api/notifications', headers=HEADERS, params={'limit': 2, 'offset': 2}).json()
    assert [row['id'] for row in rest['notifications']] == [ids[0]] and rest['next_offset'] is None


def test_rows_carry_a_preview_and_the_agent(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [event] = seed(store, 1)
    add(store, body='x' * 300, task=True, event_id=event)
    [row] = client.get('/api/notifications', headers=HEADERS).json()['notifications']
    assert set(row) == {'id', 'created_at', 'kind', 'status', 'title', 'preview', 'event_id', 'agent_id', 'agent_name'}
    assert (row['preview'], row['agent_id'], row['agent_name'], row['event_id'], row['kind']) == (
        'x' * 200, 'triage', 'Triage', event, 'agent.response')


def test_status_filter(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    add(store, status='succeeded')
    failed = add(store, status='failed', body='Agent is disabled.')
    rows = client.get('/api/notifications', headers=HEADERS, params={'status': 'failed'}).json()['notifications']
    assert [row['id'] for row in rows] == [failed]
    assert client.get('/api/notifications', headers=HEADERS, params={'status': 'queued'}).status_code == 400


def test_search_matches_literally(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    percent = add(store, title='Triage · 100% done')
    add(store, title='Triage · 1000 done')
    underscore = add(store, title='Triage · a_b')
    add(store, title='Triage · axb')
    found = lambda q: [row['id'] for row in client.get('/api/notifications', headers=HEADERS, params={'q': q}).json()['notifications']]
    assert found('100%') == [percent]
    assert found('A_B') == [underscore]


def test_one_notification_with_its_event_and_task(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [event] = seed(store, 1)
    note = add(store, body='The full answer.', task=True, event_id=event)
    one = client.get(f'/api/notifications/{note}', headers=HEADERS).json()['notification']
    assert (one['body'], one['event'], one['task']['attempts'], one['agent_name']) == (
        'The full answer.', {'id': event, 'type': 'jira.ticket.created', 'subject': 'DEMO-1'}, 2, 'Triage')
    assert one['task']['id'].startswith(f'agent-task:{event}:') and one['task']['finished_at'] is not None


def test_a_notification_whose_event_was_deleted(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    note = add(store)
    one = client.get(f'/api/notifications/{note}', headers=HEADERS).json()['notification']
    assert (one['event'], one['task'], one['event_id'], one['agent_name']) == (None, None, None, 'triage')


def test_missing_and_other_projects(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other-n','Other') RETURNING id").fetchone()['id']
    hidden = add(store, project_id=other)
    assert client.get('/api/notifications', headers=HEADERS).json()['notifications'] == []
    response = client.get(f'/api/notifications/{hidden}', headers=HEADERS)
    assert (response.status_code, response.json()['error']['message']) == (404, 'Notification not found.')
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_notifications_api.py`
Expected: FAIL — 404 on `/api/notifications`.

- [ ] **Step 3: Implement**

`services/admin-api/src/knowledge_admin/notifications_api.py`:

```python
"""Administration → Notifications: what event-triggered agent tasks produced — the agent's answer, or why it failed.
One shared, read-only list for every user; agent_tasks.py writes it."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from knowledge_admin.events_api import like
from knowledge_admin.repositories import RepositoryStore

PAGE, MAX_PAGE, MAX_OFFSET, PREVIEW = 50, 200, 100000, 200
NOT_FOUND = 'Notification not found.'
LISTED = f"""n.id,n.created_at,n.kind,n.status,n.title,left(n.body,{PREVIEW}) AS preview,n.event_id,n.agent_id,
    coalesce(t.agent_name,n.agent_id) AS agent_name"""
FROM = 'FROM ka.notifications n LEFT JOIN ka.agent_tasks t ON t.id=n.agent_task_id'


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Notification storage is not configured')
        return store

    @router.get('/notifications')
    def list_notifications(status: Literal['succeeded', 'failed'] | None = None,
                           q: str | None = Query(None, max_length=200), offset: int = Query(0, ge=0, le=MAX_OFFSET),
                           limit: int = Query(PAGE, ge=1, le=MAX_PAGE)) -> dict:
        db = database()
        with db.connect() as conn:
            where, values = ['n.project_id=%s'], [db.project(conn, app)]
            if status:
                where.append('n.status=%s'); values.append(status)
            if q and q.strip():
                where.append("n.title ILIKE %s ESCAPE '\\'"); values.append(like(q.strip()))
            rows = conn.execute(f"""SELECT {LISTED} {FROM} WHERE {' AND '.join(where)}
                ORDER BY n.id DESC LIMIT %s OFFSET %s""", (*values, limit + 1, offset)).fetchall()
        more = len(rows) > limit
        return {'notifications': rows[:limit], 'next_offset': offset + limit if more else None}

    @router.get('/notifications/{notification_id}')
    def one(notification_id: int) -> dict:
        db = database()
        with db.connect() as conn:
            row = conn.execute(f"""SELECT {LISTED},n.body,n.handler_id,
                CASE WHEN e.id IS NULL THEN NULL ELSE jsonb_build_object('id',e.id,'type',e.type,'subject',e.subject) END AS event,
                CASE WHEN t.id IS NULL THEN NULL ELSE jsonb_build_object('id',t.id,'attempts',t.attempts,
                    'created_at',t.created_at,'finished_at',t.finished_at) END AS task
                {FROM} LEFT JOIN ka.events e ON e.id=n.event_id WHERE n.id=%s AND n.project_id=%s""",
                (notification_id, db.project(conn, app))).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return {'notification': row}

    return router
```

In `api.py`, add `notifications_api` to the `from knowledge_admin import …` list and, after
`router.include_router(event_handlers_api.build_api(app_name, repository_store))`, add
`router.include_router(notifications_api.build_api(app_name, repository_store))`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_notifications_api.py tests/test_events_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/notifications_api.py services/admin-api/src/knowledge_admin/api.py \
  services/admin-api/tests/test_notifications_api.py
git commit -m "feat(api): notifications list and detail

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: UI — Administration → Notifications

**Files:**
- Create: `services/admin-ui/app/lib/notifications.ts`
- Create: `services/admin-ui/app/components/NotificationsView.tsx`
- Create: `services/admin-ui/app/routes/notifications.tsx`
- Modify: `services/admin-ui/app/routes.ts` (route after `events/handlers`)
- Modify: `services/admin-ui/app/components/AdminLayout.tsx` (sidebar item after Events, title, description)
- Create: `services/admin-ui/app/components/__tests__/notifications.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`

**Interfaces:**
- Consumes: Task 5 API.
- Produces (lib): `NotificationStatus`, `NotificationRow`, `NotificationDetail`, `NotificationFilters`,
  `NO_NOTIFICATION_FILTERS`, `NOTIFICATION_STATUS_LABELS`, `getNotifications(filters, offset?, signal?)`,
  `getNotification(id, signal?)`. Page address `/notifications`; `/notifications?id=<id>` opens that notification.

- [ ] **Step 1: Write the failing tests**

`services/admin-ui/app/components/__tests__/notifications.test.tsx`:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import NotificationsView from "../NotificationsView";

const row = (id: number, extra = {}) => ({ id, created_at: "2026-10-09T10:00:00Z", kind: "agent.response", status: "succeeded",
  title: `Triage · jira.ticket.updated DEMO-${id}`, preview: `Answer ${id}\nmore`, event_id: 40 + id, agent_id: "triage",
  agent_name: "Triage", ...extra });
const detail = (id: number, extra = {}) => ({ ...row(id), body: `Answer ${id}\nmore lines`, handler_id: "h-1",
  event: { id: 40 + id, type: "jira.ticket.updated", subject: `DEMO-${id}` },
  task: { id: `agent-task:${40 + id}:h-1`, attempts: 2, created_at: "2026-10-09T10:00:00Z", finished_at: "2026-10-09T10:01:00Z" }, ...extra });

function api(pages: Record<string, unknown>) {
  const fetch = vi.fn(async (url: string) => {
    const body = pages[url];
    if (body === undefined) throw new Error(`unexpected ${url}`);
    return new Response(JSON.stringify(body));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const view = (entry = "/notifications") => render(<MemoryRouter initialEntries={[entry]}><NotificationsView /></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

test("lists notifications newest first with status and the answer's first line, and loads more", async () => {
  const user = userEvent.setup();
  api({ "/api/notifications": { notifications: [row(3, { status: "failed", preview: "Agent is disabled." }), row(2)], next_offset: 2 },
    "/api/notifications?offset=2": { notifications: [row(2), row(1)], next_offset: null } });
  view();
  const table = await screen.findByRole("table");
  expect(within(table).getByText("Failed")).toBeInTheDocument();
  expect(within(table).getByText("Answer 2")).toBeInTheDocument();
  expect(within(table).queryByText(/more/)).toBeNull();  // only the first line
  await user.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText("Triage · jira.ticket.updated DEMO-1")).toBeInTheDocument();
  expect(screen.getAllByText("Triage · jira.ticket.updated DEMO-2")).toHaveLength(1);  // de-duplicated by id
});

test("filters by status and searches the title", async () => {
  const user = userEvent.setup();
  const fetch = api({ "/api/notifications": { notifications: [row(1)], next_offset: null },
    "/api/notifications?status=failed": { notifications: [], next_offset: null },
    "/api/notifications?status=failed&q=DEMO": { notifications: [row(2, { status: "failed" })], next_offset: null } });
  view();
  await screen.findByText("Answer 1");
  await user.click(screen.getByRole("combobox", { name: "Status" }));
  await user.click(screen.getByRole("option", { name: "Failed" }));
  expect(await screen.findByText("No notifications match these filters.")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Search title"), "DEMO");
  expect(await screen.findByText("Answer 2")).toBeInTheDocument();
  expect(fetch.mock.calls.map(([url]) => url)).toContain("/api/notifications?status=failed&q=DEMO");
});

test("opening a notification shows the full answer, the agent, attempts and a link to its event", async () => {
  const user = userEvent.setup();
  api({ "/api/notifications": { notifications: [row(1)], next_offset: null }, "/api/notifications/1": { notification: detail(1) } });
  view();
  await user.click(await screen.findByText("Triage · jira.ticket.updated DEMO-1"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText(/Answer 1\s+more lines/)).toBeInTheDocument();
  expect(within(sheet).getByText("Triage")).toBeInTheDocument();
  expect(within(sheet).getByText("2")).toBeInTheDocument();
  expect(within(sheet).getByRole("link", { name: "Open event" })).toHaveAttribute("href", "/events?event=41");
});

test("a notification whose event is gone has no event link", async () => {
  const user = userEvent.setup();
  api({ "/api/notifications": { notifications: [row(1, { event_id: null })], next_offset: null },
    "/api/notifications/1": { notification: detail(1, { event_id: null, event: null }) } });
  view();
  await user.click(await screen.findByText("Triage · jira.ticket.updated DEMO-1"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).queryByRole("link", { name: "Open event" })).toBeNull();
});

test("an address with ?id= opens that notification", async () => {
  api({ "/api/notifications": { notifications: [], next_offset: null }, "/api/notifications/5": { notification: detail(5) } });
  view("/notifications?id=5");
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText(/Answer 5/)).toBeInTheDocument();
});

test("empty and error states", async () => {
  api({ "/api/notifications": { notifications: [], next_offset: null } });
  view();
  expect(await screen.findByText("No notifications yet. A handler that sends events to an agent puts the agent's answers here.")).toBeInTheDocument();
  vi.unstubAllGlobals();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { message: "Database down" } }), { status: 503 })));
  view();
  await waitFor(() => expect(screen.getByText("Database down")).toBeInTheDocument());
});
```

In `__tests__/adminLayout.test.tsx`, in the first test add after the Events link assertion:

```tsx
  expect(screen.getByRole("link", { name: "Notifications" })).toHaveAttribute("href", "/notifications");
```

and add:

```tsx
test("the Notifications page has its heading", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))));
  render(<MemoryRouter initialEntries={["/notifications"]}><AdminLayout><div>List</div></AdminLayout></MemoryRouter>);
  expect(await screen.findByText("List")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Notifications" })).toHaveAttribute("data-active", "true");
  expect(screen.getByRole("heading", { level: 1, name: "Notifications" })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/notifications.test.tsx app/components/__tests__/adminLayout.test.tsx`
Expected: FAIL — `Failed to resolve import "../NotificationsView"`; no Notifications link.

- [ ] **Step 3: Implement**

`services/admin-ui/app/lib/notifications.ts`:

```ts
import { request } from "./api";

export type NotificationStatus = "succeeded" | "failed";
export interface NotificationRow { id: number; created_at: string; kind: string; status: NotificationStatus; title: string;
  preview: string; event_id: number | null; agent_id: string | null; agent_name: string | null }
export interface NotificationDetail extends NotificationRow { body: string; handler_id: string | null;
  event: { id: number; type: string; subject: string } | null;
  task: { id: string; attempts: number; created_at: string; finished_at: string | null } | null }
export interface NotificationFilters { status: string; q: string }

export const NO_NOTIFICATION_FILTERS: NotificationFilters = { status: "", q: "" };
export const NOTIFICATION_STATUS_LABELS: Record<NotificationStatus, string> = { succeeded: "Succeeded", failed: "Failed" };

export function getNotifications(filters: NotificationFilters, offset = 0, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (filters.status) query.set("status", filters.status);
  if (filters.q.trim()) query.set("q", filters.q.trim());
  if (offset > 0) query.set("offset", String(offset));
  const suffix = query.toString();
  return request<{ notifications: NotificationRow[]; next_offset: number | null }>(`/api/notifications${suffix ? `?${suffix}` : ""}`, { signal });
}
export const getNotification = (id: number, signal?: AbortSignal) =>
  request<{ notification: NotificationDetail }>(`/api/notifications/${id}`, { signal });
```

`services/admin-ui/app/components/NotificationsView.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Bell, RefreshCw } from "lucide-react";
import { ApiError } from "../lib/api";
import { NOTIFICATION_STATUS_LABELS, NO_NOTIFICATION_FILTERS, getNotification, getNotifications,
  type NotificationDetail, type NotificationFilters, type NotificationRow, type NotificationStatus } from "../lib/notifications";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const DEBOUNCE_MS = 300;
const ALL = "all";
const when = (value: string) => new Date(value).toLocaleString();
const text = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;
const firstLine = (value: string) => value.split("\n")[0];

function StatusBadge({ status }: { status: NotificationStatus }) {
  return <Badge variant={status === "failed" ? "destructive" : "secondary"}>{NOTIFICATION_STATUS_LABELS[status] ?? status}</Badge>;
}

/** Administration → Notifications: the answers agents gave to events (or why a call failed), newest first. */
export default function NotificationsView() {
  const [params, setParams] = useSearchParams();
  const linked = params.get("id");
  const [filters, setFilters] = useState<NotificationFilters>(NO_NOTIFICATION_FILTERS);
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<NotificationRow[] | null>(null);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [selected, setSelected] = useState<NotificationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);
  /** Bumped whenever the first page is reloaded, so a Load more that started earlier is ignored. */
  const generation = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(filters.q), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters.q]);

  useEffect(() => {
    const controller = new AbortController();
    generation.current += 1;
    setRows(null); setNextOffset(null); setLoadingMore(false);
    getNotifications({ status: filters.status, q: query }, 0, controller.signal)
      .then(result => { setRows(result.notifications); setNextOffset(result.next_offset); setError(null); })
      .catch(reason => { if (!controller.signal.aborted) { setRows([]); setNextOffset(null); setError(text(reason, "Could not load notifications.")); } });
    return () => controller.abort();
  }, [filters.status, query, reload]);

  useEffect(() => {
    if (linked && /^\d+$/.test(linked)) void open(Number(linked));
  }, [linked]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function loadMore() {
    if (nextOffset === null) return;
    const started = generation.current;
    setLoadingMore(true);
    try {
      const result = await getNotifications({ status: filters.status, q: query }, nextOffset);
      if (generation.current !== started) return;
      setRows(current => current ? [...current, ...result.notifications.filter(item => !current.some(existing => existing.id === item.id))] : current);
      setNextOffset(result.next_offset);
    } catch (reason) { if (generation.current === started) setError(text(reason, "Could not load more notifications.")); }
    finally { if (generation.current === started) setLoadingMore(false); }
  }
  async function open(id: number) {
    try { setSelected((await getNotification(id)).notification); }
    catch (reason) { setError(text(reason, "Could not load the notification.")); }
  }
  function close() {
    setSelected(null);
    if (params.has("id")) setParams(current => { current.delete("id"); return current; }, { replace: true });
  }

  const filtered = Boolean(filters.status || filters.q.trim());
  return <div className="flex flex-col gap-4">
    <div className="flex flex-wrap items-end gap-3">
      <Field className="w-full sm:w-40"><FieldLabel htmlFor="notifications-status">Status</FieldLabel>
        <Select value={filters.status || ALL} onValueChange={next => setFilters(current => ({ ...current, status: next === ALL ? "" : next }))}>
          <SelectTrigger id="notifications-status" aria-label="Status" className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value={ALL}>All statuses</SelectItem>
            <SelectItem value="succeeded">Succeeded</SelectItem><SelectItem value="failed">Failed</SelectItem></SelectGroup></SelectContent>
        </Select></Field>
      <Field className="w-full sm:w-64"><FieldLabel htmlFor="notifications-search">Search title</FieldLabel>
        <Input id="notifications-search" value={filters.q} onChange={event => setFilters(current => ({ ...current, q: event.target.value }))} /></Field>
      <Button variant="outline" onClick={() => setReload(count => count + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
    </div>
    {error && <Alert variant="destructive"><AlertTitle>Notifications</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {rows === null && !error && <Skeleton className="h-32 w-full" />}
    {rows?.length === 0 && !error && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Bell /></EmptyMedia><EmptyTitle>Notifications</EmptyTitle>
      <EmptyDescription>{filtered ? "No notifications match these filters." : "No notifications yet. A handler that sends events to an agent puts the agent's answers here."}</EmptyDescription></EmptyHeader></Empty>}
    {rows && rows.length > 0 && <div className="overflow-x-auto rounded-md border">
      <Table>
        <TableHeader><TableRow><TableHead>Time</TableHead><TableHead>Status</TableHead><TableHead>Title</TableHead><TableHead>Answer</TableHead></TableRow></TableHeader>
        <TableBody>{rows.map(item => <TableRow key={item.id} tabIndex={0} className="cursor-pointer" onClick={() => void open(item.id)}
          onKeyDown={event => { if (event.key === "Enter") void open(item.id); }}>
          <TableCell className="whitespace-nowrap">{when(item.created_at)}</TableCell>
          <TableCell><StatusBadge status={item.status} /></TableCell>
          <TableCell className="font-medium">{item.title}</TableCell>
          <TableCell className="max-w-md truncate text-muted-foreground">{firstLine(item.preview)}</TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </div>}
    {nextOffset !== null && <div className="flex justify-center"><Button variant="outline" disabled={loadingMore} onClick={() => void loadMore()}>Load more</Button></div>}
    <Sheet open={selected !== null} onOpenChange={next => { if (!next) close(); }}>
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
        {selected && <>
          <SheetHeader><SheetTitle>{selected.title}</SheetTitle><SheetDescription>{when(selected.created_at)}</SheetDescription></SheetHeader>
          <div className="flex flex-col gap-4 px-4 pb-4 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
              <dt className="text-muted-foreground">Status</dt><dd><StatusBadge status={selected.status} /></dd>
              <dt className="text-muted-foreground">Agent</dt><dd>{selected.agent_name ?? selected.agent_id ?? "—"}</dd>
              {selected.task && <><dt className="text-muted-foreground">Attempts</dt><dd>{selected.task.attempts}</dd></>}
              {selected.event && <><dt className="text-muted-foreground">Event</dt>
                <dd><Link className="text-primary underline underline-offset-4" to={`/events?event=${selected.event.id}`}>Open event</Link>
                  {" "}<span className="text-muted-foreground">{selected.event.subject} · {selected.event.type}</span></dd></>}
            </dl>
            <pre className="whitespace-pre-wrap break-words rounded-md bg-muted p-3 text-sm">{selected.body}</pre>
          </div>
        </>}
      </SheetContent>
    </Sheet>
  </div>;
}
```

`services/admin-ui/app/routes/notifications.tsx`:

```tsx
import AdminLayout from "../components/AdminLayout";
import NotificationsView from "../components/NotificationsView";

export default function NotificationsRoute() {
  return <AdminLayout><NotificationsView /></AdminLayout>;
}
```

`routes.ts`: after `route("events/handlers", "./routes/eventHandlers.tsx"),` add
`route("notifications", "./routes/notifications.tsx"),`.

`AdminLayout.tsx`:
- import `Bell` from `lucide-react` (add to the existing import list);
- after `const eventsPage = …` add `const notificationsPage = pathname === "/notifications";`;
- prepend `notificationsPage ? "Notifications" : ` to the `title` chain and
  `notificationsPage ? "Answers agents gave to events, and the calls that failed." : ` to the `description` chain;
- change the Events description text to
  `"Things that happened at your sources, and handlers that turn them into new events or agent answers."`;
- after the Events sidebar item add:

```tsx
          <SidebarMenuItem><SidebarMenuButton asChild isActive={notificationsPage}><Link to="/notifications"><Bell /><span>Notifications</span></Link></SidebarMenuButton></SidebarMenuItem>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/notifications.test.tsx app/components/__tests__/adminLayout.test.tsx && npx react-router typegen && npx tsc --noEmit`
Expected: PASS, tsc exit 0.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/notifications.ts services/admin-ui/app/components/NotificationsView.tsx \
  services/admin-ui/app/routes/notifications.tsx services/admin-ui/app/routes.ts services/admin-ui/app/components/AdminLayout.tsx \
  services/admin-ui/app/components/__tests__/notifications.test.tsx services/admin-ui/app/components/__tests__/adminLayout.test.tsx
git commit -m "feat(admin-ui): Administration → Notifications

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: UI — task status on handler runs, event links, handler help, run kind

**Files:**
- Modify: `services/admin-ui/app/lib/events.ts` (`HandlerRun.agent_task`, `TASK_STATUS_LABELS`)
- Modify: `services/admin-ui/app/components/EventsView.tsx` (task status + Open notification; `?event=` opens the sheet)
- Modify: `services/admin-ui/app/components/HandlerSheet.tsx` (help and warning text)
- Modify: `services/admin-ui/app/components/RunsView.tsx` (`event_task: 'Event task'`)
- Modify: `services/admin-ui/app/components/__tests__/events.test.tsx`, `handlers.test.tsx`, `runs.test.tsx`

**Interfaces:**
- Consumes: Task 2's `agent_task` on runs; Task 6's `/notifications?id=` address.
- Produces: `/events?event=<id>` opens that event's sheet (used by Notifications' **Open event**).

- [ ] **Step 1: Write the failing tests**

In `__tests__/events.test.tsx` add:

```tsx
test("a Send to agent run shows its task status and opens its notification", async () => {
  const user = userEvent.setup();
  const run = { handler_id: "h-1", handler_name: "Ask Triage", status: "succeeded", message: "Queued for Triage.", emitted_event_id: null,
    agent_session_id: null, agent_id: "triage", created_at: "2026-10-09T10:00:06Z",
    agent_task: { id: "agent-task:7:h-1", status: "succeeded", attempts: 2, notification_id: 9 } };
  const queued = { ...run, handler_name: "Ask Ops", agent_task: { id: "agent-task:7:h-2", status: "queued", attempts: 0, notification_id: null } };
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(7)], next_offset: null },
    "/api/events/7": { event: detail(7, { runs: [run, queued] }) } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Queued for Triage.")).toBeInTheDocument();
  expect(within(sheet).getByText("Succeeded · 2 attempts")).toBeInTheDocument();
  expect(within(sheet).getByText("Queued")).toBeInTheDocument();
  const links = within(sheet).getAllByRole("link", { name: "Open notification" });
  expect(links).toHaveLength(1);
  expect(links[0]).toHaveAttribute("href", "/notifications?id=9");
});

test("an address with ?event= opens that event", async () => {
  api({ "/api/events/facets": FACETS, "/api/events": { events: [], next_offset: null }, "/api/events/4": { event: detail(4) } });
  render(<MemoryRouter initialEntries={["/events?event=4"]}><EventsView /></MemoryRouter>);
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText(/DEMO-4/)).toBeInTheDocument();
});
```

Every existing run object in `events.test.tsx` fixtures gains `agent_task: null` (add the key where runs are built).

In `__tests__/handlers.test.tsx`, rename the broad-pattern test to
`"a broad Send to agent pattern warns that each matching event runs the agent"` and change its `warning` constant to
`"Runs the agent once for every matching event."`; in the same test, after selecting **Send to agent**, add:

```tsx
  expect(within(sheet).getByText("Enabled agents with fixed resources. The agent answers once, without a conversation. The answer goes to Notifications.")).toBeInTheDocument();
```

In `__tests__/runs.test.tsx`, the Run type option list becomes:

```tsx
    ['', 'All'], ['test', 'Agent test'], ['test_session', 'Test session'], ['event_session', 'Event session'],
    ['event_task', 'Event task'], ['native_session', 'Native session']]);
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/events.test.tsx app/components/__tests__/handlers.test.tsx app/components/__tests__/runs.test.tsx`
Expected: FAIL — no task status text, no `?event=` handling, old help text, no Event task kind.

- [ ] **Step 3: Implement**

`lib/events.ts`:

```ts
export type TaskStatus = "queued" | "running" | "succeeded" | "failed";
export interface AgentTaskRef { id: string; status: TaskStatus; attempts: number; notification_id: number | null }
export const TASK_STATUS_LABELS: Record<TaskStatus, string> = { queued: "Queued", running: "Running", succeeded: "Succeeded", failed: "Failed" };
```

and add `agent_task: AgentTaskRef | null` to `HandlerRun`.

`EventsView.tsx`:
- import `useSearchParams` from `react-router` (beside `Link`) and `TASK_STATUS_LABELS` from `../lib/events`;
- inside the component: `const [params, setParams] = useSearchParams(); const linked = params.get("event");` and

```tsx
  useEffect(() => {
    if (linked && /^\d+$/.test(linked)) void open(Number(linked));
  }, [linked]);  // eslint-disable-line react-hooks/exhaustive-deps
  function close() {
    setSelected(null);
    if (params.has("event")) setParams(current => { current.delete("event"); return current; }, { replace: true });
  }
```

  and use `onOpenChange={next => { if (!next) close(); }}` on the Sheet;
- in the handler run item, after the session link, add:

```tsx
                {run.agent_task && <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={run.agent_task.status === "failed" ? "destructive" : run.agent_task.status === "succeeded" ? "secondary" : "outline"}>
                    {TASK_STATUS_LABELS[run.agent_task.status]}{run.agent_task.attempts > 1 ? ` · ${run.agent_task.attempts} attempts` : ""}</Badge>
                  {run.agent_task.notification_id !== null && <Link className="text-primary underline underline-offset-4"
                    to={`/notifications?id=${run.agent_task.notification_id}`}>Open notification</Link>}</div>}
```

`HandlerSheet.tsx`: the broad-pattern warning text becomes `Runs the agent once for every matching event.`; the agent
`FieldDescription` becomes
`Enabled agents with fixed resources. The agent answers once, without a conversation. The answer goes to Notifications.`;
update the comment above `broad` to `Send to agent on a pattern like * or jira.ticket.* can run the agent many times at
once (the first Jira pull records every ticket).`

`RunsView.tsx`: `KINDS` becomes
`{ test: 'Agent test', test_session: 'Test session', event_session: 'Event session', event_task: 'Event task', native_session: 'Native session' }`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/admin-ui && npx vitest run && npx react-router typegen && npx tsc --noEmit`
Expected: PASS (full suite; `skills.test.tsx` is a known flaky test that passes alone), tsc exit 0.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/events.ts services/admin-ui/app/components/EventsView.tsx \
  services/admin-ui/app/components/HandlerSheet.tsx services/admin-ui/app/components/RunsView.tsx \
  services/admin-ui/app/components/__tests__/events.test.tsx services/admin-ui/app/components/__tests__/handlers.test.tsx \
  services/admin-ui/app/components/__tests__/runs.test.tsx
git commit -m "feat(admin-ui): handler runs show agent task status and link to notifications

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Docs

**Files:**
- Modify: `docs/user-guide.md` (4.17, 4.18; new 4.19 Notifications)
- Modify: `docs/agents.md` (event sessions paragraph)
- Modify: `docs/migrations.md` (0090)

**Interfaces:**
- Consumes: the behaviour built in Tasks 1–7.

- [ ] **Step 1: Update `docs/user-guide.md`**

In 4.17, replace `(each handler's result, with links to the event it emitted or the agent session it started)` with
`(each handler's result, with links to the event it emitted, or — for Send to agent — the agent task's status and its
notification)`.

In 4.18 replace the **Send to agent** bullet with:

```markdown
- **Send to agent** queues one call of the chosen agent for every matching event; the **Prompt** template is the
  message. agent-session-worker runs it once — no conversation, no streaming — and puts the answer in
  **Notifications** (4.19). A call that fails is tried again 30 seconds and then 2 minutes later (3 attempts in all);
  an agent that was deleted, disabled, switched to per-run resources or left without a usable model fails at once.
  Only enabled agents with fixed resources can be chosen. A handler whose agent was deleted or no longer qualifies
  can still be turned off or edited; choosing a new agent or turning it back on checks the agent again. Processing
  the same event again never queues a second call. Calls appear in Agent runs as **Event task**.
```

In the **Careful with Send to agent on broad patterns** paragraph replace `starts its own agent session` with
`runs the agent once` and `starts one session per ticket` with `runs the agent once per ticket`, and
`multiplying sessions down a chain` with `multiplying calls down a chain`.

After 4.18 add:

```markdown
### 4.19 Notifications

Administration → **Notifications** lists what agents answered when a handler sent them an event, newest first:
Time, Status (**Succeeded** or **Failed**), Title (`<agent> · <event type> <subject>`) and the first line of the
answer. Everyone sees the same list. Filter by status or search the title; **Load more** and **Refresh** work as on
Events. Open a notification to read the whole answer (or why the call failed, after its last attempt), the agent, the
number of attempts and **Open event**, which opens the event that triggered it. On the event, the handler run shows
the call's status (Queued, Running, Succeeded or Failed) and **Open notification**.
```

- [ ] **Step 2: Update `docs/agents.md`**

Replace the bullet that begins `Sessions started by event handlers (Administration → Events → Handlers, action Send to
agent) have` with:

```markdown
- Event handlers (Administration → Events → Handlers, action Send to agent) do not start sessions: since migration
  0090 they queue an agent task (`ka.agent_tasks`), and agent-session-worker runs it once with the rendered prompt —
  `agent(config, prompt, stream=False)`, the agent's current configuration, no history, no experts or subagents, a
  10-minute limit — and writes the answer or the failure to `ka.notifications` (Administration → Notifications).
  `AGENT_TASK_MAX` (default 2) tasks run at once, beside the session slots. Each attempt is recorded as run key
  `task:<task id>:<attempt>`, kind `event_task` (**Event task** in Agent runs). Sessions with `purpose = 'event'`
  created before 0090 stay viewable in the agent's Test tab (**Event** badge).
```

- [ ] **Step 3: Update `docs/migrations.md`**

After the `## 0089_event_handlers` section add:

```markdown
## 0090_agent_tasks_notifications

`ka.agent_tasks` holds the agent calls Send-to-agent handlers queue (id `agent-task:<event>:<handler>`, so processing
an event again queues nothing new): agent, rendered prompt, status `queued`/`running`/`succeeded`/`failed`, attempts,
the next attempt's time and the running worker's lease. `ka.notifications` holds what they produced (kind
`agent.response`, status, title, body), one list for every user. `ka.event_handler_runs.agent_task_id` links a run to
its task. Deleting an event keeps its tasks and notifications (their `event_id` becomes NULL). The down migration
drops both tables, every task and notification, and the column.
```

- [ ] **Step 4: Check the docs against the code**

Run: `grep -n "event_task\|AGENT_TASK_MAX\|agent.response\|MAX_ATTEMPTS\|BACKOFF_SECONDS" services/admin-api/src/knowledge_admin/agent_tasks.py`
Expected: the values the docs state (2, 3 attempts, 30 s/2 min, 600 s, `event_task`).

- [ ] **Step 5: Commit**

```bash
git add docs/user-guide.md docs/agents.md docs/migrations.md
git commit -m "docs: agent tasks and notifications

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Self-review notes

- Spec coverage: data model (T1); processor queues, checks, `ON CONFLICT`, run link, no session (T2); event detail
  `agent_task` (T2); runner claim/lease/run once/finish/retry/no-retry/stale worker/NUL/redaction/run record (T3); loop,
  `AGENT_TASK_MAX`, never dies, worker wiring (T4); notifications API (T5); Notifications page (T6); handler-run task
  status and Open notification, handler help text (T7); docs (T8).
- Rulings (spec ambiguities resolved here):
  - `ka.agent_tasks` also snapshots `title` (the notification title) so a task whose event was deleted still has one.
  - The run key includes the attempt (`task:<id>:<attempt>`): `agent_run_events` is unique by `(run_key, seq)`, so a
    retry under one key would fail to record.
  - A task reclaimed after its lease ran out on the third attempt fails with `LOST` instead of running a fourth time.
  - On worker shutdown a running task is released (queued again, attempt not counted) instead of waiting 15 minutes
    for its lease.
  - The turn limit (600 s) is below the lease (15 min), so no lease renewal is needed.
  - The processor checks the agent from `ka.agents` (exists, enabled, fixed) instead of loading its configuration;
    the model check happens in the runner (no-retry failure).
  - Notification `agent_name` comes from the task's snapshot (`coalesce(t.agent_name, n.agent_id)`).
  - `/events?event=<id>` and `/notifications?id=<id>` are the link targets between the two pages.
  - Task run records are not deleted by "delete agent with history" (like expert-call records today).
- Type consistency: `agent_task` shape `{id, status, attempts, notification_id}` is the same in T2 (API) and T7 (UI);
  notification row/detail fields match between T5 and T6.
