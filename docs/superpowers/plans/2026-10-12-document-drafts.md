# Document Drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents write only drafts; a person promotes a draft to the live version or discards it. Drafts record what
they were built from, people link documents by hand ("depends on"), the documents list counts drafts and the document
page shows drafts (with a diff against the live version) and connections.

**Architecture:** Migration 0098 adds `ka.document_drafts`, `ka.document_sources` and `ka.document_links`, all outside
`ka.documents` / `ka.document_versions`, so `document_details`, exports, folder statistics, version history and the
inventory and RAG tools keep seeing live versions only. A new module `knowledge_admin/document_drafts.py` (its own
router under `/api/documentation`, mounted in `api.py` like `documents.py`) creates, saves, discards and promotes drafts
(promote goes through the existing `save_document`), serves the draft reads, connections and links, and holds the
worker's "turn draft → document draft" code that `SessionRunner.finish_turn` calls in the turn's transaction. The admin
UI gets a drafts library (`lib/drafts.ts`), a small in-house line diff, a draft view used on the document page's Drafts
tab and on a new page `/documents/drafts/:draftId`, a Connections tab, Save draft in the editor, the list's Drafts
column / filter / New drafts section, Open draft / Save as draft on the run page and a confirmation in the discovery
proposal panel.

**Tech Stack:** Postgres 17 (golang-migrate SQL), FastAPI + psycopg 3 + pydantic 2, React Router 7 + shadcn/ui,
vitest + Testing Library, pytest.

**Spec:** `docs/superpowers/specs/2026-10-12-document-drafts-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/document-drafts` on branch `feature/document-drafts`.
- Never run `make` targets. Never touch `.env` or anything under `.local`. Never `git stash`. Never touch the running
  Docker stack or its database (backend tests use their own disposable PostgreSQL through the `store` fixture). Do not
  run `db/tests/run.sh` (it starts Docker containers); check it with `bash -n db/tests/run.sh` only.
- Never `git add .superpowers`; never commit `node_modules` or `services/admin-ui/build`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd services/admin-api && uv run pytest -q <files>`. UI: `cd services/admin-ui && npx vitest run
  <files>`, `npx tsc --noEmit` and `npx react-router routes >/dev/null` (if `node_modules` is missing, run
  `pnpm install --frozen-lockfile` there — pnpm, not npm).
- No new dependencies (Python or npm). The diff view uses an in-house LCS line diff (`app/lib/lineDiff.ts`).
- Error bodies stay `{"error": {"message": ...}}`; a refusal that the UI must recognise adds `"code"` beside the message
  (`{"error": {"message": ..., "code": "STALE_BASE"}}`). Request-validation errors are 400, not 422.
- Promote, create and the new-document conversion lock the project first (`SELECT id FROM ka.projects WHERE id=%s FOR
  NO KEY UPDATE`), then upsert the actor, then lock the draft `FOR UPDATE`.
- Drafts are never exported, never in `ka.document_details`, never in version history, never returned by
  `read_document`, `search_documents`, `get_document` or the inventory tools, never in the folder statistics.
- Messages, exactly (spec):
  - "This draft changed. Reload it before saving; your text has not been overwritten." (409)
  - "This draft was already promoted or discarded." (409)
  - "This draft was based on version {base}; the live version is now {live}. Promoting replaces version {live}'s
    text." (409, code `STALE_BASE`)
  - "A document named {slug} was created in this folder meanwhile. The draft is now a draft of it. Review it again."
    (409)
  - Discovery panel: "This replaces the live version {n} of “{title}”. Continue?"
- Messages this plan adds (the spec asks for "a clear message"): "Draft not found." (404) · "A document cannot depend on
  itself." (400) · "This document already depends on that document." (409) · "This link does not exist." (404). A
  missing document answers the existing "Document not found" (404).
- Audit actions: `document.draft.created`, `document.draft.updated`, `document.draft.promoted`,
  `document.draft.discarded`, `document.link.added`, `document.link.removed`.
- Known pre-existing backend failures (not caused by this work): `test_code_graphs_config`, `test_code_processing`,
  `test_code_graph_sdk` (no code-graph `node_modules` in a worktree), `test_deploy` docs test,
  `test_environments.py::test_migration_reapply_and_populated_rollback_protection`,
  `test_agent_tasks::test_the_limit_comes_from_the_environment` (timing flake). A docker-port `IndexError` in the
  postgres fixture is a flake: re-run. In the UI, `skills.test.tsx` can fail under full-suite load; it passes alone.

## Review Focus

1. **Promote clicked twice, or by two people at once:** exactly one new live version; the second request answers "This
   draft was already promoted or discarded." Tested in Task 2 (`test_promote_saves_a_live_version_with_the_drafts_sources`).
2. **A resource chosen for a new document is deleted (or its code project archived) before the draft is promoted:**
   the document is created with the resources that still exist — never a 400 "A selected resource is no longer
   available". Tested in Task 2 (`test_a_new_document_draft_creates_the_document_with_the_resources_that_still_exist`).
3. **A source document is deleted after the draft read it:** the draft and the Connections tab still show its title and
   version with "(deleted)"; promoting copies the snapshot. Tested in Task 3
   (`test_connections_show_what_the_live_version_was_built_from_even_when_a_source_is_gone`).
4. **The document draft of a finished turn cannot be saved (a constraint fails, a bug):** the turn still ends normally
   with its run draft; only the document draft is skipped and logged. Tested in Task 4
   (`test_a_document_draft_that_cannot_be_saved_never_fails_the_turn`).
5. **A very long document in the diff view:** the comparison falls back to "removed, then added" instead of freezing the
   browser. Tested in Task 5 (`lineDiff.test.ts`: "a change too large to compare …").

---

## Rulings on points the spec leaves open

- A draft of an existing document carries text and metadata only (title, description, purpose, audience, tags,
  Markdown); promote keeps the document's slug, folder and linked resources. The editor's Linked resources tab is not
  part of a draft of an existing document.
- `POST /drafts` for a new document whose folder and slug already name a document creates a draft of that document
  (as the worker does) instead of refusing.
- On promote of a new document, `resource_ids` that no longer exist are dropped (archived code projects too; a
  deployment stays only with its service and server).
- A promoted draft always points at its document: promoting a new-document draft sets `document_id` and clears
  `slug`/`folder` in the same update.
- The "frozen" trigger refuses changes to a decided draft's content, target, status, revision and decision; it allows
  the `ON DELETE SET NULL` of `session_id` and `base_version_id` (otherwise deleting a run or a document would fail).
  `promoted_version_id` is `ON DELETE CASCADE` (a version is only deleted with its document, which deletes the draft).
- The down migration refuses while open drafts, any source or any link exist (spec wording); promoted and discarded
  drafts without sources are dropped with the table.
- Worker sources: a document read several times is recorded once, at the version live at its first read; a read of the
  draft's own document is not a `read` source (it is the `parent`); a call made before the document existed is skipped.
  `read_document` paths match `<folder>/<slug>/README.md` with or without the `GENERATED_DOCS_FOLDER` prefix.
- An agent draft of an existing document keeps the live title/description/purpose/audience/tags and replaces the
  Markdown; an agent draft of a new document is titled with the run's document name, purpose `code` when the run chose
  code projects, else `explanation`.
- `POST /generations/sessions/{id}/document` takes no body now (an empty `{}` is accepted) and answers
  `{"draft": {"id", "document_id"}}`; promoting a run's draft sets `agent_sessions.document_id`, so the Generate
  documents list keeps showing the saved document.
- The session detail and the live `session` state carry `document_draft` (the run's latest open document draft:
  `{"id", "document_id"}` or null). The run page shows **Open draft** when it is set, else **Save as draft** when the run
  has a run draft; the run draft preview button becomes **Preview draft**.
- On `/documents/new`, **Save draft** creates a new-document draft (no folder: the editor has none) and opens its page.
- After **Save draft** or a promote on the document page, the editor reloads the live version and opens the Drafts tab
  (with the saved draft selected).

## File structure

| File | Responsibility |
|---|---|
| `db/migrations/0098_document_drafts.up.sql` / `.down.sql` | the three tables, the freeze trigger, indexes; down refuses with data |
| `services/admin-api/src/knowledge_admin/document_drafts.py` | models, draft create/save/discard/promote, reads, connections, links, the worker's run-draft save |
| `services/admin-api/src/knowledge_admin/documents.py` | list: `draft_count`, `has_drafts`; detail: `from_draft` per version |
| `services/admin-api/src/knowledge_admin/app.py` | error handler passes a dict detail (message + code) through |
| `services/admin-api/src/knowledge_admin/api.py` | mount the drafts router |
| `services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py` | `finish_turn` saves the document draft in the turn's transaction |
| `services/admin-api/src/knowledge_admin/generations_api.py` | Save as draft; `document_draft` in session rows |
| `services/admin-api/src/knowledge_admin/live/session_hub.py` | `document_draft` in the live session state |
| `services/admin-ui/app/lib/drafts.ts`, `lib/lineDiff.ts` | API calls and types; the line diff |
| `services/admin-ui/app/components/DiffView.tsx`, `DraftView.tsx`, `DraftEditForm.tsx`, `DraftPageView.tsx` | the draft view (diff/preview, sources, Edit/Promote/Discard), the new-document draft page |
| `services/admin-ui/app/components/DocumentDrafts.tsx`, `DocumentConnections.tsx` | the document page's Drafts and Connections tabs |
| `services/admin-ui/app/components/DocumentsView.tsx`, `DocumentEditorView.tsx` | list column/filter/New drafts; Save draft, tabs, "from draft" |
| `services/admin-ui/app/components/GenerationView.tsx`, `ProposalSheet.tsx` | Open draft / Save as draft; replace confirmation |

---

### Task 1: Migration 0098 — drafts, sources, links

**Files:**
- Create: `db/migrations/0098_document_drafts.up.sql`, `db/migrations/0098_document_drafts.down.sql`
- Modify: `db/tests/run.sh`, `docs/migrations.md`, `docs/database.md`
- Test: `services/admin-api/tests/test_document_drafts_migration.py` (new)

**Interfaces:**
- Produces: `ka.document_drafts(id, project_id, document_id, slug, folder, resource_ids jsonb, base_version_id, title,
  description, purpose, audience, tags, markdown, origin ('manual'|'agent'), session_id, edited, status
  ('open'|'promoted'|'discarded'), promoted_version_id, created_by, updated_by, decided_by, decided_at, revision,
  created_at, updated_at)`; `ka.document_sources(id, project_id, draft_id, version_id, kind ('parent'|'read'),
  source_document_id, source_version_id, source_title, source_version, created_at)`; `ka.document_links(id,
  project_id, from_document_id, to_document_id, kind ('depends_on'), created_by, created_at)`; trigger function
  `ka.freeze_decided_document_draft()` raising `check_violation` with "A promoted or discarded draft cannot change".

- [ ] **Step 1: Write the failing test** — `services/admin-api/tests/test_document_drafts_migration.py`:

```python
"""0098: document drafts, what drafts and versions were built from, and document links."""
from pathlib import Path

import psycopg
import pytest

from knowledge_admin.documents import DocumentInput, save_document

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0098_document_drafts.up.sql', '0098_document_drafts.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def world(conn, store):
    project = store.project(conn, 'shop')
    runbook = save_document(conn, project, None, DocumentInput(slug='runbook', title='Runbook', markdown='# Runbook'))
    overview = save_document(conn, project, None, DocumentInput(slug='overview', title='Overview', markdown='# Overview'))
    return project, runbook, overview


def draft(conn, project, **columns):
    values = {'project_id': project, 'title': 'Draft', 'markdown': '# Draft', 'origin': 'manual', **columns}
    names, marks = ','.join(values), ','.join(['%s'] * len(values))
    return conn.execute(f'INSERT INTO ka.document_drafts({names}) VALUES ({marks}) RETURNING id',
                        tuple(values.values())).fetchone()['id']


def test_a_draft_revises_a_document_or_names_a_new_one(store):
    with store.connect() as conn:
        project, runbook, _ = world(conn, store)
        draft(conn, project, document_id=runbook['id'], base_version_id=runbook['version_id'])
        draft(conn, project, slug='disk-layout', folder='servers/web-1')
        for columns in ({}, {'document_id': runbook['id'], 'slug': 'runbook'},
                        {'document_id': runbook['id'], 'folder': 'docs'}, {'folder': 'docs'},
                        {'slug': 'x', 'origin': 'robot'}):
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                draft(conn, project, **columns)


def test_a_promoted_draft_has_its_version_and_a_decided_draft_is_frozen(store):
    with store.connect() as conn:
        project, runbook, _ = world(conn, store)
        sid = draft(conn, project, document_id=runbook['id'], base_version_id=runbook['version_id'])
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE ka.document_drafts SET status='promoted',decided_at=now() WHERE id=%s", (sid,))
        conn.execute("UPDATE ka.document_drafts SET status='discarded',decided_at=now() WHERE id=%s", (sid,))
        for change in ("title='Changed'", "status='open',decided_at=NULL", 'revision=revision+1'):
            with pytest.raises(psycopg.errors.CheckViolation, match='promoted or discarded'), conn.transaction():
                conn.execute(f'UPDATE ka.document_drafts SET {change} WHERE id=%s', (sid,))
        conn.execute('UPDATE ka.document_drafts SET base_version_id=NULL WHERE id=%s', (sid,))  # ON DELETE SET NULL
        other = draft(conn, project, document_id=runbook['id'], base_version_id=runbook['version_id'])
        conn.execute("""UPDATE ka.document_drafts SET status='promoted',decided_at=now(),promoted_version_id=%s
            WHERE id=%s""", (runbook['version_id'], other))


def test_sources_belong_to_a_draft_or_a_version_and_keep_their_snapshot(store):
    with store.connect() as conn:
        project, runbook, overview = world(conn, store)
        sid = draft(conn, project, document_id=runbook['id'], base_version_id=runbook['version_id'])
        add = '''INSERT INTO ka.document_sources(project_id,draft_id,version_id,kind,source_document_id,source_version_id,
            source_title,source_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)'''
        conn.execute(add, (project, sid, None, 'read', overview['id'], overview['version_id'], 'Overview', 1))
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(add, (project, sid, None, 'read', overview['id'], overview['version_id'], 'Overview', 1))
        for owner in ((None, None), (sid, runbook['version_id'])):
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(add, (project, *owner, 'read', runbook['id'], None, 'Runbook', 1))
        conn.execute(add, (project, None, runbook['version_id'], 'read', overview['id'], overview['version_id'], 'Overview', 1))
        conn.execute('DELETE FROM ka.documents WHERE id=%s', (overview['id'],))
        rows = conn.execute('''SELECT source_document_id,source_version_id,source_title,source_version
            FROM ka.document_sources''').fetchall()
        assert rows == [{'source_document_id': None, 'source_version_id': None, 'source_title': 'Overview',
                         'source_version': 1}] * 2


def test_links_refuse_self_and_duplicates_and_go_with_their_documents(store):
    with store.connect() as conn:
        project, runbook, overview = world(conn, store)
        add = 'INSERT INTO ka.document_links(project_id,from_document_id,to_document_id) VALUES (%s,%s,%s)'
        conn.execute(add, (project, runbook['id'], overview['id']))
        conn.execute(add, (project, overview['id'], runbook['id']))
        with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
            conn.execute(add, (project, runbook['id'], overview['id']))
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(add, (project, runbook['id'], runbook['id']))
        draft(conn, project, document_id=overview['id'], base_version_id=overview['version_id'])
        promoted = draft(conn, project, document_id=overview['id'], base_version_id=overview['version_id'])
        conn.execute("""UPDATE ka.document_drafts SET status='promoted',decided_at=now(),promoted_version_id=%s
            WHERE id=%s""", (overview['version_id'], promoted))
        conn.execute('DELETE FROM ka.documents WHERE id=%s', (overview['id'],))
        assert conn.execute('SELECT count(*) AS n FROM ka.document_links').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM ka.document_drafts').fetchone()['n'] == 0


def test_down_refuses_while_open_drafts_sources_or_links_exist_then_round_trips(store):
    with store.connect() as conn:
        project, runbook, overview = world(conn, store)
        sid = draft(conn, project, document_id=runbook['id'], base_version_id=runbook['version_id'])
        conn.commit()  # the migration files carry their own BEGIN/COMMIT
        with pytest.raises(psycopg.errors.RaiseException, match='would be lost'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute("UPDATE ka.document_drafts SET status='discarded',decided_at=now() WHERE id=%s", (sid,))
        conn.execute('INSERT INTO ka.document_links(project_id,from_document_id,to_document_id) VALUES (%s,%s,%s)',
                     (project, runbook['id'], overview['id']))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match='would be lost'):
            run(conn, DOWN)
        conn.rollback()
        conn.execute('DELETE FROM ka.document_links')
        conn.commit()
        run(conn, DOWN)
        assert conn.execute("SELECT to_regclass('ka.document_drafts') IS NULL AS gone").fetchone()['gone']
        run(conn, UP)
        assert conn.execute("SELECT to_regclass('ka.document_links') IS NOT NULL AS back").fetchone()['back']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_document_drafts_migration.py`
Expected: FAIL (`relation "ka.document_drafts" does not exist`).

- [ ] **Step 3: Write `db/migrations/0098_document_drafts.up.sql`**

```sql
BEGIN;
SET search_path = ka, public;

-- Drafts: unpublished versions of a document (document_id) or of a new document (slug, folder), promoted to live or
-- discarded by a person. Kept apart from document_versions, so version history, document_details, exports and folder
-- statistics see live versions only.
CREATE TABLE document_drafts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    document_id uuid,
    slug text CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(slug) <= 120),
    folder text CHECK (length(folder) <= 200 AND folder ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$'
        AND folder !~ '(^|/)node_modules(/|$)' AND split_part(folder, '/', 1) <> 'services'),
    resource_ids jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(resource_ids) = 'object'),
    base_version_id uuid REFERENCES document_versions(id) ON DELETE SET NULL,
    title nonblank NOT NULL CHECK (length(title) <= 240),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
    purpose nonblank NOT NULL DEFAULT 'explanation' CHECK (length(purpose) <= 80),
    audience text NOT NULL DEFAULT 'both' CHECK (audience IN ('people', 'agents', 'both')),
    tags text[] NOT NULL DEFAULT '{}' CHECK (cardinality(tags) <= 30),
    markdown nonblank NOT NULL CHECK (length(markdown) <= 1000000),
    origin text NOT NULL CHECK (origin IN ('manual', 'agent')),
    session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL,
    edited boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'promoted', 'discarded')),
    promoted_version_id uuid REFERENCES document_versions(id) ON DELETE CASCADE,
    created_by uuid REFERENCES principals(id),
    updated_by uuid REFERENCES principals(id),
    decided_by uuid REFERENCES principals(id),
    decided_at timestamptz,
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE,
    CONSTRAINT document_drafts_target CHECK ((document_id IS NULL) = (slug IS NOT NULL) AND (document_id IS NULL OR folder IS NULL)),
    CONSTRAINT document_drafts_promoted CHECK ((status = 'promoted') = (promoted_version_id IS NOT NULL)),
    CONSTRAINT document_drafts_decided CHECK ((status = 'open') = (decided_at IS NULL))
);
CREATE INDEX document_drafts_open ON document_drafts(document_id) WHERE status = 'open';
CREATE INDEX document_drafts_new ON document_drafts(project_id) WHERE status = 'open' AND document_id IS NULL;
CREATE INDEX document_drafts_session ON document_drafts(session_id) WHERE session_id IS NOT NULL;
CREATE INDEX document_drafts_promoted_version ON document_drafts(promoted_version_id) WHERE promoted_version_id IS NOT NULL;
CREATE TRIGGER touch_updated_at BEFORE UPDATE ON document_drafts
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
COMMENT ON TABLE document_drafts IS
    'Unpublished versions of a document (document_id, base_version_id: the live version it started from) or of a new document (slug, folder, resource_ids to link on promote). origin agent: written by a Generate documents run (session_id); edited: a person saved it. Promoted (promoted_version_id) or discarded drafts are kept and frozen.';

-- A decided draft never changes; only the ON DELETE SET NULL of its run and base version (and updated_at) may.
CREATE FUNCTION freeze_decided_document_draft() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'open' AND (NEW.status, NEW.document_id, NEW.slug, NEW.folder, NEW.resource_ids, NEW.title,
            NEW.description, NEW.purpose, NEW.audience, NEW.tags, NEW.markdown, NEW.origin, NEW.edited,
            NEW.promoted_version_id, NEW.revision, NEW.decided_by, NEW.decided_at)
        IS DISTINCT FROM (OLD.status, OLD.document_id, OLD.slug, OLD.folder, OLD.resource_ids, OLD.title,
            OLD.description, OLD.purpose, OLD.audience, OLD.tags, OLD.markdown, OLD.origin, OLD.edited,
            OLD.promoted_version_id, OLD.revision, OLD.decided_by, OLD.decided_at) THEN
        RAISE EXCEPTION 'A promoted or discarded draft cannot change' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER freeze_decided_document_draft BEFORE UPDATE ON document_drafts
    FOR EACH ROW EXECUTE FUNCTION freeze_decided_document_draft();

-- What a draft or a live version was built from: the document it revises (parent) and the documents its author read,
-- with a snapshot of title and version so a deleted source still shows "(deleted)".
CREATE TABLE document_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    draft_id uuid REFERENCES document_drafts(id) ON DELETE CASCADE,
    version_id uuid REFERENCES document_versions(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('parent', 'read')),
    source_document_id uuid REFERENCES documents(id) ON DELETE SET NULL,
    source_version_id uuid REFERENCES document_versions(id) ON DELETE SET NULL,
    source_title text NOT NULL CHECK (length(source_title) <= 240),
    source_version integer CHECK (source_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_sources_owner CHECK ((draft_id IS NULL) <> (version_id IS NULL))
);
CREATE UNIQUE INDEX document_sources_draft ON document_sources(draft_id, source_document_id) WHERE draft_id IS NOT NULL;
CREATE UNIQUE INDEX document_sources_version ON document_sources(version_id, source_document_id) WHERE version_id IS NOT NULL;
CREATE INDEX document_sources_source ON document_sources(source_document_id) WHERE source_document_id IS NOT NULL;
COMMENT ON TABLE document_sources IS
    'What a draft (draft_id) or live version (version_id) was built from: parent (the document revised) or read (a document its author read), with the version live then. Copied from a draft to its version on promote.';

