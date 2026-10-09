# Schedules (cron) and Jira auto pull Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run work on a cron timetable; the first job is the Jira ticket pull, switched on from Jira → Tickets and managed on Administration → Schedules.

**Architecture:** `ka.schedules` rows (cron + time zone + one target) are claimed by a scheduler thread inside repository-worker with a lease (`FOR UPDATE SKIP LOCKED`), run through a `TARGETS` registry, and recorded in `ka.schedule_runs`. `croniter` computes fire times. The Jira fetch moves out of the endpoint into `jira_sync.py`, shared by the Fetch tickets button and the `jira.pull` target.

**Tech Stack:** PostgreSQL migrations (golang-migrate), FastAPI + psycopg3, `croniter`, `tzdata`; React Router 7 + shadcn + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-09-schedules-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/schedules` (branch `feature/schedules`). Never edit the main checkout.
- Never run `make` targets; never touch `.env` or anything under `.local`; never touch the running Docker stack's database. Tests use their own throwaway Postgres (pytest fixture `store`, `db/tests/run.sh`).
- Never `git stash`; never `git add .superpowers`.
- Credentials (the Jira token) are never logged, stored unencrypted, put in messages or passed to Bash.
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- admin-api tests: `cd services/admin-api && uv run pytest tests/<file> -q`. admin-ui: `cd services/admin-ui && npx vitest run <file>` and `npx react-router typegen && npx tsc --noEmit`. If `services/admin-ui/node_modules` is missing in the worktree, link it: `ln -s /Users/graovic/fincore/knowledge-agent-v3/services/admin-ui/node_modules services/admin-ui/node_modules` (never commit the link; it is already ignored by `.gitignore` as `node_modules`).
- Exact texts (copy verbatim):
  - `Schedules may run at most every 5 minutes.`
  - `Use a cron expression with five fields, for example */15 * * * *.`
  - `Choose a time zone from the list.`
  - `Target no longer exists.`
  - `The run failed unexpectedly. See the repository-worker log.`
  - `A manual fetch ran at the same time.`
  - `Pulled {fetched} of {total} tickets.`
  - `Schedule not found.` · `Schedule changed. Reload and try again.` · `This schedule is already running.` · `Save Jira settings first.`
  - Empty Schedules page: `No schedules yet. Turn on Auto pull on Jira → Tickets.`
- Presets (cron → words): `*/15 * * * *` Every 15 minutes · `*/30 * * * *` Every 30 minutes · `0 * * * *` Every hour · `0 */6 * * *` Every 6 hours · `0 6 * * *` Every day at 06:00.
- Lease 15 minutes; poll every 5 seconds; keep 100 runs per schedule; runs listing returns newest 20; Schedules page refreshes every 10 seconds; default pull limit 25.
- Audit actions: `schedule.saved`, `schedule.run_requested`, `jira.tickets_auto_pulled` (actor empty).

## Review Focus

1. A schedule is paused (or edited) while its run is in progress → when the run finishes the schedule stays paused (`next_run_at` NULL) and keeps the new cron. Test in Task 4 (`test_pausing_during_a_run_keeps_it_paused`).
2. A worker dies mid-run → after the lease runs out another worker runs it, and the dead worker's late finish records nothing. Test in Task 4 (`test_expired_lease_is_claimable_and_the_stale_finish_records_nothing`).
3. The Jira token is removed after auto pull was switched on → the run fails with the same readable text the button shows, the schedule keeps its next run. Test in Task 3 (`test_auto_pull_without_token_raises_the_button_error`) and Task 4 (HTTPException → failed with detail).
4. A time zone the server does not know (browser sends an odd name) or a too-frequent custom cron → 400 with the exact text, shown under the field in the editor. Tests in Task 5 and Task 6.
5. Existing Jira Tickets tests mock `fetch` with one generic response for every URL → the new Auto pull card must render nothing (not "undefined") when the auto-pull response has no numeric `limit`. Test in Task 7 (`test("auto pull card stays hidden on an unexpected response")`).

---

### Task 1: Migration 0086 (schedules, schedule_runs)

**Files:**
- Create: `db/migrations/0086_schedules.up.sql`, `db/migrations/0086_schedules.down.sql`
- Modify: `db/tests/run.sh` (one more `down 1` at the start of the rollback chain; one PASS check after the 0085 check)
- Test: `services/admin-api/tests/test_schedules_migration.py`

**Interfaces:**
- Produces: tables `ka.schedules` and `ka.schedule_runs` exactly as below; later tasks read/write these columns.

- [ ] **Step 1: Write the failing test**

```python
"""0086: schedules (a cron expression in a time zone running one target) and their run history."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0086_schedules.up.sql', '0086_schedules.down.sql'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_schedule(conn, target=None, **values) -> dict:
    project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES (%s,'Shop') RETURNING id", (f'shop-{uuid4().hex[:8]}',)).fetchone()['id']
    row = {'project_id': project, 'name': 'Jira auto pull', 'target_kind': 'jira.pull', 'target_id': target or uuid4(),
           'cron': '*/15 * * * *', **values}
    columns = ','.join(row)
    return conn.execute(f"INSERT INTO ka.schedules({columns}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *",
                        tuple(row.values())).fetchone()


def test_defaults_and_checks(store):
    with store.connect() as conn:
        schedule = a_schedule(conn)
        assert schedule['timezone'] == 'UTC' and schedule['enabled'] is True and schedule['revision'] == 1
        assert schedule['run_requested'] is False and schedule['next_run_at'] is None and schedule['last_status'] is None
    for bad in [{'target_kind': 'agent.run'}, {'last_status': 'working'}, {'name': ' '}, {'revision': 0}]:
        with pytest.raises(psycopg.errors.IntegrityError), store.connect() as conn:
            a_schedule(conn, **bad)


def test_one_schedule_per_target(store):
    target = uuid4()
    with store.connect() as conn:
        first = a_schedule(conn, target)
    with pytest.raises(psycopg.errors.UniqueViolation), store.connect() as conn:
        conn.execute("""INSERT INTO ka.schedules(project_id,name,target_kind,target_id,cron)
            VALUES (%s,'Again','jira.pull',%s,'0 * * * *')""", (first['project_id'], target))


def test_runs_are_deleted_with_their_schedule_and_checked(store):
    with store.connect() as conn:
        schedule = a_schedule(conn)
        conn.execute("""INSERT INTO ka.schedule_runs(schedule_id,trigger,started_at,finished_at,status,message)
            VALUES (%s,'manual',now(),now(),'succeeded','Pulled 1 of 1 tickets.')""", (schedule['id'],))
        conn.execute('DELETE FROM ka.schedules WHERE id=%s', (schedule['id'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.schedule_runs').fetchone()['n'] == 0
    with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
        schedule = a_schedule(conn)
        conn.execute("""INSERT INTO ka.schedule_runs(schedule_id,trigger,started_at,finished_at,status)
            VALUES (%s,'cron',now(),now(),'succeeded')""", (schedule['id'],))


def test_down_drops_both_tables_and_up_restores_them(store):
    exists = "SELECT to_regclass('ka.schedules') IS NOT NULL AND to_regclass('ka.schedule_runs') IS NOT NULL AS found"
    with store.connect() as conn:
        a_schedule(conn)
        run(conn, DOWN)
        assert conn.execute("SELECT to_regclass('ka.schedules') IS NULL AND to_regclass('ka.schedule_runs') IS NULL AS gone").fetchone()['gone']
        run(conn, UP)
        assert conn.execute(exists).fetchone()['found'] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest tests/test_schedules_migration.py -q`
Expected: FAIL (`relation "ka.schedules" does not exist`).

- [ ] **Step 3: Write the migrations**

`db/migrations/0086_schedules.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- A schedule runs one target (a named job, e.g. the Jira pull of one connection) on a cron timetable in a time zone.
-- repository-worker claims due rows with a lease; next_run_at is NULL while paused unless Run now is pending.
CREATE TABLE schedules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name nonblank NOT NULL,
    target_kind text NOT NULL CHECK (target_kind IN ('jira.pull')),
    target_id uuid NOT NULL,
    cron text NOT NULL,
    timezone text NOT NULL DEFAULT 'UTC',
    enabled boolean NOT NULL DEFAULT true,
    next_run_at timestamptz,
    run_requested boolean NOT NULL DEFAULT false,
    lease_expires_at timestamptz,
    last_run_at timestamptz,
    last_finished_at timestamptz,
    last_status text CHECK (last_status IN ('succeeded', 'failed', 'skipped')),
    last_message text,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, target_kind, target_id)
);
COMMENT ON COLUMN schedules.target_id IS 'The target''s row, e.g. jira_connections.id for jira.pull (no foreign key: targets live in different tables).';
COMMENT ON COLUMN schedules.run_requested IS 'Run now is pending; its history row gets trigger manual.';
CREATE INDEX schedules_due ON schedules(next_run_at) WHERE next_run_at IS NOT NULL;

-- The newest 100 runs of each schedule (the worker deletes older ones).
CREATE TABLE schedule_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    schedule_id uuid NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    trigger text NOT NULL CHECK (trigger IN ('schedule', 'manual')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('succeeded', 'failed', 'skipped')),
    message text NOT NULL DEFAULT ''
);
CREATE INDEX schedule_runs_newest ON schedule_runs(schedule_id, id DESC);

COMMIT;
```

`db/migrations/0086_schedules.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DROP TABLE schedule_runs;
DROP TABLE schedules;

COMMIT;
```

- [ ] **Step 4: Update `db/tests/run.sh`**

Directly after the line `run_migrate postgres up >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }` that is followed by the first `run_migrate postgres down 1` (the second `up` of the fresh-install block), insert before that first `down 1`:

```bash
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
schedules_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.schedules') IS NULL AND to_regclass('ka.schedule_runs') IS NULL")
[ "$schedules_removed" = t ] || { echo 'FAIL: 0086 down left the schedule tables' >&2; exit 1; }
```

(The existing chain then continues with `down 1` for 0085, `down 1` for 0084 and the `rolled_back_version = 83` check, unchanged.)

After the line `printf '%s\n' 'PASS: agents can be experts; expert rows are never given to subagents.'` add:

```bash
# 0086 adds schedules and their run history.
schedules=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.schedules') IS NOT NULL AND to_regclass('ka.schedule_runs') IS NOT NULL
        AND to_regclass('ka.schedules_due') IS NOT NULL")
[ "$schedules" = t ] || { echo 'FAIL: 0086 schedule tables missing' >&2; exit 1; }
printf '%s\n' 'PASS: schedules and their run history exist.'
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest tests/test_schedules_migration.py -q` → 4 passed.
Run: `bash db/tests/run.sh` from the worktree root → exit 0 and the new PASS line.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/0086_schedules.up.sql db/migrations/0086_schedules.down.sql db/tests/run.sh services/admin-api/tests/test_schedules_migration.py
git commit -m "feat(db): 0086 schedules and schedule runs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Cron rules (`schedules.py`) and the `croniter` dependency

**Files:**
- Modify: `services/admin-api/pyproject.toml`, `services/admin-api/uv.lock` (via `uv add`)
- Create: `services/admin-api/src/knowledge_admin/schedules.py`
- Test: `services/admin-api/tests/test_schedules.py`

**Interfaces:**
- Produces (module `knowledge_admin.schedules`):
  - `class ScheduleError(ValueError)` — message is the user text.
  - `class TargetMissing(Exception)` — a target raises it when its row is gone.
  - `MISSING = 'Target no longer exists.'`, `TOO_OFTEN`, `BAD_CRON`, `BAD_TIMEZONE`, `PRESETS: dict[str, str]`.
  - `validate(cron: str, timezone: str) -> None` (raises `ScheduleError`).
  - `upcoming(cron: str, timezone: str, after: datetime, count: int) -> list[datetime]` (UTC, aware).
  - `next_after(cron: str, timezone: str, after: datetime) -> datetime` (UTC, aware).
  - `describe(cron: str) -> str`.

- [ ] **Step 1: Add the dependencies**

Run: `cd services/admin-api && uv add croniter tzdata`
(`tzdata` makes `zoneinfo` work in the slim Docker image.) Check `pyproject.toml` now lists both and `uv.lock` changed.

- [ ] **Step 2: Write the failing test**

```python
"""Cron rules: five fields, a known time zone, at most every 5 minutes, fire times in the schedule's time zone."""
from datetime import UTC, datetime

