# Event handler answers as Jira comments — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Send-to-agent handler can post the agent's answer as a comment on the Jira ticket that triggered it; the
notification is still written and shows whether the comment was posted.

**Architecture:** Migration 0109 adds `event_handlers.reply_to_jira` and delivery columns on `ka.notifications`.
`notify_event_run` marks a Jira delivery `pending` in the transaction that ends the run's first turn; a new thread in
repository-worker (`events/jira_replies.py`) posts pending comments with `JiraClient.add_comment`, retrying after 1 and
5 minutes. The Jira pull skips an update caused only by our own comment.

**Tech Stack:** Python 3.14, FastAPI, psycopg 3, httpx, PostgreSQL; React + TypeScript (admin-ui), Vitest.

**Spec:** `docs/superpowers/specs/2026-10-15-jira-comment-reply-design.md`

## Global Constraints

- Backend commands run from `services/admin-api`: `uv run --frozen pytest -q -p no:warnings <paths>`; format with
  `uv run --frozen ruff format <changed files>` and sort imports with `uv run --frozen ruff check --select I001 --fix
  <changed files>`. UI commands run from `services/admin-ui`: `pnpm vitest run <paths>`, `pnpm exec prettier --write
  <changed files>`, `pnpm typecheck`.
- Never run `make`, never `git stash`, never touch `.env`/`.local` or the running Docker stack, never `git add`
  `.superpowers` or `node_modules`.
- Known unrelated failures in the full backend suite: code-graph tests inside worktrees, `test_deploy` docs command,
  `test_environments` rollback.
- Migration files: `BEGIN; SET search_path = ka, public; … COMMIT;`, no grants (as 0108).
- Messages, verbatim:
  - `Only Send to agent handlers can post to Jira.`
  - `This event did not come from a Jira connection.`
  - `The Jira connection that recorded this event no longer exists.`
  - `Posting to Jira failed unexpectedly. See the repository-worker log.`
  - Comment footer: `\n\n— <agent name> (Knowledge Agent)`; truncation marker: `… (truncated; the full answer is in Knowledge Agent)`.
- Comment limit 32,000 characters including the footer. Retries: after attempt 1 wait 1 minute, after attempt 2 wait
  5 minutes; attempt 3 is the last. HTTP 400/403/404 from Jira fail at once. Loop poll: 5 seconds. Loop guard window:
  2 seconds.
- Stack data is test data: no backfill or compatibility code.
- Commits end with the session's Co-Authored-By trailer.

## Review Focus

1. A run answered by an agent with an empty answer (`NO_ANSWER`) on a Jira handler: the placeholder text is what gets
   posted — expected, pinned in Task 3.
2. Jira returns 201 with a body missing `id` or `created`: the delivery fails (retryable), never crashes the loop —
   pinned in Task 4.
3. Two repository-workers running at once: a pending row is posted once — pinned in Task 5 (locked row is skipped).
4. Jira settings turned off (connection disabled) while a comment is pending: fails at once with the settings message,
   no retries — pinned in Task 5.
5. A ticket that gets a real field change in the same pull as our comment: the event is still recorded — pinned in
   Task 6.

---

### Task 1: Migration 0109

**Files:**
- Create: `db/migrations/0109_jira_replies.up.sql`, `db/migrations/0109_jira_replies.down.sql`
- Modify: `db/migrations/0108_tools.up.sql:4` (its comment still says `0107`), `docs/migrations.md` (new `## 0109_jira_replies` at the end)
- Test: `services/admin-api/tests/test_jira_replies_migration.py`

**Interfaces:**
- Produces: `ka.event_handlers.reply_to_jira boolean NOT NULL DEFAULT false`; `ka.notifications.jira_status`,
  `jira_connection_id`, `jira_issue_id`, `jira_issue_key`, `jira_comment_id`, `jira_comment_created`,
  `jira_attempts`, `jira_next_attempt_at`, `jira_error`.

- [ ] **Step 1: Write the failing test**

```python
"""0109: handlers may post their agent's answer to Jira; a notification carries the comment's delivery state."""

from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_event_processor import a_handler

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def run(conn, name: str) -> None:
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def a_notification(conn, project, **values) -> dict:
    row = {
        'project_id': project,
        'kind': 'agent.response',
        'status': 'succeeded',
        'title': 'Triage · jira.ticket.updated DEMO-1',
        'body': 'Answer',
        **values,
    }
    return conn.execute(
        f'INSERT INTO ka.notifications({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING *',
        tuple(row.values()),
    ).fetchone()


def test_defaults(store):
    handler = a_handler(store)
    with store.connect() as conn:
        note = a_notification(conn, store.project(conn, 'shop'))
    assert handler['reply_to_jira'] is False
    assert (note['jira_status'], note['jira_attempts'], note['jira_next_attempt_at']) == (None, 0, None)


def test_only_send_to_agent_handlers_reply_to_jira(store):
    with pytest.raises(psycopg.errors.CheckViolation):
        a_handler(store, reply_to_jira=True)  # action emit


@pytest.mark.parametrize(
    'values',
    [
        {'jira_status': 'pending'},  # no next attempt, no connection
        {'jira_status': 'pending', 'jira_next_attempt_at': '2026-10-15T10:00:00Z'},  # no connection or issue
        {'jira_status': 'posted', 'jira_connection_id': '00000000-0000-0000-0000-000000000001', 'jira_issue_id': '1'},
        {'jira_status': 'sent'},
        {'jira_next_attempt_at': '2026-10-15T10:00:00Z'},  # a next attempt without pending
        {'jira_status': 'failed', 'jira_error': 'x' * 2001},
    ],
)
def test_delivery_checks(store, values):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            a_notification(conn, project, **values)


def test_a_valid_pending_posted_and_failed_row(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        connection = {'jira_connection_id': '00000000-0000-0000-0000-000000000001', 'jira_issue_id': '10001'}
        a_notification(conn, project, jira_status='pending', jira_next_attempt_at='2026-10-15T10:00:00Z', **connection)
        a_notification(
            conn,
            project,
            jira_status='posted',
            jira_comment_id='5',
            jira_comment_created='2026-10-15T10:00:01Z',
            **connection,
        )
        a_notification(conn, project, jira_status='failed', jira_error='This event did not come from a Jira connection.')


def test_down_and_up_again(store):
    with store.connect() as conn:
        run(conn, '0109_jira_replies.down.sql')
        columns = {
            row['column_name']
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name IN ('notifications','event_handlers')"
            )
        }
        assert not any(name.startswith('jira_') for name in columns) and 'reply_to_jira' not in columns
        run(conn, '0109_jira_replies.up.sql')
        assert conn.execute("SELECT to_regclass('ka.notifications_jira_due') IS NOT NULL AS ok").fetchone()['ok']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_jira_replies_migration.py`
Expected: FAIL — `column "reply_to_jira" … does not exist` / file `0109_jira_replies.down.sql` missing.