-- Dependencies people add between documents.
CREATE TABLE document_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id),
    from_document_id uuid NOT NULL,
    to_document_id uuid NOT NULL,
    kind text NOT NULL DEFAULT 'depends_on' CHECK (kind IN ('depends_on')),
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (from_document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (to_document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE,
    UNIQUE (from_document_id, to_document_id, kind),
    CONSTRAINT document_links_not_self CHECK (from_document_id <> to_document_id)
);
CREATE INDEX document_links_to ON document_links(to_document_id);
COMMENT ON TABLE document_links IS 'A document depends on another (added by a person on the Connections tab).';

COMMIT;
```

- [ ] **Step 4: Write `db/migrations/0098_document_drafts.down.sql`**

```sql
BEGIN;
SET search_path = ka, public;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM document_drafts WHERE status = 'open') OR EXISTS (SELECT 1 FROM document_sources)
       OR EXISTS (SELECT 1 FROM document_links) THEN
        RAISE EXCEPTION 'Open document drafts, document sources or document links exist and would be lost; promote or discard the drafts and remove the links before rollback';
    END IF;
END $$;
DROP TABLE document_links;
DROP TABLE document_sources;
DROP TABLE document_drafts;
DROP FUNCTION freeze_decided_document_draft();

COMMIT;
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd services/admin-api && uv run pytest -q tests/test_document_drafts_migration.py tests/test_documents.py`
Expected: PASS.

- [ ] **Step 6: `db/tests/run.sh`** — the down chain starts right after the two `run_migrate postgres up` lines. Insert a
  0098 down step before the existing 0097 one (the `run_migrate postgres down 1` followed by `discovery_removed=`):

```sh
run_migrate postgres down 1 >> "$test_log" 2>&1 || { cat "$test_log"; exit 1; }
drafts_removed=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.document_drafts') IS NULL AND to_regclass('ka.document_sources') IS NULL
        AND to_regclass('ka.document_links') IS NULL")