import pytest

from knowledge_admin.schedules import (BAD_CRON, BAD_TIMEZONE, TOO_OFTEN, ScheduleError, describe, next_after,
                                       upcoming, validate)


@pytest.mark.parametrize('cron', ['*/15 * * * *', '*/5 * * * *', '0 6 * * *', '0 7 * * 1-5', ' 0  *  * * * '])
def test_valid(cron):
    validate(cron, 'Europe/Belgrade')


@pytest.mark.parametrize('cron,message', [
    ('', BAD_CRON), ('* * * *', BAD_CRON), ('0 0 * * * *', BAD_CRON), ('61 * * * *', BAD_CRON), ('every hour', BAD_CRON),
    ('*/4 * * * *', TOO_OFTEN), ('* * * * *', TOO_OFTEN), ('0-4 * * * *', TOO_OFTEN), ('0,3 9 * * *', TOO_OFTEN),
])
def test_invalid(cron, message):
    with pytest.raises(ScheduleError, match=message.replace('*', r'\*').replace('.', r'\.')):
        validate(cron, 'UTC')


@pytest.mark.parametrize('timezone', ['Mars/Base', '', 'utc+2', '../etc/passwd'])
def test_unknown_time_zone(timezone):
    with pytest.raises(ScheduleError) as error:
        validate('0 * * * *', timezone)
    assert str(error.value) == BAD_TIMEZONE


def test_fire_times_follow_daylight_saving_time():
    # Belgrade leaves summer time on 2026-10-25: 09:00 local is 07:00 UTC before and 08:00 UTC after.
    first = next_after('0 9 * * *', 'Europe/Belgrade', datetime(2026, 10, 23, 12, tzinfo=UTC))
    assert first == datetime(2026, 10, 24, 7, tzinfo=UTC)
    assert next_after('0 9 * * *', 'Europe/Belgrade', first) == datetime(2026, 10, 25, 8, tzinfo=UTC)


def test_upcoming_is_ascending_utc():
    runs = upcoming('*/15 * * * *', 'UTC', datetime(2026, 10, 9, 10, 7, tzinfo=UTC), 3)
    assert runs == [datetime(2026, 10, 9, 10, 15, tzinfo=UTC), datetime(2026, 10, 9, 10, 30, tzinfo=UTC),
                    datetime(2026, 10, 9, 10, 45, tzinfo=UTC)]
    assert all(run.tzinfo == UTC for run in runs)


def test_describe_presets_and_custom():
    assert describe('*/15 * * * *') == 'Every 15 minutes'
    assert describe(' 0  6 * * * ') == 'Every day at 06:00'
    assert describe('0 7 * * 1-5') == '0 7 * * 1-5'
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest tests/test_schedules.py -q`
Expected: FAIL (`ModuleNotFoundError: knowledge_admin.schedules`).

- [ ] **Step 4: Write the module**

`services/admin-api/src/knowledge_admin/schedules.py`:

```python
"""Schedules run one target (a named job, e.g. the Jira pull) on a cron timetable in a time zone. This module holds the
cron rules; scheduler.py claims and runs due schedules, schedules_api.py edits them."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, available_timezones

from croniter import croniter

MIN_INTERVAL = timedelta(minutes=5)
CHECKED_FIRES = 11  # the gaps between the next 11 fire times are checked against MIN_INTERVAL
TOO_OFTEN = 'Schedules may run at most every 5 minutes.'
BAD_CRON = 'Use a cron expression with five fields, for example */15 * * * *.'
BAD_TIMEZONE = 'Choose a time zone from the list.'
MISSING = 'Target no longer exists.'
PRESETS = {
    '*/15 * * * *': 'Every 15 minutes',
    '*/30 * * * *': 'Every 30 minutes',
    '0 * * * *': 'Every hour',
    '0 */6 * * *': 'Every 6 hours',
    '0 6 * * *': 'Every day at 06:00',
}


class ScheduleError(ValueError):
    """A cron expression or time zone a schedule may not use; the message is shown to the user."""


class TargetMissing(Exception):
    """The target's row (e.g. the Jira connection) no longer exists."""


def normal(cron: str) -> str:
    return ' '.join(cron.split())


def upcoming(cron: str, timezone: str, after: datetime, count: int) -> list[datetime]:
    """The next `count` fire times after `after`, computed in the schedule's time zone and returned in UTC."""
    times = croniter(normal(cron), after.astimezone(ZoneInfo(timezone)))
    return [times.get_next(datetime).astimezone(UTC) for _ in range(count)]


def next_after(cron: str, timezone: str, after: datetime) -> datetime:
    return upcoming(cron, timezone, after, 1)[0]


def validate(cron: str, timezone: str) -> None:
    expression = normal(cron)
    if len(expression.split(' ')) != 5 or not croniter.is_valid(expression):
        raise ScheduleError(BAD_CRON)
    if timezone not in available_timezones():
        raise ScheduleError(BAD_TIMEZONE)
    times = upcoming(expression, timezone, datetime.now(UTC), CHECKED_FIRES)
    if any(later - earlier < MIN_INTERVAL for earlier, later in zip(times, times[1:])):
        raise ScheduleError(TOO_OFTEN)


def describe(cron: str) -> str:
    """Plain words for a preset; any other expression is shown as written."""
    expression = normal(cron)
    return PRESETS.get(expression, expression)
```

Note: `croniter.is_valid('')` is False and `''.split(' ')` has one element, so empty input gives `BAD_CRON`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/admin-api && uv run pytest tests/test_schedules.py -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/pyproject.toml services/admin-api/uv.lock services/admin-api/src/knowledge_admin/schedules.py services/admin-api/tests/test_schedules.py
git commit -m "feat(admin-api): cron rules for schedules (croniter, tzdata)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Shared Jira pull (`jira_sync.py`) and the `jira.pull` target

**Files:**
- Create: `services/admin-api/src/knowledge_admin/jira_sync.py`
- Modify: `services/admin-api/src/knowledge_admin/jira_api.py` (`connection_credentials` and `fetch_tickets` delegate)
- Test: `services/admin-api/tests/test_jira_sync.py`; existing `services/admin-api/tests/test_jira.py` must pass unchanged.

**Interfaces:**
- Consumes: `knowledge_admin.schedules.TargetMissing`.
- Produces (module `knowledge_admin.jira_sync`):
  - `FETCH_CLASH = "Jira settings or tickets changed during the fetch. Fetch tickets again"`, `CLASH_SKIPPED = 'A manual fetch ran at the same time.'`, `DEFAULT_LIMIT = 25`.
  - `credentials(store, project_id) -> tuple[dict, str]` — the primary connection row and its decrypted token; raises the same `HTTPException`s as today.
  - `pull_tickets(store, project_id, jql, limit, actor: tuple[str, str] | None, transport=None, action='jira.tickets_fetched') -> dict` — returns `{'fetched': int, 'total': int}`.
  - `auto_pull(store, schedule: dict, transport=None) -> tuple[str, str]` — `('succeeded', 'Pulled N of M tickets.')` or `('skipped', CLASH_SKIPPED)`; raises `TargetMissing` or `HTTPException`.

- [ ] **Step 1: Write the failing test**

`services/admin-api/tests/test_jira_sync.py`:

```python
"""The Jira pull shared by Fetch tickets and the scheduled auto pull (target jira.pull)."""
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from knowledge_admin.jira_sync import CLASH_SKIPPED, auto_pull
from knowledge_admin.schedules import TargetMissing
from test_jira import SETTINGS, jira_response, setup
from test_repositories import HEADERS


def a_schedule(store) -> dict:
    with store.connect() as conn:
        connection = conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()
    return {'id': uuid4(), 'project_id': connection['project_id'], 'target_kind': 'jira.pull', 'target_id': connection['id']}


def audits(store) -> list[dict]:
    with store.connect() as conn:
        return conn.execute("""SELECT actor_id,details FROM ka.audit_events
            WHERE action='jira.tickets_auto_pulled' ORDER BY id""").fetchall()


def test_auto_pull_uses_the_query_and_limit_of_the_last_fetch(store, tmp_path):
    client, _ = setup(store, tmp_path)
    assert client.post('/api/jira/tickets/fetch', headers=HEADERS, json={'limit': 5}).status_code == 200
    result = auto_pull(store, a_schedule(store), transport=httpx.MockTransport(jira_response))
    assert result == ('succeeded', 'Pulled 5 of 7 tickets.')
    assert audits(store) == [{'actor_id': None, 'details': {'requested': 5, 'fetched': 5, 'total': 7}}]
    tickets = client.get('/api/jira/tickets', headers=HEADERS).json()
    assert len(tickets['tickets']) == 5 and tickets['connection']['last_fetch_limit'] == 5


def test_auto_pull_before_any_fetch_uses_25(store, tmp_path):
    setup(store, tmp_path)
    assert auto_pull(store, a_schedule(store), transport=httpx.MockTransport(jira_response)) == ('succeeded', 'Pulled 7 of 7 tickets.')


def test_auto_pull_without_token_raises_the_button_error(store, tmp_path):
    client, config = setup(store, tmp_path)
    removed = client.put('/api/jira/settings', headers=HEADERS, json={
        'base_url': config['base_url'], 'username': config['username'], 'revision': 1, 'token': None})
    assert removed.status_code == 200
    with pytest.raises(HTTPException) as error:
        auto_pull(store, a_schedule(store), transport=httpx.MockTransport(jira_response))
    assert error.value.detail == 'Save a Jira URL, username and token in Jira Settings first'


