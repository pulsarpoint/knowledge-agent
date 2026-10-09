# Jira Auto pull page, New schedule, Jira events — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto pull becomes its own Jira page with its own query and timetable; Administration → Schedules can create and delete schedules; every scheduled pull records ticket events, shown on Administration → Events.

**Architecture:** `ka.schedules.params` (0087) holds target settings (the pull's `jql`/`limit`); `jira_sync.auto_pull` reads them. `schedules_api.py` gains create/delete and the extended auto-pull endpoints. `ka.events` (0088) is a queue of things that happened; `jira_events.record` adds one per new or changed ticket inside the scheduled pull's transaction, judged against that ticket's latest event. `events_api.py` and `EventsView` show them. A shared `TimetableFields` component serves Edit, New schedule and the new `JiraAutoPullView`.

**Tech Stack:** PostgreSQL (golang-migrate), FastAPI + psycopg3, React Router 7 + shadcn + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-09-schedules-ui-design.md`

## Global Constraints

- Work only in `.claude/worktrees/schedules-ui` (branch `feature/schedules-ui`). Never edit the main checkout.
- Never run `make` targets; never touch `.env`, `.local` or the running Docker stack's database; never `git stash`; never `git add .superpowers`. Credentials never in logs, messages or Bash.
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` (this project rule overrides any generic attribution reminder).
- admin-api tests: `cd services/admin-api && uv run pytest tests/<file> -q`. admin-ui: `cd services/admin-ui && npx vitest run <file>`; types `npx react-router typegen && npx tsc --noEmit`. `services/admin-ui/node_modules` and `services/code-graph/node_modules` are symlinks to the main checkout (create them if missing: `ln -s /Users/graovic/fincore/knowledge-agent-v3/services/<x>/node_modules services/<x>/node_modules`); never commit them.
- Exact texts:
  - `Save Jira settings first.` · `This target already has a schedule.` · `Schedule not found.`
  - Schedules empty state: `No schedules yet. Create one with New schedule, or set up Jira → Auto pull.`
  - No free target: `Every target already has a schedule.`
  - Delete confirmation: title `Delete schedule {name}?`, text `Its history is deleted too.`
  - Auto pull page without connection: title `Connect Jira first`, text `Save your Jira server, username and token in Jira → Settings, then set up the automatic pull here.`
  - Tickets status line: `Auto pull: {description} · next {time}` / `Auto pull is off`, link `Auto pull settings`.
  - Labels: `Pull tickets automatically`, `Jira query (JQL)`, `Number of tickets`, `How often`, `Cron expression`, `Time zone`, `What to run`, `Enabled`; buttons `Save`, `Run now`, `New schedule`, `Create`, `Delete`.
- Auto pull `jql` 1–4000 chars, `limit` 1–200 (strict int); default limit 25. Audit actions `schedule.created`, `schedule.deleted` (plus the existing `schedule.saved`, `schedule.run_requested`).
- Events: types `jira.ticket.created`, `jira.ticket.updated`; run message `Pulled {fetched} of {total} tickets · {n} new events.` (`1 new event`); `Event not found.`; events page empty text `No events yet. Turn on Jira → Auto pull to record ticket changes.`, filtered empty `No events match these filters.`; labels `Ticket created`, `Ticket updated`, `Source`, `Type`, `Ticket or title`, buttons `Load more`, `Refresh`, link `Open in Jira`; events page size 50 (max 200).
- Only the scheduled pull (incl. Run now) records events; the manual Fetch tickets never does. The first scheduled pull records a `created` event for every ticket it pulls.

## Review Focus

1. A schedule created on the Schedules page before the Auto pull page was ever saved → it pulls with the connection's JQL and last limit (or 25), not with nothing. Test in Task 1 (`test_auto_pull_without_params_falls_back_to_the_connection`) and Task 2 (`test_create_jira_schedule_copies_the_connection_defaults`).
2. A manual Fetch tickets with a different query after Auto pull was saved → the auto pull keeps its own query. Test in Task 1 (`test_auto_pull_uses_its_own_query_after_a_manual_fetch`).
3. Deleting a schedule while it runs → the run finishes without error and records nothing; the row and its history are gone. Test in Task 2 (`test_deleting_a_running_schedule`).
4. Old clients sending `PUT /api/jira/auto-pull` without `jql`/`limit` → keep the stored values. Test in Task 2 (`test_auto_pull_without_query_keeps_the_stored_one`).
5. A ticket changed in Jira and first picked up by a manual Fetch tickets → the next scheduled pull still records `jira.ticket.updated` with the change. Test in Task 3 (`test_a_changed_ticket_records_what_changed_even_after_a_manual_fetch`).
6. A scheduled pull that clashes with a manual fetch (skipped) → records no events. Test in Task 3 (`test_a_clashing_pull_records_no_events`).
7. Search text with `%` or `_` → matched literally. Test in Task 4 (`test_filters_and_search`).

---

### Task 1: `schedules.params` (0087) and the pull reading it

**Files:**
- Create: `db/migrations/0087_schedule_params.up.sql`, `db/migrations/0087_schedule_params.down.sql`
- Modify: `db/tests/run.sh`, `services/admin-api/src/knowledge_admin/jira_sync.py` (`auto_pull`)
- Test: `services/admin-api/tests/test_schedule_params_migration.py`, `services/admin-api/tests/test_jira_sync.py` (add tests)

**Interfaces:**
- Produces: column `ka.schedules.params jsonb NOT NULL DEFAULT '{}'` (object). `auto_pull` reads `schedule['params']` (dict) — `jql` then connection `jql`; `limit` then `last_fetch_limit` then `DEFAULT_LIMIT`.

- [ ] **Step 1: Migrations**

`0087_schedule_params.up.sql`:
```sql
BEGIN;
SET search_path = ka, public;

-- Target settings, e.g. {"jql": ..., "limit": ...} for jira.pull (the pull's own query, separate from the manual fetch).
ALTER TABLE schedules ADD COLUMN params jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(params) = 'object');

COMMIT;
```
`0087_schedule_params.down.sql`:
```sql
BEGIN;
SET search_path = ka, public;

ALTER TABLE schedules DROP COLUMN params;

COMMIT;
```

- [ ] **Step 2: `db/tests/run.sh`**

The fresh-install block currently does one `down 1` (0086) right after the second `up`. Insert, before that `down 1`, a `down 1` for 0087 with its check:
```bash
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
params_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'ka' AND table_name = 'schedules' AND column_name = 'params')")
[ "$params_removed" = t ] || { echo 'FAIL: 0087 down left schedules.params' >&2; exit 1; }
```
After the line `printf '%s\n' 'PASS: schedules and their run history exist.'` add:
```bash
params=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'ka' AND table_name = 'schedules' AND column_name = 'params')")
[ "$params" = t ] || { echo 'FAIL: 0087 schedules.params missing' >&2; exit 1; }
printf '%s\n' 'PASS: schedules have target params.'
```
Check the rollback chain still ends at `rolled_back_version = 83` (it now takes one more `down 1` before that check: 0087, 0086, 0085, 0084 → 83). Adjust only if the script counts steps differently; run it to confirm.

- [ ] **Step 3: Migration test** (`tests/test_schedule_params_migration.py`)
```python
"""0087: schedules.params holds target settings (an object, default {})."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_schedule(conn, **values) -> dict:
    project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES (%s,'Shop') RETURNING id", (f'shop-{uuid4().hex[:8]}',)).fetchone()['id']
    row = {'project_id': project, 'name': 'Jira auto pull', 'target_kind': 'jira.pull', 'target_id': uuid4(), 'cron': '*/15 * * * *', **values}
    return conn.execute(f"INSERT INTO ka.schedules({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *", tuple(row.values())).fetchone()


def test_params_default_to_an_empty_object_and_must_be_an_object(store):
    with store.connect() as conn:
        assert a_schedule(conn)['params'] == {}
        assert a_schedule(conn, params=Jsonb({'jql': 'project = DEMO', 'limit': 10}))['params'] == {'jql': 'project = DEMO', 'limit': 10}
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        a_schedule(conn, params=Jsonb(['not', 'an', 'object']))


def test_down_drops_the_column_and_up_restores_it(store):
    column = """SELECT count(*) AS n FROM information_schema.columns
        WHERE table_schema='ka' AND table_name='schedules' AND column_name='params'"""
    with store.connect() as conn:
        a_schedule(conn, params=Jsonb({'jql': 'x'}))
        run(conn, '0087_schedule_params.down.sql')
        assert conn.execute(column).fetchone()['n'] == 0
        run(conn, '0087_schedule_params.up.sql')
        assert conn.execute(column).fetchone()['n'] == 1
```

- [ ] **Step 4: Failing tests for `auto_pull`** (append to `tests/test_jira_sync.py`; reuse its `a_schedule(store)` helper and imports)
```python
def jql_seen(handler_calls):
    return [call.params['jql'] for call in handler_calls if call.path.endswith('/search')]


def test_auto_pull_uses_its_own_query_after_a_manual_fetch(store, tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url)
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json={'startAt': 0, 'total': 0, 'issues': []})
        return jira_response(request)

    client, _ = setup(store, tmp_path, handler)
    assert client.post('/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 5, 'jql': 'project = MANUAL'}).status_code == 200
    schedule = {**a_schedule(store), 'params': {'jql': 'project = AUTO', 'limit': 7}}
    calls.clear()
    assert auto_pull(store, schedule, transport=httpx.MockTransport(handler)) == ('succeeded', 'Pulled 0 of 0 tickets.')
    searches = [url for url in calls if url.path.endswith('/search')]
    assert searches[0].params['jql'] == 'project = AUTO' and searches[0].params['maxResults'] == '7'


def test_auto_pull_without_params_falls_back_to_the_connection(store, tmp_path):
    client, _ = setup(store, tmp_path)
    assert client.post('/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 5}).status_code == 200
    assert auto_pull(store, {**a_schedule(store), 'params': {}}, transport=httpx.MockTransport(jira_response)) == ('succeeded', 'Pulled 5 of 7 tickets.')
```
Remove the unused `jql_seen` helper if you don't use it. Existing tests in this file call `auto_pull` with a dict without `params`; make `auto_pull` read `schedule.get('params') or {}` so they still pass.

- [ ] **Step 5: Implement `auto_pull`**
```python
def auto_pull(store: RepositoryStore, schedule: dict, transport: httpx.BaseTransport | None = None) -> tuple[str, str]:
    """Target jira.pull: pull with the schedule's own query and limit (params), else the connection's last fetch."""
    with store.connect() as conn:
        connection = conn.execute("""SELECT jql,last_fetch_limit FROM ka.jira_connections
            WHERE id=%s AND project_id=%s AND name='primary'""", (schedule['target_id'], schedule['project_id'])).fetchone()
    if connection is None:
        raise TargetMissing()
    params = schedule.get('params') or {}
    jql = params.get('jql') or connection['jql']
    limit = params.get('limit') or connection['last_fetch_limit'] or DEFAULT_LIMIT
    try:
        result = pull_tickets(store, schedule['project_id'], jql, limit, None, transport, action='jira.tickets_auto_pulled')
    except HTTPException as exc:
        if exc.status_code == 409 and exc.detail == FETCH_CLASH:
            return 'skipped', CLASH_SKIPPED
        raise
    return 'succeeded', f"Pulled {result['fetched']} of {result['total']} tickets."
```
Note: `pull_tickets` also writes `jql`/`last_fetch_limit` to the connection (the "last fetch" values the Tickets page shows). Keep that behavior.

- [ ] **Step 6: Run** `uv run pytest tests/test_schedule_params_migration.py tests/test_jira_sync.py tests/test_scheduler.py -q` and `bash db/tests/run.sh` (from the worktree root) → pass.

- [ ] **Step 7: Commit** `feat: schedules.params (0087); the Jira pull uses its own query and limit`.

---

### Task 2: API — create, delete, params and the extended auto pull

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/schedules_api.py`
- Test: `services/admin-api/tests/test_schedules_api.py` (update one test, add tests)

**Interfaces:**
- Consumes: `ka.schedules.params` (Task 1).
- Produces (HTTP under `/api`): schedule rows gain `params`; `POST /schedules` `{target_kind: 'jira.pull', cron, timezone, enabled}` → 201 `{schedule}`; `DELETE /schedules/{id}` → 204; `GET /jira/auto-pull` → `{schedule, jql, limit, connected}`; `PUT /jira/auto-pull` `{enabled, cron, timezone, jql?, limit?}` → same shape.

- [ ] **Step 1: Failing tests** (update `test_auto_pull_needs_jira_settings` to expect `{'schedule': None, 'jql': None, 'limit': None, 'connected': False}`; add:)
```python
def test_auto_pull_saves_its_own_query_and_limit(store, tmp_path):
    client, _ = setup(store, tmp_path)
    saved = client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, 'jql': 'project = AUTO', 'limit': 40}).json()
    assert saved['connected'] is True and saved['jql'] == 'project = AUTO' and saved['limit'] == 40
    assert saved['schedule']['params'] == {'jql': 'project = AUTO', 'limit': 40}
    assert client.get('/api/jira/auto-pull', headers=HEADERS).json()['jql'] == 'project = AUTO'


