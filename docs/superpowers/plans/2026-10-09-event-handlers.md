# Event handlers, event status and chains Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Events get a status and a chain (parent/root/depth); handlers matched by type pattern + field conditions either
emit a new event from Jinja templates or start an agent session; a 2 s loop in repository-worker processes pending
events once; Administration → Events gets Source/Status columns, sorting, more filters, chain and run views, and a
Handlers tab.

**Architecture:** Migration 0089 extends `ka.events` and adds `ka.event_handlers` / `ka.event_handler_runs`. Pure
modules `event_matching.py` (patterns, conditions, Preview reasons) and `event_templates.py` (sandboxed Jinja) are used
by `event_processor.py` (thread beside the scheduler in repository-worker) and by `event_handlers_api.py`. Starting an
agent session is extracted from the Test tab endpoint into `agent_sessions.start_session`, reused with purpose `event`.

**Tech Stack:** FastAPI, psycopg3 (dict rows), golang-migrate SQL, Jinja2 3.1.6 sandbox, `regex` 2026.9.3 (timeouts),
React Router 7, shadcn/ui, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-09-event-handlers-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/event-handlers` (branch `feature/event-handlers`). Never run make
  targets; never touch `.env`, `.local` or the running Docker stack's database (tests use their own disposable DBs).
- Never `git stash`; never `git add .superpowers`. Commit trailer:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd services/admin-api && uv run pytest -q tests/<file>`. UI: `cd services/admin-ui && npx vitest run
  <file>`; UI tasks also run `npx react-router typegen && npx tsc --noEmit`.
- The app maps request validation errors to **400** (not 422); error body `{"error": {"message": ...}}`.
- Migrations: `BEGIN; SET search_path = ka, public; … COMMIT;`, up and down files.
- Event status values `pending | processed | failed`; run status `succeeded | failed`; event source values
  `jira | handler`; session purpose values `documents | test | event`.
- Depth cap 5: message `Chain too deep (5 levels).` Loop interval 2 s, up to 50 events per tick.
- Templates: Jinja `SandboxedEnvironment`, `StrictUndefined`, autoescape off, context `event` =
  `{id, source, type, subject, title, occurred_at (ISO), payload, parent_id, root_id, depth}`; output ≤ 20000 chars;
  subject/title ≤ 500; defaults subject `{{ event.subject }}`, title `{{ event.title }}`,
  payload `{{ event.payload | tojson }}`.
- Regex conditions: pattern ≤ 500 chars, `regex` module with timeout 0.1 s; conditions ≤ 20 per handler.
- Handler copy: 404 `Handler not found.`; 409 `This handler was changed by someone else. Reload and try again.`; 409
  `A handler with this name already exists.`; audit `event_handler.created|updated|deleted`.
- Events list: `sort` in `time|source|type|subject|status` (default `time` = `occurred_at`), `order` `asc|desc`
  (default `desc`), ties by id in the same order; `offset` ≥ 0, `limit` ≤ 200 (default 50); response
  `{events, next_offset}`.

## Review Focus

1. A catastrophic regex (`(a+)+$` against `aaaa…b`) must not hang the loop: the condition is false within ~0.1 s
   (Task 2, `test_a_runaway_regex_times_out_as_no_match`).
2. A template that builds a huge string (`{{ 'x' * 10000000 }}`) must fail the run with a readable message, not eat
   memory (Task 3, `test_huge_output_is_refused_before_it_is_built`).
3. A handler whose agent was deleted after saving must fail its runs with `Agent not found.`, not crash the event or the
   loop (Task 5, `test_a_missing_agent_fails_the_run`).
4. A handler emitting into a chain that matches itself (`*` → emits `loop.again`) stops at depth 5 with `Chain too deep`
   and the processor does not spin (Task 5, `test_a_self_matching_handler_stops_at_depth_five`).
5. Sorting by a column while paging with Load more must neither repeat nor skip rows when values tie (Task 7,
   `test_sorting_ties_page_by_id_without_repeats`).

## File Structure

- `db/migrations/0089_event_handlers.{up,down}.sql` — event status/chain columns, handler tables, `event` purpose.
- `db/tests/run.sh` — 0089 down step and up check.
- `services/admin-api/src/knowledge_admin/event_matching.py` (new) — patterns, conditions, `explain`/`matches`.
- `services/admin-api/src/knowledge_admin/event_templates.py` (new) — sandbox, `check_*`, `render_emit`, `render_prompt`.
- `services/admin-api/src/knowledge_admin/agent_sessions.py` — `SessionRefused`, `start_session`; list/review accept `event`.
- `services/admin-api/src/knowledge_admin/generations_api.py`, `knowledge_agents/session_worker.py`,
  `knowledge_agents/native_sessions.py` — event sessions behave like Test sessions.
- `services/admin-api/src/knowledge_admin/event_processor.py` (new) — claim, process, loop.
- `services/admin-api/src/knowledge_admin/repository_worker.py` — start the processor thread.
- `services/admin-api/src/knowledge_admin/event_handlers_api.py` (new) — handler CRUD, preview, agents.
- `services/admin-api/src/knowledge_admin/events_api.py` — sort/filter/offset, facets, chain and runs.
- `services/admin-api/src/knowledge_admin/api.py` — wire the handlers router.
- `services/admin-api/pyproject.toml`, `uv.lock` — `jinja2`, `regex` as direct dependencies.
- `services/admin-ui/app/lib/events.ts`, `lib/eventHandlers.ts` (new), `lib/generations.ts`, `lib/testSessions.ts`.
- `services/admin-ui/app/components/EventsView.tsx`, `EventsPage.tsx` (new), `HandlersView.tsx` (new),
  `HandlerSheet.tsx` (new), `AdminLayout.tsx`, `AgentTestView.tsx`, `RunsView.tsx`.
- `services/admin-ui/app/routes.ts`, `routes/events.tsx`, `routes/eventHandlers.tsx` (new).
- Docs: `docs/migrations.md`, `docs/user-guide.md`.

---

### Task 1: Migration 0089 — event status and chain, handler tables, `event` sessions

**Files:**
- Create: `db/migrations/0089_event_handlers.up.sql`, `db/migrations/0089_event_handlers.down.sql`
- Create: `services/admin-api/tests/test_event_handlers_migration.py`
- Modify: `services/admin-api/tests/test_events_migration.py` (`test_down_and_up` now steps over 0089)
- Modify: `db/tests/run.sh`

**Interfaces:**
- Produces: columns `ka.events.parent_id bigint, root_id bigint NOT NULL (trigger fills = id), depth int, handler_id
  uuid, status text, processed_at timestamptz`; tables `ka.event_handlers(id uuid, project_id, name, enabled,
  type_pattern, conditions jsonb[], action, emit jsonb, agent_id text, prompt text, created_by uuid, revision,
  created_at, updated_at)` and `ka.event_handler_runs(id bigint, event_id, handler_id, handler_name, status, message,
  emitted_event_id, agent_session_id, created_at)`; purpose `event` on `ka.agent_sessions`.

- [ ] **Step 1: Write the failing test** `services/admin-api/tests/test_event_handlers_migration.py`

```python
"""0089: events get a status and a place in a chain; handlers and their runs; agent sessions may have purpose event."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_events_migration import an_event

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def test_a_new_event_is_pending_and_its_own_root(store):
    with store.connect() as conn:
        event = an_event(conn)
    assert (event['status'], event['processed_at'], event['root_id'], event['depth'], event['parent_id']) == (
        'pending', None, event['id'], 0, None)


def test_existing_events_are_processed_by_the_migration(store):
    with store.connect() as conn:
        run(conn, '0089_event_handlers.down.sql')
        old = an_event(conn)
        run(conn, '0089_event_handlers.up.sql')
        row = conn.execute('SELECT status,processed_at,created_at,root_id FROM ka.events WHERE id=%s', (old['id'],)).fetchone()
    assert (row['status'], row['processed_at'], row['root_id']) == ('processed', row['created_at'], old['id'])


def test_checks(store):
    with store.connect() as conn:
        root = an_event(conn)
        child = an_event(conn, project=root['project_id'], source='handler', handler_id=uuid4(), parent_id=root['id'],
                         root_id=root['id'], depth=1)
        assert (child['root_id'], child['source']) == (root['id'], 'handler')
    project = root['project_id']
    for bad in [{'source': 'handler'},                                   # a handler event names its handler
                {'parent_id': root['id']},                               # a Jira event has no parent
                {'handler_id': uuid4()},
                {'depth': 1},
                {'source': 'handler', 'handler_id': uuid4(), 'depth': 6},
                {'status': 'processed'},                                 # processed needs processed_at
                {'status': 'pending', 'processed_at': '2026-10-09T10:00:00Z'},
                {'status': 'done', 'processed_at': '2026-10-09T10:00:00Z'}]:
        with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
            an_event(conn, project=project, **bad)


def test_handlers_and_runs(store):
    with store.connect() as conn:
        event = an_event(conn)
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        handler = conn.execute("""INSERT INTO ka.event_handlers(project_id,name,type_pattern,action,emit,created_by)
            VALUES (%s,'Triage','jira.ticket.*','emit',%s,%s) RETURNING *""",
            (event['project_id'], Jsonb({'type': 'triage.requested'}), actor)).fetchone()
        assert (handler['enabled'], handler['conditions'], handler['revision']) == (True, [], 1)
        conn.execute("""INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message)
            VALUES (%s,%s,'Triage','succeeded','Emitted triage.requested.')""", (event['id'], handler['id']))
    bad_handlers = [
        {'action': 'emit', 'emit': None},                                      # emit needs its templates
        {'action': 'agent', 'emit': None, 'agent_id': None, 'prompt': 'x'},     # agent needs an agent
        {'action': 'agent', 'emit': None, 'agent_id': 'a', 'prompt': None},     # and a prompt
        {'action': 'mail'},
        {'name': ' '},
        {'conditions': Jsonb({'field': 'x'})},
    ]
    for bad in bad_handlers:
        values = {'project_id': event['project_id'], 'name': f'H{uuid4().hex[:6]}', 'type_pattern': '*', 'action': 'emit',
                  'emit': Jsonb({'type': 'a.b'}), 'created_by': actor, **bad}
        with pytest.raises(psycopg.errors.CheckViolation), store.connect() as conn:
            conn.execute(f"INSERT INTO ka.event_handlers({','.join(values)}) VALUES ({','.join(['%s'] * len(values))})",
                         tuple(values.values()))
    with pytest.raises(psycopg.errors.UniqueViolation), store.connect() as conn:
        conn.execute("""INSERT INTO ka.event_handlers(project_id,name,type_pattern,action,emit,created_by)
            VALUES (%s,'Triage','*','emit',%s,%s)""", (event['project_id'], Jsonb({'type': 'a.b'}), actor))
    with store.connect() as conn:  # deleting the event deletes its runs
        conn.execute('DELETE FROM ka.events WHERE id=%s', (event['id'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.event_handler_runs').fetchone()['n'] == 0


def test_event_is_a_session_purpose(store):
    with store.connect() as conn:
        definition = conn.execute("""SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint
            WHERE conname='agent_sessions_purpose' AND conrelid='ka.agent_sessions'::regclass""").fetchone()['d']
    assert "'event'" in definition


def test_down_and_up(store):
    with store.connect() as conn:
        root = an_event(conn)
        an_event(conn, project=root['project_id'], source='handler', handler_id=uuid4(), parent_id=root['id'],
                 root_id=root['id'], depth=1)
        run(conn, '0089_event_handlers.down.sql')
        assert conn.execute("SELECT to_regclass('ka.event_handlers') IS NULL AS gone").fetchone()['gone']
        assert conn.execute("SELECT count(*) AS n FROM ka.events").fetchone()['n'] == 1  # handler events are deleted
        run(conn, '0089_event_handlers.up.sql')
        assert conn.execute("SELECT to_regclass('ka.events_pending') IS NOT NULL AS found").fetchone()['found']
```

In `services/admin-api/tests/test_events_migration.py` replace `test_down_and_up` with:

```python
def test_down_and_up(store):
    with store.connect() as conn:
        an_event(conn)
        conn.execute((MIGRATIONS / '0089_event_handlers.down.sql').read_text(), prepare=False)  # 0089 builds on 0088
        conn.execute((MIGRATIONS / '0088_events.down.sql').read_text(), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.events') IS NULL AS gone").fetchone()['gone']
        conn.execute((MIGRATIONS / '0088_events.up.sql').read_text(), prepare=False)
        assert conn.execute("SELECT to_regclass('ka.events_jira_issues') IS NOT NULL AS found").fetchone()['found']
        conn.execute((MIGRATIONS / '0089_event_handlers.up.sql').read_text(), prepare=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_handlers_migration.py tests/test_events_migration.py`
Expected: FAIL (0089 files do not exist; `status` key missing).

- [ ] **Step 3: Write the migration**

`db/migrations/0089_event_handlers.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- Events get a status — the event processor in repository-worker runs every matching handler once, then marks the
-- event processed (or failed when a handler failed); nothing is retried — and a place in a chain: an event a handler
-- emitted points to the event it came from (parent), the chain's first event (root) and how deep it is.
ALTER TABLE events
    ADD COLUMN parent_id bigint REFERENCES events(id) ON DELETE SET NULL,
    ADD COLUMN root_id bigint,
    ADD COLUMN depth integer NOT NULL DEFAULT 0 CONSTRAINT events_depth CHECK (depth BETWEEN 0 AND 5),
    ADD COLUMN handler_id uuid,
    ADD COLUMN status text NOT NULL DEFAULT 'pending' CONSTRAINT events_status CHECK (status IN ('pending', 'processed', 'failed')),
    ADD COLUMN processed_at timestamptz;
-- Events recorded before handlers existed are not replayed through new handlers.
UPDATE events SET root_id = id, status = 'processed', processed_at = created_at;
ALTER TABLE events
    ALTER COLUMN root_id SET NOT NULL,
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira', 'handler')),
    ADD CONSTRAINT events_processed_at CHECK ((status = 'pending') = (processed_at IS NULL)),
    ADD CONSTRAINT events_handler_made CHECK (source <> 'handler' OR handler_id IS NOT NULL),
    ADD CONSTRAINT events_source_made CHECK (source = 'handler' OR (parent_id IS NULL AND handler_id IS NULL AND depth = 0));
COMMENT ON COLUMN events.handler_id IS 'The handler that emitted the event (no foreign key: a deleted handler leaves its events).';
COMMENT ON COLUMN events.parent_id IS 'The event a handler emitted this one from; NULL for a source event or when the parent was deleted.';

-- A root event is its own root.
CREATE FUNCTION events_root() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.root_id IS NULL THEN
        NEW.root_id := NEW.id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER events_root BEFORE INSERT ON events FOR EACH ROW EXECUTE FUNCTION events_root();

CREATE INDEX events_pending ON events(id) WHERE status = 'pending';
CREATE INDEX events_chain ON events(root_id, depth, id);
CREATE INDEX events_by_parent ON events(parent_id);

-- A handler: an event type pattern and field conditions (all must hold), then one action — emit a new event from
-- Jinja templates, or start an agent session whose first message is a Jinja prompt (created_by is its actor).
CREATE TABLE event_handlers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    name text NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
    enabled boolean NOT NULL DEFAULT true,
    type_pattern text NOT NULL CHECK (char_length(type_pattern) BETWEEN 1 AND 200),
    conditions jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(conditions) = 'array' AND jsonb_array_length(conditions) <= 20),
    action text NOT NULL CHECK (action IN ('emit', 'agent')),
    emit jsonb CHECK (emit IS NULL OR jsonb_typeof(emit) = 'object'),
    agent_id text,
    prompt text CHECK (char_length(prompt) <= 20000),
    created_by uuid NOT NULL REFERENCES principals(id),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name),
    CONSTRAINT event_handlers_action_fields CHECK (CASE action
        WHEN 'emit' THEN emit IS NOT NULL AND agent_id IS NULL AND prompt IS NULL
        ELSE emit IS NULL AND agent_id IS NOT NULL AND prompt IS NOT NULL END)
);
COMMENT ON COLUMN event_handlers.agent_id IS 'Send to agent: the agent (no foreign key: deleting the agent fails the handler''s runs with Agent not found).';
COMMENT ON COLUMN event_handlers.conditions IS 'Array of {field, op, value}; op in equals, not_equals, contains, in, matches, exists.';

-- What each matching handler did with an event.
CREATE TABLE event_handler_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id bigint NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    handler_id uuid NOT NULL,
    handler_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('succeeded', 'failed')),
    message text NOT NULL DEFAULT '' CHECK (char_length(message) <= 2000),
    emitted_event_id bigint REFERENCES events(id) ON DELETE SET NULL,
    agent_session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX event_handler_runs_by_event ON event_handler_runs(event_id);
CREATE INDEX event_handler_runs_by_handler ON event_handler_runs(handler_id, id DESC);

ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event'));
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent).';

COMMIT;
```

`db/migrations/0089_event_handlers.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DROP TABLE event_handler_runs;
DROP TABLE event_handlers;

DELETE FROM agent_sessions WHERE purpose = 'event';
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test'));
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session.';

DELETE FROM events WHERE source = 'handler';
DROP TRIGGER events_root ON events;
DROP FUNCTION events_root();
ALTER TABLE events
    DROP CONSTRAINT events_source_made,
    DROP CONSTRAINT events_handler_made,
    DROP CONSTRAINT events_processed_at,
    DROP CONSTRAINT events_source_check,
    ADD CONSTRAINT events_source_check CHECK (source IN ('jira')),
    DROP COLUMN processed_at,
    DROP COLUMN status,
    DROP COLUMN handler_id,
    DROP COLUMN depth,
    DROP COLUMN root_id,
    DROP COLUMN parent_id;

COMMIT;
```

(Dropping the columns drops `events_pending`, `events_chain` and `events_by_parent` with them.)

In `db/tests/run.sh`, insert right after the second `run_migrate postgres up >> "$test_log" …` line (before the existing
0088 `down 1` step):

```sh
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
handlers_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.event_handlers') IS NULL AND to_regclass('ka.event_handler_runs') IS NULL
        AND NOT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'ka' AND table_name = 'events' AND column_name = 'status')")
[ "$handlers_removed" = t ] || { echo 'FAIL: 0089 down left handler tables or event status' >&2; exit 1; }
```

and after the existing `printf '%s\n' 'PASS: events are recorded in ka.events.'` line:

```sh
handlers=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.event_handlers') IS NOT NULL AND to_regclass('ka.event_handler_runs') IS NOT NULL
        AND to_regclass('ka.events_pending') IS NOT NULL")
[ "$handlers" = t ] || { echo 'FAIL: 0089 event handlers missing' >&2; exit 1; }
printf '%s\n' 'PASS: events have a status and a chain; handlers and their runs exist.'
```

The rollback chain still ends at version 83 (one more `down 1` before the existing ones).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_handlers_migration.py tests/test_events_migration.py tests/test_jira_events.py tests/test_events_api.py`
Expected: PASS. Then `sh db/tests/run.sh` from the worktree root — exit 0 and the new PASS line printed.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0089_event_handlers.up.sql db/migrations/0089_event_handlers.down.sql db/tests/run.sh \
  services/admin-api/tests/test_event_handlers_migration.py services/admin-api/tests/test_events_migration.py
git commit -m "feat(db): event status and chains, event handlers and their runs (0089)"
```

---

### Task 2: `event_matching.py` — type patterns and field conditions

**Files:**
- Create: `services/admin-api/src/knowledge_admin/event_matching.py`
- Create: `services/admin-api/tests/test_event_matching.py`
- Modify: `services/admin-api/pyproject.toml`, `services/admin-api/uv.lock` (add `regex` and `jinja2`; Task 3 uses jinja2)

**Interfaces:**
- Produces: `ConditionError(ValueError)`; `check_pattern(pattern: str) -> None`; `check_conditions(conditions:
  list[dict]) -> list[dict]` (normalized `{field, op, value}`); `type_matches(pattern: str, event_type: str) -> bool`;
  `explain(handler: dict, event: dict) -> str | None` (handler needs `type_pattern`, `conditions`); `matches(handler,
  event) -> bool`; `OPERATORS`.