- [ ] **Step 3: Write the migration**

`db/migrations/0109_jira_replies.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- 0109: a Send-to-agent handler may post its agent's answer as a comment on the Jira ticket that triggered it.
-- The notification is still written; it carries the comment's delivery state, which repository-worker
-- (events/jira_replies.py) moves from pending to posted or failed.
ALTER TABLE event_handlers ADD COLUMN reply_to_jira boolean NOT NULL DEFAULT false;
ALTER TABLE event_handlers ADD CONSTRAINT event_handlers_reply_to_jira CHECK (NOT reply_to_jira OR action = 'agent');

ALTER TABLE notifications
    ADD COLUMN jira_status text CHECK (jira_status IN ('pending', 'posted', 'failed')),
    ADD COLUMN jira_connection_id uuid,
    ADD COLUMN jira_issue_id text,
    ADD COLUMN jira_issue_key text,
    ADD COLUMN jira_comment_id text,
    ADD COLUMN jira_comment_created timestamptz,
    ADD COLUMN jira_attempts integer NOT NULL DEFAULT 0 CHECK (jira_attempts >= 0),
    ADD COLUMN jira_next_attempt_at timestamptz,
    ADD COLUMN jira_error text CHECK (char_length(jira_error) <= 2000),
    ADD CONSTRAINT notifications_jira_pending
        CHECK ((jira_status IS NOT DISTINCT FROM 'pending') = (jira_next_attempt_at IS NOT NULL)),
    ADD CONSTRAINT notifications_jira_posted
        CHECK ((jira_status IS NOT DISTINCT FROM 'posted') = (jira_comment_id IS NOT NULL AND jira_comment_created IS NOT NULL)),
    ADD CONSTRAINT notifications_jira_target
        CHECK (jira_status NOT IN ('pending', 'posted') OR (jira_connection_id IS NOT NULL AND jira_issue_id IS NOT NULL));
COMMENT ON COLUMN notifications.jira_connection_id IS 'The Jira connection that recorded the event (no foreign key: a deleted connection fails the delivery).';
COMMENT ON COLUMN notifications.jira_issue_id IS 'Jira issue id (not the key, which changes when the ticket moves).';

CREATE INDEX notifications_jira_due ON notifications(jira_next_attempt_at, id) WHERE jira_status = 'pending';
CREATE INDEX notifications_jira_posted ON notifications(jira_connection_id, jira_issue_id) WHERE jira_status = 'posted';

COMMIT;
```

`db/migrations/0109_jira_replies.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DROP INDEX notifications_jira_posted;
DROP INDEX notifications_jira_due;
ALTER TABLE notifications
    DROP COLUMN jira_status,
    DROP COLUMN jira_connection_id,
    DROP COLUMN jira_issue_id,
    DROP COLUMN jira_issue_key,
    DROP COLUMN jira_comment_id,
    DROP COLUMN jira_comment_created,
    DROP COLUMN jira_attempts,
    DROP COLUMN jira_next_attempt_at,
    DROP COLUMN jira_error;
ALTER TABLE event_handlers DROP COLUMN reply_to_jira;

COMMIT;
```

In `db/migrations/0108_tools.up.sql` change the comment `-- 0107: the tool attributes` to `-- 0108: the tool attributes`.

Append to `docs/migrations.md`:

```markdown
## 0109_jira_replies

`event_handlers.reply_to_jira` (boolean, default false; only a Send to agent handler may set it): post the agent's
answer as a comment on the Jira ticket that triggered the run. `notifications` gains the comment's delivery state:
`jira_status` (`pending`, `posted`, `failed` or null for no comment), the connection and issue id it posts to, the
issue key for the link, the posted comment's id and creation time, attempts, the next attempt time (set exactly while
pending) and the last error. Repository-worker posts pending rows (`events/jira_replies.py`); the Jira pull reads
posted rows to skip an update caused only by its own comment. The down migration drops the columns, their indexes and
the handler column.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_jira_replies_migration.py tests/test_event_handlers_migration.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0109_jira_replies.*.sql db/migrations/0108_tools.up.sql docs/migrations.md services/admin-api/tests/test_jira_replies_migration.py
git commit -m "feat(db): 0109 — handlers may reply on Jira; notifications carry the comment's delivery state"
```

---

### Task 2: Handler option in the API

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/events/handlers_api.py` (`SELECT`, `HandlerDraft`, `stored`, `create`, `save`)
- Test: `services/admin-api/tests/test_event_handlers_api.py`

**Interfaces:**
- Consumes: `ka.event_handlers.reply_to_jira` (Task 1).
- Produces: handler JSON has `reply_to_jira: bool`; `HandlerDraft.reply_to_jira: bool = False`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_event_handlers_api.py`)

```python
def test_reply_to_jira_is_saved_for_send_to_agent_and_refused_for_emit(tester):
    client = tester.client
    draft = {
        **EMIT,
        'name': 'Answer on Jira',
        'action': 'agent',
        'emit': None,
        'agent_id': tester.agent,
        'prompt': 'Triage {{ event.subject }}',
        'reply_to_jira': True,
    }
    response = client.post('/api/event-handlers', headers=HEADERS, json=draft)
    assert response.status_code == 201, response.text
    handler = response.json()['handler']
    assert handler['reply_to_jira'] is True
    saved = client.put(
        f'/api/event-handlers/{handler["id"]}', headers=HEADERS, json={**draft, 'reply_to_jira': False, 'revision': 1}
    ).json()['handler']
    assert saved['reply_to_jira'] is False
    assert (
        error(client.post('/api/event-handlers', headers=HEADERS, json={**EMIT, 'name': 'X', 'reply_to_jira': True}), 400)
        == 'Only Send to agent handlers can post to Jira.'
    )


def test_reply_to_jira_defaults_to_off(client):
    assert create(client)['reply_to_jira'] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_event_handlers_api.py -k reply_to_jira`
Expected: FAIL — `KeyError: 'reply_to_jira'` / 422 extra field.

- [ ] **Step 3: Implement**

In `handlers_api.py`:

- `SELECT`: add `h.reply_to_jira` after `h.prompt`.
- `HandlerDraft`: add `reply_to_jira: bool = False` after `prompt`.
- Constant: `NOT_AGENT_JIRA = 'Only Send to agent handlers can post to Jira.'`
- `stored`: after the name check add
  ```python
      if draft.reply_to_jira and draft.action != 'agent':
          raise bad(NOT_AGENT_JIRA)
  ```
  and add `'reply_to_jira': draft.reply_to_jira,` to `values`.
