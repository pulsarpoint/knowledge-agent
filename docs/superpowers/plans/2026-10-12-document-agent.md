# Ask agent on a document Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On a document's page a person starts an agent run ("Ask agent") that reads the document with `get_doc` and
changes it only through `save_doc` / `edit_doc`, which write the run's draft; the person promotes it.

**Architecture:** A run is an ad-hoc agent session with `purpose='revision'` and a `target_document_id` (migration
0099). Three tools that exist only in revision runs live in `capabilities/revision.py` (the pattern of
`capabilities/discovery.py`). Runs start through a new universal router `/agent-sessions` (options, preview, start,
list) in `agent_launch.py`; messages, Stop, Close and events stay on `/generations/sessions/{id}/…`. The UI adds an
Ask agent sheet and an Agent runs tab to the document page and a run page at `/documents/:documentId/agent/:runId`.

**Tech Stack:** FastAPI + psycopg 3 + pydantic 2 (admin-api), PostgreSQL migrations (golang-migrate files), React
Router 7 + shadcn/ui + vitest (admin-ui, pnpm).

**Spec:** `docs/superpowers/specs/2026-10-12-document-agent-design.md` (builds on
`docs/superpowers/specs/2026-10-12-document-drafts-design.md`).

## Global Constraints

- Work only in the worktree `.claude/worktrees/document-agent` on branch `feature/document-agent`.
- Never run `make` targets. Never touch `.env` or anything under `.local`. Never `git stash`. Never touch the running
  Docker stack or its database (backend tests use their own disposable PostgreSQL through the `store` fixture). Do not
  run `db/tests/run.sh` (it starts Docker containers); check it with `bash -n db/tests/run.sh` only.
- Never `git add .superpowers`; never commit `node_modules` or `services/admin-ui/build`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd services/admin-api && uv run pytest -q <files>`. UI: `cd services/admin-ui && npx vitest run
  <files>`, `npx tsc --noEmit` and `npx react-router routes >/dev/null` (if `node_modules` is missing, run
  `pnpm install --frozen-lockfile` there — pnpm, not npm). If the `store` fixture fails with a docker-port
  `IndexError`, re-run once; it is a known flake.
- No new dependencies (Python or npm).
- Error bodies stay `{"error": {"message": ...}}`; request-validation errors are 400, not 422.
- The endpoint prefix is `/agent-sessions` (the spec's universal endpoint; `/agent-runs` already lists run records).
- Lock order for a draft save: the session row `FOR UPDATE`, then the draft `FOR UPDATE` (the worker's and promote's
  order). A tool never locks `ka.projects` or `ka.documents` rows.
- Credentials stay references in every saved or previewed configuration.
- Tool messages, exactly:
  - `This document no longer exists.`
  - `This run is closed.`
  - `This text is not in the document. Read it again with get_doc.`
  - `This text appears {n} times. Include more of the surrounding text.`
  - `No heading named "{name}".`
  - `Your earlier draft was promoted; this is a new draft based on version {version}.`
  - `Your earlier draft was discarded; this is a new draft based on version {version}.`
  - `A person edited your earlier draft; this is a new draft, and get_doc now reads it.`
  - `Saved draft ({n} lines).`
- Start refusals, exactly: `{tool} needs {what}.` (what: `a code project or a collection`, `a code project`,
  `a collection`, `at least one server`); `Unknown tool: {names}.`; `This expert is not available: {id}.`;
  `Server {name}: SSH is not set up for this server.`; `Server {name}: SSH is not allowed for this server.`;
  `Choose an enabled model.`; `Document not found` (404).
- UI texts, exactly: button `Ask agent`; sheet title `Ask agent`; buttons `Preview`, `Start`; tab `Agent runs`;
  run-page note `No draft saved in this turn`.

## Review Focus

1. Promoting a draft written by a revision run: `promote_draft` sets `agent_sessions.document_id`, which the
   `agent_sessions_cc_documentation` check forbids for non-`documents` sessions — promote must still succeed (Task 2
   test `test_promoting_a_revision_draft_leaves_the_run_alone`).
2. Two saves in one model response (parallel tool calls): they must serialize on the session lock and end in one
   draft, not two (Task 2 test `test_parallel_saves_end_in_one_draft`).
3. A heading inside a fenced code block (` ``` `) must not count as a heading for the outline or `section` (Task 2
   test `test_get_doc_reads_the_outline_a_section_and_lines`).
4. A selected server whose SSH is not set up (no key) — `resolve` accepts it, so Start must refuse it itself (Task 3
   test `test_start_refuses_what_cannot_run`).
5. Deleting a document that has runs through the real API path (`DELETE /documentation/documents/{id}`) must delete
   the runs and keep the drafts' `session_id` nulled, not fail on a foreign key (Task 3 test
   `test_deleting_the_document_deletes_its_runs`).

---

### Task 1: Migration 0099 — revision sessions

**Files:**
- Create: `db/migrations/0099_document_agent.up.sql`, `db/migrations/0099_document_agent.down.sql`
- Create: `services/admin-api/tests/test_document_agent_migration.py`
- Modify: `db/tests/run.sh` (a down step and a PASS line, beside the 0098 ones), `docs/migrations.md`,
  `docs/database.md`

**Interfaces:**
- Produces: `ka.agent_sessions.target_document_id uuid` (set exactly when `purpose='revision'`; FK
  `(target_document_id, project_id) → documents(id, project_id) ON DELETE CASCADE`); `purpose` accepts `revision`;
  an ad-hoc (`agent_id IS NULL`) session may be `revision`; index `agent_sessions_revision`.

- [ ] **Step 1: Write the failing test**

`services/admin-api/tests/test_document_agent_migration.py`:

```python
"""0099: an Ask agent run is an agent session (purpose revision) aimed at one document, and goes with it."""
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from knowledge_admin.documents import DocumentInput, save_document
from session_rows import a_model

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0099_document_agent.up.sql', '0099_document_agent.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def world(conn, store):
    project = store.project(conn, 'shop')
    runbook = save_document(conn, project, None, DocumentInput(slug='runbook', title='Runbook', markdown='# Runbook'))
    return project, runbook['id']


def session(conn, project, document, purpose='revision'):
    sid = uuid4()
    actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
    conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
        context_snapshot,purpose,target_document_id,agent_config) VALUES (%s,%s,NULL,%s,%s,'Revise','{}','{}',%s,%s,%s)""",
        (sid, project, actor, a_model(conn, actor), purpose, document, Jsonb({})))
    return sid


def test_a_revision_run_needs_its_document_and_other_sessions_have_none(store):
    with store.connect() as conn:
        project, runbook = world(conn, store)
        session(conn, project, runbook)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            session(conn, project, None)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            session(conn, project, runbook, purpose='test')


def test_a_revision_run_goes_with_its_document_and_leaves_its_drafts(store):
    with store.connect() as conn:
        project, runbook = world(conn, store)
        sid = session(conn, project, runbook)
        conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) SELECT %s,1,actor_id,%s FROM ka.agent_sessions WHERE id=%s',
                     (sid, 'Add a section.', sid))
        conn.execute('DELETE FROM ka.documents WHERE id=%s', (runbook,))
        assert conn.execute('SELECT count(*) AS n FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()['n'] == 0


def test_down_refuses_while_revision_runs_exist_then_round_trips(store):
    with store.connect() as conn:
        project, runbook = world(conn, store)
        sid = session(conn, project, runbook)
        conn.commit()  # the migration files carry their own BEGIN/COMMIT
        with pytest.raises(psycopg.errors.RaiseException, match='Ask agent runs exist'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute('DELETE FROM ka.agent_sessions WHERE id=%s', (sid,))
        conn.commit()
        run(conn, DOWN)
        assert not conn.execute("""SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='ka'
            AND table_name='agent_sessions' AND column_name='target_document_id') AS present""").fetchone()['present']
        run(conn, UP)
        session(conn, project, runbook)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_document_agent_migration.py`
Expected: FAIL (`column "target_document_id" … does not exist` / missing migration file).

- [ ] **Step 3: Write the migrations**

`db/migrations/0099_document_agent.up.sql`:

```sql
BEGIN;
SET search_path = ka, public;

-- Ask agent on a document: an ad-hoc agent session (purpose revision) revises one document through the document tools
-- (get_doc, save_doc, edit_doc), which save the run's drafts. The run goes with its document.
ALTER TABLE agent_sessions
    ADD COLUMN target_document_id uuid,
    ADD CONSTRAINT agent_sessions_target_document FOREIGN KEY (target_document_id, project_id)
        REFERENCES documents(id, project_id) ON DELETE CASCADE,
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event', 'discovery', 'revision')),
    DROP CONSTRAINT agent_sessions_ad_hoc,
    ADD CONSTRAINT agent_sessions_ad_hoc CHECK (agent_id IS NOT NULL OR purpose IN ('documents', 'discovery', 'revision')),
    ADD CONSTRAINT agent_sessions_revision_document CHECK ((purpose = 'revision') = (target_document_id IS NOT NULL));
CREATE INDEX agent_sessions_revision ON agent_sessions(target_document_id, last_activity_at DESC)
    WHERE target_document_id IS NOT NULL;
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent); discovery: a server discovery run (server_id); revision: an Ask agent run on a document (target_document_id).';
COMMENT ON COLUMN agent_sessions.target_document_id IS 'The document an Ask agent run revises; set exactly when purpose is revision.';

COMMIT;
```

`db/migrations/0099_document_agent.down.sql`:

```sql
BEGIN;
SET search_path = ka, public;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agent_sessions WHERE purpose = 'revision') THEN
        RAISE EXCEPTION 'Ask agent runs exist; delete them before rollback (their drafts stay)';
    END IF;
END $$;
DROP INDEX agent_sessions_revision;
ALTER TABLE agent_sessions
    DROP CONSTRAINT agent_sessions_revision_document,
    DROP CONSTRAINT agent_sessions_ad_hoc,
    ADD CONSTRAINT agent_sessions_ad_hoc CHECK (agent_id IS NOT NULL OR purpose IN ('documents', 'discovery')),
    DROP CONSTRAINT agent_sessions_purpose,
    ADD CONSTRAINT agent_sessions_purpose CHECK (purpose IN ('documents', 'test', 'event', 'discovery')),
    DROP CONSTRAINT agent_sessions_target_document,
    DROP COLUMN target_document_id;
COMMENT ON COLUMN agent_sessions.purpose IS
    'documents: a Generate documents session (document name, folder, draft); test: an agent Test tab session; event: started by an event handler (Send to agent); discovery: a server discovery run (server_id).';

COMMIT;
```

- [ ] **Step 4: Record it in run.sh and the docs**

In `db/tests/run.sh`, the down sequence currently starts with the 0098 step (`run_migrate postgres down 1` then
`drafts_removed=…`). Insert before it:

```bash
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
agent_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ka'
        AND table_name = 'agent_sessions' AND column_name = 'target_document_id')")
[ "$agent_removed" = t ] || { echo 'FAIL: 0099 down left Ask agent runs' >&2; exit 1; }
```

and after the `PASS: document drafts, sources and links exist.` line:

```bash
agent=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'ka'
        AND table_name = 'agent_sessions' AND column_name = 'target_document_id')")
[ "$agent" = t ] || { echo 'FAIL: 0099 Ask agent runs missing' >&2; exit 1; }
printf '%s\n' 'PASS: Ask agent runs name their document.'
```

Check: `bash -n db/tests/run.sh`.

`docs/migrations.md`, after the `## 0098_document_drafts` section:

```markdown
## 0099_document_agent

`ka.agent_sessions` gets `purpose = 'revision'` (an Ask agent run on a document, ad hoc like discovery) and
`target_document_id`, set exactly for those runs; deleting the document deletes its runs (their drafts stay, with
`session_id` cleared). The down migration refuses while revision runs exist.
```

`docs/database.md`: in the `agent_sessions` row of the table list, add "`revision` runs (Ask agent on a document,
`target_document_id`)" to the purposes it describes (keep the row's existing wording otherwise).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_document_agent_migration.py tests/test_discovery_migration.py tests/test_document_drafts_migration.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/0099_document_agent.up.sql db/migrations/0099_document_agent.down.sql db/tests/run.sh \
  docs/migrations.md docs/database.md services/admin-api/tests/test_document_agent_migration.py
git commit -m "feat(db): 0099 — Ask agent runs are revision sessions aimed at a document

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The document tools (`get_doc`, `save_doc`, `edit_doc`)

