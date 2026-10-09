# Jira Auto pull page, New schedule, live Jira mock — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto pull becomes its own Jira page with its own query and timetable; Administration → Schedules can create and delete schedules; the Jira mock can change tickets.

**Architecture:** `ka.schedules.params` (0087) holds target settings (the pull's `jql`/`limit`); `jira_sync.auto_pull` reads them. `schedules_api.py` gains create/delete and the extended auto-pull endpoints. A shared `TimetableFields` component serves Edit, New schedule and the new `JiraAutoPullView`. The mock gets in-memory write endpoints and an optional activity thread.

**Tech Stack:** PostgreSQL (golang-migrate), FastAPI + psycopg3, React Router 7 + shadcn + Vitest, Python stdlib HTTP server (mock).

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
- Mock: JSON body ≤ 65536 bytes; max 1000 issues; statuses Open(1) → In Progress(2) → Done(3); env `JIRA_MOCK_ACTIVITY_SECONDS` (0 = off).

## Review Focus

1. A schedule created on the Schedules page before the Auto pull page was ever saved → it pulls with the connection's JQL and last limit (or 25), not with nothing. Test in Task 1 (`test_auto_pull_without_params_falls_back_to_the_connection`) and Task 2 (`test_create_jira_schedule_copies_the_connection_defaults`).
2. A manual Fetch tickets with a different query after Auto pull was saved → the auto pull keeps its own query. Test in Task 1 (`test_auto_pull_uses_its_own_query_after_a_manual_fetch`).
3. Deleting a schedule while it runs → the run finishes without error and records nothing; the row and its history are gone. Test in Task 2 (`test_deleting_a_running_schedule`).
4. Old clients sending `PUT /api/jira/auto-pull` without `jql`/`limit` → keep the stored values. Test in Task 2 (`test_auto_pull_without_query_keeps_the_stored_one`).
5. Mock under concurrent writes and the activity thread → no lost or duplicate keys; search stays sorted. Test in Task 5 (`test_concurrent_creates_get_unique_keys`).

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

### Task 3: Schedules page — shared timetable fields, New schedule, Delete

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

### Task 4: Jira → Auto pull page and the Tickets status line

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

### Task 5: Live Jira mock

**Files:**
- Modify: `deploy/jira-mock/server.py`, `deploy/compose.yaml` (jira-mock `environment`), `Makefile` (`jira-mock-reset`)
- Test: `services/admin-api/tests/test_jira_mock.py` (new)

**Interfaces:**
- Produces: mock endpoints listed in the spec; `tick()` function (one activity step) and module-level `ISSUES`, `COMMENTS`, `LOCK`.

- [ ] **Step 1: Failing tests** (`tests/test_jira_mock.py`)
```python
"""The local Jira mock accepts writes: create, edit, transition, comment; search is newest-updated first."""
import json
import runpy
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

SERVER = Path(__file__).resolve().parents[3] / 'deploy/jira-mock/server.py'
AUTH = {'Authorization': 'Bearer jira-mock-token'}


@pytest.fixture
def mock():
    module = runpy.run_path(str(SERVER))  # fresh in-memory state per test
    server = ThreadingHTTPServer(('127.0.0.1', 0), module['Handler'])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}/jira/rest/api/2'
    with httpx.Client(base_url=base, headers=AUTH, timeout=5) as client:
        yield client, module
    server.shutdown()


def search(client, jql='project = DEMO'):
    return client.get('/search', params={'jql': jql, 'startAt': 0, 'maxResults': 10}).json()


def test_create_puts_the_new_issue_first(mock):
    client, _ = mock
    created = client.post('/issue', json={'fields': {'summary': 'Agent needed', 'labels': ['needs-agent']}})
    assert created.status_code == 201 and created.json()['key'] == 'DEMO-76'
    first = search(client)['issues'][0]
    assert first['key'] == 'DEMO-76' and first['fields']['status']['name'] == 'Open' and first['fields']['labels'] == ['needs-agent']
    assert search(client, 'labels = needs-agent')['total'] == 1


def test_edit_transition_and_comment_bump_updated(mock):
    client, _ = mock
    before = client.get('/issue/DEMO-50').json()['fields']['updated']
    assert client.put('/issue/DEMO-50', json={'fields': {'summary': 'Renamed'}}).status_code == 204
    transitions = client.get('/issue/DEMO-50/transitions').json()['transitions']
    assert [t['to']['name'] for t in transitions] == ['Open', 'In Progress', 'Done']
    assert client.post('/issue/DEMO-50/transitions', json={'transition': {'id': '3'}}).status_code == 204
    assert client.post('/issue/DEMO-50/comment', json={'body': 'Looked at it'}).status_code == 201
    issue = client.get('/issue/DEMO-50').json()
    assert issue['fields']['summary'] == 'Renamed' and issue['fields']['status']['name'] == 'Done' and issue['fields']['updated'] > before
    assert search(client)['issues'][0]['key'] == 'DEMO-50'
    comments = client.get('/issue/DEMO-50/comment', params={'startAt': 0, 'maxResults': 100}).json()
    assert comments['total'] == 6  # 5 seeded + the new one, appended last


def test_bad_writes_are_refused(mock):
    client, _ = mock
    assert client.post('/issue', content=b'not json', headers={'Content-Type': 'application/json'}).status_code == 400
    assert client.post('/issue', json={'fields': {}}).status_code == 400
    assert client.post('/issue', content=b'{"fields": {"summary": "' + b'x' * 70000 + b'"}}').status_code == 400
    assert client.post('/issue/DEMO-1/transitions', json={'transition': {'id': '9'}}).status_code == 400
    assert client.put('/issue/DEMO-999', json={'fields': {'summary': 'x'}}).status_code == 404
    assert httpx.post(str(client.base_url) + '/issue', json={'fields': {'summary': 'x'}}).status_code == 401


def test_concurrent_creates_get_unique_keys(mock):
    client, _ = mock
    with ThreadPoolExecutor(8) as pool:
        keys = list(pool.map(lambda n: client.post('/issue', json={'fields': {'summary': f'n{n}'}}).json()['key'], range(40)))
    assert len(set(keys)) == 40
    updated = [issue['fields']['updated'] for issue in search(client)['issues']]
    assert updated == sorted(updated, reverse=True)


def test_activity_creates_and_moves_tickets(mock):
    client, module = mock
    module['tick']()  # creates
    module['tick']()  # moves the oldest-updated ticket that is not Done
    issues = search(client)['issues']
    assert issues[0]['fields']['summary'].startswith('Simulated ticket') or issues[1]['fields']['summary'].startswith('Simulated ticket')
    assert search(client)['total'] == 76
```
(`runpy.run_path` gives each test fresh state, because the module's globals are rebuilt. The handler must read `ISSUES`/`COMMENTS` from its own module globals — which it does when defined in the same file.)

The seed has 5 comments per issue; new comments are appended last, with `created`/`updated` in the seed's `+0000` format.

- [ ] **Step 2: Implement in `server.py`**
- `LOCK = threading.Lock()`; `MAX_BODY = 65536`; `MAX_ISSUES = 1000`; `STATUSES = {'1': 'Open', '2': 'In Progress', '3': 'Done'}`.
- `now()` returns `datetime.now(UTC).isoformat(timespec='milliseconds')` in the same format family the seed uses (both parse with `datetime.fromisoformat`; ensure string comparison sorts correctly — use the same format for seeds by normalising seed `updated` with the same function).
- `ordered(issues)`: sort by `(updated, number)` descending, number = int of the key suffix. `filter_issues` returns `ordered(result)`; reads happen under `LOCK` (copy the list).
- `read_json(handler) -> dict`: rejects missing/oversized `Content-Length` (> MAX_BODY) and invalid JSON or non-object with 400 `{"errorMessages": ["Invalid JSON body"]}`.
- `check_auth()`: the same token rules as `do_GET`; share one helper used by GET/POST/PUT.
- Field validation for create/edit: `summary` non-empty str ≤ 255; `description` str; `labels` list of str; `issuetype.name`, `priority.name` str. Unknown keys ignored. Create requires `summary`.
- `do_POST`: `/issue` (create; 400 when `len(ISSUES) >= MAX_ISSUES`: "Mock issue limit reached"), `/issue/{k}/transitions`, `/issue/{k}/comment`. `do_PUT`: `/issue/{k}`. Respond 204 with no body via a `respond_empty()` helper.
- `GET /issue/{k}/transitions`.
- `tick()`: under `LOCK`, alternate (module counter) between creating `Simulated ticket <n>` and advancing the oldest-updated non-Done issue to the next status.
- `__main__`: if `int(os.environ.get('JIRA_MOCK_ACTIVITY_SECONDS', '0')) > 0`, start a daemon thread looping `time.sleep(seconds); tick()`.
- Keep `log_message` silent.

- [ ] **Step 3: Compose and Makefile**
- `deploy/compose.yaml` jira-mock: `environment: {JIRA_MOCK_ACTIVITY_SECONDS: "${JIRA_MOCK_ACTIVITY_SECONDS:-0}"}`.
- `Makefile`: add `jira-mock-reset` to the `.PHONY` line and
```make
jira-mock-reset:
	$(COMPOSE) --profile jira-mock restart jira-mock
```

- [ ] **Step 4: Run** `uv run pytest tests/test_jira_mock.py tests/test_jira.py tests/test_jira_sync.py -q` → pass (test_jira loads `ISSUES` from server.py; keep `ISSUES[:7]` compatible).

- [ ] **Step 5: Commit** `feat(jira-mock): writes, transitions, comments, simulated activity and reset`.

---

### Task 6: Docs and full verification

**Files:** `docs/migrations.md`, `docs/user-guide.md`, `deploy/README.md`

- [ ] **Step 1: `docs/migrations.md`** — after `0086_schedules`:
```markdown
## 0087_schedule_params

`ka.schedules.params` (jsonb object, default `{}`) holds target settings. For `jira.pull` it is `{jql, limit}`: the
automatic pull's own query, separate from the manual Fetch tickets query; without it the pull uses the connection's last
fetch query and limit. The down migration drops the column (pulls fall back to the last fetch query).
```
- [ ] **Step 2: `docs/user-guide.md`**
  - 4.1 Navigation: Jira group lists Tickets, Auto pull, Settings.
  - 4.8 Jira: replace the "**Auto pull.**" paragraph with:
```markdown
**Auto pull.** Jira → Auto pull fetches tickets on a timetable with its own query and ticket limit (separate from the
query on Jira → Tickets). Switch on **Pull tickets automatically**, enter the JQL and number of tickets, pick how often
(a preset or a custom cron expression) and the time zone, check the preview of the next runs, and **Save**. The page
shows the last pull and the next one; **Run now** pulls at once. Jira → Tickets shows a one-line summary. A failed pull
is shown and retried at the next scheduled time; a pull that meets a manual fetch is skipped.
```
  - Replace the jira-mock lines (around "For testing without a real Jira") with:
```markdown
For testing without a real Jira: `make jira-mock-up`, then URL `http://jira-mock:8080/jira`, user `mock-admin`, token
`jira-mock-token` (75 demo tickets). The mock accepts the Jira REST writes (create issue, edit, transition, comment), so
you can change tickets and watch the auto pull pick them up. Set `JIRA_MOCK_ACTIVITY_SECONDS=60` in `.env` before
`make jira-mock-up` to have it create or move a ticket every minute. `make jira-mock-reset` restores the 75 tickets;
`make jira-mock-down` stops it.
```
  - 4.16 Schedules: add after the first sentence: "**New schedule** creates one: choose what to run (today: Jira tickets, which uses the query from Jira → Auto pull) and when. **Delete** removes a schedule and its history."
- [ ] **Step 3: `deploy/README.md`** — in the `jira-mock` bullet add: "It accepts Jira REST writes; `JIRA_MOCK_ACTIVITY_SECONDS` (default 0) makes it change a ticket every N seconds; `make jira-mock-reset` restarts it with the original tickets."
- [ ] **Step 4: Full verification** — `bash db/tests/run.sh` (exit 0); `cd services/admin-api && uv run pytest -q` (only the two known failures: `test_deploy.py::test_docs_commands_serve_the_docs_folder_inside_the_data_root`, `test_environments.py::test_migration_reapply_and_populated_rollback_protection`; ~5 min); `cd services/admin-ui && npx vitest run && npx react-router typegen && npx tsc --noEmit`. Report each suite's summary line.
- [ ] **Step 5: Commit** `docs: Jira Auto pull page, New schedule, live Jira mock`.