- `create`: insert column `reply_to_jira` (after `prompt`) with `values['reply_to_jira']`.
- `save`: `UPDATE … prompt=%s,reply_to_jira=%s,revision=…` with `values['reply_to_jira']`.
- Audit details on create and update: add `'reply_to_jira': values['reply_to_jira']`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_event_handlers_api.py tests/test_event_processor.py`
Expected: PASS (the existing `test_create_list_edit_delete` compares the listed handler with the created one, both now include `reply_to_jira`).

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/events/handlers_api.py services/admin-api/tests/test_event_handlers_api.py
git commit -m "feat(events): Send to agent handlers can ask to post the answer to Jira"
```

---

### Task 3: The notification marks the Jira delivery

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/sessions/text.py` (`notify_event_run`)
- Test: `services/admin-api/tests/test_jira_reply_notifications.py`

**Interfaces:**
- Consumes: Task 1 columns; `reply_to_jira` on the handler.
- Produces: `NOT_FROM_JIRA = 'This event did not come from a Jira connection.'` in `sessions/text.py`; a notification
  row with `jira_status` set as below.

- [ ] **Step 1: Write the failing tests**

```python
"""When an event run's first turn ends, its notification says whether the answer goes to Jira."""

import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.sessions.text import NO_ANSWER, NOT_FROM_JIRA, notify_event_run
from test_event_run_notifications import a_run
from test_generations import sessions  # noqa: F401  (fixture)


def prepared(sessions, *, reply=True, source='jira', source_id=True, issue_id='10001'):
    sid, agent_id, event, handler = a_run(sessions)
    with sessions.store.connect() as conn:
        conn.execute('UPDATE ka.event_handlers SET reply_to_jira=%s WHERE id=%s', (reply, handler['id']))
        conn.execute(
            'UPDATE ka.events SET source=%s,source_id=%s,payload=%s WHERE id=%s',
            (
                source,
                event['source_id'] if source_id else None,
                Jsonb({'issue_id': issue_id, 'key': 'DEMO-1'} if issue_id else {}),
                event['id'],
            ),
        )
    return sid, event


def notified(sessions, sid, status='waiting', text='The answer') -> dict:
    with sessions.store.connect() as conn:
        notify_event_run(conn, sid, 1, status, text)
        return conn.execute('SELECT * FROM ka.notifications WHERE session_id=%s', (sid,)).fetchone()


def test_a_succeeded_jira_run_with_the_option_is_pending(sessions):
    sid, event = prepared(sessions)
    row = notified(sessions, sid)
    assert (row['jira_status'], row['jira_connection_id'], row['jira_issue_id'], row['jira_issue_key']) == (
        'pending',
        event['source_id'],
        '10001',
        'DEMO-1',
    )
    assert row['jira_next_attempt_at'] is not None and row['jira_attempts'] == 0


def test_an_empty_answer_posts_the_placeholder(sessions):
    sid, _ = prepared(sessions)
    row = notified(sessions, sid, text='  ')
    assert (row['jira_status'], row['body']) == ('pending', NO_ANSWER)


def test_a_simulated_jira_event_fails_the_delivery(sessions):
    sid, _ = prepared(sessions, source_id=False)
    row = notified(sessions, sid)
    assert (row['jira_status'], row['jira_error'], row['jira_next_attempt_at']) == ('failed', NOT_FROM_JIRA, None)


@pytest.mark.parametrize(
    'options,status',
    [({'reply': False}, 'waiting'), ({'source': 'manual', 'source_id': False}, 'waiting'), ({}, 'failed')],
)
def test_no_delivery(sessions, options, status):
    sid, _ = prepared(sessions, **options)
    row = notified(sessions, sid, status=status, text='Agent execution failed.')
    assert (row['jira_status'], row['jira_connection_id'], row['jira_issue_id']) == (None, None, None)
```

`a_run(sessions)` only inserts rows (it runs no turn), so the plain `sessions` fixture is enough. The manual case clears
`source_id` because 0091 requires a `manual` event to have none.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_jira_reply_notifications.py`
Expected: FAIL — `ImportError: cannot import name 'NOT_FROM_JIRA'`.

- [ ] **Step 3: Implement** — in `sessions/text.py` add `NOT_FROM_JIRA = 'This event did not come from a Jira connection.'`
next to `NO_ANSWER`, and replace the INSERT in `notify_event_run` with:

```python
    conn.execute(
        """INSERT INTO ka.notifications(project_id,kind,status,title,body,event_id,handler_id,agent_id,session_id,
            jira_status,jira_connection_id,jira_issue_id,jira_issue_key,jira_next_attempt_at,jira_error)
        SELECT s.project_id,'agent.response',%(status)s,left(s.title,500),%(body)s,s.event_id,s.handler_id,s.agent_id,
            s.id,j.status,
            CASE WHEN j.status='pending' THEN e.source_id END,
            CASE WHEN j.status='pending' THEN e.payload->>'issue_id' END,
            CASE WHEN j.status IS NOT NULL THEN e.subject END,
            CASE WHEN j.status='pending' THEN now() END,
            CASE WHEN j.status='failed' THEN %(not_from_jira)s END
        FROM ka.agent_sessions s
        LEFT JOIN ka.event_handlers h ON h.id=s.handler_id
        LEFT JOIN ka.events e ON e.id=s.event_id
        CROSS JOIN LATERAL (SELECT CASE WHEN %(succeeded)s AND h.reply_to_jira AND e.source='jira' THEN
            CASE WHEN e.source_id IS NULL OR e.payload->>'issue_id' IS NULL THEN 'failed' ELSE 'pending' END
            END AS status) j
        WHERE s.id=%(session)s AND s.purpose='event'
          AND NOT EXISTS (SELECT 1 FROM ka.notifications n WHERE n.session_id=s.id AND n.kind='agent.response')""",
        {
            'status': 'succeeded' if succeeded else 'failed',
            'body': body.replace('\x00', '')[:MAX_NOTIFICATION_BODY],
            'not_from_jira': NOT_FROM_JIRA,
            'succeeded': succeeded,
            'session': session_id,
        },
    )
```

Update the docstring's last sentence: `… and a run never gets a second one. A succeeded run whose handler replies to Jira
on a Jira event marks the comment pending (events/jira_replies.py posts it).`

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_jira_reply_notifications.py tests/test_event_run_notifications.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/sessions/text.py services/admin-api/tests/test_jira_reply_notifications.py
git commit -m "feat(events): an event run's notification marks its Jira comment pending"
```

---