**Files:**
- Create: `services/admin-api/src/knowledge_admin/capabilities/revision.py`
- Modify: `services/admin-api/src/knowledge_admin/tools/catalog.py` (argument models, usage texts, three
  `ToolSpec`s, need `revision`, `RUN_ONLY_NEEDS`, `REVISION_TOOLS`, `catalog_listing`)
- Modify: `services/admin-api/src/knowledge_admin/tools_api.py:105` (skip every run-only need, not only discovery)
- Modify: `services/admin-api/src/knowledge_admin/document_drafts.py` (`promote_draft` sets `agent_sessions.document_id`
  only for `documents` sessions; new `refresh_run_sources_safely`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py:228` (revision turns refresh
  their drafts' sources; their final answer never becomes a draft — already true, now tested)
- Create: `services/admin-api/tests/revision_world.py`, `services/admin-api/tests/test_revision_tools.py`

**Interfaces:**
- Consumes: Task 1 schema; `document_drafts.create_draft`, `record_run_sources`, `audit`; `documents.Title`,
  `Description`, `Markdown`; `capabilities.inventory.connect`.
- Produces:
  - `capabilities.revision.outline(markdown: str) -> list[dict]` — `[{'line': int, 'level': int, 'text': str}]`,
    ignoring fenced code blocks.
  - `async get_doc(context, section=None, start_line=None, end_line=None) -> str`,
    `async save_doc(context, markdown, title=None, description=None) -> str`,
    `async edit_doc(context, old_text, new_text) -> str`; they read `context['config']['revision']`
    (`{'session_id': str, 'document_id': str}`) and raise `ValueError(message)` for a refusal.
  - `tools.catalog.REVISION_TOOLS = ('get_doc', 'save_doc', 'edit_doc')`, `RUN_ONLY_NEEDS = ('discovery', 'revision')`.
  - `document_drafts.refresh_run_sources_safely(conn, session_id, turn) -> None`.

- [ ] **Step 1: Write the test fixture**

`services/admin-api/tests/revision_world.py`:

```python
"""An Ask agent run (a revision session) on the document Runbook, for tool tests (a fixture other modules import)."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from drafts_world import docs, save_live  # noqa: F401  (fixture)
from knowledge_admin.capabilities import inventory
from knowledge_admin.tools.catalog import CATALOG
from session_rows import a_model

RUNBOOK = '''# Runbook

Intro.

## Backup

Old steps.

### Details

```bash
# not a heading
```

## Restore

Steps.
'''


@pytest.fixture
def rev(docs, monkeypatch):
    monkeypatch.setattr(inventory, '_store', docs.store)
    save_live(docs, docs.runbook, RUNBOOK)  # version 2
    sid = uuid4()
    with docs.store.connect() as conn:
        actor = conn.execute('SELECT id FROM ka.principals LIMIT 1').fetchone()['id']
        conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,prompt_snapshot,
            context_snapshot,purpose,target_document_id,agent_config) VALUES (%s,%s,NULL,%s,%s,'Revise','{}','{}','revision',%s,%s)""",
            (sid, docs.project, actor, a_model(conn, actor), docs.runbook, Jsonb({})))
    config = {'project_id': str(docs.project), 'revision': {'session_id': str(sid), 'document_id': docs.runbook}}
    return SimpleNamespace(docs=docs, sid=sid, config=config)


def call(rev, tool: str, **arguments) -> str:
    spec = CATALOG[tool]
    return asyncio.run(spec.run({'config': rev.config}, spec.arguments.model_validate(arguments)))


def run_drafts(rev) -> list[dict]:
    with rev.docs.store.connect() as conn:
        return conn.execute('''SELECT id,document_id,status,edited,origin,markdown,title,description,revision,base_version_id
            FROM ka.document_drafts WHERE session_id=%s ORDER BY created_at,id''', (rev.sid,)).fetchall()
