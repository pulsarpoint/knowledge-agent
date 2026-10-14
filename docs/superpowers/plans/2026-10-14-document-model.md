# Document model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live documents and drafts in one `ka.documents` table, earlier live contents in `ka.document_archive`, every
document ↔ resource link in `ka.document_resources` with provenance, history and locked built-for links.

**Architecture:** Migration 0104 reshapes the schema and moves the data (documents get their content; versions become
archive rows; open drafts become draft rows; the five link tables become `document_resources`). A new
`documents/store.py` owns the lifecycle (create, edit, draft, promote, discard, links); `documents/api.py`,
`documents/drafts.py`, the document tools, discovery, inventory and sessions call it and read through the views
`ka.document_details` (live documents) and `ka.document_drafts_view` (drafts). The UI gets the drafts list with the
promote warning, a drafts filter by resource, locked built-for links and the link history.

**Tech Stack:** PostgreSQL 17 (golang-migrate SQL files), Python 3.14 / FastAPI / psycopg 3, pytest with a disposable
PostgreSQL; React Router 7 / TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-14-document-resources-design.md` (read it first). Code map of every current
touchpoint (file:line): `.superpowers/document-model-map.md` in the worktree (git-ignored; read it before your task).

## Global Constraints

- Work only in the worktree `.claude/worktrees/document-model` (branch `feature/document-model`). Never `make`, never
  touch `.env`/`.local`, never `git stash`, never touch the running Docker stack or its database; pytest starts its own
  throwaway PostgreSQL.
- UI checks need `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once (git-ignored;
  never commit it, never `pnpm install`).
- Format what you change before committing: `cd services/admin-api && uv run --frozen ruff format` and
  `cd services/admin-ui && pnpm format` (settings: repo-root `ruff.toml`, `services/admin-ui/.prettierrc.json`).
- Commits end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`; never `git add`
  `.superpowers` or `node_modules`.
- API paths are under `/api/documentation` (e.g. `/api/documentation/documents/{id}`, `/api/documentation/drafts`).
- Content limits stay as today: title nonblank ≤ 240, description ≤ 4000, purpose nonblank ≤ 80, audience
  `people|agents|both`, markdown nonblank ≤ 1,000,000, `content_hash = sha256(markdown)` hex, drafts' tags ≤ 30.
- `origin` values are `manual | generated | uploaded` everywhere in the database; the drafts API reports `agent` for a
  draft with a `session_id` and `manual` otherwise (the UI keeps showing "by agent").
- Built-for refusal message (409): `This document was built for {type} {name}; that link can't be removed.` with
  `{type}` one of `environment, service, server, code project, deployment`.
- Promote warning (UI): `Promote and delete {n} other draft(s)?` — shown only when n > 0.
- Tasks 1–5 are one backend switch: Task 1 drops the old tables, so tests outside a task's own list may fail until
  Task 5. Each task names the tests that must pass. **From the end of Task 5 the full backend suite must pass** apart
  from the known failures: code-graph tests in worktrees (test_code_graph_sdk errors,
  test_code_graphs_config::test_a_generated_graph_answers_code_queries_end_to_end,
  test_code_processing::test_go_module_root_…), test_deploy::test_docs_commands_serve_the_docs_folder_inside_the_data_root,
  test_environments::test_migration_reapply_and_populated_rollback_protection.