[ "$drafts_removed" = t ] || { echo 'FAIL: 0098 down left document drafts' >&2; exit 1; }
```

  and after `printf '%s\n' 'PASS: discovery runs and proposals exist.'` add:

```sh
drafts=$(docker exec "$test_container" psql -X -U postgres -Atc \
    "SELECT to_regclass('ka.document_drafts') IS NOT NULL AND to_regclass('ka.document_sources') IS NOT NULL
        AND to_regclass('ka.document_links') IS NOT NULL")
[ "$drafts" = t ] || { echo 'FAIL: 0098 document drafts missing' >&2; exit 1; }
printf '%s\n' 'PASS: document drafts, sources and links exist.'
```

  One more migration and one more `down 1` keep the later `[ "$rolled_back_version" = 83 ]` check true. Run
  `bash -n db/tests/run.sh` (syntax only; do not run the script).

- [ ] **Step 7: Docs** — `docs/migrations.md`, after the 0097 section:

```markdown
## 0098_document_drafts

`ka.document_drafts` holds unpublished versions of a document (`document_id`, `base_version_id`) or of a new document
(`slug`, `folder`, `resource_ids` to link on promote): written by a person (`manual`) or a Generate documents run
(`agent`, `session_id`), promoted to a live version (`promoted_version_id`) or discarded by a person. A decided draft is
frozen by a trigger. `ka.document_sources` records what a draft or live version was built from (the document it
revises and the documents its author read, with a title/version snapshot); `ka.document_links` holds the
`depends_on` links people add. Nothing in `documents`, `document_versions` or `document_details` changes. The down
migration refuses while open drafts, sources or links exist.
```

  `docs/database.md`, in the table under "Current ownership and storage", after the `documents`, `document_versions`
  row:

```markdown
| `document_drafts`, `document_sources`, `document_links` | Drafts (unpublished versions of a document or a new document, promoted or discarded by a person; never exported or listed as documents), what a draft or live version was built from, and `depends_on` links between documents |
```

- [ ] **Step 8: Commit**

```bash
git add db/migrations/0098_document_drafts.up.sql db/migrations/0098_document_drafts.down.sql db/tests/run.sh \
  docs/migrations.md docs/database.md services/admin-api/tests/test_document_drafts_migration.py
git commit -m "feat(db): document drafts, their sources and document links (0098)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 2: The drafts module — create, save, discard, promote

**Files:**
- Create: `services/admin-api/src/knowledge_admin/document_drafts.py`
- Create: `services/admin-api/tests/drafts_world.py` (shared fixture and helpers)
- Modify: `services/admin-api/src/knowledge_admin/api.py` (mount the router),
  `services/admin-api/src/knowledge_admin/app.py` (error handler)
- Test: `services/admin-api/tests/test_document_drafts.py` (new)

**Interfaces:**
- Consumes: `ka.document_drafts`, `ka.document_sources` (Task 1); `documents.DocumentInput`, `save_document(conn,
  project, actor, value, document_id=None, folder=None, origin='manual') -> {'id', 'revision', 'version_id'}`,
  `export_input`, `export_args`, `export_document`; `data_root.documentation_folder`.
- Produces (module `knowledge_admin.document_drafts`):
  - constants `DRAFT_NOT_FOUND`, `DOCUMENT_NOT_FOUND`, `DRAFT_CHANGED`, `DRAFT_DECIDED`, `STALE_BASE` (`'STALE_BASE'`),
    `STALE_MESSAGE` (format keys `base`, `live`), `NAME_TAKEN` (format key `slug`), `EXPORT_FAILED`, `CONTENT`
    (`('title','description','purpose','audience','tags','markdown')`), `NEW_DEFAULTS`, `SUMMARY` (SQL with `{extra}`),
    `DETAIL_EXTRA`;
  - models `DraftContent`, `Resources`, `DraftCreate`, `DraftUpdate(DraftContent)` (+ `revision`), `Decision`
    (`revision`), `Promotion(Decision)` (+ `confirm_stale: bool = False`);
  - `audit(conn, project, actor, action: str, resource_type: str, resource_id, details: dict) -> None`;
  - `sources_of(conn, column: Literal['draft_id','version_id'], owner: UUID) -> list[dict]` (keys `kind,
    source_document_id, source_version_id, source_title, source_version, deleted`; parent first);
  - `add_parent_source(conn, project: UUID, draft_id: UUID, version_id: UUID | None) -> None`;
  - `live_content(conn, project: UUID, document_id: UUID) -> dict` (the `CONTENT` keys of the live version; 404);
  - `create_draft(conn, project: UUID, actor: UUID | None, content: dict, *, origin: str, document_id: UUID | None =
    None, slug: str | None = None, folder: str | None = None, resources: dict | None = None, session_id: UUID | None =
    None) -> UUID`;
  - `draft_detail(conn, project: UUID, draft_id: UUID) -> dict` (summary fields `id, document_id, slug, folder, title,
    origin, session_id, session_title, author, edited, status, revision, base_version_id, base_version, live_version,
    stale, created_at, updated_at` + `description, purpose, audience, tags, markdown, resource_ids,
    promoted_version_id, decided_at, live_markdown, document_title, sources`);
  - `open_draft(conn, project, draft_id, revision) -> dict` (locks; 404 / 409 decided / 409 changed);
  - `available_resources(conn, project, wanted: dict) -> dict[str, list[UUID]]`;
  - `promote_draft(conn, project, actor, row: dict, confirm_stale: bool) -> dict` (`save_document`'s result);
  - `build_api(app_name, store) -> APIRouter` with prefix `/documentation`: `POST /drafts` (201, draft detail),
    `GET /drafts/{draft_id}`, `PUT /drafts/{draft_id}`, `POST /drafts/{draft_id}/discard`,
    `POST /drafts/{draft_id}/promote` → `{draft, document_id, version_id, revision, export_error}`.
- Produces (tests, `tests/drafts_world.py`): fixture `docs` → `SimpleNamespace(client, store, out, project, runbook)`
  (document "Runbook", slug `runbook`, folderless, description "How to run it", Markdown `# Runbook v1`, exported under
  `out`); helpers `API = '/api/documentation'`, `make_document(docs, slug, title) -> str`, `version_of(docs, document_id)
  -> str`, `new_draft(docs, **body) -> dict`, `save_live(docs, document_id, markdown) -> dict`, `promote(docs, draft:
  dict, **extra) -> Response`, `audit(docs, resource_id) -> list[str]`.

- [ ] **Step 1: Shared fixture** — `services/admin-api/tests/drafts_world.py`:

```python
"""Documents for draft tests: the client, the export folder and the document Runbook (version 1, '# Runbook v1')."""
from types import SimpleNamespace
from uuid import UUID

import pytest

from test_api import _client
from test_repositories import HEADERS

API = '/api/documentation'


@pytest.fixture
def docs(store, tmp_path, monkeypatch):
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    client = _client(tmp_path, repository_store=store)
    created = client.post(f'{API}/documents', headers=HEADERS, json={
        'slug': 'runbook', 'title': 'Runbook', 'description': 'How to run it', 'markdown': '# Runbook v1'})
    assert created.status_code == 201, created.text
    with store.connect() as conn:
        project = store.project(conn, 'shop')
    return SimpleNamespace(client=client, store=store, out=out, project=project, runbook=created.json()['id'])


def make_document(docs, slug: str, title: str) -> str:
    response = docs.client.post(f'{API}/documents', headers=HEADERS, json={'slug': slug, 'title': title, 'markdown': f'# {title}'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def version_of(docs, document_id: str) -> str:
    return docs.client.get(f'{API}/documents/{document_id}', headers=HEADERS).json()['current_version_id']


def new_draft(docs, **body) -> dict:
    response = docs.client.post(f'{API}/drafts', headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def save_live(docs, document_id: str, markdown: str) -> dict:
    """A person saves a new live version in the editor."""
    current = docs.client.get(f'{API}/documents/{document_id}', headers=HEADERS).json()
    response = docs.client.put(f'{API}/documents/{document_id}', headers=HEADERS, json={
        'slug': current['slug'], 'title': current['title'], 'description': current['description'],
        'markdown': markdown, 'revision': current['revision']})
    assert response.status_code == 200, response.text
    return response.json()


def promote(docs, draft: dict, **extra):
    return docs.client.post(f"{API}/drafts/{draft['id']}/promote", headers=HEADERS, json={'revision': draft['revision'], **extra})


def audit(docs, resource_id) -> list[str]:
    with docs.store.connect() as conn:
        return [row['action'] for row in conn.execute('SELECT action FROM ka.audit_events WHERE resource_id=%s ORDER BY id',
                                                      (UUID(str(resource_id)),))]
```

- [ ] **Step 2: Write the failing test** — `services/admin-api/tests/test_document_drafts.py`:

```python
"""Document drafts: create, save, discard and promote — fresh, stale (confirmed), a new document, and a new document
whose name was taken meanwhile."""
from uuid import UUID, uuid4

from drafts_world import API, audit, docs, make_document, new_draft, promote, save_live, version_of  # noqa: F401
from knowledge_admin.document_drafts import DRAFT_CHANGED, DRAFT_DECIDED, NAME_TAKEN, STALE_BASE, STALE_MESSAGE
from test_repositories import HEADERS


def test_a_draft_of_a_document_starts_from_its_live_version(docs):
    draft = new_draft(docs, document_id=docs.runbook)
    assert (draft['document_id'], draft['title'], draft['description'], draft['markdown'], draft['origin']) == (
        docs.runbook, 'Runbook', 'How to run it', '# Runbook v1', 'manual')
    assert (draft['base_version'], draft['live_version'], draft['stale'], draft['author'], draft['status']) == (
        1, 1, False, 'admin@example.test', 'open')
    assert [(s['kind'], s['source_document_id'], s['source_title'], s['source_version'], s['deleted'])
            for s in draft['sources']] == [('parent', docs.runbook, 'Runbook', 1, False)]
    given = new_draft(docs, document_id=docs.runbook, title='Runbook (draft)', markdown='# Runbook v2')
    assert (given['title'], given['description'], given['markdown']) == ('Runbook (draft)', 'How to run it', '# Runbook v2')
    assert audit(docs, draft['id']) == ['document.draft.created']
    assert docs.client.post(f'{API}/drafts', headers=HEADERS, json={'document_id': str(uuid4())}).status_code == 404


def test_a_new_document_draft_needs_its_name_and_text(docs):
    for body in ({}, {'slug': 'disk-layout'}, {'slug': 'disk-layout', 'title': 'Disk layout'},
                 {'document_id': docs.runbook, 'slug': 'x'}, {'slug': 'Disk Layout', 'title': 'D', 'markdown': '# D'},
                 {'slug': 'd', 'title': 'D', 'markdown': '# D', 'folder': 'Servers'},
                 {'document_id': docs.runbook, 'folder': 'servers'}, {'slug': 'd', 'title': ' ', 'markdown': '# D'}):
        assert docs.client.post(f'{API}/drafts', headers=HEADERS, json=body).status_code == 400, body
    draft = new_draft(docs, slug='disk-layout', folder='servers/web-1', title='Disk layout', markdown='# Disks')
    assert (draft['document_id'], draft['slug'], draft['folder'], draft['base_version'], draft['sources']) == (
        None, 'disk-layout', 'servers/web-1', None, [])
    # A name that is already a document's: the draft revises that document.
    named = new_draft(docs, slug='runbook', title='Runbook again', markdown='# Again')
    assert (named['document_id'], named['slug'], named['base_version'], named['title']) == (docs.runbook, None, 1, 'Runbook again')


def test_saving_a_draft_needs_its_revision_and_marks_it_edited(docs):
    draft = new_draft(docs, document_id=docs.runbook)
    url = f"{API}/drafts/{draft['id']}"
    body = {'title': 'Runbook', 'markdown': '# Runbook, edited'}
    saved = docs.client.put(url, headers=HEADERS, json={**body, 'revision': 1})
    assert saved.status_code == 200, saved.text
    assert (saved.json()['revision'], saved.json()['edited'], saved.json()['markdown']) == (2, True, '# Runbook, edited')
    stale = docs.client.put(url, headers=HEADERS, json={**body, 'markdown': '# Lost?', 'revision': 1})
    assert stale.status_code == 409 and stale.json()['error']['message'] == DRAFT_CHANGED
    assert docs.client.get(url, headers=HEADERS).json()['markdown'] == '# Runbook, edited'
    assert audit(docs, draft['id']) == ['document.draft.created', 'document.draft.updated']


def test_promote_saves_a_live_version_with_the_drafts_sources(docs):
    overview = make_document(docs, 'overview', 'Overview')
    draft = new_draft(docs, document_id=docs.runbook, markdown='# Runbook v2')
    with docs.store.connect() as conn:  # the author read the overview while writing
        conn.execute('''INSERT INTO ka.document_sources(project_id,draft_id,kind,source_document_id,source_version_id,
            source_title,source_version) VALUES (%s,%s,'read',%s,%s,'Overview',1)''',
            (docs.project, draft['id'], overview, version_of(docs, overview)))
    promoted = promote(docs, draft)
    assert promoted.status_code == 200, promoted.text
    result = promoted.json()
    assert (result['document_id'], result['revision'], result['export_error']) == (docs.runbook, 2, None)
    assert (result['draft']['status'], result['draft']['promoted_version_id']) == ('promoted', result['version_id'])
    document = docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()
    assert (document['version'], document['slug'], document['description'], document['current_version']['markdown'],
            document['current_version']['origin']) == (2, 'runbook', 'How to run it', '# Runbook v2', 'manual')
    assert (docs.out / 'runbook' / 'README.md').read_text() == '# Runbook v2'
    with docs.store.connect() as conn:
        copied = conn.execute('''SELECT kind,source_title,source_version FROM ka.document_sources WHERE version_id=%s
            ORDER BY kind''', (UUID(result['version_id']),)).fetchall()
    assert copied == [{'kind': 'parent', 'source_title': 'Runbook', 'source_version': 1},
                      {'kind': 'read', 'source_title': 'Overview', 'source_version': 1}]
    assert audit(docs, draft['id']) == ['document.draft.created', 'document.draft.promoted']
    # Review focus 1: Promote clicked twice — one version, the second answers "already promoted".
    again = promote(docs, draft)
    assert again.status_code == 409 and again.json()['error']['message'] == DRAFT_DECIDED
    assert len(docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()['versions']) == 2


def test_a_stale_draft_is_promoted_only_when_confirmed(docs):
    draft = new_draft(docs, document_id=docs.runbook, markdown='# Draft text')
    save_live(docs, docs.runbook, '# Runbook v2 by a person')
    assert docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()['stale'] is True
    refused = promote(docs, draft)
    message = STALE_MESSAGE.format(base=1, live=2)
    assert message == "This draft was based on version 1; the live version is now 2. Promoting replaces version 2's text."
    assert refused.status_code == 409 and refused.json()['error'] == {'message': message, 'code': STALE_BASE}
    confirmed = promote(docs, draft, confirm_stale=True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['revision'] == 3
    assert docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()['current_version']['markdown'] == '# Draft text'


def test_a_new_document_draft_creates_the_document_with_the_resources_that_still_exist(docs):
    server = docs.client.post('/api/servers', headers=HEADERS, json={'name': 'web-1', 'hostname': '10.0.0.7'}).json()
    gone = uuid4()
    with docs.store.connect() as conn:
        conn.execute("INSERT INTO ka.services(id,project_id,name) VALUES (%s,%s,'Billing')", (gone, docs.project))
    draft = new_draft(docs, slug='disk-layout', folder='servers/web-1', title='Disk layout', markdown='# Disks',
                      resource_ids={'server_ids': [server['id']], 'service_ids': [str(gone)]})
    with docs.store.connect() as conn:  # Review focus 2: a chosen resource is deleted before the promote
        conn.execute('DELETE FROM ka.services WHERE id=%s', (gone,))
    promoted = promote(docs, draft)
    assert promoted.status_code == 200, promoted.text
    created = docs.client.get(f"{API}/documents/{promoted.json()['document_id']}", headers=HEADERS).json()
    assert (created['slug'], created['folder'], created['title'], created['server_ids'], created['service_ids']) == (
        'disk-layout', 'servers/web-1', 'Disk layout', [server['id']], [])
    assert (docs.out / 'servers/web-1/disk-layout/README.md').read_text() == '# Disks'
    assert (promoted.json()['draft']['document_id'], promoted.json()['draft']['slug']) == (created['id'], None)


def test_a_new_document_whose_name_was_taken_meanwhile_becomes_a_draft_of_it(docs):
    draft = new_draft(docs, slug='overview', title='Overview draft', markdown='# Draft overview')
    created = docs.client.post(f'{API}/documents', headers=HEADERS, json={
        'slug': 'overview', 'title': 'Overview', 'markdown': '# Overview by a person'}).json()
    refused = promote(docs, draft)
    assert NAME_TAKEN.format(slug='overview') == ('A document named overview was created in this folder meanwhile. '
                                                 'The draft is now a draft of it. Review it again.')
    assert refused.status_code == 409 and refused.json()['error']['message'] == NAME_TAKEN.format(slug='overview')
    converted = docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
    assert (converted['document_id'], converted['slug'], converted['folder'], converted['revision'],
            converted['base_version'], converted['stale']) == (created['id'], None, None, 2, 1, False)
    assert converted['live_markdown'] == '# Overview by a person'
    assert [s['kind'] for s in converted['sources']] == ['parent']
    assert promote(docs, draft).status_code == 409  # its old revision
    promoted = promote(docs, converted)
    assert promoted.status_code == 200 and promoted.json()['revision'] == 2


def test_a_discarded_draft_is_kept_but_never_changes_again(docs):
    draft = new_draft(docs, document_id=docs.runbook)
    url = f"{API}/drafts/{draft['id']}"
    stale = docs.client.post(url + '/discard', headers=HEADERS, json={'revision': 2})
    assert stale.status_code == 409 and stale.json()['error']['message'] == DRAFT_CHANGED
    discarded = docs.client.post(url + '/discard', headers=HEADERS, json={'revision': 1})
    assert discarded.status_code == 200 and discarded.json()['status'] == 'discarded'
    for response in (docs.client.post(url + '/discard', headers=HEADERS, json={'revision': 2}),
                     docs.client.put(url, headers=HEADERS, json={'revision': 2, 'title': 'T', 'markdown': '# T'}),
                     promote(docs, discarded.json())):
        assert response.status_code == 409 and response.json()['error']['message'] == DRAFT_DECIDED
    assert docs.client.get(url, headers=HEADERS).json()['status'] == 'discarded'
    assert audit(docs, draft['id']) == ['document.draft.created', 'document.draft.discarded']
    assert docs.client.get(f'{API}/drafts/{uuid4()}', headers=HEADERS).status_code == 404
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_document_drafts.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'knowledge_admin.document_drafts'`).

- [ ] **Step 4: Write `services/admin-api/src/knowledge_admin/document_drafts.py`**

```python
"""Document drafts: unpublished versions of a document, or of a new document, that a person promotes to the live
version or discards. Agents only ever write drafts. Drafts live in ka.document_drafts, apart from
ka.document_versions, so version history, document_details, exports and folder statistics see live versions only."""
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from knowledge_admin.access import request_actor
from knowledge_admin.data_root import documentation_folder
from knowledge_admin.documents import DocumentInput, export_args, export_document, export_input, save_document
from knowledge_admin.repositories import RepositoryStore

DRAFT_NOT_FOUND = 'Draft not found.'
DOCUMENT_NOT_FOUND = 'Document not found'
DRAFT_CHANGED = 'This draft changed. Reload it before saving; your text has not been overwritten.'
DRAFT_DECIDED = 'This draft was already promoted or discarded.'
STALE_BASE = 'STALE_BASE'
STALE_MESSAGE = ("This draft was based on version {base}; the live version is now {live}. "
                 "Promoting replaces version {live}'s text.")
NAME_TAKEN = ('A document named {slug} was created in this folder meanwhile. The draft is now a draft of it. '
              'Review it again.')
SAVED_MEANWHILE = 'Another document was saved with this name at the same time. Try again.'
EXPORT_FAILED = 'Saved. Writing the document file failed; use Export to folder to retry.'
SLUG = r'^[a-z0-9]+(-[a-z0-9]+)*$'
CONTENT = ('title', 'description', 'purpose', 'audience', 'tags', 'markdown')
NEW_DEFAULTS = {'description': '', 'purpose': 'explanation', 'audience': 'both', 'tags': []}
RESOURCE_KEYS = ('code_project_ids', 'service_ids', 'server_ids', 'deployment_ids', 'environment_ids')


def _text(value: str | None, name: str) -> str | None:
    """DocumentInput's text rule: no null characters; nonblank except the description; stripped except the Markdown."""
    if value is None:
        return None
    if '\x00' in value or (name != 'description' and not value.strip()):
        raise ValueError('Enter nonempty text without null characters')
    return value if name == 'markdown' else value.strip()


def _tags(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    tags = list(dict.fromkeys(v.strip() for v in values))
    if any(not v or len(v) > 80 or '\x00' in v for v in tags):
        raise ValueError('Tags must contain 1–80 characters without null characters')
    return tags


class DraftContent(BaseModel):
    """A draft's text, with DocumentInput's limits."""
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default='', max_length=4000)
    purpose: str = Field(default='explanation', min_length=1, max_length=80)
    audience: Literal['people', 'agents', 'both'] = 'both'
    tags: list[str] = Field(default_factory=list, max_length=30)
    markdown: str = Field(min_length=1, max_length=1000000)

    @field_validator('title', 'description', 'purpose', 'markdown')
    @classmethod
    def valid_text(cls, value: str, info) -> str:
        return _text(value, info.field_name)

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, values: list[str]) -> list[str]:
        return _tags(values)


class DraftUpdate(DraftContent):
    revision: int = Field(ge=1, strict=True)


class Resources(BaseModel):
    """What a new document links on promote."""
    model_config = ConfigDict(extra='forbid')
    code_project_ids: list[UUID] = Field(default_factory=list, max_length=100)
    service_ids: list[UUID] = Field(default_factory=list, max_length=100)
    server_ids: list[UUID] = Field(default_factory=list, max_length=100)
    deployment_ids: list[UUID] = Field(default_factory=list, max_length=100)
    environment_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator(*RESOURCE_KEYS)
    @classmethod
    def unique_ids(cls, values: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(values))


class DraftCreate(BaseModel):
    """A draft of a document (document_id; the text defaults to the live version's) or of a new document (slug, an
    optional folder, title and Markdown, optional resources)."""
    model_config = ConfigDict(extra='forbid')
    document_id: UUID | None = None
    slug: str | None = Field(default=None, min_length=1, max_length=120, pattern=SLUG)
    folder: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    purpose: str | None = Field(default=None, min_length=1, max_length=80)
    audience: Literal['people', 'agents', 'both'] | None = None
    tags: list[str] | None = Field(default=None, max_length=30)
    markdown: str | None = Field(default=None, min_length=1, max_length=1000000)
    resource_ids: Resources | None = None

    @field_validator('title', 'description', 'purpose', 'markdown')
    @classmethod
    def valid_text(cls, value: str | None, info) -> str | None:
        return _text(value, info.field_name)

    @field_validator('tags')
    @classmethod
    def valid_tags(cls, values: list[str] | None) -> list[str] | None:
        return _tags(values)

    @field_validator('folder')
    @classmethod
    def valid_folder(cls, value: str | None) -> str | None:
        return None if value is None else documentation_folder(value)

    @model_validator(mode='after')
    def target(self):
        if (self.document_id is None) == (self.slug is None):
            raise ValueError('Give a document_id, or a slug for a new document.')
        if self.document_id is not None and (self.folder is not None or self.resource_ids is not None):
            raise ValueError('A draft of a document keeps its folder and linked resources.')
        if self.document_id is None and (self.title is None or self.markdown is None):
            raise ValueError('A new document needs a title and Markdown.')
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


class Promotion(Decision):
    confirm_stale: bool = Field(default=False, strict=True)


# {extra}: more columns; the only placeholder (no other braces in the SQL).
SUMMARY = '''SELECT x.id,x.document_id,x.slug,x.folder,x.title,x.origin,x.session_id,s.title AS session_title,
    p.email AS author,x.edited,x.status,x.revision,x.base_version_id,bv.version AS base_version,lv.version AS live_version,
    (x.document_id IS NOT NULL AND x.status='open' AND lv.id IS DISTINCT FROM x.base_version_id) AS stale,
    x.created_at,x.updated_at{extra}
    FROM ka.document_drafts x LEFT JOIN ka.document_versions bv ON bv.id=x.base_version_id
    LEFT JOIN ka.documents d ON d.id=x.document_id LEFT JOIN ka.document_versions lv ON lv.id=d.current_version_id
    LEFT JOIN ka.agent_sessions s ON s.id=x.session_id LEFT JOIN ka.principals p ON p.id=x.created_by'''
DETAIL_EXTRA = ''',x.description,x.purpose,x.audience,x.tags,x.markdown,x.resource_ids,x.promoted_version_id,x.decided_at,
    lv.markdown AS live_markdown,lv.title AS document_title'''


def audit(conn, project, actor, action: str, resource_type: str, resource_id, details: dict) -> None:
    conn.execute('''INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
        VALUES (%s,%s,%s,%s,%s,%s)''', (project, actor, action, resource_type, resource_id, Jsonb(jsonable_encoder(details))))


def sources_of(conn, column: Literal['draft_id', 'version_id'], owner: UUID) -> list[dict]:
    """What a draft or a live version was built from, the parent first; `deleted` when the source document is gone.
    `column` is fixed by the caller, never taken from a request."""
    if column not in ('draft_id', 'version_id'):
        raise ValueError(column)
    return conn.execute(f'''SELECT kind,source_document_id,source_version_id,source_title,source_version,
        source_document_id IS NULL AS deleted FROM ka.document_sources WHERE {column}=%s
        ORDER BY kind='parent' DESC,lower(source_title),source_title,id''', (owner,)).fetchall()


def add_parent_source(conn, project: UUID, draft_id: UUID, version_id: UUID | None) -> None:
    """The document a draft revises, at the version it started from."""
    if version_id is None:
        return
    conn.execute('''INSERT INTO ka.document_sources(project_id,draft_id,kind,source_document_id,source_version_id,
        source_title,source_version) SELECT %s,%s,'parent',document_id,id,title,version FROM ka.document_versions
        WHERE id=%s ON CONFLICT DO NOTHING''', (project, draft_id, version_id))


def live_content(conn, project: UUID, document_id: UUID) -> dict:
    row = conn.execute('''SELECT v.title,v.description,v.purpose,v.audience,v.tags,v.markdown FROM ka.documents d
        JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s AND d.project_id=%s''',
        (document_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return dict(row)


def create_draft(conn, project: UUID, actor: UUID | None, content: dict, *, origin: str, document_id: UUID | None = None,
                 slug: str | None = None, folder: str | None = None, resources: dict | None = None,
                 session_id: UUID | None = None) -> UUID:
    """Insert an open draft (and, for a draft of a document, its parent source). A new document whose folder and slug
    already name a document becomes a draft of that document."""
    if document_id is None:
        existing = conn.execute('''SELECT id FROM ka.documents WHERE project_id=%s AND folder IS NOT DISTINCT FROM %s
            AND slug=%s''', (project, folder, slug)).fetchone()
        if existing is not None:
            document_id = existing['id']
    base = None
    if document_id is not None:
        row = conn.execute('SELECT current_version_id FROM ka.documents WHERE id=%s AND project_id=%s',
                           (document_id, project)).fetchone()
        if row is None:
            raise HTTPException(404, DOCUMENT_NOT_FOUND)
        base, slug, folder, resources = row['current_version_id'], None, None, {}
    draft_id = conn.execute('''INSERT INTO ka.document_drafts(project_id,document_id,slug,folder,resource_ids,
        base_version_id,title,description,purpose,audience,tags,markdown,origin,session_id,created_by,updated_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
        (project, document_id, slug, folder, Jsonb(resources or {}), base, content['title'], content['description'],
         content['purpose'], content['audience'], content['tags'], content['markdown'], origin, session_id, actor,
         actor)).fetchone()['id']
    add_parent_source(conn, project, draft_id, base)
    audit(conn, project, actor, 'document.draft.created', 'document_draft', draft_id,
          {'document_id': document_id, 'origin': origin, 'session_id': session_id})
    return draft_id


def draft_detail(conn, project: UUID, draft_id: UUID) -> dict:
    row = conn.execute(SUMMARY.format(extra=DETAIL_EXTRA) + ' WHERE x.id=%s AND x.project_id=%s',
                       (draft_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DRAFT_NOT_FOUND)
    row['sources'] = sources_of(conn, 'draft_id', draft_id)
    return row


def open_draft(conn, project: UUID, draft_id: UUID, revision: int) -> dict:
    """The draft locked for a change: 404 when missing, 409 when decided (checked first) or changed."""
    row = conn.execute('SELECT * FROM ka.document_drafts WHERE id=%s AND project_id=%s FOR UPDATE',
                       (draft_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DRAFT_NOT_FOUND)
    if row['status'] != 'open':
        raise HTTPException(409, DRAFT_DECIDED)
    if row['revision'] != revision:
        raise HTTPException(409, DRAFT_CHANGED)
    return row


RESOURCE_QUERIES = {
    'code_project_ids': 'SELECT id FROM ka.repositories WHERE project_id=%s AND archived_at IS NULL AND id=ANY(%s)',
    'service_ids': 'SELECT id FROM ka.services WHERE project_id=%s AND id=ANY(%s)',
    'server_ids': 'SELECT id FROM ka.servers WHERE project_id=%s AND id=ANY(%s)',
    'environment_ids': 'SELECT id FROM ka.environments WHERE project_id=%s AND id=ANY(%s)',
}


def available_resources(conn, project: UUID, wanted: dict) -> dict:
    """The chosen resources that still exist, as DocumentInput fields: deleted entries and archived code projects drop
    out, and a deployment stays only with its service and server."""
    chosen = {key: [UUID(str(item)) for item in wanted.get(key, [])] for key in RESOURCE_KEYS}
    result = {}
    for key, query in RESOURCE_QUERIES.items():
        found = {row['id'] for row in conn.execute(query, (project, chosen[key])).fetchall()}
        result[key] = [item for item in chosen[key] if item in found]
    deployments = conn.execute('SELECT id,service_id,server_id FROM ka.service_deployments WHERE project_id=%s AND id=ANY(%s)',
                               (project, chosen['deployment_ids'])).fetchall()
    usable = {row['id'] for row in deployments
              if row['service_id'] in result['service_ids'] and row['server_id'] in result['server_ids']}
    result['deployment_ids'] = [item for item in chosen['deployment_ids'] if item in usable]
    return result


def promote_draft(conn, project: UUID, actor: UUID, row: dict, confirm_stale: bool) -> dict:
    """Save the open, locked draft `row` as the live version (save_document), mark it promoted and copy its sources to
    the version. A stale draft needs `confirm_stale`; a new document whose name was taken meanwhile is converted into a
    draft of that document, committed, and refused."""
    origin = 'generated' if row['origin'] == 'agent' else 'manual'
    content = {key: row[key] for key in CONTENT}
    if row['document_id'] is not None:
        live = conn.execute('''SELECT current_version_id,version,slug,revision,code_project_ids,service_ids,server_ids,
            deployment_ids,environment_ids FROM ka.document_details WHERE id=%s''', (row['document_id'],)).fetchone()
        if live['current_version_id'] != row['base_version_id'] and not confirm_stale:
            base = conn.execute('SELECT version FROM ka.document_versions WHERE id=%s', (row['base_version_id'],)).fetchone()
            raise HTTPException(409, {'message': STALE_MESSAGE.format(base=base['version'] if base else '?', live=live['version']),
                                      'code': STALE_BASE})
        saved = save_document(conn, project, actor, DocumentInput(
            slug=live['slug'], **content, code_project_ids=live['code_project_ids'], service_ids=live['service_ids'],
            server_ids=live['server_ids'], deployment_ids=live['deployment_ids'], environment_ids=live['environment_ids'],
            revision=live['revision']), row['document_id'], origin=origin)
    else:
        clash = conn.execute('''SELECT id,current_version_id FROM ka.documents WHERE project_id=%s
            AND folder IS NOT DISTINCT FROM %s AND slug=%s''', (project, row['folder'], row['slug'])).fetchone()
        if clash is not None:
            conn.execute('''UPDATE ka.document_drafts SET document_id=%s,base_version_id=%s,slug=NULL,folder=NULL,
                resource_ids='{}',revision=revision+1 WHERE id=%s''', (clash['id'], clash['current_version_id'], row['id']))
            conn.execute('DELETE FROM ka.document_sources WHERE draft_id=%s AND source_document_id=%s', (row['id'], clash['id']))
            add_parent_source(conn, project, row['id'], clash['current_version_id'])
            conn.commit()  # kept although the request fails, so the reload shows the converted draft
            raise HTTPException(409, NAME_TAKEN.format(slug=row['slug']))
        saved = save_document(conn, project, actor, DocumentInput(
            slug=row['slug'], **content, **available_resources(conn, project, row['resource_ids'])),
            None, folder=row['folder'], origin=origin)
    conn.execute('''UPDATE ka.document_drafts SET status='promoted',promoted_version_id=%s,document_id=%s,slug=NULL,
        folder=NULL,decided_by=%s,decided_at=now(),revision=revision+1 WHERE id=%s''',
        (saved['version_id'], saved['id'], actor, row['id']))
    conn.execute('''INSERT INTO ka.document_sources(project_id,version_id,kind,source_document_id,source_version_id,
        source_title,source_version) SELECT project_id,%s,kind,source_document_id,source_version_id,source_title,
        source_version FROM ka.document_sources WHERE draft_id=%s''', (saved['version_id'], row['id']))
    if row['session_id'] is not None:  # the Generate documents list shows the run's saved document
        conn.execute('UPDATE ka.agent_sessions SET document_id=%s WHERE id=%s', (saved['id'], row['session_id']))
    audit(conn, project, actor, 'document.draft.promoted', 'document_draft', row['id'],
          {'document_id': saved['id'], 'version': saved['revision'], 'version_id': saved['version_id'],
           'stale_confirmed': confirm_stale})
    return saved


def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:
    router = APIRouter(prefix='/documentation')

    def database() -> RepositoryStore:
        if store is None:
            raise HTTPException(503, 'Document storage is not configured')
        return store

    @router.post('/drafts', status_code=201)
    def create(value: DraftCreate, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
            actor = db.actor(conn, *request_actor(request))
            given = value.model_dump(include=set(CONTENT), exclude_none=True)
            if value.document_id is not None:
                content = {**live_content(conn, project, value.document_id), **given}
            else:
                content = {**NEW_DEFAULTS, **given}
            resources = value.resource_ids.model_dump(mode='json') if value.resource_ids else {}
            draft_id = create_draft(conn, project, actor, content, origin='manual', document_id=value.document_id,
                                    slug=value.slug, folder=value.folder, resources=resources)
            return jsonable_encoder(draft_detail(conn, project, draft_id))

    @router.get('/drafts/{draft_id}')
    def get_draft(draft_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            return jsonable_encoder(draft_detail(conn, db.project(conn, app_name), draft_id))

    @router.put('/drafts/{draft_id}')
    def update(draft_id: UUID, value: DraftUpdate, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            row = open_draft(conn, project, draft_id, value.revision)
            conn.execute('''UPDATE ka.document_drafts SET title=%s,description=%s,purpose=%s,audience=%s,tags=%s,
                markdown=%s,edited=true,updated_by=%s,revision=revision+1 WHERE id=%s''',
                (value.title, value.description, value.purpose, value.audience, value.tags, value.markdown, actor, draft_id))
            audit(conn, project, actor, 'document.draft.updated', 'document_draft', draft_id, {'revision': row['revision'] + 1})
            return jsonable_encoder(draft_detail(conn, project, draft_id))

    @router.post('/drafts/{draft_id}/discard')
    def discard(draft_id: UUID, value: Decision, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            open_draft(conn, project, draft_id, value.revision)
            conn.execute('''UPDATE ka.document_drafts SET status='discarded',decided_by=%s,decided_at=now(),
                revision=revision+1 WHERE id=%s''', (actor, draft_id))
            audit(conn, project, actor, 'document.draft.discarded', 'document_draft', draft_id, {})
            return jsonable_encoder(draft_detail(conn, project, draft_id))

    @router.post('/drafts/{draft_id}/promote')
    def promote(draft_id: UUID, value: Promotion, request: Request) -> dict:
        db = database()
        try:
            with db.connect() as conn:
                project = db.project(conn, app_name)
                conn.execute('SELECT id FROM ka.projects WHERE id=%s FOR NO KEY UPDATE', (project,))
                actor = db.actor(conn, *request_actor(request))
                row = open_draft(conn, project, draft_id, value.revision)
                saved = promote_draft(conn, project, actor, row, value.confirm_stale)
                detail = draft_detail(conn, project, draft_id)
        except ValidationError as exc:
            raise HTTPException(400, '; '.join(error['msg'] for error in exc.errors())) from None
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, SAVED_MEANWHILE) from None
        try:  # after the commit, as the editor's save: a failed export never undoes the version
            with db.connect() as conn:
                current = export_input(conn, saved['id'])
            export_document(**export_args(current))
            export_error = None
        except (OSError, ValueError):
            export_error = EXPORT_FAILED
        return jsonable_encoder({'draft': detail, 'document_id': saved['id'], 'version_id': saved['version_id'],
                                 'revision': saved['revision'], 'export_error': export_error})

    return router
```

- [ ] **Step 5: Mount the router and pass error codes through**

  `api.py`: add `document_drafts` to the `from knowledge_admin import ...` line and, right after
  `router.include_router(documents.build_api(app_name, repository_store))`:

```python
    router.include_router(document_drafts.build_api(app_name, repository_store))
```

  `app.py`: replace the body of `http_error`:

```python
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        # A dict detail carries a code the UI recognises beside the message, e.g. {"message": ..., "code": "STALE_BASE"}.
        error = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": error})
```

  (Check with `grep -rn "HTTPException([0-9]*, {" services/admin-api/src` that no existing route already raises a dict
  detail; there is none today.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_document_drafts.py tests/test_documents.py tests/test_api.py tests/test_discovery_review.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/document_drafts.py services/admin-api/src/knowledge_admin/api.py \
  services/admin-api/src/knowledge_admin/app.py services/admin-api/tests/drafts_world.py \
  services/admin-api/tests/test_document_drafts.py
git commit -m "feat(admin-api): document drafts — create, save, discard, promote (stale base, name taken meanwhile)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Draft reads, the documents list, connections and links

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/document_drafts.py` (routes inside `build_api`, constants)
- Modify: `services/admin-api/src/knowledge_admin/documents.py` (`list_documents`, `get_document`)
- Modify: `docs/documentation.md` (API table)
- Test: `services/admin-api/tests/test_document_draft_reads.py` (new)

**Interfaces:**
- Consumes: Task 2's `SUMMARY`, `draft_detail`, `sources_of`, `audit`, `DOCUMENT_NOT_FOUND`; fixture `docs` and helpers
  from `tests/drafts_world.py`.
- Produces:
  - constants `LINK_SELF = 'A document cannot depend on itself.'`, `LINK_TWICE = 'This document already depends on that
    document.'`, `LINK_NOT_FOUND = 'This link does not exist.'`; model `LinkInput(to_document_id: UUID)`;
  - `GET /documentation/drafts?new=true` → `{drafts: [summary]}` (open drafts of new documents; without `new`, all open
    drafts); `GET /documentation/documents/{id}/drafts` → `{drafts: [summary]}` (open drafts, newest first, no
    Markdown; 404 for an unknown document);
  - `GET /documentation/documents/{id}/connections` → `{depends_on: [{id,title,slug,folder,version}], used_by: [...],
    built_from: [source]}`; `POST /documentation/documents/{id}/links` `{to_document_id}` → 201 connections;
    `DELETE /documentation/documents/{id}/links/{to_document_id}` → connections;
  - `GET /documentation/documents` rows gain `draft_count: int`; query `has_drafts=true` keeps documents with open
    drafts; `GET /documentation/documents/{id}` `versions[]` gain `from_draft: bool`.

- [ ] **Step 1: Write the failing test** — `services/admin-api/tests/test_document_draft_reads.py`:

```python
"""Reading drafts: the documents list's counts and filter, new-document drafts, a document's drafts, connections and
links — and that drafts are never documents."""
import asyncio
import json
from uuid import uuid4

from drafts_world import API, audit, docs, make_document, new_draft, promote, save_live, version_of  # noqa: F401
from knowledge_admin.capabilities import inventory
from knowledge_admin.document_drafts import LINK_NOT_FOUND, LINK_SELF, LINK_TWICE
from knowledge_admin.tools.catalog import CATALOG
from test_repositories import HEADERS


def test_the_list_counts_open_drafts_and_filters_documents_that_have_them(docs):
    overview = make_document(docs, 'overview', 'Overview')
    new_draft(docs, document_id=docs.runbook)
    new_draft(docs, document_id=docs.runbook, markdown='# Second')
    discarded = new_draft(docs, document_id=overview)
    docs.client.post(f"{API}/drafts/{discarded['id']}/discard", headers=HEADERS, json={'revision': 1})
    new_draft(docs, slug='disk-layout', title='Disk layout', markdown='# Disks')
    rows = {row['slug']: row['draft_count'] for row in docs.client.get(f'{API}/documents', headers=HEADERS).json()['documents']}
    assert rows == {'runbook': 2, 'overview': 0}
    filtered = docs.client.get(f'{API}/documents', params={'has_drafts': 'true'}, headers=HEADERS).json()
    assert (filtered['total'], [row['slug'] for row in filtered['documents']]) == (1, ['runbook'])


def test_new_document_drafts_are_listed_apart_and_a_documents_drafts_without_their_text(docs):
    of_runbook = new_draft(docs, document_id=docs.runbook, markdown='# Long text')
    fresh = new_draft(docs, slug='disk-layout', folder='servers', title='Disk layout', markdown='# Disks')
    listed = docs.client.get(f'{API}/drafts', params={'new': 'true'}, headers=HEADERS).json()['drafts']
    assert [(d['id'], d['title'], d['folder'], d['slug'], d['origin'], d['author']) for d in listed] == [
        (fresh['id'], 'Disk layout', 'servers', 'disk-layout', 'manual', 'admin@example.test')]
    assert 'markdown' not in listed[0]
    mine = docs.client.get(f'{API}/documents/{docs.runbook}/drafts', headers=HEADERS).json()['drafts']
    assert [d['id'] for d in mine] == [of_runbook['id']] and 'markdown' not in mine[0]
    assert (mine[0]['base_version'], mine[0]['live_version'], mine[0]['stale']) == (1, 1, False)
    save_live(docs, docs.runbook, '# Runbook v2')
    detail = docs.client.get(f"{API}/drafts/{of_runbook['id']}", headers=HEADERS).json()
    assert (detail['stale'], detail['base_version'], detail['live_version'], detail['live_markdown']) == (True, 1, 2, '# Runbook v2')
    assert docs.client.get(f'{API}/documents/{uuid4()}/drafts', headers=HEADERS).status_code == 404


def test_drafts_are_never_documents(docs, monkeypatch):
    monkeypatch.setattr(inventory, '_store', docs.store)
    new_draft(docs, document_id=docs.runbook, markdown='# DRAFT ONLY TEXT')
    new_draft(docs, slug='secret-plan', folder='plans', title='Secret plan', markdown='# DRAFT ONLY TEXT')
    document = docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()
    assert (document['version'], document['current_version']['markdown'], len(document['versions'])) == (1, '# Runbook v1', 1)
    assert [row['slug'] for row in docs.client.get(f'{API}/documents', headers=HEADERS).json()['documents']] == ['runbook']
    assert not any('DRAFT ONLY TEXT' in path.read_text() for path in docs.out.rglob('*') if path.is_file())
    assert all(row['folder'] != 'plans' for row in docs.client.get(f'{API}/folders', headers=HEADERS).json()['folders'])
    with docs.store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.document_details WHERE slug='secret-plan'").fetchone()['n'] == 0
        server = conn.execute("""INSERT INTO ka.servers(project_id,name,hostname,username,enabled)
            VALUES (%s,'web-1','10.0.0.7','ops',false) RETURNING id""", (docs.project,)).fetchone()['id']
        conn.execute('INSERT INTO ka.inventory_documents(project_id,server_id,document_id) VALUES (%s,%s,%s)',
                     (docs.project, server, docs.runbook))
    spec = CATALOG['get_document']
    text = asyncio.run(spec.run({'config': {'project_id': str(docs.project)}},
                                spec.arguments.model_validate({'document_id': docs.runbook})))
    assert json.loads(text)['markdown'] == '# Runbook v1'


def test_links_add_and_remove_dependencies(docs):
    overview = make_document(docs, 'overview', 'Overview')
    links = f'{API}/documents/{docs.runbook}/links'
    added = docs.client.post(links, headers=HEADERS, json={'to_document_id': overview})
    assert added.status_code == 201, added.text
    assert [d['title'] for d in added.json()['depends_on']] == ['Overview']
    twice = docs.client.post(links, headers=HEADERS, json={'to_document_id': overview})
    assert twice.status_code == 409 and twice.json()['error']['message'] == LINK_TWICE
    itself = docs.client.post(links, headers=HEADERS, json={'to_document_id': docs.runbook})
    assert itself.status_code == 400 and itself.json()['error']['message'] == LINK_SELF
    assert docs.client.post(links, headers=HEADERS, json={'to_document_id': str(uuid4())}).status_code == 404
    other = docs.client.get(f'{API}/documents/{overview}/connections', headers=HEADERS).json()
    assert ([d['title'] for d in other['used_by']], other['depends_on']) == (['Runbook'], [])
    removed = docs.client.delete(f'{links}/{overview}', headers=HEADERS)
    assert removed.status_code == 200 and removed.json()['depends_on'] == []
    again = docs.client.delete(f'{links}/{overview}', headers=HEADERS)
    assert again.status_code == 404 and again.json()['error']['message'] == LINK_NOT_FOUND
    assert audit(docs, docs.runbook)[-2:] == ['document.link.added', 'document.link.removed']


def test_connections_show_what_the_live_version_was_built_from_even_when_a_source_is_gone(docs):
    overview = make_document(docs, 'overview', 'Overview')
    draft = new_draft(docs, document_id=docs.runbook, markdown='# Runbook v2')
    with docs.store.connect() as conn:
        conn.execute('''INSERT INTO ka.document_sources(project_id,draft_id,kind,source_document_id,source_version_id,
            source_title,source_version) VALUES (%s,%s,'read',%s,%s,'Overview',1)''',
            (docs.project, draft['id'], overview, version_of(docs, overview)))
    assert promote(docs, draft).status_code == 200
    # Review focus 3: the source is deleted afterwards.
    assert docs.client.delete(f'{API}/documents/{overview}', headers=HEADERS,
                              params={'revision': 1, 'confirmed': True}).status_code == 200
    built = docs.client.get(f'{API}/documents/{docs.runbook}/connections', headers=HEADERS).json()['built_from']
    assert [(s['kind'], s['source_title'], s['source_version'], s['deleted']) for s in built] == [
        ('parent', 'Runbook', 1, False), ('read', 'Overview', 1, True)]
    detail = docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).json()
    assert [s['deleted'] for s in detail['sources']] == [False, True]
    versions = docs.client.get(f'{API}/documents/{docs.runbook}', headers=HEADERS).json()['versions']
    assert [(v['version'], v['from_draft']) for v in versions] == [(2, True), (1, False)]


def test_deleting_a_document_deletes_its_drafts_and_links(docs):
    overview = make_document(docs, 'overview', 'Overview')
    draft = new_draft(docs, document_id=overview)
    docs.client.post(f'{API}/documents/{docs.runbook}/links', headers=HEADERS, json={'to_document_id': overview})
    assert docs.client.delete(f'{API}/documents/{overview}', headers=HEADERS,
                              params={'revision': 1, 'confirmed': True}).status_code == 200
    assert docs.client.get(f"{API}/drafts/{draft['id']}", headers=HEADERS).status_code == 404
    assert docs.client.get(f'{API}/documents/{docs.runbook}/connections', headers=HEADERS).json()['depends_on'] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_document_draft_reads.py`
Expected: FAIL (`ImportError: cannot import name 'LINK_NOT_FOUND'`).

- [ ] **Step 3: `document_drafts.py`** — add near the other constants:

```python
LINK_SELF = 'A document cannot depend on itself.'
LINK_TWICE = 'This document already depends on that document.'
LINK_NOT_FOUND = 'This link does not exist.'
LINKED = '''SELECT d.id,d.title,d.slug,d.folder,d.version FROM ka.document_links l JOIN ka.document_details d ON d.id=l.{other}
    WHERE l.{this}=%s AND l.kind='depends_on' ORDER BY lower(d.title),d.id'''


class LinkInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    to_document_id: UUID


def connections(conn, project: UUID, document_id: UUID) -> dict:
    """What a document depends on, what depends on it (links people add) and what its live version was built from."""
    row = conn.execute('SELECT current_version_id FROM ka.documents WHERE id=%s AND project_id=%s',
                       (document_id, project)).fetchone()
    if row is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return {'depends_on': conn.execute(LINKED.format(this='from_document_id', other='to_document_id'), (document_id,)).fetchall(),
            'used_by': conn.execute(LINKED.format(this='to_document_id', other='from_document_id'), (document_id,)).fetchall(),
            'built_from': sources_of(conn, 'version_id', row['current_version_id'])}
```

  and inside `build_api`, before `return router`:

```python
    @router.get('/drafts')
    def drafts(new: bool = False) -> dict:
        """Open drafts, newest first; new=true: only drafts of new documents (the Documents page's New drafts)."""
        db = database()
        with db.connect() as conn:
            rows = conn.execute(SUMMARY.format(extra='') + """ WHERE x.project_id=%s AND x.status='open'
                AND (NOT %s OR x.document_id IS NULL) ORDER BY x.updated_at DESC,x.id""",
                (db.project(conn, app_name), new)).fetchall()
            return jsonable_encoder({'drafts': rows})

    @router.get('/documents/{document_id}/drafts')
    def drafts_of_document(document_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            if conn.execute('SELECT 1 FROM ka.documents WHERE id=%s AND project_id=%s', (document_id, project)).fetchone() is None:
                raise HTTPException(404, DOCUMENT_NOT_FOUND)
            rows = conn.execute(SUMMARY.format(extra='') + """ WHERE x.document_id=%s AND x.status='open'
                ORDER BY x.updated_at DESC,x.id""", (document_id,)).fetchall()
            return jsonable_encoder({'drafts': rows})

    @router.get('/documents/{document_id}/connections')
    def document_connections(document_id: UUID) -> dict:
        db = database()
        with db.connect() as conn:
            return jsonable_encoder(connections(conn, db.project(conn, app_name), document_id))

    @router.post('/documents/{document_id}/links', status_code=201)
    def add_link(document_id: UUID, value: LinkInput, request: Request) -> dict:
        if value.to_document_id == document_id:
            raise HTTPException(400, LINK_SELF)
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            found = conn.execute('SELECT count(*) AS n FROM ka.documents WHERE project_id=%s AND id=ANY(%s)',
                                 (project, [document_id, value.to_document_id])).fetchone()['n']
            if found != 2:
                raise HTTPException(404, DOCUMENT_NOT_FOUND)
            added = conn.execute('''INSERT INTO ka.document_links(project_id,from_document_id,to_document_id,created_by)
                VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id''',
                (project, document_id, value.to_document_id, actor)).fetchone()
            if added is None:
                raise HTTPException(409, LINK_TWICE)
            audit(conn, project, actor, 'document.link.added', 'document', document_id,
                  {'to_document_id': value.to_document_id, 'kind': 'depends_on'})
            return jsonable_encoder(connections(conn, project, document_id))

    @router.delete('/documents/{document_id}/links/{to_document_id}')
    def remove_link(document_id: UUID, to_document_id: UUID, request: Request) -> dict:
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            actor = db.actor(conn, *request_actor(request))
            removed = conn.execute('''DELETE FROM ka.document_links WHERE project_id=%s AND from_document_id=%s
                AND to_document_id=%s AND kind='depends_on' RETURNING id''', (project, document_id, to_document_id)).fetchone()
            if removed is None:
                raise HTTPException(404, LINK_NOT_FOUND)
            audit(conn, project, actor, 'document.link.removed', 'document', document_id,
                  {'to_document_id': to_document_id, 'kind': 'depends_on'})
            return jsonable_encoder(connections(conn, project, document_id))
```

- [ ] **Step 4: `documents.py`** — in `list_documents` add the parameter `has_drafts: bool = False` (before `offset`),
  append to `where`:

```python
                AND (NOT %s OR EXISTS (SELECT 1 FROM ka.document_drafts x WHERE x.document_id=d.id AND x.status='open'))'''
```

  (so the closing `'''` moves to this line), append `has_drafts` to `params`, and alias the view in both queries:

```python
            rows = conn.execute(f'''SELECT d.*, (SELECT count(*) FROM ka.document_drafts x WHERE x.document_id=d.id
                AND x.status='open') AS draft_count FROM ka.document_details d WHERE {where}
                ORDER BY updated_at DESC,id LIMIT 50 OFFSET %s''', (*params,offset)).fetchall()
            total = conn.execute(f'SELECT count(*) AS n FROM ka.document_details d WHERE {where}', params).fetchone()['n']
```

  In `get_document` replace the versions query:

```python
            row['versions'] = conn.execute('''SELECT v.id,v.version,v.title,v.origin,v.base_version_id,v.content_hash,v.created_at,
                EXISTS (SELECT 1 FROM ka.document_drafts x WHERE x.promoted_version_id=v.id) AS from_draft
                FROM ka.document_versions v WHERE v.document_id=%s ORDER BY v.version DESC''', (document_id,)).fetchall()
```

- [ ] **Step 5: `docs/documentation.md`** — in the "API and pages" table, change the `GET /documents` row's text to end
  with "; `has_drafts=true` keeps documents with open drafts; each row has `draft_count`", note in the
  `GET /documents/{id}` row "versions say `from_draft`", and add after the `DELETE /documents/{id}` row:

```markdown
| `POST /drafts` | A draft of a document (`document_id`; text defaults to the live version) or of a new document (`slug`, optional `folder`, `title`, `markdown`, optional `resource_ids`) |
| `GET /drafts?new=true` | Open drafts of new documents (without `new`: all open drafts) |
| `GET /drafts/{id}` | A draft with its Markdown, sources, base and live version numbers, `stale` and the live Markdown |
| `PUT /drafts/{id}` | Save an open draft's text with its `revision` (marks it `edited`) |
| `POST /drafts/{id}/promote` | `{revision, confirm_stale?}`: save as the live version; 409 `STALE_BASE` when the live version moved on |
| `POST /drafts/{id}/discard` | `{revision}`: discard (kept for audit, never listed) |
| `GET /documents/{id}/drafts` | Open drafts of a document (no Markdown) |
| `GET /documents/{id}/connections` | `depends_on`, `used_by` and `built_from` (the live version's sources) |
| `POST /documents/{id}/links`, `DELETE /documents/{id}/links/{to_id}` | Add or remove a `depends_on` link |
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_document_draft_reads.py tests/test_document_drafts.py tests/test_documents.py tests/test_documentation_folders.py tests/test_inventory_tools.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/document_drafts.py services/admin-api/src/knowledge_admin/documents.py \
  services/admin-api/tests/test_document_draft_reads.py docs/documentation.md
git commit -m "feat(admin-api): draft lists, connections and depends-on links; draft counts on the documents list

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Generate documents runs write document drafts

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/document_drafts.py` (run-draft functions, `SESSION_DRAFT`)
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py` (`finish_turn`)
- Modify: `services/admin-api/src/knowledge_admin/generations_api.py` (`_select`, `POST /sessions/{id}/document`)
- Modify: `services/admin-api/src/knowledge_admin/live/session_hub.py` (`STATE`)
- Modify: `services/admin-api/tests/test_generations.py` (remove the old Save document test)
- Test: `services/admin-api/tests/test_document_drafts_worker.py` (new)

**Interfaces:**
- Consumes: Task 2's `create_draft`, `add_parent_source`, `live_content`, `audit`; `documents.document_slug`;
  `data_root.generated_docs_folder`; `ka.agent_run_events` tool calls (`kind='tool_call'`, `payload.name`,
  `payload.arguments` — a JSON string from the SDK, possibly an object), run keys `cc:<session_id>:<turn>`.
- Produces (module `knowledge_admin.document_drafts`):
  - `SESSION_DRAFT` — SQL select item `(...) AS document_draft` (`{"id", "document_id"}` of the session `s`'s latest open
    document draft, or NULL);
  - `run_resources(snapshot: dict) -> dict` (the run's selection as `resource_ids`);
  - `call_arguments(payload: dict) -> dict`; `export_key(path: str) -> tuple[str | None, str] | None` (`(folder, slug)`);
  - `record_run_sources(conn, project, draft_id, document_id, base_version_id, session_id, turn: int) -> None`;
  - `save_run_draft(conn, session_id: UUID, turn: int, actor: UUID | None = None) -> dict | None`
    (`{"id", "document_id"}`);
  - `save_run_draft_safely(conn, session_id: UUID, turn: int) -> None` (savepoint; logs and swallows a failure).
- Produces (API): session detail / list rows and the live `session` state carry `document_draft`;
  `POST /api/generations/sessions/{id}/document` (no body; `{}` accepted) → 201 `{"draft": {"id", "document_id"}}`.

- [ ] **Step 1: Write the failing test** — `services/admin-api/tests/test_document_drafts_worker.py`:

```python
"""A Generate documents run's finished turns write a document draft: created, then updated until a person saves it;
aimed at the document with the run's name and folder, or a new one; its sources are the documents the run read."""
import json
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from knowledge_admin import document_drafts
from knowledge_admin.documents import DocumentInput, save_document
from knowledge_admin.knowledge_agents import session_worker as shared
from test_generations import sessions, start  # noqa: F401  (fixture)
from test_repositories import HEADERS


def runner(sessions) -> tuple[UUID, shared.SessionRunner]:
    sid = UUID(start(sessions)['id'])
    [row] = shared.claim(sessions.store, 'worker-1', [], 4)
    return sid, shared.SessionRunner(sessions.store, row)


def drafts_of(sessions, sid) -> list[dict]:
    with sessions.store.connect() as conn:
        return conn.execute('''SELECT id,document_id,slug,folder,title,purpose,markdown,origin,edited,revision,resource_ids,
            base_version_id FROM ka.document_drafts WHERE session_id=%s ORDER BY created_at,id''', (sid,)).fetchall()


def tool_call(sessions, sid, turn: int, seq: int, name: str, arguments: dict) -> None:
    with sessions.store.connect() as conn:
        conn.execute('''INSERT INTO ka.agent_run_events(project_id,run_key,seq,kind,agent_id,payload)
            VALUES (%s,%s,%s,'tool_call','ad-hoc',%s)''', (sessions.store.project(conn, 'shop'), f'cc:{sid}:{turn}', seq,
            Jsonb({'name': name, 'call_id': f'call-{seq}', 'arguments': json.dumps(arguments)})))


def document(sessions, slug: str, folder: str | None, markdown: str) -> dict:
    with sessions.store.connect() as conn:
        return save_document(conn, sessions.store.project(conn, 'shop'), None,
                             DocumentInput(slug=slug, title=slug.replace('-', ' ').title(), markdown=markdown), folder=folder)


def test_a_documents_turn_creates_its_draft_then_updates_it(sessions):
    sid, run = runner(sessions)
    run.finish_turn(1, 'waiting', '', draft='# Payments API v1', usage=None)
    [first] = drafts_of(sessions, sid)
    assert (first['document_id'], first['slug'], first['folder'], first['title'], first['purpose'], first['origin'],
            first['markdown']) == (None, 'payments-api-reference', 'payments-api-docs', 'Payments – API reference', 'code',
                                   'agent', '# Payments API v1')
    assert first['resource_ids'] == {'code_project_ids': [sessions.repo], 'environment_ids': [sessions.environment],
                                     'service_ids': [sessions.service], 'server_ids': [], 'deployment_ids': []}
    run.finish_turn(2, 'waiting', '', draft='# Payments API v2', usage=None)
    [second] = drafts_of(sessions, sid)
    assert (second['id'], second['markdown'], second['revision']) == (first['id'], '# Payments API v2', 2)
    run.finish_turn(3, 'failed', 'Broken.', draft='# Lost', usage=None)
    assert drafts_of(sessions, sid)[0]['markdown'] == '# Payments API v2'
    session = sessions.client.get(f'/api/generations/sessions/{sid}', headers=HEADERS).json()
    assert session['document_draft'] == {'id': str(first['id']), 'document_id': None}
    promoted = sessions.client.post(f"/api/documentation/drafts/{first['id']}/promote", headers=HEADERS, json={'revision': 2})
    assert promoted.status_code == 200, promoted.text
    created = sessions.client.get(f"/api/documentation/documents/{promoted.json()['document_id']}", headers=HEADERS).json()
    assert (created['folder'], created['code_project_ids'], created['current_version']['origin']) == (
        'payments-api-docs', [sessions.repo], 'generated')
    session = sessions.client.get(f'/api/generations/sessions/{sid}', headers=HEADERS).json()
    assert (session['document_id'], session['document_draft']) == (created['id'], None)


def test_once_a_person_saves_the_draft_the_next_turn_starts_a_new_one(sessions):
    sid, run = runner(sessions)
    run.finish_turn(1, 'waiting', '', draft='# Agent text', usage=None)
    [draft] = drafts_of(sessions, sid)
    edited = sessions.client.put(f"/api/documentation/drafts/{draft['id']}", headers=HEADERS, json={
        'revision': draft['revision'], 'title': draft['title'], 'markdown': '# Agent text, corrected by a person'})
    assert edited.status_code == 200, edited.text
    run.finish_turn(2, 'waiting', '', draft='# Agent text v2', usage=None)
    kept, new = drafts_of(sessions, sid)
    assert (kept['markdown'], kept['edited']) == ('# Agent text, corrected by a person', True)
    assert (new['markdown'], new['edited'], new['origin']) == ('# Agent text v2', False, 'agent')
    run.finish_turn(3, 'waiting', '', draft='# Agent text v3', usage=None)
    assert [item['markdown'] for item in drafts_of(sessions, sid)] == ['# Agent text, corrected by a person', '# Agent text v3']


def test_the_draft_revises_the_document_with_the_runs_name_and_folder(sessions):
    live = document(sessions, 'payments-api-reference', 'payments-api-docs', '# Live text')
    sid, run = runner(sessions)
    run.finish_turn(1, 'waiting', '', draft='# Agent text', usage=None)
    [draft] = drafts_of(sessions, sid)
    assert (draft['document_id'], draft['base_version_id'], draft['slug'], draft['folder'], draft['title']) == (
        live['id'], live['version_id'], None, None, 'Payments Api Reference')
    detail = sessions.client.get(f"/api/documentation/drafts/{draft['id']}", headers=HEADERS).json()
    assert [(s['kind'], s['source_version']) for s in detail['sources']] == [('parent', 1)]
    assert (detail['live_markdown'], detail['markdown']) == ('# Live text', '# Agent text')


def test_sources_are_the_documents_the_run_read_at_the_version_it_read(sessions):
    alpha = document(sessions, 'alpha', 'team', '# Alpha v1')
    document(sessions, 'beta', None, '# Beta')
    document(sessions, 'gamma', 'team/deep', '# Gamma')
    sid, run = runner(sessions)
    tool_call(sessions, sid, 1, 1, 'get_document', {'document_id': str(alpha['id'])})
    tool_call(sessions, sid, 1, 2, 'read_document', {'collection': 'documents', 'path': 'generated-documents/beta/README.md'})
    tool_call(sessions, sid, 1, 3, 'read_document', {'collection': 'documents', 'path': 'team/deep/gamma/README.md'})
    tool_call(sessions, sid, 1, 4, 'read_document', {'collection': 'documents', 'path': 'team/alpha/notes.md'})
    tool_call(sessions, sid, 1, 5, 'get_document', {'document_id': 'not-a-uuid'})
    tool_call(sessions, sid, 1, 6, 'list_files', {'path': 'team/alpha/README.md'})
    with sessions.store.connect() as conn:  # alpha changes after the run read it
        save_document(conn, sessions.store.project(conn, 'shop'), None,
                      DocumentInput(slug='alpha', title='Alpha', markdown='# Alpha v2', revision=1), alpha['id'])
    run.finish_turn(1, 'waiting', '', draft='# Agent text', usage=None)
    [draft] = drafts_of(sessions, sid)
    url = f"/api/documentation/drafts/{draft['id']}"
    read = lambda: sorted((s['kind'], s['source_title'], s['source_version'])
                          for s in sessions.client.get(url, headers=HEADERS).json()['sources'])
    assert read() == [('read', 'Alpha', 1), ('read', 'Beta', 1), ('read', 'Gamma', 1)]
    tool_call(sessions, sid, 2, 1, 'get_document', {'document_id': str(alpha['id'])})  # read again, now at v2
    run.finish_turn(2, 'waiting', '', draft='# Agent text v2', usage=None)
    assert read() == [('read', 'Alpha', 1), ('read', 'Beta', 1), ('read', 'Gamma', 1)]  # recomputed; first read kept


def test_a_document_draft_that_cannot_be_saved_never_fails_the_turn(sessions, monkeypatch):
    def broken(*args, **kwargs):
        raise psycopg.errors.CheckViolation('broken')
    monkeypatch.setattr(document_drafts, 'save_run_draft', broken)  # Review focus 4
    sid, run = runner(sessions)
    run.finish_turn(1, 'waiting', '', draft='# Agent text', usage=None)
    with sessions.store.connect() as conn:
        row = conn.execute('SELECT status,draft,draft_turn FROM ka.agent_sessions WHERE id=%s', (sid,)).fetchone()
    assert row == {'status': 'waiting', 'draft': '# Agent text', 'draft_turn': 1}
    assert drafts_of(sessions, sid) == []


def test_save_as_draft_turns_an_old_runs_draft_into_a_document_draft(sessions):
    client, store = sessions.client, sessions.store
    sid = start(sessions)['id']
    url = f'/api/generations/sessions/{sid}'
    empty = client.post(url + '/document', headers=HEADERS, json={})
    assert empty.status_code == 409 and empty.json()['error']['message'] == 'There is no draft yet. Wait for the agent to finish a turn.'
    with store.connect() as conn:  # a run from before drafts: a run draft, no document draft
        conn.execute("UPDATE ka.agent_sessions SET status='waiting',turns=1,draft='# Payments API',draft_turn=1 WHERE id=%s", (sid,))
    assert client.get(url, headers=HEADERS).json()['document_draft'] is None
    saved = client.post(url + '/document', headers=HEADERS, json={})
    assert saved.status_code == 201, saved.text
    draft = saved.json()['draft']
    assert draft['document_id'] is None
    assert client.get(url, headers=HEADERS).json()['document_draft'] == draft
    assert client.get('/api/documentation/documents', headers=HEADERS).json()['total'] == 0  # nothing went live
    detail = client.get(f"/api/documentation/drafts/{draft['id']}", headers=HEADERS).json()
    assert (detail['markdown'], detail['origin'], detail['author']) == ('# Payments API', 'agent', 'admin@example.test')
    assert client.post(url + '/document', headers=HEADERS, json={}).json()['draft']['id'] == draft['id']
    assert not (sessions.data / 'generated-documents/payments-api-docs').exists()
    assert client.post(url + '/document', json={}).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-api && uv run pytest -q tests/test_document_drafts_worker.py`
Expected: FAIL (no document drafts are written; `document_draft` missing from the session detail).

- [ ] **Step 3: `document_drafts.py`** — add `import json`, `import logging`, `logger = logging.getLogger(__name__)`,
  extend the imports to `from knowledge_admin.data_root import documentation_folder, generated_docs_folder` and
  `from knowledge_admin.documents import DocumentInput, document_slug, export_args, export_document, export_input,
  save_document`, then add at module level:

```python
# The session `s`'s latest open document draft, for session rows and the live session state.
SESSION_DRAFT = """(SELECT jsonb_build_object('id',x.id,'document_id',x.document_id) FROM ka.document_drafts x
    WHERE x.session_id=s.id AND x.status='open' ORDER BY x.created_at DESC,x.id DESC LIMIT 1) AS document_draft"""
DOCUMENT_TOOLS = ['get_document', 'read_document']


def run_resources(snapshot: dict) -> dict:
    """A Generate documents run's selection (its context snapshot) as the resource_ids a new document links on promote."""
    return {'code_project_ids': [str(item) for item in snapshot.get('code_project_ids', [])],
            'environment_ids': [str(item['id']) for item in snapshot.get('environments', [])],
            'service_ids': [str(item['id']) for item in snapshot.get('services', [])],
            'server_ids': [str(item['id']) for item in snapshot.get('servers', [])],
            'deployment_ids': []}


def call_arguments(payload: dict) -> dict:
    """A recorded tool call's arguments: the SDK records them as a JSON string."""
    value = payload.get('arguments')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def export_key(path: str) -> tuple[str | None, str] | None:
    """(folder, slug) of a document export path [<GENERATED_DOCS_FOLDER>/][<folder>/]<slug>/README.md, else None."""
    parts = [part for part in path.strip().split('/') if part]
    try:
        prefix = generated_docs_folder().split('/')
    except ValueError:
        prefix = []
    if prefix and parts[:len(prefix)] == prefix and len(parts) > len(prefix) + 1:
        parts = parts[len(prefix):]
    if len(parts) < 2 or parts[-1] != 'README.md':
        return None
    return '/'.join(parts[:-2]) or None, parts[-2]


def record_run_sources(conn, project: UUID, draft_id: UUID, document_id: UUID | None, base_version_id: UUID | None,
                       session_id: UUID, turn: int) -> None:
    """Recompute a run draft's sources: its parent, and every document the run read with get_document or
    read_document (an export path) in turns 1..turn, at the version live at its first read."""
    conn.execute('DELETE FROM ka.document_sources WHERE draft_id=%s', (draft_id,))
    if document_id is not None:
        add_parent_source(conn, project, draft_id, base_version_id)
    keys = [f'cc:{session_id}:{number}' for number in range(1, turn + 1)]
    calls = conn.execute('''SELECT payload,created_at FROM ka.agent_run_events WHERE run_key=ANY(%s) AND kind='tool_call'
        AND payload->>'name'=ANY(%s) ORDER BY id''', (keys, DOCUMENT_TOOLS)).fetchall()
    for call in calls:
        arguments = call_arguments(call['payload'])
        if call['payload']['name'] == 'get_document':
            try:
                wanted = UUID(str(arguments.get('document_id', '')))
            except ValueError:
                continue
            found = conn.execute('SELECT id FROM ka.documents WHERE id=%s AND project_id=%s', (wanted, project)).fetchone()
        else:
            key = export_key(str(arguments.get('path', '')))
            if key is None:
                continue
            found = conn.execute('''SELECT id FROM ka.documents WHERE project_id=%s AND folder IS NOT DISTINCT FROM %s
                AND slug=%s AND phase_metadata IS NULL''', (project, *key)).fetchone()
        if found is None or found['id'] == document_id:
            continue
        version = conn.execute('''SELECT id,version,title FROM ka.document_versions WHERE document_id=%s AND created_at<=%s
            ORDER BY version DESC LIMIT 1''', (found['id'], call['created_at'])).fetchone()
        if version is None:  # read before the document existed
            continue
        conn.execute('''INSERT INTO ka.document_sources(project_id,draft_id,kind,source_document_id,source_version_id,
            source_title,source_version) VALUES (%s,%s,'read',%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (project, draft_id, found['id'], version['id'], version['title'], version['version']))


def save_run_draft(conn, session_id: UUID, turn: int, actor: UUID | None = None) -> dict | None:
    """A documents run's draft (agent_sessions.draft) as a document draft: update the run's latest open draft no person
    has saved, or create one — a draft of the document at the run's folder and name, or of a new document there. Its
    sources are recomputed from the run's tool calls. None when the run has no draft."""
    session = conn.execute('''SELECT id,project_id,purpose,document_name,folder,draft,context_snapshot
        FROM ka.agent_sessions WHERE id=%s''', (session_id,)).fetchone()
    if session is None or session['purpose'] != 'documents' or not (session['draft'] or '').strip():
        return None
    project = session['project_id']
    current = conn.execute('''SELECT id,document_id,base_version_id FROM ka.document_drafts WHERE session_id=%s
        AND status='open' AND NOT edited ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE''', (session_id,)).fetchone()
    if current is not None:
        conn.execute('UPDATE ka.document_drafts SET markdown=%s,revision=revision+1 WHERE id=%s',
                     (session['draft'], current['id']))
        audit(conn, project, actor, 'document.draft.updated', 'document_draft', current['id'],
              {'session_id': session_id, 'turn': turn})
        draft = dict(current)
    else:
        slug = document_slug(session['document_name'])
        existing = conn.execute('''SELECT id FROM ka.documents WHERE project_id=%s AND folder IS NOT DISTINCT FROM %s
            AND slug=%s''', (project, session['folder'], slug)).fetchone()
        snapshot = session['context_snapshot']
        if existing is not None:  # the run revises it: its metadata stays, the text is the run's
            content = {**live_content(conn, project, existing['id']), 'markdown': session['draft']}
        else:
            content = {**NEW_DEFAULTS, 'title': session['document_name'], 'markdown': session['draft'],
                       'purpose': 'code' if snapshot.get('code_project_ids') else 'explanation'}
        draft_id = create_draft(conn, project, actor, content, origin='agent',
                                document_id=existing['id'] if existing else None, slug=slug, folder=session['folder'],
                                resources=run_resources(snapshot), session_id=session_id)
        draft = conn.execute('SELECT id,document_id,base_version_id FROM ka.document_drafts WHERE id=%s',
                             (draft_id,)).fetchone()
    record_run_sources(conn, project, draft['id'], draft['document_id'], draft['base_version_id'], session_id, turn)
    return {'id': draft['id'], 'document_id': draft['document_id']}


def save_run_draft_safely(conn, session_id: UUID, turn: int) -> None:
    """save_run_draft inside the turn's transaction, in a savepoint: a failure loses only the document draft, never the
    turn (its run draft, tokens and history)."""
    try:
        with conn.transaction():
            save_run_draft(conn, session_id, turn)
    except (psycopg.Error, ValueError, HTTPException) as exc:
        logger.warning('Session %s turn %s: the document draft was not saved (%s)', session_id, turn, type(exc).__name__)
```

- [ ] **Step 4: `session_worker.py`** — import `from knowledge_admin.document_drafts import save_run_draft_safely`; in
  `finish_turn` make the UPDATE return the purpose and save the document draft after the commit callback:

```python
                 'id': self.id, 'worker': self.worker_id}).fetchone()
```

  becomes (the statement's `RETURNING id` changes to `RETURNING id,purpose`):

```python
                stop_requested_at=NULL, last_activity_at=now() WHERE id=%(id)s AND worker_id=%(worker)s RETURNING id,purpose""",
                {'status': status, 'message': message, 'draft': draft, 'turn': number,
                 'input': usage['input_tokens'], 'output': usage['output_tokens'], 'cached': usage['cached_tokens'],
                 'id': self.id, 'worker': self.worker_id}).fetchone()
            if updated is not None and status == 'waiting' and commit is not None:
                commit(conn)
            if updated is not None and draft is not None and updated['purpose'] == 'documents':
                save_run_draft_safely(conn, self.id, number)  # the turn's draft becomes a document draft
```

  and add to the docstring: "A documents session's new draft is also saved as a document draft (document_drafts) in the
  same transaction; a failure there loses only the document draft." (The recorder flushes the turn's events before
  `finish_turn`, so the turn's own tool calls are in `agent_run_events` when the sources are computed.)

- [ ] **Step 5: `generations_api.py`** —
  - imports: replace the `knowledge_admin.documents` import with `from knowledge_admin.documents import document_slug`
    and add `from knowledge_admin.document_drafts import SESSION_DRAFT, save_run_draft`; delete the `SaveInput` class.
  - `_select`: change `s.created_at,s.last_activity_at,s.closed_at{extra}` to
    `s.created_at,s.last_activity_at,s.closed_at,{SESSION_DRAFT}{extra}`.
  - replace the `save` route:

```python
    @router.post('/sessions/{session_id}/document', status_code=201)
    def save(session_id: UUID, request: Request) -> dict:
        """Save as draft, for runs from before document drafts: the run's draft becomes a document draft (as every
        finished turn now does by itself). Nothing goes live; a person promotes the draft on its page."""
        db = database()
        with db.connect() as conn:
            project = db.project(conn, app_name)
            session = conn.execute('SELECT id,purpose,draft,turns FROM ka.agent_sessions WHERE id=%s AND project_id=%s FOR UPDATE',
                                   (session_id, project)).fetchone()
            if session is None:
                raise HTTPException(404, 'Session not found.')
            if session['purpose'] != 'documents':
                raise HTTPException(400, ONLY_DOCUMENTS)
            if not (session['draft'] or '').strip():
                raise HTTPException(409, 'There is no draft yet. Wait for the agent to finish a turn.')
            actor = db.actor(conn, *request_actor(request))
            draft = save_run_draft(conn, session_id, session['turns'], actor)
            conn.execute('UPDATE ka.agent_sessions SET last_activity_at=now() WHERE id=%s', (session_id,))
            audit(conn, actor, 'generation.session.draft_saved', 'agent_session', {'draft_id': str(draft['id'])}, session_id)
            return jsonable_encoder({'draft': draft})
```

  - update the module docstring's "saving the draft as a document" to "saving the run's draft as a document draft".

- [ ] **Step 6: `live/session_hub.py`** — import `from knowledge_admin.document_drafts import SESSION_DRAFT` and make
  `STATE`:

```python
STATE = """SELECT s.status,s.status_message,s.turns,s.input_tokens,s.output_tokens,s.cached_tokens,s.model_calls,s.reasoning_tokens,s.draft,
    s.draft_turn,s.document_id,coalesce(s.lease_expires_at > now(), false) AS worker_active,
    (SELECT count(*) FROM ka.agent_session_inputs i WHERE i.session_id=s.id AND i.status='queued') AS queued,
    """ + SESSION_DRAFT + """
    FROM ka.agent_sessions s WHERE s.id=%s"""
```

  (The document draft is written in the same transaction as the turn's session update, so the notice that update
  sends already sees it.)

- [ ] **Step 7: `tests/test_generations.py`** — delete
  `test_save_document_needs_confirmation_for_the_same_name_and_exports_into_the_folder` (replaced by
  `test_save_as_draft_turns_an_old_runs_draft_into_a_document_draft`) and the now unused
  `from knowledge_admin.documents import SAME_DOCUMENT_NAME` import. `tests/test_test_sessions.py`
  (`...ONLY_DOCUMENTS`) stays as it is.

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd services/admin-api && uv run pytest -q tests/test_document_drafts_worker.py tests/test_generations.py tests/test_session_worker.py tests/test_test_sessions.py tests/test_session_hub.py tests/test_session_stream.py tests/test_native_sessions.py tests/test_document_drafts.py`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add services/admin-api/src/knowledge_admin/document_drafts.py \
  services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py \
  services/admin-api/src/knowledge_admin/generations_api.py services/admin-api/src/knowledge_admin/live/session_hub.py \
  services/admin-api/tests/test_document_drafts_worker.py services/admin-api/tests/test_generations.py
git commit -m "feat(admin-api): Generate documents turns write document drafts with the documents they read

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: UI — drafts library, line diff, draft view, draft page, documents list

**Files:**
- Create: `services/admin-ui/app/lib/drafts.ts`, `services/admin-ui/app/lib/lineDiff.ts`,
  `services/admin-ui/app/components/DiffView.tsx`, `services/admin-ui/app/components/DraftView.tsx`,
  `services/admin-ui/app/components/DraftEditForm.tsx`, `services/admin-ui/app/components/DraftPageView.tsx`,
  `services/admin-ui/app/routes/documentDraft.tsx`
- Modify: `services/admin-ui/app/lib/api.ts` (`ApiError.code`), `services/admin-ui/app/lib/documents.ts` (types),
  `services/admin-ui/app/routes.ts`, `services/admin-ui/app/components/DocumentsView.tsx`,
  `services/admin-ui/app/components/__tests__/documents.test.tsx` (fetch mock answers the drafts list)
- Test: `services/admin-ui/app/lib/__tests__/lineDiff.test.ts`, `services/admin-ui/app/components/__tests__/documentDrafts.test.tsx` (new)

**Interfaces:**
- Consumes: the Task 2–3 API (`/api/documentation/drafts…`, `/documents/{id}/drafts|connections|links`), error body
  `{error: {message, code?}}`.
- Produces:
  - `ApiError(message, status, code?)` with `code?: string`;
  - `lib/drafts.ts`: types `DraftOrigin`, `DraftSource`, `DraftSummary`, `DraftContent`, `DraftDetail`, `DraftCreate`,
    `PromoteResult`, `LinkedDocument`, `Connections`; `STALE_BASE`; `getNewDrafts(signal?)`,
    `getDocumentDrafts(documentId, signal?)`, `getDraft(id, signal?)`, `createDraft(value: DraftCreate)`,
    `updateDraft(id, revision, content: DraftContent)`, `promoteDraft(id, revision, confirmStale: boolean)`,
    `discardDraft(id, revision)`, `getConnections(documentId, signal?)`, `addLink(documentId, toId)`,
    `removeLink(documentId, toId)`, `draftPath(draft: {id, document_id}) => string`
    (`/documents/<doc>?tab=drafts&draft=<id>` or `/documents/drafts/<id>`);
  - `lib/lineDiff.ts`: `type DiffLine = { kind: 'same' | 'added' | 'removed'; text: string }`, `MAX_DIFF_CELLS`,
    `lineDiff(before, after): { lines: DiffLine[]; approximate: boolean }`;
  - components: `DiffView({ before, after })` (added lines in `<ins>`, removed in `<del>`); `DraftView({ draftId,
    reloadKey?, onEdit?, onPromoted, onDiscarded, onLoaded? })` (a `<section aria-label="Draft">`), and its named
    exports `DraftAuthor({ draft })`, `SourceList({ sources, empty })`; `DraftEditForm({ draft, onCancel, onSaved })`;
    `DraftPageView()` (route `documents/drafts/:draftId`);
  - `DocumentSummary.draft_count?: number`; `DocumentDetail.versions[].from_draft?: boolean`.

- [ ] **Step 1: Write the failing tests**

  `app/lib/__tests__/lineDiff.test.ts`:

```ts
import { lineDiff, MAX_DIFF_CELLS } from '../lineDiff';

test('same, removed and added lines in order', () => {
  expect(lineDiff('a\nb\nc', 'a\nx\nc').lines).toEqual([
    { kind: 'same', text: 'a' }, { kind: 'removed', text: 'b' }, { kind: 'added', text: 'x' }, { kind: 'same', text: 'c' }]);
});

test('identical texts have no changes; an insertion keeps the common lines', () => {
  expect(lineDiff('a\nb', 'a\nb').lines.every((line) => line.kind === 'same')).toBe(true);
  expect(lineDiff('a\nc', 'a\nb\nc').lines.map((line) => line.kind)).toEqual(['same', 'added', 'same']);
  expect(lineDiff('', '# New').lines.map((line) => line.kind)).toEqual(['removed', 'added']);
});

test('a change too large to compare is shown as removed then added', () => {
  const size = Math.ceil(Math.sqrt(MAX_DIFF_CELLS)) + 1;
  const before = Array.from({ length: size }, (_, i) => `old ${i}`).join('\n');
  const after = Array.from({ length: size }, (_, i) => `new ${i}`).join('\n');
  const result = lineDiff(`same\n${before}\nend`, `same\n${after}\nend`);
  expect(result.approximate).toBe(true);
  expect(result.lines[0]).toEqual({ kind: 'same', text: 'same' });
  expect(result.lines.at(-1)).toEqual({ kind: 'same', text: 'end' });
  expect(result.lines.filter((line) => line.kind === 'removed')).toHaveLength(size);
  expect(result.lines.filter((line) => line.kind === 'added')).toHaveLength(size);
});
```

  `app/components/__tests__/documentDrafts.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import DocumentsView from '../DocumentsView';
import DraftPageView from '../DraftPageView';

vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const NativeRequest = globalThis.Request;
// React Router's Node Request cannot consume jsdom's AbortSignal. Fetch is mocked in this suite.
beforeEach(() => vi.stubGlobal('Request', class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
}));
afterEach(() => vi.unstubAllGlobals());

const resources = { code_projects: [], services: [], servers: [], deployments: [], environments: [] };
const doc = { id: 'doc-1', slug: 'runbook', title: 'Runbook', description: 'How to run it', purpose: 'runbook', audience: 'both', tags: [],
  code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [], revision: 1, version: 1, current_version_id: 'v1',
  published_version_id: null, content_hash: 'a', created_at: '2026-10-12T10:00:00Z', updated_at: '2026-10-12T10:00:00Z', draft_count: 2 };
const newDraft = { id: 'd9', document_id: null, slug: 'disk-layout', folder: 'servers/web-1', title: 'Disk layout', origin: 'agent',
  session_id: 's1', session_title: 'Disk layout', author: null, edited: false, status: 'open', revision: 1, base_version_id: null,
  base_version: null, live_version: null, stale: false, created_at: '2026-10-12T10:00:00Z', updated_at: '2026-10-12T11:00:00Z' };
const detail = { ...newDraft, description: '', purpose: 'explanation', audience: 'both', tags: [], markdown: '# Disks', resource_ids: {},
  sources: [{ kind: 'read', source_document_id: null, source_version_id: null, source_title: 'Old overview', source_version: 4, deleted: true }],
  live_markdown: null, document_title: null, promoted_version_id: null, decided_at: null };

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
function show(url: string) {
  const router = createMemoryRouter([{ path: '/documents', element: <DocumentsView /> },
    { path: '/documents/drafts/:draftId', element: <DraftPageView /> }, { path: '/documents/:documentId', element: <p>Document page</p> }],
    { initialEntries: [url] });
  render(<RouterProvider router={router} />);
  return router;
}

test("the list shows each document's drafts, a Has drafts filter and the new-document drafts", async () => {
  const calls = api({
    'GET /api/documentation/documents?': () => [200, { documents: [doc], total: 1 }],
    'GET /api/documentation/documents?has_drafts=true': () => [200, { documents: [doc], total: 1 }],
    'GET /api/documentation/resources': () => [200, resources],
    'GET /api/documentation/drafts?new=true': () => [200, { drafts: [newDraft] }],
    'POST /api/documentation/drafts/d9/discard': () => [200, { ...detail, status: 'discarded' }],
  });
  show('/documents');
  expect(await screen.findByRole('link', { name: '2 drafts' })).toHaveAttribute('href', '/documents/doc-1?tab=drafts');
  const section = within(screen.getByRole('region', { name: 'New drafts' }));
  expect(section.getByRole('link', { name: 'Disk layout' })).toHaveAttribute('href', '/documents/drafts/d9');
  expect(section.getByText('servers/web-1')).toBeInTheDocument();
  expect(section.getByRole('link', { name: 'Agent run' })).toHaveAttribute('href', '/documentation/generations/s1');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Has drafts' }));
  await waitFor(() => expect(calls.some((c) => c.key === 'GET /api/documentation/documents?has_drafts=true')).toBe(true));
  fireEvent.click(section.getByRole('button', { name: 'Discard Disk layout' }));
  fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Discard draft' }));
  await waitFor(() => expect(calls.find((c) => c.key === 'POST /api/documentation/drafts/d9/discard')?.body).toEqual({ revision: 1 }));
});

test('a new-document draft page previews it, lists its sources and Promote creates the document', async () => {
  const calls = api({
    'GET /api/documentation/drafts/d9': () => [200, detail],
    'POST /api/documentation/drafts/d9/promote': () => [200, { draft: { ...detail, status: 'promoted', document_id: 'doc-9' },
      document_id: 'doc-9', version_id: 'v1', revision: 1, export_error: null }],
  });
  const router = show('/documents/drafts/d9');
  expect(await screen.findByText('# Disks')).toBeInTheDocument();
  expect(screen.getByText('Old overview v4 (deleted)')).toBeInTheDocument();
  expect(screen.queryByRole('tab', { name: 'Changes' })).toBeNull();  // nothing live to compare with
  fireEvent.click(screen.getByRole('button', { name: 'Promote' }));
  await waitFor(() => expect(router.state.location.pathname).toBe('/documents/doc-9'));
  expect(calls.find((c) => c.key === 'POST /api/documentation/drafts/d9/promote')!.body).toEqual({ revision: 1, confirm_stale: false });
});

test('Edit on the draft page saves the draft with its revision', async () => {
  let saved = false;
  const calls = api({
    'GET /api/documentation/drafts/d9': () => [200, saved ? { ...detail, markdown: '# Disks v2', revision: 2 } : detail],
    'PUT /api/documentation/drafts/d9': () => { saved = true; return [200, { ...detail, markdown: '# Disks v2', revision: 2, edited: true }]; },
  });
  show('/documents/drafts/d9');
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  fireEvent.change(screen.getByLabelText('Markdown'), { target: { value: '# Disks v2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  expect(await screen.findByText('# Disks v2')).toBeInTheDocument();
  expect(screen.getByText('Draft saved.')).toBeInTheDocument();
  expect(calls.find((c) => c.key === 'PUT /api/documentation/drafts/d9')!.body).toEqual({
    revision: 1, title: 'Disk layout', description: '', purpose: 'explanation', audience: 'both', tags: [], markdown: '# Disks v2' });
});

test('a refused promote shows why and reloads the draft, which may now revise an existing document', async () => {
  let converted = false;
  api({
    'GET /api/documentation/drafts/d9': () => [200, converted ? { ...detail, document_id: 'doc-3', slug: null, folder: null,
      base_version: 1, live_version: 1, live_markdown: '# Old', revision: 2 } : detail],
    'POST /api/documentation/drafts/d9/promote': () => { converted = true; return [409, { error: { message:
      'A document named disk-layout was created in this folder meanwhile. The draft is now a draft of it. Review it again.' } }]; },
  });
  show('/documents/drafts/d9');
  fireEvent.click(await screen.findByRole('button', { name: 'Promote' }));
  expect(await screen.findByText(/was created in this folder meanwhile/)).toBeInTheDocument();
  expect(await screen.findByRole('link', { name: 'Open it on its document' })).toHaveAttribute('href', '/documents/doc-3?tab=drafts&draft=d9');
  expect(screen.getByRole('tab', { name: 'Changes' })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/lib/__tests__/lineDiff.test.ts app/components/__tests__/documentDrafts.test.tsx`
Expected: FAIL (cannot resolve `../lineDiff`, `../DraftPageView`).

- [ ] **Step 3: `lib/api.ts`** — `ApiError` and `request` carry the error code:

```ts
export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function request<T>(url: string, init?: RequestInit, responseType: "json" | "text" = "json"): Promise<T> {
  const response = init ? await fetch(url, init) : await fetch(url);
  if (!response.ok) {
    let message = `request failed (${response.status})`;
    let code: string | undefined;
    try {
      const body = await response.json();
      if (body?.error?.message) message = body.error.message;
      if (typeof body?.error?.code === "string") code = body.error.code;
    } catch { /* non-JSON error body — keep the status message */ }
    throw new ApiError(message, response.status, code);
  }
  if (response.status === 204) return undefined as T;
  if (responseType === "text") return response.text() as Promise<T>;
  return response.json() as Promise<T>;
}
```

  `lib/documents.ts`: add `draft_count?: number;` to `DocumentSummary`, and change `DocumentDetail`'s `versions` element
  type to `Pick<DocumentVersion, 'id' | 'version' | 'title' | 'origin' | 'base_version_id' | 'content_hash' |
  'created_at'> & { from_draft?: boolean }`.

- [ ] **Step 4: `lib/lineDiff.ts`**

```ts
export type DiffLine = { kind: 'same' | 'added' | 'removed'; text: string };
/** Above this many line pairs (changed lines before × after) the middle is not compared: it would freeze the page. */
export const MAX_DIFF_CELLS = 4_000_000;

const mark = (kind: DiffLine['kind']) => (text: string): DiffLine => ({ kind, text });

/** A line diff (longest common subsequence) after trimming the common start and end. When the changed middle is too
 * large to compare, it is shown as removed, then added (`approximate`). */
export function lineDiff(before: string, after: string): { lines: DiffLine[]; approximate: boolean } {
  const a = before.split('\n'), b = after.split('\n');
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length, endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  const head = a.slice(0, start).map(mark('same')), tail = a.slice(endA).map(mark('same'));
  const x = a.slice(start, endA), y = b.slice(start, endB);
  if (x.length * y.length > MAX_DIFF_CELLS) {
    return { lines: [...head, ...x.map(mark('removed')), ...y.map(mark('added')), ...tail], approximate: true };
  }
  const n = x.length, m = y.length;
  const table = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i][j] = x[i] === y[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const middle: DiffLine[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { middle.push(mark('same')(x[i])); i++; j++; }
    else if (table[i + 1][j] >= table[i][j + 1]) { middle.push(mark('removed')(x[i])); i++; }
    else { middle.push(mark('added')(y[j])); j++; }
  }
  while (i < n) middle.push(mark('removed')(x[i++]));
  while (j < m) middle.push(mark('added')(y[j++]));
  return { lines: [...head, ...middle, ...tail], approximate: false };
}
```

- [ ] **Step 5: `components/DiffView.tsx`**

```tsx
import { useMemo } from 'react';
import { lineDiff } from '~/lib/lineDiff';

/** The draft against the live version, line by line: added lines in <ins>, removed lines in <del>. */
export default function DiffView({ before, after }: { before: string; after: string }) {
  const { lines, approximate } = useMemo(() => lineDiff(before, after), [before, after]);
  if (lines.every((line) => line.kind === 'same')) return <p className="text-sm text-muted-foreground">No changes against the live version.</p>;
  return <div className="flex flex-col gap-2">
    {approximate && <p className="text-xs text-muted-foreground">These texts are too long to compare line by line; the changed part is shown as removed, then added.</p>}
    <pre aria-label="Changes against the live version" className="max-h-[32rem] overflow-auto rounded-lg border p-2 text-xs leading-5">
      {lines.map((line, index) => line.kind === 'added'
        ? <ins key={index} className="block bg-green-500/10 no-underline before:mr-2 before:content-['+']">{line.text}</ins>
        : line.kind === 'removed'
          ? <del key={index} className="block bg-red-500/10 no-underline before:mr-2 before:content-['−']">{line.text}</del>
          : <span key={index} className="block before:mr-2 before:content-['_'] before:invisible">{line.text}</span>)}
    </pre>
  </div>;
}
```

- [ ] **Step 6: `lib/drafts.ts`**

```ts
import { request } from './api';
import type { DocumentInput } from './documents';

export type DraftOrigin = 'manual' | 'agent';
export interface DraftSource {
  kind: 'parent' | 'read'; source_document_id: string | null; source_version_id: string | null;
  source_title: string; source_version: number | null; deleted: boolean;
}
export interface DraftSummary {
  id: string; document_id: string | null; slug: string | null; folder: string | null; title: string; origin: DraftOrigin;
  session_id: string | null; session_title: string | null; author: string | null; edited: boolean;
  status: 'open' | 'promoted' | 'discarded'; revision: number; base_version_id: string | null;
  base_version: number | null; live_version: number | null; stale: boolean; created_at: string; updated_at: string;
}
export type DraftContent = Pick<DocumentInput, 'title' | 'description' | 'purpose' | 'audience' | 'tags' | 'markdown'>;
export interface DraftDetail extends DraftSummary, DraftContent {
  resource_ids: Record<string, string[]>; sources: DraftSource[]; live_markdown: string | null; document_title: string | null;
  promoted_version_id: string | null; decided_at: string | null;
}
export type DraftCreate = Partial<DraftContent> & {
  document_id?: string; slug?: string; folder?: string | null;
  resource_ids?: { code_project_ids: string[]; service_ids: string[]; server_ids: string[]; deployment_ids: string[]; environment_ids: string[] };
};
export interface PromoteResult { draft: DraftDetail; document_id: string; version_id: string; revision: number; export_error: string | null }
export interface LinkedDocument { id: string; title: string; slug: string; folder: string | null; version: number }
export interface Connections { depends_on: LinkedDocument[]; used_by: LinkedDocument[]; built_from: DraftSource[] }
/** The error code of a promote refused because the live version moved on; Promote anyway confirms it. */
export const STALE_BASE = 'STALE_BASE';

const json = (method: string, value: unknown) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
const base = '/api/documentation';
const draftUrl = (id: string) => `${base}/drafts/${encodeURIComponent(id)}`;
const documentUrl = (id: string) => `${base}/documents/${encodeURIComponent(id)}`;

export const getNewDrafts = (signal?: AbortSignal) => request<{ drafts: DraftSummary[] }>(`${base}/drafts?new=true`, { signal });
export const getDocumentDrafts = (documentId: string, signal?: AbortSignal) =>
  request<{ drafts: DraftSummary[] }>(`${documentUrl(documentId)}/drafts`, { signal });
export const getDraft = (id: string, signal?: AbortSignal) => request<DraftDetail>(draftUrl(id), { signal });
export const createDraft = (value: DraftCreate) => request<DraftDetail>(`${base}/drafts`, json('POST', value));
export const updateDraft = (id: string, revision: number, content: DraftContent) =>
  request<DraftDetail>(draftUrl(id), json('PUT', { revision, ...content }));
export const promoteDraft = (id: string, revision: number, confirmStale: boolean) =>
  request<PromoteResult>(`${draftUrl(id)}/promote`, json('POST', { revision, confirm_stale: confirmStale }));
export const discardDraft = (id: string, revision: number) => request<DraftDetail>(`${draftUrl(id)}/discard`, json('POST', { revision }));
export const getConnections = (documentId: string, signal?: AbortSignal) =>
  request<Connections>(`${documentUrl(documentId)}/connections`, { signal });
export const addLink = (documentId: string, toId: string) =>
  request<Connections>(`${documentUrl(documentId)}/links`, json('POST', { to_document_id: toId }));
export const removeLink = (documentId: string, toId: string) =>
  request<Connections>(`${documentUrl(documentId)}/links/${encodeURIComponent(toId)}`, { method: 'DELETE' });
/** Where a draft opens: its document's Drafts tab, or the page of a new document's draft. */
export const draftPath = (draft: { id: string; document_id: string | null }) => draft.document_id
  ? `/documents/${draft.document_id}?tab=drafts&draft=${draft.id}` : `/documents/drafts/${draft.id}`;
```

- [ ] **Step 7: `components/DraftView.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { ApiError } from '~/lib/api';
import { discardDraft, getDraft, promoteDraft, STALE_BASE, type DraftDetail, type DraftSource, type DraftSummary, type PromoteResult } from '~/lib/drafts';
import DiffView from './DiffView';
import Markdown from './Markdown';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';
import { Spinner } from '~/components/ui/spinner';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';

const failure = (e: unknown, fallback: string) => e instanceof Error ? e.message : fallback;

/** Who wrote a draft: the person, or "Agent run" linking to the run. */
export function DraftAuthor({ draft }: { draft: Pick<DraftSummary, 'origin' | 'session_id' | 'session_title' | 'author'> }) {
  if (draft.origin === 'agent') return draft.session_id
    ? <Link className="underline" title={draft.session_title ?? undefined} to={`/documentation/generations/${encodeURIComponent(draft.session_id)}`}>Agent run</Link>
    : <span>Agent run</span>;
  return <span>{draft.author || 'Someone'}</span>;
}

/** The documents a draft or version was built from; a deleted one keeps its title and version. */
export function SourceList({ sources, empty }: { sources: DraftSource[]; empty: string }) {
  if (sources.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return <ul className="flex flex-col gap-1 text-sm">{sources.map((source, index) => <li key={`${source.source_document_id ?? 'deleted'}-${index}`}>
    <span className="text-muted-foreground">{source.kind === 'parent' ? 'Revises ' : 'Read '}</span>
    {source.deleted || !source.source_document_id
      ? <span>{`${source.source_title} v${source.source_version ?? '?'} (deleted)`}</span>
      : <><Link className="underline" to={`/documents/${encodeURIComponent(source.source_document_id)}`}>{source.source_title}</Link>{` v${source.source_version ?? '?'}`}</>}
  </li>)}</ul>;
}

/** One draft: who wrote it and when, the diff against the live version (a draft of a document) and the preview, its
 * sources, and Edit, Promote (a stale draft asks again: Promote anyway) and Discard while it is open. */
export default function DraftView({ draftId, reloadKey = 0, onEdit, onPromoted, onDiscarded, onLoaded }: {
  draftId: string; reloadKey?: number; onEdit?: (draft: DraftDetail) => void; onPromoted: (result: PromoteResult) => void;
  onDiscarded: () => void; onLoaded?: (draft: DraftDetail) => void;
}) {
  const [draft, setDraft] = useState<DraftDetail | null>(null);
  const [error, setError] = useState('');
  const [stale, setStale] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [view, setView] = useState('changes');
  const [load, setLoad] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getDraft(draftId, controller.signal).then((value) => { if (!controller.signal.aborted) { setDraft(value); onLoaded?.(value); } })
      .catch((e) => { if (!controller.signal.aborted) setError(failure(e, 'Could not load the draft')); });
    return () => controller.abort();
  }, [draftId, reloadKey, load]);

  async function promote(confirmStale: boolean) {
    if (!draft) return;
    setBusy(true); setError('');
    try { const result = await promoteDraft(draft.id, draft.revision, confirmStale); setStale(''); onPromoted(result); }
    catch (e) {
      if (e instanceof ApiError && e.code === STALE_BASE) { setStale(e.message); setView('changes'); }
      else { setStale(''); setError(failure(e, 'Could not promote the draft')); setLoad((n) => n + 1); }
    } finally { setBusy(false); }
  }
  async function discard() {
    if (!draft) return;
    setBusy(true); setError('');
    try { await discardDraft(draft.id, draft.revision); setConfirmDiscard(false); onDiscarded(); }
    catch (e) { setConfirmDiscard(false); setError(failure(e, 'Could not discard the draft')); setLoad((n) => n + 1); }
    finally { setBusy(false); }
  }

  if (!draft) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  const isNew = draft.document_id === null;
  const open = draft.status === 'open';
  return <section aria-label="Draft" className="flex min-w-0 flex-col gap-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0"><h2 className="break-words font-semibold">{draft.title}</h2>
        <p className="text-sm text-muted-foreground">Written by <DraftAuthor draft={draft} /> · updated {new Date(draft.updated_at).toLocaleString()}
          {draft.base_version ? ` · based on version ${draft.base_version}` : ''}
          {isNew ? ` · new document in ${draft.folder ? `${draft.folder}/` : 'the top folder'}` : ''}</p></div>
      <div className="flex flex-wrap items-center gap-2">
        {draft.stale && <Badge variant="destructive">stale</Badge>}
        {!open && <Badge variant="outline">{draft.status === 'promoted' ? 'Promoted' : 'Discarded'}</Badge>}
        {open && onEdit && <Button type="button" variant="outline" disabled={busy} onClick={() => onEdit(draft)}>Edit</Button>}
        {open && <Button type="button" variant="outline" disabled={busy} onClick={() => setConfirmDiscard(true)}>Discard</Button>}
        {open && <Button type="button" disabled={busy || stale !== ''} onClick={() => void promote(false)}>{busy && <Spinner data-icon="inline-start" />}Promote</Button>}
      </div>
    </div>
    {stale && <Alert variant="destructive"><AlertDescription className="flex flex-col gap-2"><span>{stale}</span>
      <span className="flex gap-2"><Button type="button" size="sm" variant="destructive" disabled={busy} onClick={() => void promote(true)}>Promote anyway</Button>
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setStale('')}>Cancel</Button></span></AlertDescription></Alert>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <Tabs value={isNew ? 'preview' : view} onValueChange={setView}>
      <TabsList>{!isNew && <TabsTrigger value="changes">Changes</TabsTrigger>}<TabsTrigger value="preview">Preview</TabsTrigger></TabsList>
      {!isNew && <TabsContent value="changes"><DiffView before={draft.live_markdown ?? ''} after={draft.markdown} /></TabsContent>}
      <TabsContent value="preview"><div className="rounded-lg border p-4"><Markdown content={draft.markdown} /></div></TabsContent>
    </Tabs>
    <section aria-label="Sources" className="flex flex-col gap-1"><h3 className="text-sm font-semibold">Sources</h3>
      <SourceList sources={draft.sources} empty="No recorded sources." /></section>
    <AlertDialog open={confirmDiscard} onOpenChange={(value) => { if (!busy) setConfirmDiscard(value); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>{`Discard “${draft.title}”?`}</AlertDialogTitle>
        <AlertDialogDescription>The draft is no longer listed (it stays in the audit history). The live document does not change.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
        <AlertDialogAction disabled={busy} onClick={(e) => { e.preventDefault(); void discard(); }}>Discard draft</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </section>;
}
```

- [ ] **Step 8: `components/DraftEditForm.tsx`**

```tsx
import { useState } from 'react';
import { updateDraft, type DraftDetail } from '~/lib/drafts';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Field, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Spinner } from '~/components/ui/spinner';
import { Textarea } from '~/components/ui/textarea';

/** Edit a new document's draft on its page: title, description and Markdown; Save draft keeps its other fields. */
export default function DraftEditForm({ draft, onCancel, onSaved }: { draft: DraftDetail; onCancel: () => void; onSaved: (draft: DraftDetail) => void }) {
  const [title, setTitle] = useState(draft.title);
  const [description, setDescription] = useState(draft.description);
  const [markdown, setMarkdown] = useState(draft.markdown);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function save() {
    setBusy(true); setError('');
    try { onSaved(await updateDraft(draft.id, draft.revision, { title, description, purpose: draft.purpose, audience: draft.audience, tags: draft.tags, markdown })); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not save the draft'); }
    finally { setBusy(false); }
  }
  return <section aria-label="Edit draft" className="flex flex-col gap-4">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <Field><FieldLabel htmlFor="draft-title">Title</FieldLabel><Input id="draft-title" maxLength={240} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
    <Field><FieldLabel htmlFor="draft-description">Short description</FieldLabel><Textarea id="draft-description" rows={2} maxLength={4000} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
    <Field><FieldLabel htmlFor="draft-markdown">Markdown</FieldLabel><Textarea id="draft-markdown" className="min-h-96 font-mono" maxLength={1000000} value={markdown} onChange={(e) => setMarkdown(e.target.value)} /></Field>
    <div className="flex justify-end gap-2"><Button type="button" variant="outline" disabled={busy} onClick={onCancel}>Cancel</Button>
      <Button type="button" disabled={busy || !title.trim() || !markdown.trim()} onClick={() => void save()}>{busy && <Spinner data-icon="inline-start" />}Save draft</Button></div>
  </section>;
}
```

- [ ] **Step 9: `components/DraftPageView.tsx` and the route**

```tsx
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { ArrowLeft } from 'lucide-react';
import { draftPath, type DraftDetail } from '~/lib/drafts';
import DraftEditForm from './DraftEditForm';
import DraftView from './DraftView';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';

/** /documents/drafts/:draftId — a new document's draft: preview, sources, Edit, Discard, and Promote, which creates
 * the document. A draft that now revises an existing document (its name was taken meanwhile) links to that document. */
export default function DraftPageView() {
  const { draftId } = useParams();
  const navigate = useNavigate();
  const [editing, setEditing] = useState<DraftDetail | null>(null);
  const [reload, setReload] = useState(0);
  const [notice, setNotice] = useState('');
  const [converted, setConverted] = useState<DraftDetail | null>(null);
  return <div className="flex flex-col gap-6">
    <Button asChild variant="ghost" className="self-start"><Link to="/documents"><ArrowLeft data-icon="inline-start" />Documents</Link></Button>
    <h1 className="text-2xl font-semibold">Draft of a new document</h1>
    {notice && <Alert role="status"><AlertDescription>{notice}</AlertDescription></Alert>}
    {converted && <Alert role="status"><AlertDescription>This draft now revises an existing document. <Link className="underline" to={draftPath(converted)}>Open it on its document</Link></AlertDescription></Alert>}
    {editing
      ? <DraftEditForm draft={editing} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); setNotice('Draft saved.'); setReload((n) => n + 1); }} />
      : <DraftView draftId={draftId!} reloadKey={reload} onEdit={(draft) => { setNotice(''); setEditing(draft); }}
          onLoaded={(draft) => setConverted(draft.document_id && draft.status === 'open' ? draft : null)}
          onPromoted={(result) => navigate(`/documents/${result.document_id}`)} onDiscarded={() => navigate('/documents')} />}
  </div>;
}
```

  `routes/documentDraft.tsx`:

```tsx
import AdminLayout from '../components/AdminLayout';
import DraftPageView from '../components/DraftPageView';

export default function Page() {
  return <AdminLayout><DraftPageView /></AdminLayout>;
}
```

  `routes.ts`: before `route("documents/:documentId", "./routes/documentDetail.tsx"),` add
  `route("documents/drafts/:draftId", "./routes/documentDraft.tsx"),`.

- [ ] **Step 10: `components/DocumentsView.tsx`** — replace the file:

```tsx
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { FileText, Plus, RefreshCw } from 'lucide-react';
import { getDocuments, getDocumentResources, type DocumentResources, type DocumentSummary } from '~/lib/documents';
import { discardDraft, getNewDrafts, type DraftSummary } from '~/lib/drafts';
import { DraftAuthor } from './DraftView';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle } from '~/components/ui/alert-dialog';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Checkbox } from '~/components/ui/checkbox';
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription } from '~/components/ui/empty';
import { Field, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Label } from '~/components/ui/label';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '~/components/ui/select';
import { Skeleton } from '~/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '~/components/ui/table';

const draftsLabel = (count: number) => count === 1 ? '1 draft' : `${count} drafts`;

export default function DocumentsView() {
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<DocumentSummary[]>([]);
  const [resources, setResources] = useState<DocumentResources | null>(null);
  const [newDrafts, setNewDrafts] = useState<DraftSummary[]>([]);
  const [discarding, setDiscarding] = useState<DraftSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const offset = Number(params.get('offset') || 0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    Promise.all([getDocuments(params, controller.signal), getDocumentResources(controller.signal), getNewDrafts(controller.signal)]).then(([data, assets, drafts]) => {
      if (!controller.signal.aborted) { setRows(data.documents); setTotal(data.total); setResources(assets); setNewDrafts(drafts.drafts); }
    }).catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load documents'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [params, refresh]);
  function filter(key: string, value: string) {
    setParams((old) => { const next = new URLSearchParams(old); value && value !== 'all' ? next.set(key, value) : next.delete(key); if (key !== 'offset') next.delete('offset'); return next; }, { replace: true });
  }
  async function discard() {
    if (!discarding) return;
    setBusy(true); setError('');
    try { await discardDraft(discarding.id, discarding.revision); setDiscarding(null); setRefresh((n) => n + 1); }
    catch (e) { setDiscarding(null); setError(e instanceof Error ? e.message : 'Could not discard the draft'); }
    finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm text-muted-foreground">{total} documents</p><div className="flex gap-2">
      <Button variant="outline" disabled={loading} onClick={() => setRefresh((n) => n + 1)}><RefreshCw data-icon="inline-start" />Refresh</Button>
      <Button asChild variant="outline"><Link to="/documentation/generations">Generate documents</Link></Button>
      <Button asChild><Link to="/documents/new"><Plus data-icon="inline-start" />Create document</Link></Button>
    </div></div>
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <Field><FieldLabel htmlFor="document-query">Search documents</FieldLabel><Input id="document-query" placeholder="Title, slug or description" value={params.get('q') || ''} onChange={(e) => filter('q', e.target.value)} /></Field>
      {(['code_projects', 'services', 'servers', 'environments'] as const).map((kind) => {
        const key = { code_projects: 'code_project_id', services: 'service_id', servers: 'server_id', environments: 'environment_id' }[kind];
        const label = { code_projects: 'Code project', services: 'Service', servers: 'Server', environments: 'Environment' }[kind];
        return <Field key={kind}><FieldLabel htmlFor={`filter-${kind}`}>{label}</FieldLabel>
          <Select value={params.get(key) || 'all'} onValueChange={(v) => filter(key, v)}><SelectTrigger id={`filter-${kind}`}><SelectValue /></SelectTrigger><SelectContent><SelectGroup>
            <SelectItem value="all">All {label.toLowerCase()}s</SelectItem>{resources?.[kind]?.map((item) => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}
          </SelectGroup></SelectContent></Select></Field>;
      })}
      <div className="flex items-center gap-2 self-end pb-2"><Checkbox id="filter-has-drafts" checked={params.get('has_drafts') === 'true'}
        onCheckedChange={(on) => filter('has_drafts', on === true ? 'true' : '')} /><Label htmlFor="filter-has-drafts">Has drafts</Label></div>
    </div>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {newDrafts.length > 0 && <section aria-label="New drafts" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">New drafts</h2>
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Title</TableHead><TableHead>Folder</TableHead><TableHead>Written by</TableHead><TableHead>Updated</TableHead><TableHead /></TableRow></TableHeader><TableBody>
        {newDrafts.map((draft) => <TableRow key={draft.id}>
          <TableCell><Link className="font-medium underline-offset-4 hover:underline" to={`/documents/drafts/${draft.id}`}>{draft.title}</Link></TableCell>
          <TableCell>{draft.folder ?? '—'}</TableCell><TableCell><DraftAuthor draft={draft} /></TableCell>
          <TableCell>{new Date(draft.updated_at).toLocaleString()}</TableCell>
          <TableCell><div className="flex justify-end gap-2">
            <Button asChild size="sm" variant="outline"><Link to={`/documents/drafts/${draft.id}`} aria-label={`Open ${draft.title}`}>Open</Link></Button>
            <Button size="sm" variant="outline" aria-label={`Discard ${draft.title}`} onClick={() => setDiscarding(draft)}>Discard</Button></div></TableCell>
        </TableRow>)}
      </TableBody></Table></div>
    </section>}
    {loading ? <Skeleton className="h-48 w-full" /> : !error && (rows.length === 0 ? <Empty className="border"><EmptyHeader><EmptyMedia variant="icon"><FileText /></EmptyMedia>
      <EmptyTitle>{total === 0 && params.size === 0 ? 'No documents yet' : 'No matching documents'}</EmptyTitle><EmptyDescription>Create a Markdown document and connect it to the projects, services, and servers it describes.</EmptyDescription></EmptyHeader></Empty> : <>
      <div className="rounded-lg border"><Table><TableHeader><TableRow><TableHead>Document</TableHead><TableHead>Purpose / audience</TableHead><TableHead>Version</TableHead><TableHead>Drafts</TableHead><TableHead>Updated</TableHead></TableRow></TableHeader><TableBody>
        {rows.map((doc) => <TableRow key={doc.id}><TableCell className="max-w-md whitespace-normal"><Link className="font-medium underline-offset-4 hover:underline" to={`/documents/${doc.id}`}>{doc.title}</Link><p className="mt-1 text-sm text-muted-foreground">{doc.description}</p>
          <p className="mt-1 text-xs text-muted-foreground">{doc.code_project_ids.length} code projects · {doc.service_ids.length} services · {doc.server_ids.length} servers</p></TableCell>
          <TableCell><p>{doc.purpose}</p><span className="text-sm text-muted-foreground">{doc.audience === 'both' ? 'People and agents' : doc.audience === 'people' ? 'People' : 'Agents'}</span></TableCell>
          <TableCell><Badge variant="secondary">v{doc.version}</Badge></TableCell>
          <TableCell>{(doc.draft_count ?? 0) > 0 ? <Link className="underline-offset-4 hover:underline" to={`/documents/${doc.id}?tab=drafts`}>{draftsLabel(doc.draft_count!)}</Link> : <span className="text-muted-foreground">—</span>}</TableCell>
          <TableCell>{new Date(doc.updated_at).toLocaleString()}</TableCell></TableRow>)}
      </TableBody></Table></div>
    </>)}
    {total > 50 && <div className="flex items-center justify-between gap-3"><span className="text-sm text-muted-foreground">{offset + 1}–{Math.min(offset + 50, total)} of {total}</span><div className="flex gap-2">
      <Button variant="outline" disabled={loading || offset === 0} onClick={() => filter('offset', String(Math.max(0, offset - 50)))}>Previous</Button><Button variant="outline" disabled={loading || offset + 50 >= total} onClick={() => filter('offset', String(offset + 50))}>Next</Button>
    </div></div>}
    <AlertDialog open={discarding !== null} onOpenChange={(open) => { if (!open && !busy) setDiscarding(null); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>{`Discard “${discarding?.title ?? ''}”?`}</AlertDialogTitle>
        <AlertDialogDescription>The draft is no longer listed (it stays in the audit history). No document is created.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
        <AlertDialogAction disabled={busy} onClick={(e) => { e.preventDefault(); void discard(); }}>Discard draft</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </div>;
}
```

- [ ] **Step 11: `__tests__/documents.test.tsx`** — `DocumentsView` now also loads `/api/documentation/drafts?new=true`.
  In `setup`'s fetch mock, before the `if (path.includes('/documents?'))` line, add
  `if (path.includes('/drafts')) return response({ drafts: [] });`.

- [ ] **Step 12: Run tests**

Run: `cd services/admin-ui && npx vitest run app/lib/__tests__/lineDiff.test.ts app/components/__tests__/documentDrafts.test.tsx app/components/__tests__/documents.test.tsx app/components/__tests__/promptLibrary.test.tsx && npx tsc --noEmit && npx react-router routes >/dev/null`
Expected: PASS (`promptLibrary.test.tsx` holds the route-id uniqueness test).

- [ ] **Step 13: Commit**

```bash
git add services/admin-ui/app/lib/api.ts services/admin-ui/app/lib/documents.ts services/admin-ui/app/lib/drafts.ts \
  services/admin-ui/app/lib/lineDiff.ts services/admin-ui/app/lib/__tests__/lineDiff.test.ts \
  services/admin-ui/app/components/DiffView.tsx services/admin-ui/app/components/DraftView.tsx \
  services/admin-ui/app/components/DraftEditForm.tsx services/admin-ui/app/components/DraftPageView.tsx \
  services/admin-ui/app/components/DocumentsView.tsx services/admin-ui/app/routes/documentDraft.tsx services/admin-ui/app/routes.ts \
  services/admin-ui/app/components/__tests__/documentDrafts.test.tsx services/admin-ui/app/components/__tests__/documents.test.tsx
git commit -m "feat(admin-ui): drafts on the documents list, the draft view with a diff, and the new-document draft page

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: UI — the document page: Save draft, Drafts tab, Connections tab, "from draft"

**Files:**
- Create: `services/admin-ui/app/components/DocumentDrafts.tsx`, `services/admin-ui/app/components/DocumentConnections.tsx`
- Modify: `services/admin-ui/app/components/DocumentEditorView.tsx`
- Test: `services/admin-ui/app/components/__tests__/documentPageDrafts.test.tsx` (new)

**Interfaces:**
- Consumes (Task 5): `createDraft`, `updateDraft`, `getDocumentDrafts`, `getConnections`, `addLink`, `removeLink`,
  `draftPath`, `PromoteResult`, `DraftDetail`, `DraftSummary`, `Connections`; `DraftView` (+ `SourceList`);
  `getDocuments` from `lib/documents.ts`.
- Produces: `DocumentDrafts({ documentId, selected, onEdit, onPromoted })`; `DocumentConnections({ documentId })` (a
  `<section aria-label="Connections">`); the document page reads `?tab=` (`write|preview|resources|history|drafts|
  connections`) and `?draft=<id>` (the draft selected on the Drafts tab).

- [ ] **Step 1: Write the failing test** — `app/components/__tests__/documentPageDrafts.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import DocumentEditorView from '../DocumentEditorView';

vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <article>{content}</article> }));
const NativeRequest = globalThis.Request;
beforeEach(() => vi.stubGlobal('Request', class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
}));
afterEach(() => vi.unstubAllGlobals());

const at = '2026-10-12T10:00:00Z';
const resources = { code_projects: [], services: [], servers: [], deployments: [], environments: [] };
const version = { id: 'v2', version: 2, title: 'Runbook', description: 'How to run it', purpose: 'runbook', audience: 'both', tags: [],
  markdown: '# Runbook v2 live', content_hash: 'a'.repeat(64), source_snapshot: resources, origin: 'manual', base_version_id: 'v1', created_at: at };
const document = { id: 'doc-1', slug: 'runbook', title: 'Runbook', description: 'How to run it', purpose: 'runbook', audience: 'both', tags: [],
  code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [], environment_ids: [], revision: 2, version: 2,
  current_version_id: 'v2', published_version_id: null, content_hash: 'a', created_at: at, updated_at: at, current_version: version,
  versions: [{ ...version, from_draft: true }, { ...version, id: 'v1', version: 1, from_draft: false }] };
const summary = { id: 'd1', document_id: 'doc-1', slug: null, folder: null, title: 'Runbook', origin: 'agent', session_id: 's1',
  session_title: 'Runbook', author: null, edited: false, status: 'open', revision: 1, base_version_id: 'v1', base_version: 1,
  live_version: 2, stale: true, created_at: at, updated_at: at };
const detail = { ...summary, description: 'How to run it', purpose: 'runbook', audience: 'both', tags: [], markdown: '# Runbook draft text',
  resource_ids: {}, live_markdown: '# Runbook v2 live', document_title: 'Runbook', promoted_version_id: null, decided_at: null,
  sources: [{ kind: 'parent', source_document_id: 'doc-1', source_version_id: 'v1', source_title: 'Runbook', source_version: 1, deleted: false }] };
const STALE = "This draft was based on version 1; the live version is now 2. Promoting replaces version 2's text.";
const base: Record<string, (body: any) => [number, unknown]> = {
  'GET /api/documentation/documents/doc-1': () => [200, document],
  'GET /api/documentation/resources': () => [200, resources],
  'GET /api/documentation/documents/doc-1/drafts': () => [200, { drafts: [summary] }],
  'GET /api/documentation/drafts/d1': () => [200, detail],
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
function show(url: string) {
  const router = createMemoryRouter([{ path: '/documents/new', element: <DocumentEditorView /> },
    { path: '/documents/drafts/:draftId', element: <p>Draft page</p> }, { path: '/documents/:documentId', element: <DocumentEditorView /> }],
    { initialEntries: [url] });
  render(<RouterProvider router={router} />);
  return router;
}

test('Save draft saves the editor text as a draft of the document and opens it on the Drafts tab', async () => {
  const calls = api({ ...base, 'POST /api/documentation/drafts': () => [201, { ...detail, stale: false }] });
  show('/documents/doc-1');
  fireEvent.change(await screen.findByLabelText('Markdown'), { target: { value: '# Runbook v3 draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(calls.find((c) => c.key === 'POST /api/documentation/drafts')?.body).toEqual({
    document_id: 'doc-1', title: 'Runbook', description: 'How to run it', purpose: 'runbook', audience: 'both', tags: [], markdown: '# Runbook v3 draft' }));
  expect(await screen.findByText('Draft saved.')).toBeInTheDocument();
  expect(await screen.findByRole('tab', { name: 'Drafts', selected: true })).toBeInTheDocument();
  expect(await screen.findByRole('region', { name: 'Draft' })).toBeInTheDocument();
});

test('Save draft on a new document creates a new-document draft and opens its page', async () => {
  const calls = api({ 'GET /api/documentation/resources': () => [200, resources],
    'POST /api/documentation/drafts': () => [201, { ...detail, id: 'd5', document_id: null, slug: 'new-runbook' }] });
  const router = show('/documents/new');
  fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'New runbook' } });
  fireEvent.change(screen.getByLabelText('Markdown'), { target: { value: '# How to' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(router.state.location.pathname).toBe('/documents/drafts/d5'));
  expect(calls.find((c) => c.key === 'POST /api/documentation/drafts')!.body).toEqual({ slug: 'new-runbook', title: 'New runbook',
    description: '', purpose: 'explanation', audience: 'both', tags: [], markdown: '# How to',
    resource_ids: { code_project_ids: [], service_ids: [], server_ids: [], deployment_ids: [], environment_ids: [] } });
});

test('a stale draft shows the diff against the live version and promotes only on Promote anyway', async () => {
  const promoted = { draft: { ...detail, status: 'promoted' }, document_id: 'doc-1', version_id: 'v3', revision: 3, export_error: null };
  const calls = api({ ...base, 'POST /api/documentation/drafts/d1/promote': (body) => body.confirm_stale
    ? [200, promoted] : [409, { error: { message: STALE, code: 'STALE_BASE' } }] });
  show('/documents/doc-1?tab=drafts&draft=d1');
  const list = await screen.findByRole('list', { name: 'Drafts' });
  expect(within(list).getByText('stale')).toBeInTheDocument();
  expect(within(list).getByText(/Agent run/)).toBeInTheDocument();
  const view = within(await screen.findByRole('region', { name: 'Draft' }));
  expect(view.getByText('# Runbook draft text', { selector: 'ins' })).toBeInTheDocument();
  expect(view.getByText('# Runbook v2 live', { selector: 'del' })).toBeInTheDocument();
  expect(view.getByRole('link', { name: 'Agent run' })).toHaveAttribute('href', '/documentation/generations/s1');
  fireEvent.click(view.getByRole('button', { name: 'Promote' }));
  expect(await view.findByText(STALE)).toBeInTheDocument();
  fireEvent.click(view.getByRole('button', { name: 'Promote anyway' }));
  await waitFor(() => expect(calls.filter((c) => c.key === 'POST /api/documentation/drafts/d1/promote').map((c) => c.body))
    .toEqual([{ revision: 1, confirm_stale: false }, { revision: 1, confirm_stale: true }]));
  expect(await screen.findByText('Draft promoted to version 3.')).toBeInTheDocument();
});

test('Edit loads a draft into the editor and Save draft then updates that draft', async () => {
  const calls = api({ ...base, 'PUT /api/documentation/drafts/d1': () => [200, { ...detail, revision: 2, edited: true }] });
  show('/documents/doc-1?tab=drafts&draft=d1');
  const view = within(await screen.findByRole('region', { name: 'Draft' }));
  fireEvent.click(view.getByRole('button', { name: 'Edit' }));
  expect(await screen.findByLabelText('Markdown')).toHaveValue('# Runbook draft text');
  expect(screen.getByText(/Editing the draft “Runbook”/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Markdown'), { target: { value: '# Runbook draft text, edited' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(calls.find((c) => c.key === 'PUT /api/documentation/drafts/d1')?.body)
    .toMatchObject({ revision: 1, markdown: '# Runbook draft text, edited' }));
  expect(calls.some((c) => c.key === 'POST /api/documentation/drafts')).toBe(false);
});

test('Connections adds and removes dependencies and shows what uses the document and what built it', async () => {
  const overview = { id: 'doc-2', title: 'Overview', slug: 'overview', folder: null, version: 1 };
  const connections = { depends_on: [], used_by: [{ id: 'doc-7', title: 'Billing guide', slug: 'billing-guide', folder: null, version: 3 }],
    built_from: [{ kind: 'read', source_document_id: null, source_version_id: null, source_title: 'Old notes', source_version: 2, deleted: true }] };
  const calls = api({ ...base,
    'GET /api/documentation/documents/doc-1/connections': () => [200, connections],
    'GET /api/documentation/documents?q=over': () => [200, { documents: [{ ...document, id: 'doc-2', title: 'Overview' }, document], total: 2 }],
    'POST /api/documentation/documents/doc-1/links': () => [201, { ...connections, depends_on: [overview] }],
    'DELETE /api/documentation/documents/doc-1/links/doc-2': () => [200, connections] });
  show('/documents/doc-1?tab=connections');
  const panel = within(await screen.findByRole('region', { name: 'Connections' }));
  expect(await panel.findByRole('link', { name: 'Billing guide' })).toHaveAttribute('href', '/documents/doc-7');
  expect(panel.getByText('Old notes v2 (deleted)')).toBeInTheDocument();
  fireEvent.change(panel.getByLabelText('Find a document'), { target: { value: 'over' } });
  const add = await panel.findByRole('button', { name: 'Add Overview' });
  expect(panel.queryByRole('button', { name: 'Add Runbook' })).toBeNull();  // never itself
  fireEvent.click(add);
  await waitFor(() => expect(calls.find((c) => c.key === 'POST /api/documentation/documents/doc-1/links')?.body).toEqual({ to_document_id: 'doc-2' }));
  fireEvent.click(await panel.findByRole('button', { name: 'Remove Overview' }));
  await waitFor(() => expect(calls.some((c) => c.key === 'DELETE /api/documentation/documents/doc-1/links/doc-2')).toBe(true));
});

test('version history notes the versions promoted from a draft', async () => {
  api(base);
  show('/documents/doc-1?tab=history');
  expect(await screen.findByRole('button', { name: /Version 2 · Current · from draft/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /^Version 1/ })).not.toHaveTextContent('from draft');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/documentPageDrafts.test.tsx`
Expected: FAIL (no "Save draft" button, no Drafts or Connections tab).

- [ ] **Step 3: `components/DocumentDrafts.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { getDocumentDrafts, type DraftDetail, type DraftSummary, type PromoteResult } from '~/lib/drafts';
import DraftView from './DraftView';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Skeleton } from '~/components/ui/skeleton';

/** The document page's Drafts tab: its open drafts (who wrote each, when, the version it is based on, "stale" when the
 * live version moved on) and the selected one in the draft view. */
export default function DocumentDrafts({ documentId, selected, onEdit, onPromoted }: {
  documentId: string; selected: string | null; onEdit: (draft: DraftDetail) => void; onPromoted: (result: PromoteResult) => void;
}) {
  const [drafts, setDrafts] = useState<DraftSummary[] | null>(null);
  const [open, setOpen] = useState<string | null>(selected);
  const [error, setError] = useState('');
  const [load, setLoad] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getDocumentDrafts(documentId, controller.signal).then((value) => { if (!controller.signal.aborted) setDrafts(value.drafts); })
      .catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load the drafts'); });
    return () => controller.abort();
  }, [documentId, load]);
  if (!drafts) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  if (drafts.length === 0) return <p className="text-sm text-muted-foreground">No open drafts. Save draft keeps the editor's text as a draft; Generate documents runs write drafts too.</p>;
  const shown = drafts.some((draft) => draft.id === open) ? open : null;
  return <div className="grid gap-6 md:grid-cols-[260px_minmax(0,1fr)]">
    <ul aria-label="Drafts" className="flex flex-col gap-2">{drafts.map((draft) => <li key={draft.id}>
      <Button type="button" variant={shown === draft.id ? 'secondary' : 'outline'} className="h-auto w-full justify-start whitespace-normal py-3 text-left" onClick={() => setOpen(draft.id)}>
        <span className="flex flex-col gap-1"><span>{draft.title}{draft.stale && <Badge variant="destructive" className="ml-2">stale</Badge>}</span>
          <span className="text-xs font-normal text-muted-foreground">{draft.origin === 'agent' ? 'Agent run' : draft.author || 'Someone'}
            {` · ${new Date(draft.updated_at).toLocaleString()} · based on version ${draft.base_version ?? '?'}`}</span></span>
      </Button></li>)}</ul>
    <div className="min-w-0">{shown
      ? <DraftView key={shown} draftId={shown} onEdit={onEdit} onPromoted={onPromoted} onDiscarded={() => { setOpen(null); setLoad((n) => n + 1); }} />
      : <p className="text-sm text-muted-foreground">Select a draft to compare it with the live version.</p>}</div>
  </div>;
}
```

- [ ] **Step 4: `components/DocumentConnections.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { getDocuments, type DocumentSummary } from '~/lib/documents';
import { addLink, getConnections, removeLink, type Connections } from '~/lib/drafts';
import { SourceList } from './DraftView';
import { Alert, AlertDescription } from '~/components/ui/alert';
import { Button } from '~/components/ui/button';
import { Field, FieldLabel } from '~/components/ui/field';
import { Input } from '~/components/ui/input';
import { Skeleton } from '~/components/ui/skeleton';

/** The document page's Connections tab: Depends on (added with a document picker, removable), Used by, and what the
 * live version was built from. */
export default function DocumentConnections({ documentId }: { documentId: string }) {
  const [connections, setConnections] = useState<Connections | null>(null);
  const [query, setQuery] = useState('');
  const [found, setFound] = useState<DocumentSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    getConnections(documentId, controller.signal).then((value) => { if (!controller.signal.aborted) setConnections(value); })
      .catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load the connections'); });
    return () => controller.abort();
  }, [documentId]);
  useEffect(() => {
    const q = query.trim();
    if (!q) { setFound([]); return; }
    const controller = new AbortController();
    getDocuments(new URLSearchParams({ q }), controller.signal).then((value) => { if (!controller.signal.aborted) setFound(value.documents); })
      .catch(() => { if (!controller.signal.aborted) setFound([]); });
    return () => controller.abort();
  }, [query]);
  async function change(action: () => Promise<Connections>) {
    setBusy(true); setError('');
    try { setConnections(await action()); } catch (e) { setError(e instanceof Error ? e.message : 'Could not change the connection'); }
    finally { setBusy(false); }
  }
  if (!connections) return error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-48 w-full" />;
  const linked = new Set(connections.depends_on.map((item) => item.id));
  const offered = found.filter((item) => item.id !== documentId && !linked.has(item.id));
  return <section aria-label="Connections" className="flex flex-col gap-6">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <section aria-label="Depends on" className="flex flex-col gap-2"><h3 className="font-medium">Depends on</h3>
      {connections.depends_on.length === 0 ? <p className="text-sm text-muted-foreground">This document depends on no other document.</p> :
        <ul className="flex flex-col gap-1">{connections.depends_on.map((item) => <li key={item.id} className="flex items-center justify-between gap-2 text-sm">
          <Link className="underline" to={`/documents/${item.id}`}>{item.title}</Link>
          <Button type="button" size="sm" variant="outline" disabled={busy} aria-label={`Remove ${item.title}`}
            onClick={() => void change(() => removeLink(documentId, item.id))}>Remove</Button></li>)}</ul>}
      <Field><FieldLabel htmlFor="connection-search">Find a document</FieldLabel>
        <Input id="connection-search" placeholder="Title, slug or description" value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') e.preventDefault(); /* the page's form must not submit */ }} /></Field>
      {offered.length > 0 && <ul aria-label="Documents to add" className="flex flex-col gap-1">{offered.map((item) => <li key={item.id} className="flex items-center justify-between gap-2 text-sm">
        <span>{item.title}</span>
        <Button type="button" size="sm" disabled={busy} aria-label={`Add ${item.title}`}
          onClick={() => void change(async () => { const next = await addLink(documentId, item.id); setQuery(''); return next; })}>Add</Button></li>)}</ul>}
    </section>
    <section aria-label="Used by" className="flex flex-col gap-2"><h3 className="font-medium">Used by</h3>
      {connections.used_by.length === 0 ? <p className="text-sm text-muted-foreground">No document depends on this one.</p> :
        <ul className="flex flex-col gap-1 text-sm">{connections.used_by.map((item) => <li key={item.id}><Link className="underline" to={`/documents/${item.id}`}>{item.title}</Link></li>)}</ul>}
    </section>
    <section aria-label="Built from" className="flex flex-col gap-2"><h3 className="font-medium">Built from</h3>
      <SourceList sources={connections.built_from} empty="The live version records no sources." /></section>
  </section>;
}
```

- [ ] **Step 5: `components/DocumentEditorView.tsx`** — edits, in order:

  1. Imports: `import { Link, useNavigate, useParams } from 'react-router';` becomes
     `import { Link, useNavigate, useParams, useSearchParams } from 'react-router';`; add

```tsx
import { createDraft, draftPath, updateDraft, type PromoteResult } from '~/lib/drafts';
import DocumentConnections from './DocumentConnections';
import DocumentDrafts from './DocumentDrafts';

const TABS = ['write', 'preview', 'resources', 'history', 'drafts', 'connections'];
```

  2. `Editor`'s signature becomes:

```tsx
function Editor({ document, resources, onSaved, onDraftSaved, onPromoted }: { document: DocumentDetail | null; resources: DocumentResources;
  onSaved: (id: string) => void; onDraftSaved: (draftId: string) => void; onPromoted: (result: PromoteResult) => void }) {
```

  3. Replace `const [tab, setTab] = useState('write');` with:

```tsx
  const [params] = useSearchParams();
  const [tab, setTab] = useState(() => { const wanted = params.get('tab') ?? ''; return document && TABS.includes(wanted) ? wanted : 'write'; });
  // A draft loaded with Edit: Save draft updates it instead of starting a new one.
  const [editingDraft, setEditingDraft] = useState<{ id: string; revision: number; title: string } | null>(null);
```

  4. After the `save` function add:

```tsx
  async function saveDraft() {
    setPending(true); setError('');
    const content = { title: value.title, description: value.description, purpose: value.purpose, audience: value.audience,
      tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean), markdown: value.markdown };
    try {
      if (editingDraft) { const saved = await updateDraft(editingDraft.id, editingDraft.revision, content); leaving.current = true; onDraftSaved(saved.id); }
      else if (document) { const saved = await createDraft({ document_id: document.id, ...content }); leaving.current = true; onDraftSaved(saved.id); }
      else {
        const saved = await createDraft({ slug: value.slug, ...content, resource_ids: { code_project_ids: value.code_project_ids,
          service_ids: value.service_ids, server_ids: value.server_ids, deployment_ids: value.deployment_ids, environment_ids: value.environment_ids ?? [] } });
        leaving.current = true; navigate(draftPath(saved));
      }
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not save the draft'); }
    finally { setPending(false); }
  }
```

  5. Just before the submit button (`<Button type="submit" ...>{document ? 'Save new version' : 'Create document'}</Button>`) add:

```tsx
      <Button type="button" variant="outline" disabled={pending || !value.markdown.trim() || !value.title.trim()} onClick={() => void saveDraft()}>Save draft</Button>
```

  6. After `{notice && <Alert role="status">...</Alert>}` add:

```tsx
    {editingDraft && <Alert role="status"><AlertDescription>{`Editing the draft “${editingDraft.title}”. Save draft updates it; Save new version makes this text the live version and leaves the draft as it is.`}</AlertDescription></Alert>}
```

  7. In the `TabsList`, after the Version history trigger add
     `<TabsTrigger value="drafts" disabled={!document}>Drafts</TabsTrigger><TabsTrigger value="connections" disabled={!document}>Connections</TabsTrigger>`.

  8. In the history list, `Version {version.version}{version.id === document.current_version_id ? ' · Current' : ''}` becomes
     `Version {version.version}{version.id === document.current_version_id ? ' · Current' : ''}{version.from_draft ? ' · from draft' : ''}`.

  9. Before `</Tabs>` add:

```tsx
        <TabsContent value="drafts">{document && <DocumentDrafts documentId={document.id} selected={params.get('draft')} onPromoted={onPromoted}
          onEdit={(draft) => {
            setValue((old) => ({ ...old, title: draft.title, description: draft.description, purpose: draft.purpose, audience: draft.audience, tags: draft.tags, markdown: draft.markdown }));
            setTags(draft.tags.join(', ')); setEditingDraft({ id: draft.id, revision: draft.revision, title: draft.title }); setTab('write');
            setNotice(`Draft “${draft.title}” loaded into the editor.`);
          }} />}</TabsContent>
        <TabsContent value="connections">{document && <DocumentConnections documentId={document.id} />}</TabsContent>
```

  10. In `DocumentEditorView` (the page component) add `const [, setParams] = useSearchParams();` after
      `const navigate = useNavigate();` and pass to `Editor`:

```tsx
      onDraftSaved={(draftId) => { setNotice('Draft saved.'); setParams({ tab: 'drafts', draft: draftId }, { replace: true }); setRefresh((n) => n + 1); }}
      onPromoted={(result) => { setNotice(`Draft promoted to version ${result.revision}.${result.export_error ? ` ${result.export_error}` : ''}`);
        setParams({ tab: 'drafts' }, { replace: true }); setRefresh((n) => n + 1); }}
```

  (The editor remounts on `refresh` with the live text and opens the tab and draft from the URL.)

- [ ] **Step 6: Run tests**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/documentPageDrafts.test.tsx app/components/__tests__/documents.test.tsx app/components/__tests__/documentDrafts.test.tsx && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/admin-ui/app/components/DocumentDrafts.tsx services/admin-ui/app/components/DocumentConnections.tsx \
  services/admin-ui/app/components/DocumentEditorView.tsx services/admin-ui/app/components/__tests__/documentPageDrafts.test.tsx
git commit -m "feat(admin-ui): document page — Save draft, Drafts tab with diff and stale promote, Connections tab

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Run page Open draft / Save as draft, discovery confirmation, user guide

**Files:**
- Modify: `services/admin-ui/app/lib/generations.ts`, `services/admin-ui/app/components/GenerationView.tsx`,
  `services/admin-ui/app/components/ProposalSheet.tsx`, `docs/user-guide.md`
- Test: `services/admin-ui/app/components/__tests__/generation.test.tsx`,
  `services/admin-ui/app/components/__tests__/discoveryRun.test.tsx`

**Interfaces:**
- Consumes: Task 4's `document_draft` on the session detail and live state, `POST
  /api/generations/sessions/{id}/document` → `{draft: {id, document_id}}`; Task 5's `draftPath`.
- Produces: `SessionDetail.document_draft: { id: string; document_id: string | null } | null` (also in
  `SessionState`); `saveSessionDraft(id) => Promise<{ draft: { id: string; document_id: string | null } }>` (replaces
  `saveSessionDocument` and the `SavedDocument` type, both removed).

- [ ] **Step 1: Update the failing tests**

  `__tests__/generation.test.tsx`:
  - remove `import { SAME_DOCUMENT_NAME } from '~/lib/documents';` and add `document_draft: null` to `state`;
  - in "the session page follows the turn, sends messages, stops, opens and saves the draft and closes": the mock's
    `/document` branch becomes
    `if (url.endsWith('/document')) return Response.json({ draft: { id: 'd1', document_id: 'doc' } }, { status: 201 });`;
    `expect(draft.getByRole('button', { name: 'Save document' })).toBeDisabled();` becomes
    `expect(draft.queryByRole('button', { name: 'Save as draft' })).toBeNull();`;
    both `{ name: 'Open draft' }` button lookups become `{ name: 'Preview draft' }`; and the lines from
    `fireEvent.click(draft.getByRole('button', { name: 'Save document' }));` through the `expect(posts.filter(...))`
    line become:

```tsx
  fireEvent.click(draft.getByRole('button', { name: 'Save as draft' }));
  expect(await draft.findByRole('link', { name: 'Open draft' })).toHaveAttribute('href', '/documents/doc?tab=drafts&draft=d1');
  expect(draft.queryByRole('button', { name: 'Save as draft' })).toBeNull();
  expect(posts.filter(p => p.url.endsWith('/document')).map(p => p.body)).toEqual([{}]);
```

  - add:

```tsx
test("a run whose turn wrote a document draft links to it; a new document's draft opens its own page", async () => {
  const withDraft = { ...detail, status: 'waiting', turns: 1, draft: '# Payments API', draft_turn: 1, document_draft: { id: 'd2', document_id: null } };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/events/history') ? page([]) : Response.json(withDraft)));
  render(<MemoryRouter><GenerationView sessionId="s1" /></MemoryRouter>);
  const draft = within(await screen.findByRole('region', { name: 'Draft' }));
  expect(await draft.findByRole('link', { name: 'Open draft' })).toHaveAttribute('href', '/documents/drafts/d2');
  expect(draft.queryByRole('button', { name: 'Save as draft' })).toBeNull();
  expect(draft.getByRole('button', { name: 'Preview draft' })).toBeEnabled();
});
```

  `__tests__/discoveryRun.test.tsx` — add:

```tsx
test("accepting a new version of a document asks first", async () => {
  const user = userEvent.setup();
  const version = { ...document, id: "q3", number: 3, document_id: "doc1", current: { id: "doc1", title: "web-1 overview", version: 3 } };
  const calls = api({ "GET /api/discovery/r1/proposals": () => [200, { proposals: [version] }],
    "POST /api/discovery/proposals/q3/accept": () => [200, { proposal: { ...version, status: "accepted", result_id: "doc1" }, result_id: "doc1", export_error: null }] });
  show();
  await user.click(await screen.findByRole("button", { name: "Review" }));
  await user.click(screen.getByRole("button", { name: "Accept" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("This replaces the live version 3 of “web-1 overview”. Continue?");
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(calls.some(c => c.key === "POST /api/discovery/proposals/q3/accept")).toBe(false);
  await user.click(screen.getByRole("button", { name: "Accept" }));
  await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Continue" }));
  await waitFor(() => expect(calls.find(c => c.key === "POST /api/discovery/proposals/q3/accept")!.body).toEqual({ revision: 1 }));
});
```

  (`document`, `api` and `show` are the file's existing fixtures; a new document — `document_id: null` — is accepted
  without asking, as the existing "a document shows its Markdown" tests already exercise.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/generation.test.tsx app/components/__tests__/discoveryRun.test.tsx`
Expected: FAIL (no "Save as draft" / "Preview draft"; no confirmation dialog).

- [ ] **Step 3: `lib/generations.ts`**
  - in `SessionDetail` add `/** The run's latest open document draft (Generate documents). */ document_draft?: { id: string; document_id: string | null } | null;`
  - add `'document_draft'` to the `SessionState` `Pick` list;
  - delete `SavedDocument` and `saveSessionDocument`; add:

```ts
/** Save as draft (runs from before document drafts): the run's draft becomes a document draft; nothing goes live. */
export const saveSessionDraft = (id: string) =>
  request<{ draft: { id: string; document_id: string | null } }>(`${session(id)}/document`, json('POST', {}));
```

- [ ] **Step 4: `components/GenerationView.tsx`**
  - imports: drop `ApiError`, `SAME_DOCUMENT_NAME`, `saveSessionDocument`, `SavedDocument` and the `AlertDialog…`
    import; import `saveSessionDraft` from `~/lib/generations` and `draftPath` from `~/lib/drafts`;
  - delete the `confirming` and `saved` state, the `save(updateExisting)` function and the `<AlertDialog>` at the end;
  - add, next to `stop` / `close`:

```tsx
  const saveDraft = () => act(async () => {
    const result = await saveSessionDraft(sessionId);
    setSession(current => current && { ...current, document_draft: result.draft });
  });
```

  - the sidebar becomes:

```tsx
      sidebar={<section aria-label="Draft" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">{draftTitle}</h2>
        {!session.draft && <p className="text-xs text-muted-foreground">The draft appears when the agent finishes a turn.</p>}
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" disabled={!session.draft} onClick={() => setDraftOpen(true)}>Preview draft</Button>
          {session.document_draft
            ? <Button asChild size="sm"><Link to={draftPath(session.document_draft)}>Open draft</Link></Button>
            : session.draft ? <Button type="button" size="sm" disabled={busy} onClick={() => void saveDraft()}>Save as draft</Button> : null}
        </div>
        {session.document_draft && <p className="text-xs text-muted-foreground">Each finished turn updates this draft until someone saves it. Promote it on its page to make it the live document.</p>}
      </section>}
```

  - the side panel's description becomes `The agent's latest complete document. It is kept as a document draft; open the
    draft to compare it with the live version and promote it.`; update the component's doc comment ("with Save
    document" → "with Open draft").

- [ ] **Step 5: `components/ProposalSheet.tsx`** — import the `AlertDialog`, `AlertDialogAction`, `AlertDialogCancel`,
  `AlertDialogContent`, `AlertDialogDescription`, `AlertDialogFooter`, `AlertDialogHeader`, `AlertDialogTitle` from
  `~/components/ui/alert-dialog`; inside `ProposalSheet` add

```tsx
  const [confirmReplace, setConfirmReplace] = useState(false);
  // Accepting a new version of an existing document replaces its live version: ask first.
  const replaces = proposal.kind === "document" && proposal.current ? proposal.current : null;
```

  change the Accept button's `onClick` to `() => replaces ? setConfirmReplace(true) : void decide("accept")`, and add
  after `</SheetContent>` (inside `<Sheet>`):

```tsx
    <AlertDialog open={confirmReplace} onOpenChange={setConfirmReplace}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>Replace the live version?</AlertDialogTitle>
        <AlertDialogDescription>{replaces ? `This replaces the live version ${replaces.version} of “${replaces.title}”. Continue?` : ""}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction onClick={() => { setConfirmReplace(false); void decide("accept"); }}>Continue</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
```

- [ ] **Step 6: `docs/user-guide.md`**
  - **4.12 Generate documents**, the bullets after "**Stop**": replace the **Draft** and **Save document** bullets with:

```markdown
- **Draft**: each finished turn's final answer, as Markdown; **Preview draft** shows it in a wide side panel. Each
  finished turn also keeps it as a **document draft** — of the document with the run's name in its folder, or of a new
  document there — and **Open draft** opens it (on the document's Drafts tab, or on the new document's draft page). The
  next turn updates that draft until someone saves it in the editor; after that a turn starts a new draft. Nothing goes
  live until a person promotes a draft. Runs from before drafts show **Save as draft** instead.
```

  - **4.13 Documents**: after the editor paragraph add a subsection:

```markdown
#### Drafts and connections

Anything an agent writes is a **draft**; a version goes live only when a person saves it (**Save new version**),
promotes a draft, or accepts a discovery document proposal.

- The list's **Drafts** column counts each document's open drafts and opens its Drafts tab; **Has drafts** shows only
  documents with drafts. **New drafts** above the table lists drafts of documents that do not exist yet (title, folder,
  written by, updated) with **Open** and **Discard**.
- **Save draft** (beside **Save new version**) keeps the editor's text as a new draft, or updates the draft you opened
  with **Edit**. On **Create document** it saves a draft of the new document and opens its page.
- **Drafts** tab: the open drafts (written by a person or an **Agent run**, updated, based on version n, **stale** when
  the live version moved on). A draft shows its changes against the live version, a preview and its **Sources** (the
  document it revises and the documents its author read, "(deleted)" when one is gone). **Edit** loads it into the
  editor; **Promote** makes it the live version — a stale draft first says "This draft was based on version n; the
  live version is now m. Promoting replaces version m's text." and promotes only with **Promote anyway**; **Discard**
  removes it from the lists (it stays in the audit history).
- A new document's draft has its own page; **Promote** creates the document. If a document with that name was created
  in the folder meanwhile, the draft becomes a draft of it and asks you to review it again.
- **Connections** tab: **Depends on** (add with **Find a document**, remove), **Used by**, and **Built from** (what the
  live version was built from).
- **Version history** marks versions promoted from a draft ("from draft").
- Drafts are never exported, searched or shown to agents; deleting a document deletes its drafts and links.
```

  - **4.5 Server discovery**, after "A **document** shows its text.": add "Accepting a new version of an existing
    document first asks: This replaces the live version n of “title”. Continue?"
  - Workflows **B** step 6, **F** step 1 and **G** step 3: "**Save document**" becomes "**Open draft**, then
    **Promote**".

- [ ] **Step 7: Run the tests and the full suites**

Run: `cd services/admin-ui && npx vitest run app/components/__tests__/generation.test.tsx app/components/__tests__/discoveryRun.test.tsx && npx tsc --noEmit && npx react-router routes >/dev/null && npx vitest run`
and `cd services/admin-api && uv run pytest -q`
Expected: PASS (only the known failures listed under Global Constraints).

- [ ] **Step 8: Commit**

```bash
git add services/admin-ui/app/lib/generations.ts services/admin-ui/app/components/GenerationView.tsx \
  services/admin-ui/app/components/ProposalSheet.tsx services/admin-ui/app/components/__tests__/generation.test.tsx \
  services/admin-ui/app/components/__tests__/discoveryRun.test.tsx docs/user-guide.md
git commit -m "feat(admin-ui): run page opens or saves the document draft; discovery asks before replacing a live version; docs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