```

- [ ] **Step 2: Write the failing tests**

`services/admin-api/tests/test_revision_tools.py`:

```python
"""Ask agent runs read and change their document only through get_doc, save_doc and edit_doc, which write the run's
draft; a promoted, discarded or hand-edited draft makes the next save start a new one."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from drafts_world import API, make_document, promote, version_of  # noqa: F401
from knowledge_admin.capabilities.revision import outline
from knowledge_admin.knowledge_agents import session_worker as shared
from knowledge_admin.tools.catalog import CATALOG, REVISION_TOOLS, catalog_listing
from revision_world import RUNBOOK, call, rev, run_drafts  # noqa: F401  (fixture)
from test_repositories import HEADERS


def test_get_doc_reads_the_outline_a_section_and_lines(rev):
    assert [(h['line'], h['level'], h['text']) for h in outline(RUNBOOK)] == [
        (1, 1, 'Runbook'), (5, 2, 'Backup'), (9, 3, 'Details'), (15, 2, 'Restore')]
    first = call(rev, 'get_doc')
    assert 'Reads: the live version 2' in first and '5: ## Backup' in first and '15: ## Restore' in first
    assert '# not a heading' not in first and 'Length: 17 lines' in first
    section = call(rev, 'get_doc', section='backup')
    assert section.splitlines()[0] == '5: ## Backup' and '13: ```' in section and 'Restore' not in section
    assert call(rev, 'get_doc', section='Deploy').startswith('No heading named "Deploy".')
    assert call(rev, 'get_doc', start_line=15, end_line=17) == '15: ## Restore\n16: \n17: Steps.'


def test_get_doc_cuts_long_reads_at_50000_characters(rev):
    from drafts_world import save_live
    save_live(rev.docs, rev.docs.runbook, '\n'.join(f'line {n} ' + 'x' * 90 for n in range(1, 1001)))
    text = call(rev, 'get_doc', start_line=1)
    body, note = text.rsplit('\n\n', 1)
    assert len(body) <= 50_000 and note.startswith('Cut at 50,000 characters; read on with start_line=')


def test_save_doc_creates_the_runs_draft_then_updates_it(rev):
    assert call(rev, 'save_doc', markdown='# Runbook\n\nNew text.\n') == 'Saved draft (3 lines).'
    [draft] = run_drafts(rev)
    assert (str(draft['document_id']), draft['origin'], draft['status'], draft['title'], draft['markdown']) == (
        rev.docs.runbook, 'agent', 'open', 'Runbook', '# Runbook\n\nNew text.\n')
    assert str(draft['base_version_id']) == version_of(rev.docs, rev.docs.runbook)
    assert 'Reads: your draft' in call(rev, 'get_doc')
    call(rev, 'save_doc', markdown='# Runbook\n\nNewer.', title='Runbook (revised)', description='Revised')
    [again] = run_drafts(rev)
    assert (again['id'], again['revision'], again['title'], again['description']) == (draft['id'], 2, 'Runbook (revised)', 'Revised')
    detail = rev.docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
    assert [s['kind'] for s in detail['sources']] == ['parent']


def test_edit_doc_replaces_one_exact_passage(rev):
    with pytest.raises(ValueError, match='This text is not in the document. Read it again with get_doc.'):
        call(rev, 'edit_doc', old_text='Nowhere', new_text='x')
    with pytest.raises(ValueError, match='This text appears 3 times. Include more of the surrounding text.'):
        call(rev, 'edit_doc', old_text='## ', new_text='x')  # ## Backup, ### Details, ## Restore
    call(rev, 'edit_doc', old_text='## Backup\n\nOld steps.\n\n', new_text='')
    [draft] = run_drafts(rev)
    assert 'Old steps.' not in draft['markdown'] and draft['markdown'].startswith('# Runbook\n\nIntro.\n\n### Details')
    with pytest.raises(Exception):
        CATALOG['edit_doc'].arguments.model_validate({'old_text': '', 'new_text': 'x'})


def test_a_decided_or_edited_draft_makes_the_next_save_a_new_draft(rev):
    call(rev, 'save_doc', markdown='# One')
    [first] = run_drafts(rev)
    assert promote(rev.docs, first).status_code == 200
    assert call(rev, 'save_doc', markdown='# Two') == (
        'Saved draft (1 lines). Your earlier draft was promoted; this is a new draft based on version 3.')
    second = run_drafts(rev)[-1]
    rev.docs.client.post(f"{API}/drafts/{second['id']}/discard", headers=HEADERS, json={'revision': second['revision']})
    assert call(rev, 'save_doc', markdown='# Three').endswith('Your earlier draft was discarded; this is a new draft based on version 3.')
    third = run_drafts(rev)[-1]
    edited = rev.docs.client.put(f"{API}/drafts/{third['id']}", headers=HEADERS, json={
        'revision': third['revision'], 'title': 'Runbook', 'description': '', 'purpose': 'explanation', 'audience': 'both',
        'tags': [], 'markdown': '# Three, edited by a person'})
    assert edited.status_code == 200, edited.text
    assert call(rev, 'save_doc', markdown='# Four').endswith(
        'A person edited your earlier draft; this is a new draft, and get_doc now reads it.')
    assert [d['markdown'] for d in run_drafts(rev)] == ['# One', '# Two', '# Three, edited by a person', '# Four']


def test_promoting_a_revision_draft_leaves_the_run_alone(rev):
    call(rev, 'save_doc', markdown='# Promoted')
    [draft] = run_drafts(rev)
    assert promote(rev.docs, draft).status_code == 200
    with rev.docs.store.connect() as conn:
        row = conn.execute('SELECT document_id,target_document_id FROM ka.agent_sessions WHERE id=%s', (rev.sid,)).fetchone()
    assert row['document_id'] is None and str(row['target_document_id']) == rev.docs.runbook


def test_parallel_saves_end_in_one_draft(rev):
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda text: call(rev, 'save_doc', markdown=text), ['# A', '# B']))
    [draft] = run_drafts(rev)
    assert draft['markdown'] in ('# A', '# B') and draft['revision'] == 2


def test_saves_record_the_documents_the_run_read(rev):
    other = make_document(rev.docs, 'overview', 'Overview')
    with rev.docs.store.connect() as conn:
        conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,1,'tool_call','ad-hoc',%s)''', (rev.docs.project, f'cc:{rev.sid}:1',
            Jsonb({'name': 'get_document', 'call_id': 'c1', 'arguments': {'document_id': other}})))
    call(rev, 'save_doc', markdown='# With sources')
    [draft] = run_drafts(rev)
    detail = rev.docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
    assert [(s['kind'], s['source_title']) for s in detail['sources']] == [('parent', 'Runbook'), ('read', 'Overview')]


def test_a_closed_run_or_a_deleted_document_cannot_save(rev):
    with rev.docs.store.connect() as conn:
        conn.execute("UPDATE ka.agent_sessions SET status='closed',closed_at=now() WHERE id=%s", (rev.sid,))
    with pytest.raises(ValueError, match='This run is closed.'):
        call(rev, 'save_doc', markdown='# Late')
    current = rev.docs.client.get(f'{API}/documents/{rev.docs.runbook}', headers=HEADERS).json()
    assert rev.docs.client.request('DELETE', f'{API}/documents/{rev.docs.runbook}', headers=HEADERS,
                                   json={'revision': current['revision']}).status_code in (200, 204)
    for tool, arguments in (('get_doc', {}), ('save_doc', {'markdown': '# Gone'})):
        with pytest.raises(ValueError, match='This document no longer exists.'):
            call(rev, tool, **arguments)


def test_the_document_tools_are_run_only_and_kept_from_subagents():
    names = {item['name'] for item in catalog_listing()}
    assert not names & set(REVISION_TOOLS) and 'propose_service' not in names
    assert all(CATALOG[name].needs == 'revision' for name in REVISION_TOOLS)


def test_a_revision_turn_never_turns_its_answer_into_a_draft_and_refreshes_sources(rev):
    call(rev, 'save_doc', markdown='# Saved by the tool')
    other = make_document(rev.docs, 'overview', 'Overview')
    with rev.docs.store.connect() as conn:
        conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) SELECT id,1,actor_id,%s FROM ka.agent_sessions WHERE id=%s',
                     ('Add a section.', rev.sid))
        conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,1,'tool_call','ad-hoc',%s)''', (rev.docs.project, f'cc:{rev.sid}:1',
            Jsonb({'name': 'get_document', 'call_id': 'c1', 'arguments': json.dumps({'document_id': other})})))
    [row] = [r for r in shared.claim(rev.docs.store, 'worker-1', [], 4) if r['id'] == rev.sid]
    shared.SessionRunner(rev.docs.store, row).finish_turn(1, 'waiting', '', draft='# The final answer', usage=None)
    [draft] = run_drafts(rev)
    assert draft['markdown'] == '# Saved by the tool'
    detail = rev.docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
    assert ('read', 'Overview') in [(s['kind'], s['source_title']) for s in detail['sources']]
    with rev.docs.store.connect() as conn:
        assert conn.execute('SELECT draft FROM ka.agent_sessions WHERE id=%s', (rev.sid,)).fetchone()['draft'] is None
```

Note: the `DELETE /documentation/documents/{id}` request shape in `test_a_closed_run_or_a_deleted_document_cannot_save`
is a guess — read `delete_document` in `documents.py` and match it (revision in the body or as a query parameter).

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_revision_tools.py`
Expected: FAIL (`ModuleNotFoundError: knowledge_admin.capabilities.revision`).

- [ ] **Step 4: Write `capabilities/revision.py`**

```python
"""Ask agent runs (purpose revision): the document tools. get_doc reads the run's working copy — its latest open draft
no person has edited, else the document's live version; save_doc and edit_doc save the run's draft. A save locks the
run's session, then its draft (the order the worker and promote take them in), never ka.projects or ka.documents rows,
and recomputes the draft's sources from the run's tool calls."""
import asyncio
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from knowledge_admin.capabilities.inventory import connect
from knowledge_admin.document_drafts import audit, create_draft, record_run_sources
from knowledge_admin.documents import Description, Markdown, Title

NOT_REVISION = 'The document tools work only in an Ask agent run.'
DOCUMENT_GONE = 'This document no longer exists.'
CLOSED = 'This run is closed.'
NOT_FOUND = 'This text is not in the document. Read it again with get_doc.'
MANY = 'This text appears {n} times. Include more of the surrounding text.'
NO_HEADING = 'No heading named "{name}".'
PROMOTED = 'Your earlier draft was promoted; this is a new draft based on version {version}.'
DISCARDED = 'Your earlier draft was discarded; this is a new draft based on version {version}.'
EDITED = 'A person edited your earlier draft; this is a new draft, and get_doc now reads it.'
SAVED = 'Saved draft ({n} lines).'
MAX_READ = 50_000
FENCE = re.compile(r'^ {0,3}(```|~~~)')
HEADING = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$')
LIVE = '''SELECT v.id AS version_id,v.version,v.title,v.description,v.purpose,v.audience,v.tags,v.markdown
    FROM ka.documents d JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s AND d.project_id=%s'''
WORKING = '''SELECT id,title,description,purpose,audience,tags,markdown FROM ka.document_drafts
    WHERE session_id=%s AND status='open' AND NOT edited ORDER BY created_at DESC,id DESC LIMIT 1'''


class DraftText(BaseModel):
    title: Title
    description: Description
    markdown: Markdown


def outline(markdown: str) -> list[dict[str, Any]]:
    """The headings (1-based line, level, text), skipping fenced code blocks."""
    headings, fenced = [], False
    for number, line in enumerate(markdown.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        found = None if fenced else HEADING.match(line)
        if found:
            headings.append({'line': number, 'level': len(found.group(1)), 'text': found.group(2)})
    return headings


def _numbered(lines: list[str], first: int) -> str:
    """Lines prefixed with their numbers, cut at MAX_READ characters with a note on where to read on."""
    out, size = [], 0
    for offset, line in enumerate(lines):
        text = f'{first + offset}: {line}'
        if size + len(text) + 1 > MAX_READ:
            return '\n'.join(out) + f'\n\nCut at 50,000 characters; read on with start_line={first + offset}.'
        out.append(text)
        size += len(text) + 1
    return '\n'.join(out)


def _outline_text(markdown: str) -> str:
    return '\n'.join(f"{h['line']}: {'#' * h['level']} {h['text']}" for h in outline(markdown)) or '(no headings)'


def _run(conn, config: dict[str, Any], *, lock: bool) -> tuple[dict, dict]:
    """The run's session (locked for a save) and its document's live version; ValueError when either is gone."""
    run = config.get('revision')
    if not run:
        raise ValueError(NOT_REVISION)
    session = conn.execute("SELECT id,project_id,status,turns,target_document_id FROM ka.agent_sessions WHERE id=%s"
                           " AND purpose='revision'" + (' FOR UPDATE' if lock else ''), (run['session_id'],)).fetchone()
    if session is None:
        raise ValueError(DOCUMENT_GONE)  # the run went with its document
    live = conn.execute(LIVE, (session['target_document_id'], session['project_id'])).fetchone()
    if live is None:
        raise ValueError(DOCUMENT_GONE)
    return session, live


def _get_doc(config: dict[str, Any], section: str | None, start_line: int | None, end_line: int | None) -> str:
    with connect() as conn:
        session, live = _run(conn, config, lock=False)
        draft = conn.execute(WORKING, (session['id'],)).fetchone()
    copy = draft or live
    lines = copy['markdown'].splitlines()
    if section is not None:
        headings = outline(copy['markdown'])
        found = next((h for h in headings if h['text'].strip().lower() == section.strip().lower()), None)
        if found is None:
            return NO_HEADING.format(name=section) + '\n\nOutline:\n' + _outline_text(copy['markdown'])
        end = next((h['line'] - 1 for h in headings if h['line'] > found['line'] and h['level'] <= found['level']), len(lines))
        return _numbered(lines[found['line'] - 1:end], found['line'])
    if start_line is not None:
        last = min(end_line or len(lines), len(lines))
        return _numbered(lines[start_line - 1:last], start_line)
    reads = 'your draft' if draft else f"the live version {live['version']}"
    return '\n'.join([f"Title: {copy['title']}", f"Description: {copy['description'] or '(none)'}", f'Reads: {reads}',
                      f"Length: {len(lines)} lines, {len(copy['markdown'])} characters", '', 'Outline:',
                      _outline_text(copy['markdown']), '',
                      'Read the text with section (a heading) or start_line/end_line.'])


def _save(config: dict[str, Any], change: Callable[[dict[str, Any]], dict[str, Any]]) -> str:
    with connect() as conn:
        session, live = _run(conn, config, lock=True)
        if session['status'] == 'closed':
            raise ValueError(CLOSED)
        project, document = session['project_id'], session['target_document_id']
        current = conn.execute(WORKING + ' FOR UPDATE', (session['id'],)).fetchone()
        base = dict(current or live)
        try:
            text = DraftText.model_validate(change(base))
        except ValidationError as exc:
            raise ValueError(str(exc.errors()[0]['msg']).removeprefix('Value error, ')) from None
        note = ''
        if current is not None:
            conn.execute('UPDATE ka.document_drafts SET title=%s,description=%s,markdown=%s,revision=revision+1 WHERE id=%s',
                         (text.title, text.description, text.markdown, current['id']))
            audit(conn, project, None, 'document.draft.updated', 'document_draft', current['id'],
                  {'session_id': session['id'], 'turn': session['turns'] + 1})
            draft_id = current['id']
        else:
            previous = conn.execute('''SELECT status,edited FROM ka.document_drafts WHERE session_id=%s
                ORDER BY created_at DESC,id DESC LIMIT 1''', (session['id'],)).fetchone()
            if previous is not None:
                note = (EDITED if previous['status'] == 'open'
                        else (PROMOTED if previous['status'] == 'promoted' else DISCARDED).format(version=live['version']))
            content = {key: live[key] for key in ('purpose', 'audience', 'tags')} | text.model_dump()
            draft_id = create_draft(conn, project, None, content, origin='agent', document_id=document,
                                    session_id=session['id'])
        base_version = conn.execute('SELECT base_version_id FROM ka.document_drafts WHERE id=%s', (draft_id,)).fetchone()
        record_run_sources(conn, project, draft_id, document, base_version['base_version_id'], session['id'],
                           session['turns'] + 1)
    saved = SAVED.format(n=len(text.markdown.splitlines()))
    return f'{saved} {note}' if note else saved


def _replace(old_text: str, new_text: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def change(base: dict[str, Any]) -> dict[str, Any]:
        count = base['markdown'].count(old_text)
        if count == 0:
            raise ValueError(NOT_FOUND)
        if count > 1:
            raise ValueError(MANY.format(n=count))
        return {'title': base['title'], 'description': base['description'],
                'markdown': base['markdown'].replace(old_text, new_text, 1)}
    return change


async def get_doc(context, section: str | None = None, start_line: int | None = None, end_line: int | None = None) -> str:
    return await asyncio.to_thread(_get_doc, context['config'], section, start_line, end_line)


async def save_doc(context, markdown: str, title: str | None = None, description: str | None = None) -> str:
    def change(base: dict[str, Any]) -> dict[str, Any]:
        return {'title': base['title'] if title is None else title,
                'description': base['description'] if description is None else description, 'markdown': markdown}
    return await asyncio.to_thread(_save, context['config'], change)


async def edit_doc(context, old_text: str, new_text: str) -> str:
    return await asyncio.to_thread(_save, context['config'], _replace(old_text, new_text))
```

Notes for the implementer:
- `create_draft` (document_drafts.py) adds the `parent` source and the `document.draft.created` audit event;
  `record_run_sources` then recomputes all sources (it re-adds the parent). Keep both calls.
- A draft "edited by a person" is `status='open' AND edited`; `WORKING` excludes it, so `previous` (the latest draft
  of the run of any status) is exactly the draft that stopped being the working copy.
- `_run` raises `DOCUMENT_GONE` when the session row is gone: deleting the document cascades to its runs (0099).

- [ ] **Step 5: Register the tools in the catalog**

In `tools/catalog.py`:

1. `Need` gains `'revision'`:
   `Need = Literal['files', 'code', 'code_graph', 'documents', 'grounded', 'ssh', 'discovery', 'revision']`
2. Import: `from knowledge_admin.capabilities import revision as revision_tools`
3. Argument models (after `ProposeDocument`):

```python
class GetDoc(Arguments):
    section: str | None = Field(None, max_length=240, description='A heading of the document; returns that section with line numbers.')
    start_line: int | None = Field(None, ge=1, description='First line to read (1-based); alone, reads to the end.')
    end_line: int | None = Field(None, ge=1, description='Last line to read, inclusive.')


class SaveDoc(Proposal):
    markdown: str = Field(min_length=1, max_length=1_000_000, description='The complete document in Markdown.')
    title: str | None = Field(None, min_length=1, max_length=240, description='A new title; omit it to keep the title.')
    description: str | None = Field(None, max_length=4000, description='A new description; omit it to keep it.')


class EditDoc(Proposal):
    old_text: str = Field(min_length=1, max_length=200_000, description='The exact passage to replace; it must occur exactly once.')
    new_text: str = Field(max_length=1_000_000, description='The text that replaces it; empty removes the passage.')
```

   (`Proposal` already refuses null characters; `SaveDoc` and `EditDoc` must not strip whitespace — override
   `model_config = ConfigDict(extra='forbid')` in both so `str_strip_whitespace` is off.)
4. Usage texts:

```python
GET_DOC = ('Read the document you revise. Without arguments: its title, length and outline (headings with line '
           'numbers). With section: that section. With start_line (and end_line): those lines. At most 50,000 '
           'characters per call.')
SAVE_DOC = ('Save the complete document as your draft (optionally a new title or description). A person reviews the '
            'draft and decides whether it becomes the live version.')
EDIT_DOC = ('Replace one exact passage of the document (old_text, which must occur exactly once) with new_text, and '
            'save the result as your draft. An empty new_text removes the passage.')
```

5. `ToolSpec`s, after `propose_document`:

```python
    ToolSpec('get_doc', 'documents', 'Read the document', 'Read the document an Ask agent run revises.', GET_DOC,
             GetDoc, _call(revision_tools.get_doc), 'revision', _never),
    ToolSpec('save_doc', 'documents', 'Save the document', "Save the run's draft of the document.", SAVE_DOC,
             SaveDoc, _call(revision_tools.save_doc), 'revision', _never),
    ToolSpec('edit_doc', 'documents', 'Edit the document', "Replace one passage in the run's draft.", EDIT_DOC,
             EditDoc, _call(revision_tools.edit_doc), 'revision', _never),
```

6. Constants and checks:

```python
REVISION_TOOLS = ('get_doc', 'save_doc', 'edit_doc')
RUN_ONLY_NEEDS = ('discovery', 'revision')  # tools that exist only in one kind of run, never chosen for an agent
```

   `NEED_REASONS['revision'] = 'Only in Ask agent runs'`; in `needs_met` add
   `if need == 'revision': return bool(config.get('revision'))`; `catalog_listing` filters
   `spec.needs not in RUN_ONLY_NEEDS`.
7. `tools_api.py` (agent Tools tab listing): replace `if spec.needs == 'discovery':` with
   `if spec.needs in RUN_ONLY_NEEDS:` (import it) and the comment with `# only in discovery or Ask agent runs`.

- [ ] **Step 6: Promote leaves revision runs alone; revision turns refresh sources**

In `document_drafts.py`, `promote_draft`, change the session update to:

```python
    if row['session_id'] is not None:  # the Generate documents list shows the run's saved document
        conn.execute("UPDATE ka.agent_sessions SET document_id=%s WHERE id=%s AND purpose='documents'",
                     (saved['id'], row['session_id']))
```

Add after `save_run_draft_safely`:

```python
def refresh_run_sources_safely(conn, session_id: UUID, turn: int) -> None:
    """An Ask agent turn ended: recompute the sources of the run's working draft from every tool call recorded so far
    (a read recorded after the save that preceded it). In a savepoint; a failure loses only this refresh."""
    try:
        with conn.transaction():
            draft = conn.execute('''SELECT id,project_id,document_id,base_version_id FROM ka.document_drafts
                WHERE session_id=%s AND status='open' AND NOT edited ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE''',
                (session_id,)).fetchone()
            if draft is not None:
                record_run_sources(conn, draft['project_id'], draft['id'], draft['document_id'], draft['base_version_id'],
                                   session_id, turn)
    except Exception:  # any failure, a bug included, rolls back only the savepoint
        logger.exception('Session %s turn %s: the draft sources were not refreshed', session_id, turn)
```

In `session_worker.py` `finish_turn`, after the existing `save_run_draft_safely` branch:

```python
            if updated is not None and updated['purpose'] == 'revision':
                refresh_run_sources_safely(conn, self.id, number)  # the tools saved the draft; reads may come later
```

(import `refresh_run_sources_safely` beside `save_run_draft_safely`; extend the docstring's last sentence: "An Ask agent
session's draft is saved by its tools; the turn only refreshes its sources.")

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_revision_tools.py tests/test_document_drafts_worker.py tests/test_document_drafts.py tests/test_discovery_tools.py tests/test_tools_api.py tests/test_session_worker.py`
Expected: PASS. (If `tests/test_tools_api.py` does not exist, run `grep -l "catalog_listing\|/tools" tests/*.py` and
run those instead.)

- [ ] **Step 8: Commit**

```bash
git add services/admin-api/src/knowledge_admin/capabilities/revision.py services/admin-api/src/knowledge_admin/tools/catalog.py \
  services/admin-api/src/knowledge_admin/tools_api.py services/admin-api/src/knowledge_admin/document_drafts.py \
  services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py \
  services/admin-api/tests/revision_world.py services/admin-api/tests/test_revision_tools.py
git commit -m "feat(agents): get_doc, save_doc and edit_doc — Ask agent runs write their document's draft through tools

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `/agent-sessions` — options, preview, start, list

**Files:**
- Create: `services/admin-api/src/knowledge_admin/agent_launch.py`
- Modify: `services/admin-api/src/knowledge_admin/api.py` (mount the router beside `discovery`)
- Modify: `services/admin-api/src/knowledge_admin/generations_api.py` (`DETAIL` selects `s.target_document_id`;
  `session_detail` returns `config` for `revision` too)
- Modify: `services/admin-api/src/knowledge_admin/document_drafts.py` (`SUMMARY` selects `s.purpose AS session_purpose`)
- Create: `services/admin-api/tests/test_agent_launch.py`

**Interfaces:**
- Consumes: Task 1, Task 2 (`REVISION_TOOLS`, `RUN_ONLY_NEEDS`, `capabilities.revision.outline`);
  `generations_api.Selection`, `session_detail`, `NO_MODEL`; `ephemeral.ad_hoc_config`; `experts.offered`;
  `config.load_model`, `model_fits`; `run_resources.RunResourceError`; `tools.catalog.CATALOG`, `SUBAGENT`, `needs_met`.
- Produces (HTTP, under `/api`):
  - `GET /agent-sessions/options?kind=revision&document_id=…` →
    `{models: [{id, name}], default_model_id, prompts: [{id, name, content}], default_prompt: {id: null, name:
    'Built-in', content}, resources: {servers: [{id, name, linked, usable, reason}], services: [{id, name, linked}],
    environments: [...], code_projects: [...], collections: [{id, name, linked: false}]}, tools: [{name, label,
    summary, group, needs, requires}], fixed_tools: [{name, label}], experts: [{id, name, when_to_use}]}`
    (`requires`: null or the "needs …" phrase; `linked` from the document's `*_ids`).
  - `POST /agent-sessions/preview` (body `StartInput`) → `{config, first_message}`; `config.revision.session_id` is null.
  - `POST /agent-sessions` (body `StartInput`, 201) → `session_detail` of the new session.
  - `GET /agent-sessions?document_id=…` → `{sessions: [{id, title, status, model_name, started_by, created_at,
    last_activity_at, draft_id}]}`, open (status ≠ closed) first, then latest activity.
  - `StartInput`: `{kind: 'revision', target: {document_id}, instruction, model_id, prompt_id?, system_prompt,
    resources: {code_project_ids, collection_ids, environment_ids, service_ids, server_ids}, tools: [names],
    expert_ids: [], subagents: false}`.
  - Session detail and draft rows gain `target_document_id` and `session_purpose`.

- [ ] **Step 1: Write the failing tests**

`services/admin-api/tests/test_agent_launch.py`:

```python
"""Starting an Ask agent run on a document through /agent-sessions: the panel's options, Preview equal to what Start
pins, refusals, the document's runs, the session routes, and the runs going with their document."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from knowledge_admin.agent_launch import DEFAULT_REVISION_PROMPT
from test_discovery_runs import ssh_ready
from test_generations import sessions  # noqa: F401  (fixture)
from test_repositories import HEADERS