def test_auto_pull_without_query_keeps_the_stored_one(store, tmp_path):
    client, _ = setup(store, tmp_path)
    client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, 'jql': 'project = AUTO', 'limit': 40})
    again = client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, 'cron': '0 * * * *'}).json()
    assert again['schedule']['params'] == {'jql': 'project = AUTO', 'limit': 40} and again['schedule']['description'] == 'Every hour'


def test_auto_pull_query_and_limit_are_checked(store, tmp_path):
    client, _ = setup(store, tmp_path)
    for bad in [{'jql': ''}, {'jql': 'x' * 4001}, {'limit': 0}, {'limit': 201}, {'limit': '10'}]:
        assert client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, **bad}).status_code in (400, 422)


def test_create_jira_schedule_copies_the_connection_defaults(store, tmp_path):
    client, config = setup(store, tmp_path)
    created = client.post('/api/schedules', headers=HEADERS, json={'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True})
    assert created.status_code == 201, created.text
    schedule = created.json()['schedule']
    assert schedule['name'] == 'Jira auto pull' and schedule['target_id'] == config['id'] and schedule['next_run_at'] is not None
    assert schedule['params'] == {'jql': 'ORDER BY updated DESC, key DESC', 'limit': 25}
    duplicate = client.post('/api/schedules', headers=HEADERS, json={'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True})
    assert duplicate.status_code == 409 and error(duplicate) == 'This target already has a schedule.'
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.created'").fetchone()['n'] == 1


def test_create_needs_jira_and_a_known_target(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    value = {'target_kind': 'jira.pull', 'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True}
    refused = client.post('/api/schedules', headers=HEADERS, json=value)
    assert refused.status_code == 400 and error(refused) == 'Save Jira settings first.'
    assert client.post('/api/schedules', headers=HEADERS, json={**value, 'target_kind': 'agent.run'}).status_code == 422
    assert client.post('/api/schedules', headers=HEADERS, json={**value, 'cron': '*/2 * * * *'}).status_code == 400


def test_delete_removes_the_schedule_and_its_history(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    client.post(f"/api/schedules/{schedule['id']}/run", headers=HEADERS)
    scheduler.run_once(store, {'jira.pull': lambda store, s: ('succeeded', 'ok')})
    assert client.delete(f"/api/schedules/{schedule['id']}", headers=HEADERS).status_code == 204
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'] == []
    assert client.delete(f"/api/schedules/{schedule['id']}", headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.schedule_runs').fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.deleted'").fetchone()['n'] == 1


def test_deleting_a_running_schedule(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    client.post(f"/api/schedules/{schedule['id']}/run", headers=HEADERS)

    def delete_meanwhile(store, claimed):
        assert client.delete(f"/api/schedules/{claimed['id']}", headers=HEADERS).status_code == 204
        return 'succeeded', 'ok'

    assert scheduler.run_once(store, {'jira.pull': delete_meanwhile}) is True
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) AS n FROM ka.schedule_runs').fetchone()['n'] == 0
```

- [ ] **Step 2: Run** `uv run pytest tests/test_schedules_api.py -q` → the new tests fail.

- [ ] **Step 3: Implement** (in `schedules_api.py`)
- `FIELDS`: add `params` after `revision`.
- Models:
```python
class AutoPullInput(Timetable):
    enabled: bool
    jql: str | None = Field(default=None, min_length=1, max_length=4000)
    limit: int | None = Field(default=None, ge=1, le=200, strict=True)


class ScheduleInput(Timetable):
    enabled: bool
    revision: int = Field(ge=1, strict=True)


class NewSchedule(Timetable):
    target_kind: Literal['jira.pull']
    enabled: bool
```
(`from typing import Literal`; `first_run` takes any model with `enabled`, `cron`, `timezone` — annotate it as `Timetable` and read `value.enabled`.)
- Constants: `DUPLICATE = 'This target already has a schedule.'`.
- `jira_state(conn, project)` returns `(connection, state)`; with no connection `state = {'schedule': None, 'jql': None, 'limit': None, 'connected': False}`. Otherwise `params = row['params'] if row else {}` and `state = {'schedule': shown(row) if row else None, 'jql': params.get('jql') or connection['jql'], 'limit': params.get('limit') or connection['last_fetch_limit'] or DEFAULT_LIMIT, 'connected': True}`.
- A helper `jira_defaults(connection) -> dict` = `{'jql': connection['jql'], 'limit': connection['last_fetch_limit'] or DEFAULT_LIMIT}`.
- `save_auto_pull`: compute `params = {'jql': value.jql or current['jql'], 'limit': value.limit or current['limit']}` where `current` is the `state` from `jira_state` (it already resolves stored params, then the connection defaults). Add `params` to the INSERT columns (`Jsonb(params)`) and `params=EXCLUDED.params` to the `ON CONFLICT ... DO UPDATE`. Audit details also carry `jql` and `limit`.
- Create:
```python
    @router.post('/schedules', status_code=201)
    def create(value: NewSchedule, request: Request) -> dict:
        checked(value)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            connection, _ = jira_state(conn, project)
            if connection is None:
                raise HTTPException(400, NO_JIRA)
            row = conn.execute(f"""INSERT INTO ka.schedules(project_id,name,target_kind,target_id,cron,timezone,enabled,next_run_at,params)
                VALUES (%s,%s,'jira.pull',%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id,target_kind,target_id) DO NOTHING
                RETURNING {FIELDS}""", (project, JIRA_PULL, connection['id'], value.cron, value.timezone, value.enabled,
                                        first_run(value), Jsonb(jira_defaults(connection)))).fetchone()
            if row is None:
                raise HTTPException(409, DUPLICATE)
            audit(conn, project, request, 'schedule.created', row['id'],
                  {'target_kind': value.target_kind, 'cron': value.cron, 'timezone': value.timezone, 'enabled': value.enabled})
        return {'schedule': shown(row)}
```
- Delete:
```python
    @router.delete('/schedules/{schedule_id}', status_code=204)
    def delete(schedule_id: UUID, request: Request) -> None:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            row = conn.execute('DELETE FROM ka.schedules WHERE id=%s AND project_id=%s RETURNING name,target_kind',
                               (schedule_id, project)).fetchone()
            if row is None:
                raise HTTPException(404, NOT_FOUND)
            audit(conn, project, request, 'schedule.deleted', schedule_id, {'name': row['name'], 'target_kind': row['target_kind']})
```
  (Follow `profiles_api.py:311-343`: `@router.delete(..., status_code=204)` and `return Response(status_code=204)`; annotate the return as `Response`.)
- Update the module docstring: "Administration → Schedules and Jira → Auto pull: create, list, edit, pause, run and delete schedules…".

- [ ] **Step 4: Run** `uv run pytest tests/test_schedules_api.py tests/test_scheduler.py tests/test_jira_sync.py -q` → pass.

- [ ] **Step 5: Commit** `feat(admin-api): create and delete schedules; Auto pull keeps its own query and limit`.

---

### Task 3: Events table (0088) and the scheduled pull recording events

**Files:**
- Create: `db/migrations/0088_events.up.sql`, `db/migrations/0088_events.down.sql`, `services/admin-api/src/knowledge_admin/jira_events.py`
- Modify: `db/tests/run.sh`, `services/admin-api/src/knowledge_admin/jira_sync.py` (`pull_tickets(..., events=False)`, `auto_pull`)
- Test: `services/admin-api/tests/test_events_migration.py`, `services/admin-api/tests/test_jira_events.py`; update message assertions in `services/admin-api/tests/test_jira_sync.py`

**Interfaces:**
- Consumes: `jira_sync.pull_tickets`, `auto_pull` (Task 1 version reading params).
- Produces: table `ka.events`; `jira_events.CREATED = 'jira.ticket.created'`, `UPDATED = 'jira.ticket.updated'`; `jira_events.ticket(base_url, issue) -> dict`, `changes(before, after) -> dict`, `record(conn, project_id, connection_id, base_url, issues) -> int`; `pull_tickets` returns `{'fetched', 'total', 'events'}`; `auto_pull` message `Pulled {fetched} of {total} tickets · {n} new events.` (`· 1 new event.`).

- [ ] **Step 1: Migrations**

`0088_events.up.sql`:
```sql
BEGIN;
SET search_path = ka, public;

-- Things that happened at a source, kept as a queue for later handling (no status yet). The scheduled Jira pull
-- records jira.ticket.created / jira.ticket.updated; dedupe_key makes a repeated pull record nothing new.
CREATE TABLE events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id),
    source text NOT NULL CHECK (source IN ('jira')),
    source_id uuid,
    type text NOT NULL CHECK (type ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
    subject nonblank NOT NULL,
    title text NOT NULL DEFAULT '',
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    dedupe_key nonblank NOT NULL,
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, dedupe_key)
);
COMMENT ON COLUMN events.source_id IS 'The source''s row, e.g. jira_connections.id (no foreign key: sources live in different tables).';
CREATE INDEX events_newest ON events(project_id, id DESC);
CREATE INDEX events_by_type ON events(project_id, type, id DESC);
-- The Jira pull compares each ticket with its latest event from the same connection.
CREATE INDEX events_jira_issues ON events(project_id, source_id, (payload->>'issue_id'), id DESC) WHERE source = 'jira';

COMMIT;
```
`0088_events.down.sql`:
```sql
BEGIN;
SET search_path = ka, public;

DROP TABLE events;

COMMIT;
```

- [ ] **Step 2: `db/tests/run.sh`** — add a `down 1` for 0088 (before the 0087 one added in Task 1) with
```bash
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
events_removed=$(docker exec "$test_container" psql -X -U postgres -Atc "SELECT to_regclass('ka.events') IS NULL")
[ "$events_removed" = t ] || { echo 'FAIL: 0088 down left ka.events' >&2; exit 1; }
```
and after the 0087 PASS line:
```bash
events=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.events') IS NOT NULL AND to_regclass('ka.events_jira_issues') IS NOT NULL")
[ "$events" = t ] || { echo 'FAIL: 0088 events table missing' >&2; exit 1; }
printf '%s\n' 'PASS: events are recorded in ka.events.'
```
Run it: the rollback chain must still reach `rolled_back_version = 83`.

- [ ] **Step 3: Migration test** (`tests/test_events_migration.py`)
```python
"""0088: ka.events — one row per thing that happened at a source; dedupe_key is unique per project."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def an_event(conn, project=None, **values) -> dict:
    project = project or conn.execute("INSERT INTO ka.projects(slug,name) VALUES (%s,'Shop') RETURNING id", (f'shop-{uuid4().hex[:8]}',)).fetchone()['id']
    row = {'project_id': project, 'source': 'jira', 'source_id': uuid4(), 'type': 'jira.ticket.created', 'subject': 'DEMO-1',
           'title': 'Check latency', 'payload': Jsonb({'issue_id': '10001'}), 'dedupe_key': f'jira:x:10001:{uuid4()}',
           'occurred_at': '2026-10-09T10:00:00Z', **values}
    return conn.execute(f"INSERT INTO ka.events({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *", tuple(row.values())).fetchone()


def test_checks(store):
    with store.connect() as conn:
        assert an_event(conn)['created_at'] is not None
    for bad in [{'source': 'mail'}, {'type': 'Created'}, {'type': 'jira'}, {'subject': ' '}, {'payload': Jsonb([1])}]:
        with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
            an_event(conn, **bad)


def test_dedupe_key_is_unique_per_project(store):
    with store.connect() as conn:
        first = an_event(conn, dedupe_key='same')
        an_event(conn, dedupe_key='same')  # another project
    with pytest.raises(psycopg.errors.UniqueViolation), store.connect() as conn:
        an_event(conn, project=first['project_id'], dedupe_key='same')


def test_down_and_up(store):
    with store.connect() as conn:
        an_event(conn)
        conn.execute((MIGRATIONS / '0088_events.down.sql').read_text(), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.events') IS NULL AS gone").fetchone()['gone']
        conn.execute((MIGRATIONS / '0088_events.up.sql').read_text(), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.events_jira_issues') IS NOT NULL AS found").fetchone()['found']
```

- [ ] **Step 4: Failing tests** (`tests/test_jira_events.py`)
```python
"""The scheduled Jira pull records an event per new or changed ticket; repeats and manual fetches record none."""
import copy
from uuid import uuid4

import httpx

from knowledge_admin.jira_events import changes
from knowledge_admin.jira_sync import auto_pull
from test_api import _client
from test_jira import ISSUES, setup
from test_repositories import HEADERS


def a_schedule(store, **params) -> dict:
    with store.connect() as conn:
        connection = conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()
    return {'id': uuid4(), 'project_id': connection['project_id'], 'target_kind': 'jira.pull',
            'target_id': connection['id'], 'params': {'jql': 'project = DEMO', 'limit': 3, **params}}


def jira(issues):
    def handler(request):
        if request.url.path.endswith('/myself'):
            return httpx.Response(200, json={'name': 'mock-admin', 'displayName': 'Mock', 'active': True})
        start, size = int(request.url.params['startAt']), int(request.url.params['maxResults'])
        return httpx.Response(200, json={'startAt': start, 'total': len(issues), 'issues': copy.deepcopy(issues[start:start + size])})
    return httpx.MockTransport(handler)


def events(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT type,subject,title,payload,occurred_at FROM ka.events ORDER BY id').fetchall()


def test_first_pull_records_every_ticket_and_a_repeat_records_nothing(store, tmp_path):
    setup(store, tmp_path)
    issues = ISSUES[:3]
    assert auto_pull(store, a_schedule(store), transport=jira(issues)) == ('succeeded', 'Pulled 3 of 3 tickets · 3 new events.')
    recorded = events(store)
    assert [e['type'] for e in recorded] == ['jira.ticket.created'] * 3
    first = recorded[0]
    assert first['subject'] == issues[0]['key'] and first['title'] == issues[0]['fields']['summary']
    assert first['payload']['url'] == f"https://jira.internal/jira/browse/{issues[0]['key']}" and first['payload']['changes'] == {}
    assert first['payload']['status'] == issues[0]['fields']['status']['name']
    assert auto_pull(store, a_schedule(store), transport=jira(issues)) == ('succeeded', 'Pulled 3 of 3 tickets · 0 new events.')
    assert len(events(store)) == 3


def test_a_changed_ticket_records_what_changed_even_after_a_manual_fetch(store, tmp_path):
    client, _ = setup(store, tmp_path)
    issues = copy.deepcopy(ISSUES[:2])
    auto_pull(store, a_schedule(store), transport=jira(issues))
    changed = issues[0]['fields']
    changed['status'] = {'id': '3', 'name': 'Done'}
    changed['labels'] = ['demo', 'agent']
    changed['updated'] = '2026-10-09T10:00:00.000+0000'
    # The manual fetch updates the tickets table first; the scheduled pull must still notice the change.
    manual = _client(tmp_path, repository_store=store, jira_transport=jira(issues))
    assert manual.post('/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 2, 'jql': 'project = DEMO'}).status_code == 200
    assert len(events(store)) == 2  # the manual fetch records nothing
    assert auto_pull(store, a_schedule(store), transport=jira(issues)) == ('succeeded', 'Pulled 2 of 2 tickets · 1 new event.')
    update = events(store)[-1]
    assert update['type'] == 'jira.ticket.updated' and update['subject'] == issues[0]['key']
    before = ISSUES[0]['fields']
    assert update['payload']['changes'] == {'status': {'from': before['status']['name'], 'to': 'Done'},
                                            'labels': {'added': ['agent'], 'removed': [l for l in before['labels'] if l not in ('demo', 'agent')]}}


def test_a_clashing_pull_records_no_events(store, tmp_path):
    setup(store, tmp_path)
    inner = jira(ISSUES[:2])

    def clashing(request):
        if request.url.path.endswith('/search'):
            with store.connect() as conn:
                conn.execute('UPDATE ka.jira_connections SET fetch_revision=fetch_revision+1')
        return inner.handle_request(request)

    assert auto_pull(store, a_schedule(store), transport=httpx.MockTransport(clashing))[0] == 'skipped'
    assert events(store) == []


def test_changes():
    before = {'summary': 'A', 'status': 'Open', 'issue_type': 'Task', 'priority': 'High', 'assignee': None, 'labels': ['x', 'y'], 'description': 'd'}
    after = {**before, 'summary': 'B', 'assignee': 'Ana', 'labels': ['y', 'z'], 'description': 'e'}
    assert changes(before, after) == {'summary': {'from': 'A', 'to': 'B'}, 'assignee': {'from': None, 'to': 'Ana'},
                                      'labels': {'added': ['z'], 'removed': ['x']}, 'description': {'changed': True}}
    assert changes(before, dict(before)) == {}
```
(`setup` cannot be called twice: it saves Jira settings without a revision.) If `ISSUES[0]` has labels `['local-mock', 'demo']`, the expected `removed` is `['local-mock']` — compute it as written, don't hard-code.

Update `tests/test_jira_sync.py` message assertions: auto pulls now end with ` · N new events.` (e.g. `'Pulled 5 of 7 tickets · 5 new events.'`, `'Pulled 7 of 7 tickets · 7 new events.'`, `'Pulled 0 of 0 tickets · 0 new events.'`).

- [ ] **Step 5: `jira_events.py`**
```python
"""Jira events: what the scheduled pull saw change. One event per new or changed ticket (Jira's `updated` moved),
judged against the pull's own latest event for that ticket — not the tickets table, which the manual fetch also
updates."""
from uuid import UUID

from psycopg.types.json import Jsonb

CREATED, UPDATED = 'jira.ticket.created', 'jira.ticket.updated'
COMPARED = ('summary', 'status', 'issue_type', 'priority', 'assignee')


def ticket(base_url: str, issue: dict) -> dict:
    fields = issue['fields']
    named = lambda value: (value or {}).get('name')
    person = lambda value: (value or {}).get('displayName') or (value or {}).get('key')
    description = fields.get('description')
    return {'issue_id': issue['id'], 'key': issue['key'], 'url': f"{base_url}/browse/{issue['key']}",
            'summary': fields['summary'], 'description': description if isinstance(description, str) else '',
            'status': named(fields.get('status')), 'issue_type': named(fields.get('issuetype')),
            'priority': named(fields.get('priority')), 'assignee': person(fields.get('assignee')),
            'reporter': person(fields.get('reporter')), 'labels': list(fields.get('labels') or []),
            'created': fields.get('created'), 'updated': fields['updated']}


def changes(before: dict, after: dict) -> dict:
    result = {field: {'from': before.get(field), 'to': after.get(field)} for field in COMPARED if before.get(field) != after.get(field)}
    old, new = before.get('labels') or [], after.get('labels') or []
    added, removed = [label for label in new if label not in old], [label for label in old if label not in new]
    if added or removed:
        result['labels'] = {'added': added, 'removed': removed}
    if before.get('description') != after.get('description'):
        result['description'] = {'changed': True}
    return result


def record(conn, project_id: UUID, connection_id: UUID, base_url: str, issues: list[dict]) -> int:
    """Record events for the pulled issues inside the caller's transaction; returns how many were new."""
    latest = {row['issue_id']: row['payload'] for row in conn.execute("""SELECT DISTINCT ON (payload->>'issue_id')
            payload->>'issue_id' AS issue_id, payload FROM ka.events
        WHERE project_id=%s AND source='jira' AND source_id=%s AND payload->>'issue_id' = ANY(%s)
        ORDER BY payload->>'issue_id', id DESC""", (project_id, connection_id, [issue['id'] for issue in issues]))}
    recorded = 0
    for issue in issues:
        after, before = ticket(base_url, issue), latest.get(issue['id'])
        if before is not None and before.get('updated') == after['updated']:
            continue
        payload = {**after, 'changes': {} if before is None else changes(before, after)}
        row = conn.execute("""INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,occurred_at)
            VALUES (%s,'jira',%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id,dedupe_key) DO NOTHING RETURNING id""",
            (project_id, connection_id, CREATED if before is None else UPDATED, after['key'], after['summary'], Jsonb(payload),
             f"jira:{connection_id}:{issue['id']}:{after['updated']}", after['updated'])).fetchone()
        recorded += row is not None
    return recorded
```

- [ ] **Step 6: `jira_sync.py`**
- `pull_tickets(..., action='jira.tickets_fetched', events: bool = False)`: inside the write transaction, after the ticket upserts and before the connection UPDATE: `new_events = jira_events.record(conn, config['project_id'], config['id'], config['base_url'], issues) if events else 0`. Audit details: add `'events': new_events` only when `events` is true. Return `{'fetched': len(issues), 'total': total, 'events': new_events}`.
- `auto_pull` passes `events=True` and returns `'succeeded', f"Pulled {fetched} of {total} tickets · {plural(events)}."` with `plural(n) = '1 new event' if n == 1 else f'{n} new events'`.

- [ ] **Step 7: Run** `uv run pytest tests/test_events_migration.py tests/test_jira_events.py tests/test_jira_sync.py tests/test_jira.py -q` and `bash db/tests/run.sh` → pass.

- [ ] **Step 8: Commit** `feat: events table (0088); the scheduled Jira pull records ticket events`.

---

### Task 4: Events API

**Files:**
- Create: `services/admin-api/src/knowledge_admin/events_api.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py` (include the router next to `schedules_api`)
- Test: `services/admin-api/tests/test_events_api.py`

**Interfaces:**
- Produces (under `/api`): `GET /events` (`source`, `type`, `q`, `before`, `limit`) → `{events: [{id, source, type, subject, title, occurred_at, created_at}], next_before: int | null}`; `GET /events/facets` → `{sources: [str], types: [str]}`; `GET /events/{id}` → `{event: {...list fields, source_id, dedupe_key, payload}}`; 404 `Event not found.`

- [ ] **Step 1: Failing tests** (`tests/test_events_api.py`)
```python
"""Administration → Events: newest first, filters, cursor paging, one event with its payload."""
from uuid import uuid4

from psycopg.types.json import Jsonb

from test_api import _client
from test_repositories import HEADERS


def seed(store, count=5, **values) -> list[int]:
    ids = []
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        for number in range(1, count + 1):
            row = {'project_id': project, 'source': 'jira', 'source_id': uuid4(), 'type': 'jira.ticket.created',
                   'subject': f'DEMO-{number}', 'title': f'Ticket {number}', 'payload': Jsonb({'issue_id': str(number), 'changes': {}}),
                   'dedupe_key': f'k{uuid4()}', 'occurred_at': '2026-10-09T10:00:00Z', **values}
            ids.append(conn.execute(f"INSERT INTO ka.events({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING id",
                                    tuple(row.values())).fetchone()['id'])
    return ids


def test_newest_first_with_cursor(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = seed(store, 5)
    page = client.get('/api/events', headers=HEADERS, params={'limit': 2}).json()
    assert [e['id'] for e in page['events']] == ids[::-1][:2] and page['next_before'] == ids[3]
    rest = client.get('/api/events', headers=HEADERS, params={'limit': 10, 'before': page['next_before']}).json()
    assert [e['id'] for e in rest['events']] == ids[::-1][2:] and rest['next_before'] is None
    assert 'payload' not in page['events'][0]


def test_filters_and_search(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store, 2)
    seed(store, 1, type='jira.ticket.updated', subject='OPS-7', title='Disk 100% full_')
    assert [e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'type': 'jira.ticket.updated'}).json()['events']] == ['OPS-7']
    assert [e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'q': 'ops-'}).json()['events']] == ['OPS-7']
    assert [e['subject'] for e in client.get('/api/events', headers=HEADERS, params={'q': '100%'}).json()['events']] == ['OPS-7']
    assert client.get('/api/events', headers=HEADERS, params={'q': '_'}).json()['events'][0]['subject'] == 'OPS-7'
    assert client.get('/api/events', headers=HEADERS, params={'source': 'jira'}).json()['events'] != []
    assert client.get('/api/events/facets', headers=HEADERS).json() == {'sources': ['jira'], 'types': ['jira.ticket.created', 'jira.ticket.updated']}
    assert client.get('/api/events', headers=HEADERS, params={'limit': 201}).status_code == 422


def test_one_event_and_missing(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [event_id] = seed(store, 1)
    event = client.get(f'/api/events/{event_id}', headers=HEADERS).json()['event']
    assert event['payload'] == {'issue_id': '1', 'changes': {}} and event['dedupe_key'].startswith('k')
    missing = client.get('/api/events/999999', headers=HEADERS)
    assert missing.status_code == 404 and missing.json()['error']['message'] == 'Event not found.'


def test_other_projects_events_are_not_listed(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other-x','Other') RETURNING id").fetchone()['id']
    [foreign] = seed(store, 1, project_id=other)
    assert client.get('/api/events', headers=HEADERS).json()['events'] == []
    assert client.get(f'/api/events/{foreign}', headers=HEADERS).status_code == 404
```

- [ ] **Step 2: Implement `events_api.py`**
```python
"""Administration → Events: things that happened at a source (today the scheduled Jira pull). Read-only; handling
comes later."""
from fastapi import APIRouter, HTTPException, Query

from knowledge_admin.repositories import RepositoryStore

LISTED = 'id,source,type,subject,title,occurred_at,created_at'
PAGE, MAX_PAGE = 50, 200
NOT_FOUND = 'Event not found.'


def like(text: str) -> str:
    """A case-insensitive substring pattern with LIKE wildcards escaped."""
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Event storage is not configured')
        return store

    @router.get('/events')
    def list_events(source: str | None = Query(None, max_length=50), type: str | None = Query(None, max_length=100),
                    q: str | None = Query(None, max_length=200), before: int | None = Query(None, ge=1),
                    limit: int = Query(PAGE, ge=1, le=MAX_PAGE)) -> dict:
        db = database()
        with db.connect() as conn:
            where, values = ['project_id=%s'], [db.project(conn, app)]
            if source:
                where.append('source=%s'); values.append(source)
            if type:
                where.append('type=%s'); values.append(type)
            if q and q.strip():
                where.append("(subject ILIKE %s ESCAPE '\\' OR title ILIKE %s ESCAPE '\\')"); values += [like(q.strip())] * 2
            if before:
                where.append('id<%s'); values.append(before)
            rows = conn.execute(f"SELECT {LISTED} FROM ka.events WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s",
                                (*values, limit + 1)).fetchall()
        more = len(rows) > limit
        return {'events': rows[:limit], 'next_before': rows[limit - 1]['id'] if more else None}

    @router.get('/events/facets')
    def facets() -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            sources = [row['source'] for row in conn.execute('SELECT DISTINCT source FROM ka.events WHERE project_id=%s ORDER BY 1', (project,))]
            types = [row['type'] for row in conn.execute('SELECT DISTINCT type FROM ka.events WHERE project_id=%s ORDER BY 1', (project,))]
        return {'sources': sources, 'types': types}

    @router.get('/events/{event_id}')
    def one(event_id: int) -> dict:
        db = database()
        with db.connect() as conn:
            row = conn.execute(f'SELECT {LISTED},source_id,dedupe_key,payload FROM ka.events WHERE id=%s AND project_id=%s',
                               (event_id, db.project(conn, app))).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return {'event': row}

    return router
```
Wire it in `api.py` (`events_api` in the import list; `router.include_router(events_api.build_api(app_name, repository_store))`). Declare `/events/facets` before `/events/{event_id}` (it is `int`, so `facets` would 422 otherwise).

- [ ] **Step 3: Run** `uv run pytest tests/test_events_api.py tests/test_api.py -q` → pass.

- [ ] **Step 4: Commit** `feat(admin-api): events API`.

---

### Task 5: Schedules page — shared timetable fields, New schedule, Delete

**Files:**
- Create: `services/admin-ui/app/components/TimetableFields.tsx`, `services/admin-ui/app/components/NewScheduleSheet.tsx`
- Modify: `services/admin-ui/app/lib/schedules.ts`, `services/admin-ui/app/components/ScheduleEditor.tsx`, `services/admin-ui/app/components/SchedulesView.tsx`
- Test: `services/admin-ui/app/components/__tests__/schedules.test.tsx` (update the empty-state test, add tests)

**Interfaces:**
- Consumes: Task 2 HTTP API.
- Produces: `lib/schedules.ts` — `Schedule.params: Record<string, unknown>`; `AutoPull` gains `connected: boolean`; `AutoPullInput = ScheduleInput & { jql: string; limit: number }`; `saveAutoPull(value: AutoPullInput)`; `createSchedule(value: ScheduleInput & { target_kind: string })`; `deleteSchedule(id)`. Component `TimetableFields({ cron, timezone, onChange, onProblem })` where `onChange({cron, timezone})` and `onProblem(message | null)` — it owns preset/custom choice and the debounced preview. Task 4 uses `TimetableFields`, `saveAutoPull`, `runSchedule`, `getAutoPull`, `ScheduleStatus`.

- [ ] **Step 1: `lib/schedules.ts` additions**
```ts
export interface Schedule { /* existing fields */ params: Record<string, unknown> }
export interface AutoPull { schedule: Schedule | null; jql: string | null; limit: number | null; connected: boolean }
export interface AutoPullInput extends ScheduleInput { jql: string; limit: number }

export const createSchedule = (value: ScheduleInput & { target_kind: string }) => request<{ schedule: Schedule }>("/api/schedules", send("POST", value));
export const deleteSchedule = (id: string) => request<void>(path(id), { method: "DELETE" });
export const saveAutoPull = (value: AutoPullInput) => request<AutoPull>("/api/jira/auto-pull", send("PUT", value));
```
(`request` returns `undefined` for 204.)

- [ ] **Step 2: `TimetableFields.tsx`** — move the preset/custom/time-zone/preview JSX and the preview effect out of `ScheduleEditor` unchanged in behavior:
```tsx
import { useEffect, useState } from "react";
import { ApiError } from "../lib/api";
import { PRESETS, previewSchedule, timeZones } from "../lib/schedules";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";

const PREVIEW_DELAY_MS = 300;

export interface Timetable { cron: string; timezone: string }

/** How often (preset or custom cron), time zone and a preview of the next runs. Reports a server validation message
 * through onProblem (null when the timetable is valid). */
export default function TimetableFields({ id, value, onChange, onProblem }: { id: string; value: Timetable; onChange: (value: Timetable) => void; onProblem: (problem: string | null) => void }) {
  const [custom, setCustom] = useState(() => !PRESETS.some(preset => preset.cron === value.cron));
  const [preview, setPreview] = useState<string[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    const cron = value.cron.trim();
    if (!cron) return;
    let live = true;
    const timer = setTimeout(() => previewSchedule(cron, value.timezone)
      .then(result => { if (live) { setPreview(result.runs); setProblem(null); onProblem(null); } })
      .catch(reason => { if (!live) return; const message = reason instanceof ApiError ? reason.message : "Could not check the schedule.";
        setPreview(null); setProblem(message); onProblem(message); }), PREVIEW_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [value.cron, value.timezone]);  // onProblem is a setter from the parent

  return <>
    <Field><FieldLabel htmlFor={`${id}-preset`}>How often</FieldLabel>
      <Select value={custom ? "custom" : value.cron} onValueChange={choice => { if (choice === "custom") setCustom(true); else { setCustom(false); onChange({ ...value, cron: choice }); } }}>
        <SelectTrigger id={`${id}-preset`} aria-label="How often" className="w-full"><SelectValue /></SelectTrigger>
        <SelectContent><SelectGroup>{PRESETS.map(preset => <SelectItem key={preset.cron} value={preset.cron}>{preset.label}</SelectItem>)}<SelectItem value="custom">Custom</SelectItem></SelectGroup></SelectContent>
      </Select></Field>
    {custom && <Field><FieldLabel htmlFor={`${id}-cron`}>Cron expression</FieldLabel>
      <Input id={`${id}-cron`} value={value.cron} maxLength={100} onChange={event => onChange({ ...value, cron: event.target.value })} />
      <FieldDescription>Five fields: minute, hour, day of month, month, day of week. For example <code>30 8 * * 1-5</code> runs at 08:30 on weekdays.</FieldDescription></Field>}
    <Field><FieldLabel htmlFor={`${id}-timezone`}>Time zone</FieldLabel>
      <Select value={value.timezone} onValueChange={timezone => onChange({ ...value, timezone })}>
        <SelectTrigger id={`${id}-timezone`} aria-label="Time zone" className="w-full"><SelectValue /></SelectTrigger>
        <SelectContent><SelectGroup>{timeZones(value.timezone).map(zone => <SelectItem key={zone} value={zone}>{zone}</SelectItem>)}</SelectGroup></SelectContent>
      </Select></Field>
    {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p>
      : preview && <div className="flex flex-col gap-1"><p className="text-sm font-medium">Next runs</p>
        <ul className="text-sm text-muted-foreground">{preview.map(run => <li key={run}>{new Date(run).toLocaleString()}</li>)}</ul></div>}
  </>;
}
```
Refactor `ScheduleEditor` to hold `{cron, timezone}` + `problem` state and render `<TimetableFields id="schedule" …/>` (remount it per schedule with `key={schedule.id}` so `custom` re-initialises). Existing editor tests must keep passing unchanged.

- [ ] **Step 3: `NewScheduleSheet.tsx`**
```tsx
import { useState, type FormEvent } from "react";
import { ApiError } from "../lib/api";
import { DEFAULT_CRON, TARGETS, browserTimeZone, createSchedule, type Schedule } from "../lib/schedules";
import TimetableFields, { type Timetable } from "./TimetableFields";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";

export default function NewScheduleSheet({ open, taken, onClose, onCreated }: { open: boolean; taken: string[]; onClose: () => void; onCreated: (schedule: Schedule) => void }) {
  const free = Object.keys(TARGETS).filter(kind => !taken.includes(kind));
  const [target, setTarget] = useState(free[0] ?? "");
  const [timetable, setTimetable] = useState<Timetable>({ cron: DEFAULT_CRON, timezone: browserTimeZone() });
  const [enabled, setEnabled] = useState(true);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function create(event: FormEvent) {
    event.preventDefault();
    setSaving(true); setError(null);
    try { onCreated((await createSchedule({ target_kind: target, cron: timetable.cron.trim(), timezone: timetable.timezone, enabled })).schedule); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not create the schedule."); }
    finally { setSaving(false); }
  }

  return <Sheet open={open} onOpenChange={next => { if (!next) onClose(); }}>
    <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
      <SheetHeader><SheetTitle>New schedule</SheetTitle><SheetDescription>Choose what to run and when.</SheetDescription></SheetHeader>
      {free.length === 0 ? <p className="text-sm text-muted-foreground">Every target already has a schedule.</p> :
      <form onSubmit={create} className="flex flex-col gap-4">
        <FieldGroup>
          <Field><FieldLabel htmlFor="new-schedule-target">What to run</FieldLabel>
            <Select value={target} onValueChange={setTarget}>
              <SelectTrigger id="new-schedule-target" aria-label="What to run" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup>{free.map(kind => <SelectItem key={kind} value={kind}>{TARGETS[kind]}</SelectItem>)}</SelectGroup></SelectContent>
            </Select></Field>
          <TimetableFields id="new-schedule" value={timetable} onChange={setTimetable} onProblem={setProblem} />
          <Field orientation="horizontal"><Switch id="new-schedule-enabled" checked={enabled} onCheckedChange={setEnabled} /><FieldLabel htmlFor="new-schedule-enabled">Enabled</FieldLabel></Field>
        </FieldGroup>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <SheetFooter><Button type="submit" disabled={saving || problem !== null || !timetable.cron.trim() || !target}>{saving && <Spinner data-icon="inline-start" />}Create</Button></SheetFooter>
      </form>}
    </SheetContent>
  </Sheet>;
}
```
Render it only while open (`{creating && <NewScheduleSheet open … />}`) so its state resets each time.

- [ ] **Step 4: `SchedulesView.tsx`**
- A toolbar above the table with `<Button onClick={() => setCreating(true)}><Plus data-icon="inline-start" />New schedule</Button>` (also shown with the empty state).
- Empty text → `No schedules yet. Create one with New schedule, or set up Jira → Auto pull.`
- `onCreated`: `changes.current += 1; setSchedules(current => [...(current ?? []), created].sort((a, b) => a.name.localeCompare(b.name))); setCreating(false)`.
- Row action `<Button size="sm" variant="ghost" onClick={() => setDeleting(schedule)}><Trash2 data-icon="inline-start" />Delete</Button>`, and one `AlertDialog` for `deleting`: title `Delete schedule {name}?`, description `Its history is deleted too.`, `AlertDialogCancel` "Cancel", `AlertDialogAction` "Delete" (`onClick={event => { event.preventDefault(); void remove(); }}`). `remove` calls `deleteSchedule`, then `changes.current += 1`, drops the row, closes history if it was that row, closes the dialog; on error sets `error` and closes the dialog.
- `taken` for the sheet = `schedules.map(s => s.target_kind)`.

- [ ] **Step 5: Tests** (in `schedules.test.tsx`; add `params: {}` to the `schedule` fixture; update the empty-state text; add:)
```tsx
test("new schedule creates a Jira pull", async () => {
  const user = userEvent.setup();
  const fetch = api({ "GET /api/schedules": () => [200, { schedules: [] }],
    "POST /api/schedules/preview": () => [200, { runs: ["2026-10-12T05:00:00Z"] }],
    "POST /api/schedules": () => [201, { schedule }] });
  view();
  await user.click(await screen.findByRole("button", { name: "New schedule" }));
  expect(screen.getByRole("combobox", { name: "What to run" })).toHaveTextContent("Jira tickets");
  await screen.findByText("Next runs");
  await user.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByText("Jira auto pull")).toBeInTheDocument();
  expect(body(fetch, "POST /api/schedules")).toEqual({ target_kind: "jira.pull", cron: "*/15 * * * *", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", enabled: true });
});

test("new schedule says when every target is taken", async () => {
  const user = userEvent.setup();
  api({ "GET /api/schedules": () => [200, { schedules: [schedule] }] });
  view();
  await user.click(await screen.findByRole("button", { name: "New schedule" }));
  expect(screen.getByText("Every target already has a schedule.")).toBeInTheDocument();
});

test("delete asks first and removes the row", async () => {
  const user = userEvent.setup();
  const fetch = api({ "GET /api/schedules": () => [200, { schedules: [schedule] }], "DELETE /api/schedules/s-1": () => [204, null] });
  view();
  await user.click(await screen.findByRole("button", { name: "Delete" }));
  expect(screen.getByText("Delete schedule Jira auto pull?")).toBeInTheDocument();
  expect(screen.getByText("Its history is deleted too.")).toBeInTheDocument();
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
  expect(await screen.findByText("No schedules yet. Create one with New schedule, or set up Jira → Auto pull.")).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url, init]) => url === "/api/schedules/s-1" && init?.method === "DELETE")).toBe(true);
});
```
The test helper `api()` builds `new Response(JSON.stringify(body), { status })` — a 204 with a body throws in the Response constructor; make the helper use `new Response(status === 204 ? null : JSON.stringify(body), { status })`.

- [ ] **Step 6: Run** `npx vitest run app/components/__tests__/schedules.test.tsx` and `npx tsc --noEmit` → pass.

- [ ] **Step 7: Commit** `feat(admin-ui): New schedule and Delete on Schedules; shared timetable fields`.

---

### Task 6: Jira → Auto pull page and the Tickets status line

**Files:**
- Create: `services/admin-ui/app/components/JiraAutoPullView.tsx`, `services/admin-ui/app/routes/jiraAutoPull.tsx`, `services/admin-ui/app/components/JiraAutoPullStatus.tsx`
- Delete: `services/admin-ui/app/components/JiraAutoPullCard.tsx`
- Modify: `services/admin-ui/app/routes.ts`, `services/admin-ui/app/components/AdminLayout.tsx`, `services/admin-ui/app/components/JiraTicketsView.tsx`, `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`
- Test: `services/admin-ui/app/components/__tests__/jira.test.tsx` (replace the three auto pull card tests), new `services/admin-ui/app/components/__tests__/jiraAutoPull.test.tsx`

**Interfaces:**
- Consumes: `getAutoPull`, `saveAutoPull`, `runSchedule`, `browserTimeZone`, `DEFAULT_CRON`, `AutoPull`, `ScheduleStatus` (from `SchedulesView`), `TimetableFields`.

- [ ] **Step 1: `JiraAutoPullView.tsx`**
```tsx
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { CalendarClock, Play } from "lucide-react";
import { ApiError } from "../lib/api";
import { DEFAULT_CRON, browserTimeZone, getAutoPull, runSchedule, saveAutoPull, type AutoPull } from "../lib/schedules";
import { ScheduleStatus } from "./SchedulesView";
import TimetableFields, { type Timetable } from "./TimetableFields";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Skeleton } from "~/components/ui/skeleton";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";

const when = (value: string) => new Date(value).toLocaleString();
const text = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

export default function JiraAutoPullView() {
  const [state, setState] = useState<AutoPull | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [jql, setJql] = useState("");
  const [limit, setLimit] = useState("25");
  const [timetable, setTimetable] = useState<Timetable>({ cron: DEFAULT_CRON, timezone: browserTimeZone() });
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function show(next: AutoPull) {
    setState(next);
    setEnabled(next.schedule?.enabled ?? false);
    setJql(next.jql ?? ""); setLimit(String(next.limit ?? 25));
    if (next.schedule) setTimetable({ cron: next.schedule.cron, timezone: next.schedule.timezone });
  }
  useEffect(() => {
    const controller = new AbortController();
    getAutoPull(controller.signal).then(show).catch(reason => { if (!controller.signal.aborted) setError(text(reason, "Could not load the auto pull settings.")); });
    return () => controller.abort();
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(null); setNotice(null);
    try { show(await saveAutoPull({ enabled, cron: timetable.cron.trim(), timezone: timetable.timezone, jql: jql.trim(), limit: Number(limit) })); setNotice("Auto pull saved."); }
    catch (reason) { setError(text(reason, "Could not save the auto pull settings.")); }
    finally { setBusy(false); }
  }
  async function runNow() {
    if (!state?.schedule) return;
    setBusy(true); setError(null); setNotice(null);
    try { const { schedule } = await runSchedule(state.schedule.id); setState({ ...state, schedule }); setNotice("The pull starts within a few seconds."); }
    catch (reason) { setError(text(reason, "Could not start the pull.")); }
    finally { setBusy(false); }
  }

  if (state === null) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-64 w-full" />;
  if (!state.connected) return <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><CalendarClock /></EmptyMedia><EmptyTitle>Connect Jira first</EmptyTitle>
    <EmptyDescription>Save your Jira server, username and token in Jira → Settings, then set up the automatic pull here.</EmptyDescription></EmptyHeader>
    <EmptyContent><Button asChild><Link to="/jira/settings">Open Jira settings</Link></Button></EmptyContent></Empty>;
  const schedule = state.schedule;
  return <div className="flex flex-col gap-6">
    {schedule && <Card><CardHeader><CardTitle>Status</CardTitle><CardDescription>{schedule.description} · {schedule.timezone}</CardDescription></CardHeader>
      <CardContent className="flex flex-wrap items-center gap-3 text-sm">
        <ScheduleStatus schedule={schedule} />
        <span className="text-muted-foreground">{schedule.last_finished_at ? `Last pull ${when(schedule.last_finished_at)}${schedule.last_message ? ` · ${schedule.last_message}` : ""}` : "Not pulled automatically yet"}</span>
        <span className="text-muted-foreground">{schedule.next_run_at ? `Next pull ${when(schedule.next_run_at)}` : "Paused"}</span>
        <Button size="sm" variant="outline" disabled={busy || schedule.running} onClick={runNow}><Play data-icon="inline-start" />Run now</Button>
        <Link className="underline" to="/schedules">All schedules</Link>
      </CardContent></Card>}
    <form onSubmit={save} className="flex max-w-2xl flex-col gap-4">
      <FieldGroup>
        <Field orientation="horizontal"><Switch id="auto-pull-enabled" checked={enabled} disabled={busy} onCheckedChange={setEnabled} /><FieldLabel htmlFor="auto-pull-enabled">Pull tickets automatically</FieldLabel></Field>
        <Field><FieldLabel htmlFor="auto-pull-jql">Jira query (JQL)</FieldLabel>
          <Input id="auto-pull-jql" required maxLength={4000} value={jql} disabled={busy} onChange={event => setJql(event.target.value)} />
          <FieldDescription>Separate from the query on Jira → Tickets. For example <code>project = DEMO ORDER BY updated DESC</code>.</FieldDescription></Field>
        <Field className="w-40"><FieldLabel htmlFor="auto-pull-limit">Number of tickets</FieldLabel>
          <Input id="auto-pull-limit" type="number" required min={1} max={200} step={1} value={limit} disabled={busy} onChange={event => setLimit(event.target.value)} /></Field>
        <TimetableFields id="auto-pull" value={timetable} onChange={setTimetable} onProblem={setProblem} />
      </FieldGroup>
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
      <div><Button type="submit" disabled={busy || problem !== null || !jql.trim() || !timetable.cron.trim()}>{busy && <Spinner data-icon="inline-start" />}Save</Button></div>
    </form>
  </div>;
}
```
`TimetableFields` initialises `custom` from the first `value`; render it with `key={state.schedule?.id ?? "new"}` so it re-initialises after the first load.

- [ ] **Step 2: `JiraAutoPullStatus.tsx`** (Tickets status line)
```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { getAutoPull, type AutoPull } from "../lib/schedules";

/** Jira → Tickets: one line about the automatic pull, linking to its settings. Renders nothing on errors. */
export default function JiraAutoPullStatus({ refreshKey }: { refreshKey: string | null }) {
  const [state, setState] = useState<AutoPull | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getAutoPull(controller.signal).then(setState).catch(() => { if (!controller.signal.aborted) setState(null); });
    return () => controller.abort();
  }, [refreshKey]);
  if (!state || state.connected !== true) return null;
  const schedule = state.schedule;
  const summary = schedule?.enabled
    ? `Auto pull: ${schedule.description}${schedule.next_run_at ? ` · next ${new Date(schedule.next_run_at).toLocaleString()}` : ""}`
    : "Auto pull is off";
  return <p className="text-sm text-muted-foreground">{summary} · <Link className="underline" to="/jira/auto-pull">Auto pull settings</Link></p>;
}
```
In `JiraTicketsView.tsx` replace the `JiraAutoPullCard` import and usage with `JiraAutoPullStatus` (same place, same `refreshKey`). Delete `JiraAutoPullCard.tsx`.

- [ ] **Step 3: Route and sidebar**
- `routes.ts`: `route("jira/auto-pull", "./routes/jiraAutoPull.tsx"),` after `jira/tickets`.
- `routes/jiraAutoPull.tsx`: `export default function JiraAutoPullPage() { return <AdminLayout><JiraAutoPullView /></AdminLayout>; }`.
- `AdminLayout.tsx`: `const jiraAutoPull = pathname === "/jira/auto-pull";`; title `"Jira Auto pull"`, description `"Pull Jira tickets on a timetable with their own query."`; sidebar item between Tickets and Settings: `<Link to="/jira/auto-pull"><CalendarClock /><span>Auto pull</span></Link>` with `isActive={jiraAutoPull}`.
- `adminLayout.test.tsx`: assert the link `Auto pull` → `/jira/auto-pull`.

- [ ] **Step 4: Tests**

`jiraAutoPull.test.tsx`:
```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import JiraAutoPullView from "../JiraAutoPullView";
import type { AutoPull, Schedule } from "../../lib/schedules";

const schedule: Schedule = { id: "s-1", name: "Jira auto pull", target_kind: "jira.pull", target_id: "jira-1", cron: "*/15 * * * *",
  description: "Every 15 minutes", timezone: "UTC", enabled: true, next_run_at: "2026-10-09T10:15:00Z", running: false,
  last_run_at: "2026-10-09T10:00:00Z", last_finished_at: "2026-10-09T10:00:02Z", last_status: "succeeded", last_message: "Pulled 10 of 12 tickets.",
  revision: 1, params: { jql: "project = DEMO", limit: 10 } };

function api(state: AutoPull, saved?: AutoPull | [number, unknown]) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/schedules/preview") return new Response(JSON.stringify({ runs: ["2026-10-09T10:15:00Z"] }));
    if (url === "/api/schedules/s-1/run") return new Response(JSON.stringify({ schedule: { ...schedule, next_run_at: "2026-10-09T10:01:00Z" } }));
    if (url === "/api/jira/auto-pull" && init?.method === "PUT") {
      const [status, body] = Array.isArray(saved) ? saved : [200, saved];
      return new Response(JSON.stringify(body), { status });
    }
    return new Response(JSON.stringify(state));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const view = () => render(<MemoryRouter><JiraAutoPullView /></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

test("without a Jira connection it points to Settings", async () => {
  api({ schedule: null, jql: null, limit: null, connected: false });
  view();
  expect(await screen.findByText("Connect Jira first")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open Jira settings" })).toHaveAttribute("href", "/jira/settings");
});

test("saves its own query, limit and timetable", async () => {
  const user = userEvent.setup();
  const fetch = api({ schedule: null, jql: "ORDER BY updated DESC", limit: 25, connected: true },
    { schedule, jql: "project = DEMO", limit: 10, connected: true });
  view();
  const query = await screen.findByLabelText("Jira query (JQL)");
  await user.clear(query); await user.type(query, "project = DEMO");
  const count = screen.getByLabelText("Number of tickets");
  await user.clear(count); await user.type(count, "10");
  await user.click(screen.getByRole("switch", { name: "Pull tickets automatically" }));
  await screen.findByText("Next runs");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("Auto pull saved.")).toBeInTheDocument();
  const put = fetch.mock.calls.find(([url, init]) => url === "/api/jira/auto-pull" && init?.method === "PUT")!;
  expect(JSON.parse(put[1]!.body as string)).toEqual({ enabled: true, cron: "*/15 * * * *", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", jql: "project = DEMO", limit: 10 });
  expect(screen.getByText(/Pulled 10 of 12 tickets\./)).toBeInTheDocument();
});

test("shows a server error and runs now", async () => {
  const user = userEvent.setup();
  api({ schedule, jql: "project = DEMO", limit: 10, connected: true }, [400, { error: { message: "Schedules may run at most every 5 minutes." } }]);
  view();
  await user.click(await screen.findByRole("button", { name: "Save" }));
  expect(await screen.findByText("Schedules may run at most every 5 minutes.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Run now" }));
  expect(await screen.findByText("The pull starts within a few seconds.")).toBeInTheDocument();
});
```
In `jira.test.tsx`, replace the three auto pull card tests with:
```tsx
test("tickets show the auto pull status and link to its settings", async () => {
  ticketsApi({ schedule: pullSchedule, jql: "project = DEMO", limit: 10, connected: true });
  render(<MemoryRouter><JiraTicketsView /></MemoryRouter>);
  expect(await screen.findByText(/Auto pull: Every 15 minutes/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Auto pull settings" })).toHaveAttribute("href", "/jira/auto-pull");
});

test("tickets say when auto pull is off", async () => {
  ticketsApi({ schedule: null, jql: "project = DEMO", limit: 10, connected: true });
  render(<MemoryRouter><JiraTicketsView /></MemoryRouter>);
  expect(await screen.findByText(/Auto pull is off/)).toBeInTheDocument();
});

test("auto pull status stays hidden on an unexpected response", async () => {
  ticketsApi({ connection, tickets: [] });
  render(<MemoryRouter><JiraTicketsView /></MemoryRouter>);
  await screen.findByText("Check payment latency");
  expect(screen.queryByText(/Auto pull/)).toBeNull();
});
```
(add `params: {}` to `pullSchedule`; keep `ticketsApi`, drop its unused `saved` parameter if no test uses it.)

- [ ] **Step 5: Run** `npx vitest run app/components/__tests__/jiraAutoPull.test.tsx app/components/__tests__/jira.test.tsx app/components/__tests__/adminLayout.test.tsx app/components/__tests__/schedules.test.tsx` and `npx react-router typegen && npx tsc --noEmit` → pass.

- [ ] **Step 6: Commit** `feat(admin-ui): Jira → Auto pull page; Tickets shows its status`.

---

### Task 7: Administration → Events page

**Files:**
- Create: `services/admin-ui/app/lib/events.ts`, `services/admin-ui/app/components/EventsView.tsx`, `services/admin-ui/app/routes/events.tsx`
- Modify: `services/admin-ui/app/routes.ts`, `services/admin-ui/app/components/AdminLayout.tsx`, `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`
- Test: `services/admin-ui/app/components/__tests__/events.test.tsx`

**Interfaces:**
- Consumes: Task 4 API.

- [ ] **Step 1: `lib/events.ts`**
```ts
import { request } from "./api";

export interface EventRow { id: number; source: string; type: string; subject: string; title: string; occurred_at: string; created_at: string }
export interface EventDetail extends EventRow { source_id: string | null; dedupe_key: string; payload: Record<string, unknown> }
export interface EventFilters { source: string; type: string; q: string }
export type FieldChange = { from: unknown; to: unknown } | { added: string[]; removed: string[] } | { changed: true };

export const TYPE_LABELS: Record<string, string> = { "jira.ticket.created": "Ticket created", "jira.ticket.updated": "Ticket updated" };
export const SOURCE_LABELS: Record<string, string> = { jira: "Jira" };
const FIELD_LABELS: Record<string, string> = { summary: "Summary", status: "Status", issue_type: "Type", priority: "Priority", assignee: "Assignee", labels: "Labels", description: "Description" };

export function getEvents(filters: EventFilters, before?: number, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (filters.source) query.set("source", filters.source);
  if (filters.type) query.set("type", filters.type);
  if (filters.q.trim()) query.set("q", filters.q.trim());
  if (before) query.set("before", String(before));
  const suffix = query.toString();
  return request<{ events: EventRow[]; next_before: number | null }>(`/api/events${suffix ? `?${suffix}` : ""}`, { signal });
}
export const getEventFacets = (signal?: AbortSignal) => request<{ sources: string[]; types: string[] }>("/api/events/facets", { signal });
export const getEvent = (id: number, signal?: AbortSignal) => request<{ event: EventDetail }>(`/api/events/${id}`, { signal });

/** One readable line per change, e.g. "Status: Open → In Progress", "Labels: +agent −old", "Description changed". */
export function describeChanges(changes: unknown): string[] {
  if (!changes || typeof changes !== "object") return [];
  const show = (value: unknown) => value === null || value === undefined || value === "" ? "—" : String(value);
  return Object.entries(changes as Record<string, FieldChange>).map(([field, change]) => {
    const label = FIELD_LABELS[field] ?? field;
    if ("added" in change) return `${label}: ${[...change.added.map(item => `+${item}`), ...change.removed.map(item => `−${item}`)].join(" ")}`;
    if ("changed" in change) return `${label} changed`;
    return `${label}: ${show(change.from)} → ${show(change.to)}`;
  });
}
```

- [ ] **Step 2: Failing tests** (`events.test.tsx`)
```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import EventsView from "../EventsView";
import { describeChanges } from "../../lib/events";

const row = (id: number, extra = {}) => ({ id, source: "jira", type: "jira.ticket.created", subject: `DEMO-${id}`, title: `Ticket ${id}`,
  occurred_at: "2026-10-09T10:00:00Z", created_at: "2026-10-09T10:00:05Z", ...extra });

function api(pages: Record<string, unknown>) {
  const fetch = vi.fn(async (url: string) => {
    const body = pages[url];
    if (body === undefined) throw new Error(`unexpected ${url}`);
    return new Response(JSON.stringify(body));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const view = () => render(<MemoryRouter><EventsView /></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

test("lists events newest first and loads more", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": { sources: ["jira"], types: ["jira.ticket.created"] },
    "/api/events": { events: [row(3), row(2)], next_before: 2 },
    "/api/events?before=2": { events: [row(1)], next_before: null } });
  view();
  expect(await screen.findByText("DEMO-3")).toBeInTheDocument();
  expect(screen.getAllByText("Ticket created")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText("DEMO-1")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
});

test("filters by type and search", async () => {
  const user = userEvent.setup();
  const fetch = api({ "/api/events/facets": { sources: ["jira"], types: ["jira.ticket.created", "jira.ticket.updated"] },
    "/api/events": { events: [row(2)], next_before: null },
    "/api/events?type=jira.ticket.updated": { events: [row(9, { type: "jira.ticket.updated" })], next_before: null },
    "/api/events?type=jira.ticket.updated&q=DEMO-9": { events: [row(9, { type: "jira.ticket.updated" })], next_before: null } });
  view();
  await screen.findByText("DEMO-2");
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  await user.click(screen.getByRole("option", { name: "Ticket updated" }));
  expect(await screen.findByText("DEMO-9")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Ticket or title"), "DEMO-9");
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === "/api/events?type=jira.ticket.updated&q=DEMO-9")).toBe(true));
});

test("an event opens with its changes and a Jira link", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": { sources: ["jira"], types: ["jira.ticket.updated"] },
    "/api/events": { events: [row(7, { type: "jira.ticket.updated" })], next_before: null },
    "/api/events/7": { event: { ...row(7, { type: "jira.ticket.updated" }), source_id: "c-1", dedupe_key: "jira:c-1:7:x",
      payload: { url: "https://jira.internal/jira/browse/DEMO-7", changes: { status: { from: "Open", to: "Done" }, labels: { added: ["agent"], removed: [] } } } } } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Status: Open → Done")).toBeInTheDocument();
  expect(within(sheet).getByText("Labels: +agent")).toBeInTheDocument();
  expect(within(sheet).getByRole("link", { name: "Open in Jira" })).toHaveAttribute("href", "https://jira.internal/jira/browse/DEMO-7");
});

test("empty state", async () => {
  api({ "/api/events/facets": { sources: [], types: [] }, "/api/events": { events: [], next_before: null } });
  view();
  expect(await screen.findByText("No events yet. Turn on Jira → Auto pull to record ticket changes.")).toBeInTheDocument();
});

test("describeChanges", () => {
  expect(describeChanges({ assignee: { from: null, to: "Ana" }, description: { changed: true }, labels: { added: ["a"], removed: ["b"] } }))
    .toEqual(["Assignee: — → Ana", "Description changed", "Labels: +a −b"]);
  expect(describeChanges(undefined)).toEqual([]);
});
```

- [ ] **Step 3: `EventsView.tsx`** — behavior to implement (follow `SchedulesView.tsx` style and shadcn components):
  - State: `filters {source:"", type:"", q:""}`, `events: EventRow[] | null`, `nextBefore`, `facets`, `selected: EventDetail | null`, `error`.
  - Load facets once. Load the first page whenever `source`/`type` change, and 300 ms after `q` stops changing (debounce); abort the previous request; a response from an older filter set is ignored.
  - Filters row: `Select` **Source** (`aria-label="Source"`, options "All sources" + facets mapped through `SOURCE_LABELS`), `Select` **Type** (`aria-label="Type"`, "All types" + facets through `TYPE_LABELS`), `Input` with label **Ticket or title**, and a **Refresh** button (reloads page 1 and facets). Radix Select cannot use `""` as an item value — use `"all"` for the "All …" item and map it to `""`.
  - Table: Time (`occurred_at` local time), Type (label or raw type), Subject (font-medium), Title. The whole row is clickable (`role="button"`-free: put the click on a `<button>` inside the Subject cell styled as a link, or on the row with `onClick` and `tabIndex={0}` + Enter key) and opens the sheet.
  - **Load more** appends `getEvents(filters, nextBefore)`; hidden when `nextBefore` is null.
  - Empty (no events and no filters): `Empty` with `No events yet. Turn on Jira → Auto pull to record ticket changes.`; with filters: `No events match these filters.`
  - Sheet (`Sheet`, `SheetTitle` = `{subject} · {type label}`, `SheetDescription` = title): rows Happened (`occurred_at`), Recorded (`created_at`), Source; **Open in Jira** link when `payload.url` is a string (`target="_blank" rel="noreferrer"`); **Changes** list from `describeChanges(payload.changes)` (hidden when empty); a `<details><summary>Raw payload</summary><pre>` with `JSON.stringify(payload, null, 2)`.
  - Errors: an `Alert` with the API message.

- [ ] **Step 4: Route and sidebar**
  - `routes.ts`: `route("events", "./routes/events.tsx"),` after `schedules`.
  - `routes/events.tsx`: `<AdminLayout><EventsView /></AdminLayout>`.
  - `AdminLayout.tsx`: `const eventsPage = pathname === "/events";` title `"Events"`, description `"Things that happened at your sources, such as Jira ticket changes found by Auto pull."`; sidebar item after Schedules: `<Link to="/events"><Inbox /><span>Events</span></Link>` (`Inbox` from lucide-react).
  - `adminLayout.test.tsx`: assert the `Events` link → `/events`.

- [ ] **Step 5: Run** `npx vitest run app/components/__tests__/events.test.tsx app/components/__tests__/adminLayout.test.tsx` and `npx react-router typegen && npx tsc --noEmit` → pass.

- [ ] **Step 6: Commit** `feat(admin-ui): Administration → Events`.

---

### Task 8: Docs and full verification

**Files:** `docs/migrations.md`, `docs/user-guide.md`, `deploy/README.md`

- [ ] **Step 1: `docs/migrations.md`** — after `0086_schedules` add the 0087 section below and a 0088 section:
```markdown
## 0087_schedule_params

`ka.schedules.params` (jsonb object, default `{}`) holds target settings. For `jira.pull` it is `{jql, limit}`: the
automatic pull's own query, separate from the manual Fetch tickets query; without it the pull uses the connection's last
fetch query and limit. The down migration drops the column (pulls fall back to the last fetch query).

## 0088_events

`ka.events` records things that happened at a source, as a queue for later handling (no status yet). The scheduled
Jira pull records `jira.ticket.created` for a ticket it has not recorded before and `jira.ticket.updated` (with
`changes`) when Jira's `updated` moved; `dedupe_key` (`jira:<connection>:<issue>:<updated>`, unique per project) keeps
repeated pulls from recording twice. The partial index `events_jira_issues` serves the pull's lookup of each ticket's
latest event. The down migration drops the table and every recorded event.
```
- [ ] **Step 2: `docs/user-guide.md`**
  - 4.1 Navigation: Jira group lists Tickets, Auto pull, Settings.
  - 4.8 Jira: replace the "**Auto pull.**" paragraph with:
```markdown
**Auto pull.** Jira → Auto pull fetches tickets on a timetable with its own query and ticket limit (separate from the
query on Jira → Tickets). Switch on **Pull tickets automatically**, enter the JQL and number of tickets, pick how often
(a preset or a custom cron expression) and the time zone, check the preview of the next runs, and **Save**. The page
shows the last pull and the next one; **Run now** pulls at once. Jira → Tickets shows a one-line summary. A failed pull
is shown and retried at the next scheduled time; a pull that meets a manual fetch is skipped. Each scheduled pull also
records an event for every ticket that is new or changed since its last pull (see Administration → Events); the manual
Fetch tickets does not.
```
  - 4.16 Schedules: add after the first sentence: "**New schedule** creates one: choose what to run (today: Jira tickets, which uses the query from Jira → Auto pull) and when. **Delete** removes a schedule and its history."
  - Add `### 4.17 Events` after 4.16:
```markdown
### 4.17 Events

Administration → Events lists what happened at your sources, newest first. Today the source is Jira: every scheduled
pull (Jira → Auto pull, or Run now) records **Ticket created** for a ticket it sees for the first time and **Ticket
updated** when a ticket changed in Jira, with what changed (status, labels, assignee, priority, type, summary,
description). Filter by source and type, search by ticket key or title, and open an event to see its changes, a link to
the ticket and the raw data. The very first pull records every ticket it fetches. Events are kept for handling by
agents in a later version; nothing acts on them yet.
```
  - 4.1 Navigation: the Administration group also lists Events after Schedules.
- [ ] **Step 3:** (no `deploy/README.md` change in this plan.)
- [ ] **Step 4: Full verification** — `bash db/tests/run.sh` (exit 0); `cd services/admin-api && uv run pytest -q` (only the two known failures: `test_deploy.py::test_docs_commands_serve_the_docs_folder_inside_the_data_root`, `test_environments.py::test_migration_reapply_and_populated_rollback_protection`; ~5 min); `cd services/admin-ui && npx vitest run && npx react-router typegen && npx tsc --noEmit`. Report each suite's summary line.
- [ ] **Step 5: Commit** `docs: Jira Auto pull page, New schedule and events`.