- Full backend suite: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings`. UI: `cd services/admin-ui &&
  pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm exec react-router routes` (long UI tests can time out when the
  machine is loaded — rerun those files alone and report).

## Review Focus

1. A brand-new draft whose folder+slug matches a live document created after the draft (migration and promote): it
   must become a draft of that document, never fail the migration or the promote with a unique violation.
2. Two runs promoting different drafts of one document at the same time: one wins, the other gets 409 (its draft was
   deleted), never two archive rows with the same version or a lost update.
3. A person unticking a built-for link from a page loaded before the document was created by a run (stale page): 409
   with the built-for message, links unchanged, page reloads links.
4. Readers that list documents (Documents list, folder statistics, export backfill, inventory attach, document links,
   agents' `get_document`) must never show or accept a draft row as a document.
5. A run that saves after its draft was deleted by someone else's promote: it starts a new draft of the new live
   version and its tool note says the earlier draft was promoted.

---

## File structure

| File | Responsibility |
|---|---|
| `db/migrations/0104_document_model.up.sql` / `.down.sql` | schema + data move; rollback with the "would be lost" guard |
| `services/admin-api/src/knowledge_admin/documents/store.py` (new) | document lifecycle: create live, create/update draft, edit live, promote, discard, links, versions, history |
| `documents/api.py` | HTTP for documents; export; snapshot building; calls store |
| `documents/drafts.py` | HTTP for drafts and connections; sources; calls store |
| `documents/folders.py` | folder statistics over live documents |
| `capabilities/document_tools.py` | the run's draft through store; notes via `last_draft_id` |
| `sessions/rows.py`, `sessions/api.py`, `sessions/hub.py`, `sessions/start.py` | session draft reference, document reads |
| `servers/discovery.py`, `capabilities/discovery.py`, `capabilities/inventory.py`, `servers/inventory_map.py`, `servers/inventory_links_api.py` | read live documents; discovery creates live documents through store |
| `services/admin-ui/app/features/documents/*` | types, drafts list + promote warning, drafts filter, locked links, link history, versions by number |

---

### Task 1: Migration 0104 — schema and data move

**Files:**
- Create: `db/migrations/0104_document_model.up.sql`, `db/migrations/0104_document_model.down.sql`
- Create: `services/admin-api/tests/test_document_model_migration.py`
- Modify (add `'0104_document_model.down.sql'` first in the list of later migrations they roll back, and the matching
  `.up.sql` last when they roll forward): `tests/test_document_drafts_migration.py`, `test_document_agent_migration.py`,
  `test_sessions_api_migration.py`, `test_write_documents_migration.py`, `test_discovery_migration.py`,
  `test_discovery_runs_migration.py`, `test_inventory_links_migration.py`, `test_document_folders_migration.py`,
  `test_document_folders_lowercase_migration.py`, `test_generate_documents_migration.py`,
  `test_document_publish_migration.py`, `test_event_runs_migration.py` (check each file's own mechanism: fixed
  `LATER` list, `down_from_latest`/`up_to_latest`, `before_0102`; the glob in `test_agent_system_prompt_migration.py`
  already picks 0104 up).

**Interfaces:**
- Produces (schema, used by every later task):
  - `ka.documents` new columns: `is_draft, draft_of, version, base_version, title, description, purpose, audience,
    tags, markdown, content_hash, source_snapshot, origin, session_id, edited, from_draft, version_created_at,
    version_created_by, created_by, updated_by`; dropped: `current_version_id, published_version_id`; `slug` nullable.
  - `ka.document_archive`, `ka.document_resources`, `ka.document_sources(document_id | archive_id owner)`,
    `ka.agent_sessions.last_draft_id`.
  - Views `ka.live_documents`, `ka.document_details` (live rows; columns below), `ka.document_drafts_view`.
  - Triggers: `document_draft_of_live`, `document_stays_live`, `refuse_draft_document(cols…)` on `document_links`,
    `inventory_documents`, `agent_sessions`, `discovery_proposals`; `document_resources_owner`.
  - Dropped: `ka.document_versions`, `ka.document_drafts`, `freeze_decided_document_draft()`, the five link tables.

- [ ] **Step 1: Write the failing migration tests** — `tests/test_document_model_migration.py`. Build the "before"
  world by rolling 0104 back first (the template already has it applied), then insert old-shape rows, run the up file,
  and assert. Use the repo's migration-test pattern:

```python
"""0104: live documents and drafts in ka.documents, ka.document_archive, ka.document_resources."""

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0104_document_model.up.sql', '0104_document_model.down.sql'


def run(conn, name):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def before(conn):
    """Roll 0104 back so old-shape rows can be written."""
    conn.commit()
    run(conn, DOWN)


def old_document(conn, project, slug, versions, folder=None, links=None):
    """A document in the 0103 shape with `versions` markdown bodies (last one current); returns (id, [version ids]).

    The document row goes first (versions reference it); current_version_id is a deferred foreign key, so it may name
    a version inserted later in the same transaction."""
    doc, ids = uuid4(), [uuid4() for _ in versions]
    conn.execute(
        'INSERT INTO ka.documents(id,project_id,slug,folder,current_version_id,revision) VALUES (%s,%s,%s,%s,%s,%s)',
        (doc, project, slug, folder, ids[-1], len(ids)),
    )
    for number, (version_id, body) in enumerate(zip(ids, versions), start=1):
        conn.execute(
            """INSERT INTO ka.document_versions(id,document_id,project_id,version,title,description,purpose,audience,
            tags,markdown,content_hash,source_snapshot,origin) VALUES (%s,%s,%s,%s,%s,'','explanation','both','{}',%s,
            encode(sha256(convert_to(%s,'UTF8')),'hex'),'{"code_projects": []}','manual')""",
            (version_id, doc, project, number, f'{slug} v{number}', body, body),
        )
    for table, column, value in links or ():
        conn.execute(f'INSERT INTO ka.{table}(document_id,project_id,{column}) VALUES (%s,%s,%s)', (doc, project, value))
    return doc, ids
```

  Tests to write (each opens `store.connect()`, gets `project = store.project(conn, 'shop')`, calls `before(conn)`,
  builds rows, `conn.commit()`, `run(conn, UP)`):
  - `test_a_document_keeps_its_id_slug_folder_and_current_content` — `old_document(…, ['# One', '# Two'])` → the row
    has `is_draft = false`, `version = 2`, `markdown = '# Two'`, same id/slug/folder, `version_created_at` = version 2's
    `created_at`; `ka.document_archive` has exactly version 1 with the old version 1 id and `markdown = '# One'`.
  - `test_links_become_migrated_document_resources` — links in `document_environments`, `document_services`,
    `document_servers`, `document_code_projects`, `document_deployments` (create the resources with the existing
    fixtures/SQL used by `test_documents.py`) → one `document_resources` row each, `connection='linked'`,
    `added_how='migrated'`, `removed_at IS NULL`; `ka.document_details` arrays equal what the old view returned
    (capture `SELECT * FROM ka.document_details` arrays before running UP).
  - `test_open_drafts_become_draft_rows_and_decided_drafts_are_dropped` — an open draft of the document (base = v1),
    a promoted and a discarded draft → one row `is_draft`, `draft_of = doc`, `base_version = 1`, same id as the old
    draft, `origin = 'generated'` when the old origin was `agent`; no rows for the decided drafts.
  - `test_a_new_document_draft_keeps_its_name_and_gets_built_for_links` — open draft with `slug, folder`,
    `resource_ids = {"environment_ids": [env]}`, `session_id` → brand-new draft row (`draft_of IS NULL`, slug/folder
    kept) and one `document_resources` row `built_for`, `added_how='agent_run'`, `session_id` set; an id in
    `resource_ids` that no longer exists is dropped.
  - `test_a_new_document_draft_named_like_a_live_document_becomes_its_draft` — open new-document draft with the same
    folder+slug as a live document → `draft_of` = that document, slug/folder NULL (Review Focus 1).
  - `test_two_new_document_drafts_with_one_name_get_numbered` — two open new drafts with the same folder+slug →
    slugs `name` and `name-2` (the older keeps `name`).
  - `test_sources_follow_their_owner` — a `document_sources` row of the current version → `document_id` = doc; of an
    older version → `archive_id` = that version id; of an open draft → `document_id` = draft id; of a decided draft →
    gone.
  - `test_sessions_remember_their_last_draft` — a session with two drafts → `agent_sessions.last_draft_id` = the newer.
  - `test_drafts_cannot_be_used_as_documents` — after UP: inserting `document_links`, `inventory_documents`,
    `agent_sessions.target_document_id`, `discovery_proposals.document_id` pointing at a draft raises
    `psycopg.errors.RaiseException`; a `document_resources` row on a draft of a live document raises; a draft whose
    `draft_of` is another draft raises; `UPDATE ka.documents SET is_draft=true` on a live row raises.
  - `test_a_built_for_link_cannot_be_removed` — `UPDATE … SET removed_at=now(), removed_by=…` on a `built_for` row
    raises `psycopg.errors.CheckViolation`; two active rows for one document+environment raise `UniqueViolation`; a
    removed row plus a new active one is fine.
  - `test_rollback_restores_the_old_shape` — after UP with migrated data only: run DOWN → `document_versions` has both
    versions with the same ids, `documents.current_version_id` points at a version with the live markdown, the open
    draft is back in `document_drafts` (`status='open'`), the five link tables hold the links, and the schema
    (columns, constraints, indexes, views of the affected tables, read from `information_schema.columns`,
    `pg_constraint`, `pg_indexes`, `pg_views`) equals a snapshot taken before UP.
  - `test_rollback_refuses_when_link_history_would_be_lost` — after UP, mark one `linked` row removed (or insert a
    `person` link) → DOWN raises `psycopg.errors.RaiseException` matching `would be lost`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings tests/test_document_model_migration.py`
Expected: errors — `0104_document_model.down.sql` does not exist.

- [ ] **Step 3: Write `0104_document_model.up.sql`** (complete file; keep the statement order):

```sql
BEGIN;
SET search_path = ka, public;

-- Live documents and drafts in one table; earlier live contents in document_archive; links in document_resources.

-- 1. documents: content and draft columns.
ALTER TABLE documents
    ADD COLUMN is_draft boolean NOT NULL DEFAULT false,
    ADD COLUMN draft_of uuid,
    ADD COLUMN version integer CHECK (version > 0),
    ADD COLUMN base_version integer CHECK (base_version > 0),
    ADD COLUMN title nonblank, ADD COLUMN description text, ADD COLUMN purpose nonblank, ADD COLUMN audience text,
    ADD COLUMN tags text[], ADD COLUMN markdown nonblank, ADD COLUMN content_hash sha256,
    ADD COLUMN source_snapshot jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(source_snapshot) = 'object'),
    ADD COLUMN origin text, ADD COLUMN session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL,
    ADD COLUMN edited boolean NOT NULL DEFAULT false,
    ADD COLUMN from_draft boolean NOT NULL DEFAULT false,
    ADD COLUMN version_created_at timestamptz,
    ADD COLUMN version_created_by uuid REFERENCES principals(id),
    ADD COLUMN created_by uuid REFERENCES principals(id),
    ADD COLUMN updated_by uuid REFERENCES principals(id);

UPDATE documents d SET
    version = v.version, title = v.title, description = v.description, purpose = v.purpose, audience = v.audience,
    tags = v.tags, markdown = v.markdown, content_hash = v.content_hash, source_snapshot = v.source_snapshot,
    origin = v.origin, version_created_at = v.created_at, version_created_by = v.created_by,
    created_by = (SELECT f.created_by FROM document_versions f WHERE f.document_id = d.id ORDER BY f.version LIMIT 1),
    from_draft = EXISTS (SELECT 1 FROM document_drafts x WHERE x.promoted_version_id = v.id)
FROM document_versions v WHERE v.id = d.current_version_id;

-- 2. archive: every version that is not current, keeping its id.
CREATE TABLE document_archive (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    document_id uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    title nonblank NOT NULL CHECK (length(title) <= 240),
    description text NOT NULL CHECK (length(description) <= 4000),
    purpose nonblank NOT NULL CHECK (length(purpose) <= 80),
    audience text NOT NULL CHECK (audience IN ('people', 'agents', 'both')),
    tags text[] NOT NULL DEFAULT '{}',
    markdown nonblank NOT NULL CHECK (length(markdown) <= 1000000),
    content_hash sha256 NOT NULL CHECK (content_hash = encode(sha256(convert_to(markdown, 'UTF8')), 'hex')),
    source_snapshot jsonb NOT NULL CHECK (jsonb_typeof(source_snapshot) = 'object'),
    origin text NOT NULL CHECK (origin IN ('manual', 'generated', 'uploaded')),
    from_draft boolean NOT NULL DEFAULT false,
    session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL,
    created_by uuid REFERENCES principals(id),
    created_at timestamptz NOT NULL,
    archived_at timestamptz NOT NULL DEFAULT now(),
    archived_by uuid REFERENCES principals(id),
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE,
    UNIQUE (document_id, version)
);
CREATE TRIGGER document_archive_immutable BEFORE UPDATE ON document_archive
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
COMMENT ON TABLE document_archive IS
    'Earlier live contents of a document (document_id = the live document), one row per replaced version.';

INSERT INTO document_archive(id, project_id, document_id, version, title, description, purpose, audience, tags,
    markdown, content_hash, source_snapshot, origin, from_draft, created_by, created_at, archived_at)
SELECT v.id, v.project_id, v.document_id, v.version, v.title, v.description, v.purpose, v.audience, v.tags,
    v.markdown, v.content_hash, v.source_snapshot, v.origin,
    EXISTS (SELECT 1 FROM document_drafts x WHERE x.promoted_version_id = v.id),
    v.created_by, v.created_at,
    COALESCE((SELECT min(n.created_at) FROM document_versions n
              WHERE n.document_id = v.document_id AND n.version > v.version), v.created_at)
FROM document_versions v JOIN documents d ON d.id = v.document_id
WHERE v.id <> d.current_version_id;

-- 3. drafts become rows of documents. Slug uniqueness moves to a partial index first.
ALTER TABLE documents DROP CONSTRAINT documents_project_id_folder_slug_key;
ALTER TABLE documents ALTER COLUMN slug DROP NOT NULL;

-- A new-document draft named like a live document becomes a draft of it (as promote does today).
UPDATE document_drafts x SET document_id = d.id, slug = NULL, folder = NULL,
    base_version_id = d.current_version_id
FROM documents d
WHERE x.status = 'open' AND x.document_id IS NULL AND d.project_id = x.project_id
  AND d.slug = x.slug AND d.folder IS NOT DISTINCT FROM x.folder;
-- Two open new-document drafts with one name: the older keeps it, the others get -2, -3, ...
WITH numbered AS (
    SELECT id, row_number() OVER (PARTITION BY project_id, folder, slug ORDER BY created_at, id) AS n
    FROM document_drafts WHERE status = 'open' AND document_id IS NULL
)
UPDATE document_drafts x SET slug = left(x.slug, 116) || '-' || numbered.n
FROM numbered WHERE numbered.id = x.id AND numbered.n > 1;

INSERT INTO documents(id, project_id, slug, folder, revision, created_at, updated_at, is_draft, draft_of,
    base_version, title, description, purpose, audience, tags, markdown, content_hash, origin, session_id, edited,
    created_by, updated_by)
SELECT x.id, x.project_id, x.slug, x.folder, x.revision, x.created_at, x.updated_at, true, x.document_id,
    (SELECT v.version FROM document_versions v WHERE v.id = x.base_version_id),
    x.title, x.description, x.purpose, x.audience, x.tags, x.markdown,
    encode(sha256(convert_to(x.markdown, 'UTF8')), 'hex'),
    CASE x.origin WHEN 'agent' THEN 'generated' ELSE 'manual' END, x.session_id, x.edited,
    x.created_by, x.updated_by
FROM document_drafts x WHERE x.status = 'open';

ALTER TABLE documents
    ALTER COLUMN title SET NOT NULL, ALTER COLUMN description SET NOT NULL, ALTER COLUMN purpose SET NOT NULL,
    ALTER COLUMN audience SET NOT NULL, ALTER COLUMN tags SET NOT NULL, ALTER COLUMN tags SET DEFAULT '{}',
    ALTER COLUMN markdown SET NOT NULL, ALTER COLUMN content_hash SET NOT NULL, ALTER COLUMN origin SET NOT NULL,
    ADD CONSTRAINT documents_title_length CHECK (length(title) <= 240),
    ADD CONSTRAINT documents_description_length CHECK (length(description) <= 4000),
    ADD CONSTRAINT documents_purpose_length CHECK (length(purpose) <= 80),
    ADD CONSTRAINT documents_audience_check CHECK (audience IN ('people', 'agents', 'both')),
    ADD CONSTRAINT documents_markdown_length CHECK (length(markdown) <= 1000000),
    ADD CONSTRAINT documents_content_hash_check
        CHECK (content_hash = encode(sha256(convert_to(markdown, 'UTF8')), 'hex')),
    ADD CONSTRAINT documents_origin_check CHECK (origin IN ('manual', 'generated', 'uploaded')),
    ADD CONSTRAINT documents_kind CHECK (
        (NOT is_draft AND draft_of IS NULL AND slug IS NOT NULL AND version IS NOT NULL
            AND version_created_at IS NOT NULL AND base_version IS NULL)
        OR (is_draft AND version IS NULL AND draft_of IS NULL AND slug IS NOT NULL AND base_version IS NULL)
        OR (is_draft AND version IS NULL AND draft_of IS NOT NULL AND slug IS NULL AND folder IS NULL)),
    ADD CONSTRAINT documents_draft_of_fk FOREIGN KEY (draft_of, project_id)
        REFERENCES documents(id, project_id) ON DELETE CASCADE;
CREATE UNIQUE INDEX documents_folder_slug ON documents(project_id, folder, slug) NULLS NOT DISTINCT
    WHERE slug IS NOT NULL;
CREATE INDEX documents_drafts ON documents(draft_of, created_at) WHERE is_draft;
CREATE INDEX documents_new_drafts ON documents(project_id, created_at) WHERE is_draft AND draft_of IS NULL;
CREATE INDEX documents_session ON documents(session_id) WHERE session_id IS NOT NULL;

CREATE FUNCTION document_draft_of_live() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.draft_of IS NOT NULL AND NOT EXISTS (SELECT 1 FROM documents WHERE id = NEW.draft_of AND NOT is_draft) THEN
        RAISE EXCEPTION 'A draft can only revise a live document';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER document_draft_of_live BEFORE INSERT OR UPDATE OF draft_of ON documents
    FOR EACH ROW EXECUTE FUNCTION document_draft_of_live();

CREATE FUNCTION document_stays_live() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.is_draft AND NOT OLD.is_draft THEN
        RAISE EXCEPTION 'A live document cannot become a draft';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER document_stays_live BEFORE UPDATE OF is_draft ON documents
    FOR EACH ROW EXECUTE FUNCTION document_stays_live();

-- Tables that name a document must name a live one.
CREATE FUNCTION refuse_draft_document() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    col text;
    target uuid;
BEGIN
    FOREACH col IN ARRAY TG_ARGV LOOP
        target := (to_jsonb(NEW) ->> col)::uuid;
        IF target IS NOT NULL AND EXISTS (SELECT 1 FROM documents WHERE id = target AND is_draft) THEN
            RAISE EXCEPTION 'A draft is not a document yet; % must name a live document', col;
        END IF;
    END LOOP;
    RETURN NEW;
END $$;
CREATE TRIGGER document_links_live BEFORE INSERT OR UPDATE ON document_links
    FOR EACH ROW EXECUTE FUNCTION refuse_draft_document('from_document_id', 'to_document_id');
CREATE TRIGGER inventory_documents_live BEFORE INSERT OR UPDATE ON inventory_documents
    FOR EACH ROW EXECUTE FUNCTION refuse_draft_document('document_id');
CREATE TRIGGER agent_sessions_live_document BEFORE INSERT OR UPDATE OF document_id, target_document_id ON agent_sessions
    FOR EACH ROW EXECUTE FUNCTION refuse_draft_document('document_id', 'target_document_id');
CREATE TRIGGER discovery_proposals_live_document BEFORE INSERT OR UPDATE OF document_id ON discovery_proposals
    FOR EACH ROW EXECUTE FUNCTION refuse_draft_document('document_id');

-- 4. links with provenance and history.
CREATE TABLE document_resources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL,
    document_id uuid NOT NULL,
    environment_id uuid,
    service_id uuid,
    server_id uuid,
    code_project_id uuid,
    deployment_id uuid,
    resource_type text NOT NULL GENERATED ALWAYS AS (CASE
        WHEN environment_id IS NOT NULL THEN 'environment'
        WHEN service_id IS NOT NULL THEN 'service'
        WHEN server_id IS NOT NULL THEN 'server'
        WHEN code_project_id IS NOT NULL THEN 'code_project'
        ELSE 'deployment' END) STORED,
    connection text NOT NULL CHECK (connection IN ('built_for', 'linked')),
    added_how text NOT NULL CHECK (added_how IN ('person', 'agent_run', 'discovery', 'migrated')),
    added_by uuid REFERENCES principals(id),
    session_id uuid REFERENCES agent_sessions(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    removed_at timestamptz,
    removed_by uuid REFERENCES principals(id),
    FOREIGN KEY (document_id, project_id) REFERENCES documents(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (environment_id, project_id) REFERENCES environments(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (service_id, project_id) REFERENCES services(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (server_id, project_id) REFERENCES servers(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (code_project_id, project_id) REFERENCES repositories(id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (deployment_id, project_id) REFERENCES service_deployments(id, project_id) ON DELETE CASCADE,
    CONSTRAINT document_resources_one_resource
        CHECK (num_nonnulls(environment_id, service_id, server_id, code_project_id, deployment_id) = 1),
    CONSTRAINT document_resources_built_for_stays CHECK (connection <> 'built_for' OR removed_at IS NULL),
    CONSTRAINT document_resources_removed CHECK ((removed_at IS NULL) = (removed_by IS NULL))
);
COMMENT ON TABLE document_resources IS
    'A document''s links to resources: built_for (what it was created for; never removed) or linked; history kept via removed_at.';
CREATE UNIQUE INDEX document_resources_active_environment ON document_resources(document_id, environment_id)
    WHERE removed_at IS NULL AND environment_id IS NOT NULL;
CREATE UNIQUE INDEX document_resources_active_service ON document_resources(document_id, service_id)
    WHERE removed_at IS NULL AND service_id IS NOT NULL;
CREATE UNIQUE INDEX document_resources_active_server ON document_resources(document_id, server_id)
    WHERE removed_at IS NULL AND server_id IS NOT NULL;
CREATE UNIQUE INDEX document_resources_active_code_project ON document_resources(document_id, code_project_id)
    WHERE removed_at IS NULL AND code_project_id IS NOT NULL;
CREATE UNIQUE INDEX document_resources_active_deployment ON document_resources(document_id, deployment_id)
    WHERE removed_at IS NULL AND deployment_id IS NOT NULL;
CREATE INDEX document_resources_environment ON document_resources(environment_id) WHERE removed_at IS NULL;
CREATE INDEX document_resources_service ON document_resources(service_id) WHERE removed_at IS NULL;
CREATE INDEX document_resources_server ON document_resources(server_id) WHERE removed_at IS NULL;
CREATE INDEX document_resources_code_project ON document_resources(code_project_id) WHERE removed_at IS NULL;
CREATE INDEX document_resources_deployment ON document_resources(deployment_id) WHERE removed_at IS NULL;
CREATE INDEX document_resources_document ON document_resources(document_id, created_at);

CREATE FUNCTION document_resources_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM documents WHERE id = NEW.document_id AND draft_of IS NOT NULL) THEN
        RAISE EXCEPTION 'Links belong to the document, not to a draft of it';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER document_resources_owner BEFORE INSERT OR UPDATE OF document_id ON document_resources
    FOR EACH ROW EXECUTE FUNCTION document_resources_owner();

INSERT INTO document_resources(project_id, document_id, environment_id, connection, added_how)
    SELECT project_id, document_id, environment_id, 'linked', 'migrated' FROM document_environments;
INSERT INTO document_resources(project_id, document_id, service_id, connection, added_how)
    SELECT project_id, document_id, service_id, 'linked', 'migrated' FROM document_services;
INSERT INTO document_resources(project_id, document_id, server_id, connection, added_how)
    SELECT project_id, document_id, server_id, 'linked', 'migrated' FROM document_servers;
INSERT INTO document_resources(project_id, document_id, code_project_id, connection, added_how)
    SELECT project_id, document_id, code_project_id, 'linked', 'migrated' FROM document_code_projects;
INSERT INTO document_resources(project_id, document_id, deployment_id, connection, added_how)
    SELECT project_id, document_id, deployment_id, 'linked', 'migrated' FROM document_deployments;

-- A new-document draft's links-to-be (resource_ids JSON) become its built-for links; ids that no longer exist are dropped.
INSERT INTO document_resources(project_id, document_id, environment_id, connection, added_how, added_by, session_id,
    created_at)
SELECT x.project_id, x.id, e.id, 'built_for', CASE WHEN x.session_id IS NULL THEN 'person' ELSE 'agent_run' END,
    x.created_by, x.session_id, x.created_at
FROM document_drafts x
CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(x.resource_ids -> 'environment_ids', '[]')) AS ids(value)
JOIN environments e ON e.id = ids.value::uuid AND e.project_id = x.project_id
WHERE x.status = 'open' AND x.document_id IS NULL;
-- … the same INSERT for service_ids → services, server_ids → servers, code_project_ids → repositories,
--   deployment_ids → service_deployments (write all four out; same shape, other column and table).

-- 5. sources follow their owner.
ALTER TABLE document_sources
    ADD COLUMN document_id uuid REFERENCES documents(id) ON DELETE CASCADE,
    ADD COLUMN archive_id uuid REFERENCES document_archive(id) ON DELETE CASCADE;
DELETE FROM document_sources s USING document_drafts x WHERE s.draft_id = x.id AND x.status <> 'open';
UPDATE document_sources SET document_id = draft_id WHERE draft_id IS NOT NULL;
UPDATE document_sources s SET document_id = d.id FROM documents d
    WHERE s.version_id IS NOT NULL AND d.current_version_id = s.version_id;
UPDATE document_sources SET archive_id = version_id WHERE version_id IS NOT NULL AND document_id IS NULL;
ALTER TABLE document_sources
    DROP CONSTRAINT document_sources_owner,
    DROP COLUMN draft_id, DROP COLUMN version_id, DROP COLUMN source_version_id,
    ADD CONSTRAINT document_sources_owner CHECK ((document_id IS NULL) <> (archive_id IS NULL));
CREATE UNIQUE INDEX document_sources_document ON document_sources(document_id, source_document_id)
    WHERE document_id IS NOT NULL;
CREATE UNIQUE INDEX document_sources_archive ON document_sources(archive_id, source_document_id)
    WHERE archive_id IS NOT NULL;

-- 6. a run remembers its last draft (drafts are deleted on promote and discard).
ALTER TABLE agent_sessions ADD COLUMN last_draft_id uuid;
UPDATE agent_sessions s SET last_draft_id = (
    SELECT x.id FROM document_drafts x WHERE x.session_id = s.id ORDER BY x.created_at DESC, x.id DESC LIMIT 1);
COMMENT ON COLUMN agent_sessions.last_draft_id IS
    'The draft this run wrote last (no foreign key: promote and discard delete drafts; the audit log has the outcome).';

-- 7. drop the old shape.
DROP VIEW document_details;
ALTER TABLE documents DROP COLUMN current_version_id, DROP COLUMN published_version_id;
DROP TABLE document_drafts;
DROP FUNCTION freeze_decided_document_draft();
DROP TABLE document_versions;
DROP TABLE document_environments, document_services, document_servers, document_code_projects, document_deployments;

-- 8. views.
CREATE VIEW live_documents AS SELECT * FROM documents WHERE NOT is_draft;

CREATE VIEW document_details AS
SELECT d.id, d.project_id, d.slug, d.folder, d.revision, d.created_at, d.updated_at, d.phase_metadata,
    d.version, d.title, d.description, d.purpose, d.audience, d.tags, d.content_hash, d.origin, d.from_draft,
    d.version_created_at, d.version_created_by, d.created_by, d.updated_by,
    COALESCE((SELECT array_agg(r.code_project_id ORDER BY r.code_project_id) FROM document_resources r
        WHERE r.document_id = d.id AND r.removed_at IS NULL AND r.code_project_id IS NOT NULL), '{}') AS code_project_ids,
    COALESCE((SELECT array_agg(r.service_id ORDER BY r.service_id) FROM document_resources r
        WHERE r.document_id = d.id AND r.removed_at IS NULL AND r.service_id IS NOT NULL), '{}') AS service_ids,
    COALESCE((SELECT array_agg(r.server_id ORDER BY r.server_id) FROM document_resources r
        WHERE r.document_id = d.id AND r.removed_at IS NULL AND r.server_id IS NOT NULL), '{}') AS server_ids,
    COALESCE((SELECT array_agg(r.deployment_id ORDER BY r.deployment_id) FROM document_resources r
        WHERE r.document_id = d.id AND r.removed_at IS NULL AND r.deployment_id IS NOT NULL), '{}') AS deployment_ids,
    COALESCE((SELECT array_agg(r.environment_id ORDER BY r.environment_id) FROM document_resources r
        WHERE r.document_id = d.id AND r.removed_at IS NULL AND r.environment_id IS NOT NULL), '{}') AS environment_ids,
    (SELECT count(*) FROM documents x WHERE x.draft_of = d.id)::integer AS draft_count
FROM documents d WHERE NOT d.is_draft;

-- Drafts with their document's name and the links that apply (the live document's, or a brand-new draft's own).
CREATE VIEW document_drafts_view AS
SELECT x.id, x.project_id, x.draft_of AS document_id, COALESCE(x.slug, l.slug) AS slug,
    COALESCE(x.folder, l.folder) AS folder, x.title, x.description, x.purpose, x.audience, x.tags, x.markdown,
    x.content_hash, x.origin, x.session_id, x.edited, x.revision, x.base_version, l.version AS live_version,
    l.title AS document_title, (x.draft_of IS NOT NULL AND x.base_version IS DISTINCT FROM l.version) AS stale,
    x.created_by, x.updated_by, x.created_at, x.updated_at,
    COALESCE((SELECT array_agg(r.code_project_id ORDER BY r.code_project_id) FROM document_resources r
        WHERE r.document_id = COALESCE(x.draft_of, x.id) AND r.removed_at IS NULL AND r.code_project_id IS NOT NULL), '{}') AS code_project_ids,
    COALESCE((SELECT array_agg(r.service_id ORDER BY r.service_id) FROM document_resources r
        WHERE r.document_id = COALESCE(x.draft_of, x.id) AND r.removed_at IS NULL AND r.service_id IS NOT NULL), '{}') AS service_ids,
    COALESCE((SELECT array_agg(r.server_id ORDER BY r.server_id) FROM document_resources r
        WHERE r.document_id = COALESCE(x.draft_of, x.id) AND r.removed_at IS NULL AND r.server_id IS NOT NULL), '{}') AS server_ids,
    COALESCE((SELECT array_agg(r.deployment_id ORDER BY r.deployment_id) FROM document_resources r
        WHERE r.document_id = COALESCE(x.draft_of, x.id) AND r.removed_at IS NULL AND r.deployment_id IS NOT NULL), '{}') AS deployment_ids,
    COALESCE((SELECT array_agg(r.environment_id ORDER BY r.environment_id) FROM document_resources r
        WHERE r.document_id = COALESCE(x.draft_of, x.id) AND r.removed_at IS NULL AND r.environment_id IS NOT NULL), '{}') AS environment_ids
FROM documents x LEFT JOIN documents l ON l.id = x.draft_of
WHERE x.is_draft;

COMMIT;
```

  Write out the four remaining "resource_ids JSON → built_for" INSERTs in full (no `…` in the file). If `ruff`/linters
  flag nothing for SQL, keep lines ≤ 160 characters as other migrations do.

- [ ] **Step 4: Write `0104_document_model.down.sql`.** Shape (write it in full):
  1. `BEGIN; SET search_path = ka, public;`
  2. Guard — refuse when provenance or history would be lost:
     ```sql
     DO $$ BEGIN
         IF EXISTS (SELECT 1 FROM document_resources r JOIN documents d ON d.id = r.document_id
                    WHERE NOT d.is_draft AND NOT (r.connection = 'linked' AND r.added_how = 'migrated' AND r.removed_at IS NULL)) THEN
             RAISE EXCEPTION 'Document link provenance or history exists and would be lost; rollback is not possible after links were added, removed or built for a document';
         END IF;
     END $$;
     ```
  3. Recreate `document_versions` exactly as it is in 0103 (columns, constraint names, `reject_mutation` trigger, index
     `document_versions_generation`) — take the definitions from 0040 with the changes of 0073 and 0097; insert archive
     rows (same ids) and one row per live document (`gen_random_uuid()`; content from the row; `created_at =
     version_created_at`, `created_by = version_created_by`).
  4. Add back `documents.current_version_id` (NOT NULL after fill) and `published_version_id` with their deferred FKs
     `documents_current_version_fk` / `documents_published_version_fk`; `slug SET NOT NULL` after drafts are removed;
     re-add `documents_project_id_folder_slug_key UNIQUE NULLS NOT DISTINCT (project_id, folder, slug)`.
  5. Recreate `document_drafts` and `freeze_decided_document_draft()` exactly as 0098 defines them; insert one open
     draft per draft row (same id, `document_id = draft_of`, `base_version_id` = the version id of `base_version`,
     `resource_ids` built from a brand-new draft's built-for links, `origin = CASE WHEN origin = 'generated' THEN
     'agent' ELSE 'manual' END`).
  6. `document_sources`: add back `draft_id`, `version_id`, `source_version_id` and the 0098 constraint/indexes; map
     `document_id` of a draft row → `draft_id`, of a live row → that row's new version id, `archive_id` → `version_id`;
     drop the new columns/indexes.
  7. Recreate the five link tables exactly as 0040/0046 define them; fill from active `document_resources` rows of
     live documents.
  8. Drop views `document_drafts_view`, `document_details`, `live_documents`; drop triggers and functions created by
     the up file; delete draft rows from `documents`; drop `document_resources`, `document_archive`,
     `agent_sessions.last_draft_id` and the new `documents` columns/constraints/indexes; recreate the 0068
     `document_details` view verbatim.
  9. `COMMIT;`

- [ ] **Step 5: Run the migration tests**

Run: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings tests/test_document_model_migration.py`
Expected: all pass.

- [ ] **Step 6: Update the older migration tests** listed under Files so they roll 0104 back before their own down
  and forward again after; run them:

Run: `uv run --frozen pytest -q -p no:warnings tests/test_*migration*.py`
Expected: all pass (other test files are expected to fail until Task 5).

- [ ] **Step 7: Commit**

```bash
git add db/migrations/0104_document_model.up.sql db/migrations/0104_document_model.down.sql services/admin-api/tests/test_*migration*.py
git commit -m "feat(db): 0104 live documents and drafts in one table, archive, document resources"
```

---

### Task 2: `documents/store.py` — the document lifecycle

**Files:**
- Create: `services/admin-api/src/knowledge_admin/documents/store.py`
- Create: `services/admin-api/tests/test_document_store.py`

**Interfaces:**
- Consumes: the 0104 schema (Task 1).
- Produces (exact names; later tasks call these):

```python
# documents/store.py
LINK_COLUMNS = {  # DocumentInput / resource_ids field → document_resources column
    'code_project_ids': 'code_project_id', 'service_ids': 'service_id', 'server_ids': 'server_id',
    'deployment_ids': 'deployment_id', 'environment_ids': 'environment_id',
}
TYPE_LABEL = {'environment_id': 'environment', 'service_id': 'service', 'server_id': 'server',
              'code_project_id': 'code project', 'deployment_id': 'deployment'}
BUILT_FOR = "This document was built for {type} {name}; that link can't be removed."
CONTENT = ('title', 'description', 'purpose', 'audience', 'tags', 'markdown')

@dataclass(frozen=True)
class Provenance:
    how: str                       # 'person' | 'agent_run' | 'discovery'
    actor: UUID | None
    session_id: UUID | None = None

def build_snapshot(conn, project: UUID, ids: dict[str, list[UUID]], source_commits: dict[str, str] | None,
                   previous: dict | None) -> dict
    # today's save_document lines 323-378 moved verbatim: FOR SHARE reads of the chosen resources, the 400s
    # ('A selected resource is no longer available. …', 'Select the service and server belonging to each selected
    # deployment', 'Source commits must belong to the selected code projects'), git_commit carried from `previous`.

def save_links(conn, project: UUID, document_id: UUID, wanted: dict[str, list[UUID]], prov: Provenance, *,
               connection: str = 'linked') -> dict[str, list[str]]
    # returns {'added': [...], 'removed': [...]} (resource ids as str)

def write_live(conn, project: UUID, prov: Provenance, *, document_id: UUID | None, slug: str, folder: str | None,
               content: dict, snapshot: dict, origin: str, revision: int | None, from_draft: bool = False,
               links: dict[str, list[UUID]] | None = None) -> dict   # {'id', 'revision', 'version'}

def create_draft(conn, project: UUID, prov: Provenance, content: dict, *, origin: str,
                 document_id: UUID | None = None, slug: str | None = None, folder: str | None = None,
                 links: dict[str, list[UUID]] | None = None) -> UUID

def update_draft(conn, draft_id: UUID, actor: UUID | None, content: dict, *, revision: int | None,
                 edited: bool) -> int                                  # the new revision

def promote_draft(conn, project: UUID, actor: UUID | None, draft_id: UUID, *, revision: int,
                  confirm_stale: bool) -> dict  # {'document_id', 'version', 'revision', 'deleted_drafts', 'new': bool}

def discard_draft(conn, project: UUID, actor: UUID | None, draft_id: UUID, *, revision: int) -> None

def versions(conn, document_id: UUID) -> list[dict]         # [{version, title, origin, created_at, from_draft, live}]
def version(conn, document_id: UUID, number: int) -> dict | None
def resource_links(conn, document_id: UUID) -> list[dict]   # active: type, id, name, connection, added_how,
                                                             #   added_by (display name), session_id, created_at
def resource_history(conn, document_id: UUID) -> list[dict] # all rows, newest change first, + removed_at/removed_by
def draft_outcome(conn, draft_id: UUID) -> dict | None      # {'id','document_id','status'}: row → 'open'; else the
                                                             #   latest audit document.draft.promoted|discarded
```

Behaviour (each bullet is a test in Step 1):
- `save_links`: per column, compare `wanted` with active rows; unchanged rows untouched; new ids → insert with
  `connection`, `added_how = prov.how`, `added_by = prov.actor`, `session_id = prov.session_id`; an active `linked`
  row not wanted → `removed_at = now(), removed_by = prov.actor`; an active `built_for` row not wanted →
  `HTTPException(409, BUILT_FOR.format(type=TYPE_LABEL[col], name=<resource name>))` and nothing written (check every
  column before writing). Lock the document row `FOR UPDATE` first. `document_id` must be a live document or a
  brand-new draft (the trigger enforces it; let the error surface).
- `write_live` with `document_id=None`: `pg_advisory_xact_lock(hashtext(project || ':document-export'))` as today,
  insert a live row (`version = 1`, `revision = 1`, `version_created_at = now()`, `version_created_by = prov.actor`,
  `created_by = prov.actor`, `origin`, `from_draft`, `session_id = prov.session_id`), then `save_links(…,
  connection='built_for')` with `links`; audit `document.created` `{'version': 1}`.
- `write_live` with a `document_id`: lock the row `FOR UPDATE`; 404 `Document not found` (also for a draft id); 409
  `This document changed. Reload it before saving; your text has not been overwritten.` when `revision` differs; when
  any CONTENT field, slug or snapshot changed: insert the current content into `ka.document_archive` (`version`,
  content, `origin`, `from_draft`, `session_id`, `created_by = version_created_by`, `created_at =
  version_created_at`, `archived_by = prov.actor`), move the live row's `document_sources` (`document_id` → the new
  `archive_id`), then update the row (`version + 1`, content, `origin`, `from_draft`, `session_id`,
  `version_created_at = now()`, `version_created_by`, `updated_by`); always `revision + 1`; when `links` is given,
  `save_links(…, connection='linked')`; audit `document.updated` `{'version', 'links_added', 'links_removed'}`.
- `create_draft`: a `folder`+`slug` naming a live document → a draft of that document (today's rule); a draft of a
  live document gets `draft_of`, `base_version` = its live `version`; a brand-new draft gets `slug`, `folder` and its
  `links` as `built_for` via `save_links`; `origin` stored as given (`manual` | `generated`); `session_id`, `created_by`;
  when `prov.session_id` is set, `UPDATE ka.agent_sessions SET last_draft_id = <id>`; audit `document.draft.created`
  (resource_type `document_draft`) with today's details.
- `update_draft`: lock `FOR UPDATE`; 404 `Draft not found` when the row is gone (promoted or discarded); 409
  `DRAFT_CHANGED` text from `drafts.py` when `revision` is given and differs; update content, `content_hash`,
  `edited = edited OR <edited>`, `updated_by`, `revision + 1`.
- `promote_draft` (Review Focus 2): lock the draft row `FOR UPDATE` (404 when gone), check `revision` (409 as
  `update_draft`); for a draft of a live document lock the live row `FOR UPDATE`, stale when `base_version <>
  live.version` → `HTTPException(409, {'message': STALE_MESSAGE, 'code': 'STALE_BASE'})` unless `confirm_stale`
  (import `STALE_MESSAGE` text from `drafts.py`; move it here); archive the live content (as `write_live`), copy the
  draft's content onto the live row (`version + 1`, `from_draft = true`, `origin`, `session_id`, `version_created_at`,
  `version_created_by = actor`, `revision + 1`, snapshot = `build_snapshot` over the live row's active links with the
  previous snapshot's commits), move the draft's sources to the live row, `DELETE FROM ka.documents WHERE draft_of =
  live.id` (counts as `deleted_drafts`, the promoted draft included); for a brand-new draft: `is_draft = false`,
  `version = 1`, `from_draft = true`, `version_created_at`, `version_created_by`, snapshot from its links,
  `revision + 1`. Audit `document.draft.promoted` `{'document_id', 'version', 'stale_confirmed', 'deleted_drafts'}`
  with `resource_id = draft_id`. The caller exports after commit.
- `discard_draft`: lock, check revision, delete the row (a brand-new draft's links go with it), audit
  `document.draft.discarded` `{'document_id': draft_of}` with `resource_id = draft_id`.
- `versions` = archive rows + the live row, ordered by `version` descending; `live` true only for the live row.
- `draft_outcome`: the row → `{'id', 'document_id': draft_of or id, 'status': 'open'}`; else the newest audit event
  with `resource_type = 'document_draft'`, `resource_id = draft_id`, action promoted/discarded →
  `{'id', 'document_id': details.document_id, 'status': 'promoted' | 'discarded'}`; else None.

- [ ] **Step 1: Write the failing tests** — `tests/test_document_store.py`, one test per behaviour bullet above, using
  `store` (disposable DB) and the resource helpers of `tests/test_documents.py` (environments, services, servers, code
  projects, deployments). Include at least:

```python
def test_promote_archives_the_live_content_keeps_the_id_and_deletes_every_draft(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        prov = Provenance('person', None)
        live = write_live(conn, project, prov, document_id=None, slug='runbook', folder=None,
                          content=content('# One'), snapshot=EMPTY, origin='manual', revision=None)
        first = create_draft(conn, project, prov, content('# Two'), origin='manual', document_id=live['id'])
        other = create_draft(conn, project, prov, content('# Other'), origin='manual', document_id=live['id'])
        result = promote_draft(conn, project, None, first, revision=1, confirm_stale=False)
        row = conn.execute('SELECT * FROM ka.documents WHERE id=%s', (live['id'],)).fetchone()
        assert (result['document_id'], result['version'], result['deleted_drafts']) == (live['id'], 2, 2)
        assert row['markdown'] == '# Two' and row['from_draft'] and not row['is_draft']
        assert conn.execute('SELECT markdown FROM ka.document_archive WHERE document_id=%s AND version=1',
                            (live['id'],)).fetchone()['markdown'] == '# One'
        assert conn.execute('SELECT count(*) AS n FROM ka.documents WHERE id = ANY(%s)',
                            ([first, other],)).fetchone()['n'] == 0


def test_unticking_a_built_for_link_is_refused_and_nothing_changes(store):
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        env = environment(conn, project, 'Prod')
        live = write_live(conn, project, Provenance('person', None), document_id=None, slug='prod', folder=None,
                          content=content('# Prod'), snapshot=EMPTY, origin='manual', revision=None,
                          links={'environment_ids': [env]})
        with pytest.raises(HTTPException) as error:
            save_links(conn, project, live['id'], {'environment_ids': []}, Provenance('person', None))
        assert error.value.status_code == 409
        assert error.value.detail == "This document was built for environment Prod; that link can't be removed."
        assert conn.execute('SELECT count(*) AS n FROM ka.document_resources WHERE removed_at IS NULL').fetchone()['n'] == 1
```

  plus: linked links are added/removed with history (`removed_at`, `removed_by`), unchanged rows keep `created_at`;
  a brand-new draft's links are built-for with `added_how='agent_run'` and `session_id`; promote of a brand-new draft
  keeps id and links; promote of a stale draft is 409 `STALE_BASE` and works with `confirm_stale=True`; two
  concurrent promotes of different drafts of one document (two connections, threads): one succeeds, the other gets
  404 (draft deleted) — never a second archive row for the same version; `update_draft` / `discard_draft` of a
  deleted draft → 404; `create_draft` with a folder+slug naming a live document makes a draft of it;
  `draft_outcome` after promote and after discard; `versions` order and `live` flag; a run's draft sets
  `agent_sessions.last_draft_id`.

- [ ] **Step 2: Run them to see them fail**

Run: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings tests/test_document_store.py`
Expected: ImportError (`knowledge_admin.documents.store` has no such names).

- [ ] **Step 3: Implement `documents/store.py`** to the interfaces and behaviour above. Move `build_snapshot` out of
  `save_document` (api.py lines 323-378) unchanged in behaviour; read the previous snapshot from the live row instead
  of `document_versions`. Use `hashlib.sha256(markdown.encode('utf-8')).hexdigest()` for `content_hash`. Keep SQL
  identifiers fixed (column names come from `LINK_COLUMNS`, never from requests).

- [ ] **Step 4: Run the tests** (same command) — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/documents/store.py services/admin-api/tests/test_document_store.py
git commit -m "feat(documents): one lifecycle for live documents, drafts, archive and links"
```

---

### Task 3: Documents API on the new model

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/documents/api.py` (save_document, export_input, backfill_exports,
  export_conflict, list/get/get_version/bundle/export/delete routes)
- Modify: `services/admin-api/src/knowledge_admin/documents/folders.py`
- Test: `services/admin-api/tests/test_documents.py`, `tests/test_documentation_folders.py`,
  `tests/test_document_resources_api.py` (new)

**Interfaces:**
- Consumes: Task 2 (`write_live`, `save_links`, `build_snapshot`, `versions`, `version`, `resource_links`,
  `resource_history`, `Provenance`).
- Produces:
  - `save_document(conn, project, actor, value: DocumentInput, document_id=None, folder=None, origin='manual', *,
    how='person', session_id=None) -> {'id', 'revision', 'version'}` — same name and arguments as today plus the two
    keyword-only provenance arguments; it builds the snapshot and calls `write_live` (create → links `built_for`;
    edit → links `linked`, built-for refusal 409). The `version_id` key is gone (callers in Tasks 4–5 change).
  - `GET /api/documentation/documents/{id}` → the `document_details` row plus `markdown`, `source_snapshot`,
    `versions` (from `store.versions`), `drafts` (from `document_drafts_view WHERE document_id = id ORDER BY created_at
    DESC`: id, title, origin as `agent`|`manual`, session_id, author, edited, base_version, stale, created_at,
    updated_at), `resource_links`. `current_version` and `current_version_id` are gone.
  - `GET /api/documentation/documents/{id}/versions/{version:int}` → that version's content (`store.version`), 404
    otherwise.
  - `GET /api/documentation/documents/{id}/resource-history` → `{'history': store.resource_history(...)}`.
  - List: `has_drafts` uses `draft_count > 0`; filters unchanged (`document_details` arrays).

- [ ] **Step 1: Write/adjust failing tests.** In `tests/test_documents.py` replace reads of `current_version`,
  `current_version_id`, `version_id` and the old tables (lines 164, 203, 207, 348, 841 per the map) with the new
  fields; add to `tests/test_document_resources_api.py`:
  - `POST /api/documentation/documents` with `environment_ids=[env]` → `GET` shows `resource_links` with
    `connection='built_for'`, `added_how='person'`;
  - `PUT` that unticks it → 409 with the built-for message and links unchanged (Review Focus 3);
  - `PUT` that adds a service then removes it → `resource-history` lists both changes, newest first, with
    `removed_by`;
  - `PUT` that changes only links → `version` unchanged, `revision + 1`; `PUT` that changes markdown → `version + 1`,
    the old content at `GET …/versions/1`;
  - the Documents list and `GET /folders` never count or show draft rows (create a brand-new draft and a draft of a
    live document directly with `store.create_draft`) (Review Focus 4); `backfill_exports` exports live documents only.
- [ ] **Step 2: Run them to see them fail**
  Run: `uv run --frozen pytest -q -p no:warnings tests/test_documents.py tests/test_document_resources_api.py tests/test_documentation_folders.py`
- [ ] **Step 3: Implement.** `export_input` reads the live row (`markdown`, `source_snapshot` on `ka.documents`,
  `AND NOT is_draft`); `backfill_exports` and `export_conflict` select from `ka.documents` rows with `slug IS NOT
  NULL` (live and brand-new drafts reserve export names — a brand-new draft will export there when promoted);
  `folders.py` counts and lists live documents (`ka.live_documents` / `document_details`), `last_generated_at` =
  `max(version_created_at)` over live rows with `origin='generated'` and archive rows with `origin='generated'`
  (`created_at`), code-project counts from active `document_resources`.
- [ ] **Step 4: Run the tests** (same command) — Expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(documents): documents API reads and writes the new model; built-for links refuse removal"`

---

### Task 4: Drafts API, document tools and a run's draft

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/documents/drafts.py` (SUMMARY/DETAIL, LINKED, connections,
  sources_of, add_parent_source, live_content, document_named, create_draft → store, open_draft, promote_draft →
  store, discard → store, SESSION_DRAFT, record_run_sources, refresh_run_sources_safely, routes)
- Modify: `services/admin-api/src/knowledge_admin/capabilities/document_tools.py` (LIVE, WORKING, NEW_WORKING,
  NEW_PREVIOUS, `_save*`, `_write`, `_note`, `get_document`)
- Modify: `services/admin-api/src/knowledge_admin/sessions/rows.py`, `sessions/api.py` (LIST `draft_id`),
  `sessions/hub.py` (STATE)
- Test: `tests/test_document_drafts.py`, `tests/test_document_draft_reads.py`, `tests/test_document_edit_tools.py`,
  `tests/test_document_tool_binding.py`, `tests/test_new_document_runs.py`, `tests/test_document_drafts_worker.py`,
  fixtures `tests/drafts_world.py`, `tests/document_tools_world.py`

**Interfaces:**
- Consumes: Task 2 (`create_draft`, `update_draft`, `promote_draft`, `discard_draft`, `draft_outcome`,
  `Provenance`, `STALE_MESSAGE`), Task 3 (`save_document` returns `version`, no `version_id`).
- Produces:
  - Draft JSON (`DraftSummary`/`DraftDetail`, read from `ka.document_drafts_view`): `id, document_id (draft_of or
    null), slug, folder, title, origin ('agent' when session_id else 'manual'), session_id, session_title,
    session_purpose, author, edited, revision, base_version, live_version, stale, created_at, updated_at`; detail adds
    `description, purpose, audience, tags, markdown, resource_ids` (the view's arrays as `{field: [...]}`), `sources`,
    `live_markdown`, `document_title`. Removed: `status`, `promoted_version_id`, `decided_at`, `base_version_id`.
  - `POST /drafts/{id}/promote` → `{document_id, version, revision, deleted_drafts, export_error}`;
    `POST /drafts/{id}/discard` → `{}`; both 404 when the draft is gone.
  - `GET /api/documentation/drafts?new=true` (brand-new drafts) and new resource filters
    `?environment_id=|service_id=|server_id=|code_project_id=|deployment_id=` (drafts whose view arrays contain it).
  - Session JSON keeps `document_draft {id, document_id, status}` = `store.draft_outcome(conn, s.last_draft_id)`;
    run list `draft_id` = `last_draft_id` when that draft still exists.
  - `document_sources`: owner `document_id` (draft or live) / `archive_id`; `sources_of(conn, document_id)`.

- [ ] **Step 1: Adjust the fixtures and failing tests.** `drafts_world.version_of` reads `version` from
  `GET …/documents/{id}`; `run_drafts` reads `ka.document_drafts_view`; replace `status`/`promoted_version_id`
  assertions with: promoted → the draft row is gone, the document's `version` went up, `draft_outcome` says
  `promoted`; discarded → row gone, outcome `discarded`. Add tests:
  - promote deletes the document's other drafts and reports `deleted_drafts` (UI warning relies on it);
  - `GET /drafts?environment_id=` finds a brand-new draft built for that environment and a draft of a document linked
    to it, and not other drafts;
  - a run saves, a person promotes its draft, the run saves again → a new draft of the new live version is created and
    the tool result carries the PROMOTED note (Review Focus 5); same with discard → DISCARDED note; a person edits the
    run's draft → EDITED note (as today);
  - a run's `get_document` never returns another run's draft and never treats a draft id as a document id
    ("This document is not available in this run.").
- [ ] **Step 2: Run them to see them fail**
  Run: `uv run --frozen pytest -q -p no:warnings tests/test_document_drafts.py tests/test_document_draft_reads.py tests/test_document_edit_tools.py tests/test_document_tool_binding.py tests/test_new_document_runs.py tests/test_document_drafts_worker.py`
- [ ] **Step 3: Implement.** The run's working draft is `SELECT … FROM ka.documents WHERE is_draft AND session_id = %s
  AND draft_of = %s AND NOT edited ORDER BY created_at DESC LIMIT 1 FOR UPDATE` (brand-new: `draft_of IS NULL AND
  folder IS NOT DISTINCT FROM %s AND slug = %s`, or the document that folder+slug now names); the previous-draft note
  uses `draft_outcome(conn, session.last_draft_id)` (`promoted` → PROMOTED, `discarded` → DISCARDED, row with
  `edited` → EDITED). `_run_links` stays (chosen resources) and is passed as `links` to `create_draft` with
  `Provenance('agent_run', actor, session_id)`. Promote/discard routes keep their locking order (project `FOR NO KEY
  UPDATE`, then the run's session, then the draft) and export after commit as today.
- [ ] **Step 4: Run the tests** (same command) — Expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(documents): drafts as document rows; promote clears the other drafts; runs track their last draft"`

---

### Task 5: Remaining readers, fixtures, docs — full backend suite green

**Files:**
- Modify: `servers/discovery.py` (free_slug, clash check, new document → `save_document(…, how='discovery')`, update,
  `version` instead of `version_id`), `capabilities/discovery.py`, `capabilities/inventory.py`,
  `servers/inventory_map.py`, `servers/inventory_links_api.py` (attach refuses a draft id with 404 `Document not
  found`), `sessions/start.py` (target_document, document_access title from `ka.documents` live row, existence checks
  live only), `app.py` (backfill unchanged call)
- Modify tests per the map's "Tests touching these tables" list (`tests/discovery_world.py`,
  `test_discovery_review.py`, `test_delete_agent_history.py`, `test_sessions_start.py`, `test_inventory_documents_api.py`,
  `test_document_publish_migration.py` …) — replace old-table SQL with `store`/API calls or the new tables.
- Modify docs: `docs/documentation.md` (documents, drafts, archive, links: built-for vs linked, history, drafts by
  resource, promote deletes other drafts, versions by number), `docs/migrations.md` (0104 and its rollback rule),
  `docs/agents.md` (a run's draft after promote/discard), `deploy/README.md` upgrade note (apply 0104; rebuild
  admin-api, agent-session-worker, repository-worker, admin-web; rollback refuses once links have provenance).

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: a green full backend suite.

- [ ] **Step 1: Run the full suite and list failures**
  Run: `uv run --frozen pytest -q -p no:warnings 2>&1 | grep -E "^(FAILED|ERROR)"`
- [ ] **Step 2: Fix each failing module and its tests**, one module at a time, re-running that module's tests. Add:
  - accepting a discovery proposal for a new document creates a live document whose links are `built_for` with
    `added_how='discovery'` (its service, else its server);
  - attaching a draft to an inventory entry is refused (404), attaching a live document works (Review Focus 4).
- [ ] **Step 3: Full suite** — Run: `uv run --frozen pytest -q -p no:warnings` — Expected: only the known failures.
- [ ] **Step 4: Format and commit** — `uv run --frozen ruff format` then
  `git commit -m "feat(documents): discovery, inventory and sessions read live documents; docs for the new model"`

---

### Task 6: UI — drafts list, promote warning, drafts by resource, locked links, link history, versions by number

**Files:**
- Modify: `services/admin-ui/app/features/documents/api.ts` (types: `DocumentDetail` without `current_version` /
  `current_version_id`, with `markdown`, `source_snapshot`, `versions: {version, title, origin, created_at,
  from_draft, live}[]`, `drafts`, `resource_links`; `getDocumentVersion(id, version: number)`;
  `getResourceHistory(id)`), `drafts.ts` (types without `status`/`promoted_version_id`/`decided_at`/
  `base_version_id`; `promoteDraft` result with `deleted_drafts`; `getDrafts(filter)` with resource filters)
- Modify: `DocumentEditorView.tsx` (init from `document.markdown` / `source_snapshot`; Version history by number with
  `live` flag; Linked resources locked items; Link history section), `DocumentResourceSelection.tsx` (locked +
  "Built for this document" via `ResourcePicker`'s existing locked state), `DocumentDrafts.tsx` (list newest first;
  Promote/Discard; warning `Promote and delete {n} other draft(s)?` when n > 0), `DraftView.tsx`, `DraftPageView.tsx`,
  `AgentDraftBar.tsx` (status from the session's `document_draft`), `DocumentsView.tsx` (Drafts tab/filter listing
  drafts with the same resource filters), `features/sessions/SessionView.tsx` (document_draft unchanged shape)
- Test: the UI tests listed in the map plus `documentLinks.test.tsx` (new)

**Interfaces:**
- Consumes: Tasks 3–4 API shapes above.

- [ ] **Step 1: Write failing UI tests**:
  - Linked resources shows a built-for environment ticked, disabled, with "Built for this document"; a linked service
    toggles;
  - a 409 on save shows the message under Linked resources and reloads the document (Review Focus 3);
  - Link history lists `Added Prod (environment) · built for · agent run · <date> · <who>` and a removed line;
  - the Drafts tab lists drafts newest first; Promote with two other drafts asks `Promote and delete 2 other
    draft(s)?`, with none promotes without asking;
  - Documents → Drafts filtered by an environment calls `GET /api/documentation/drafts?environment_id=…` and lists
    the results;
  - Version history opens `GET …/versions/1` and marks the live version.
- [ ] **Step 2: Run them to see them fail** — `pnpm exec vitest run <the new and changed test files>`
- [ ] **Step 3: Implement**, then update the existing UI tests' fixtures (`current_version` → `markdown`,
  `from_draft`/`versions` shape, `document_draft`, `resource_ids`).
- [ ] **Step 4: Run all UI checks** — `pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm exec react-router routes
  && pnpm format:check` — Expected: pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(admin-ui): drafts list and promote warning, drafts by resource, locked built-for links, link history"`