@pytest.fixture
def ask(sessions):
    s = sessions
    web = s.client.post('/api/servers', headers=HEADERS, json={'name': 'web-1', 'hostname': '10.0.0.7', 'ssh_allowed': True}).json()
    ssh_ready(SimpleNamespace(client=s.client, server=web), web)
    document = s.client.post('/api/documentation/documents', headers=HEADERS, json={
        'slug': 'payments-runbook', 'title': 'Payments runbook', 'markdown': '# Payments runbook\n\n## Backup\n\nOld steps.',
        'service_ids': [s.service], 'server_ids': [web['id']]}).json()
    body = {'kind': 'revision', 'target': {'document_id': document['id']}, 'instruction': 'Add a section on log rotation.\nKeep it short.',
            'model_id': s.gpt, 'system_prompt': 'You revise documents.', 'resources': {'service_ids': [s.service], 'server_ids': [web['id']]},
            'tools': ['ssh_run', 'get_service_info'], 'expert_ids': [], 'subagents': False}
    return SimpleNamespace(s=s, web=web, document=document, body=body)


def post(ask, path='', **change):
    return ask.s.client.post(f'/api/agent-sessions{path}', headers=HEADERS, json={**ask.body, **change})


def test_options_mark_the_documents_links_and_unusable_servers(ask):
    options = ask.s.client.get(f"/api/agent-sessions/options?kind=revision&document_id={ask.document['id']}", headers=HEADERS).json()
    servers = {item['name']: item for item in options['resources']['servers']}
    assert servers['web-1']['linked'] and servers['web-1']['usable']
    assert not servers['prod-1']['linked'] and servers['prod-1']['reason'] == 'SSH is not set up for this server.'
    assert [item['name'] for item in options['resources']['services'] if item['linked']] == ['Payments']
    assert options['default_prompt'] == {'id': None, 'name': 'Built-in', 'content': DEFAULT_REVISION_PROMPT}
    assert [item['name'] for item in options['fixed_tools']] == ['get_doc', 'save_doc', 'edit_doc']
    tools = {item['name']: item for item in options['tools']}
    assert tools['ssh_run']['requires'] == 'at least one server' and 'run_subagent' not in tools and 'propose_service' not in tools
    assert ask.s.gpt in {item['id'] for item in options['models']}


def test_preview_is_exactly_what_start_pins(ask):
    preview = post(ask, '/preview')
    assert preview.status_code == 200, preview.text
    started = post(ask)
    assert started.status_code == 201, started.text
    session = started.json()
    assert (session['purpose'], session['target_document_id'], session['title']) == (
        'revision', ask.document['id'], 'Add a section on log rotation.')
    pinned = session['config']
    assert pinned['revision'] == {'session_id': session['id'], 'document_id': ask.document['id']}
    assert {**preview.json()['config'], 'revision': pinned['revision']} == pinned
    assert preview.json()['first_message'] == ask.body['instruction']
    modes = {item['tool']: item for item in pinned['tools']['items']}
    assert all(modes[name]['mode'] == 'on' and not modes[name]['subagent_allowed'] for name in ('get_doc', 'save_doc', 'edit_doc'))
    assert modes['ssh_run']['mode'] == 'on' and modes['get_server_info_basic']['mode'] == 'off'
    assert modes['run_subagent']['mode'] == 'off' and pinned['experts'] == []
    assert 'You revise documents.' in pinned['system_prompt']['content'] and '## Backup' in pinned['system_prompt']['content']
    assert 'Old steps.' not in pinned['system_prompt']['content']  # the outline, not the text
    assert 'bedrock-secret-token' not in started.text and 'PRIVATE KEY' not in started.text
    assert session['inputs'][0]['text'] == ask.body['instruction']


def test_start_refuses_what_cannot_run(ask):
    web = [ask.web['id']]
    for change, message in (
            ({'tools': ['read_file'], 'resources': {'server_ids': web}}, 'read_file needs a code project or a collection.'),
            # Payments is deployed only on prod-1, whose SSH is not set up, so it brings no server.
            ({'tools': ['ssh_run'], 'resources': {'service_ids': [ask.s.service]}}, 'ssh_run needs at least one server.'),
            ({'tools': ['nope']}, 'Unknown tool: nope.'),
            ({'expert_ids': ['00000000-0000-0000-0000-000000000001']},
             'This expert is not available: 00000000-0000-0000-0000-000000000001.')):
        for path in ('/preview', ''):
            response = post(ask, path, **change)
            assert response.status_code == 400 and response.json()['error']['message'] == message, response.text


def test_start_refuses_an_unusable_server_a_disabled_model_and_a_missing_document(ask):
    prod = ask.s.server['id']
    assert post(ask, resources={'server_ids': [prod]}).json()['error']['message'] in (
        'Server prod-1: SSH is not set up for this server.', 'Server prod-1: SSH is not allowed for this server.')
    assert post(ask, model_id=str(uuid4())).json()['error']['message'] == 'Choose an enabled model.'
    gone = post(ask, target={'document_id': str(uuid4())})
    assert gone.status_code == 404


def test_the_documents_runs_are_listed_open_first_and_use_the_session_routes(ask):
    first = post(ask).json()
    second = post(ask, instruction='Remove the old backup steps.').json()
    assert ask.s.client.post(f"/api/generations/sessions/{first['id']}/close", headers=HEADERS).status_code == 200
    listed = ask.s.client.get(f"/api/agent-sessions?document_id={ask.document['id']}", headers=HEADERS).json()['sessions']
    assert [row['id'] for row in listed] == [second['id'], first['id']]
    assert listed[0]['title'] == 'Remove the old backup steps.' and listed[0]['draft_id'] is None
    sent = ask.s.client.post(f"/api/generations/sessions/{second['id']}/messages", headers=HEADERS, json={'text': 'Also mention cron.'})
    assert sent.status_code == 202, sent.text
    with ask.s.store.connect() as conn:
        actions = [row['action'] for row in conn.execute("SELECT action,details FROM ka.audit_events WHERE action='agent_run.created'")]
        details = conn.execute("SELECT details FROM ka.audit_events WHERE action='agent_run.created' ORDER BY id LIMIT 1").fetchone()['details']
    assert actions == ['agent_run.created'] * 2
    assert details['kind'] == 'revision' and details['document_id'] == ask.document['id'] and 'get_server_info_basic' in details['tools_off']


def test_deleting_the_document_deletes_its_runs(ask):
    session = post(ask).json()
    current = ask.s.client.get(f"/api/documentation/documents/{ask.document['id']}", headers=HEADERS).json()
    deleted = ask.s.client.request('DELETE', f"/api/documentation/documents/{ask.document['id']}", headers=HEADERS,
                                   json={'revision': current['revision']})
    assert deleted.status_code in (200, 204), deleted.text
    assert ask.s.client.get(f"/api/generations/sessions/{session['id']}", headers=HEADERS).status_code == 404