- [ ] **Step 1: Add the dependencies**

In `services/admin-api/pyproject.toml` `dependencies`, after `"tzdata>=2026.3",` add:

```toml
    "jinja2>=3.1.6",
    "regex>=2026.9.3",
```

Run: `cd services/admin-api && uv lock` (if offline: `uv lock --offline`). Expected: only the `admin-api` package's
`requires-dist` changes in `uv.lock`; no version changes (`git diff --stat uv.lock` shows a few lines).

- [ ] **Step 2: Write the failing test** `services/admin-api/tests/test_event_matching.py`

```python
"""Handler matching: type patterns with *, field conditions on source/type/subject/title/payload.*, Preview reasons."""
import time

import pytest

from knowledge_admin.event_matching import ConditionError, check_conditions, check_pattern, explain, matches, type_matches

EVENT = {'id': 1, 'source': 'jira', 'type': 'jira.ticket.updated', 'subject': 'DEMO-1', 'title': 'Fix login',
         'payload': {'status': 'In Progress', 'labels': ['agent', 'ui'], 'priority': None, 'points': 3,
                     'changes': {'status': {'from': 'Open', 'to': 'In Progress'}}}}


def handler(pattern: str = 'jira.ticket.*', *conditions: dict) -> dict:
    return {'type_pattern': pattern, 'conditions': check_conditions(list(conditions))}


def cond(field: str, op: str, value=None) -> dict:
    return {'field': field, 'op': op, 'value': value}


@pytest.mark.parametrize('pattern,expected', [
    ('jira.ticket.updated', True), ('jira.ticket.*', True), ('jira.*', True), ('*', True), ('jira.*.updated', True),
    ('jira.*.created', False), ('jira.ticket', False), ('jira.ticket.updated.more', False), ('mail.*', False)])
def test_type_patterns(pattern, expected):
    check_pattern(pattern)
    assert type_matches(pattern, 'jira.ticket.updated') is expected


@pytest.mark.parametrize('pattern', ['', 'Jira.*', 'jira..x', 'jira.t*', '.jira', 'jira.', 'a' * 201])
def test_bad_patterns(pattern):
    with pytest.raises(ConditionError, match='lowercase dotted names'):
        check_pattern(pattern)


@pytest.mark.parametrize('condition,expected', [
    (cond('payload.status', 'equals', 'In Progress'), True),
    (cond('payload.status', 'equals', 'Done'), False),
    (cond('payload.points', 'equals', '3'), True),
    (cond('payload.nope', 'equals', 'x'), False),
    (cond('payload.status', 'not_equals', 'Done'), True),
    (cond('payload.nope', 'not_equals', 'x'), True),
    (cond('payload.labels', 'contains', 'agent'), True),
    (cond('payload.labels', 'contains', 'ag'), False),
    (cond('title', 'contains', 'login'), True),
    (cond('payload.status', 'in', ['Open', 'In Progress']), True),
    (cond('payload.status', 'in', ['Open']), False),
    (cond('subject', 'matches', r'^DEMO-\d+$'), True),
    (cond('payload.labels', 'matches', 'agent'), False),
    (cond('payload.changes.status', 'exists'), True),
    (cond('payload.priority', 'exists'), False),
    (cond('payload.nope', 'exists'), False),
    (cond('source', 'equals', 'jira'), True),
])
def test_operators(condition, expected):
    assert matches(handler('*', condition), EVENT) is expected


def test_all_conditions_must_hold():
    both = handler('jira.ticket.*', cond('payload.status', 'equals', 'In Progress'), cond('payload.labels', 'contains', 'x'))
    assert matches(both, EVENT) is False


def test_explain_names_the_first_failing_check():
    assert explain(handler('mail.*'), EVENT) == 'type is "jira.ticket.updated", not mail.*'
    assert explain(handler('*', cond('payload.status', 'equals', 'Done')), EVENT) == 'payload.status is "In Progress", not "Done"'
    assert explain(handler('*', cond('payload.nope', 'exists')), EVENT) == 'payload.nope is missing; it must exist'
    assert explain(handler('*', cond('payload.labels', 'contains', 'x')), EVENT) == 'payload.labels is ["agent", "ui"]; it must contain "x"'
    assert explain(handler('*'), EVENT) is None


@pytest.mark.parametrize('condition,message', [
    (cond('payload', 'equals', 'x'), 'Condition 1: use source, type, subject, title or payload.<field>'),
    (cond('labels', 'equals', 'x'), 'Condition 1: use source'),
    (cond('title', 'like', 'x'), 'Condition 1: choose an operator'),
    (cond('title', 'equals', ''), 'Condition 1: enter a value'),
    (cond('title', 'equals', ['x']), 'Condition 1: enter a value'),
    (cond('title', 'in', 'x'), 'Condition 1: list one or more values'),
    (cond('title', 'in', []), 'Condition 1: list one or more values'),
    (cond('title', 'matches', '('), 'Condition 1: the pattern is not a valid regular expression'),
    (cond('title', 'matches', 'a' * 501), 'Condition 1: the pattern is longer than 500 characters'),
    (cond('title', 'equals', 'x' * 1001), 'Condition 1: the value is longer than 1000 characters'),
])
def test_bad_conditions(condition, message):
    with pytest.raises(ConditionError) as raised:
        check_conditions([condition])
    assert str(raised.value).startswith(message)


def test_exists_drops_its_value():
    assert check_conditions([cond('title', 'exists', 'ignored')]) == [{'field': 'title', 'op': 'exists', 'value': None}]


def test_a_runaway_regex_times_out_as_no_match():
    event = {**EVENT, 'subject': 'a' * 40 + 'b'}
    started = time.monotonic()
    assert matches(handler('*', cond('subject', 'matches', r'^(a+)+$')), event) is False
    assert time.monotonic() - started < 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_matching.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_admin.event_matching'`.

- [ ] **Step 4: Write the implementation** `services/admin-api/src/knowledge_admin/event_matching.py`

```python
"""Event handler matching: an event type pattern plus field conditions that must all hold. Used by the event processor
and by the handler API (validation on save, the reason shown by Preview)."""
import json
import re
from typing import Any

import regex

OPERATORS = ('equals', 'not_equals', 'contains', 'in', 'matches', 'exists')
PATTERN = re.compile(r'^(\*|[a-z][a-z0-9_]*)(\.(\*|[a-z][a-z0-9_]*))*$')
FIELD = re.compile(r'^(source|type|subject|title|payload(\.[A-Za-z0-9_-]+)+)$')
MAX_PATTERN, MAX_REGEX, MAX_VALUE = 200, 500, 1000
REGEX_SECONDS = 0.1
MISSING = object()
BAD_PATTERN = 'Use lowercase dotted names with * for any part, e.g. jira.ticket.* or jira.ticket.updated.'
WANTED = {'not_equals': 'not be', 'contains': 'contain', 'in': 'be one of', 'matches': 'match', 'exists': 'exist'}


class ConditionError(ValueError):
    """A pattern or condition that cannot be saved; the message is shown on the handler form."""


def check_pattern(pattern: str) -> None:
    if len(pattern) > MAX_PATTERN or not PATTERN.fullmatch(pattern):
        raise ConditionError(BAD_PATTERN)


def check_conditions(conditions: list[dict]) -> list[dict]:
    """The conditions as stored, {field, op, value}: value is None for exists, a list of strings for in, else a string."""
    checked = []
    for number, condition in enumerate(conditions, 1):
        field, op, value = condition.get('field') or '', condition.get('op'), condition.get('value')
        where = f'Condition {number}'
        if not FIELD.fullmatch(field):
            raise ConditionError(f'{where}: use source, type, subject, title or payload.<field>, e.g. payload.status.')
        if op not in OPERATORS:
            raise ConditionError(f'{where}: choose an operator.')
        if op == 'exists':
            value = None
        elif op == 'in':
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
                raise ConditionError(f'{where}: list one or more values.')
        elif not isinstance(value, str) or not value:
            raise ConditionError(f'{where}: enter a value.')
        if op == 'matches' and len(value) > MAX_REGEX:
            raise ConditionError(f'{where}: the pattern is longer than {MAX_REGEX} characters.')
        if value is not None and any(len(item) > MAX_VALUE for item in (value if isinstance(value, list) else [value])):
            raise ConditionError(f'{where}: the value is longer than {MAX_VALUE} characters.')
        if op == 'matches':
            try:
                regex.compile(value)
            except regex.error as exc:
                raise ConditionError(f'{where}: the pattern is not a valid regular expression ({exc}).') from None
        checked.append({'field': field, 'op': op, 'value': value})
    return checked


def type_matches(pattern: str, event_type: str) -> bool:
    """`*` alone matches every type; a `*` part matches one part, a trailing `.*` one or more trailing parts."""
    if pattern == '*':
        return True
    parts = pattern.split('.')
    expression = r'\.'.join(
        (r'[a-z0-9_]+(\.[a-z0-9_]+)*' if index == len(parts) - 1 else r'[a-z0-9_]+') if part == '*' else re.escape(part)
        for index, part in enumerate(parts))
    return re.fullmatch(expression, event_type) is not None


def lookup(event: dict, field: str) -> Any:
    if not field.startswith('payload.'):
        return event.get(field, MISSING)
    value: Any = event.get('payload')
    for key in field.split('.')[1:]:
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def text(value: Any) -> str:
    """Strings as they are; numbers, booleans and null as JSON (3 → "3", true → "true")."""
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def holds(condition: dict, actual: Any) -> bool:
    op, expected = condition['op'], condition['value']
    if op == 'exists':
        return actual is not MISSING and actual is not None
    if actual is MISSING:
        return op == 'not_equals'
    if op == 'equals':
        return text(actual) == expected
    if op == 'not_equals':
        return text(actual) != expected
    if op == 'contains':
        if isinstance(actual, list):
            return any(text(item) == expected for item in actual)
        return isinstance(actual, str) and expected in actual
    if op == 'in':
        return text(actual) in expected
    if op == 'matches':
        if isinstance(actual, (dict, list)):
            return False
        try:
            return regex.search(expected, text(actual), timeout=REGEX_SECONDS) is not None
        except TimeoutError:
            return False
    return False


def show(value: Any) -> str:
    return 'missing' if value is MISSING else json.dumps(value, ensure_ascii=False)


def explain(handler: dict, event: dict) -> str | None:
    """None when the handler matches the event, else the first failing check in words (Preview shows it)."""
    if not type_matches(handler['type_pattern'], event['type']):
        return f"type is {show(event['type'])}, not {handler['type_pattern']}"
    for condition in handler['conditions']:
        actual = lookup(event, condition['field'])
        if holds(condition, actual):
            continue
        field, op, value = condition['field'], condition['op'], condition['value']
        if op == 'equals':
            return f'{field} is {show(actual)}, not {show(value)}'
        return f"{field} is {show(actual)}; it must {WANTED[op]}{'' if op == 'exists' else ' ' + show(value)}"
    return None


def matches(handler: dict, event: dict) -> bool:
    return explain(handler, event) is None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_matching.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/pyproject.toml services/admin-api/uv.lock \
  services/admin-api/src/knowledge_admin/event_matching.py services/admin-api/tests/test_event_matching.py
git commit -m "feat(events): handler matching — type patterns and field conditions"
```

---

### Task 3: `event_templates.py` — sandboxed Jinja for emit and prompt

**Files:**
- Create: `services/admin-api/src/knowledge_admin/event_templates.py`
- Create: `services/admin-api/tests/test_event_templates.py`

**Interfaces:**
- Consumes: `jinja2` (added in Task 2).
- Produces: `TemplateProblem(ValueError)`; `check_emit(emit: dict) -> None`; `check_prompt(prompt: str) -> None`;
  `render_emit(emit: dict, event: dict) -> dict` (`{type, subject, title, payload}`); `render_prompt(prompt: str,
  event: dict) -> str`; `DEFAULTS`, `MAX_OUTPUT = 20000`.

- [ ] **Step 1: Write the failing test** `services/admin-api/tests/test_event_templates.py`

```python
"""Handler templates: the matched event is `event`; strict, sandboxed, size-limited; payload must be a JSON object."""
from datetime import UTC, datetime

import pytest

from knowledge_admin.event_templates import TemplateProblem, check_emit, check_prompt, render_emit, render_prompt

EVENT = {'id': 7, 'source': 'jira', 'type': 'jira.ticket.updated', 'subject': 'DEMO-1', 'title': 'Fix <login>',
         'occurred_at': datetime(2026, 10, 9, 10, tzinfo=UTC), 'parent_id': None, 'root_id': 7, 'depth': 0,
         'payload': {'status': 'In Progress', 'summary': 'Fix "login"', 'labels': ['agent']}}


def test_defaults_copy_the_parent():
    assert render_emit({'type': 'triage.requested'}, EVENT) == {
        'type': 'triage.requested', 'subject': 'DEMO-1', 'title': 'Fix <login>', 'payload': EVENT['payload']}


def test_custom_templates_see_every_event_field():
    emit = {'type': 'triage.{{ event.payload.status | lower | replace(" ", "_") }}',
            'subject': '{{ event.subject }}-triage', 'title': 'Triage {{ event.title }} ({{ event.occurred_at }})',
            'payload': '{"summary": {{ event.payload.summary | tojson }}, "from": {{ event.id }}, "root": {{ event.root_id }}}'}
    assert render_emit(emit, EVENT) == {'type': 'triage.in_progress', 'subject': 'DEMO-1-triage',
                                        'title': 'Triage Fix <login> (2026-10-09T10:00:00+00:00)',
                                        'payload': {'summary': 'Fix "login"', 'from': 7, 'root': 7}}


@pytest.mark.parametrize('emit,message', [
    ({'type': '{{ event.payload.nope }}'}, 'Template error:'),
    ({'type': 'Bad Type'}, 'The new event type "Bad Type" is not valid'),
    ({'type': 'a.b', 'subject': '{{ "" }}'}, 'The new event subject is empty.'),
    ({'type': 'a.b', 'title': '{{ "x" * 501 }}'}, 'The new event subject and title must be at most 500 characters.'),
    ({'type': 'a.b', 'payload': '{"title": {{ event.title }}}'}, 'The payload template must render JSON'),
    ({'type': 'a.b', 'payload': '[1, 2]'}, 'The payload template must render a JSON object.'),
    ({'type': 'a.b', 'subject': "{{ ''.__class__.__mro__ }}"}, 'Template error:'),
])
def test_render_problems(emit, message):
    with pytest.raises(TemplateProblem) as raised:
        render_emit(emit, EVENT)
    assert str(raised.value).startswith(message)


def test_huge_output_is_refused_before_it_is_built():
    with pytest.raises(TemplateProblem, match='longer than 20000 characters'):
        render_prompt("{{ 'x' * 10000000 }}", EVENT)
    with pytest.raises(TemplateProblem, match='exponent is too large'):
        render_prompt('{{ 9 ** 9 ** 9 }}', EVENT)
    with pytest.raises(TemplateProblem, match='longer than 20000 characters'):
        render_prompt('{% for i in range(30000) %}x{% endfor %}', EVENT)


def test_prompt():
    assert render_prompt('  Triage {{ event.subject }}: {{ event.payload.summary }}  ', EVENT) == 'Triage DEMO-1: Fix "login"'
    with pytest.raises(TemplateProblem, match='The prompt rendered empty.'):
        render_prompt('{{ "" }}', EVENT)


def test_syntax_is_checked_on_save():
    check_emit({'type': 'triage.requested', 'payload': '{"a": {{ event.id }}}'})
    check_prompt('Triage {{ event.subject }}')
    with pytest.raises(TemplateProblem, match=r'^Subject: .*line 1'):
        check_emit({'type': 'a.b', 'subject': '{{ event.subject'})
    with pytest.raises(TemplateProblem, match='^New event type: enter a type'):
        check_emit({'type': '  '})
    with pytest.raises(TemplateProblem, match='^Prompt: write what the agent should do.'):
        check_prompt(' ')
    with pytest.raises(TemplateProblem, match='^Prompt: '):
        check_prompt('{% if %}')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_templates.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation** `services/admin-api/src/knowledge_admin/event_templates.py`

```python
"""Handler templates: Jinja in a sandbox with the matched event as `event`. Emit renders the new event's type, subject,
title and payload (JSON that must be an object); Send to agent renders the agent's first message."""
import json
import re
from typing import Any

from jinja2 import StrictUndefined, TemplateSyntaxError
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

MAX_OUTPUT = 20000
MAX_TEXT = 500
EVENT_TYPE = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')
DEFAULTS = {'subject': '{{ event.subject }}', 'title': '{{ event.title }}', 'payload': '{{ event.payload | tojson }}'}
TOO_LONG = f'Template output is longer than {MAX_OUTPUT} characters.'


class TemplateProblem(ValueError):
    """A template that does not parse or render; shown on the handler form, in Preview and on the handler run."""


class Sandbox(SandboxedEnvironment):
    """Jinja's sandbox that also refuses to build huge strings, lists or numbers ('x' * 10**9, 9 ** 9 ** 9)."""
    intercepted_binops = frozenset({'*', '**'})

    def call_binop(self, context, operator, left, right):
        if operator == '**' and isinstance(right, (int, float)) and abs(right) > 100:
            raise SecurityError('The exponent is too large.')
        if operator == '*':
            sized = next((side for side in (left, right) if isinstance(side, (str, list, tuple))), None)
            count = next((side for side in (left, right) if isinstance(side, int) and not isinstance(side, bool)), 0)
            if sized is not None and count * len(sized) > MAX_OUTPUT:
                raise SecurityError(TOO_LONG)
        return super().call_binop(context, operator, left, right)


ENV = Sandbox(undefined=StrictUndefined, autoescape=False)


def context(event: dict) -> dict[str, Any]:
    occurred = event.get('occurred_at')
    return {'id': event['id'], 'source': event['source'], 'type': event['type'], 'subject': event['subject'],
            'title': event['title'], 'occurred_at': occurred.isoformat() if hasattr(occurred, 'isoformat') else occurred,
            'payload': event['payload'], 'parent_id': event.get('parent_id'), 'root_id': event.get('root_id'),
            'depth': event.get('depth', 0)}


def check(source: str, label: str) -> None:
    try:
        ENV.parse(source)
    except TemplateSyntaxError as exc:
        raise TemplateProblem(f'{label}: {exc.message} (line {exc.lineno}).') from None


def render(source: str, event: dict) -> str:
    try:
        chunks, size = [], 0
        for chunk in ENV.from_string(source).generate(event=context(event)):
            size += len(chunk)
            if size > MAX_OUTPUT:
                raise SecurityError(TOO_LONG)
            chunks.append(chunk)
    except Exception as exc:  # undefined fields, sandbox refusals, errors raised inside filters
        message = str(exc)
        raise TemplateProblem(message if message == TOO_LONG else f'Template error: {message}') from None
    return ''.join(chunks)


def check_emit(emit: dict) -> None:
    if not (emit.get('type') or '').strip():
        raise TemplateProblem('New event type: enter a type, e.g. triage.requested.')
    for field, label in (('type', 'New event type'), ('subject', 'Subject'), ('title', 'Title'), ('payload', 'Payload')):
        check(emit.get(field) or DEFAULTS.get(field, ''), label)


def check_prompt(prompt: str) -> None:
    if not prompt.strip():
        raise TemplateProblem('Prompt: write what the agent should do.')
    check(prompt, 'Prompt')