### Task 4: `JiraClient.post` and `add_comment`

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/jira/client.py`
- Test: `services/admin-api/tests/test_jira_comments.py`

**Interfaces:**
- Produces: `JiraClient.post(path: str, payload: dict) -> dict` — raises `fastapi.HTTPException` with status 400
  (Jira refused the request), 403 (401/403 from Jira), 404 (not found), 503 (429), 502 (other status, redirect,
  network, invalid JSON), 504 (timeout); `JiraClient.add_comment(issue_id: str, body: str) -> dict` returning
  `{'id': str, 'created': datetime}` (timezone-aware) or raising `HTTPException(502, …)` when the response lacks them.

- [ ] **Step 1: Write the failing tests**

```python
"""JiraClient writes: posting a comment on an issue."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException

from knowledge_admin.jira.client import JiraClient


def client(handler) -> JiraClient:
    return JiraClient('https://jira.internal/jira', 'private-jira-token', httpx.MockTransport(handler))


def test_add_comment_posts_the_body_and_returns_id_and_created():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, method=request.method, body=json.loads(request.content))
        seen['auth'] = request.headers['Authorization']
        return httpx.Response(201, json={'id': '10500', 'created': '2026-10-15T10:00:01.000+0000', 'body': 'x'})

    with client(handler) as jira:
        comment = jira.add_comment('10001', 'The answer')
    assert seen == {
        'path': '/jira/rest/api/2/issue/10001/comment',
        'method': 'POST',
        'body': {'body': 'The answer'},
        'auth': 'Bearer private-jira-token',
    }
    assert comment == {'id': '10500', 'created': datetime(2026, 10, 15, 10, 0, 1, tzinfo=timezone.utc)}


@pytest.mark.parametrize(
    'response,status',
    [
        (httpx.Response(400, json={}), 400),
        (httpx.Response(401), 403),
        (httpx.Response(403), 403),
        (httpx.Response(404), 404),
        (httpx.Response(429), 503),
        (httpx.Response(500), 502),
        (httpx.Response(302, headers={'Location': 'https://sso'}), 502),
        (httpx.Response(201, content=b'not json'), 502),
        (httpx.Response(201, json={'id': '1'}), 502),  # no created
        (httpx.Response(201, json={'created': '2026-10-15T10:00:01.000+0000'}), 502),  # no id
    ],
)
def test_add_comment_failures(response, status):
    with client(lambda request: response) as jira, pytest.raises(HTTPException) as caught:
        jira.add_comment('10001', 'The answer')
    assert caught.value.status_code == status
    assert 'private-jira-token' not in str(caught.value.detail)


def test_a_timeout_is_504():
    def handler(request):
        raise httpx.ReadTimeout('slow', request=request)

    with client(handler) as jira, pytest.raises(HTTPException) as caught:
        jira.add_comment('10001', 'x')
    assert caught.value.status_code == 504
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_jira_comments.py`
Expected: FAIL — `AttributeError: 'JiraClient' object has no attribute 'add_comment'`.

- [ ] **Step 3: Implement** — add to `JiraClient` (after `get`), and `Content-Type` is set by `json=`:

```python
    def post(self, path: str, payload: dict) -> dict:
        """One bounded POST (the first Jira write: posting comments). Errors carry no response body."""
        if time.monotonic() >= self.deadline:
            raise HTTPException(504, 'Jira request timed out')
        try:
            with self.client.stream('POST', path, json=payload) as response:
                if response.status_code in {401, 403}:
                    raise HTTPException(403, "Jira rejected the comment. Check the token user's Add Comments permission")
                if response.status_code == 404:
                    raise HTTPException(404, 'Jira issue not found, or the token user cannot see it')
                if response.status_code == 400:
                    raise HTTPException(400, 'Jira refused the comment')
                if response.status_code == 429:
                    raise HTTPException(503, 'Jira is rate limiting requests. Try again later')
                if response.is_redirect:
                    raise HTTPException(502, 'Jira redirected the request. Use its final base URL')
                if response.status_code not in {200, 201}:
                    raise HTTPException(502, 'Jira could not complete the request')
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise HTTPException(502, 'Jira response is too large')
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError('Expected an object')
                return result
        except httpx.TimeoutException:
            raise HTTPException(504, 'Jira request timed out') from None
        except httpx.HTTPError:
            raise HTTPException(502, 'Could not reach Jira. Check the URL, network access, and TLS certificate') from None
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(502, 'Jira returned an invalid response') from None

    def add_comment(self, issue_id: str, body: str) -> dict:
        """Post `body` as a comment on the issue; returns the comment's id and creation time."""
        result = self.post(f'issue/{issue_id}/comment', {'body': body})
        try:
            created = datetime.fromisoformat(result['created'])
            comment_id = str(result['id'])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(502, 'Jira returned an invalid response') from None
        if created.tzinfo is None or not comment_id:
            raise HTTPException(502, 'Jira returned an invalid response')
        return {'id': comment_id, 'created': created}
```

Note: `HTTPException` raised inside the `try` is not an `httpx.HTTPError` or `ValueError`, so it passes through unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_jira_comments.py tests/test_jira.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/jira/client.py services/admin-api/tests/test_jira_comments.py
git commit -m "feat(jira): JiraClient posts a comment on an issue"
```

---

### Task 5: The delivery loop

**Files:**
- Create: `services/admin-api/src/knowledge_admin/events/jira_replies.py`
- Modify: `services/admin-api/src/knowledge_admin/code_projects/worker.py` (start and join the thread),
  `docs/jira.md` (new section `## Answers as comments`)
- Test: `services/admin-api/tests/test_jira_replies.py`

**Interfaces:**
- Consumes: Task 1 columns, Task 3 pending rows, `JiraClient.add_comment` (Task 4), `jira_sync.credentials(store, project_id) -> (config, token)`.
- Produces: `jira_replies.run_once(store, transport=None) -> bool`, `jira_replies.loop(store, stopped)`,
  `comment_body(answer: str, agent_name: str) -> str`, constants `MAX_COMMENT`, `TRUNCATED`, `CONNECTION_GONE`,
  `UNEXPECTED`, `RETRIES`.

- [ ] **Step 1: Write the failing tests**