def test_a_fetch_at_the_same_time_skips_the_run(store, tmp_path):
    setup(store, tmp_path)

    def clashing(request):
        if request.url.path.endswith('/search'):
            with store.connect() as conn:  # a manual fetch finishing while the auto pull talks to Jira
                conn.execute('UPDATE ka.jira_connections SET fetch_revision=fetch_revision+1')
        return jira_response(request)

    assert auto_pull(store, a_schedule(store), transport=httpx.MockTransport(clashing)) == ('skipped', CLASH_SKIPPED)
    assert audits(store) == []


def test_a_missing_connection_is_a_missing_target(store, tmp_path):
    setup(store, tmp_path)
    schedule = {**a_schedule(store), 'target_id': uuid4()}
    with pytest.raises(TargetMissing):
        auto_pull(store, schedule, transport=httpx.MockTransport(jira_response))


def test_the_token_never_reaches_the_audit(store, tmp_path):
    setup(store, tmp_path)
    auto_pull(store, a_schedule(store), transport=httpx.MockTransport(jira_response))
    assert SETTINGS['token'] not in str(audits(store))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest tests/test_jira_sync.py -q`
Expected: FAIL (`ModuleNotFoundError: knowledge_admin.jira_sync`).

- [ ] **Step 3: Write `jira_sync.py`**

Move the bodies of `connection_credentials` and `fetch_tickets` out of `jira_api.py` (lines ~100-117 and ~159-197 on main) without changing their SQL or messages:

```python
"""The Jira ticket pull shared by Fetch tickets (Jira → Tickets) and the scheduled auto pull (target jira.pull)."""
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from knowledge_admin.jira import JiraClient
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.schedules import TargetMissing

FETCH_CLASH = "Jira settings or tickets changed during the fetch. Fetch tickets again"
CLASH_SKIPPED = 'A manual fetch ran at the same time.'
DEFAULT_LIMIT = 25


def credentials(store: RepositoryStore, project_id: UUID) -> tuple[dict, str]:
    """The project's primary Jira connection and its decrypted token."""
    with store.connect() as conn:
        config = conn.execute("SELECT * FROM ka.jira_connections WHERE project_id=%s AND name='primary' FOR SHARE", (project_id,)).fetchone()
        if config is None or config["credential_id"] is None or config["username"] is None:
            raise HTTPException(400, "Save a Jira URL, username and token in Jira Settings first")
        if not config["enabled"] or config["deployment"] != "data_center":
            raise HTTPException(400, "This Jira Data Center connection is disabled or unsupported")
        secret = conn.execute("SELECT ciphertext,key_version FROM ka.credentials WHERE id=%s AND project_id=%s AND kind='jira_pat'", (config["credential_id"], project_id)).fetchone()
        try:
            if secret is None or secret["key_version"] != "v1":
                raise ValueError("Unsupported credential")
            blob = bytes(secret["ciphertext"])
            token = store.cipher.decrypt(blob[:12], blob[12:], f"{project_id}:{config['credential_id']}:jira_pat:v1".encode()).decode()
        except (InvalidTag, ValueError, UnicodeDecodeError):
            raise HTTPException(503, "Stored Jira token could not be read. Re-enter the token in Jira Settings") from None
    return config, token