def render_emit(emit: dict, event: dict) -> dict:
    kind = render(emit['type'], event).strip()
    if not EVENT_TYPE.fullmatch(kind):
        raise TemplateProblem(f'The new event type "{kind[:100]}" is not valid; use lowercase dotted names such as triage.requested.')
    subject = render(emit.get('subject') or DEFAULTS['subject'], event).strip()
    if not subject:
        raise TemplateProblem('The new event subject is empty.')
    title = render(emit.get('title') or DEFAULTS['title'], event).strip()
    if len(subject) > MAX_TEXT or len(title) > MAX_TEXT:
        raise TemplateProblem(f'The new event subject and title must be at most {MAX_TEXT} characters.')
    try:
        payload = json.loads(render(emit.get('payload') or DEFAULTS['payload'], event))
    except json.JSONDecodeError as exc:
        raise TemplateProblem(f'The payload template must render JSON ({exc.msg}, line {exc.lineno}). Use | tojson for values.') from None
    if not isinstance(payload, dict):
        raise TemplateProblem('The payload template must render a JSON object.')
    return {'type': kind, 'subject': subject, 'title': title, 'payload': payload}


def render_prompt(prompt: str, event: dict) -> str:
    text = render(prompt, event).strip()
    if not text:
        raise TemplateProblem('The prompt rendered empty.')
    if '\x00' in text:
        raise TemplateProblem('The prompt contains a null character.')
    return text
```

Note: the `range(30000)` loop is caught by the streaming size check in `render` (Jinja's sandbox already caps
`range` at 100000 items).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_templates.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/event_templates.py services/admin-api/tests/test_event_templates.py
git commit -m "feat(events): sandboxed Jinja templates for emitted events and agent prompts"
```

---

### Task 4: `start_session` and `event` sessions (API and worker side)

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/agent_sessions.py` (extract `start_session`; list and review accept `event`)
- Modify: `services/admin-api/src/knowledge_admin/generations_api.py:131` (`purpose in ('test', 'event')`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py:206-217` (no draft unless documents)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py:310` (run kind `event_session`)
- Modify: `services/admin-api/tests/session_rows.py` (`add_test_session(..., purpose='test')`)
- Modify: `services/admin-api/tests/test_test_sessions.py` (list rows now include `purpose`)
- Create: `services/admin-api/tests/test_event_sessions.py`
- Modify: `services/admin-api/tests/test_test_session_worker.py` (event session turn keeps no draft)

**Interfaces:**
- Consumes: purpose `event` (Task 1).
- Produces: `class SessionRefused(ValueError)` with `.status: int`; `start_session(conn, project: UUID, agent_id: str,
  actor: UUID, message: str, *, purpose: str = 'test', run_resources: RunResources | None = None, title: str | None =
  None, conninfo: str = '') -> UUID`. Agent session list rows gain `purpose`.

- [ ] **Step 1: Write the failing tests**

`services/admin-api/tests/test_event_sessions.py`:

```python
"""Sessions an event handler starts (purpose 'event'): start_session creates them like a Test session; they are listed
with the agent's Test sessions and shown the same way (configuration, suggestions)."""
import pytest

from knowledge_admin.agent_sessions import NO_MODEL, SessionRefused, start_session
from test_repositories import HEADERS
from test_test_sessions import tester  # noqa: F401  (fixture)


def started(t, message='Triage DEMO-1', **options):
    with t.store.connect() as conn:
        project = t.store.project(conn, 'shop')
        actor = t.store.actor(conn, 'admin-id', 'admin@example.test')
        return start_session(conn, project, t.agent, actor, message, conninfo=t.store.conninfo, **options)


def test_an_event_session_starts_like_a_test_session(tester):
    t = tester
    session_id = started(t, purpose='event', title='jira.ticket.created DEMO-1')
    detail = t.client.get(f'/api/generations/sessions/{session_id}', headers=HEADERS).json()
    assert (detail['purpose'], detail['status'], detail['title'], detail['agent_id']) == (
        'event', 'starting', 'jira.ticket.created DEMO-1', t.agent)
    assert [(i['seq'], i['text']) for i in detail['inputs']] == [(1, 'Triage DEMO-1')]
    assert detail['config']['id'] == t.agent and detail['reviews'] == []
    rows = t.client.get(t.path, headers=HEADERS).json()['sessions']
    assert [(row['id'], row['purpose']) for row in rows] == [(str(session_id), 'event')]


def test_refusals_carry_the_test_tab_status_and_message(tester):
    t = tester
    with t.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (t.agent,))
    with pytest.raises(SessionRefused) as raised:
        started(t, purpose='event')
    assert (raised.value.status, str(raised.value)) == (409, 'Agent is disabled.')
    with t.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=true,model_id=NULL WHERE id=%s', (t.agent,))
    with pytest.raises(SessionRefused) as raised:
        started(t, purpose='event')
    assert (raised.value.status, str(raised.value)) == (409, NO_MODEL)
```

In `services/admin-api/tests/test_test_sessions.py`, in `test_the_first_message_pins_the_configuration_for_the_whole_session`,
change the key-set assertion to:

```python
    assert set(rows[0]) == {'id', 'title', 'status', 'created_at', 'last_activity_at', 'actor_email', 'purpose'}
```

In `services/admin-api/tests/session_rows.py`, change `add_test_session` to take `purpose: str = 'test'` (keyword) and
insert it instead of the literal `'test'`:

```python
def add_test_session(conn, project, actor, agent_id: str, model_id, *, config: dict[str, Any] | None = None,
                     message: str | None = None, status: str = 'starting', turns: int = 0, purpose: str = 'test') -> UUID:
    """A Test session of the agent as POST /api/agents/{id}/sessions inserts it (or, with purpose 'event', as an event
    handler starts one); `message` queues input 1."""
    session = uuid4()
    conn.execute('''INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,purpose,agent_config,status,turns,closed_at)
        VALUES (%s,%s,%s,%s,%s,%s,'{}',%s,%s,%s,%s,%s,CASE WHEN %s='closed' THEN now() END)''',
        (session, project, agent_id, actor, model_id, (message or 'Test chat')[:200],
         Jsonb({'folders': [], 'code_project_ids': []}), purpose, Jsonb(config or {'id': agent_id}), status, turns, status))