```python
"""Repository-worker posts pending Jira replies, one at a time, retrying after 1 and 5 minutes."""

import json
from datetime import timedelta

import httpx
import pytest

from knowledge_admin.events import jira_replies
from knowledge_admin.events.jira_replies import CONNECTION_GONE, MAX_COMMENT, TRUNCATED, UNEXPECTED, comment_body
from test_jira import setup


def connection(store) -> dict:
    with store.connect() as conn:
        return conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()


def pending(store, body='The answer', **values) -> int:
    jira = connection(store)
    row = {
        'project_id': jira['project_id'],
        'kind': 'agent.response',
        'status': 'succeeded',
        'title': 'Triage · jira.ticket.updated DEMO-1',
        'body': body,
        'agent_id': 'triage',
        'jira_status': 'pending',
        'jira_connection_id': jira['id'],
        'jira_issue_id': '10001',
        'jira_issue_key': 'DEMO-1',
        'jira_next_attempt_at': '2000-01-01T00:00:00Z',
        **values,
    }
    with store.connect() as conn:
        return conn.execute(
            f'INSERT INTO ka.notifications({",".join(row)}) VALUES ({",".join(["%s"] * len(row))}) RETURNING id',
            tuple(row.values()),
        ).fetchone()['id']


def note(store, notification_id) -> dict:
    with store.connect() as conn:
        return conn.execute(
            'SELECT *,jira_next_attempt_at-now() AS wait FROM ka.notifications WHERE id=%s', (notification_id,)
        ).fetchone()


def jira(status=201, posted=None):
    def handler(request):
        if posted is not None:
            posted.append(json.loads(request.content)['body'])
        if status == 201:
            return httpx.Response(201, json={'id': '10500', 'created': '2026-10-15T10:00:01.000+0000'})
        return httpx.Response(status)

    return httpx.MockTransport(handler)


def test_comment_body_adds_the_footer_and_cuts_long_answers():
    assert comment_body('Hi', 'Triage') == 'Hi\n\n— Triage (Knowledge Agent)'
    long = comment_body('x' * 40_000, 'Triage')
    assert len(long) <= MAX_COMMENT and long.endswith(TRUNCATED + '\n\n— Triage (Knowledge Agent)')


def test_a_pending_reply_is_posted(store, tmp_path):
    setup(store, tmp_path)
    nid, posted = pending(store), []
    assert jira_replies.run_once(store, jira(posted=posted)) is True
    row = note(store, nid)
    assert (row['jira_status'], row['jira_comment_id'], row['jira_attempts'], row['jira_next_attempt_at']) == (
        'posted',
        '10500',
        1,
        None,
    )
    assert row['jira_comment_created'].isoformat() == '2026-10-15T10:00:01+00:00'
    assert posted == ['The answer\n\n— triage (Knowledge Agent)']  # agent_id when the agent row is gone
    assert jira_replies.run_once(store, jira()) is False


def test_retries_after_one_and_five_minutes_then_fails(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store)
    for attempt, wait in ((1, timedelta(minutes=1)), (2, timedelta(minutes=5))):
        assert jira_replies.run_once(store, jira(500)) is True
        row = note(store, nid)
        assert (row['jira_status'], row['jira_attempts']) == ('pending', attempt)
        assert wait - timedelta(seconds=5) < row['wait'] <= wait
        assert row['jira_error'] == 'Jira could not complete the request'
        with store.connect() as conn:
            conn.execute("UPDATE ka.notifications SET jira_next_attempt_at=now()-interval '1 second' WHERE id=%s", (nid,))
    assert jira_replies.run_once(store, jira(500)) is True
    row = note(store, nid)
    assert (row['jira_status'], row['jira_attempts'], row['jira_next_attempt_at']) == ('failed', 3, None)


@pytest.mark.parametrize('status', [400, 403, 404])
def test_a_refusal_fails_at_once(store, tmp_path, status):
    setup(store, tmp_path)
    nid = pending(store)
    jira_replies.run_once(store, jira(status))
    row = note(store, nid)
    assert (row['jira_status'], row['jira_attempts']) == ('failed', 1)


def test_a_missing_or_replaced_connection_fails_at_once(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store, jira_connection_id='00000000-0000-0000-0000-000000000001')
    jira_replies.run_once(store, jira())
    assert (note(store, nid)['jira_status'], note(store, nid)['jira_error']) == ('failed', CONNECTION_GONE)


def test_a_disabled_connection_fails_at_once_with_the_settings_message(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store)
    with store.connect() as conn:
        conn.execute('UPDATE ka.jira_connections SET enabled=false')
    jira_replies.run_once(store, jira())
    row = note(store, nid)
    assert (row['jira_status'], row['jira_error']) == ('failed', 'This Jira Data Center connection is disabled or unsupported')


def test_an_invalid_jira_response_is_retried(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store)
    jira_replies.run_once(store, httpx.MockTransport(lambda request: httpx.Response(201, json={'id': '1'})))
    assert note(store, nid)['jira_status'] == 'pending'


def test_a_row_locked_by_another_worker_is_skipped(store, tmp_path):
    setup(store, tmp_path)
    nid = pending(store)
    with store.connect() as other:
        other.execute('SELECT id FROM ka.notifications WHERE id=%s FOR UPDATE', (nid,))
        assert jira_replies.run_once(store, jira()) is False
    assert jira_replies.run_once(store, jira()) is True


def test_an_unexpected_error_fails_the_row_and_the_loop_goes_on(store, tmp_path, monkeypatch):
    setup(store, tmp_path)
    nid = pending(store)
    monkeypatch.setattr(jira_replies, 'comment_body', lambda *_: 1 / 0)
    assert jira_replies.run_once(store, jira()) is True
    assert (note(store, nid)['jira_status'], note(store, nid)['jira_error']) == ('failed', UNEXPECTED)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_jira_replies.py`
Expected: FAIL — `ModuleNotFoundError: knowledge_admin.events.jira_replies`.

- [ ] **Step 3: Implement** `services/admin-api/src/knowledge_admin/events/jira_replies.py`:

```python
"""Event run answers posted as Jira comments. A thread in repository-worker takes one pending notification at a time
(FOR UPDATE SKIP LOCKED: several workers never post the same comment), posts its answer on the ticket with the
connection's token and records the comment, or why it could not: retried after 1 and 5 minutes, failed after the
third attempt; Jira refusing the comment or a missing connection fails at once."""

import logging
import threading
from datetime import timedelta

import httpx
import psycopg
from fastapi import HTTPException

from knowledge_admin.jira import sync as jira_sync
from knowledge_admin.jira.client import JiraClient
from knowledge_admin.repositories import RepositoryStore

logger = logging.getLogger(__name__)

POLL_SECONDS = 5
MAX_COMMENT = 32_000
TRUNCATED = '… (truncated; the full answer is in Knowledge Agent)'
RETRIES = (timedelta(minutes=1), timedelta(minutes=5))  # the waits after attempts 1 and 2; attempt 3 is the last
REFUSED = {400, 403, 404}  # Jira refused the comment: trying again cannot help
CONNECTION_GONE = 'The Jira connection that recorded this event no longer exists.'
UNEXPECTED = 'Posting to Jira failed unexpectedly. See the repository-worker log.'

CLAIM = """SELECT n.id,n.project_id,n.body,n.jira_connection_id,n.jira_issue_id,n.jira_attempts,
    coalesce(a.name,n.agent_id,'Agent') AS agent_name
    FROM ka.notifications n LEFT JOIN ka.agents a ON a.id=n.agent_id
    WHERE n.jira_status='pending' AND n.jira_next_attempt_at<=now()
    ORDER BY n.jira_next_attempt_at,n.id LIMIT 1 FOR UPDATE OF n SKIP LOCKED"""


class Failed(Exception):
    def __init__(self, message: str, final: bool):
        super().__init__(message)
        self.message, self.final = message, final


def comment_body(answer: str, agent_name: str) -> str:
    footer = f'\n\n— {agent_name} (Knowledge Agent)'
    if len(answer) + len(footer) > MAX_COMMENT:
        answer = answer[: MAX_COMMENT - len(footer) - len(TRUNCATED) - 1].rstrip() + '\n' + TRUNCATED
    return answer + footer


def post(store: RepositoryStore, row: dict, transport: httpx.BaseTransport | None) -> dict:
    with store.connect() as conn:
        current = conn.execute(
            "SELECT id FROM ka.jira_connections WHERE project_id=%s AND name='primary'", (row['project_id'],)
        ).fetchone()
    if current is None or current['id'] != row['jira_connection_id']:
        raise Failed(CONNECTION_GONE, final=True)
    try:
        config, token = jira_sync.credentials(store, row['project_id'])
    except HTTPException as exc:  # 400: settings incomplete or disabled; 503: the token cannot be read
        raise Failed(str(exc.detail), final=exc.status_code == 400) from None
    try:
        with JiraClient(config['base_url'], token, transport) as client:
            return client.add_comment(row['jira_issue_id'], comment_body(row['body'], row['agent_name']))
    except HTTPException as exc:
        raise Failed(str(exc.detail), final=exc.status_code in REFUSED) from None


def run_once(store: RepositoryStore, transport: httpx.BaseTransport | None = None) -> bool:
    """Post one due reply; False when none is due."""
    with store.connect() as conn:
        row = conn.execute(CLAIM).fetchone()
        if row is None:
            return False
        attempt = row['jira_attempts'] + 1
        try:
            comment = post(store, row, transport)
        except Failed as exc:
            if exc.final or attempt > len(RETRIES):
                conn.execute(
                    """UPDATE ka.notifications SET jira_status='failed',jira_attempts=%s,jira_error=%s,
                    jira_next_attempt_at=NULL WHERE id=%s""",
                    (attempt, exc.message[:2000], row['id']),
                )
            else:
                conn.execute(
                    """UPDATE ka.notifications SET jira_attempts=%s,jira_error=%s,jira_next_attempt_at=now()+%s
                    WHERE id=%s""",
                    (attempt, exc.message[:2000], RETRIES[attempt - 1], row['id']),
                )
            return True
        except Exception:
            if conn.broken or conn.closed:
                raise
            logger.exception('Posting notification %s to Jira failed', row['id'])
            conn.execute(
                """UPDATE ka.notifications SET jira_status='failed',jira_attempts=%s,jira_error=%s,
                jira_next_attempt_at=NULL WHERE id=%s""",
                (attempt, UNEXPECTED, row['id']),
            )
            return True
        conn.execute(
            """UPDATE ka.notifications SET jira_status='posted',jira_attempts=%s,jira_comment_id=%s,
            jira_comment_created=%s,jira_next_attempt_at=NULL,jira_error=NULL WHERE id=%s""",
            (attempt, comment['id'], comment['created'], row['id']),
        )
    return True


def loop(store: RepositoryStore, stopped: threading.Event) -> None:
    while not stopped.is_set():
        try:
            while not stopped.is_set() and run_once(store):
                pass
        except psycopg.Error:
            print('Jira replies database unavailable; retrying', flush=True)
        except Exception:
            logger.exception('Jira reply loop failed; retrying')
        stopped.wait(POLL_SECONDS)
```

In `code_projects/worker.py`: import `jira_replies` with the other `knowledge_admin.events` imports
(`from knowledge_admin.events import jira_replies, processor as event_processor, scheduler, templates as event_templates`);
after `events.start()` add

```python
    # Answers that handlers send to Jira are posted as comments every 5 s (jira_replies.py).
    replies = threading.Thread(target=jira_replies.loop, args=(store, stopped), name='jira-replies')
    replies.start()
```

and `replies.join()` after `events.join()` in the `finally`.

Append to `docs/jira.md`:

```markdown
## Answers as comments

A Send to agent handler with **Also post the answer as a comment on the Jira ticket** posts the agent's answer on the
ticket of a Jira event (events from the scheduled pull; simulated events from Events → New event cannot be answered).
The answer is posted as text (Jira renders wiki markup; Markdown is not converted) with the footer
`— <agent name> (Knowledge Agent)`, cut to 32,000 characters. Only answers are posted; a failed run is not.

The comment is posted by repository-worker after the run's first turn ends, as the user of the Jira connection's
personal access token, which needs the **Add Comments** permission in the ticket's project. A failed post is tried
again after 1 and 5 minutes; Jira refusing it (no permission, issue gone) or a deleted or replaced connection fails
at once. The notification shows **Posted to KEY** (a link to the comment), **Posting to KEY…** or **Not posted to KEY**
with the reason.

Posting a comment moves the ticket's updated time. The next scheduled pull records no `jira.ticket.updated` for a
ticket whose only change is our own comment (no compared field changed and the ticket was updated within 2 seconds of
the comment), so a handler never answers itself. A person's comment within those 2 seconds is missed too.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_jira_replies.py tests/test_event_processor.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/events/jira_replies.py services/admin-api/src/knowledge_admin/code_projects/worker.py docs/jira.md services/admin-api/tests/test_jira_replies.py
git commit -m "feat(events): repository-worker posts handler answers as Jira comments, with retries"
```

---

### Task 6: Loop guard in the Jira pull

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/jira/events.py` (`record`, new `own_comment`)
- Test: `services/admin-api/tests/test_jira_events.py`

**Interfaces:**
- Consumes: posted notifications (Task 1/5 columns).
- Produces: `own_comment(conn, project_id, connection_id, issue_id: str, before_updated: str, after_updated: str) -> bool`; `OWN_COMMENT_WINDOW = timedelta(seconds=2)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_jira_events.py`)

```python
def posted_comment(store, issue, created: str) -> None:
    with store.connect() as conn:
        jira = conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()
        conn.execute(
            """INSERT INTO ka.notifications(project_id,kind,status,title,body,jira_status,jira_connection_id,
            jira_issue_id,jira_issue_key,jira_comment_id,jira_comment_created)
            VALUES (%s,'agent.response','succeeded','T','A','posted',%s,%s,%s,'10500',%s)""",
            (jira['project_id'], jira['id'], issue['id'], issue['key'], created),
        )


def test_an_update_that_is_only_our_comment_records_no_event(store, tmp_path):
    setup(store, tmp_path)
    issues = copy.deepcopy(ISSUES[:2])
    auto_pull(store, a_schedule(store), transport=jira(issues))
    issues[0]['fields']['updated'] = '2026-10-15T10:00:01.400+0000'
    posted_comment(store, issues[0], '2026-10-15T10:00:01Z')
    assert auto_pull(store, a_schedule(store), transport=jira(issues))[1].endswith('0 new events.')
    assert auto_pull(store, a_schedule(store), transport=jira(issues))[1].endswith('0 new events.')  # stays skipped


def test_a_field_change_with_our_comment_is_recorded(store, tmp_path):
    setup(store, tmp_path)
    issues = copy.deepcopy(ISSUES[:2])
    auto_pull(store, a_schedule(store), transport=jira(issues))
    issues[0]['fields']['updated'] = '2026-10-15T10:00:01.400+0000'
    issues[0]['fields']['status'] = {'id': '3', 'name': 'Done'}
    posted_comment(store, issues[0], '2026-10-15T10:00:01Z')
    assert auto_pull(store, a_schedule(store), transport=jira(issues))[1].endswith('1 new events.')