```

Note: the `DELETE /documentation/documents/{id}` body shape — check `documents.py` `delete_document` and match it
(revision in the body or as a query parameter); adjust both tests that delete (also in Task 2) to the real shape.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-api && uv run pytest -q tests/test_agent_launch.py`
Expected: FAIL (`ModuleNotFoundError: knowledge_admin.agent_launch`).

- [ ] **Step 3: Write `agent_launch.py`**

```python
"""One way to start an ad-hoc agent session: /agent-sessions — the options a start panel needs, Preview (exactly what
Start pins), Start and the sessions about something. Kinds: revision (Ask agent on a document: the document tools
get_doc, save_doc and edit_doc, the chosen resources and tools, experts and subagents only when chosen). Generate
documents and server discovery keep their own endpoints until they move here."""
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.capabilities.revision import outline
from knowledge_admin.generations_api import NO_MODEL, Selection, session_detail
from knowledge_admin.knowledge_agents.config import load_model, model_fits
from knowledge_admin.knowledge_agents.ephemeral import ad_hoc_config
from knowledge_admin.knowledge_agents.experts import offered
from knowledge_admin.knowledge_agents.run_resources import RunResourceError
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.tools.catalog import CATALOG, REVISION_TOOLS, RUN_ONLY_NEEDS, SUBAGENT, needs_met

REVISION_TAG = 'document-revision'
DOCUMENT_NOT_FOUND = 'Document not found'
UNKNOWN_TOOL = 'Unknown tool: {names}.'
NEEDS = '{tool} needs {what}.'
REQUIRES = {'files': 'a code project or a collection', 'code': 'a code project', 'code_graph': 'a code project',
            'documents': 'a collection', 'ssh': 'at least one server'}
NO_EXPERT = 'This expert is not available: {id}.'
NOT_SET_UP = 'SSH is not set up for this server.'
NOT_ALLOWED = 'SSH is not allowed for this server.'
TITLE_LENGTH = 120
DEFAULT_REVISION_PROMPT = '''You revise one technical document at a person's request. Read the parts of the document
you change before you change them, and check facts against the servers, code and documents you were given. Keep the
document's structure and voice; change only what the request asks for. Never include passwords, keys or tokens.'''
RULES = '''Rules:
- Read the document with get_doc: without arguments for its outline, with section for one section, with start_line
  and end_line for lines.
- Change the document only with edit_doc (one exact passage) or save_doc (the complete text). Each save updates your
  draft; a person reviews it and decides whether it becomes the live version. Never say the change is live.
- When the request is unclear, ask in plain chat before you change the document.
- When you are done, say in a few sentences what you changed.'''
RESOURCE_NAMES = {'server_ids': ('servers', 'SELECT id,name FROM ka.servers WHERE id=ANY(%s) ORDER BY lower(name)'),
                  'service_ids': ('services', 'SELECT id,name FROM ka.services WHERE id=ANY(%s) ORDER BY lower(name)'),
                  'environment_ids': ('environments', 'SELECT id,name FROM ka.environments WHERE id=ANY(%s) ORDER BY lower(name)'),
                  'code_project_ids': ('code projects', 'SELECT id,name FROM ka.repositories WHERE id=ANY(%s) ORDER BY lower(name)')}
RUNS = '''SELECT s.id,s.title,s.status,s.created_at,s.last_activity_at,m.name AS model_name,p.email AS started_by,
    (SELECT x.id FROM ka.document_drafts x WHERE x.session_id=s.id AND x.status='open'
     ORDER BY x.created_at DESC,x.id DESC LIMIT 1) AS draft_id
    FROM ka.agent_sessions s JOIN ka.llm_models m ON m.id=s.model_id LEFT JOIN ka.principals p ON p.id=s.actor_id
    WHERE s.project_id=%s AND s.purpose='revision' AND s.target_document_id=%s
    ORDER BY s.status='closed',s.last_activity_at DESC,s.id LIMIT 100'''


class RevisionTarget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_id: UUID


class StartInput(BaseModel):
    """What a start panel sends. kind picks the target's shape (revision: a document)."""
    model_config = ConfigDict(extra='forbid')
    kind: Literal['revision']
    target: RevisionTarget
    instruction: str = Field(max_length=20000)
    model_id: UUID
    prompt_id: UUID | None = None
    system_prompt: str = Field(max_length=20000)
    resources: Selection = Field(default_factory=Selection)
    tools: list[str] = Field(default_factory=list, max_length=100)
    expert_ids: list[UUID] = Field(default_factory=list, max_length=50)
    subagents: bool = False

    @model_validator(mode='after')
    def valid(self):
        for name in ('instruction', 'system_prompt'):
            value = getattr(self, name)
            if '\x00' in value:
                raise ValueError('Text cannot contain null characters')
            setattr(self, name, value.strip())
        if not self.instruction:
            raise ValueError('Enter an instruction')
        if not self.system_prompt:
            raise ValueError('Enter a system prompt')
        self.tools = list(dict.fromkeys(self.tools))
        self.expert_ids = list(dict.fromkeys(self.expert_ids))
        return self


def choosable() -> list:
    """The tools a person can switch on for a run: not run-only, not "Answer only from documents", not run_subagent
    (its own switch)."""
    return [spec for spec in CATALOG.values()
            if spec.needs not in RUN_ONLY_NEEDS and spec.needs != 'grounded' and spec.name != SUBAGENT]


def server_reason(row: dict) -> str | None:
    if not row['enabled'] or row['credential_id'] is None:
        return NOT_SET_UP
    if not row['ssh_allowed']:
        return NOT_ALLOWED
    return None


def target_document(conn, project: UUID, document_id: UUID) -> dict:
    row = conn.execute('''SELECT d.id,d.title,d.folder,d.description,d.purpose,d.audience,d.code_project_ids,d.service_ids,
        d.server_ids,d.environment_ids,v.markdown FROM ka.document_details d JOIN ka.document_versions v ON v.id=d.current_version_id
        WHERE d.id=%s AND d.project_id=%s''', (document_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return row


def revision_context(conn, document: dict) -> str:
    """What the agent knows about its document before it reads it: the fields, linked resources and outline."""
    lines = ['# The document you revise', f"Title: {document['title']}",
             f"Folder: {document['folder'] or '(top folder)'}", f"Description: {document['description'] or '(none)'}",
             f"Purpose: {document['purpose']}", f"Audience: {document['audience']}"]
    for key, (label, query) in RESOURCE_NAMES.items():
        names = [row['name'] for row in conn.execute(query, (list(document[key] or []),)).fetchall()]
        if names:
            lines.append(f"Linked {label}: {', '.join(names)}")
    markdown = document['markdown']
    headings = '\n'.join(f"{h['line']}: {'#' * h['level']} {h['text']}" for h in outline(markdown)) or '(no headings)'
    lines += ['', f'Length: {len(markdown.splitlines())} lines, {len(markdown)} characters', '', 'Outline:', headings, '', RULES]
    return '\n'.join(lines)


def build(conn, project: UUID, value: StartInput, session_id: UUID | None) -> tuple[dict, dict, dict]:
    """(pinned configuration, model, document) for a revision run: preview and start share it, so they cannot differ."""
    document = target_document(conn, project, value.target.document_id)
    model = load_model(conn, value.model_id)
    if model is None or not model['enabled'] or not model_fits(model):
        raise HTTPException(400, NO_MODEL)
    for row in conn.execute('SELECT id,name,enabled,ssh_allowed,credential_id FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
                            (project, value.resources.server_ids)).fetchall():
        reason = server_reason(row)
        if reason:
            raise HTTPException(400, f"Server {row['name']}: {reason}")
    names = {spec.name: spec for spec in choosable()}
    unknown = [name for name in value.tools if name not in names and name not in REVISION_TOOLS]
    if unknown:
        raise HTTPException(400, UNKNOWN_TOOL.format(names=', '.join(unknown)))
    system_prompt = f'{value.system_prompt}\n\n{revision_context(conn, document)}'
    try:
        pinned = ad_hoc_config(conn, project, model=model, system_prompt=system_prompt, selection=value.resources)
    except RunResourceError as exc:
        raise HTTPException(400, str(exc)) from None
    for name in value.tools:
        spec = names.get(name)
        if spec is not None and not needs_met(spec.needs, pinned, None):
            raise HTTPException(400, NEEDS.format(tool=name, what=REQUIRES[spec.needs]))
    chosen = set(value.tools)
    for item in pinned['tools']['items']:
        if item['tool'] in REVISION_TOOLS:
            item.update(mode='on', subagent_allowed=False)
        elif item['tool'] == SUBAGENT:
            item['mode'] = 'on' if value.subagents else 'off'
        elif item['tool'] in CATALOG:
            item['mode'] = 'on' if item['tool'] in chosen else 'off'
    experts = {entry['id']: entry for entry in pinned['experts']}
    for expert_id in value.expert_ids:
        if str(expert_id) not in experts:
            raise HTTPException(400, NO_EXPERT.format(id=expert_id))
    pinned['experts'] = [experts[str(expert_id)] for expert_id in value.expert_ids]
    pinned['project_id'] = str(project)
    pinned['revision'] = {'session_id': str(session_id) if session_id else None, 'document_id': str(document['id'])}
    return jsonable_encoder(pinned), model, document


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/agent-sessions')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Agent session storage is not configured.')
        return store

    @router.get('/options')
    def options(request: Request, kind: Literal['revision'] = Query(...), document_id: UUID = Query(...)) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            document = target_document(conn, project, document_id)
            actor = db.actor(conn, *request_actor(request))
            models = [{'id': row['id'], 'name': row['name']} for row in conn.execute(
                'SELECT id,name,provider,api_format,model FROM ka.llm_models WHERE enabled ORDER BY lower(name),id').fetchall()
                if model_fits(row)]
            last = conn.execute('''SELECT model_id FROM ka.agent_sessions WHERE target_document_id=%s AND actor_id=%s
                ORDER BY created_at DESC LIMIT 1''', (document_id, actor)).fetchone()
            ids = [item['id'] for item in models]
            default_model = last['model_id'] if last and last['model_id'] in ids else (ids[0] if ids else None)
            prompts = conn.execute('''SELECT p.id,p.name,v.content FROM ka.documentation_prompts p
                JOIN ka.documentation_prompt_versions v ON v.id=p.active_version_id
                WHERE p.project_id=%s AND p.enabled AND p.tags @> ARRAY[%s]::text[] ORDER BY lower(p.name),p.id''',
                (project, REVISION_TAG)).fetchall()

            def listed(query: str, linked_ids) -> list[dict]:
                linked = {str(item) for item in linked_ids or []}
                return [{'id': row['id'], 'name': row['name'], 'linked': str(row['id']) in linked}
                        for row in conn.execute(query, (project,)).fetchall()]
            servers = []
            for row in conn.execute('''SELECT id,name,enabled,ssh_allowed,credential_id FROM ka.servers WHERE project_id=%s
                    ORDER BY lower(name),id''', (project,)).fetchall():
                reason = server_reason(row)
                servers.append({'id': row['id'], 'name': row['name'], 'linked': str(row['id']) in {str(i) for i in document['server_ids'] or []},
                                'usable': reason is None, 'reason': reason})
            resources = {
                'servers': servers,
                'services': listed('SELECT id,name FROM ka.services WHERE project_id=%s ORDER BY lower(name),id', document['service_ids']),
                'environments': listed('SELECT id,name FROM ka.environments WHERE project_id=%s ORDER BY lower(name),id', document['environment_ids']),
                'code_projects': listed('''SELECT id,name FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL
                    ORDER BY lower(name),id''', document['code_project_ids']),
                'collections': listed('''SELECT id,name FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL
                    ORDER BY lower(name),id''', []),
            }
            experts = [{'id': entry['id'], 'name': entry['name'], 'when_to_use': entry['when_to_use']} for entry in offered(conn, None)]
        return jsonable_encoder({
            'models': models, 'default_model_id': default_model, 'prompts': prompts,
            'default_prompt': {'id': None, 'name': 'Built-in', 'content': DEFAULT_REVISION_PROMPT},
            'resources': resources,
            'tools': [{'name': spec.name, 'label': spec.label, 'summary': spec.summary, 'group': spec.group,
                       'needs': spec.needs, 'requires': REQUIRES.get(spec.needs) if spec.needs else None} for spec in choosable()],
            'fixed_tools': [{'name': name, 'label': CATALOG[name].label} for name in REVISION_TOOLS],
            'experts': experts})

    @router.post('/preview')
    def preview(value: StartInput) -> dict:
        db = database()
        with db.connect() as conn:
            pinned, _, _ = build(conn, db.project(conn, app_name), value, None)
        return {'config': pinned, 'first_message': value.instruction}

    @router.post('', status_code=201)
    def start(value: StartInput, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            session_id = uuid4()
            pinned, model, document = build(conn, project, value, session_id)
            title = value.instruction.splitlines()[0][:TITLE_LENGTH]
            snapshot = {'system_prompt': value.system_prompt, 'prompt_id': value.prompt_id}
            conn.execute("""INSERT INTO ka.agent_sessions(id,project_id,agent_id,actor_id,model_id,title,purpose,
                target_document_id,prompt_snapshot,context_snapshot,agent_config,run_resources)
                VALUES (%s,%s,NULL,%s,%s,%s,'revision',%s,%s,%s,%s,%s)""",
                (session_id, project, actor, model['id'], title, document['id'], Jsonb(jsonable_encoder(snapshot)),
                 Jsonb({'document_id': str(document['id'])}), Jsonb(pinned), Jsonb(pinned['run_resources'])))
            conn.execute('INSERT INTO ka.agent_session_inputs(session_id,seq,actor_id,text) VALUES (%s,1,%s,%s)',
                         (session_id, actor, value.instruction))
            tools_off = sorted(item['tool'] for item in pinned['tools']['items']
                               if item['tool'] in CATALOG and item['mode'] != 'on' and CATALOG[item['tool']].needs not in RUN_ONLY_NEEDS)
            conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,'agent_run.created','agent_session',%s,%s)''', (project, actor, session_id, Jsonb(jsonable_encoder({
                    'kind': value.kind, 'document_id': document['id'], 'model_id': model['id'], 'prompt_id': value.prompt_id,
                    'tools_off': tools_off, 'expert_ids': value.expert_ids, 'subagents': value.subagents}))))
            return session_detail(conn, project, session_id)

    @router.get('')
    def sessions(document_id: UUID = Query(...)) -> dict:
        db = database()
        with db.connect() as conn:
            return {'sessions': conn.execute(RUNS, (db.project(conn, app_name), document_id)).fetchall()}

    return router
```