```

(the `if message is not None:` input insert below stays as it is).

Append to `services/admin-api/tests/test_test_session_worker.py`:

```python
def test_an_event_session_turn_leaves_no_draft(sessions, monkeypatch):
    monkeypatch.setattr(run_record, '_store', sessions.store)
    agent_id = native_agent(sessions.client, 'Event agent', model_id=sessions.claude)
    config = jsonable_encoder(get_agent_config(agent_id, conninfo=sessions.store.conninfo))
    with sessions.store.connect() as conn:
        project = sessions.store.project(conn, 'shop')
        actor = sessions.store.actor(conn, 'admin-id', 'admin@example.test')
        sid = add_test_session(conn, project, actor, agent_id, sessions.claude, config=config, message='Triage DEMO-1',
                               purpose='event')
    store = sessions.store
    fake = FakeAgent([{'steps': [llm(message('Triaged.'))], 'answer': 'Triaged.'}])

    async def scenario():
        runner = asyncio.create_task(native_sessions.NativeRunner(store, claimed(store), fake, poll=0.02).run())
        done = await wait_for(store, sid, lambda s: s['status'] == 'waiting' and s['turns'] == 1)
        await post(sessions, f'/api/generations/sessions/{sid}/close')
        await asyncio.wait_for(runner, 5)
        return done

    done = asyncio.run(scenario())
    assert (done['purpose'], done['draft'], done['draft_turn']) == ('event', None, None)
    with store.connect() as conn:
        start = conn.execute("SELECT payload FROM ka.agent_run_events WHERE run_key=%s AND kind='start'",
                             (shared.run_key(sid, 1),)).fetchone()['payload']
    assert start['kind'] == 'event_session'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_sessions.py tests/test_test_sessions.py tests/test_test_session_worker.py`
Expected: FAIL (`ImportError: cannot import name 'SessionRefused'`; key set; the worker writes a draft for the event
session → `CheckViolation` on `agent_sessions_documentation`).

- [ ] **Step 3: Implement**

In `agent_sessions.py`, update the module docstring's first sentence to: `Test sessions: the agent Test tab's
conversations, run by agent-session-worker like native Generate documents sessions (ka.agent_sessions with purpose
'test'; an event handler's Send to agent starts the same kind of session with purpose 'event').` Then add after
`NO_SUBJECTS`:

```python
NOT_A_TEST = 'Suggestions are for Test and event sessions only.'
CONVERSATIONS = ('test', 'event')  # sessions shown and reviewed in the agent's Test tab


class SessionRefused(ValueError):
    """Why an agent session cannot start; `status` is the HTTP status the Test tab answers with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def start_session(conn, project: UUID, agent_id: str, actor: UUID, message: str, *, purpose: str = 'test',
                  run_resources: RunResources | None = None, title: str | None = None, conninfo: str = '') -> UUID:
    """Start an agent session with the agent's current configuration pinned (a per-run agent's narrowed to
    `run_resources`); `message` becomes input 1 and agent-session-worker runs it. Used by the Test tab and by event
    handlers (purpose 'event', with the handler's creator as actor)."""
    try:
        config = get_agent_config(agent_id, conninfo=conninfo)
    except LookupError as exc:
        raise SessionRefused(404, str(exc)) from None
    except ValueError as exc:
        raise SessionRefused(409, str(exc)) from None
    try:
        pinned = resolve(conn, project, config, run_resources)
        require_run_resources(pinned)
        if not pinned['enabled']:
            raise ValueError('Agent is disabled.')
        model = pinned['model']
        if model is None or not model['enabled'] or not model_fits(model):
            raise ValueError(NO_MODEL)
    except RunResourceError as exc:  # a ValueError: caught first
        raise SessionRefused(400, str(exc)) from None
    except ValueError as exc:
        raise SessionRefused(409, str(exc)) from None
    pinned = pin(conn, pinned, agent_id)
    session_id = uuid4()
    # The worker decrypts per turn: the pinned config holds credential references only.
    conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,purpose,agent_config,run_resources)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (session_id, project, agent_id, actor, model['id'], (title or message)[:200],
         Jsonb({'system_prompt': agent_system_prompt(pinned)}), Jsonb(NO_SUBJECTS), purpose,
         Jsonb(jsonable_encoder(pinned)), Jsonb(pinned['run_resources']) if per_run(config) else None))
    conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                 (session_id, actor, message))
    return session_id
```

Remove the old `NOT_A_TEST = 'Suggestions are for Test sessions only.'` line. Replace the body of `create` with:

```python
    @router.post('/{agent_id}/sessions', status_code=201)
    def create(agent_id: str, value: TestSessionInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            try:
                session_id = start_session(conn, project, agent_id, actor, value.message,
                                           run_resources=value.run_resources, conninfo=db.conninfo)
            except SessionRefused as exc:
                raise HTTPException(exc.status, str(exc)) from None
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'agent.test_session.created','agent_session',%s,%s)''',
                (project, actor, session_id, Jsonb({'agent_id': agent_id})))
            return session_detail(conn, project, session_id)
```

In `sessions` (the list): docstring `This agent's Test and event sessions, newest first.`; select `s.purpose` and filter
`s.purpose IN ('test','event')`:

```python
            return {'sessions': conn.execute('''SELECT s.id,s.title,s.status,s.purpose,s.created_at,s.last_activity_at,
                p.email AS actor_email FROM ka.agent_sessions s JOIN ka.principals p ON p.id=s.actor_id
                WHERE s.agent_id=%s AND s.project_id=%s AND s.purpose IN ('test','event')
                ORDER BY s.created_at DESC,s.id LIMIT 100''', (agent_id, db.project(conn, app_name))).fetchall()}
```

In `review`: `if session['purpose'] not in CONVERSATIONS: raise HTTPException(400, NOT_A_TEST)`.

`generations_api.py:131`: `if row['purpose'] in ('test', 'event'):  # its pinned configuration … and its suggestions`.

`session_worker.py` `finish_turn`: docstring `…; a Test or event session never gets a draft…`; SQL:

```python
                draft=CASE WHEN purpose<>'documents' THEN draft ELSE coalesce(%(draft)s::text, draft) END,
                draft_turn=CASE WHEN purpose<>'documents' OR %(draft)s::text IS NULL THEN draft_turn ELSE %(turn)s END,
```

`native_sessions.py:310`:

```python
        kind = {'test': 'test_session', 'event': 'event_session'}.get(self.session.get('purpose'), 'native_session')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_sessions.py tests/test_test_sessions.py tests/test_test_session_worker.py tests/test_test_session_feedback.py tests/test_generations.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/agent_sessions.py services/admin-api/src/knowledge_admin/generations_api.py \
  services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py \
  services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py \
  services/admin-api/tests/session_rows.py services/admin-api/tests/test_test_sessions.py \
  services/admin-api/tests/test_event_sessions.py services/admin-api/tests/test_test_session_worker.py
git commit -m "feat(agents): start_session shared by the Test tab and event handlers; event sessions"
```

---

### Task 5: `event_processor.py` — the 2 s loop in repository-worker

**Files:**
- Create: `services/admin-api/src/knowledge_admin/event_processor.py`
- Modify: `services/admin-api/src/knowledge_admin/repository_worker.py` (start/join the thread)
- Create: `services/admin-api/tests/test_event_processor.py`

**Interfaces:**
- Consumes: `event_matching.matches`, `event_templates.render_emit/render_prompt/TemplateProblem`,
  `agent_sessions.start_session/SessionRefused`.
- Produces: `run_once(store) -> bool`, `tick(store) -> int`, `loop(store, stopped: threading.Event) -> None`,
  `process(conn, store, event: dict) -> str`; constants `POLL_SECONDS = 2`, `BATCH = 50`, `MAX_DEPTH = 5`,
  `TOO_DEEP`, `NO_AGENT`, `UNEXPECTED`. Run messages `Emitted <type>.` / `Started a session with <agent name>.`

- [ ] **Step 1: Write the failing test** `services/admin-api/tests/test_event_processor.py`

```python
"""The event processor: each pending event goes once through the enabled handlers of its project; emitted events join
the chain and are processed on a later pass; failures are recorded and nothing is retried."""
import threading
from uuid import uuid4

from psycopg.types.json import Jsonb

from knowledge_admin import event_processor
from test_test_sessions import tester  # noqa: F401  (fixture)


def an_event(store, project=None, **values) -> dict:
    with store.connect() as conn:
        project = project or store.project(conn, 'shop')
        row = {'project_id': project, 'source': 'jira', 'source_id': uuid4(), 'type': 'jira.ticket.updated',
               'subject': 'DEMO-1', 'title': 'Fix login', 'payload': Jsonb({'status': 'In Progress', 'labels': ['agent']}),
               'dedupe_key': f'k{uuid4()}', 'occurred_at': '2026-10-09T10:00:00Z', **values}
        return conn.execute(f"INSERT INTO ka.events({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *",
                            tuple(row.values())).fetchone()


def a_handler(store, name='Triage', **values) -> dict:
    with store.connect() as conn:
        row = {'project_id': store.project(conn, 'shop'), 'name': name, 'type_pattern': 'jira.ticket.*',
               'conditions': Jsonb([]), 'action': 'emit', 'emit': Jsonb({'type': 'triage.requested'}),
               'created_by': store.actor(conn, 'admin-id', 'admin@example.test'), **values}
        return conn.execute(f"INSERT INTO ka.event_handlers({','.join(row)}) VALUES ({','.join(['%s'] * len(row))}) RETURNING *",
                            tuple(row.values())).fetchone()


def event(store, event_id) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.events WHERE id=%s', (event_id,)).fetchone()


def runs(store, event_id) -> list[dict]:
    with store.connect() as conn:
        return conn.execute('SELECT * FROM ka.event_handler_runs WHERE event_id=%s ORDER BY id', (event_id,)).fetchall()


def test_an_event_without_a_handler_is_processed(store):
    first = an_event(store)
    assert event_processor.run_once(store) is True
    assert event_processor.run_once(store) is False  # never picked up again
    assert (event(store, first['id'])['status'], runs(store, first['id'])) == ('processed', [])
    assert event(store, first['id'])['processed_at'] is not None


def test_emit_creates_a_pending_child_in_the_chain(store):
    handler = a_handler(store, conditions=Jsonb([{'field': 'payload.labels', 'op': 'contains', 'value': 'agent'}]),
                        emit=Jsonb({'type': 'triage.requested', 'title': 'Triage {{ event.subject }}',
                                    'payload': '{"from": {{ event.payload.status | tojson }}}'}))
    parent = an_event(store)
    assert event_processor.run_once(store) is True
    [run] = runs(store, parent['id'])
    child = event(store, run['emitted_event_id'])
    assert (run['status'], run['message'], run['handler_name'], run['handler_id']) == (
        'succeeded', 'Emitted triage.requested.', 'Triage', handler['id'])
    assert (child['source'], child['source_id'], child['type'], child['subject'], child['title'], child['payload']) == (
        'handler', handler['id'], 'triage.requested', 'DEMO-1', 'Triage DEMO-1', {'from': 'In Progress'})
    assert (child['parent_id'], child['root_id'], child['depth'], child['handler_id'], child['status']) == (
        parent['id'], parent['root_id'], 1, handler['id'], 'pending')
    assert child['dedupe_key'] == f"handler:{handler['id']}:{parent['id']}"
    assert event(store, parent['id'])['status'] == 'processed'
    assert event_processor.run_once(store) is True  # the child: no handler matches triage.* → processed
    assert event(store, child['id'])['status'] == 'processed'


def test_disabled_and_other_projects_handlers_do_not_run(store):
    a_handler(store, enabled=False)
    with store.connect() as conn:
        other = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('other-x','Other') RETURNING id").fetchone()['id']
    first = an_event(store, project=other)
    event_processor.run_once(store)
    assert (event(store, first['id'])['status'], runs(store, first['id'])) == ('processed', [])


def test_one_failing_handler_fails_the_event_but_the_others_run(store):
    a_handler(store, name='A broken', emit=Jsonb({'type': '{{ event.payload.nope }}'}))
    a_handler(store, name='B fine')
    first = an_event(store)
    event_processor.run_once(store)
    broken, fine = runs(store, first['id'])
    assert (broken['status'], fine['status']) == ('failed', 'succeeded')
    assert broken['message'].startswith('Template error:') and broken['emitted_event_id'] is None
    assert event(store, first['id'])['status'] == 'failed'
    assert event_processor.run_once(store) is True   # B's child
    assert event_processor.run_once(store) is False  # the failed event is not retried


def test_a_self_matching_handler_stops_at_depth_five(store):
    a_handler(store, type_pattern='*', emit=Jsonb({'type': 'loop.again'}))
    first = an_event(store)
    assert event_processor.tick(store) == 6  # depths 0..5; the one at depth 5 cannot emit
    with store.connect() as conn:
        chain = conn.execute('SELECT id,depth,status FROM ka.events WHERE root_id=%s ORDER BY depth', (first['id'],)).fetchall()
    assert [(row['depth'], row['status']) for row in chain] == [(0, 'processed'), (1, 'processed'), (2, 'processed'),
                                                                 (3, 'processed'), (4, 'processed'), (5, 'failed')]
    [last_run] = runs(store, chain[-1]['id'])
    assert last_run['message'] == event_processor.TOO_DEEP
    assert event_processor.tick(store) == 0


def test_a_claimed_event_is_not_processed_by_another_worker(store):
    an_event(store)
    with store.connect() as holder:
        assert holder.execute(event_processor.CLAIM).fetchone() is not None  # held until this block ends
        assert event_processor.run_once(store) is False


def test_send_to_agent_starts_an_event_session(tester):
    t = tester
    handler = a_handler(t.store, action='agent', emit=None, agent_id=t.agent,
                        prompt='Triage {{ event.subject }} ({{ event.payload.status }})')
    first = an_event(t.store)
    assert event_processor.run_once(t.store) is True
    [run] = runs(t.store, first['id'])
    assert (run['status'], run['message']) == ('succeeded', 'Started a session with Tester.')
    with t.store.connect() as conn:
        session = conn.execute('SELECT purpose,title,actor_id,agent_id FROM ka.agent_sessions WHERE id=%s',
                               (run['agent_session_id'],)).fetchone()
        text = conn.execute('SELECT text FROM ka.agent_session_inputs WHERE session_id=%s', (run['agent_session_id'],)).fetchone()['text']
    assert (session['purpose'], session['title'], session['actor_id'], session['agent_id']) == (
        'event', 'jira.ticket.updated DEMO-1', handler['created_by'], t.agent)
    assert text == 'Triage DEMO-1 (In Progress)'
    assert event(t.store, first['id'])['status'] == 'processed'


def test_a_disabled_agent_fails_the_run(tester):
    t = tester
    a_handler(t.store, action='agent', emit=None, agent_id=t.agent, prompt='Triage {{ event.subject }}')
    with t.store.connect() as conn:
        conn.execute('UPDATE ka.agents SET enabled=false WHERE id=%s', (t.agent,))
    first = an_event(t.store)
    event_processor.run_once(t.store)
    [run] = runs(t.store, first['id'])
    assert (run['status'], run['message'], run['agent_session_id']) == ('failed', 'Agent is disabled.', None)
    assert event(t.store, first['id'])['status'] == 'failed'


def test_a_missing_agent_fails_the_run(store):
    a_handler(store, action='agent', emit=None, agent_id='gone', prompt='Triage {{ event.subject }}')
    first = an_event(store)
    event_processor.run_once(store)
    [run] = runs(store, first['id'])
    assert (run['status'], run['message']) == ('failed', event_processor.NO_AGENT)


def test_the_loop_survives_errors_and_stops(store, monkeypatch):
    calls = []

    def flaky(_store):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('boom')
        stopped.set()
        return 0

    stopped = threading.Event()
    monkeypatch.setattr(event_processor, 'POLL_SECONDS', 0.01)
    monkeypatch.setattr(event_processor, 'tick', flaky)
    event_processor.loop(store, stopped)
    assert len(calls) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_processor.py`
Expected: FAIL with `ImportError: cannot import name 'event_processor'`.

- [ ] **Step 3: Write the implementation** `services/admin-api/src/knowledge_admin/event_processor.py`

```python
"""The event processor: every pending event goes once through the enabled handlers of its project. A thread in
repository-worker claims one event at a time (FOR UPDATE SKIP LOCKED, so several workers never take the same event),
runs each matching handler's action in a savepoint, records one run per handler and marks the event processed — or
failed when a handler failed. Emitted events enter as pending and are processed on a later pass. Nothing is retried."""
import logging
import threading

import psycopg
from psycopg.types.json import Jsonb

from knowledge_admin import event_matching, event_templates
from knowledge_admin.agent_sessions import SessionRefused, start_session
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

POLL_SECONDS = 2
BATCH = 50
MAX_DEPTH = 5
TOO_DEEP = f'Chain too deep ({MAX_DEPTH} levels).'
NO_AGENT = 'Agent not found.'
UNEXPECTED = 'The handler failed unexpectedly. See the repository-worker log.'

CLAIM = "SELECT * FROM ka.events WHERE status='pending' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
HANDLERS = 'SELECT * FROM ka.event_handlers WHERE project_id=%s AND enabled ORDER BY name,id'


class HandlerFailed(Exception):
    """A handler action that could not run; the message is recorded on its run."""


def emit(conn, store: RepositoryStore, handler: dict, event: dict) -> tuple[str, dict]:
    if event['depth'] >= MAX_DEPTH:
        raise HandlerFailed(TOO_DEEP)
    new = event_templates.render_emit(handler['emit'], event)
    key = f"handler:{handler['id']}:{event['id']}"
    row = conn.execute("""INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,
        occurred_at,parent_id,root_id,depth,handler_id) VALUES (%s,'handler',%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)
        ON CONFLICT (project_id,dedupe_key) DO NOTHING RETURNING id""",
        (event['project_id'], handler['id'], new['type'], new['subject'], new['title'], Jsonb(new['payload']), key,
         event['id'], event['root_id'], event['depth'] + 1, handler['id'])).fetchone()
    if row is None:  # emitted before (a crash after the commit cannot happen, but a re-run must not duplicate)
        row = conn.execute('SELECT id FROM ka.events WHERE project_id=%s AND dedupe_key=%s', (event['project_id'], key)).fetchone()
    return f"Emitted {new['type']}.", {'emitted_event_id': row['id']}


def send(conn, store: RepositoryStore, handler: dict, event: dict) -> tuple[str, dict]:
    agent = conn.execute('SELECT name FROM ka.agents WHERE id=%s', (handler['agent_id'],)).fetchone()
    if agent is None:
        raise HandlerFailed(NO_AGENT)
    prompt = event_templates.render_prompt(handler['prompt'], event)
    try:
        session = start_session(conn, event['project_id'], handler['agent_id'], handler['created_by'], prompt,
                                purpose='event', title=f"{event['type']} {event['subject']}", conninfo=store.conninfo)
    except SessionRefused as exc:
        raise HandlerFailed(str(exc)) from None
    return f"Started a session with {agent['name']}.", {'agent_session_id': session}


ACTIONS = {'emit': emit, 'agent': send}


def process(conn, store: RepositoryStore, event: dict) -> str:
    """Run every matching enabled handler on the claimed event inside the caller's transaction; returns its status."""
    failed = False
    for handler in conn.execute(HANDLERS, (event['project_id'],)).fetchall():
        if not event_matching.matches(handler, event):
            continue
        links: dict = {}
        try:
            with conn.transaction():  # a savepoint: a failing action leaves nothing behind
                message, links = ACTIONS[handler['action']](conn, store, handler, event)
            status = 'succeeded'
        except (HandlerFailed, event_templates.TemplateProblem) as exc:
            status, message = 'failed', str(exc)
        except psycopg.OperationalError:
            raise  # the connection is gone: the event stays pending
        except Exception:
            logger.exception('Handler %s failed on event %s', handler['id'], event['id'])
            status, message = 'failed', UNEXPECTED
        failed = failed or status == 'failed'
        conn.execute("""INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message,emitted_event_id,
            agent_session_id) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (event['id'], handler['id'], handler['name'], status, message[:2000],
             links.get('emitted_event_id'), links.get('agent_session_id')))
    status = 'failed' if failed else 'processed'
    conn.execute('UPDATE ka.events SET status=%s,processed_at=now() WHERE id=%s', (status, event['id']))
    return status


def run_once(store: RepositoryStore) -> bool:
    with store.connect() as conn:
        event = conn.execute(CLAIM).fetchone()
        if event is None:
            return False
        status = process(conn, store, event)
    if status == 'failed':
        print(f"Event {event['id']} ({event['type']}): failed", flush=True)
    return True


def tick(store: RepositoryStore) -> int:
    """Process up to BATCH pending events, oldest first; returns how many."""
    done = 0
    while done < BATCH and run_once(store):
        done += 1
    return done


def loop(store: RepositoryStore, stopped: threading.Event) -> None:
    while not stopped.is_set():
        try:
            tick(store)
        except psycopg.Error:
            print('Event processor database unavailable; retrying', flush=True)
        except Exception:
            logger.exception('Event processor loop failed; retrying')
        stopped.wait(POLL_SECONDS)
```

In `repository_worker.py`: import `from knowledge_admin import event_processor, scheduler`; in `main` after
`schedules.start()`:

```python
    # Pending events go through the event handlers every 2 s (event_processor.py).
    events = threading.Thread(target=event_processor.loop, args=(store, stopped), name="event-processor")
    events.start()
```

and in `finally`, after `schedules.join()`: `events.join()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_processor.py tests/test_scheduler.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/event_processor.py services/admin-api/src/knowledge_admin/repository_worker.py \
  services/admin-api/tests/test_event_processor.py
git commit -m "feat(events): event processor loop runs handlers on pending events"
```

---

### Task 6: Event handlers API — CRUD, validation, preview, agents

**Files:**
- Create: `services/admin-api/src/knowledge_admin/event_handlers_api.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py` (import and `router.include_router(event_handlers_api.build_api(app_name, repository_store))` after the events router)
- Create: `services/admin-api/tests/test_event_handlers_api.py`

**Interfaces:**
- Consumes: `event_matching.check_pattern/check_conditions/explain/ConditionError`,
  `event_templates.check_emit/check_prompt/render_emit/render_prompt/TemplateProblem`.
- Produces: `GET /api/event-handlers` → `{handlers: [Handler]}`; `POST /api/event-handlers` → 201 `{handler}`;
  `PUT /api/event-handlers/{id}` → `{handler}`; `DELETE` → 204; `POST /api/event-handlers/preview` →
  `{matches, reason, output}`; `GET /api/event-handlers/agents` → `{agents: [{id, name}]}`. `Handler` =
  `{id, name, enabled, type_pattern, conditions, action, emit, agent_id, agent_name, prompt, revision, created_at,
  updated_at, last_run: {status, created_at} | null}`.

- [ ] **Step 1: Write the failing test** `services/admin-api/tests/test_event_handlers_api.py`

```python
"""Administration → Events → Handlers: create, list, edit with revisions, delete, Preview against a real event."""
import pytest
from psycopg.types.json import Jsonb

from test_api import _client
from test_events_api import seed
from test_repositories import HEADERS
from test_test_sessions import tester  # noqa: F401  (fixture)

EMIT = {'name': 'Triage', 'enabled': True, 'type_pattern': 'jira.ticket.*',
        'conditions': [{'field': 'payload.labels', 'op': 'contains', 'value': 'agent'}],
        'action': 'emit', 'emit': {'type': 'triage.requested', 'subject': '', 'title': 'Triage {{ event.subject }}', 'payload': ''}}


@pytest.fixture
def client(store, tmp_path):
    return _client(tmp_path, repository_store=store)


def create(client, **changes):
    response = client.post('/api/event-handlers', headers=HEADERS, json={**EMIT, **changes})
    assert response.status_code == 201, response.text
    return response.json()['handler']


def error(response, status):
    assert response.status_code == status, response.text
    return response.json()['error']['message']


def test_create_list_edit_delete(client, store):
    handler = create(client)
    assert (handler['name'], handler['revision'], handler['agent_id'], handler['prompt'], handler['last_run']) == (
        'Triage', 1, None, None, None)
    assert handler['conditions'] == [{'field': 'payload.labels', 'op': 'contains', 'value': 'agent'}]
    assert client.get('/api/event-handlers', headers=HEADERS).json()['handlers'] == [handler]
    saved = client.put(f"/api/event-handlers/{handler['id']}", headers=HEADERS,
                       json={**EMIT, 'enabled': False, 'revision': 1}).json()['handler']
    assert (saved['enabled'], saved['revision']) == (False, 2)
    assert error(client.put(f"/api/event-handlers/{handler['id']}", headers=HEADERS, json={**EMIT, 'revision': 1}), 409) == \
        'This handler was changed by someone else. Reload and try again.'
    assert error(client.post('/api/event-handlers', headers=HEADERS, json=EMIT), 409) == 'A handler with this name already exists.'
    assert client.delete(f"/api/event-handlers/{handler['id']}", headers=HEADERS).status_code == 204
    assert error(client.delete(f"/api/event-handlers/{handler['id']}", headers=HEADERS), 404) == 'Handler not found.'
    with store.connect() as conn:
        actions = [row['action'] for row in conn.execute(
            "SELECT action FROM ka.audit_events WHERE resource_type='event_handler' ORDER BY id")]
    assert actions == ['event_handler.created', 'event_handler.updated', 'event_handler.deleted']


def test_last_run_is_listed(client, store):
    handler = create(client)
    [event_id] = seed(store, 1)
    with store.connect() as conn:
        conn.execute("""INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message)
            VALUES (%s,%s,'Triage','failed','x')""", (event_id, handler['id']))
    [listed] = client.get('/api/event-handlers', headers=HEADERS).json()['handlers']
    assert listed['last_run']['status'] == 'failed'


@pytest.mark.parametrize('changes,message', [
    ({'type_pattern': 'Jira.*'}, 'Use lowercase dotted names'),
    ({'conditions': [{'field': 'labels', 'op': 'equals', 'value': 'x'}]}, 'Condition 1: use source'),
    ({'conditions': [{'field': 'title', 'op': 'in', 'value': 'x'}]}, 'Condition 1: list one or more values'),
    ({'emit': None}, 'New event type: enter a type'),
    ({'emit': {'type': 'a.b', 'subject': '{{ event.subject'}}, 'Subject: '),
    ({'action': 'agent', 'emit': None}, 'Choose an agent.'),
    ({'action': 'agent', 'emit': None, 'agent_id': 'nope', 'prompt': 'Hi'}, 'Agent not found.'),
    ({'name': ' '}, 'Enter a name.'),
])
def test_validation(client, changes, message):
    assert error(client.post('/api/event-handlers', headers=HEADERS, json={**EMIT, **changes}), 400).startswith(message)


def test_send_to_agent_handlers_and_the_agent_list(tester):
    t = tester
    listed = t.client.get('/api/event-handlers/agents', headers=HEADERS).json()['agents']
    assert {'id': t.agent, 'name': 'Tester'} in listed  # seeded agents (0020, 0031) may be listed too
    assert [agent['name'] for agent in listed] == sorted(agent['name'] for agent in listed)
    body = {**EMIT, 'action': 'agent', 'emit': {'type': 'ignored.type'}, 'agent_id': t.agent, 'prompt': 'Triage {{ event.subject }}'}
    response = t.client.post('/api/event-handlers', headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    handler = response.json()['handler']
    assert (handler['emit'], handler['agent_id'], handler['agent_name'], handler['prompt']) == (
        None, t.agent, 'Tester', 'Triage {{ event.subject }}')
    assert error(t.client.post('/api/event-handlers', headers=HEADERS, json={**body, 'name': 'B', 'prompt': ' '}), 400) == \
        'Prompt: write what the agent should do.'
    with t.store.connect() as conn:
        conn.execute("UPDATE ka.agents SET resources='per_run' WHERE id=%s", (t.agent,))
    assert t.agent not in [agent['id'] for agent in t.client.get('/api/event-handlers/agents', headers=HEADERS).json()['agents']]
    assert 'resources per run' in error(t.client.post('/api/event-handlers', headers=HEADERS, json={**body, 'name': 'C'}), 400)


def test_preview(client, store):
    [plain] = seed(store, 1)
    [labelled] = seed(store, 1, subject='DEMO-9', payload=Jsonb({'labels': ['agent'], 'changes': {}}))
    miss = client.post('/api/event-handlers/preview', headers=HEADERS, json={'handler': EMIT, 'event_id': plain}).json()
    assert miss == {'matches': False, 'reason': 'payload.labels is missing; it must contain "agent"',
                    'output': {'type': 'triage.requested', 'subject': 'DEMO-1', 'title': 'Triage DEMO-1',
                               'payload': {'issue_id': '1', 'changes': {}}}}
    hit = client.post('/api/event-handlers/preview', headers=HEADERS, json={'handler': EMIT, 'event_id': labelled}).json()
    assert (hit['matches'], hit['reason'], hit['output']['subject']) == (True, None, 'DEMO-9')
    broken = {**EMIT, 'name': '', 'emit': {'type': '{{ event.payload.nope }}'}}
    assert client.post('/api/event-handlers/preview', headers=HEADERS,
                       json={'handler': broken, 'event_id': labelled}).json()['output']['error'].startswith('Template error:')
    agent = {**EMIT, 'action': 'agent', 'emit': None, 'agent_id': 'x', 'prompt': 'Do {{ event.subject }}'}
    assert client.post('/api/event-handlers/preview', headers=HEADERS,
                       json={'handler': agent, 'event_id': labelled}).json()['output'] == {'prompt': 'Do DEMO-9'}
    assert error(client.post('/api/event-handlers/preview', headers=HEADERS, json={'handler': EMIT, 'event_id': 999999}), 404) == 'Event not found.'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_handlers_api.py`
Expected: FAIL (404 on `/api/event-handlers`).

- [ ] **Step 3: Write the implementation** `services/admin-api/src/knowledge_admin/event_handlers_api.py`

```python
"""Administration → Events → Handlers: create, list, edit, delete and Preview handlers. The event processor
(event_processor.py, in repository-worker) runs them; this API only changes their rows."""
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin import event_matching, event_templates
from knowledge_admin.access import request_actor
from knowledge_admin.repositories import RepositoryStore

NOT_FOUND = 'Handler not found.'
STALE = 'This handler was changed by someone else. Reload and try again.'
DUPLICATE = 'A handler with this name already exists.'
NO_EVENT = 'Event not found.'
NO_AGENT = 'Agent not found.'
PER_RUN = 'This agent gets its resources per run; handlers can only send to agents with fixed resources.'
SELECT = """SELECT h.id,h.name,h.enabled,h.type_pattern,h.conditions,h.action,h.emit,h.agent_id,a.name AS agent_name,h.prompt,
    h.revision,h.created_at,h.updated_at,(SELECT jsonb_build_object('status',r.status,'created_at',r.created_at)
        FROM ka.event_handler_runs r WHERE r.handler_id=h.id ORDER BY r.id DESC LIMIT 1) AS last_run
    FROM ka.event_handlers h LEFT JOIN ka.agents a ON a.id=h.agent_id"""


class Condition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: str = Field(max_length=200)
    op: str = Field(max_length=20)
    value: str | list[str] | None = None


class EmitTemplates(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: str = Field(default='', max_length=200)
    subject: str = Field(default='', max_length=2000)
    title: str = Field(default='', max_length=2000)
    payload: str = Field(default='', max_length=20000)


class HandlerDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(max_length=120)
    enabled: bool = True
    type_pattern: str = Field(max_length=200)
    conditions: list[Condition] = Field(default_factory=list, max_length=20)
    action: Literal['emit', 'agent']
    emit: EmitTemplates | None = None
    agent_id: str | None = Field(default=None, max_length=200)
    prompt: str | None = Field(default=None, max_length=20000)


class HandlerInput(HandlerDraft):
    revision: int = Field(ge=1, strict=True)


class PreviewDraft(HandlerDraft):
    name: str = Field(default='', max_length=120)  # Preview works before the handler has a name


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    handler: PreviewDraft
    event_id: int = Field(ge=1)


def bad(message: str) -> HTTPException:
    return HTTPException(400, message)


def stored(conn, draft: HandlerDraft, *, named: bool = True) -> dict:
    """The handler's columns, validated: 400 with a message for the form when something is wrong."""
    name = draft.name.strip()
    if named and not name:
        raise bad('Enter a name.')
    try:
        event_matching.check_pattern(draft.type_pattern.strip())
        conditions = event_matching.check_conditions([condition.model_dump() for condition in draft.conditions])
    except event_matching.ConditionError as exc:
        raise bad(str(exc)) from None
    values = {'name': name, 'enabled': draft.enabled, 'type_pattern': draft.type_pattern.strip(), 'conditions': conditions,
              'action': draft.action, 'emit': None, 'agent_id': None, 'prompt': None}
    try:
        if draft.action == 'emit':
            emit = (draft.emit or EmitTemplates()).model_dump()
            event_templates.check_emit(emit)
            values['emit'] = emit
        else:
            if not draft.agent_id:
                raise bad('Choose an agent.')
            event_templates.check_prompt(draft.prompt or '')
            values.update(agent_id=draft.agent_id, prompt=draft.prompt)
    except event_templates.TemplateProblem as exc:
        raise bad(str(exc)) from None
    return values


def build_api(app: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter()

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Event storage is not configured')
        return store

    def agent_usable(conn, agent_id: str) -> None:
        agent = conn.execute('SELECT resources FROM ka.agents WHERE id=%s', (agent_id,)).fetchone()
        if agent is None:
            raise bad(NO_AGENT)
        if agent['resources'] != 'fixed':
            raise bad(PER_RUN)

    def one(conn, project: UUID, handler_id: UUID) -> dict:
        row = conn.execute(SELECT + ' WHERE h.id=%s AND h.project_id=%s', (handler_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return row

    def audit(conn, project: UUID, request: Request, action: str, handler_id: UUID, details: dict) -> UUID:
        actor = database().actor(conn, *request_actor(request))
        conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,'event_handler',%s,%s)""", (project, actor, action, handler_id, Jsonb(details)))
        return actor

    @router.get('/event-handlers')
    def list_handlers() -> dict:
        db = database()
        with db.connect() as conn:
            rows = conn.execute(SELECT + ' WHERE h.project_id=%s ORDER BY h.name', (db.project(conn, app),)).fetchall()
        return {'handlers': rows}

    @router.get('/event-handlers/agents')
    def agents() -> dict:
        """Agents a handler can send to: enabled, with fixed resources."""
        db = database()
        with db.connect() as conn:
            return {'agents': conn.execute("SELECT id,name FROM ka.agents WHERE enabled AND resources='fixed' ORDER BY name").fetchall()}

    @router.post('/event-handlers/preview')
    def preview(value: PreviewInput) -> dict:
        db = database()
        with db.connect() as conn:
            handler = stored(conn, value.handler, named=False)
            event = conn.execute('SELECT * FROM ka.events WHERE id=%s AND project_id=%s',
                                 (value.event_id, db.project(conn, app))).fetchone()
        if event is None:
            raise HTTPException(404, NO_EVENT)
        try:
            output = (event_templates.render_emit(handler['emit'], event) if handler['action'] == 'emit'
                      else {'prompt': event_templates.render_prompt(handler['prompt'], event)})
        except event_templates.TemplateProblem as exc:
            output = {'error': str(exc)}
        reason = event_matching.explain(handler, event)
        return {'matches': reason is None, 'reason': reason, 'output': output}

    @router.post('/event-handlers', status_code=201)
    def create(value: HandlerDraft, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            values = stored(conn, value)
            if values['agent_id']:
                agent_usable(conn, values['agent_id'])
            actor = db.actor(conn, *request_actor(request))
            try:
                with conn.transaction():
                    handler_id = conn.execute("""INSERT INTO ka.event_handlers(project_id,name,enabled,type_pattern,conditions,
                        action,emit,agent_id,prompt,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (project, values['name'], values['enabled'], values['type_pattern'], Jsonb(values['conditions']),
                         values['action'], Jsonb(values['emit']) if values['emit'] else None, values['agent_id'],
                         values['prompt'], actor)).fetchone()['id']
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, DUPLICATE) from None
            audit(conn, project, request, 'event_handler.created', handler_id, {'name': values['name'], 'action': values['action']})
            return {'handler': one(conn, project, handler_id)}

    @router.put('/event-handlers/{handler_id}')
    def save(handler_id: UUID, value: HandlerInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            current = conn.execute('SELECT revision FROM ka.event_handlers WHERE id=%s AND project_id=%s FOR UPDATE',
                                   (handler_id, project)).fetchone()
            if current is None:
                raise HTTPException(404, NOT_FOUND)
            if current['revision'] != value.revision:
                raise HTTPException(409, STALE)
            values = stored(conn, value)
            if values['agent_id']:
                agent_usable(conn, values['agent_id'])
            try:
                with conn.transaction():
                    conn.execute("""UPDATE ka.event_handlers SET name=%s,enabled=%s,type_pattern=%s,conditions=%s,action=%s,
                        emit=%s,agent_id=%s,prompt=%s,revision=revision+1,updated_at=now() WHERE id=%s""",
                        (values['name'], values['enabled'], values['type_pattern'], Jsonb(values['conditions']),
                         values['action'], Jsonb(values['emit']) if values['emit'] else None, values['agent_id'],
                         values['prompt'], handler_id))
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, DUPLICATE) from None
            audit(conn, project, request, 'event_handler.updated', handler_id,
                  {'name': values['name'], 'action': values['action'], 'enabled': values['enabled']})
            return {'handler': one(conn, project, handler_id)}

    @router.delete('/event-handlers/{handler_id}', status_code=204)
    def delete(handler_id: UUID, request: Request) -> Response:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            row = conn.execute('DELETE FROM ka.event_handlers WHERE id=%s AND project_id=%s RETURNING name',
                               (handler_id, project)).fetchone()
            if row is None:
                raise HTTPException(404, NOT_FOUND)
            audit(conn, project, request, 'event_handler.deleted', handler_id, {'name': row['name']})
        return Response(status_code=204)

    return router
```

Wire it in `api.py`: add `event_handlers_api` to the `from knowledge_admin import …` line and
`router.include_router(event_handlers_api.build_api(app_name, repository_store))` after the events router (the
`/event-handlers/preview` and `/event-handlers/agents` paths are declared before the `{handler_id}` routes).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/admin-api && uv run pytest -q tests/test_event_handlers_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/event_handlers_api.py services/admin-api/src/knowledge_admin/api.py \
  services/admin-api/tests/test_event_handlers_api.py
git commit -m "feat(events): event handlers API with validation and Preview"
```

---

### Task 7: Events API — sorting, filters, offset paging, facets, chain and runs

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/events_api.py`
- Modify: `services/admin-api/tests/test_events_api.py`

**Interfaces:**
- Consumes: 0089 columns and tables.
- Produces: `GET /api/events?source&type&status&handler_id&root_id&q&sort&order&offset&limit` → `{events, next_offset}`
  (rows: `id, source, type, subject, title, occurred_at, created_at, status, depth, parent_id, root_id, handler_id`);
  `GET /api/events/facets` → `{sources, types, statuses, handlers: [{id, name}]}`; `GET /api/events/{id}` →
  `{event}` with `source_id, dedupe_key, payload, parent, chain, runs` (run: `handler_id, handler_name, status,
  message, emitted_event_id, agent_session_id, agent_id, created_at`).

- [ ] **Step 1: Write the failing tests**

In `services/admin-api/tests/test_events_api.py` replace `test_newest_first_with_cursor` and the facets assertion of
`test_filters_and_search`, and add the new tests:

```python
def test_newest_first_with_offsets(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = seed(store, 5)
    page = client.get('/api/events', headers=HEADERS, params={'limit': 2}).json()
    assert [e['id'] for e in page['events']] == ids[::-1][:2] and page['next_offset'] == 2
    rest = client.get('/api/events', headers=HEADERS, params={'limit': 10, 'offset': 2}).json()
    assert [e['id'] for e in rest['events']] == ids[::-1][2:] and rest['next_offset'] is None
    assert 'payload' not in page['events'][0]
    assert {'source', 'status', 'depth', 'parent_id', 'root_id', 'handler_id'} <= set(page['events'][0])


def test_sorting_ties_page_by_id_without_repeats(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    ids = seed(store, 5, subject='SAME-1')
    seen = []
    for offset in (0, 2, 4):
        page = client.get('/api/events', headers=HEADERS, params={'sort': 'subject', 'order': 'asc', 'limit': 2, 'offset': offset}).json()
        seen += [e['id'] for e in page['events']]
    assert seen == ids  # ties ordered by id in the same direction, no repeats, no gaps
    newest = client.get('/api/events', headers=HEADERS, params={'sort': 'subject', 'order': 'desc'}).json()['events']
    assert [e['id'] for e in newest] == ids[::-1]
    assert client.get('/api/events', headers=HEADERS, params={'sort': 'payload'}).status_code == 400
    assert client.get('/api/events', headers=HEADERS, params={'offset': -1}).status_code == 400


def test_sort_by_source_and_status_and_filters(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [root] = seed(store, 1, subject='A-1')
    handler = uuid4()
    [child] = seed(store, 1, subject='B-1', source='handler', handler_id=handler, parent_id=root, root_id=root, depth=1)
    with store.connect() as conn:
        conn.execute("UPDATE ka.events SET status='failed',processed_at=now() WHERE id=%s", (root,))
    by_source = client.get('/api/events', headers=HEADERS, params={'sort': 'source', 'order': 'asc'}).json()['events']
    assert [e['source'] for e in by_source] == ['handler', 'jira']
    by_status = client.get('/api/events', headers=HEADERS, params={'sort': 'status', 'order': 'asc'}).json()['events']
    assert [e['status'] for e in by_status] == ['failed', 'pending']
    assert [e['id'] for e in client.get('/api/events', headers=HEADERS, params={'status': 'failed'}).json()['events']] == [root]
    assert [e['id'] for e in client.get('/api/events', headers=HEADERS, params={'handler_id': str(handler)}).json()['events']] == [child]
    assert [e['id'] for e in client.get('/api/events', headers=HEADERS, params={'root_id': root}).json()['events']] == [child, root]
    assert client.get('/api/events', headers=HEADERS, params={'status': 'done'}).status_code == 400


def test_facets(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    seed(store, 1)
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        actor = store.actor(conn, 'admin-id', 'admin@example.test')
        handler = conn.execute("""INSERT INTO ka.event_handlers(project_id,name,type_pattern,action,emit,created_by)
            VALUES (%s,'Triage','*','emit',%s,%s) RETURNING id""", (project, Jsonb({'type': 'a.b'}), actor)).fetchone()['id']
    assert client.get('/api/events/facets', headers=HEADERS).json() == {
        'sources': ['jira'], 'types': ['jira.ticket.created'], 'statuses': ['pending'],
        'handlers': [{'id': str(handler), 'name': 'Triage'}]}


def test_one_event_has_its_parent_chain_and_runs(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    [root] = seed(store, 1)
    handler = uuid4()
    [child] = seed(store, 1, subject='T-1', type='triage.requested', source='handler', handler_id=handler,
                   parent_id=root, root_id=root, depth=1)
    [grandchild] = seed(store, 1, subject='T-2', type='triage.done', source='handler', handler_id=handler,
                        parent_id=child, root_id=root, depth=2)
    with store.connect() as conn:
        conn.execute("""INSERT INTO ka.event_handler_runs(event_id,handler_id,handler_name,status,message,emitted_event_id)
            VALUES (%s,%s,'Triage','succeeded','Emitted triage.done.',%s)""", (child, handler, grandchild))
    event = client.get(f'/api/events/{child}', headers=HEADERS).json()['event']
    assert event['parent']['id'] == root and 'payload' not in event['parent']
    assert [(e['id'], e['depth']) for e in event['chain']] == [(root, 0), (child, 1), (grandchild, 2)]
    assert [(r['handler_name'], r['status'], r['emitted_event_id'], r['agent_session_id'], r['agent_id']) for r in event['runs']] == [
        ('Triage', 'succeeded', grandchild, None, None)]
    assert client.get(f'/api/events/{root}', headers=HEADERS).json()['event']['parent'] is None
```

In `test_filters_and_search` replace the facets line with:

```python
    assert client.get('/api/events/facets', headers=HEADERS).json() == {
        'sources': ['jira'], 'types': ['jira.ticket.created', 'jira.ticket.updated'], 'statuses': ['pending'], 'handlers': []}
```

(`Jsonb` and `uuid4` are already imported in that file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_events_api.py`
Expected: FAIL (`next_offset` missing, unknown params ignored, facets shape).

- [ ] **Step 3: Write the implementation** — replace `services/admin-api/src/knowledge_admin/events_api.py` with:

```python
"""Administration → Events: things that happened at a source (the scheduled Jira pull) or that a handler emitted.
Read-only here; handlers (event_handlers_api.py) and the event processor act on them."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from knowledge_admin.repositories import RepositoryStore

LISTED = 'id,source,type,subject,title,occurred_at,created_at,status,depth,parent_id,root_id,handler_id'
PAGE, MAX_PAGE, MAX_OFFSET, MAX_CHAIN = 50, 200, 100000, 200
NOT_FOUND = 'Event not found.'
SORTS = {'time': 'occurred_at', 'source': 'source', 'type': 'type', 'subject': 'subject', 'status': 'status'}


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
                    status: Literal['pending', 'processed', 'failed'] | None = None, handler_id: UUID | None = None,
                    root_id: int | None = Query(None, ge=1), q: str | None = Query(None, max_length=200),
                    sort: Literal['time', 'source', 'type', 'subject', 'status'] = 'time',
                    order: Literal['asc', 'desc'] = 'desc', offset: int = Query(0, ge=0, le=MAX_OFFSET),
                    limit: int = Query(PAGE, ge=1, le=MAX_PAGE)) -> dict:
        db = database()
        with db.connect() as conn:
            where, values = ['project_id=%s'], [db.project(conn, app)]
            for column, value in (('source', source), ('type', type), ('status', status), ('handler_id', handler_id),
                                  ('root_id', root_id)):
                if value:
                    where.append(f'{column}=%s'); values.append(value)
            if q and q.strip():
                where.append("(subject ILIKE %s ESCAPE '\\' OR title ILIKE %s ESCAPE '\\')"); values += [like(q.strip())] * 2
            direction = 'ASC' if order == 'asc' else 'DESC'
            rows = conn.execute(f"""SELECT {LISTED} FROM ka.events WHERE {' AND '.join(where)}
                ORDER BY {SORTS[sort]} {direction},id {direction} LIMIT %s OFFSET %s""", (*values, limit + 1, offset)).fetchall()
        more = len(rows) > limit
        return {'events': rows[:limit], 'next_offset': offset + limit if more else None}

    @router.get('/events/facets')
    def facets() -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            distinct = lambda column: [row[column] for row in conn.execute(
                f'SELECT DISTINCT {column} FROM ka.events WHERE project_id=%s ORDER BY 1', (project,))]
            handlers = conn.execute('SELECT id,name FROM ka.event_handlers WHERE project_id=%s ORDER BY name', (project,)).fetchall()
            return {'sources': distinct('source'), 'types': distinct('type'), 'statuses': distinct('status'), 'handlers': handlers}

    @router.get('/events/{event_id}')
    def one(event_id: int) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app)
            row = conn.execute(f'SELECT {LISTED},source_id,dedupe_key,payload FROM ka.events WHERE id=%s AND project_id=%s',
                               (event_id, project)).fetchone()
            if row is None:
                raise HTTPException(404, NOT_FOUND)
            row['parent'] = None if row['parent_id'] is None else conn.execute(
                f'SELECT {LISTED} FROM ka.events WHERE id=%s AND project_id=%s', (row['parent_id'], project)).fetchone()
            row['chain'] = conn.execute(f'SELECT {LISTED} FROM ka.events WHERE project_id=%s AND root_id=%s ORDER BY depth,id LIMIT %s',
                                        (project, row['root_id'], MAX_CHAIN)).fetchall()
            row['runs'] = conn.execute("""SELECT r.handler_id,r.handler_name,r.status,r.message,r.emitted_event_id,
                r.agent_session_id,s.agent_id,r.created_at FROM ka.event_handler_runs r
                LEFT JOIN ka.agent_sessions s ON s.id=r.agent_session_id WHERE r.event_id=%s ORDER BY r.id""", (event_id,)).fetchall()
        return {'event': row}

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_events_api.py tests/test_event_handlers_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/events_api.py services/admin-api/tests/test_events_api.py
git commit -m "feat(events): sortable, filterable events with offsets, chain and handler runs"
```

---

### Task 8: UI — Events tab: Source and Status columns, sorting, filters, chain and runs

**Files:**
- Modify: `services/admin-ui/app/lib/events.ts`
- Modify: `services/admin-ui/app/components/EventsView.tsx`
- Modify: `services/admin-ui/app/components/__tests__/events.test.tsx`

**Interfaces:**
- Consumes: Task 7 API.
- Produces (lib): `EventStatus`, `EventRow` (+`status, depth, parent_id, root_id, handler_id`), `HandlerRun`,
  `EventDetail` (+`parent, chain, runs`), `EventFilters {source, type, status, handler, q}`, `SortKey`,
  `EventSort {key, order}`, `DEFAULT_SORT`, `EventFacets`, `getEvents(filters, sort, offset?, signal?)`,
  `getEventFacets`, `getEvent`, `STATUS_LABELS`, `SOURCE_LABELS` (+`handler`), `TYPE_LABELS`, `describeChanges`.
  Query parameter order: `source, type, status, handler_id, q, sort, order, offset`; `sort`/`order` only when not the
  default (`time`/`desc`), `offset` only when > 0.

- [ ] **Step 1: Write the failing test** — replace `services/admin-ui/app/components/__tests__/events.test.tsx` with:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import EventsView from "../EventsView";
import { describeChanges } from "../../lib/events";

const row = (id: number, extra = {}) => ({ id, source: "jira", type: "jira.ticket.created", subject: `DEMO-${id}`, title: `Ticket ${id}`,
  occurred_at: "2026-10-09T10:00:00Z", created_at: "2026-10-09T10:00:05Z", status: "processed", depth: 0, parent_id: null,
  root_id: id, handler_id: null, ...extra });
const FACETS = { sources: ["handler", "jira"], types: ["jira.ticket.created", "jira.ticket.updated"], statuses: ["failed", "processed"],
  handlers: [{ id: "h-1", name: "Triage" }] };
const detail = (id: number, extra = {}) => ({ ...row(id), source_id: "c-1", dedupe_key: "k", payload: {}, parent: null, chain: [row(id)], runs: [], ...extra });

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

test("lists events with source and status and loads more by offset", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": FACETS,
    "/api/events": { events: [row(3, { source: "handler", status: "failed" }), row(2)], next_offset: 2 },
    "/api/events?offset=2": { events: [row(1)], next_offset: null } });
  view();
  expect(await screen.findByText("DEMO-3")).toBeInTheDocument();
  const table = screen.getByRole("table");
  expect(within(table).getByText("Handler")).toBeInTheDocument();
  expect(within(table).getByText("Failed")).toBeInTheDocument();
  expect(within(table).getAllByText("Processed")).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText("DEMO-1")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
});

test("a column header sorts, and clicking it again flips the order", async () => {
  const user = userEvent.setup();
  const fetch = api({ "/api/events/facets": FACETS, "/api/events": { events: [row(2)], next_offset: null },
    "/api/events?sort=subject&order=desc": { events: [row(5)], next_offset: null },
    "/api/events?sort=subject&order=asc": { events: [row(4)], next_offset: null },
    "/api/events?order=asc": { events: [row(1)], next_offset: null } });
  view();
  await screen.findByText("DEMO-2");
  await user.click(screen.getByRole("button", { name: "Subject" }));
  expect(await screen.findByText("DEMO-5")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /Subject/ })).toHaveAttribute("aria-sort", "descending");
  await user.click(screen.getByRole("button", { name: "Subject" }));
  expect(await screen.findByText("DEMO-4")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /Subject/ })).toHaveAttribute("aria-sort", "ascending");
  await user.click(screen.getByRole("button", { name: "Time" }));
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === "/api/events")).toBe(true));
  await user.click(screen.getByRole("button", { name: "Time" }));
  expect(await screen.findByText("DEMO-1")).toBeInTheDocument();
});

test("filters by type, status, handler and search", async () => {
  const user = userEvent.setup();
  const fetch = api({ "/api/events/facets": FACETS, "/api/events": { events: [row(2)], next_offset: null },
    "/api/events?type=jira.ticket.updated": { events: [row(9, { type: "jira.ticket.updated" })], next_offset: null },
    "/api/events?type=jira.ticket.updated&status=failed": { events: [row(8)], next_offset: null },
    "/api/events?type=jira.ticket.updated&status=failed&handler_id=h-1": { events: [row(7)], next_offset: null },
    "/api/events?type=jira.ticket.updated&status=failed&handler_id=h-1&q=DEMO-7": { events: [row(7)], next_offset: null } });
  view();
  await screen.findByText("DEMO-2");
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  await user.click(screen.getByRole("option", { name: "Ticket updated" }));
  expect(await screen.findByText("DEMO-9")).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Status" }));
  await user.click(screen.getByRole("option", { name: "Failed" }));
  expect(await screen.findByText("DEMO-8")).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "Handler" }));
  await user.click(screen.getByRole("option", { name: "Triage" }));
  expect(await screen.findByText("DEMO-7")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Ticket or title"), "DEMO-7");
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === "/api/events?type=jira.ticket.updated&status=failed&handler_id=h-1&q=DEMO-7")).toBe(true));
});

test("an event opens with its changes and a Jira link", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(7, { type: "jira.ticket.updated" })], next_offset: null },
    "/api/events/7": { event: detail(7, { type: "jira.ticket.updated",
      payload: { url: "https://jira.internal/jira/browse/DEMO-7", changes: { status: { from: "Open", to: "Done" }, labels: { added: ["agent"], removed: [] } } } }) } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText("Status: Open → Done")).toBeInTheDocument();
  expect(within(sheet).getByText("Labels: +agent")).toBeInTheDocument();
  expect(within(sheet).getByRole("link", { name: "Open in Jira" })).toHaveAttribute("href", "https://jira.internal/jira/browse/DEMO-7");
});

test("the sheet shows the chain and the handler runs with their links", async () => {
  const user = userEvent.setup();
  const child = row(8, { source: "handler", type: "triage.requested", subject: "T-8", depth: 1, parent_id: 7, root_id: 7, handler_id: "h-1" });
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(7)], next_offset: null },
    "/api/events/7": { event: detail(7, { chain: [row(7), child], runs: [
      { handler_id: "h-1", handler_name: "Triage", status: "succeeded", message: "Emitted triage.requested.", emitted_event_id: 8, agent_session_id: null, agent_id: null, created_at: "2026-10-09T10:00:06Z" },
      { handler_id: "h-2", handler_name: "Ask agent", status: "succeeded", message: "Started a session with Tester.", emitted_event_id: null, agent_session_id: "s-1", agent_id: "tester", created_at: "2026-10-09T10:00:06Z" },
      { handler_id: "h-3", handler_name: "Broken", status: "failed", message: "Template error: x", emitted_event_id: null, agent_session_id: null, agent_id: null, created_at: "2026-10-09T10:00:06Z" }] }) },
    "/api/events/8": { event: detail(8, { ...child, chain: [row(7), child], parent: row(7) }) } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  const chain = within(sheet).getByRole("list", { name: "Chain" });
  expect(within(chain).getByText(/DEMO-7/).closest("[aria-current]")).toHaveAttribute("aria-current", "true");
  expect(within(sheet).getByText("Template error: x")).toBeInTheDocument();
  expect(within(sheet).getByRole("link", { name: "Open agent session" })).toHaveAttribute("href", "/agents/tester?view=test&session_id=s-1");
  await user.click(within(sheet).getByRole("button", { name: "Open event #8" }));
  expect(await screen.findByRole("heading", { name: "T-8 · triage.requested" })).toBeInTheDocument();
  expect(within(screen.getByRole("dialog")).getByText("Parent")).toBeInTheDocument();
});

test("empty state", async () => {
  api({ "/api/events/facets": { sources: [], types: [], statuses: [], handlers: [] }, "/api/events": { events: [], next_offset: null } });
  view();
  expect(await screen.findByText("No events yet. Turn on Jira → Auto pull to record ticket changes.")).toBeInTheDocument();
});

test("describeChanges", () => {
  expect(describeChanges({ assignee: { from: null, to: "Ana" }, description: { changed: true }, labels: { added: ["a"], removed: ["b"] } }))
    .toEqual(["Assignee: — → Ana", "Description changed", "Labels: +a −b"]);
  expect(describeChanges(undefined)).toEqual([]);
  expect(describeChanges({ a: null, b: "x", labels: { added: "z" } })).toEqual([]);
});

test("a Load more answered after a filter change is ignored", async () => {
  const user = userEvent.setup();
  let release: (response: Response) => void = () => {};
  const late = new Promise<Response>(resolve => { release = resolve; });
  const pages: Record<string, unknown> = { "/api/events/facets": FACETS, "/api/events": { events: [row(3)], next_offset: 1 },
    "/api/events?type=jira.ticket.updated": { events: [row(9, { type: "jira.ticket.updated" })], next_offset: null } };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/events?offset=1") return late;
    if (pages[url] === undefined) throw new Error(`unexpected ${url}`);
    return new Response(JSON.stringify(pages[url]));
  }));
  view();
  await screen.findByText("DEMO-3");
  await user.click(screen.getByRole("button", { name: "Load more" }));
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  await user.click(screen.getByRole("option", { name: "Ticket updated" }));
  expect(await screen.findByText("DEMO-9")).toBeInTheDocument();
  release(new Response(JSON.stringify({ events: [row(1)], next_offset: 2 })));
  await new Promise(resolve => setTimeout(resolve, 20));
  expect(screen.queryByText("DEMO-1")).toBeNull();
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
});

test("a non-http payload url gets no Jira link", async () => {
  const user = userEvent.setup();
  api({ "/api/events/facets": FACETS, "/api/events": { events: [row(7)], next_offset: null },
    "/api/events/7": { event: detail(7, { payload: { url: "javascript:alert(1)" } }) } });
  view();
  await user.click(await screen.findByText("DEMO-7"));
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).queryByRole("link", { name: "Open in Jira" })).toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/events.test.tsx`
Expected: FAIL (no Source/Status columns, `?before=` URLs, no sort buttons).

- [ ] **Step 3: Implement**

Replace `services/admin-ui/app/lib/events.ts` with:

```ts
import { request } from "./api";

export type EventStatus = "pending" | "processed" | "failed";
export interface EventRow { id: number; source: string; type: string; subject: string; title: string; occurred_at: string; created_at: string;
  status: EventStatus; depth: number; parent_id: number | null; root_id: number; handler_id: string | null }
export interface HandlerRun { handler_id: string; handler_name: string; status: "succeeded" | "failed"; message: string;
  emitted_event_id: number | null; agent_session_id: string | null; agent_id: string | null; created_at: string }
export interface EventDetail extends EventRow { source_id: string | null; dedupe_key: string; payload: Record<string, unknown>;
  parent: EventRow | null; chain: EventRow[]; runs: HandlerRun[] }
export interface EventFilters { source: string; type: string; status: string; handler: string; q: string }
export type SortKey = "time" | "source" | "type" | "subject" | "status";
export interface EventSort { key: SortKey; order: "asc" | "desc" }
export interface EventFacets { sources: string[]; types: string[]; statuses: EventStatus[]; handlers: { id: string; name: string }[] }
export type FieldChange = { from: unknown; to: unknown } | { added: string[]; removed: string[] } | { changed: true };

export const DEFAULT_SORT: EventSort = { key: "time", order: "desc" };
export const NO_FILTERS: EventFilters = { source: "", type: "", status: "", handler: "", q: "" };
export const TYPE_LABELS: Record<string, string> = { "jira.ticket.created": "Ticket created", "jira.ticket.updated": "Ticket updated" };
export const SOURCE_LABELS: Record<string, string> = { jira: "Jira", handler: "Handler" };
export const STATUS_LABELS: Record<EventStatus, string> = { pending: "Pending", processed: "Processed", failed: "Failed" };
const FIELD_LABELS: Record<string, string> = { summary: "Summary", status: "Status", issue_type: "Type", priority: "Priority", assignee: "Assignee", labels: "Labels", description: "Description" };

export function getEvents(filters: EventFilters, sort: EventSort = DEFAULT_SORT, offset = 0, signal?: AbortSignal) {
  const query = new URLSearchParams();
  if (filters.source) query.set("source", filters.source);
  if (filters.type) query.set("type", filters.type);
  if (filters.status) query.set("status", filters.status);
  if (filters.handler) query.set("handler_id", filters.handler);
  if (filters.q.trim()) query.set("q", filters.q.trim());
  if (sort.key !== DEFAULT_SORT.key) query.set("sort", sort.key);
  if (sort.key !== DEFAULT_SORT.key || sort.order !== DEFAULT_SORT.order) query.set("order", sort.order);
  if (offset > 0) query.set("offset", String(offset));
  const suffix = query.toString();
  return request<{ events: EventRow[]; next_offset: number | null }>(`/api/events${suffix ? `?${suffix}` : ""}`, { signal });
}
export const getEventFacets = (signal?: AbortSignal) => request<EventFacets>("/api/events/facets", { signal });
export const getEvent = (id: number, signal?: AbortSignal) => request<{ event: EventDetail }>(`/api/events/${id}`, { signal });

/** One readable line per change, e.g. "Status: Open → In Progress", "Labels: +agent −old", "Description changed". */
export function describeChanges(changes: unknown): string[] {
  if (!changes || typeof changes !== "object") return [];
  const show = (value: unknown) => value === null || value === undefined || value === "" ? "—" : String(value);
  return Object.entries(changes as Record<string, unknown>).flatMap(([field, value]) => {
    if (!value || typeof value !== "object") return [];
    const change = value as Record<string, unknown>;
    const label = FIELD_LABELS[field] ?? field;
    if ("added" in change || "removed" in change) {
      const added = Array.isArray(change.added) ? (change.added as unknown[]) : [];
      const removed = Array.isArray(change.removed) ? (change.removed as unknown[]) : [];
      if (added.length === 0 && removed.length === 0) return [];
      return [`${label}: ${[...added.map(item => `+${item}`), ...removed.map(item => `−${item}`)].join(" ")}`];
    }
    if ("changed" in change) return [`${label} changed`];
    return [`${label}: ${show(change.from)} → ${show(change.to)}`];
  });
}
```

(Note: with the default sort key and a non-default order the URL is `?order=asc` — the test's Time flip relies on it.)

Replace `services/admin-ui/app/components/EventsView.tsx` with:

```tsx
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ArrowDown, ArrowUp, ArrowUpDown, Inbox, RefreshCw } from "lucide-react";
import { ApiError } from "../lib/api";
import { DEFAULT_SORT, NO_FILTERS, SOURCE_LABELS, STATUS_LABELS, TYPE_LABELS, describeChanges, getEvent, getEventFacets, getEvents,
  type EventDetail, type EventFacets, type EventFilters, type EventRow, type EventSort, type EventStatus, type SortKey } from "../lib/events";
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
const typeLabel = (type: string) => TYPE_LABELS[type] ?? type;
const sourceLabel = (source: string) => SOURCE_LABELS[source] ?? source;
const COLUMNS: { key: SortKey | null; label: string }[] = [
  { key: "time", label: "Time" }, { key: "source", label: "Source" }, { key: "type", label: "Type" },
  { key: "subject", label: "Subject" }, { key: null, label: "Title" }, { key: "status", label: "Status" }];

export function StatusBadge({ status }: { status: EventStatus }) {
  return <Badge variant={status === "failed" ? "destructive" : status === "pending" ? "outline" : "secondary"}>{STATUS_LABELS[status] ?? status}</Badge>;
}

function FilterSelect({ id, label, value, all, options, onChange, className }: { id: string; label: string; value: string; all: string;
  options: { value: string; label: string }[]; onChange: (value: string) => void; className: string }) {
  return <Field className={className}><FieldLabel htmlFor={id}>{label}</FieldLabel>
    <Select value={value || ALL} onValueChange={next => onChange(next === ALL ? "" : next)}>
      <SelectTrigger id={id} aria-label={label} className="w-full"><SelectValue /></SelectTrigger>
      <SelectContent><SelectGroup><SelectItem value={ALL}>{all}</SelectItem>
        {options.map(option => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectGroup></SelectContent>
    </Select></Field>;
}

export default function EventsView() {
  const [filters, setFilters] = useState<EventFilters>(NO_FILTERS);
  const [sort, setSort] = useState<EventSort>(DEFAULT_SORT);
  const [query, setQuery] = useState("");
  const [events, setEvents] = useState<EventRow[] | null>(null);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [facets, setFacets] = useState<EventFacets>({ sources: [], types: [], statuses: [], handlers: [] });
  const [selected, setSelected] = useState<EventDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);
  /** Bumped whenever the first page is reloaded, so a Load more that started earlier is ignored. */
  const generation = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(filters.q), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [filters.q]);

  const active: EventFilters = { ...filters, q: query };

  useEffect(() => {
    const controller = new AbortController();
    getEventFacets(controller.signal).then(setFacets).catch(reason => { if (!controller.signal.aborted) setError(text(reason, "Could not load the filters.")); });
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    generation.current += 1;
    setEvents(null); setNextOffset(null); setLoadingMore(false);
    getEvents(active, sort, 0, controller.signal)
      .then(result => { setEvents(result.events); setNextOffset(result.next_offset); setError(null); })
      .catch(reason => { if (!controller.signal.aborted) { setEvents([]); setNextOffset(null); setError(text(reason, "Could not load events.")); } });
    return () => controller.abort();
  }, [filters.source, filters.type, filters.status, filters.handler, query, sort.key, sort.order, reload]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function loadMore() {
    if (nextOffset === null) return;
    const started = generation.current;
    setLoadingMore(true);
    try {
      const result = await getEvents(active, sort, nextOffset);
      if (generation.current !== started) return;
      setEvents(current => current ? [...current, ...result.events] : current);
      setNextOffset(result.next_offset);
    } catch (reason) { if (generation.current === started) setError(text(reason, "Could not load more events.")); }
    finally { if (generation.current === started) setLoadingMore(false); }
  }
  async function open(id: number) {
    try { setSelected((await getEvent(id)).event); }
    catch (reason) { setError(text(reason, "Could not load the event.")); }
  }
  /** A new column sorts newest/last first; the same column again flips the order. */
  const sortBy = (key: SortKey) => setSort(current => current.key === key ? { key, order: current.order === "desc" ? "asc" : "desc" } : { key, order: "desc" });
  const set = (field: keyof EventFilters) => (value: string) => setFilters(current => ({ ...current, [field]: value }));

  const filtered = Boolean(filters.source || filters.type || filters.status || filters.handler || filters.q.trim());
  const changes = selected ? describeChanges(selected.payload.changes) : [];
  const url = selected && typeof selected.payload.url === "string" && /^https?:\/\//i.test(selected.payload.url) ? selected.payload.url : null;

  return <div className="flex flex-col gap-4">
    <div className="flex flex-wrap items-end gap-3">
      <FilterSelect id="events-source" label="Source" all="All sources" className="w-full sm:w-40" value={filters.source} onChange={set("source")}
        options={facets.sources.map(source => ({ value: source, label: sourceLabel(source) }))} />
      <FilterSelect id="events-type" label="Type" all="All types" className="w-full sm:w-52" value={filters.type} onChange={set("type")}
        options={facets.types.map(type => ({ value: type, label: typeLabel(type) }))} />
      <FilterSelect id="events-status" label="Status" all="All statuses" className="w-full sm:w-40" value={filters.status} onChange={set("status")}
        options={facets.statuses.map(status => ({ value: status, label: STATUS_LABELS[status] ?? status }))} />
      <FilterSelect id="events-handler" label="Handler" all="All handlers" className="w-full sm:w-44" value={filters.handler} onChange={set("handler")}
        options={facets.handlers.map(handler => ({ value: handler.id, label: handler.name }))} />
      <Field className="w-full sm:w-56"><FieldLabel htmlFor="events-search">Ticket or title</FieldLabel>
        <Input id="events-search" value={filters.q} onChange={event => set("q")(event.target.value)} /></Field>
      <Button variant="outline" onClick={() => setReload(count => count + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
    </div>
    {error && <Alert variant="destructive"><AlertTitle>Events</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {events === null && !error && <Skeleton className="h-32 w-full" />}
    {events?.length === 0 && !error && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Inbox /></EmptyMedia><EmptyTitle>Events</EmptyTitle>
      <EmptyDescription>{filtered ? "No events match these filters." : "No events yet. Turn on Jira → Auto pull to record ticket changes."}</EmptyDescription></EmptyHeader></Empty>}
    {events && events.length > 0 && <div className="overflow-x-auto rounded-md border">
      <Table>
        <TableHeader><TableRow>{COLUMNS.map(column => {
          const sorted = column.key !== null && sort.key === column.key;
          const Arrow = !sorted ? ArrowUpDown : sort.order === "asc" ? ArrowUp : ArrowDown;
          return <TableHead key={column.label} aria-sort={sorted ? (sort.order === "asc" ? "ascending" : "descending") : undefined}>
            {column.key === null ? column.label
              : <Button variant="ghost" size="sm" className="-ml-2" onClick={() => sortBy(column.key as SortKey)}>{column.label}<Arrow data-icon="inline-end" aria-hidden="true" /></Button>}
          </TableHead>;
        })}</TableRow></TableHeader>
        <TableBody>{events.map(row => <TableRow key={row.id} tabIndex={0} className="cursor-pointer" onClick={() => void open(row.id)}
          onKeyDown={event => { if (event.key === "Enter") void open(row.id); }}>
          <TableCell className="whitespace-nowrap">{when(row.occurred_at)}</TableCell>
          <TableCell className="whitespace-nowrap">{sourceLabel(row.source)}</TableCell>
          <TableCell className="whitespace-nowrap">{typeLabel(row.type)}</TableCell>
          <TableCell className="font-medium">{row.subject}</TableCell>
          <TableCell>{row.title}</TableCell>
          <TableCell><StatusBadge status={row.status} /></TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </div>}
    {nextOffset !== null && <div className="flex justify-center"><Button variant="outline" disabled={loadingMore} onClick={() => void loadMore()}>Load more</Button></div>}
    <Sheet open={selected !== null} onOpenChange={next => { if (!next) setSelected(null); }}>
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
        {selected && <>
          <SheetHeader><SheetTitle>{selected.subject} · {typeLabel(selected.type)}</SheetTitle><SheetDescription>{selected.title}</SheetDescription></SheetHeader>
          <div className="flex flex-col gap-4 px-4 pb-4 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
              <dt className="text-muted-foreground">Happened</dt><dd>{when(selected.occurred_at)}</dd>
              <dt className="text-muted-foreground">Recorded</dt><dd>{when(selected.created_at)}</dd>
              <dt className="text-muted-foreground">Source</dt><dd>{sourceLabel(selected.source)}</dd>
              <dt className="text-muted-foreground">Status</dt><dd><StatusBadge status={selected.status} /></dd>
              {selected.parent && <><dt className="text-muted-foreground">Parent</dt>
                <dd><Button variant="link" className="h-auto p-0" onClick={() => void open(selected.parent!.id)}>{selected.parent.subject} · {typeLabel(selected.parent.type)}</Button></dd></>}
            </dl>
            {url && <a href={url} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-4">Open in Jira</a>}
            {selected.chain.length > 1 && <div><h3 className="mb-1 font-medium">Chain</h3>
              <ul aria-label="Chain" className="flex flex-col gap-1">{selected.chain.map(node => <li key={node.id} style={{ paddingLeft: `${node.depth}rem` }}>
                {node.id === selected.id
                  ? <span aria-current="true" className="font-medium">{node.subject} · {typeLabel(node.type)}</span>
                  : <Button variant="link" className="h-auto p-0" onClick={() => void open(node.id)}>{node.subject} · {typeLabel(node.type)}</Button>}
                {" "}<StatusBadge status={node.status} /></li>)}</ul></div>}
            {selected.runs.length > 0 && <div><h3 className="mb-1 font-medium">Handler runs</h3>
              <ul className="flex flex-col gap-2">{selected.runs.map((run, index) => <li key={index} className="flex flex-col gap-1">
                <div className="flex items-center gap-2"><span className="font-medium">{run.handler_name}</span>
                  <Badge variant={run.status === "failed" ? "destructive" : "secondary"}>{run.status === "failed" ? "Failed" : "Succeeded"}</Badge></div>
                {run.message && <span className="text-muted-foreground">{run.message}</span>}
                {run.emitted_event_id !== null && <Button variant="link" className="h-auto self-start p-0" onClick={() => void open(run.emitted_event_id!)}>Open event #{run.emitted_event_id}</Button>}
                {run.agent_session_id && run.agent_id && <Link className="text-primary underline underline-offset-4"
                  to={`/agents/${encodeURIComponent(run.agent_id)}?view=test&session_id=${encodeURIComponent(run.agent_session_id)}`}>Open agent session</Link>}
              </li>)}</ul></div>}
            {changes.length > 0 && <div><h3 className="mb-1 font-medium">Changes</h3><ul className="flex flex-col gap-1">{changes.map(line => <li key={line}>{line}</li>)}</ul></div>}
            <details><summary className="cursor-pointer">Raw payload</summary>
              <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(selected.payload, null, 2)}</pre></details>
          </div>
        </>}
      </SheetContent>
    </Sheet>
  </div>;
}
```

(The chain list is shown when the chain has more than one event; the test's event 7 has a two-event chain.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/events.test.tsx && npx react-router typegen && npx tsc --noEmit`
Expected: PASS, exit 0.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/events.ts services/admin-ui/app/components/EventsView.tsx \
  services/admin-ui/app/components/__tests__/events.test.tsx
git commit -m "feat(admin-ui): events show source and status, sort, filter, chain and handler runs"
```

---

### Task 9: UI — Handlers tab, handler form with Preview, Events page tabs

**Files:**
- Create: `services/admin-ui/app/lib/eventHandlers.ts`
- Create: `services/admin-ui/app/components/HandlersView.tsx`, `HandlerSheet.tsx`, `EventsPage.tsx`
- Create: `services/admin-ui/app/routes/eventHandlers.tsx`
- Modify: `services/admin-ui/app/routes/events.tsx`, `services/admin-ui/app/routes.ts`, `services/admin-ui/app/components/AdminLayout.tsx`
- Create: `services/admin-ui/app/components/__tests__/handlers.test.tsx`
- Modify: `services/admin-ui/app/components/__tests__/adminLayout.test.tsx` (Events link active on `/events/handlers`)

**Interfaces:**
- Consumes: Task 6 API; `getEvents`, `getEventFacets`, `NO_FILTERS`, `DEFAULT_SORT`, `TYPE_LABELS` (Task 8).
- Produces (lib): `Operator`, `Condition`, `EmitTemplates`, `HandlerDraft`, `EventHandler`, `PreviewResult`,
  `OPERATOR_LABELS`, `FIELD_SUGGESTIONS`, `getHandlers()`, `createHandler(draft)`, `saveHandler(id, draft & {revision})`,
  `deleteHandler(id)`, `previewHandler(draft, eventId)`, `getHandlerAgents()`, `toDraft(handler)`.

- [ ] **Step 1: Write the failing test** `services/admin-ui/app/components/__tests__/handlers.test.tsx`

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import HandlersView from "../HandlersView";

const handler = (extra = {}) => ({ id: "h-1", name: "Triage", enabled: true, type_pattern: "jira.ticket.*",
  conditions: [{ field: "payload.labels", op: "contains", value: "agent" }], action: "emit",
  emit: { type: "triage.requested", subject: "", title: "", payload: "" }, agent_id: null, agent_name: null, prompt: null,
  revision: 1, created_at: "2026-10-09T10:00:00Z", updated_at: "2026-10-09T10:00:00Z", last_run: null, ...extra });
const EVENTS = { events: [{ id: 7, source: "jira", type: "jira.ticket.updated", subject: "DEMO-7", title: "Fix login",
  occurred_at: "2026-10-09T10:00:00Z", created_at: "2026-10-09T10:00:00Z", status: "processed", depth: 0, parent_id: null,
  root_id: 7, handler_id: null }], next_offset: null };

type Route = (body: unknown) => { status?: number; body?: unknown };
function api(routes: Record<string, Route | unknown>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const route = routes[key];
    if (route === undefined) throw new Error(`unexpected ${key}`);
    const answer = typeof route === "function" ? (route as Route)(init?.body ? JSON.parse(String(init.body)) : undefined) : { body: route };
    return new Response(answer.status === 204 ? null : JSON.stringify(answer.body), { status: answer.status ?? 200 });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const view = () => render(<MemoryRouter><HandlersView /></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());
const SHEET_DATA = { "GET /api/event-handlers/agents": { agents: [{ id: "tester", name: "Tester" }] },
  "GET /api/events/facets": { sources: ["jira"], types: ["jira.ticket.updated"], statuses: [], handlers: [] }, "GET /api/events": EVENTS };

test("lists handlers with what they do", async () => {
  api({ "GET /api/event-handlers": { handlers: [handler(), handler({ id: "h-2", name: "Ask", action: "agent", emit: null, agent_id: "tester",
    agent_name: "Tester", prompt: "Do it", last_run: { status: "failed", created_at: "2026-10-09T10:00:00Z" } })] } });
  view();
  expect(await screen.findByText("Triage")).toBeInTheDocument();
  expect(screen.getByText("Emit triage.requested")).toBeInTheDocument();
  expect(screen.getByText("Send to Tester")).toBeInTheDocument();
  expect(screen.getByText("jira.ticket.* · 1 condition")).toBeInTheDocument();
  expect(screen.getByText("Failed")).toBeInTheDocument();
});

test("empty state", async () => {
  api({ "GET /api/event-handlers": { handlers: [] } });
  view();
  expect(await screen.findByText("No handlers yet. Create one to turn events into new events or agent sessions.")).toBeInTheDocument();
});

test("creating an emit handler with a condition sends the draft", async () => {
  const user = userEvent.setup();
  let sent: unknown;
  api({ "GET /api/event-handlers": { handlers: [] }, ...SHEET_DATA,
    "POST /api/event-handlers": body => { sent = body; return { status: 201, body: { handler: handler() } }; } });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  await user.type(within(sheet).getByLabelText("Name"), "Triage");
  await user.click(within(sheet).getByRole("button", { name: "Add condition" }));
  await user.type(within(sheet).getByLabelText("Condition 1 field"), "payload.status");
  await user.click(within(sheet).getByRole("combobox", { name: "Condition 1 operator" }));
  await user.click(screen.getByRole("option", { name: "is one of" }));
  await user.type(within(sheet).getByLabelText("Condition 1 value"), "Open, In Progress");
  await user.type(within(sheet).getByLabelText("New event type"), "triage.requested");
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(sent).toEqual({ name: "Triage", enabled: true, type_pattern: "jira.ticket.*",
    conditions: [{ field: "payload.status", op: "in", value: ["Open", "In Progress"] }], action: "emit",
    emit: { type: "triage.requested", subject: "", title: "", payload: "" }, agent_id: null, prompt: null }));
  expect(await screen.findByText("Emit triage.requested")).toBeInTheDocument();
});

test("send to agent picks an agent and a prompt; server errors show in the form", async () => {
  const user = userEvent.setup();
  let sent: Record<string, unknown> = {};
  api({ "GET /api/event-handlers": { handlers: [] }, ...SHEET_DATA,
    "POST /api/event-handlers": body => { sent = body as Record<string, unknown>; return { status: 400, body: { error: { message: "Prompt: unexpected end of template (line 1)." } } }; } });
  view();
  await user.click(await screen.findByRole("button", { name: "New handler" }));
  const sheet = await screen.findByRole("dialog");
  await user.type(within(sheet).getByLabelText("Name"), "Ask");
  await user.click(within(sheet).getByRole("combobox", { name: "Action" }));
  await user.click(screen.getByRole("option", { name: "Send to agent" }));
  await user.click(within(sheet).getByRole("combobox", { name: "Agent" }));
  await user.click(screen.getByRole("option", { name: "Tester" }));
  await user.click(within(sheet).getByLabelText("Prompt"));
  await user.paste("Triage {{ event.subject");
  await user.click(within(sheet).getByRole("button", { name: "Save" }));
  expect(await within(sheet).findByText("Prompt: unexpected end of template (line 1).")).toBeInTheDocument();
  expect([sent.action, sent.agent_id, sent.prompt, sent.emit]).toEqual(["agent", "tester", "Triage {{ event.subject", null]);
});

test("preview shows why an event does not match and the rendered output", async () => {
  const user = userEvent.setup();
  let asked: Record<string, unknown> = {};
  api({ "GET /api/event-handlers": { handlers: [handler()] }, ...SHEET_DATA,
    "POST /api/event-handlers/preview": body => { asked = body as Record<string, unknown>; return { body: { matches: false,
      reason: 'payload.labels is missing; it must contain "agent"', output: { type: "triage.requested", subject: "DEMO-7", title: "Fix login", payload: {} } } }; } });
  view();
  await user.click(await screen.findByRole("button", { name: "Edit Triage" }));
  const sheet = await screen.findByRole("dialog");
  await user.click(within(sheet).getByRole("combobox", { name: "Preview event" }));
  await user.click(screen.getByRole("option", { name: /DEMO-7/ }));
  await user.click(within(sheet).getByRole("button", { name: "Preview" }));
  expect(await within(sheet).findByText('Doesn’t match: payload.labels is missing; it must contain "agent"')).toBeInTheDocument();
  expect(within(sheet).getByText(/"subject": "DEMO-7"/)).toBeInTheDocument();
  expect(asked.event_id).toBe(7);
});

test("the switch saves enabled with the revision; delete asks first", async () => {
  const user = userEvent.setup();
  let saved: Record<string, unknown> = {};
  const fetch = api({ "GET /api/event-handlers": { handlers: [handler()] },
    "PUT /api/event-handlers/h-1": body => { saved = body as Record<string, unknown>; return { body: { handler: handler({ enabled: false, revision: 2 }) } }; },
    "DELETE /api/event-handlers/h-1": () => ({ status: 204 }) });
  view();
  await user.click(await screen.findByRole("switch", { name: "Enabled: Triage" }));
  await waitFor(() => expect([saved.enabled, saved.revision]).toEqual([false, 1]));
  await user.click(screen.getByRole("button", { name: "Delete Triage" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("Delete handler Triage?")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(screen.queryByText("Triage")).toBeNull());
  expect(fetch.mock.calls.some(([url, init]) => url === "/api/event-handlers/h-1" && (init as RequestInit)?.method === "DELETE")).toBe(true);
});
```

In `services/admin-ui/app/components/__tests__/adminLayout.test.tsx`, add (copy the file's existing render helper for a
path; it renders `AdminLayout` inside a `MemoryRouter` with `initialEntries`):

```tsx
test("the Events link stays active on the Handlers tab", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ is_admin: true }))));
  render(<MemoryRouter initialEntries={["/events/handlers"]}><AdminLayout><div>Handlers content</div></AdminLayout></MemoryRouter>);
  expect(await screen.findByText("Handlers content")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Events" })).toHaveAttribute("data-active", "true");
  expect(screen.getByRole("heading", { level: 1, name: "Events" })).toBeInTheDocument();
  vi.unstubAllGlobals();
});
```

(`SidebarMenuButton` puts `data-active` on the link; the page title is the layout's `<h1>`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/handlers.test.tsx app/components/__tests__/adminLayout.test.tsx`
Expected: FAIL (`HandlersView` missing; Events not active on `/events/handlers`).

- [ ] **Step 3: Implement**

`services/admin-ui/app/lib/eventHandlers.ts`:

```ts
import { request } from "./api";

export type Operator = "equals" | "not_equals" | "contains" | "in" | "matches" | "exists";
export interface Condition { field: string; op: Operator; value: string | string[] | null }
export interface EmitTemplates { type: string; subject: string; title: string; payload: string }
export interface HandlerDraft { name: string; enabled: boolean; type_pattern: string; conditions: Condition[]; action: "emit" | "agent";
  emit: EmitTemplates | null; agent_id: string | null; prompt: string | null }
export interface EventHandler extends HandlerDraft { id: string; agent_name: string | null; revision: number; created_at: string; updated_at: string;
  last_run: { status: "succeeded" | "failed"; created_at: string } | null }
export type PreviewOutput = { type: string; subject: string; title: string; payload: Record<string, unknown> } | { prompt: string } | { error: string };
export interface PreviewResult { matches: boolean; reason: string | null; output: PreviewOutput }

export const OPERATOR_LABELS: Record<Operator, string> = { equals: "equals", not_equals: "does not equal", contains: "contains",
  in: "is one of", matches: "matches regex", exists: "exists" };
export const FIELD_SUGGESTIONS = ["source", "type", "subject", "title", "payload.status", "payload.labels", "payload.assignee",
  "payload.priority", "payload.issue_type", "payload.summary", "payload.changes.status", "payload.changes.assignee"];

const json = (method: string, value: unknown) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
export const getHandlers = () => request<{ handlers: EventHandler[] }>("/api/event-handlers");
export const getHandlerAgents = () => request<{ agents: { id: string; name: string }[] }>("/api/event-handlers/agents");
export const createHandler = (draft: HandlerDraft) => request<{ handler: EventHandler }>("/api/event-handlers", json("POST", draft));
export const saveHandler = (id: string, draft: HandlerDraft & { revision: number }) =>
  request<{ handler: EventHandler }>(`/api/event-handlers/${encodeURIComponent(id)}`, json("PUT", draft));
export const deleteHandler = (id: string) => request<void>(`/api/event-handlers/${encodeURIComponent(id)}`, { method: "DELETE" });
export const previewHandler = (draft: HandlerDraft, eventId: number) =>
  request<PreviewResult>("/api/event-handlers/preview", json("POST", { handler: draft, event_id: eventId }));

/** The editable part of a saved handler, as PUT takes it. */
export const toDraft = (handler: EventHandler): HandlerDraft => ({ name: handler.name, enabled: handler.enabled, type_pattern: handler.type_pattern,
  conditions: handler.conditions, action: handler.action, emit: handler.emit, agent_id: handler.agent_id, prompt: handler.prompt });
```

`services/admin-ui/app/components/HandlerSheet.tsx`:

```tsx
import { useEffect, useState, type FormEvent } from "react";
import { Plus, X } from "lucide-react";
import { ApiError } from "../lib/api";
import { DEFAULT_SORT, NO_FILTERS, getEventFacets, getEvents, type EventRow } from "../lib/events";
import { FIELD_SUGGESTIONS, OPERATOR_LABELS, createHandler, getHandlerAgents, previewHandler, saveHandler,
  type EventHandler, type HandlerDraft, type Operator, type PreviewResult } from "../lib/eventHandlers";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Spinner } from "~/components/ui/spinner";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

interface ConditionRow { field: string; op: Operator; value: string }
interface Form { name: string; enabled: boolean; type_pattern: string; conditions: ConditionRow[]; action: "emit" | "agent";
  emit: { type: string; subject: string; title: string; payload: string }; agent_id: string; prompt: string }

const BLANK: Form = { name: "", enabled: true, type_pattern: "jira.ticket.*", conditions: [], action: "emit",
  emit: { type: "", subject: "", title: "", payload: "" }, agent_id: "", prompt: "" };
const HELP = "event.id, event.source, event.type, event.subject, event.title, event.occurred_at, event.payload.<field>, event.parent_id, event.root_id, event.depth";

function fromHandler(handler: EventHandler): Form {
  return { name: handler.name, enabled: handler.enabled, type_pattern: handler.type_pattern, action: handler.action,
    conditions: handler.conditions.map(c => ({ field: c.field, op: c.op, value: Array.isArray(c.value) ? c.value.join(", ") : c.value ?? "" })),
    emit: handler.emit ?? BLANK.emit, agent_id: handler.agent_id ?? "", prompt: handler.prompt ?? "" };
}
function toDraft(form: Form): HandlerDraft {
  return { name: form.name.trim(), enabled: form.enabled, type_pattern: form.type_pattern.trim(), action: form.action,
    conditions: form.conditions.map(c => ({ field: c.field.trim(), op: c.op,
      value: c.op === "exists" ? null : c.op === "in" ? c.value.split(",").map(item => item.trim()).filter(Boolean) : c.value })),
    emit: form.action === "emit" ? form.emit : null, agent_id: form.action === "agent" ? form.agent_id || null : null,
    prompt: form.action === "agent" ? form.prompt : null };
}

function HandlerForm({ handler, onSaved }: { handler: EventHandler | null; onSaved: (handler: EventHandler) => void }) {
  const [form, setForm] = useState<Form>(handler ? fromHandler(handler) : BLANK);
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [recent, setRecent] = useState<EventRow[]>([]);
  const [previewEvent, setPreviewEvent] = useState("");
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getHandlerAgents().then(result => setAgents(result.agents)).catch(() => setAgents([]));
    getEventFacets().then(result => setTypes(result.types)).catch(() => setTypes([]));
    getEvents(NO_FILTERS, DEFAULT_SORT).then(result => setRecent(result.events)).catch(() => setRecent([]));
  }, []);

  const update = (changes: Partial<Form>) => setForm(current => ({ ...current, ...changes }));
  const setCondition = (index: number, changes: Partial<ConditionRow>) =>
    update({ conditions: form.conditions.map((row, at) => at === index ? { ...row, ...changes } : row) });
  const setEmit = (field: keyof Form["emit"], value: string) => update({ emit: { ...form.emit, [field]: value } });
  const message = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(null);
    try {
      const draft = toDraft(form);
      onSaved((handler ? await saveHandler(handler.id, { ...draft, revision: handler.revision }) : await createHandler(draft)).handler);
    } catch (reason) { setError(message(reason, "Could not save the handler.")); }
    finally { setBusy(false); }
  }
  async function runPreview() {
    if (!previewEvent) return;
    setPreview(null); setError(null);
    try { setPreview(await previewHandler(toDraft(form), Number(previewEvent))); }
    catch (reason) { setError(message(reason, "Could not preview the handler.")); }
  }

  return <form onSubmit={save} className="flex flex-col gap-4 px-4 pb-4">
    <FieldGroup>
      <Field><FieldLabel htmlFor="handler-name">Name</FieldLabel><Input id="handler-name" value={form.name} onChange={e => update({ name: e.target.value })} /></Field>
      <Field orientation="horizontal"><Switch id="handler-enabled" checked={form.enabled} onCheckedChange={enabled => update({ enabled })} />
        <FieldLabel htmlFor="handler-enabled">Enabled</FieldLabel></Field>
      <Field><FieldLabel htmlFor="handler-pattern">Event type</FieldLabel>
        <Input id="handler-pattern" list="handler-types" value={form.type_pattern} onChange={e => update({ type_pattern: e.target.value })} />
        <datalist id="handler-types">{["*", "jira.ticket.*", ...types].map(type => <option key={type} value={type} />)}</datalist>
        <FieldDescription>Use * for any part, e.g. jira.ticket.* or jira.*.created.</FieldDescription></Field>
      <FieldSet><FieldLegend variant="label">Conditions</FieldLegend>
        <FieldDescription>All conditions must hold.</FieldDescription>
        <datalist id="handler-fields">{FIELD_SUGGESTIONS.map(field => <option key={field} value={field} />)}</datalist>
        {form.conditions.map((row, index) => <div key={index} className="flex flex-wrap items-center gap-2">
          <Input aria-label={`Condition ${index + 1} field`} list="handler-fields" className="w-40" value={row.field} onChange={e => setCondition(index, { field: e.target.value })} />
          <Select value={row.op} onValueChange={op => setCondition(index, { op: op as Operator })}>
            <SelectTrigger aria-label={`Condition ${index + 1} operator`} className="w-36"><SelectValue /></SelectTrigger>
            <SelectContent><SelectGroup>{(Object.keys(OPERATOR_LABELS) as Operator[]).map(op => <SelectItem key={op} value={op}>{OPERATOR_LABELS[op]}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
          {row.op !== "exists" && <Input aria-label={`Condition ${index + 1} value`} className="w-40" value={row.value}
            placeholder={row.op === "in" ? "a, b, c" : ""} onChange={e => setCondition(index, { value: e.target.value })} />}
          <Button type="button" variant="ghost" size="icon" aria-label={`Remove condition ${index + 1}`}
            onClick={() => update({ conditions: form.conditions.filter((_, at) => at !== index) })}><X /></Button>
        </div>)}
        {form.conditions.length < 20 && <Button type="button" variant="outline" size="sm" className="self-start"
          onClick={() => update({ conditions: [...form.conditions, { field: "", op: "equals", value: "" }] })}><Plus data-icon="inline-start" />Add condition</Button>}
      </FieldSet>
      <Field><FieldLabel htmlFor="handler-action">Action</FieldLabel>
        <Select value={form.action} onValueChange={action => update({ action: action as Form["action"] })}>
          <SelectTrigger id="handler-action" aria-label="Action" className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value="emit">Emit event</SelectItem><SelectItem value="agent">Send to agent</SelectItem></SelectGroup></SelectContent>
        </Select></Field>
      {form.action === "emit" ? <>
        <Field><FieldLabel htmlFor="emit-type">New event type</FieldLabel><Input id="emit-type" placeholder="triage.requested" value={form.emit.type} onChange={e => setEmit("type", e.target.value)} /></Field>
        <Field><FieldLabel htmlFor="emit-subject">Subject</FieldLabel><Input id="emit-subject" placeholder="{{ event.subject }}" value={form.emit.subject} onChange={e => setEmit("subject", e.target.value)} /></Field>
        <Field><FieldLabel htmlFor="emit-title">Title</FieldLabel><Input id="emit-title" placeholder="{{ event.title }}" value={form.emit.title} onChange={e => setEmit("title", e.target.value)} /></Field>
        <Field><FieldLabel htmlFor="emit-payload">Payload (JSON template)</FieldLabel>
          <Textarea id="emit-payload" rows={5} className="font-mono text-xs" placeholder="{{ event.payload | tojson }}" value={form.emit.payload} onChange={e => setEmit("payload", e.target.value)} /></Field>
      </> : <>
        <Field><FieldLabel htmlFor="handler-agent">Agent</FieldLabel>
          <Select value={form.agent_id} onValueChange={agent_id => update({ agent_id })}>
            <SelectTrigger id="handler-agent" aria-label="Agent" className="w-full"><SelectValue placeholder="Choose an agent" /></SelectTrigger>
            <SelectContent><SelectGroup>{agents.map(agent => <SelectItem key={agent.id} value={agent.id}>{agent.name}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
          <FieldDescription>Enabled agents with fixed resources. Each matching event starts a new session.</FieldDescription></Field>
        <Field><FieldLabel htmlFor="handler-prompt">Prompt</FieldLabel>
          <Textarea id="handler-prompt" rows={5} value={form.prompt} placeholder="Triage {{ event.subject }}: {{ event.payload.summary }}" onChange={e => update({ prompt: e.target.value })} /></Field>
      </>}
      <Alert><AlertDescription><span className="font-medium">Template help.</span> Fields: {HELP}. In the payload, write values with
        {" "}<code>{"{{ event.payload.summary | tojson }}"}</code> so quotes stay valid JSON.</AlertDescription></Alert>
      <FieldSet><FieldLegend variant="label">Preview</FieldLegend>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={previewEvent} onValueChange={setPreviewEvent}>
            <SelectTrigger aria-label="Preview event" className="w-64"><SelectValue placeholder="Choose a recent event" /></SelectTrigger>
            <SelectContent><SelectGroup>{recent.map(event => <SelectItem key={event.id} value={String(event.id)}>{event.subject} · {event.type}</SelectItem>)}</SelectGroup></SelectContent>
          </Select>
          <Button type="button" variant="outline" disabled={!previewEvent} onClick={() => void runPreview()}>Preview</Button>
        </div>
        {preview && <div className="flex flex-col gap-2 text-sm">
          <p className={preview.matches ? "font-medium" : "text-muted-foreground"}>{preview.matches ? "Matches" : `Doesn’t match: ${preview.reason}`}</p>
          {"error" in preview.output ? <Alert variant="destructive"><AlertDescription>{preview.output.error}</AlertDescription></Alert>
            : <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">{"prompt" in preview.output ? preview.output.prompt : JSON.stringify(preview.output, null, 2)}</pre>}
        </div>}
      </FieldSet>
    </FieldGroup>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <SheetFooter className="p-0"><Button type="submit" disabled={busy}>{busy && <Spinner data-icon="inline-start" />}Save</Button></SheetFooter>
  </form>;
}

export default function HandlerSheet({ handler, open, onClose, onSaved }: { handler: EventHandler | null; open: boolean;
  onClose: () => void; onSaved: (handler: EventHandler) => void }) {
  return <Sheet open={open} onOpenChange={next => { if (!next) onClose(); }}>
    <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-xl">
      <SheetHeader><SheetTitle>{handler ? "Edit handler" : "New handler"}</SheetTitle>
        <SheetDescription>When an event matches, emit a new event or send it to an agent.</SheetDescription></SheetHeader>
      {open && <HandlerForm key={handler?.id ?? "new"} handler={handler} onSaved={onSaved} />}
    </SheetContent>
  </Sheet>;
}
```

`services/admin-ui/app/components/HandlersView.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { Pencil, Plus, Trash2, Workflow } from "lucide-react";
import { ApiError } from "../lib/api";
import { deleteHandler, getHandlers, saveHandler, toDraft, type EventHandler } from "../lib/eventHandlers";
import HandlerSheet from "./HandlerSheet";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Alert, AlertDescription, AlertTitle } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "~/components/ui/empty";
import { Skeleton } from "~/components/ui/skeleton";
import { Switch } from "~/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

const text = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback;
const when = (handler: EventHandler) => `${handler.type_pattern} · ${handler.conditions.length} condition${handler.conditions.length === 1 ? "" : "s"}`;
const action = (handler: EventHandler) => handler.action === "emit" ? `Emit ${handler.emit?.type ?? ""}` : `Send to ${handler.agent_name ?? handler.agent_id}`;

export default function HandlersView() {
  const [handlers, setHandlers] = useState<EventHandler[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<EventHandler | "new" | null>(null);
  const [deleting, setDeleting] = useState<EventHandler | null>(null);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const deletingName = useRef("");

  useEffect(() => {
    getHandlers().then(result => { setHandlers(result.handlers); setError(null); })
      .catch(reason => { setHandlers([]); setError(text(reason, "Could not load handlers.")); });
  }, []);

  const sorted = (rows: EventHandler[]) => [...rows].sort((a, b) => a.name.localeCompare(b.name));
  const replace = (saved: EventHandler) => setHandlers(current => sorted([...(current ?? []).filter(item => item.id !== saved.id), saved]));
  async function toggle(handler: EventHandler, enabled: boolean) {
    setBusy(handler.id); setError(null);
    try { replace((await saveHandler(handler.id, { ...toDraft(handler), enabled, revision: handler.revision })).handler); }
    catch (reason) { setError(text(reason, "Could not change the handler.")); }
    finally { setBusy(null); }
  }
  async function remove() {
    const doomed = deleting;
    if (!doomed || removing) return;
    setRemoving(true); setError(null);
    try { await deleteHandler(doomed.id); setHandlers(current => current?.filter(item => item.id !== doomed.id) ?? null); }
    catch (reason) { setError(text(reason, "Could not delete the handler.")); }
    finally { setRemoving(false); setDeleting(null); }
  }

  return <div className="flex flex-col gap-4">
    <div className="flex justify-end"><Button onClick={() => setEditing("new")}><Plus data-icon="inline-start" />New handler</Button></div>
    {error && <Alert variant="destructive"><AlertTitle>Handlers</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {handlers === null && <Skeleton className="h-32 w-full" />}
    {handlers?.length === 0 && <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><Workflow /></EmptyMedia><EmptyTitle>Handlers</EmptyTitle>
      <EmptyDescription>No handlers yet. Create one to turn events into new events or agent sessions.</EmptyDescription></EmptyHeader></Empty>}
    {handlers && handlers.length > 0 && <div className="overflow-x-auto rounded-md border"><Table>
      <TableHeader><TableRow><TableHead>On</TableHead><TableHead>Name</TableHead><TableHead>When</TableHead><TableHead>Action</TableHead>
        <TableHead>Last run</TableHead><TableHead><span className="sr-only">Actions</span></TableHead></TableRow></TableHeader>
      <TableBody>{handlers.map(handler => <TableRow key={handler.id}>
        <TableCell><Switch aria-label={`Enabled: ${handler.name}`} checked={handler.enabled} disabled={busy === handler.id}
          onCheckedChange={enabled => void toggle(handler, enabled)} /></TableCell>
        <TableCell className="font-medium">{handler.name}</TableCell>
        <TableCell className="font-mono text-xs">{when(handler)}</TableCell>
        <TableCell>{action(handler)}</TableCell>
        <TableCell>{handler.last_run ? <div className="flex flex-col items-start gap-1">
          <Badge variant={handler.last_run.status === "failed" ? "destructive" : "secondary"}>{handler.last_run.status === "failed" ? "Failed" : "Succeeded"}</Badge>
          <span className="text-xs text-muted-foreground">{new Date(handler.last_run.created_at).toLocaleString()}</span></div>
          : <span className="text-sm text-muted-foreground">Never run</span>}</TableCell>
        <TableCell><div className="flex justify-end gap-1">
          <Button size="sm" variant="ghost" aria-label={`Edit ${handler.name}`} onClick={() => setEditing(handler)}><Pencil data-icon="inline-start" />Edit</Button>
          <Button size="sm" variant="ghost" aria-label={`Delete ${handler.name}`} onClick={() => { deletingName.current = handler.name; setDeleting(handler); }}><Trash2 data-icon="inline-start" />Delete</Button>
        </div></TableCell>
      </TableRow>)}</TableBody>
    </Table></div>}
    <HandlerSheet open={editing !== null} handler={editing === "new" ? null : editing} onClose={() => setEditing(null)}
      onSaved={saved => { replace(saved); setEditing(null); }} />
    <AlertDialog open={deleting !== null} onOpenChange={open => { if (!open && !removing) setDeleting(null); }}>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>Delete handler {deletingName.current}?</AlertDialogTitle>
          <AlertDialogDescription>Its run history stays on the events.</AlertDialogDescription></AlertDialogHeader>
        <AlertDialogFooter><AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
          <AlertDialogAction disabled={removing} onClick={event => { event.preventDefault(); void remove(); }}>Delete</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </div>;
}
```

`services/admin-ui/app/components/EventsPage.tsx`:

```tsx
import { useNavigate } from "react-router";
import EventsView from "./EventsView";
import HandlersView from "./HandlersView";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "~/components/ui/tabs";

/** Administration → Events: the Events and Handlers tabs, each its own address (/events, /events/handlers). */
export default function EventsPage({ tab }: { tab: "events" | "handlers" }) {
  const navigate = useNavigate();
  return <Tabs value={tab} onValueChange={value => navigate(value === "handlers" ? "/events/handlers" : "/events")} className="flex flex-col gap-4">
    <TabsList><TabsTrigger value="events">Events</TabsTrigger><TabsTrigger value="handlers">Handlers</TabsTrigger></TabsList>
    <TabsContent value="events">{tab === "events" && <EventsView />}</TabsContent>
    <TabsContent value="handlers">{tab === "handlers" && <HandlersView />}</TabsContent>
  </Tabs>;
}
```

`services/admin-ui/app/routes/events.tsx`:

```tsx
import AdminLayout from "../components/AdminLayout";
import EventsPage from "../components/EventsPage";

export default function EventsRoute() {
  return <AdminLayout><EventsPage tab="events" /></AdminLayout>;
}
```

`services/admin-ui/app/routes/eventHandlers.tsx`:

```tsx
import AdminLayout from "../components/AdminLayout";
import EventsPage from "../components/EventsPage";

export default function EventHandlersRoute() {
  return <AdminLayout><EventsPage tab="handlers" /></AdminLayout>;
}
```

`routes.ts`: after `route("events", "./routes/events.tsx"),` add `route("events/handlers", "./routes/eventHandlers.tsx"),`.

`AdminLayout.tsx`: `const eventsPage = pathname === "/events" || pathname.startsWith("/events/");` and the events
description becomes `"Things that happened at your sources, and handlers that turn them into new events or agent sessions."`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/handlers.test.tsx app/components/__tests__/adminLayout.test.tsx app/components/__tests__/events.test.tsx && npx react-router typegen && npx tsc --noEmit`
Expected: PASS, exit 0.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/eventHandlers.ts services/admin-ui/app/components/HandlersView.tsx \
  services/admin-ui/app/components/HandlerSheet.tsx services/admin-ui/app/components/EventsPage.tsx \
  services/admin-ui/app/routes/events.tsx services/admin-ui/app/routes/eventHandlers.tsx services/admin-ui/app/routes.ts \
  services/admin-ui/app/components/AdminLayout.tsx services/admin-ui/app/components/__tests__/handlers.test.tsx \
  services/admin-ui/app/components/__tests__/adminLayout.test.tsx
git commit -m "feat(admin-ui): Events → Handlers tab with handler form and Preview"
```

---

### Task 10: Agent Test tab shows event sessions; run kind label; docs

**Files:**
- Modify: `services/admin-ui/app/lib/generations.ts:9` (`SessionPurpose` adds `'event'`)
- Modify: `services/admin-ui/app/lib/testSessions.ts` (`TestSessionSummary.purpose`)
- Modify: `services/admin-ui/app/components/AgentTestView.tsx:44` and the history list (~line 200)
- Modify: `services/admin-ui/app/components/RunsView.tsx:12` (`event_session: 'Event session'`)
- Modify: `services/admin-ui/app/components/__tests__/agentSessions.test.tsx`
- Modify: `docs/migrations.md`, `docs/user-guide.md`

**Interfaces:**
- Consumes: list rows with `purpose` (Task 4); event sessions open at `/agents/<id>?view=test&session_id=<id>`.

- [ ] **Step 1: Write the failing test** — append to `services/admin-ui/app/components/__tests__/agentSessions.test.tsx`
  (it uses the file's `session` fixture, `detail` variable, `renderPage` helper and the mocked `tests.getTestSessions`):

```tsx
it('an event handler\'s session opens in the Test tab and is marked Event in History', async () => {
  detail = { ...structuredClone(session), purpose: 'event', title: 'jira.ticket.created DEMO-1' } as SessionDetail;
  vi.mocked(tests.getTestSessions).mockResolvedValue({ sessions: [{ id: 's1', title: 'jira.ticket.created DEMO-1', status: 'waiting',
    purpose: 'event', created_at: '2026-10-09T10:00:00Z', last_activity_at: '2026-10-09T10:00:00Z', actor_email: 'admin@test' }] });
  renderPage('?view=test&session_id=s1');
  expect(await screen.findByText('Three retries.')).toBeVisible();
  expect(screen.queryByText('Session not found for this agent.')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'History' }));
  const sheet = within(await screen.findByRole('dialog'));
  expect(await sheet.findByText('jira.ticket.created DEMO-1')).toBeVisible();
  expect(sheet.getByText('Event')).toBeVisible();
});
```

Also add `purpose: 'test'` to the session row in the existing History test (`it('History lists this agent…')`), since
`TestSessionSummary.purpose` becomes required.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/agentSessions.test.tsx`
Expected: FAIL (event session treated as foreign; no badge).

- [ ] **Step 3: Implement**

- `generations.ts`: `export type SessionPurpose = 'documents' | 'test' | 'event';`
- `testSessions.ts`: `TestSessionSummary` gains `purpose: 'test' | 'event'`; update its doc comment to say event
  handlers' sessions are listed too.
- `AgentTestView.tsx:44`: `const foreign = session !== null && (session.agent_id !== agentId || (session.purpose !== 'test' && session.purpose !== 'event'));`
- History row (~line 201): after the title paragraph add
  `{saved.purpose === 'event' && <Badge variant="outline">Event</Badge>}` inside the `min-w-0` div, before the title
  (import `Badge` from `~/components/ui/badge` if the file does not yet).
- `RunsView.tsx:12`: add `event_session: 'Event session'` to `KINDS`.

Docs — `docs/migrations.md`, after the 0088 section:

```markdown
## 0089_event_handlers

`ka.events` gains a status (`pending`, `processed`, `failed`, with `processed_at`) and a chain: `parent_id`, `root_id`
(a trigger sets it to the event's own id for a root), `depth` (0–5) and `handler_id`. Source `handler` marks events a
handler emitted. Existing events are marked `processed` so new handlers do not replay them. `ka.event_handlers` holds
handlers (type pattern, conditions, action `emit` with Jinja templates or `agent` with an agent and a prompt template)
and `ka.event_handler_runs` what each handler did with each event. `ka.agent_sessions.purpose` allows `event` (sessions
a handler started). The down migration deletes handler-emitted events and event sessions, then drops the tables and
columns.
```

`docs/user-guide.md` — replace the body of 4.17 Events (the paragraph under the heading, up to the `---`) with:

```markdown
Administration → Events lists what happened at your sources and what handlers made of it: Time, Source, Type,
Subject, Title and Status. Click a column header to sort by it, and again to flip the order. Today the source is Jira:
every scheduled pull (Jira → Auto pull, or Run now) records **Ticket created** for a ticket it sees for the first time
and **Ticket updated** when a ticket changed in Jira, with what changed (status, labels, assignee, priority, type,
summary, description). The very first pull records every ticket it fetches. Events a handler emitted have the source
**Handler** (see 4.18).

Each event is **Pending** until the event handlers have looked at it (within a few seconds), then **Processed** — or
**Failed** when one of its handlers failed; failed events are not retried. Events recorded before handlers existed are
Processed. Filter by source, type, status and handler, search by ticket key or title, and open an event to see its
changes, a link to the ticket, the raw data, its **Chain** (the event it came from and everything handlers made of it,
indented by level) and its **Handler runs** (each handler's result, with links to the event it emitted or the agent
session it started).
```

Then add:

```markdown
### 4.18 Event handlers

Administration → Events → **Handlers** reacts to events. Every few seconds repository-worker takes each new event and
runs every enabled handler whose **Event type** pattern matches (`*` for any part: `jira.ticket.*`, `jira.*.created`,
or `*` for every event) and whose **Conditions** all hold. Conditions compare a field — `source`, `type`, `subject`,
`title` or `payload.<field>` such as `payload.status` or `payload.changes.status` — with **equals**, **does not
equal**, **contains** (text or list item), **is one of** (comma-separated), **matches regex** or **exists**.

A handler does one thing:

- **Emit event** creates a new event in the same chain. **New event type** (e.g. `triage.requested`), **Subject**,
  **Title** and **Payload** are Jinja templates over the matched event, available as `event` (`event.subject`,
  `event.payload.summary`, …). Empty Subject, Title or Payload copy the matched event's. Write payload values with
  `| tojson`, e.g. `{"summary": {{ event.payload.summary | tojson }}}`; the payload must be a JSON object. The new
  event is processed like any other, so handlers can build chains — at most 5 levels deep.
- **Send to agent** starts a new session of the chosen agent for every matching event; the **Prompt** template is its
  first message, and you (the handler's creator) are its user. Only enabled agents with fixed resources can be
  chosen. The session appears in the agent's Test tab, marked **Event**.

**Preview** renders the handler against a recent event without saving: it says whether the event matches (or which
check failed) and shows the new event or prompt. A handler that fails — a template error, a disabled or deleted agent,
a chain that is too deep — marks the event **Failed** and records why under **Handler runs**; other handlers on that
event still run. Deleting a handler keeps its run history on the events.
```

Insert it right after 4.17 (before the `---` that ends section 4); the guide has no table of contents to update.
Keep lines ≤ 120 characters like the surrounding text.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/admin-ui && npx vitest run && npx react-router typegen && npx tsc --noEmit`
Then: `cd services/admin-api && uv run pytest -q` (known unrelated failures: `test_deploy` docs folder and
`test_environments` rollback, if still present on main) and `sh db/tests/run.sh` from the worktree root.
Expected: UI all pass; API only the known failures; db exit 0.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/generations.ts services/admin-ui/app/lib/testSessions.ts \
  services/admin-ui/app/components/AgentTestView.tsx services/admin-ui/app/components/RunsView.tsx \
  services/admin-ui/app/components/__tests__/agentSessions.test.tsx docs/migrations.md docs/user-guide.md
git commit -m "feat(admin-ui): event sessions in the agent Test tab; docs for event handlers"
```

---

## Self-review notes

- Spec coverage: data model (T1), matching (T2), templates (T3), agent action/session purpose (T4), loop + depth cap +
  no retry + savepoints (T5), handler API incl. preview/agents/revision/audit (T6), events API sort/filter/offset/facets/
  detail (T7), Events tab (T8), Handlers tab + tabs/routes (T9), Test tab badge, docs (T10).
- Rulings recorded here: `event_handlers.agent_id` has no foreign key (agent deletion would otherwise fail or silently
  delete the handler) — runs fail with `Agent not found.`; the Action choice is a Select (no radio-group component in
  `components/ui`); Preview renders the output even when the event does not match; suggestions (review) are allowed
  for event sessions too, so the Test tab behaves the same for both.