def test_a_later_comment_by_a_person_is_recorded(store, tmp_path):
    setup(store, tmp_path)
    issues = copy.deepcopy(ISSUES[:2])
    auto_pull(store, a_schedule(store), transport=jira(issues))
    posted_comment(store, issues[0], '2026-10-15T10:00:01Z')
    issues[0]['fields']['updated'] = '2026-10-15T10:05:00.000+0000'  # minutes after ours: someone else commented
    assert auto_pull(store, a_schedule(store), transport=jira(issues))[1].endswith('1 new events.')


def test_an_old_comment_of_ours_does_not_hide_a_new_update(store, tmp_path):
    setup(store, tmp_path)
    issues = copy.deepcopy(ISSUES[:2])
    posted_comment(store, issues[0], issues[0]['fields']['updated'])  # before the recorded state
    auto_pull(store, a_schedule(store), transport=jira(issues))
    issues[0]['fields']['updated'] = '2026-10-15T10:00:01.000+0000'
    assert auto_pull(store, a_schedule(store), transport=jira(issues))[1].endswith('1 new events.')
```

Check the exact summary text `auto_pull` returns (`'Pulled 2 of 2 tickets · 0 new events.'`) and keep the
`endswith` assertions in line with it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_jira_events.py -k "comment"`
Expected: FAIL on `test_an_update_that_is_only_our_comment_records_no_event` (`1 new events.`).

- [ ] **Step 3: Implement** in `jira/events.py` (add `from datetime import datetime, timedelta`):

```python
OWN_COMMENT_WINDOW = timedelta(seconds=2)


def own_comment(conn, project_id: UUID, connection_id: UUID, issue_id: str, before_updated: str, after_updated: str) -> bool:
    """The update is our own comment (events/jira_replies.py): we posted one on the issue after the recorded state and
    the ticket was updated within 2 seconds of it. Recording it would make a handler answer its own comment."""
    created = conn.execute(
        """SELECT max(jira_comment_created) AS created FROM ka.notifications
        WHERE project_id=%s AND jira_status='posted' AND jira_connection_id=%s AND jira_issue_id=%s""",
        (project_id, connection_id, issue_id),
    ).fetchone()['created']
    if created is None or created <= datetime.fromisoformat(before_updated):
        return False
    return abs(datetime.fromisoformat(after_updated) - created) <= OWN_COMMENT_WINDOW
```

In `record`, replace the payload line's computation with:

```python
        diff = {} if before is None else changes(before, after)
        if before is not None and not diff and own_comment(
            conn, project_id, connection_id, issue['id'], before['updated'], after['updated']
        ):
            continue
        payload = {**after, 'changes': diff}
```

Add one sentence to the module docstring: `An update that is only our own comment (events/jira_replies.py) is not
recorded.`

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_jira_events.py tests/test_jira_sync.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/jira/events.py services/admin-api/tests/test_jira_events.py
git commit -m "feat(jira): the pull skips an update that is only our own comment"
```

---

### Task 7: Notifications API, handler form, notifications view, user guide

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/events/notifications_api.py`,
  `services/admin-ui/app/features/events/handlers.ts`, `services/admin-ui/app/features/events/HandlerSheet.tsx`,
  `services/admin-ui/app/features/events/notifications.ts`, `services/admin-ui/app/features/events/NotificationsView.tsx`,
  `docs/user-guide.md` (4.18 Send to agent bullet, 4.19 Notifications)
- Test: `services/admin-api/tests/test_notifications_api.py`, `services/admin-ui/app/components/__tests__/handlers.test.tsx`,
  `services/admin-ui/app/components/__tests__/notifications.test.tsx`

**Interfaces:**
- Consumes: `reply_to_jira` on handlers (Task 2); `jira_*` columns (Task 1).
- Produces: list rows gain `jira_status`, `jira_issue_key`; the detail also gains `jira_comment_id`, `jira_attempts`,
  `jira_error`, `jira_base_url` (`ka.jira_connections.base_url` of `jira_connection_id`, null when gone).