Notes for the implementer:
- `pinned['run_resources']`: `ad_hoc_config` returns the resolved configuration with `run_resources`; discovery uses
  the same key (`discovery.py` start). If the key is missing, follow `generations_api.create_session` exactly.
- `pinned['experts']` entries carry `id` as a string (see `experts.offered`).
- `models` uses `model_fits`, as Start does; a model that does not fit is not offered.
- `db.actor(...)` in a GET upserts the principal, as other GET routes that need the actor do; if no GET route does
  this, look the actor up read-only instead (`SELECT id FROM ka.principals WHERE …` as `RepositoryStore.actor` does)
  and drop the "last model" default when not found.

- [ ] **Step 4: Mount and extend the shared reads**

- `api.py`: import `agent_launch` and add `router.include_router(agent_launch.build_api(app_name, repository_store))`
  after the discovery router.
- `generations_api.py`: `DETAIL = _select(',s.document_name,s.draft,s.draft_turn,s.prompt_snapshot,s.context_snapshot,s.model_id,s.run_resources,s.server_id,s.target_document_id')`;
  in `session_detail`, `if row['purpose'] in ('test', 'event', 'revision'):` — the reviews query stays as is (a
  revision run simply has none). Update the comment: "its pinned configuration (credential references only) and, for
  Test sessions, its suggestions".
- `document_drafts.py` `SUMMARY`: add `s.purpose AS session_purpose` after `s.title AS session_title`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_agent_launch.py tests/test_generations.py tests/test_document_draft_reads.py tests/test_document_drafts.py tests/test_discovery_runs.py tests/test_test_sessions.py tests/test_revision_tools.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/admin-api/src/knowledge_admin/agent_launch.py services/admin-api/src/knowledge_admin/api.py \
  services/admin-api/src/knowledge_admin/generations_api.py services/admin-api/src/knowledge_admin/document_drafts.py \
  services/admin-api/tests/test_agent_launch.py
git commit -m "feat(agents): /agent-sessions — options, preview, start and list for Ask agent runs on a document

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: UI — the Ask agent sheet on the document page

**Files:**
- Create: `services/admin-ui/app/lib/agentSessions.ts`
- Create: `services/admin-ui/app/components/AskAgentSheet.tsx`
- Modify: `services/admin-ui/app/components/DocumentEditorView.tsx` (an `Ask agent` button beside `Save draft`, shown
  for an existing document; opens the sheet)
- Modify: `services/admin-ui/app/lib/generations.ts` (`SessionPurpose` gains `'revision'`; `SessionDetail` gains
  `target_document_id?: string | null`)
- Create: `services/admin-ui/app/components/__tests__/askAgent.test.tsx`

**Interfaces:**
- Consumes: Task 3 HTTP API.
- Produces: `lib/agentSessions.ts` exports `AgentOptions`, `StartInput`, `AgentRunSummary`,
  `getAgentOptions(documentId, signal?)`, `previewAgentSession(value)`, `startAgentSession(value)`,
  `getDocumentAgentRuns(documentId, signal?)`, `agentRunPath(documentId, runId)` (→ `/documents/{id}/agent/{runId}`).

- [ ] **Step 1: Write `lib/agentSessions.ts`**

```ts
import { request } from './api';
import type { SessionDetail } from './generations';

const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });

export interface AgentResource { id: string; name: string; linked: boolean }
export interface AgentServer extends AgentResource { usable: boolean; reason: string | null }
export interface AgentTool { name: string; label: string; summary: string; group: string; needs: string | null; requires: string | null }
export interface AgentOptions {
  models: { id: string; name: string }[]; default_model_id: string | null;
  prompts: { id: string; name: string; content: string }[]; default_prompt: { id: null; name: string; content: string };
  resources: { servers: AgentServer[]; services: AgentResource[]; environments: AgentResource[]; code_projects: AgentResource[]; collections: AgentResource[] };
  tools: AgentTool[]; fixed_tools: { name: string; label: string }[]; experts: { id: string; name: string; when_to_use: string }[];
}
export interface AgentResources { server_ids: string[]; service_ids: string[]; environment_ids: string[]; code_project_ids: string[]; collection_ids: string[] }
export interface StartInput {
  kind: 'revision'; target: { document_id: string }; instruction: string; model_id: string; prompt_id: string | null;
  system_prompt: string; resources: AgentResources; tools: string[]; expert_ids: string[]; subagents: boolean;
}
export interface AgentRunSummary {
  id: string; title: string; status: SessionDetail['status']; model_name: string; started_by: string | null;
  created_at: string; last_activity_at: string; draft_id: string | null;
}

export const getAgentOptions = (documentId: string, signal?: AbortSignal) =>
  request<AgentOptions>(`/api/agent-sessions/options?kind=revision&document_id=${encodeURIComponent(documentId)}`, { signal });
export const previewAgentSession = (value: StartInput) =>
  request<{ config: Record<string, unknown>; first_message: string }>('/api/agent-sessions/preview', json('POST', value));
export const startAgentSession = (value: StartInput) => request<SessionDetail>('/api/agent-sessions', json('POST', value));
export const getDocumentAgentRuns = (documentId: string, signal?: AbortSignal) =>
  request<{ sessions: AgentRunSummary[] }>(`/api/agent-sessions?document_id=${encodeURIComponent(documentId)}`, { signal });
export const agentRunPath = (documentId: string, runId: string) =>
  `/documents/${encodeURIComponent(documentId)}/agent/${encodeURIComponent(runId)}`;

/** Which tool groups apply: a tool shows when what it needs is selected (inventory and other no-need tools always). */
export function toolApplies(tool: AgentTool, chosen: AgentResources): boolean {
  const code = chosen.code_project_ids.length > 0 || chosen.service_ids.length > 0 || chosen.environment_ids.length > 0;
  switch (tool.needs) {
    case null: return true;
    case 'ssh': return chosen.server_ids.length > 0 || chosen.service_ids.length > 0 || chosen.environment_ids.length > 0;
    case 'documents': return chosen.collection_ids.length > 0;
    case 'files': return code || chosen.collection_ids.length > 0;
    default: return code;  // code, code_graph
  }
}
```

(A service or environment may bring servers and code projects, so their tools show when one is ticked; Start
still refuses a tool whose need the server resolution does not meet, and the sheet shows that message.)

- [ ] **Step 2: Write the failing UI test**

`services/admin-ui/app/components/__tests__/askAgent.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import AskAgentSheet from '../AskAgentSheet';

const NativeRequest = globalThis.Request;
beforeEach(() => vi.stubGlobal('Request', class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
}));
afterEach(() => vi.unstubAllGlobals());

const options = {
  models: [{ id: 'm1', name: 'GPT' }, { id: 'm2', name: 'Claude' }], default_model_id: 'm2',
  prompts: [{ id: 'p1', name: 'Careful reviser', content: 'You revise carefully.' }],
  default_prompt: { id: null, name: 'Built-in', content: 'Built-in prompt.' },
  resources: {
    servers: [{ id: 's1', name: 'web-1', linked: true, usable: true, reason: null },
      { id: 's2', name: 'prod-1', linked: false, usable: false, reason: 'SSH is not set up for this server.' }],
    services: [{ id: 'v1', name: 'Payments', linked: true }], environments: [], code_projects: [{ id: 'c1', name: 'Shop', linked: false }], collections: [] },
  tools: [{ name: 'ssh_run', label: 'SSH', summary: 'Run a command', group: 'servers', needs: 'ssh', requires: 'at least one server' },
    { name: 'read_file', label: 'Read file', summary: 'Read a file', group: 'code', needs: 'files', requires: 'a code project or a collection' },
    { name: 'get_service_info', label: 'Service info', summary: 'A service', group: 'inventory', needs: null, requires: null }],
  fixed_tools: [{ name: 'get_doc', label: 'Read the document' }, { name: 'save_doc', label: 'Save the document' }, { name: 'edit_doc', label: 'Edit the document' }],
  experts: [{ id: 'e1', name: 'Payments expert', when_to_use: 'Payments questions' }],
};

function api(routes: Record<string, (body: any) => [number, unknown]>) {
  const calls: { key: string; body: any }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url}`;
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ key, body });
    if (!(key in routes)) throw new Error(`unexpected ${key}`);
    const [status, value] = routes[key](body);
    return new Response(JSON.stringify(value), { status });
  }));
  return calls;
}
const OPTIONS = 'GET /api/agent-sessions/options?kind=revision&document_id=doc-1';
function show() {
  const router = createMemoryRouter([
    { path: '/documents/:documentId', element: <AskAgentSheet documentId="doc-1" documentTitle="Runbook" onClose={() => {}} /> },
    { path: '/documents/:documentId/agent/:runId', element: <p>Run page</p> }], { initialEntries: ['/documents/doc-1'] });
  render(<RouterProvider router={router} />);
  return router;
}
const expected = {
  kind: 'revision', target: { document_id: 'doc-1' }, instruction: 'Add log rotation.', model_id: 'm2', prompt_id: 'p1',
  system_prompt: 'You revise carefully.', resources: { server_ids: ['s1'], service_ids: ['v1'], environment_ids: [], code_project_ids: [], collection_ids: [] },
  tools: ['ssh_run', 'get_service_info'], expert_ids: [], subagents: false };