def pull_tickets(store: RepositoryStore, project_id: UUID, jql: str, limit: int, actor: tuple[str, str] | None,
                 transport: httpx.BaseTransport | None = None, action: str = 'jira.tickets_fetched') -> dict:
    """Fetch up to `limit` tickets for `jql` and replace the saved selection with them. `actor` is the signed-in
    (subject, email), or None for the scheduler (the audit row then has no actor)."""
    config, token = credentials(store, project_id)
    with JiraClient(config["base_url"], token, transport) as jira:
        jira.current_user(config["username"])
        issues, total = jira.tickets(limit, jql)
    with store.connect() as conn:
        current = conn.execute("SELECT revision,fetch_revision FROM ka.jira_connections WHERE id=%s AND project_id=%s FOR UPDATE", (config["id"], config["project_id"])).fetchone()
        if current is None or current["revision"] != config["revision"] or current["fetch_revision"] != config["fetch_revision"]:
            raise HTTPException(409, FETCH_CLASH)
        actor_id = None if actor is None else store.actor(conn, *actor)
        conn.execute("UPDATE ka.jira_tickets SET out_of_scope_at=now() WHERE connection_id=%s AND out_of_scope_at IS NULL", (config["id"],))
        for issue in issues:
            ...  # the INSERT ... ON CONFLICT statement from jira_api.fetch_tickets, unchanged
        conn.execute("""UPDATE ka.jira_connections SET last_successful_sync_at=now(),last_fetch_limit=%s,
            last_fetch_total=%s,jql=%s,fetch_revision=fetch_revision+1 WHERE id=%s""", (limit, total, jql, config["id"]))
        conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,'jira_connection',%s,%s)""",
            (config["project_id"], actor_id, action, config["id"], Jsonb({"requested": limit, "fetched": len(issues), "total": total})))
    return {'fetched': len(issues), 'total': total}


def auto_pull(store: RepositoryStore, schedule: dict, transport: httpx.BaseTransport | None = None) -> tuple[str, str]:
    """Target jira.pull: the Fetch tickets pull with the query and limit of the last fetch."""
    with store.connect() as conn:
        connection = conn.execute("""SELECT jql,last_fetch_limit FROM ka.jira_connections
            WHERE id=%s AND project_id=%s AND name='primary'""", (schedule['target_id'], schedule['project_id'])).fetchone()
    if connection is None:
        raise TargetMissing()
    try:
        result = pull_tickets(store, schedule['project_id'], connection['jql'], connection['last_fetch_limit'] or DEFAULT_LIMIT,
                              None, transport, action='jira.tickets_auto_pulled')
    except HTTPException as exc:
        if exc.status_code == 409 and exc.detail == FETCH_CLASH:
            return 'skipped', CLASH_SKIPPED
        raise
    return 'succeeded', f"Pulled {result['fetched']} of {result['total']} tickets."
```

The `...` line above stands for the existing per-issue `INSERT INTO ka.jira_tickets(...) ... ON CONFLICT(connection_id,remote_id) DO UPDATE ...` statement and its parameter tuple — copy it verbatim from `jira_api.fetch_tickets` (it is ~20 lines; do not alter it). The audit action was a literal `'jira.tickets_fetched'` in SQL; it is now the `%s` parameter `action`.

- [ ] **Step 4: Make `jira_api.py` delegate**

In `build_api`, replace the body of `connection_credentials` and of `fetch_tickets`:

```python
    def connection_credentials(db: RepositoryStore) -> tuple[dict, str]:
        with db.connect() as conn:
            project = db.project(conn, app)
        return jira_sync.credentials(db, project)
```

```python
    @router.post("/jira/tickets/fetch")
    def fetch_tickets(value: TicketFetchInput, request: Request) -> dict:
        db = database()
        actor = request_actor(request)
        with db.connect() as conn:
            project = db.project(conn, app)
        jira_sync.pull_tickets(db, project, value.jql, value.limit, actor, transport)
        return list_tickets()
```

Add `from knowledge_admin import jira_sync` and remove imports that became unused (`InvalidTag` if no longer used in `jira_api.py`; keep `Jsonb`, `JiraClient` — still used by test/export).

- [ ] **Step 5: Run tests**

Run: `cd services/admin-api && uv run pytest tests/test_jira_sync.py tests/test_jira.py tests/test_jira_export.py -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/jira_sync.py services/admin-api/src/knowledge_admin/jira_api.py services/admin-api/tests/test_jira_sync.py
git commit -m "refactor(admin-api): shared Jira pull and the jira.pull schedule target

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Scheduler loop in repository-worker

**Files:**
- Create: `services/admin-api/src/knowledge_admin/scheduler.py`
- Modify: `services/admin-api/src/knowledge_admin/repository_worker.py` (`main`: start and join the scheduler thread)
- Test: `services/admin-api/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `schedules.next_after`, `schedules.MISSING`, `schedules.TargetMissing`, `jira_sync.auto_pull`.
- Produces (module `knowledge_admin.scheduler`):
  - `TARGETS: dict[str, Callable[[RepositoryStore, dict], tuple[str, str]]]` = `{'jira.pull': jira_sync.auto_pull}`.
  - `UNEXPECTED`, `KEEP_RUNS = 100`, `POLL_SECONDS = 5`.
  - `claim(store) -> dict | None`, `finish(store, schedule, status, message) -> None`, `run_once(store, targets=None) -> bool`, `loop(store, stopped: threading.Event, targets=None) -> None`.

- [ ] **Step 1: Write the failing test**

`services/admin-api/tests/test_scheduler.py`:

```python
"""The scheduler claims due schedules with a lease, runs their target and records the result."""
import threading
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from knowledge_admin import scheduler
from knowledge_admin.schedules import MISSING, TargetMissing


def a_schedule(store, **values) -> dict:
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        row = {'project_id': project, 'name': 'Jira auto pull', 'target_kind': 'jira.pull', 'target_id': uuid4(),
               'cron': '*/15 * * * *', 'timezone': 'UTC', **values}
        schedule = conn.execute(f"""INSERT INTO ka.schedules({','.join(row)}) VALUES ({','.join(['%s'] * len(row))})
            RETURNING id""", tuple(row.values())).fetchone()
        conn.execute("UPDATE ka.schedules SET next_run_at=coalesce(next_run_at, now()-interval '1 minute') WHERE id=%s AND enabled", (schedule['id'],))
    return schedule


def row(store, schedule) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.schedules WHERE id=%s', (schedule['id'],)).fetchone()


def runs(store, schedule) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.schedule_runs WHERE schedule_id=%s ORDER BY id', (schedule['id'],)).fetchall()


def ok(store, schedule):
    return 'succeeded', 'Pulled 3 of 3 tickets.'


def test_a_due_schedule_runs_once_and_moves_to_its_next_time(store):
    schedule = a_schedule(store)
    assert scheduler.run_once(store, {'jira.pull': ok}) is True
    assert scheduler.run_once(store, {'jira.pull': ok}) is False
    saved = row(store, schedule)
    assert saved['last_status'] == 'succeeded' and saved['last_message'] == 'Pulled 3 of 3 tickets.'
    assert saved['lease_expires_at'] is None and saved['run_requested'] is False
    assert saved['next_run_at'] > datetime.now(UTC) and saved['next_run_at'].minute % 15 == 0
    assert [(r['trigger'], r['status']) for r in runs(store, schedule)] == [('schedule', 'succeeded')]


def test_missed_slots_run_once(store):
    schedule = a_schedule(store)
    with store.connect() as conn:
        conn.execute("UPDATE ka.schedules SET next_run_at=now()-interval '3 hours' WHERE id=%s", (schedule['id'],))
    assert scheduler.run_once(store, {'jira.pull': ok}) is True
    assert scheduler.run_once(store, {'jira.pull': ok}) is False
    assert len(runs(store, schedule)) == 1


def test_a_claimed_schedule_is_not_claimed_again(store):
    a_schedule(store)
    assert scheduler.claim(store) is not None
    assert scheduler.claim(store) is None


def test_expired_lease_is_claimable_and_the_stale_finish_records_nothing(store):
    schedule = a_schedule(store)
    stale = scheduler.claim(store)
    with store.connect() as conn:
        conn.execute("UPDATE ka.schedules SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (schedule['id'],))
    fresh = scheduler.claim(store)
    assert fresh is not None
    scheduler.finish(store, stale, 'failed', 'late')
    assert runs(store, schedule) == [] and row(store, schedule)['lease_expires_at'] is not None
    scheduler.finish(store, fresh, 'succeeded', 'done')
    assert [r['message'] for r in runs(store, schedule)] == ['done']


def test_failures_are_recorded_and_the_schedule_moves_on(store):
    def jira_down(store, schedule):
        raise HTTPException(502, 'Could not reach Jira. Check the URL, network access, and TLS certificate')

    def broken(store, schedule):
        raise RuntimeError('secret-looking detail')

    def gone(store, schedule):
        raise TargetMissing()

    for target, message in [(jira_down, 'Could not reach Jira. Check the URL, network access, and TLS certificate'),
                            (broken, scheduler.UNEXPECTED), (gone, MISSING)]:
        schedule = a_schedule(store)
        assert scheduler.run_once(store, {'jira.pull': target}) is True
        saved = row(store, schedule)
        assert (saved['last_status'], saved['last_message']) == ('failed', message)
        assert saved['next_run_at'] > datetime.now(UTC) and saved['lease_expires_at'] is None


def test_an_unknown_target_fails(store):
    schedule = a_schedule(store)
    assert scheduler.run_once(store, {}) is True
    assert row(store, schedule)['last_message'] == MISSING


def test_a_skipped_run_is_recorded_as_skipped(store):
    schedule = a_schedule(store)
    scheduler.run_once(store, {'jira.pull': lambda store, schedule: ('skipped', 'A manual fetch ran at the same time.')})
    assert row(store, schedule)['last_status'] == 'skipped'


def test_pausing_during_a_run_keeps_it_paused(store):
    schedule = a_schedule(store)

    def pause_meanwhile(store, claimed):
        with store.connect() as conn:
            conn.execute("UPDATE ka.schedules SET enabled=false,next_run_at=NULL,cron='0 * * * *' WHERE id=%s", (claimed['id'],))
        return ok(store, claimed)

    scheduler.run_once(store, {'jira.pull': pause_meanwhile})
    saved = row(store, schedule)
    assert saved['next_run_at'] is None and saved['cron'] == '0 * * * *' and saved['last_status'] == 'succeeded'


def test_run_now_on_a_paused_schedule_runs_once_and_stays_paused(store):
    schedule = a_schedule(store, enabled=False)
    with store.connect() as conn:
        conn.execute('UPDATE ka.schedules SET run_requested=true,next_run_at=now() WHERE id=%s', (schedule['id'],))
    assert scheduler.run_once(store, {'jira.pull': ok}) is True
    saved = row(store, schedule)
    assert saved['next_run_at'] is None and saved['run_requested'] is False and saved['enabled'] is False
    assert [r['trigger'] for r in runs(store, schedule)] == ['manual']


def test_history_keeps_the_newest_100(store):
    schedule = a_schedule(store)
    with store.connect() as conn:
        for _ in range(105):
            conn.execute("""INSERT INTO ka.schedule_runs(schedule_id,trigger,started_at,finished_at,status,message)
                VALUES (%s,'schedule',now(),now(),'succeeded','old')""", (schedule['id'],))
    scheduler.run_once(store, {'jira.pull': ok})
    kept = runs(store, schedule)
    assert len(kept) == scheduler.KEEP_RUNS and kept[-1]['message'] == 'Pulled 3 of 3 tickets.'


def test_loop_returns_when_stopped(store):
    stopped = threading.Event()
    stopped.set()
    scheduler.loop(store, stopped, {'jira.pull': ok})  # returns at once
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest tests/test_scheduler.py -q`
Expected: FAIL (`ImportError: cannot import name 'scheduler'`).

- [ ] **Step 3: Write `scheduler.py`**

```python
"""The scheduler: claim a due schedule, run its target, record the result. It runs as a thread in repository-worker;
the lease keeps several workers from running the same schedule, and a worker whose lease ran out records nothing."""
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
from fastapi import HTTPException

from knowledge_admin import jira_sync
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.schedules import MISSING, TargetMissing, next_after

logger = logging.getLogger(__name__)

POLL_SECONDS = 5
KEEP_RUNS = 100
UNEXPECTED = 'The run failed unexpectedly. See the repository-worker log.'

Target = Callable[[RepositoryStore, dict], tuple[str, str]]
TARGETS: dict[str, Target] = {'jira.pull': jira_sync.auto_pull}

CLAIM = """UPDATE ka.schedules SET lease_expires_at=clock_timestamp()+interval '15 minutes', last_run_at=clock_timestamp()
    WHERE id=(SELECT id FROM ka.schedules WHERE next_run_at<=clock_timestamp()
        AND (lease_expires_at IS NULL OR lease_expires_at<clock_timestamp())
        ORDER BY next_run_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
    RETURNING *"""


def claim(store: RepositoryStore) -> dict | None:
    with store.connect() as conn:
        return conn.execute(CLAIM).fetchone()


def run(store: RepositoryStore, schedule: dict, targets: dict[str, Target]) -> tuple[str, str]:
    target = targets.get(schedule['target_kind'])
    if target is None:
        return 'failed', MISSING
    try:
        return target(store, schedule)
    except TargetMissing:
        return 'failed', MISSING
    except HTTPException as exc:
        return 'failed', str(exc.detail)
    except Exception:
        logger.exception('Schedule %s (%s) failed', schedule['id'], schedule['target_kind'])
        return 'failed', UNEXPECTED


def finish(store: RepositoryStore, schedule: dict, status: str, message: str) -> None:
    """Record the run and set the next time from the schedule as it is now (it may have been edited or paused)."""
    with store.connect() as conn:
        current = conn.execute("""SELECT cron,timezone,enabled FROM ka.schedules WHERE id=%s AND lease_expires_at=%s
            FOR UPDATE""", (schedule['id'], schedule['lease_expires_at'])).fetchone()
        if current is None:
            return  # deleted, or the lease ran out and another worker took the schedule over
        next_run = next_after(current['cron'], current['timezone'], datetime.now(UTC)) if current['enabled'] else None
        conn.execute("""UPDATE ka.schedules SET lease_expires_at=NULL,run_requested=false,next_run_at=%s,
            last_finished_at=clock_timestamp(),last_status=%s,last_message=%s WHERE id=%s""",
            (next_run, status, message, schedule['id']))
        conn.execute("""INSERT INTO ka.schedule_runs(schedule_id,trigger,started_at,finished_at,status,message)
            VALUES (%s,%s,%s,clock_timestamp(),%s,%s)""",
            (schedule['id'], 'manual' if schedule['run_requested'] else 'schedule', schedule['last_run_at'], status, message))
        conn.execute("""DELETE FROM ka.schedule_runs WHERE schedule_id=%s AND id NOT IN
            (SELECT id FROM ka.schedule_runs WHERE schedule_id=%s ORDER BY id DESC LIMIT %s)""",
            (schedule['id'], schedule['id'], KEEP_RUNS))


def run_once(store: RepositoryStore, targets: dict[str, Target] | None = None) -> bool:
    schedule = claim(store)
    if schedule is None:
        return False
    status, message = run(store, schedule, TARGETS if targets is None else targets)
    finish(store, schedule, status, message)
    print(f"Schedule {schedule['id']} ({schedule['target_kind']}): {status}", flush=True)
    return True


def loop(store: RepositoryStore, stopped: threading.Event, targets: dict[str, Target] | None = None) -> None:
    while not stopped.is_set():
        try:
            worked = run_once(store, targets)
        except psycopg.Error:
            print('Scheduler database unavailable; retrying', flush=True)
            worked = False
        if not worked:
            stopped.wait(POLL_SECONDS)
```

- [ ] **Step 4: Start the thread in `repository_worker.main`**

Add `from knowledge_admin import scheduler` to the imports and change the end of `main`:

```python
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    # Schedules (e.g. the Jira auto pull) run beside the repository jobs; a running target finishes before exit.
    schedules = threading.Thread(target=scheduler.loop, args=(store, stopped), name='scheduler')
    schedules.start()
    while not stopped.is_set():
        ...  # the existing loop body, unchanged
    schedules.join()
```

- [ ] **Step 5: Run tests**

Run: `cd services/admin-api && uv run pytest tests/test_scheduler.py tests/test_repository_worker.py -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/scheduler.py services/admin-api/src/knowledge_admin/repository_worker.py services/admin-api/tests/test_scheduler.py
git commit -m "feat(admin-api): scheduler loop in repository-worker

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Schedules API and Jira auto-pull endpoints

**Files:**
- Create: `services/admin-api/src/knowledge_admin/schedules_api.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py` (include the router next to `jira_api`)
- Test: `services/admin-api/tests/test_schedules_api.py`

**Interfaces:**
- Consumes: `schedules.validate/upcoming/next_after/describe/ScheduleError`, `jira_sync.DEFAULT_LIMIT`, `scheduler.run_once` (tests only).
- Produces (HTTP, all under `/api`):
  - `GET /schedules` → `{schedules: [Schedule]}`; `Schedule` = `id,name,target_kind,target_id,cron,description,timezone,enabled,next_run_at,running,last_run_at,last_finished_at,last_status,last_message,revision`.
  - `POST /schedules/preview` `{cron,timezone}` → `{runs: [iso, iso, iso]}`.
  - `GET /schedules/{id}/runs` → `{runs: [{id,trigger,started_at,finished_at,status,message}]}` newest first, 20.
  - `PUT /schedules/{id}` `{cron,timezone,enabled,revision}` → `{schedule}`.
  - `POST /schedules/{id}/run` → `{schedule}`.
  - `GET /jira/auto-pull` → `{schedule: Schedule | null, jql: str | null, limit: int | null}`.
  - `PUT /jira/auto-pull` `{enabled,cron,timezone}` → same shape as GET.

- [ ] **Step 1: Write the failing test**

`services/admin-api/tests/test_schedules_api.py`:

```python
"""Administration → Schedules and the Jira auto pull card."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from knowledge_admin import scheduler
from knowledge_admin.schedules import BAD_CRON, BAD_TIMEZONE, TOO_OFTEN
from test_api import _client
from test_jira import setup
from test_repositories import HEADERS

ON = {'enabled': True, 'cron': '*/15 * * * *', 'timezone': 'Europe/Belgrade'}


def error(response) -> str:
    return response.json()['error']['message']


def enable(client) -> dict:
    response = client.put('/api/jira/auto-pull', headers=HEADERS, json=ON)
    assert response.status_code == 200, response.text
    return response.json()['schedule']


def test_auto_pull_needs_jira_settings(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    assert client.get('/api/jira/auto-pull', headers=HEADERS).json() == {'schedule': None, 'jql': None, 'limit': None}
    refused = client.put('/api/jira/auto-pull', headers=HEADERS, json=ON)
    assert refused.status_code == 400 and error(refused) == 'Save Jira settings first.'


def test_turning_auto_pull_on_creates_the_schedule(store, tmp_path):
    client, config = setup(store, tmp_path)
    schedule = enable(client)
    assert schedule['name'] == 'Jira auto pull' and schedule['target_kind'] == 'jira.pull' and schedule['target_id'] == config['id']
    assert schedule['description'] == 'Every 15 minutes' and schedule['timezone'] == 'Europe/Belgrade'
    assert schedule['enabled'] is True and schedule['running'] is False and schedule['next_run_at'] is not None
    state = client.get('/api/jira/auto-pull', headers=HEADERS).json()
    assert state['jql'] == 'ORDER BY updated DESC, key DESC' and state['limit'] == 25 and state['schedule']['id'] == schedule['id']
    assert [s['id'] for s in client.get('/api/schedules', headers=HEADERS).json()['schedules']] == [schedule['id']]
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.saved'").fetchone()['n'] == 1


def test_turning_auto_pull_off_keeps_the_schedule_paused(store, tmp_path):
    client, _ = setup(store, tmp_path)
    first = enable(client)
    off = client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, 'enabled': False}).json()['schedule']
    assert off['id'] == first['id'] and off['enabled'] is False and off['next_run_at'] is None and off['revision'] == 2