- [ ] **Step 1: Write the failing backend test** (append to `tests/test_notifications_api.py`, reusing its client and
  insert helpers; read the file's top for their names)

```python
def test_a_notification_shows_its_jira_comment(store, tmp_path):
    from test_jira import setup

    client, _ = setup(store, tmp_path)
    with store.connect() as conn:
        jira = conn.execute('SELECT id,project_id FROM ka.jira_connections').fetchone()
        nid = conn.execute(
            """INSERT INTO ka.notifications(project_id,kind,status,title,body,jira_status,jira_connection_id,
            jira_issue_id,jira_issue_key,jira_comment_id,jira_comment_created,jira_attempts)
            VALUES (%s,'agent.response','succeeded','T','A','posted',%s,'10001','DEMO-1','10500',now(),1) RETURNING id""",
            (jira['project_id'], jira['id']),
        ).fetchone()['id']
    [row] = client.get('/api/notifications', headers=HEADERS).json()['notifications']
    assert (row['jira_status'], row['jira_issue_key']) == ('posted', 'DEMO-1')
    detail = client.get(f'/api/notifications/{nid}', headers=HEADERS).json()['notification']
    assert (detail['jira_comment_id'], detail['jira_attempts'], detail['jira_error'], detail['jira_base_url']) == (
        '10500',
        1,
        None,
        'https://jira.internal/jira',
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --frozen pytest -q -p no:warnings tests/test_notifications_api.py -k jira`
Expected: FAIL — `KeyError: 'jira_status'`.

- [ ] **Step 3: Implement the API** in `notifications_api.py`:
  - `LISTED`: append `,n.jira_status,n.jira_issue_key`.
  - Detail SELECT: after `n.body,n.handler_id,` add `n.jira_comment_id,n.jira_attempts,n.jira_error,c.base_url AS jira_base_url,`
    and add `LEFT JOIN ka.jira_connections c ON c.id=n.jira_connection_id` after the events join.
  - Module docstring: add `A notification also says whether its answer was posted to Jira (events/jira_replies.py).`

Run: `uv run --frozen pytest -q -p no:warnings tests/test_notifications_api.py` — Expected: PASS.

- [ ] **Step 4: Write the failing UI tests**

In `handlers.test.tsx`, extend `test("send to agent picks an agent and a prompt; …")`: before Save add
`await user.click(within(sheet).getByRole("checkbox", { name: "Also post the answer as a comment on the Jira ticket" }));`
and change the final assertion to
`expect([sent.action, sent.agent_id, sent.prompt, sent.emit, sent.reply_to_jira]).toEqual(["agent", "tester", "Triage {{ event.subject", null, true]);`.
Add `reply_to_jira: false` to the `handler()` fixture. In `creating an emit handler with a condition sends the draft`,
add `expect(within(sheet).queryByRole("checkbox", { name: /Jira ticket/ })).toBeNull();` before the Save click and
`reply_to_jira: false,` to the expected draft (after `prompt: null,`).

In `notifications.test.tsx` add (each state opened through `?id=`, as `an address with ?id= opens that notification` does):

```tsx
const JIRA = { jira_base_url: "https://jira.internal/jira", jira_attempts: 1, jira_comment_id: null, jira_error: null };
test.each([
  [{ jira_status: "posted", jira_issue_key: "DEMO-3", jira_comment_id: "10500" }, "Posted to DEMO-3"],
  [
    { jira_status: "pending", jira_issue_key: "DEMO-3", jira_error: "Jira could not complete the request" },
    "Posting to DEMO-3… (attempt 1 failed: Jira could not complete the request)",
  ],
  [{ jira_status: "pending", jira_issue_key: "DEMO-3", jira_attempts: 0 }, "Posting to DEMO-3…"],
  [{ jira_status: "failed", jira_issue_key: "DEMO-3", jira_error: "Jira refused the comment" }, "Not posted to DEMO-3: Jira refused the comment"],
])("the detail shows the Jira comment's state %#", async (jira, text) => {
  api({
    "/api/notifications": { notifications: [], next_offset: null },
    "/api/notifications/3": { notification: detail(3, { ...JIRA, ...jira }) },
  });
  view("/notifications?id=3");
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByText(text)).toBeInTheDocument();
});

test("a posted comment links to it on Jira", async () => {
  api({
    "/api/notifications": { notifications: [], next_offset: null },
    "/api/notifications/3": {
      notification: detail(3, { ...JIRA, jira_status: "posted", jira_issue_key: "DEMO-3", jira_comment_id: "10500" }),
    },
  });
  view("/notifications?id=3");
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByRole("link", { name: "Posted to DEMO-3" })).toHaveAttribute(
    "href",
    "https://jira.internal/jira/browse/DEMO-3?focusedCommentId=10500",
  );
});

test("a notification without a Jira comment shows no Jira line", async () => {
  api({
    "/api/notifications": { notifications: [], next_offset: null },
    "/api/notifications/4": { notification: detail(4) },
  });
  view("/notifications?id=4");
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).queryByText("Jira")).toBeNull();
});
```

Run: `pnpm vitest run app/components/__tests__/handlers.test.tsx app/components/__tests__/notifications.test.tsx`
Expected: FAIL (no checkbox; no Jira line).

- [ ] **Step 5: Implement the UI**

`handlers.ts`: `HandlerDraft` gains `reply_to_jira: boolean;`; `toDraft` copies `reply_to_jira: handler.reply_to_jira`.

`HandlerSheet.tsx`:
- import `{ Checkbox } from "~/components/ui/checkbox"`;
- `Form` gains `reply_to_jira: boolean`; `BLANK.reply_to_jira = false`; `fromHandler` copies `handler.reply_to_jira`;
  `toDraft` sends `reply_to_jira: form.action === "agent" && form.reply_to_jira`;
- after the Prompt `Field` in the agent branch:

```tsx
            <Field orientation="horizontal">
              <Checkbox
                id="handler-reply-to-jira"
                checked={form.reply_to_jira}
                onCheckedChange={(checked) => update({ reply_to_jira: checked === true })}
              />
              <div>
                <FieldLabel htmlFor="handler-reply-to-jira">
                  Also post the answer as a comment on the Jira ticket
                </FieldLabel>
                <FieldDescription>
                  Jira events only. Posted as the Jira connection&apos;s user, which needs the Add Comments permission.
                </FieldDescription>
              </div>
            </Field>
```

`notifications.ts`: `NotificationRow` gains `jira_status?: "pending" | "posted" | "failed" | null; jira_issue_key?: string | null;`;
`NotificationDetail` gains `jira_comment_id?: string | null; jira_attempts?: number; jira_error?: string | null; jira_base_url?: string | null;`.

`NotificationsView.tsx`: in the detail `<dl>`, after the Event block:

```tsx
                  {selected.jira_status && (
                    <>
                      <dt className="text-muted-foreground">Jira</dt>
                      <dd>
                        {selected.jira_status === "posted" && selected.jira_base_url ? (
                          <a
                            className="text-primary underline underline-offset-4"
                            href={`${selected.jira_base_url}/browse/${encodeURIComponent(selected.jira_issue_key ?? "")}?focusedCommentId=${encodeURIComponent(selected.jira_comment_id ?? "")}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Posted to {selected.jira_issue_key}
                          </a>
                        ) : selected.jira_status === "posted" ? (
                          `Posted to ${selected.jira_issue_key}`
                        ) : selected.jira_status === "pending" ? (
                          selected.jira_error ? (
                            `Posting to ${selected.jira_issue_key}… (attempt ${selected.jira_attempts} failed: ${selected.jira_error})`
                          ) : (
                            `Posting to ${selected.jira_issue_key}…`
                          )
                        ) : (
                          `Not posted to ${selected.jira_issue_key}: ${selected.jira_error}`
                        )}
                      </dd>
                    </>
                  )}
```

Run: `pnpm vitest run app/components/__tests__/handlers.test.tsx app/components/__tests__/notifications.test.tsx && pnpm typecheck`
Expected: PASS

- [ ] **Step 6: User guide** — in `docs/user-guide.md` 4.18, after the Send to agent sentence `… writes the answer, or why
it failed (4.19).` add: `With **Also post the answer as a comment on the Jira ticket**, the answer to a Jira event is
also posted on its ticket as the Jira connection's user (see the Jira docs for the permission it needs); a failed run
is not posted.` In 4.19, after the sentence about **Open event**, add: `A notification whose answer goes to Jira shows
**Posted to KEY** (opens the comment), **Posting to KEY…** while it is being posted or retried, or **Not posted to
KEY** with the reason.`

- [ ] **Step 7: Format and commit**

```bash
cd services/admin-ui && pnpm exec prettier --write app/features/events/handlers.ts app/features/events/HandlerSheet.tsx app/features/events/notifications.ts app/features/events/NotificationsView.tsx app/components/__tests__/handlers.test.tsx app/components/__tests__/notifications.test.tsx && cd -
git add services/admin-api/src/knowledge_admin/events/notifications_api.py services/admin-api/tests/test_notifications_api.py services/admin-ui/app/features/events services/admin-ui/app/components/__tests__/handlers.test.tsx services/admin-ui/app/components/__tests__/notifications.test.tsx docs/user-guide.md
git commit -m "feat(admin-ui): handler option to post on Jira; notifications show the comment's state"
```

- [ ] **Step 8: Full suites**

Run: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (only the known failures) and
`cd services/admin-ui && pnpm vitest run && pnpm typecheck`.