test('defaults: linked resources ticked, applicable tools ticked, fixed tools shown, unusable server disabled', async () => {
  api({ [OPTIONS]: () => [200, options] });
  show();
  expect(await screen.findByRole('checkbox', { name: 'web-1' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Payments' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'prod-1' })).toBeDisabled();
  expect(screen.getByText('SSH is not set up for this server.')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'SSH' })).toBeChecked();
  expect(screen.queryByRole('checkbox', { name: 'Read file' })).toBeNull();  // no code project or collection ticked
  expect(screen.getByRole('checkbox', { name: 'Read the document' })).toBeDisabled();
  expect(screen.getByRole('switch', { name: 'Allow subagents' })).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Shop' }));
  expect(screen.getByRole('checkbox', { name: 'Read file' })).toBeChecked();
});

test('Preview shows the JSON and Start opens the run page', async () => {
  const calls = api({ [OPTIONS]: () => [200, options],
    'POST /api/agent-sessions/preview': () => [200, { config: { model: { name: 'Claude' } }, first_message: 'Add log rotation.' }],
    'POST /api/agent-sessions': () => [201, { id: 'run-1' }] });
  const router = show();
  fireEvent.change(await screen.findByLabelText('Instruction'), { target: { value: 'Add log rotation.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  const preview = await screen.findByLabelText('Configuration preview');
  expect(preview.textContent).toContain('"first_message": "Add log rotation."');
  expect(calls.find(c => c.key === 'POST /api/agent-sessions/preview')?.body).toEqual(expected);
  fireEvent.click(screen.getByRole('button', { name: 'Start' }));
  await waitFor(() => expect(router.state.location.pathname).toBe('/documents/doc-1/agent/run-1'));
  expect(calls.find(c => c.key === 'POST /api/agent-sessions')?.body).toEqual(expected);
});

test('a refusal is shown in the sheet', async () => {
  api({ [OPTIONS]: () => [200, options],
    'POST /api/agent-sessions': () => [400, { error: { message: 'ssh_run needs at least one server.' } }] });
  show();
  fireEvent.change(await screen.findByLabelText('Instruction'), { target: { value: 'Add log rotation.' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'web-1' }));
  fireEvent.click(screen.getByRole('button', { name: 'Start' }));
  expect(await screen.findByText('ssh_run needs at least one server.')).toBeInTheDocument();
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/askAgent.test.tsx`
Expected: FAIL (cannot resolve `../AskAgentSheet`).

- [ ] **Step 4: Write `AskAgentSheet.tsx`**

Follow `DiscoverySheet.tsx` for the sheet, field and select markup. Behaviour:

```tsx
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { ApiError } from '~/lib/api';
import { agentRunPath, getAgentOptions, previewAgentSession, startAgentSession, toolApplies,
  type AgentOptions, type AgentResources, type StartInput } from '~/lib/agentSessions';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '~/components/ui/collapsible';
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '~/components/ui/sheet';
import { Skeleton } from '~/components/ui/skeleton';
import { Spinner } from '~/components/ui/spinner';
import { Switch } from '~/components/ui/switch';
import { Textarea } from '~/components/ui/textarea';

const KINDS: [keyof AgentOptions['resources'], keyof AgentResources, string][] = [
  ['servers', 'server_ids', 'Servers'], ['services', 'service_ids', 'Services'], ['environments', 'environment_ids', 'Environments'],
  ['code_projects', 'code_project_ids', 'Code projects'], ['collections', 'collection_ids', 'Collections']];
const GROUPS: [string, string][] = [['code', 'Code'], ['documents', 'Documents'], ['servers', 'Servers'], ['inventory', 'Inventory']];
const BUILT_IN = 'built-in';

/** Ask agent on a document: an instruction, a model, the servers and resources (the document's links ticked), the
 * tools that apply (all ticked), subagents and experts (off), the system prompt under Advanced; Preview shows exactly
 * what Start saves; Start opens the run page. */
export default function AskAgentSheet({ documentId, documentTitle, onClose }: { documentId: string; documentTitle: string; onClose: () => void }) {
  const navigate = useNavigate();
  const [options, setOptions] = useState<AgentOptions | null>(null);
  const [instruction, setInstruction] = useState('');
  const [modelId, setModelId] = useState('');
  const [promptKey, setPromptKey] = useState(BUILT_IN);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [chosen, setChosen] = useState<AgentResources>({ server_ids: [], service_ids: [], environment_ids: [], code_project_ids: [], collection_ids: [] });
  const [off, setOff] = useState<string[]>([]);  // tools the person unticked
  const [subagents, setSubagents] = useState(false);
  const [experts, setExperts] = useState<string[]>([]);
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getAgentOptions(documentId, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setOptions(value); setModelId(value.default_model_id ?? '');
      const first = value.prompts[0];
      setPromptKey(first ? first.id : BUILT_IN); setSystemPrompt(first ? first.content : value.default_prompt.content);
      const linked = (key: keyof AgentOptions['resources']) => value.resources[key]
        .filter(item => item.linked && (!('usable' in item) || item.usable)).map(item => item.id);
      setChosen({ server_ids: linked('servers'), service_ids: linked('services'), environment_ids: linked('environments'),
        code_project_ids: linked('code_projects'), collection_ids: linked('collections') });
    }).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load the options.'); });
    return () => controller.abort();
  }, [documentId]);

  const shown = useMemo(() => options?.tools.filter(tool => toolApplies(tool, chosen)) ?? [], [options, chosen]);
  const value = (): StartInput => ({
    kind: 'revision', target: { document_id: documentId }, instruction: instruction.trim(), model_id: modelId,
    prompt_id: promptKey === BUILT_IN ? null : promptKey, system_prompt: systemPrompt.trim(), resources: chosen,
    tools: shown.filter(tool => !off.includes(tool.name)).map(tool => tool.name), expert_ids: experts, subagents });
  async function act(run: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await run(); } catch (e) { setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : 'The request failed.'); }
    finally { setBusy(false); }
  }
  const toggle = (key: keyof AgentResources, id: string, on: boolean) => setChosen(current => ({ ...current,
    [key]: on ? [...current[key], id] : current[key].filter(item => item !== id) }));
  const ready = !busy && !!modelId && !!instruction.trim() && !!systemPrompt.trim();
  // … markup: Sheet (title "Ask agent", description `An agent revises “${documentTitle}”. It saves a draft; you
  // review it and decide whether it becomes the live version.`), then in a FieldGroup:
  //  - error Alert;
  //  - Field "Instruction": <Textarea id="ask-instruction" aria-label="Instruction" rows={6} autoFocus …>;
  //  - Field "Model": Select over options.models (aria-label "Model");
  //  - FieldSet "Servers and resources": for each KINDS entry with items, a FieldLegend and a checkbox per item
  //    (<Checkbox id={`ask-${key}-${item.id}`} aria-label={item.name} …>, disabled when a server is not usable, its
  //    reason in a FieldDescription below it);
  //  - FieldSet "Tools": the fixed tools first as checked, disabled checkboxes (aria-label = label); then each GROUPS
  //    entry that has shown tools, a legend and a checkbox per tool (aria-label = tool.label, checked = !off.includes);
  //  - FieldSet "Agents": <Switch aria-label="Allow subagents">; experts as checkboxes (aria-label = name) with
  //    when_to_use as description; none listed → "No experts.";
  //  - Collapsible "Advanced": Select "Prompt" (options.prompts plus "Built-in" with value BUILT_IN; choosing one
  //    sets systemPrompt to its content) and Textarea "System prompt" (FieldDescription "Changes apply to this run
  //    only; the prompt in the library stays as it is.");
  //  - preview !== null → <pre aria-label="Configuration preview" tabIndex={0} className="max-h-96 overflow-auto
  //    whitespace-pre-wrap break-words text-xs">{preview}</pre>;
  //  - buttons: Cancel (onClose), Preview (disabled={!ready}; act: const result = await previewAgentSession(value());
  //    setPreview(JSON.stringify(result, null, 2))), Start (disabled={!ready}; act: const run = await
  //    startAgentSession(value()); navigate(agentRunPath(documentId, run.id))).
  //  While options is null and no error: <Skeleton className="h-96" />.
}
```

Write the markup exactly as the comment lists (it is the specification of the JSX; the test pins the labels). The
Preview JSON is `{config, first_message}` as returned. Unticking a tool adds it to `off`; ticking removes it, so a
tool that newly applies (a resource ticked later) starts ticked.

- [ ] **Step 5: Add the button to the document page**

In `DocumentEditorView.tsx`: `const [asking, setAsking] = useState(false);`; before the `Save draft` button:

```tsx
      {document && <Button type="button" variant="outline" disabled={pending} onClick={() => setAsking(true)}><Bot data-icon="inline-start" />Ask agent</Button>}
```

(`Bot` from `lucide-react`), and after the header block:
`{document && asking && <AskAgentSheet documentId={document.id} documentTitle={document.title} onClose={() => setAsking(false)} />}`.

`lib/generations.ts`: `export type SessionPurpose = 'documents' | 'test' | 'event' | 'discovery' | 'revision';` and in
`SessionDetail` add `/** Ask agent runs: the document they revise. */ target_document_id?: string | null;`.

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/askAgent.test.tsx app/components/__tests__/documentPageDrafts.test.tsx && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 7: Commit**

```bash
git add services/admin-ui/app/lib/agentSessions.ts services/admin-ui/app/components/AskAgentSheet.tsx \
  services/admin-ui/app/components/DocumentEditorView.tsx services/admin-ui/app/lib/generations.ts \
  services/admin-ui/app/components/__tests__/askAgent.test.tsx
git commit -m "feat(admin-ui): Ask agent on a document — instruction, model, resources, tools, agents, Preview and Start

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: UI — run page, Agent runs tab, links, docs

**Files:**
- Create: `services/admin-ui/app/components/DocumentAgentRunView.tsx`, `services/admin-ui/app/routes/documentAgentRun.tsx`
- Create: `services/admin-ui/app/components/DocumentAgentRuns.tsx`
- Modify: `services/admin-ui/app/routes.ts` (route `documents/:documentId/agent/:runId` before `documents/:documentId`)
- Modify: `services/admin-ui/app/components/DocumentEditorView.tsx` (`TABS` gains `'agent-runs'`; tab `Agent runs`)
- Modify: `services/admin-ui/app/components/DraftView.tsx` (`DraftAuthor` links a revision run to its run page)
- Modify: `services/admin-ui/app/lib/drafts.ts` (`DraftSummary.session_purpose?: SessionPurpose | null`)
- Modify: `services/admin-ui/app/components/GenerationView.tsx` (a revision session redirects to its run page)
- Modify: `docs/user-guide.md` (section `4.13 Documents` → a new `#### Ask agent` subsection after `#### Drafts and
  connections`), `docs/documentation.md` (one paragraph where drafts are described)
- Create: `services/admin-ui/app/components/__tests__/documentAgentRun.test.tsx`

**Interfaces:**
- Consumes: Task 4 `lib/agentSessions.ts`; `useSessionEvents`, `useSessionNotice`, `RunWorkspace`,
  `closeSession`, `sendMessage`, `stopSession` (as `DiscoveryRunView.tsx`); `getDraft`, `draftPath` from `lib/drafts`.
- Produces: route `/documents/:documentId/agent/:runId`.

- [ ] **Step 1: Write the failing UI tests**

`services/admin-ui/app/components/__tests__/documentAgentRun.test.tsx`:

```tsx
import { afterEach, expect, test, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router";
import DocumentAgentRunView, { savedIn } from "../DocumentAgentRunView";
import DocumentAgentRuns from "../DocumentAgentRuns";
import { DraftAuthor } from "../DraftView";
import type { Turn } from "~/lib/turns";

const live = vi.hoisted(() => ({ purpose: "revision" }));
vi.mock("~/lib/useSessionEvents", () => ({
  useSessionEvents: () => ({ session: { id: "r1", title: "Add a section on log rotation.", status: "waiting", purpose: live.purpose,
    agent_name: "Ad-hoc agent", model_name: "GPT", turns: 2, target_document_id: "doc-1", config: { model: { name: "GPT" } },
    document_draft: { id: "d1", document_id: "doc-1", status: "open" } },
    setSession: vi.fn(), error: "", setError: vi.fn(), events: [], older: "start", loadOlder: vi.fn(), toolCounts: undefined, reconnecting: false }),
  useSessionNotice: () => ({ notice: null, clear: vi.fn(), stopping: vi.fn(), sent: vi.fn() }),
}));
const turn = (n: number, work: Turn["work"]): Turn => ({ turn: n, firstId: n, input: null, work, toolCalls: work.length, answer: "Done.",
  grounding: null, status: "completed", error: null, usage: { input: 0, output: 0, cached: 0, reasoning: 0, total: 0, calls: 0 }, subagents: [] });
const saved = turn(1, [{ id: 1, kind: "tool", text: "", name: "save_doc", preview: "Saved draft (3 lines)." }]);
const chat = turn(2, [{ id: 2, kind: "tool", text: "", name: "get_doc", preview: "Title: Runbook" }]);
vi.mock("../RunWorkspace", () => ({ default: ({ heading, headerActions, sidebar, answerActions }: Record<string, any>) =>
  <div>{heading}{headerActions}{sidebar}<ol aria-label="Turns">{[saved, chat].map(t => <li key={t.turn}>{answerActions(t)}</li>)}</ol></div> }));

function api(routes: Record<string, () => [number, unknown]>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    if (!(key in routes)) throw new Error(`unexpected ${key}`);
    const [status, value] = routes[key]();
    return new Response(JSON.stringify(value), { status });
  }));
}
afterEach(() => { vi.unstubAllGlobals(); live.purpose = "revision"; });
const at = "2026-10-12T10:00:00Z";

test("the run page links the document, shows the draft and notes a turn without a save", async () => {
  const user = userEvent.setup();
  api({ "GET /api/documentation/documents/doc-1": () => [200, { id: "doc-1", title: "Payments runbook" }],
    "GET /api/documentation/drafts/d1": () => [200, { id: "d1", document_id: "doc-1", base_version: 2, stale: true, status: "open" }] });
  render(<MemoryRouter><DocumentAgentRunView documentId="doc-1" runId="r1" /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: "Payments runbook" })).toHaveAttribute("href", "/documents/doc-1");
  const panel = screen.getByRole("region", { name: "Draft panel" });
  expect(await within(panel).findByText(/based on version 2/)).toBeInTheDocument();
  expect(within(panel).getByText("stale")).toBeInTheDocument();
  expect(within(panel).getByRole("link", { name: "Open draft" })).toHaveAttribute("href", "/documents/doc-1?tab=drafts&draft=d1");
  const turns = within(screen.getByRole("list", { name: "Turns" })).getAllByRole("listitem");
  expect(within(turns[0]).queryByText("No draft saved in this turn")).toBeNull();
  expect(within(turns[1]).getByText("No draft saved in this turn")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Show config" }));
  expect((await screen.findByLabelText("Run configuration")).textContent).toContain('"name": "GPT"');
});

test("a session that is not an Ask agent run redirects to the Generate documents page", async () => {
  live.purpose = "documents";
  api({ "GET /api/documentation/documents/doc-1": () => [200, { id: "doc-1", title: "Payments runbook" }] });
  const router = createMemoryRouter([{ path: "/documents/:documentId/agent/:runId", element: <DocumentAgentRunView documentId="doc-1" runId="r1" /> },
    { path: "/documentation/generations/:id", element: <p>Generation</p> }], { initialEntries: ["/documents/doc-1/agent/r1"] });
  render(<RouterProvider router={router} />);
  expect(await screen.findByText("Generation")).toBeInTheDocument();
});

test("savedIn counts only a successful save_doc or edit_doc", () => {
  expect(savedIn(saved)).toBe(true);
  expect(savedIn(chat)).toBe(false);
  expect(savedIn(turn(3, [{ id: 3, kind: "tool", text: "", name: "edit_doc", preview: "This text is not in the document.", isError: true }]))).toBe(false);
});

test("the Agent runs tab lists the runs with their drafts", async () => {
  api({ "GET /api/agent-sessions?document_id=doc-1": () => [200, { sessions: [
    { id: "r2", title: "Remove the old backup steps.", status: "waiting", model_name: "GPT", started_by: "a@b.c", created_at: at, last_activity_at: at, draft_id: "d1" },
    { id: "r1", title: "Add a section on log rotation.", status: "closed", model_name: "Claude", started_by: null, created_at: at, last_activity_at: at, draft_id: null }] }] });
  render(<MemoryRouter><DocumentAgentRuns documentId="doc-1" /></MemoryRouter>);
  const first = await screen.findByRole("link", { name: "Remove the old backup steps." });
  expect(first).toHaveAttribute("href", "/documents/doc-1/agent/r2");
  expect(screen.getByText(/Waiting for you · GPT · a@b.c/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open draft" })).toHaveAttribute("href", "/documents/doc-1?tab=drafts&draft=d1");
  expect(screen.getAllByRole("link", { name: "Open draft" })).toHaveLength(1);
});

test("an empty Agent runs tab says how to start one", async () => {
  api({ "GET /api/agent-sessions?document_id=doc-1": () => [200, { sessions: [] }] });
  render(<MemoryRouter><DocumentAgentRuns documentId="doc-1" /></MemoryRouter>);
  expect(await screen.findByText("No agent runs yet. Ask agent starts one.")).toBeInTheDocument();
});

test("a draft written by an Ask agent run links to its run page", () => {
  const base = { origin: "agent" as const, session_id: "s1", session_title: "Revise", author: null, document_id: "doc-1" };
  const { rerender } = render(<MemoryRouter><DraftAuthor draft={{ ...base, session_purpose: "revision" }} /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "Agent run" })).toHaveAttribute("href", "/documents/doc-1/agent/s1");
  rerender(<MemoryRouter><DraftAuthor draft={{ ...base, session_purpose: "documents" }} /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "Agent run" })).toHaveAttribute("href", "/documentation/generations/s1");
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/documentAgentRun.test.tsx`
Expected: FAIL (missing modules).

- [ ] **Step 3: Write the run page**

`DocumentAgentRunView.tsx`, modelled line by line on `DiscoveryRunView.tsx`:

```tsx
/** One Ask agent run: the transcript (as Generate documents), the document it revises, its draft in the sidebar, Show
 * config (the saved JSON) and Close; a finished turn without a successful save_doc or edit_doc says so. */
export default function DocumentAgentRunView({ documentId, runId }: { documentId: string; runId: string }) {
  // const run = useSessionEvents(runId); session, notice, act(), message state — exactly as DiscoveryRunView.
  // Document title: getDocument(documentId) once (title only; on failure show "Document").
  // Draft: when session.document_draft?.id changes (or status changes to waiting), getDraft(id) → base_version, stale.
  // if (!session) → error Alert or Skeleton (as DiscoveryRunView);
  // if (session.purpose !== 'revision') → <Navigate replace to={`/documentation/generations/${encodeURIComponent(runId)}`} />.
  // heading: <Link to={`/documents/${encodeURIComponent(documentId)}`} className="self-start underline">{title}</Link>,
  //          <h1 className="break-words text-2xl font-semibold">{session.title}</h1>, model name line.
  // headerActions: "Show config" (opens a Sheet titled "Run configuration" with
  //   <pre aria-label="Run configuration" …>{JSON.stringify(session.config, null, 2)}</pre>), "Close run" (as discovery).
  // sidebar: <section aria-label="Draft panel"><h2>Draft</h2> …
  //   no document_draft → "The agent's draft appears here when it saves the document."
  //   open → "based on version {base_version}" + stale Badge + <Link to={draftPath(...)}>Open draft</Link>
  //          (draftPath from lib/drafts gives /documents/doc-1?tab=drafts&draft=d1)
  //   promoted → "Promoted." + link "Open document"; discarded → "Discarded. The next save starts a new draft."
  // answerActions: turn => <>{!savedIn(turn) && <span className="text-xs text-muted-foreground">No draft saved in this turn</span>}
  //   <Button variant="ghost" size="sm" asChild><Link to={runPath(`cc:${session.id}:${turn.turn}`)}>Full record</Link></Button></>
  // composer: as discovery; blockedReason closed → "This run is closed. Start a new one with Ask agent on the document.";
  //   placeholder "For example: also remove the old cron section".
}

const SAVE_TOOLS = ['save_doc', 'edit_doc'];
export const savedIn = (turn: Turn) => turn.work.some(item => item.kind === 'tool' && SAVE_TOOLS.includes(item.name ?? '')
  && !item.isError && (item.preview ?? '').startsWith('Saved draft'));
```

Write the component in full following those comments and `DiscoveryRunView.tsx`'s structure (same imports, same
`useFillViewport` wrapper).