def test_bad_timetables_are_refused(store, tmp_path):
    client, _ = setup(store, tmp_path)
    for cron, timezone, message in [('*/2 * * * *', 'UTC', TOO_OFTEN), ('bad', 'UTC', BAD_CRON),
                                    ('0 * * * *', 'Mars/Base', BAD_TIMEZONE)]:
        for response in [client.put('/api/jira/auto-pull', headers=HEADERS, json={'enabled': True, 'cron': cron, 'timezone': timezone}),
                         client.post('/api/schedules/preview', headers=HEADERS, json={'cron': cron, 'timezone': timezone})]:
            assert response.status_code == 400 and error(response) == message
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'] == []


def test_preview_lists_the_next_three_runs(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    runs = client.post('/api/schedules/preview', headers=HEADERS, json={'cron': '0 6 * * *', 'timezone': 'UTC'}).json()['runs']
    times = [datetime.fromisoformat(run) for run in runs]
    assert len(times) == 3 and times == sorted(times) and all(t.hour == 6 and t > datetime.now(UTC) for t in times)


def test_editing_checks_the_revision(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    value = {'cron': '0 * * * *', 'timezone': 'UTC', 'enabled': True, 'revision': schedule['revision']}
    saved = client.put(f"/api/schedules/{schedule['id']}", headers=HEADERS, json=value)
    assert saved.status_code == 200 and saved.json()['schedule']['description'] == 'Every hour'
    stale = client.put(f"/api/schedules/{schedule['id']}", headers=HEADERS, json=value)
    assert stale.status_code == 409 and error(stale) == 'Schedule changed. Reload and try again.'
    missing = client.put(f'/api/schedules/{uuid4()}', headers=HEADERS, json=value)
    assert missing.status_code == 404 and error(missing) == 'Schedule not found.'


def test_run_now_and_history(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    paused = client.put('/api/jira/auto-pull', headers=HEADERS, json={**ON, 'enabled': False}).json()['schedule']
    requested = client.post(f"/api/schedules/{paused['id']}/run", headers=HEADERS)
    assert requested.status_code == 200
    assert datetime.fromisoformat(requested.json()['schedule']['next_run_at']) <= datetime.now(UTC) + timedelta(seconds=1)
    assert scheduler.run_once(store, {'jira.pull': lambda store, s: ('succeeded', 'Pulled 7 of 7 tickets.')}) is True
    after = client.get('/api/schedules', headers=HEADERS).json()['schedules'][0]
    assert after['enabled'] is False and after['next_run_at'] is None and after['last_message'] == 'Pulled 7 of 7 tickets.'
    history = client.get(f"/api/schedules/{schedule['id']}/runs", headers=HEADERS).json()['runs']
    assert [(r['trigger'], r['status']) for r in history] == [('manual', 'succeeded')]
    assert client.get(f'/api/schedules/{uuid4()}/runs', headers=HEADERS).status_code == 404
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.audit_events WHERE action='schedule.run_requested'").fetchone()['n'] == 1


def test_run_now_is_refused_while_running(store, tmp_path):
    client, _ = setup(store, tmp_path)
    schedule = enable(client)
    with store.connect() as conn:
        conn.execute("UPDATE ka.schedules SET lease_expires_at=now()+interval '5 minutes' WHERE id=%s", (schedule['id'],))
    assert client.get('/api/schedules', headers=HEADERS).json()['schedules'][0]['running'] is True
    busy = client.post(f"/api/schedules/{schedule['id']}/run", headers=HEADERS)
    assert busy.status_code == 409 and error(busy) == 'This schedule is already running.'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest tests/test_schedules_api.py -q`
Expected: FAIL (404 on `/api/jira/auto-pull`).

- [ ] **Step 3: Write `schedules_api.py`**

```python
"""Administration → Schedules and the Jira auto pull card: list, edit, pause and run schedules. repository-worker runs
them (scheduler.py); this API only changes their rows."""
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.access import request_actor
from knowledge_admin.jira_sync import DEFAULT_LIMIT
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.schedules import ScheduleError, describe, next_after, upcoming, validate

FIELDS = """id,name,target_kind,target_id,cron,timezone,enabled,next_run_at,
    coalesce(lease_expires_at > now(), false) AS running,last_run_at,last_finished_at,last_status,last_message,revision"""
NOT_FOUND = 'Schedule not found.'
STALE = 'Schedule changed. Reload and try again.'
RUNNING = 'This schedule is already running.'
NO_JIRA = 'Save Jira settings first.'
JIRA_PULL = 'Jira auto pull'
PREVIEW_RUNS = 3
HISTORY = 20


class Timetable(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    cron: str = Field(min_length=1, max_length=100)
    timezone: str = Field(min_length=1, max_length=100)


class AutoPullInput(Timetable):
    enabled: bool


class ScheduleInput(AutoPullInput):
    revision: int = Field(ge=1, strict=True)


def shown(row: dict) -> dict:
    return {**row, 'description': describe(row['cron'])}


def checked(value: Timetable) -> None:
    try:
        validate(value.cron, value.timezone)
    except ScheduleError as exc:
        raise HTTPException(400, str(exc)) from None


def first_run(value: AutoPullInput) -> datetime | None:
    return next_after(value.cron, value.timezone, datetime.now(UTC)) if value.enabled else None


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Schedule storage is not configured')
        return store

    def audit(conn, project: UUID, request: Request, action: str, schedule_id: UUID, details: dict) -> None:
        actor = database().actor(conn, *request_actor(request))
        conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,'schedule',%s,%s)""", (project, actor, action, schedule_id, Jsonb(details)))

    def jira_state(conn, project: UUID) -> tuple[dict | None, dict]:
        connection = conn.execute("""SELECT id,jql,last_fetch_limit FROM ka.jira_connections
            WHERE project_id=%s AND name='primary'""", (project,)).fetchone()
        if connection is None:
            return None, {'schedule': None, 'jql': None, 'limit': None}
        row = conn.execute(f"""SELECT {FIELDS} FROM ka.schedules WHERE project_id=%s AND target_kind='jira.pull'
            AND target_id=%s""", (project, connection['id'])).fetchone()
        return connection, {'schedule': shown(row) if row else None, 'jql': connection['jql'],
                            'limit': connection['last_fetch_limit'] or DEFAULT_LIMIT}

    @router.get('/schedules')
    def list_schedules() -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            rows = conn.execute(f'SELECT {FIELDS} FROM ka.schedules WHERE project_id=%s ORDER BY name,created_at', (project,)).fetchall()
        return {'schedules': [shown(row) for row in rows]}

    @router.post('/schedules/preview')
    def preview(value: Timetable) -> dict:
        checked(value)
        return {'runs': upcoming(value.cron, value.timezone, datetime.now(UTC), PREVIEW_RUNS)}

    @router.get('/schedules/{schedule_id}/runs')
    def runs(schedule_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            if conn.execute('SELECT 1 FROM ka.schedules WHERE id=%s AND project_id=%s', (schedule_id, project)).fetchone() is None:
                raise HTTPException(404, NOT_FOUND)
            rows = conn.execute("""SELECT id,trigger,started_at,finished_at,status,message FROM ka.schedule_runs
                WHERE schedule_id=%s ORDER BY id DESC LIMIT %s""", (schedule_id, HISTORY)).fetchall()
        return {'runs': rows}

    @router.put('/schedules/{schedule_id}')
    def save(schedule_id: UUID, value: ScheduleInput, request: Request) -> dict:
        checked(value)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            current = conn.execute("""SELECT revision,run_requested,next_run_at FROM ka.schedules
                WHERE id=%s AND project_id=%s FOR UPDATE""", (schedule_id, project)).fetchone()
            if current is None:
                raise HTTPException(404, NOT_FOUND)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE)
            next_run = current['next_run_at'] if current['run_requested'] else first_run(value)
            row = conn.execute(f"""UPDATE ka.schedules SET cron=%s,timezone=%s,enabled=%s,next_run_at=%s,
                revision=revision+1,updated_at=now() WHERE id=%s RETURNING {FIELDS}""",
                (value.cron, value.timezone, value.enabled, next_run, schedule_id)).fetchone()
            audit(conn, project, request, 'schedule.saved', schedule_id,
                  {'cron': value.cron, 'timezone': value.timezone, 'enabled': value.enabled})
        return {'schedule': shown(row)}

    @router.post('/schedules/{schedule_id}/run')
    def run_now(schedule_id: UUID, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            current = conn.execute("""SELECT coalesce(lease_expires_at > now(), false) AS running FROM ka.schedules
                WHERE id=%s AND project_id=%s FOR UPDATE""", (schedule_id, project)).fetchone()
            if current is None:
                raise HTTPException(404, NOT_FOUND)
            if current['running']:
                raise HTTPException(409, RUNNING)
            row = conn.execute(f"""UPDATE ka.schedules SET run_requested=true,next_run_at=now(),updated_at=now()
                WHERE id=%s RETURNING {FIELDS}""", (schedule_id,)).fetchone()
            audit(conn, project, request, 'schedule.run_requested', schedule_id, {})
        return {'schedule': shown(row)}

    @router.get('/jira/auto-pull')
    def auto_pull() -> dict:
        db = database()
        with db.connect() as conn:
            return jira_state(conn, db.project(conn, app))[1]

    @router.put('/jira/auto-pull')
    def save_auto_pull(value: AutoPullInput, request: Request) -> dict:
        checked(value)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            connection, _ = jira_state(conn, project)
            if connection is None:
                raise HTTPException(400, NO_JIRA)
            schedule = conn.execute("""INSERT INTO ka.schedules(project_id,name,target_kind,target_id,cron,timezone,enabled,next_run_at)
                VALUES (%s,%s,'jira.pull',%s,%s,%s,%s,%s)
                ON CONFLICT (project_id,target_kind,target_id) DO UPDATE SET cron=EXCLUDED.cron,timezone=EXCLUDED.timezone,
                    enabled=EXCLUDED.enabled,
                    next_run_at=CASE WHEN schedules.run_requested THEN schedules.next_run_at ELSE EXCLUDED.next_run_at END,
                    revision=schedules.revision+1,updated_at=now()
                RETURNING id""", (project, JIRA_PULL, connection['id'], value.cron, value.timezone, value.enabled,
                                  first_run(value))).fetchone()
            audit(conn, project, request, 'schedule.saved', schedule['id'],
                  {'cron': value.cron, 'timezone': value.timezone, 'enabled': value.enabled})
            return jira_state(conn, project)[1]

    return router
```

- [ ] **Step 4: Wire the router**

In `services/admin-api/src/knowledge_admin/api.py`, add `schedules_api` to the import on line 8 and, right after `router.include_router(jira_api.build_api(...))`, add:

```python
    router.include_router(schedules_api.build_api(app_name, repository_store))
```

- [ ] **Step 5: Run tests**

Run: `cd services/admin-api && uv run pytest tests/test_schedules_api.py tests/test_api.py -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/schedules_api.py services/admin-api/src/knowledge_admin/api.py services/admin-api/tests/test_schedules_api.py
git commit -m "feat(admin-api): schedules API and Jira auto pull endpoints

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Administration → Schedules page

**Files:**
- Create: `services/admin-ui/app/lib/schedules.ts`, `services/admin-ui/app/components/SchedulesView.tsx`, `services/admin-ui/app/components/ScheduleEditor.tsx`, `services/admin-ui/app/routes/schedules.tsx`
- Modify: `services/admin-ui/app/routes.ts`, `services/admin-ui/app/components/AdminLayout.tsx`, `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`
- Test: `services/admin-ui/app/components/__tests__/schedules.test.tsx`

**Interfaces:**
- Consumes: the Task 5 HTTP API.
- Produces (`app/lib/schedules.ts`): types `Schedule`, `ScheduleRun`, `ScheduleInput`, `AutoPull`; `PRESETS: {cron, label}[]`, `DEFAULT_CRON = "*/15 * * * *"`, `TARGETS: Record<string, string>`; functions `getSchedules`, `getScheduleRuns`, `saveSchedule`, `runSchedule`, `previewSchedule`, `getAutoPull`, `saveAutoPull`, `browserTimeZone`, `timeZones`. Task 7 uses `getAutoPull`, `saveAutoPull`, `PRESETS`, `DEFAULT_CRON`, `browserTimeZone`, `AutoPull`.

- [ ] **Step 1: Write `app/lib/schedules.ts`**

```ts
import { request } from "./api";

export type RunStatus = "succeeded" | "failed" | "skipped";
export interface Schedule {
  id: string; name: string; target_kind: string; target_id: string; cron: string; description: string; timezone: string;
  enabled: boolean; next_run_at: string | null; running: boolean; last_run_at: string | null; last_finished_at: string | null;
  last_status: RunStatus | null; last_message: string | null; revision: number;
}
export interface ScheduleRun { id: number; trigger: "schedule" | "manual"; started_at: string; finished_at: string; status: RunStatus; message: string }
export interface ScheduleInput { cron: string; timezone: string; enabled: boolean }
export interface AutoPull { schedule: Schedule | null; jql: string | null; limit: number | null }

export const PRESETS = [
  { cron: "*/15 * * * *", label: "Every 15 minutes" },
  { cron: "*/30 * * * *", label: "Every 30 minutes" },
  { cron: "0 * * * *", label: "Every hour" },
  { cron: "0 */6 * * *", label: "Every 6 hours" },
  { cron: "0 6 * * *", label: "Every day at 06:00" },
];
export const DEFAULT_CRON = PRESETS[0].cron;
export const TARGETS: Record<string, string> = { "jira.pull": "Jira tickets" };
export const STATUS_LABELS: Record<RunStatus, string> = { succeeded: "Succeeded", failed: "Failed", skipped: "Skipped" };

const send = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const path = (id: string) => `/api/schedules/${encodeURIComponent(id)}`;

export const getSchedules = (signal?: AbortSignal) => request<{ schedules: Schedule[] }>("/api/schedules", { signal });
export const getScheduleRuns = (id: string, signal?: AbortSignal) => request<{ runs: ScheduleRun[] }>(`${path(id)}/runs`, { signal });
export const saveSchedule = (id: string, value: ScheduleInput & { revision: number }) => request<{ schedule: Schedule }>(path(id), send("PUT", value));
export const runSchedule = (id: string) => request<{ schedule: Schedule }>(`${path(id)}/run`, { method: "POST" });
export const previewSchedule = (cron: string, timezone: string) => request<{ runs: string[] }>("/api/schedules/preview", send("POST", { cron, timezone }));
export const getAutoPull = (signal?: AbortSignal) => request<AutoPull>("/api/jira/auto-pull", { signal });
export const saveAutoPull = (value: ScheduleInput) => request<AutoPull>("/api/jira/auto-pull", send("PUT", value));

export function browserTimeZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
}

/** IANA time zones for the picker: UTC first, and the current value kept even if this browser does not list it. */
export function timeZones(current: string): string[] {
  let zones: string[] = [];
  try { zones = Intl.supportedValuesOf("timeZone"); } catch { zones = []; }
  return [...new Set(["UTC", current, ...zones])];
}
```

- [ ] **Step 2: Write the failing test**

`services/admin-ui/app/components/__tests__/schedules.test.tsx`:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import SchedulesView from "../SchedulesView";
import type { Schedule } from "../../lib/schedules";

const schedule: Schedule = { id: "s-1", name: "Jira auto pull", target_kind: "jira.pull", target_id: "jira-1", cron: "*/15 * * * *",
  description: "Every 15 minutes", timezone: "Europe/Belgrade", enabled: true, next_run_at: "2026-10-09T10:15:00Z", running: false,
  last_run_at: "2026-10-09T10:00:00Z", last_finished_at: "2026-10-09T10:00:02Z", last_status: "succeeded", last_message: "Pulled 25 of 140 tickets.", revision: 3 };

function api(routes: Record<string, (init?: RequestInit) => [number, unknown]>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const handler = routes[key];
    if (!handler) throw new Error(`unexpected ${key}`);
    const [status, body] = handler(init);
    return new Response(JSON.stringify(body), { status });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const body = (fetch: ReturnType<typeof api>, key: string) => JSON.parse(fetch.mock.calls.find(([url, init]) => `${init?.method ?? "GET"} ${url}` === key)![1]!.body as string);
const view = () => render(<MemoryRouter><SchedulesView /></MemoryRouter>);

afterEach(() => vi.unstubAllGlobals());

test("lists schedules with their timetable and last result", async () => {
  api({ "GET /api/schedules": () => [200, { schedules: [schedule] }] });
  view();
  const row = (await screen.findByText("Jira auto pull")).closest("tr")!;
  expect(within(row).getByText("Jira tickets")).toBeInTheDocument();
  expect(within(row).getByText("Every 15 minutes")).toBeInTheDocument();
  expect(within(row).getByText("Europe/Belgrade")).toBeInTheDocument();
  expect(within(row).getByText("Succeeded")).toBeInTheDocument();
  expect(within(row).getByText("Pulled 25 of 140 tickets.")).toBeInTheDocument();
});

test("shows the empty state", async () => {
  api({ "GET /api/schedules": () => [200, { schedules: [] }] });
  view();
  expect(await screen.findByText("No schedules yet. Turn on Auto pull on Jira → Tickets.")).toBeInTheDocument();
});

test("pausing sends the revision and shows the paused row", async () => {
  const user = userEvent.setup();
  const fetch = api({ "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "PUT /api/schedules/s-1": () => [200, { schedule: { ...schedule, enabled: false, next_run_at: null, revision: 4 } }] });
  view();
  await user.click(await screen.findByRole("switch", { name: "Enabled: Jira auto pull" }));
  expect(await screen.findByText("Paused")).toBeInTheDocument();
  expect(body(fetch, "PUT /api/schedules/s-1")).toEqual({ cron: "*/15 * * * *", timezone: "Europe/Belgrade", enabled: false, revision: 3 });
});

test("run now while running shows the server message", async () => {
  const user = userEvent.setup();
  api({ "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "POST /api/schedules/s-1/run": () => [409, { error: { message: "This schedule is already running." } }] });
  view();
  await user.click(await screen.findByRole("button", { name: "Run now" }));
  expect(await screen.findByText("This schedule is already running.")).toBeInTheDocument();
});

test("history lists the newest runs", async () => {
  const user = userEvent.setup();
  api({ "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "GET /api/schedules/s-1/runs": () => [200, { runs: [{ id: 2, trigger: "manual", started_at: "2026-10-09T10:00:00Z", finished_at: "2026-10-09T10:00:02Z", status: "failed", message: "Could not reach Jira." }] }] });
  view();
  await user.click(await screen.findByRole("button", { name: "History" }));
  expect(await screen.findByText(/Run now · Failed · Could not reach Jira\./)).toBeInTheDocument();
});

test("editor previews a custom cron, shows errors under the field and saves", async () => {
  const user = userEvent.setup();
  const fetch = api({ "GET /api/schedules": () => [200, { schedules: [schedule] }],
    "POST /api/schedules/preview": (init) => JSON.parse(init!.body as string).cron === "*/2 * * * *"
      ? [400, { error: { message: "Schedules may run at most every 5 minutes." } }]
      : [200, { runs: ["2026-10-12T05:00:00Z", "2026-10-13T05:00:00Z", "2026-10-14T05:00:00Z"] }],
    "PUT /api/schedules/s-1": () => [200, { schedule: { ...schedule, cron: "0 7 * * 1-5", description: "0 7 * * 1-5", revision: 4 } }] });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("combobox", { name: "How often" }));
  await user.click(screen.getByRole("option", { name: "Custom" }));
  const cron = screen.getByLabelText("Cron expression");
  await user.clear(cron); await user.type(cron, "*/2 * * * *");
  expect(await screen.findByText("Schedules may run at most every 5 minutes.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  await user.clear(cron); await user.type(cron, "0 7 * * 1-5");
  expect(await screen.findByText("Next runs")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(body(fetch, "PUT /api/schedules/s-1")).toEqual({ cron: "0 7 * * 1-5", timezone: "Europe/Belgrade", enabled: true, revision: 3 }));
  expect(await screen.findByText("0 7 * * 1-5")).toBeInTheDocument();
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/schedules.test.tsx`
Expected: FAIL (cannot resolve `../SchedulesView`).

- [ ] **Step 4: Write `ScheduleEditor.tsx`**

```tsx
import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../lib/api";
import { PRESETS, previewSchedule, saveSchedule, timeZones, type Schedule } from "../lib/schedules";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";

const PREVIEW_DELAY_MS = 300;

export default function ScheduleEditor({ schedule, onClose, onSaved }: { schedule: Schedule | null; onClose: () => void; onSaved: (schedule: Schedule) => void }) {
  const [cron, setCron] = useState("");
  const [custom, setCustom] = useState(false);
  const [timezone, setTimezone] = useState("UTC");
  const [preview, setPreview] = useState<string[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!schedule) return;
    setCron(schedule.cron); setTimezone(schedule.timezone); setCustom(!PRESETS.some(preset => preset.cron === schedule.cron));
    setPreview(null); setProblem(null); setError(null);
  }, [schedule]);

  useEffect(() => {
    if (!schedule || !cron.trim()) return;
    let live = true;
    const timer = setTimeout(() => previewSchedule(cron.trim(), timezone)
      .then(result => { if (live) { setPreview(result.runs); setProblem(null); } })
      .catch(reason => { if (live) { setPreview(null); setProblem(reason instanceof ApiError ? reason.message : "Could not check the schedule."); } }), PREVIEW_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [schedule, cron, timezone]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!schedule) return;
    setSaving(true); setError(null);
    try { onSaved((await saveSchedule(schedule.id, { cron: cron.trim(), timezone, enabled: schedule.enabled, revision: schedule.revision })).schedule); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not save the schedule."); }
    finally { setSaving(false); }
  }

  return <Sheet open={schedule !== null} onOpenChange={open => { if (!open) onClose(); }}>
    <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
      <SheetHeader><SheetTitle>Edit schedule</SheetTitle><SheetDescription>{schedule?.name}</SheetDescription></SheetHeader>
      <form onSubmit={save} className="flex flex-col gap-4">
        <FieldGroup>
          <Field><FieldLabel htmlFor="schedule-preset">How often</FieldLabel>
            <Select value={custom ? "custom" : cron} onValueChange={value => { if (value === "custom") setCustom(true); else { setCustom(false); setCron(value); } }}>
              <SelectTrigger id="schedule-preset" aria-label="How often" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup>{PRESETS.map(preset => <SelectItem key={preset.cron} value={preset.cron}>{preset.label}</SelectItem>)}<SelectItem value="custom">Custom</SelectItem></SelectGroup></SelectContent>
            </Select></Field>
          {custom && <Field><FieldLabel htmlFor="schedule-cron">Cron expression</FieldLabel>
            <Input id="schedule-cron" value={cron} maxLength={100} onChange={event => setCron(event.target.value)} />
            <FieldDescription>Five fields: minute, hour, day of month, month, day of week. For example <code>30 8 * * 1-5</code> runs at 08:30 on weekdays.</FieldDescription></Field>}
          <Field><FieldLabel htmlFor="schedule-timezone">Time zone</FieldLabel>
            <Select value={timezone} onValueChange={setTimezone}>
              <SelectTrigger id="schedule-timezone" aria-label="Time zone" className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup>{timeZones(timezone).map(zone => <SelectItem key={zone} value={zone}>{zone}</SelectItem>)}</SelectGroup></SelectContent>
            </Select></Field>
          {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p>
            : preview && <div className="flex flex-col gap-1"><p className="text-sm font-medium">Next runs</p>
              <ul className="text-sm text-muted-foreground">{preview.map(run => <li key={run}>{new Date(run).toLocaleString()}</li>)}</ul></div>}
        </FieldGroup>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        <SheetFooter><Button type="submit" disabled={saving || problem !== null || !cron.trim()}>{saving && <Spinner data-icon="inline-start" />}Save</Button></SheetFooter>
      </form>
    </SheetContent>
  </Sheet>;
}
```

- [ ] **Step 5: Write `SchedulesView.tsx`**

```tsx
import { Fragment, useCallback, useEffect, useState } from "react";
import { CalendarClock, History, Pencil, Play } from "lucide-react";
import { ApiError } from "../lib/api";
import { STATUS_LABELS, TARGETS, getScheduleRuns, getSchedules, runSchedule, saveSchedule, type Schedule, type ScheduleRun } from "../lib/schedules";
import ScheduleEditor from "./ScheduleEditor";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Skeleton } from "~/components/ui/skeleton";
import { Switch } from "~/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const REFRESH_MS = 10_000;
const when = (value: string | null) => value ? new Date(value).toLocaleString() : "—";
const text = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

export function ScheduleStatus({ schedule }: { schedule: Schedule }) {
  if (schedule.running) return <Badge variant="secondary">Running</Badge>;
  if (!schedule.last_status) return <span className="text-sm text-muted-foreground">Never run</span>;
  const variant = schedule.last_status === "failed" ? "destructive" : schedule.last_status === "skipped" ? "outline" : "secondary";
  return <Badge variant={variant}>{STATUS_LABELS[schedule.last_status]}</Badge>;
}

export default function SchedulesView() {
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<Schedule | null>(null);
  const [history, setHistory] = useState<{ id: string; runs: ScheduleRun[] | null } | null>(null);

  const load = useCallback((signal?: AbortSignal) => getSchedules(signal)
    .then(result => { setSchedules(result.schedules); })
    .catch(reason => { if (!signal?.aborted) setError(text(reason, "Could not load schedules.")); }), []);
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    const timer = setInterval(() => load(), REFRESH_MS);
    return () => { controller.abort(); clearInterval(timer); };
  }, [load]);

  const replace = (changed: Schedule) => setSchedules(current => current?.map(item => item.id === changed.id ? changed : item) ?? null);
  async function change(schedule: Schedule, action: () => Promise<{ schedule: Schedule }>) {
    setBusy(schedule.id); setError(null);
    try { replace((await action()).schedule); } catch (reason) { setError(text(reason, "Could not change the schedule.")); } finally { setBusy(null); }
  }
  async function toggleHistory(schedule: Schedule) {
    if (history?.id === schedule.id) { setHistory(null); return; }
    setHistory({ id: schedule.id, runs: null });
    try {
      const { runs } = await getScheduleRuns(schedule.id);
      setHistory(current => current?.id === schedule.id ? { id: schedule.id, runs } : current);
    } catch (reason) { setHistory(null); setError(text(reason, "Could not load the run history.")); }
  }

  return <div className="flex flex-col gap-4">
    {error && <Alert variant="destructive"><AlertTitle>Schedules</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {schedules === null && !error && <Skeleton className="h-32 w-full" />}
    {schedules?.length === 0 && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><CalendarClock /></EmptyMedia><EmptyTitle>Schedules</EmptyTitle>
      <EmptyDescription>No schedules yet. Turn on Auto pull on Jira → Tickets.</EmptyDescription></EmptyHeader></Empty>}
    {schedules && schedules.length > 0 && <Table>
      <TableHeader><TableRow><TableHead>On</TableHead><TableHead>Name</TableHead><TableHead>Runs</TableHead><TableHead>When</TableHead>
        <TableHead>Next run</TableHead><TableHead>Last run</TableHead><TableHead><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader>
      <TableBody>{schedules.map(schedule => <Fragment key={schedule.id}>
        <TableRow>
          <TableCell><Switch aria-label={`Enabled: ${schedule.name}`} checked={schedule.enabled} disabled={busy === schedule.id}
            onCheckedChange={enabled => change(schedule, () => saveSchedule(schedule.id, { cron: schedule.cron, timezone: schedule.timezone, enabled, revision: schedule.revision }))} /></TableCell>
          <TableCell className="font-medium">{schedule.name}</TableCell>
          <TableCell>{TARGETS[schedule.target_kind] ?? schedule.target_kind}</TableCell>
          <TableCell><div>{schedule.description}</div><div className="text-xs text-muted-foreground">{schedule.timezone}</div></TableCell>
          <TableCell>{schedule.next_run_at ? when(schedule.next_run_at) : "Paused"}</TableCell>
          <TableCell><div className="flex flex-col items-start gap-1"><ScheduleStatus schedule={schedule} />
            {schedule.last_message && <span className="text-xs text-muted-foreground">{schedule.last_message}</span>}</div></TableCell>
          <TableCell><div className="flex justify-end gap-1">
            <Button size="sm" variant="outline" disabled={busy === schedule.id || schedule.running} onClick={() => change(schedule, () => runSchedule(schedule.id))}><Play data-icon="inline-start" />Run now</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(schedule)}><Pencil data-icon="inline-start" />Edit</Button>
            <Button size="sm" variant="ghost" aria-expanded={history?.id === schedule.id} onClick={() => toggleHistory(schedule)}><History data-icon="inline-start" />History</Button>
          </div></TableCell>
        </TableRow>
        {history?.id === schedule.id && <TableRow><TableCell colSpan={7}>
          {history.runs === null ? <Skeleton className="h-8 w-full" /> : history.runs.length === 0 ? <p className="text-sm text-muted-foreground">No runs yet.</p>
            : <ul className="flex flex-col gap-1 text-sm">{history.runs.map(run => <li key={run.id}>
              {when(run.started_at)} · {run.trigger === "manual" ? "Run now" : "Scheduled"} · {STATUS_LABELS[run.status]}{run.message ? ` · ${run.message}` : ""}</li>)}</ul>}
        </TableCell></TableRow>}
      </Fragment>)}</TableBody>
    </Table>}
    <ScheduleEditor schedule={editing} onClose={() => setEditing(null)} onSaved={saved => { replace(saved); setEditing(null); }} />
  </div>;
}
```

- [ ] **Step 6: Route, sidebar and title**

`app/routes/schedules.tsx`:

```tsx
import AdminLayout from "../components/AdminLayout";
import SchedulesView from "../components/SchedulesView";

export default function SchedulesPage() {
  return <AdminLayout><SchedulesView /></AdminLayout>;
}
```

`app/routes.ts`: add `route("schedules", "./routes/schedules.tsx"),` after the `activity/runs/:runKey` route.

`app/components/AdminLayout.tsx`:
- import `CalendarClock` from `lucide-react` with the other icons;
- add `const schedulesPage = pathname === "/schedules";` next to `runsPage`;
- title chain: start with `schedulesPage ? "Schedules" : runsPage ? ...`;
- description chain: start with `schedulesPage ? "Run work on a timetable, such as pulling Jira tickets." : runsPage ? ...`;
- after the Agent runs menu item add:

```tsx
          <SidebarMenuItem><SidebarMenuButton asChild isActive={schedulesPage}><Link to="/schedules"><CalendarClock /><span>Schedules</span></Link></SidebarMenuButton></SidebarMenuItem>
```

`app/components/__tests__/adminLayout.test.tsx`: next to the `Agent runs` link assertion add

```tsx
  expect(screen.getByRole("link", { name: "Schedules" })).toHaveAttribute("href", "/schedules");
```

- [ ] **Step 7: Run tests and types**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/schedules.test.tsx app/components/__tests__/adminLayout.test.tsx` → pass.
Run: `npx react-router typegen && npx tsc --noEmit` → exit 0.

- [ ] **Step 8: Commit**

```bash
git add services/admin-ui/app/lib/schedules.ts services/admin-ui/app/components/SchedulesView.tsx services/admin-ui/app/components/ScheduleEditor.tsx services/admin-ui/app/routes/schedules.tsx services/admin-ui/app/routes.ts services/admin-ui/app/components/AdminLayout.tsx services/admin-ui/app/components/__tests__/adminLayout.test.tsx services/admin-ui/app/components/__tests__/schedules.test.tsx
git commit -m "feat(admin-ui): Administration → Schedules

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Auto pull card on Jira → Tickets

**Files:**
- Create: `services/admin-ui/app/components/JiraAutoPullCard.tsx`
- Modify: `services/admin-ui/app/components/JiraTicketsView.tsx` (render the card above the fetch form)
- Test: `services/admin-ui/app/components/__tests__/jira.test.tsx` (new tests; existing ones must still pass)

**Interfaces:**
- Consumes: `getAutoPull`, `saveAutoPull`, `PRESETS`, `DEFAULT_CRON`, `browserTimeZone`, `AutoPull` from `app/lib/schedules.ts`; `ScheduleStatus` from `SchedulesView.tsx`.
- Produces: `<JiraAutoPullCard refreshKey={string | null} />`.

- [ ] **Step 1: Write the failing tests** (append to `jira.test.tsx`; add `import userEvent from "@testing-library/user-event";` and `import type { AutoPull, Schedule } from "../../lib/schedules";` at the top)

```tsx
const pullSchedule: Schedule = { id: "s-1", name: "Jira auto pull", target_kind: "jira.pull", target_id: "jira-1", cron: "*/15 * * * *",
  description: "Every 15 minutes", timezone: "UTC", enabled: true, next_run_at: "2026-10-09T10:15:00Z", running: false,
  last_run_at: "2026-10-09T10:00:00Z", last_finished_at: "2026-10-09T10:00:02Z", last_status: "succeeded", last_message: "Pulled 10 of 12 tickets.", revision: 1 };

function ticketsApi(autoPull: AutoPull | Record<string, unknown>, saved?: AutoPull) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/jira/auto-pull") return new Response(JSON.stringify(init?.method === "PUT" ? saved : autoPull));
    return new Response(JSON.stringify(snapshot));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

test("auto pull card switches the pull on with the browser time zone", async () => {
  const user = userEvent.setup();
  const fetch = ticketsApi({ schedule: null, jql: "project = DEMO", limit: 10 },
    { schedule: pullSchedule, jql: "project = DEMO", limit: 10 });
  render(<MemoryRouter><JiraTicketsView /></MemoryRouter>);
  expect(await screen.findByText("project = DEMO")).toBeInTheDocument();
  expect(screen.getByText(/up to 10 tickets/)).toBeInTheDocument();
  await user.click(screen.getByRole("switch", { name: "Pull tickets automatically" }));
  expect(await screen.findByText(/Pulled 10 of 12 tickets\./)).toBeInTheDocument();
  const put = fetch.mock.calls.find(([url, init]) => url === "/api/jira/auto-pull" && init?.method === "PUT")!;
  expect(JSON.parse(put[1]!.body as string)).toEqual({ enabled: true, cron: "*/15 * * * *", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" });
});

test("a custom timetable links to Schedules instead of the presets", async () => {
  ticketsApi({ schedule: { ...pullSchedule, cron: "0 7 * * 1-5", description: "0 7 * * 1-5" }, jql: "project = DEMO", limit: 10 });
  render(<MemoryRouter><JiraTicketsView /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: "Edit in Schedules" })).toHaveAttribute("href", "/schedules");
  expect(screen.queryByRole("combobox", { name: "How often" })).toBeNull();
});

test("auto pull card stays hidden on an unexpected response", async () => {
  ticketsApi({ connection, tickets: [] });
  render(<MemoryRouter><JiraTicketsView /></MemoryRouter>);
  await screen.findByText("Check payment latency");
  expect(screen.queryByText("Auto pull")).toBeNull();
  expect(screen.queryByText(/undefined/)).toBeNull();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/jira.test.tsx`
Expected: the three new tests FAIL (no switch / link).

- [ ] **Step 3: Write `JiraAutoPullCard.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../lib/api";
import { DEFAULT_CRON, PRESETS, browserTimeZone, getAutoPull, saveAutoPull, type AutoPull } from "../lib/schedules";
import { ScheduleStatus } from "./SchedulesView";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "~/components/ui/card";
import { Field, FieldLabel } from "~/components/ui/field";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Switch } from "~/components/ui/switch";

const when = (value: string) => new Date(value).toLocaleString();

/** Jira → Tickets: switch the scheduled pull on or off and pick how often (a custom cron is edited on Schedules). */
export default function JiraAutoPullCard({ refreshKey }: { refreshKey: string | null }) {
  const [state, setState] = useState<AutoPull | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getAutoPull(controller.signal).then(setState)
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof ApiError ? reason.message : "Could not load the auto pull settings."); });
    return () => controller.abort();
  }, [refreshKey]);

  if (state === null || typeof state.limit !== "number") return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : null;
  const schedule = state.schedule ?? null;
  const cron = schedule?.cron ?? DEFAULT_CRON;
  const custom = !PRESETS.some(preset => preset.cron === cron);

  async function save(enabled: boolean, nextCron: string) {
    setSaving(true); setError(null);
    try { setState(await saveAutoPull({ enabled, cron: nextCron, timezone: schedule?.timezone ?? browserTimeZone() })); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Could not save the auto pull settings."); }
    finally { setSaving(false); }
  }

  return <Card>
    <CardHeader><CardTitle>Auto pull</CardTitle>
      <CardDescription>Uses the query and limit of the last fetch: <code>{state.jql}</code> · up to {state.limit} tickets</CardDescription></CardHeader>
    <CardContent className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-4">
        <Field orientation="horizontal" className="w-auto"><Switch id="jira-auto-pull" checked={schedule?.enabled ?? false} disabled={saving}
          onCheckedChange={enabled => save(enabled, cron)} /><FieldLabel htmlFor="jira-auto-pull">Pull tickets automatically</FieldLabel></Field>
        {custom ? <span className="text-sm">{schedule?.description} · <Link className="underline" to="/schedules">Edit in Schedules</Link></span>
          : <Select value={cron} disabled={saving} onValueChange={value => save(schedule?.enabled ?? false, value)}>
            <SelectTrigger aria-label="How often" className="w-48"><SelectValue /></SelectTrigger>
            <SelectContent><SelectGroup>{PRESETS.map(preset => <SelectItem key={preset.cron} value={preset.cron}>{preset.label}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>}
      </div>
      {schedule && <div role="status" className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <ScheduleStatus schedule={schedule} />
        <span>{schedule.last_finished_at ? `Last pull ${when(schedule.last_finished_at)}${schedule.last_message ? ` · ${schedule.last_message}` : ""}` : "Not pulled automatically yet"}</span>
        {schedule.next_run_at && <span>· Next pull {when(schedule.next_run_at)}</span>}
      </div>}
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    </CardContent>
  </Card>;
}
```

- [ ] **Step 4: Render it in `JiraTicketsView.tsx`**

Import `JiraAutoPullCard from "./JiraAutoPullCard"` and, as the first child of the returned `<div className="flex flex-col gap-6">` (before `<form onSubmit={fetchTickets} ...>`), add:

```tsx
    {data?.connection?.has_token && <JiraAutoPullCard refreshKey={data.connection.last_successful_sync_at} />}
```

- [ ] **Step 5: Run tests and types**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/jira.test.tsx app/components/__tests__/schedules.test.tsx` → all pass (old Jira tests included; if an old test asserts an exact number of `fetch` calls, count only non-`/api/jira/auto-pull` calls in it).
Run: `npx tsc --noEmit` → exit 0.

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui/app/components/JiraAutoPullCard.tsx services/admin-ui/app/components/JiraTicketsView.tsx services/admin-ui/app/components/__tests__/jira.test.tsx
git commit -m "feat(admin-ui): Auto pull card on Jira tickets

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Docs and full verification

**Files:**
- Modify: `docs/migrations.md`, `docs/user-guide.md`, `deploy/README.md`

- [ ] **Step 1: `docs/migrations.md`** — append after the `0085_agent_experts` section:

```markdown
## 0086_schedules

Schedules. `ka.schedules` holds one row per scheduled target (`target_kind` `jira.pull` with `target_id` = the Jira
connection): a five-field cron expression, an IANA time zone, `enabled`, `next_run_at` (NULL while paused unless Run now
is pending, `run_requested`), the lease of a run in progress and the last result. `UNIQUE (project_id, target_kind,
target_id)`. `ka.schedule_runs` keeps each schedule's newest 100 runs (`trigger` `schedule` or `manual`) and is deleted
with its schedule. repository-worker runs due schedules. The down migration drops both tables: auto pull settings and
run history are lost; the Jira tickets already pulled stay.
```

- [ ] **Step 2: `docs/user-guide.md`**
  - In **4.1 Navigation**, add Schedules to the Administration group list (after Agent runs).
  - In **4.8 Jira**, add a paragraph:

```markdown
**Auto pull.** The Auto pull card on Jira → Tickets fetches tickets on a timetable with the query and ticket limit of
your last Fetch tickets (change them by fetching once with the new values). Switch on **Pull tickets automatically** and
pick how often: every 15 or 30 minutes, every hour, every 6 hours or every day at 06:00, in your browser's time zone.
The card shows the last pull and the next one. A failed pull (Jira unreachable, token removed) is shown there and
retried at the next scheduled time; a pull that meets a manual fetch is skipped.
```

  - Add a new subsection after **4.15 Agent runs**:

```markdown
### 4.16 Schedules

Administration → Schedules lists everything that runs on a timetable (today: the Jira auto pull). Each row shows what
it runs, when (in plain words and its time zone), the next run and the last result. Switch a schedule off to pause it;
**Run now** runs it once (a paused schedule stays paused); **Edit** changes the timetable: a preset or a custom cron
expression (minute, hour, day of month, month, day of week), the time zone, with a preview of the next three runs.
Schedules run at most every 5 minutes. **History** shows the newest 20 runs. Schedules are run by the
`repository-worker` service; after downtime a missed schedule runs once, not once per missed time.
```

- [ ] **Step 3: `deploy/README.md`** — after the sentence about `repository-worker` checking for due repositories (line ~148), add:

```markdown
The same service also runs due **schedules** (Administration → Schedules, e.g. the Jira auto pull) on a second thread:
it checks every 5 seconds, leases a schedule for 15 minutes while it runs, and finishes a running pull before it stops.
```

- [ ] **Step 4: Full verification**

Run from the worktree root:
- `bash db/tests/run.sh` → exit 0.
- `cd services/admin-api && uv run pytest -q` → only the two known failures (`test_environments.py::test_migration_reapply_and_populated_rollback_protection`, `test_deploy.py::test_docs_commands_serve_the_docs_folder_inside_the_data_root`).
- `cd services/admin-ui && npx vitest run && npx react-router typegen && npx tsc --noEmit` → all pass (rerun any known-flaky file alone before calling it a failure).

- [ ] **Step 5: Commit**

```bash
git add docs/migrations.md docs/user-guide.md deploy/README.md
git commit -m "docs: schedules and Jira auto pull

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