`routes/documentAgentRun.tsx`:

```tsx
import { useParams } from "react-router";
import AdminLayout from "../components/AdminLayout";
import DocumentAgentRunView from "../components/DocumentAgentRunView";

export default function Page() {
  const { documentId, runId } = useParams();
  return <AdminLayout><DocumentAgentRunView key={runId} documentId={documentId!} runId={runId!} /></AdminLayout>;
}
```

`routes.ts`: add `route("documents/:documentId/agent/:runId", "./routes/documentAgentRun.tsx"),` immediately before
`route("documents/:documentId", …)`.

- [ ] **Step 4: Agent runs tab, links, redirect**

- `DocumentAgentRuns.tsx`: loads `getDocumentAgentRuns(documentId)` (abortable, as `DocumentDrafts.tsx`); renders a
  list: each row a `Link` to `agentRunPath(documentId, run.id)` with the title, then
  `${statusLabels[run.status]} · ${run.model_name} · ${run.started_by ?? 'Someone'} · ${new Date(run.last_activity_at).toLocaleString()}`,
  and `Open draft` (`draftPath({ id: run.draft_id, document_id: documentId })`) when `draft_id`; empty: `No agent runs
  yet. Ask agent starts one.`
- `DocumentEditorView.tsx`: `TABS` adds `'agent-runs'`; a `TabsTrigger value="agent-runs" disabled={!document}` labelled
  `Agent runs` after Connections, and `<TabsContent value="agent-runs">{document && <DocumentAgentRuns documentId={document.id} />}</TabsContent>`.
- `DraftView.tsx` `DraftAuthor`: its prop type adds `'session_purpose' | 'document_id'` (both optional in
  `DraftSummary`); the agent link is
  `draft.session_purpose === 'revision' && draft.document_id ? agentRunPath(draft.document_id, draft.session_id) : \`/documentation/generations/${…}\``.
- `lib/drafts.ts`: `DraftSummary` gains `session_purpose?: SessionPurpose | null` (import the type from `./generations`).
- `GenerationView.tsx`: after the discovery redirect add
  `if (session.purpose === 'revision' && session.target_document_id) return <Navigate replace to={agentRunPath(session.target_document_id, session.id)} />;`.

- [ ] **Step 5: Docs**

`docs/user-guide.md`, after `#### Drafts and connections`:

```markdown
#### Ask agent

**Ask agent** on a document's page starts an agent that changes this document for you. Write what you want
("Add a section on log rotation; remove the old backup steps"), choose the model, and check the servers and other
resources it may use: the ones linked to the document are ticked. A server whose SSH is not set up or not allowed
cannot be chosen. Under **Tools**, untick what the agent must not use (for example SSH, so it only reads what is
recorded); reading and saving the document are always on. Subagents and experts are off unless you turn them on.
**Advanced** holds the system prompt (prompts tagged `document-revision` in Prompts, or the built-in one); changes
apply to this run only. **Preview** shows exactly what the run will start with, as JSON.

The agent reads the document part by part and saves its changes as a draft of the document — never the live version.
Its replies are a normal conversation: it may ask you something first, and you can ask for more changes; each save
updates the same draft. The run page shows the draft (Open draft to compare and promote it) and says when a turn
ended without a save. If you edit, promote or discard the draft meanwhile, the agent's next save starts a new draft.
The **Agent runs** tab lists the document's runs, open ones first. Deleting the document deletes its runs; their
drafts are deleted with it.
```

`docs/documentation.md`: one paragraph after the drafts description: "Ask agent runs (agent sessions with purpose
`revision`, started through `/api/agent-sessions`) change a document only through the `get_doc`, `save_doc` and
`edit_doc` tools, which write the run's draft; Generate documents runs still turn their final answer into a draft."

- [ ] **Step 6: Run the tests**

Run: `cd services/admin-ui && npx vitest run && npx tsc --noEmit && npx react-router routes >/dev/null`
Expected: all tests pass, no type errors, routes resolve.

- [ ] **Step 7: Commit**

```bash
git add services/admin-ui/app/components/DocumentAgentRunView.tsx services/admin-ui/app/routes/documentAgentRun.tsx \
  services/admin-ui/app/components/DocumentAgentRuns.tsx services/admin-ui/app/routes.ts \
  services/admin-ui/app/components/DocumentEditorView.tsx services/admin-ui/app/components/DraftView.tsx \
  services/admin-ui/app/lib/drafts.ts services/admin-ui/app/components/GenerationView.tsx \
  services/admin-ui/app/components/__tests__/documentAgentRun.test.tsx docs/user-guide.md docs/documentation.md
git commit -m "feat(admin-ui): Ask agent run page, Agent runs tab and run links; docs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Self-review

- Spec coverage: migration and cascade (Task 1); tools, working copy, notes, lock order, sources, closed/deleted,
  final answer never a draft (Task 2); options, preview = start, refusals, experts/subagents defaults, list, session
  routes, audit (Task 3); sheet with Preview (Task 4); run page, no-save note, Agent runs tab, Drafts tab link (Task 5).
- Review Focus items each have a named test in the owning task.
- Names used across tasks: `REVISION_TOOLS`, `RUN_ONLY_NEEDS`, `outline`, `refresh_run_sources_safely`,
  `DEFAULT_REVISION_PROMPT`, `agentRunPath`, `toolApplies`, `savedIn`, `session_purpose`, `target_document_id`.
