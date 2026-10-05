# RAG Collections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the RAG folder tree with named collections. Each collection maps
to one folder anywhere in `DATA_ROOT`, has its own settings, and is indexed in
place. Each one shows when it was last synced, when its folder last changed, and a
Refresh action.

**Architecture:**
- **New table `ka.rag_collections`.** Indexed files, jobs and agent grants move from
  `rag_folders` to collections. Existing data is converted, so search keeps working
  without reprocessing.
- **The RAG service** gets a small `collections.py` module for area roots, safe
  paths, glob matching and folder walks. A per-collection sync job and
  collection-scoped search are built on it.
- **Consumers switch from folder selectors to collection names:** admin-api agents
  and Reader, docs-wiki, chat-ui, and admin-ui.
- **Later parts:** Jira export targets a collection; documents export current-only;
  publish-to-RAG and the legacy SQLite collections API are removed.

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations;
- FastAPI and psycopg3 (`services/rag`, `services/admin-api`, `services/docs-wiki`);
- React Router 7 with shadcn/ui and Vitest (`services/admin-ui`,
  `services/docs-wiki-ui`, `packages/chat-ui`);
- pytest. RAG tests run a real PostgreSQL container from the
  `knowledge-agent-v3-postgres:17.10-vector0.8.6` image.

**Spec:** `docs/superpowers/specs/2026-10-05-rag-collections-design.md`

## Global Constraints

- Collection `name`: `^[a-z0-9]+(-[a-z0-9]+)*$`, at most 60 characters, unique
  per project among non-deleted collections.
- `description`: at most 2000 characters.
- `source_path`: relative to `DATA_ROOT`. Its first segment is one of `code`,
  `code-index`, `rag-data`, `generated-documents`, `docs`. Each later segment
  matches `[A-Za-z0-9_-][A-Za-z0-9._-]*`, so `.` and `..` are impossible.
- `search_mode`: `full_text` | `embedding` | `hybrid`. Default `full_text`.
- `include` defaults to `{**/*.md,**/*.markdown,**/*.txt}`; `exclude` defaults to
  `{}`; each list holds at most 50 patterns.
- `chunk_size` 200..12000 (default 2000); `chunk_overlap` 0..2000 (default 200),
  and must be smaller than `chunk_size`.
- Container roots per area, which already exist in admin-api's `storage.py`:
  - `code` → `CODE_ROOT`
  - `code-index` → `CODE_INDEX_ROOT`
  - `rag-data` → `RAG_SOURCE_ROOT`
  - `generated-documents` → `DOCUMENT_OUTPUT_ROOT`
  - `docs` → `BOOTSTRAP_DOCS_ROOT`
- Host display paths per area: `CODE_PATH`, `CODE_INDEX_PATH`, `RG_DATA`,
  `GENERATED_DOCS_PATH`, `DOCS_PATH`.
- Only collections in the `rag-data` area accept file writes, uploads and edits.
- Always skipped while walking:
  - symlinks and special files (counted);
  - `.git` and `node_modules` directories;
  - files named `.env*`.
- Skipped during parsing: non-UTF-8 files, files containing a NUL byte, and files
  larger than the global `max_file_bytes` (all counted).
- Limits per walk or sync: 20,000 files and 200 MB.
- At most one active (`queued`/`running`/`waiting_input`) job per collection.
- Search fuses rankings with RRF, k=60.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12: no `PurePath.full_match`. Glob matching is
  implemented with the regex translation in Task 2.

## Review Focus

1. **A code collection with thousands of files, symlinks and binary files** must
   still sync. Symlinks and binaries are skipped and counted, and the job result
   reports the counts. Pinned in Task 4 (`test_sync_skips_links_binaries_and_counts_them`).
2. **Deleting a file in a folder** must show the collection as needing a refresh,
   because the directory mtime changes, and the sync then removes that file from
   search. Pinned in Task 3 (`test_deleting_a_file_marks_collection_stale`) and
   Task 4 (`test_sync_removes_deleted_and_unmatched_files`).
3. **Changing only the include patterns** must mark the collection stale (revision ≠
   synced_revision), and the next sync must drop files that no longer match.
   Pinned in Task 3 (`test_settings_change_marks_collection_stale`) and Task 4.
4. **Searching a collection whose search mode is `embedding` while embeddings are
   off or no model is set** must fall back to full text and list the collection in
   `fallback`, not return a 409. Pinned in Task 5 (`test_semantic_collection_without_model_falls_back`).
5. **An older published agent version** with `documents.folders` (dotted selectors)
   must still search the right collections. Pinned in Task 7
   (`test_legacy_folder_config_maps_to_collections`) and Task 8.

---

# Part A — collections end to end (Tasks 1–13, merge together)

### Task 1: Migration 0063 — collections, converted data, jobs and grants

**Files:**
- Create: `db/migrations/0063_rag_collections.up.sql`
- Create: `db/migrations/0063_rag_collections.down.sql`
- Modify: `db/tests/run.sh` (rolled-back version pin `61` → `62`)
- Test: `services/admin-api/tests/test_rag_collections_migration.py`

**Interfaces:**
- Produces:
  - table `ka.rag_collections` with the columns listed in the spec;
  - `ka.rag_files(collection_id NOT NULL, path relative)` with unique
    `(collection_id, path)`; there are no `folder_id` or `kind` columns any more;
  - `ka.jobs.rag_collection_id` and kind `rag_collection_sync`;
  - unique index `rag_collection_one_active_job`;
  - `ka.agent_collections(agent_id text, collection_id uuid)`;
  - `ka.agents.all_collections` (renamed from `all_folders`); `include_subfolders`
    is dropped;
  - `ka.rag_workspaces.settings` = `{"max_file_bytes": n}` only.

- [ ] **Step 1: Write the failing migration test**

Model it on `services/admin-api/tests/test_document_publish_migration.py`: same
fixtures and the same way of applying migrations up to a version. First run
migrations up to 0062, seed legacy data, then apply 0063 and assert.

```python
"""0063 converts RAG folders into collections without reprocessing."""
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'


def apply(conn, upto: int, start: int = 1):
    for path in sorted(MIGRATIONS.glob('*.up.sql')):
        number = int(path.name[:4])
        if start <= number <= upto:
            conn.execute(path.read_text(), prepare=False)


@pytest.fixture
def legacy(empty_database):
    """A database at 0062 with folders, indexed files, vectors, grants and jobs."""
    conn = empty_database
    apply(conn, 62)
    project = conn.execute("SELECT id FROM ka.projects LIMIT 1").fetchone()
    if project is None:
        project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('t','T') RETURNING id").fetchone()
    project = project['id']
    conn.execute("""INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,
        '{"chunk_size":1500,"chunk_overlap":100,"max_file_bytes":3000000,"extensions":[".md",".txt"],"embedding_enabled":true}')
        ON CONFLICT (project_id) DO UPDATE SET settings=EXCLUDED.settings""", (project,))
    ids = {}
    for path in ('jira', 'demo', 'demo/payments', 'Old_Stuff'):
        ids[path] = conn.execute("INSERT INTO ka.rag_folders(project_id,path,description) VALUES (%s,%s,%s) RETURNING id",
                                 (project, path, f'About {path}')).fetchone()['id']
    actor = conn.execute("INSERT INTO ka.principals(issuer,subject) VALUES ('t','t') RETURNING id").fetchone()['id']
    job = conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_folder_id,requested_by,input,status,finished_at)
        VALUES (%s,'rag_folder_index',%s,%s,'{}','succeeded',now()) RETURNING id""", (project, ids['jira'], actor)).fetchone()['id']
    queued = conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_folder_id,requested_by,input)
        VALUES (%s,'rag_folder_index',%s,%s,'{}') RETURNING id""", (project, ids['demo'], actor)).fetchone()['id']
    for folder, path in (('jira', 'jira/DEMO-1.md'), ('demo/payments', 'demo/payments/operations.md')):
        file_id = conn.execute("""INSERT INTO ka.rag_files(project_id,folder_id,path,kind,content_hash,processing_hash,embedding_profile,job_id,indexed_at)
            VALUES (%s,%s,%s,'file',repeat('a',64),repeat('b',64),'',%s,'2026-10-01T10:00:00Z') RETURNING id""",
            (project, ids[folder], path, job)).fetchone()['id']
        conn.execute("INSERT INTO ka.rag_chunks(project_id,file_id,position,text,input_hash) VALUES (%s,%s,0,'Retry policy',repeat('c',64))", (project, file_id))
    conn.execute("""INSERT INTO ka.rag_files(project_id,folder_id,path,kind,content_hash,processing_hash,embedding_profile,job_id)
        VALUES (%s,%s,'demo','folder',repeat('d',64),repeat('b',64),'',%s)""", (project, ids['demo'], job))
    conn.execute("INSERT INTO ka.agent_knowledge_folders(agent_id,folder_id) VALUES ('search',%s),('search',%s)", (ids['demo/payments'], ids['jira']))
    return conn, project, ids, queued


def test_folders_become_collections_and_index_survives(legacy):
    conn, project, _, queued = legacy
    apply(conn, 63, start=63)
    rows = {r['name']: r for r in conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s", (project,)).fetchall()}
    assert set(rows) == {'jira', 'demo', 'old-stuff', 'documents'}
    assert rows['jira']['source_path'] == 'rag-data/jira' and rows['jira']['description'] == 'About jira'
    assert rows['old-stuff']['source_path'] == 'rag-data/Old_Stuff'
    assert rows['jira']['include'] == ['**/*.md', '**/*.txt'] and rows['jira']['embeddings_enabled'] is True
    assert (rows['jira']['chunk_size'], rows['jira']['chunk_overlap']) == (1500, 100)
    assert rows['jira']['last_synced_at'] is not None and rows['jira']['synced_revision'] == rows['jira']['revision']
    assert rows['documents']['source_path'] == 'generated-documents'
    assert rows['documents']['exclude'] == ['**/v[0-9]*/**'] and rows['documents']['last_synced_at'] is None
    files = {r['path']: r['collection_id'] for r in conn.execute("SELECT path,collection_id FROM ka.rag_files").fetchall()}
    assert files == {'DEMO-1.md': rows['jira']['id'], 'payments/operations.md': rows['demo']['id']}
    assert conn.execute("SELECT count(*) AS n FROM ka.rag_chunks").fetchone()['n'] == 2
    assert conn.execute("SELECT settings FROM ka.rag_workspaces WHERE project_id=%s", (project,)).fetchone()['settings'] == {'max_file_bytes': 3000000}
    assert conn.execute("SELECT status FROM ka.jobs WHERE id=%s", (queued,)).fetchone()['status'] == 'cancelled'


def test_grants_and_job_rules(legacy):
    conn, project, _, _ = legacy
    apply(conn, 63, start=63)
    granted = {r['name'] for r in conn.execute("""SELECT c.name FROM ka.agent_collections a JOIN ka.rag_collections c ON c.id=a.collection_id
        WHERE a.agent_id='search'""").fetchall()}
    assert granted == {'demo', 'jira'}
    agent = conn.execute("SELECT * FROM ka.agents WHERE id='search'").fetchone()
    assert 'all_collections' in agent and 'all_folders' not in agent and 'include_subfolders' not in agent
    collection = conn.execute("SELECT id FROM ka.rag_collections WHERE name='jira'").fetchone()['id']
    conn.execute("INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input) VALUES (%s,'rag_collection_sync',%s,'{}')", (project, collection))
    with pytest.raises(Exception, match='rag_collection_one_active_job'):
        conn.execute("INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input) VALUES (%s,'rag_collection_sync',%s,'{}')", (project, collection))


def test_rollback_restores_folders(legacy):
    conn, project, ids, _ = legacy
    apply(conn, 63, start=63)
    conn.execute((MIGRATIONS / '0063_rag_collections.down.sql').read_text(), prepare=False)
    paths = {r['path']: r['folder_path'] for r in conn.execute("""SELECT f.path, d.path AS folder_path FROM ka.rag_files f
        JOIN ka.rag_folders d ON d.id=f.folder_id""").fetchall()}
    assert paths == {'jira/DEMO-1.md': 'jira', 'demo/payments/operations.md': 'demo'}
    assert {r['folder_id'] for r in conn.execute("SELECT folder_id FROM ka.agent_knowledge_folders WHERE agent_id='search'").fetchall()} == {ids['jira'], ids['demo']}
    settings = conn.execute("SELECT settings FROM ka.rag_workspaces WHERE project_id=%s", (project,)).fetchone()['settings']
    assert settings['max_file_bytes'] == 3000000 and settings['chunk_size'] == 1500 and settings['embedding_enabled'] is True
```

Before writing the fixture, check how `test_document_publish_migration.py` gets
an empty database (it may be named `fresh_database`, `database` or `store`) and use
that fixture as `empty_database`. On rollback, files that were in subfolders map to
the top-level folder; the spec accepts that.

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_rag_collections_migration.py -q`
Expected: FAIL, because `0063_rag_collections.up.sql` does not exist yet.

- [ ] **Step 3: Write `0063_rag_collections.up.sql`**

```sql
SET search_path = ka, public;

CREATE TABLE rag_collections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES rag_workspaces(project_id),
    name text NOT NULL CHECK (name ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(name) <= 60),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 2000),
    source_path text NOT NULL CHECK (length(source_path) <= 500 AND source_path ~
        '^(code|code-index|rag-data|generated-documents|docs)(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$'),
    search_mode text NOT NULL DEFAULT 'full_text' CHECK (search_mode IN ('full_text','embedding','hybrid')),
    embeddings_enabled boolean NOT NULL DEFAULT false,
    include text[] NOT NULL DEFAULT '{**/*.md,**/*.markdown,**/*.txt}' CHECK (cardinality(include) BETWEEN 1 AND 50),
    exclude text[] NOT NULL DEFAULT '{}' CHECK (cardinality(exclude) <= 50),
    chunk_size integer NOT NULL DEFAULT 2000 CHECK (chunk_size BETWEEN 200 AND 12000),
    chunk_overlap integer NOT NULL DEFAULT 200 CHECK (chunk_overlap BETWEEN 0 AND 2000),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    synced_revision bigint,
    last_synced_at timestamptz,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (chunk_overlap < chunk_size),
    UNIQUE (id, project_id)
);
CREATE UNIQUE INDEX rag_collections_active_name ON rag_collections(project_id, name) WHERE deleted_at IS NULL;
COMMENT ON TABLE rag_collections IS 'Searchable unit: one folder under DATA_ROOT indexed in place with its own settings.';

-- One collection per live top-level folder; names are slugs of the folder name.
WITH tops AS (
    SELECT d.project_id, d.path, d.description,
           trim(both '-' from lower(regexp_replace(d.path, '[^A-Za-z0-9]+', '-', 'g'))) AS slug
    FROM rag_folders d WHERE d.deleted_at IS NULL AND position('/' in d.path) = 0
), named AS (
    SELECT *, CASE WHEN count(*) OVER (PARTITION BY project_id, slug) > 1 OR slug = ''
                   THEN left(coalesce(nullif(slug, ''), 'folder'), 50) || '-' || left(md5(path), 6) ELSE slug END AS name
    FROM tops
)
INSERT INTO rag_collections(project_id, name, description, source_path, embeddings_enabled, include,
                            chunk_size, chunk_overlap, last_synced_at, synced_revision)
SELECT n.project_id, n.name, left(n.description, 2000), 'rag-data/' || n.path,
       coalesce((w.settings->>'embedding_enabled')::boolean, false),
       coalesce((SELECT array_agg('**/*' || e ORDER BY e) FROM jsonb_array_elements_text(w.settings->'extensions') e),
                '{**/*.md,**/*.markdown,**/*.txt}'),
       coalesce((w.settings->>'chunk_size')::int, 2000), coalesce((w.settings->>'chunk_overlap')::int, 200),
       synced.at, CASE WHEN synced.at IS NULL THEN NULL ELSE 1 END
FROM named n JOIN rag_workspaces w ON w.project_id = n.project_id
LEFT JOIN LATERAL (
    SELECT max(f.indexed_at) AS at FROM rag_files f JOIN rag_folders s ON s.id = f.folder_id
    WHERE s.project_id = n.project_id AND (s.path = n.path OR starts_with(s.path, n.path || '/')) AND f.kind = 'file'
) synced ON true;

-- The documentation export folder; old v<n>/ version folders are not indexed.
INSERT INTO rag_collections(project_id, name, description, source_path, embeddings_enabled, exclude)
SELECT w.project_id,
       CASE WHEN EXISTS (SELECT 1 FROM rag_collections c WHERE c.project_id = w.project_id AND c.name = 'documents')
            THEN 'generated-documents' ELSE 'documents' END,
       'Documents from Docs → Documents (current version of each document).', 'generated-documents',
       coalesce((w.settings->>'embedding_enabled')::boolean, false), '{**/v[0-9]*/**}'
FROM rag_workspaces w;

-- Indexed files move into their collection with collection-relative paths.
DELETE FROM rag_files WHERE kind = 'folder';
ALTER TABLE rag_files ADD COLUMN collection_id uuid;
UPDATE rag_files f SET collection_id = c.id, path = substr(f.path, length(split_part(f.path, '/', 1)) + 2)
FROM rag_collections c
WHERE c.project_id = f.project_id AND c.deleted_at IS NULL AND c.source_path = 'rag-data/' || split_part(f.path, '/', 1);
DELETE FROM rag_files WHERE collection_id IS NULL;
ALTER TABLE rag_files DROP CONSTRAINT rag_files_project_id_path_kind_key;
ALTER TABLE rag_files DROP COLUMN folder_id, DROP COLUMN kind;
ALTER TABLE rag_files ALTER COLUMN collection_id SET NOT NULL,
    ADD CONSTRAINT rag_files_collection_fk FOREIGN KEY (collection_id, project_id) REFERENCES rag_collections(id, project_id),
    ADD CONSTRAINT rag_files_collection_path UNIQUE (collection_id, path);

-- Settings that moved to collections leave the workspace.
UPDATE rag_workspaces SET settings = jsonb_build_object('max_file_bytes', coalesce((settings->>'max_file_bytes')::int, 2000000)),
    revision = revision + 1, updated_at = now();

-- Jobs: per-collection sync, one active job per collection; folder jobs stay valid history.
UPDATE jobs SET status = 'cancelled', finished_at = now(), error = 'Replaced by RAG collections; refresh the collection',
    worker_id = NULL, lease_token = NULL, lease_expires_at = NULL
WHERE kind = 'rag_folder_index' AND status IN ('queued', 'running', 'waiting_input');
DROP INDEX rag_one_active_job;
ALTER TABLE jobs ADD COLUMN rag_collection_id uuid,
    ADD CONSTRAINT jobs_rag_collection_fk FOREIGN KEY (rag_collection_id, project_id) REFERENCES rag_collections(id, project_id);
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check, DROP CONSTRAINT jobs_check1;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN
    ('repository_sync','code_index','documentation_generate','jira_sync','server_test','rag_folder_index','rag_collection_sync'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check CHECK (
    num_nonnulls(repository_id, jira_connection_id, server_id, rag_folder_id, rag_collection_id) =
    CASE WHEN kind = 'documentation_generate' THEN 0 ELSE 1 END);
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    kind = 'documentation_generate' OR (kind IN ('repository_sync','code_index') AND repository_id IS NOT NULL)
    OR (kind = 'jira_sync' AND jira_connection_id IS NOT NULL)
    OR (kind = 'server_test' AND server_id IS NOT NULL)
    OR (kind = 'rag_folder_index' AND rag_folder_id IS NOT NULL)
    OR (kind = 'rag_collection_sync' AND rag_collection_id IS NOT NULL));
CREATE UNIQUE INDEX rag_collection_one_active_job ON jobs(rag_collection_id)
    WHERE kind = 'rag_collection_sync' AND status IN ('queued', 'running', 'waiting_input');
CREATE OR REPLACE FUNCTION guard_job_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'job history cannot be deleted' USING ERRCODE = '23514'; END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN RAISE EXCEPTION 'terminal jobs are immutable' USING ERRCODE = '23514'; END IF;
    IF ROW(NEW.project_id, NEW.kind, NEW.repository_id, NEW.jira_connection_id, NEW.server_id, NEW.rag_folder_id, NEW.rag_collection_id)
        IS DISTINCT FROM ROW(OLD.project_id, OLD.kind, OLD.repository_id, OLD.jira_connection_id, OLD.server_id, OLD.rag_folder_id, OLD.rag_collection_id) THEN
        RAISE EXCEPTION 'job targets are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

-- Agents are granted whole collections.
CREATE TABLE agent_collections (
    agent_id text NOT NULL REFERENCES agents(id),
    collection_id uuid NOT NULL REFERENCES rag_collections(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, collection_id)
);
INSERT INTO agent_collections(agent_id, collection_id)
SELECT DISTINCT a.agent_id, c.id FROM agent_knowledge_folders a
JOIN rag_folders f ON f.id = a.folder_id
JOIN rag_collections c ON c.project_id = f.project_id AND c.deleted_at IS NULL
    AND c.source_path = 'rag-data/' || split_part(f.path, '/', 1);
DROP TABLE agent_knowledge_folders;
ALTER TABLE agents RENAME COLUMN all_folders TO all_collections;
ALTER TABLE agents DROP COLUMN include_subfolders;
```

If `rag_files_project_id_path_kind_key` is not the real constraint name, find it
with `\d ka.rag_files` in the test container and use that. The test pins the
behaviour, not the name.

- [ ] **Step 4: Write `0063_rag_collections.down.sql`**

```sql
SET search_path = ka, public;

ALTER TABLE agents ADD COLUMN include_subfolders boolean NOT NULL DEFAULT true;
ALTER TABLE agents RENAME COLUMN all_collections TO all_folders;
CREATE TABLE agent_knowledge_folders (
    agent_id text NOT NULL REFERENCES agents(id), folder_id uuid NOT NULL REFERENCES rag_folders(id) ON DELETE CASCADE,
    PRIMARY KEY(agent_id, folder_id)
);
-- Collections outside rag-data (and new ones) get a folder row so grants and files survive.
INSERT INTO rag_folders(project_id, path, description)
SELECT c.project_id, substr(c.source_path, 10), c.description FROM rag_collections c
WHERE c.deleted_at IS NULL AND c.source_path LIKE 'rag-data/%' AND position('/' in substr(c.source_path, 10)) = 0
  AND NOT EXISTS (SELECT 1 FROM rag_folders d WHERE d.project_id = c.project_id AND d.path = substr(c.source_path, 10) AND d.deleted_at IS NULL);
INSERT INTO agent_knowledge_folders(agent_id, folder_id)
SELECT DISTINCT a.agent_id, d.id FROM agent_collections a JOIN rag_collections c ON c.id = a.collection_id
JOIN rag_folders d ON d.project_id = c.project_id AND d.deleted_at IS NULL AND 'rag-data/' || d.path = c.source_path;
DROP TABLE agent_collections;

DROP INDEX rag_collection_one_active_job;
UPDATE jobs SET status = 'cancelled', finished_at = now(), error = 'RAG collections were rolled back',
    worker_id = NULL, lease_token = NULL, lease_expires_at = NULL
WHERE kind = 'rag_collection_sync' AND status IN ('queued', 'running', 'waiting_input');
CREATE OR REPLACE FUNCTION guard_job_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'job history cannot be deleted' USING ERRCODE = '23514'; END IF;
    IF OLD.status IN ('succeeded', 'failed', 'cancelled') THEN RAISE EXCEPTION 'terminal jobs are immutable' USING ERRCODE = '23514'; END IF;
    IF ROW(NEW.project_id, NEW.kind, NEW.repository_id, NEW.jira_connection_id, NEW.server_id, NEW.rag_folder_id)
        IS DISTINCT FROM ROW(OLD.project_id, OLD.kind, OLD.repository_id, OLD.jira_connection_id, OLD.server_id, OLD.rag_folder_id) THEN
        RAISE EXCEPTION 'job targets are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
-- Collection sync history cannot be represented without its column; such rows block rollback by design.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM jobs WHERE kind = 'rag_collection_sync') THEN
        RAISE EXCEPTION 'RAG collection sync history exists; restore a backup taken before 0063 to roll back';
    END IF;
END $$;
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check, DROP CONSTRAINT jobs_check, DROP CONSTRAINT jobs_check1;
ALTER TABLE jobs DROP COLUMN rag_collection_id;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN
    ('repository_sync','code_index','documentation_generate','jira_sync','server_test','rag_folder_index'));
ALTER TABLE jobs ADD CONSTRAINT jobs_check CHECK (
    num_nonnulls(repository_id, jira_connection_id, server_id, rag_folder_id) =
    CASE WHEN kind = 'documentation_generate' THEN 0 ELSE 1 END);
ALTER TABLE jobs ADD CONSTRAINT jobs_check1 CHECK (
    kind = 'documentation_generate' OR (kind IN ('repository_sync','code_index') AND repository_id IS NOT NULL)
    OR (kind = 'jira_sync' AND jira_connection_id IS NOT NULL)
    OR (kind = 'server_test' AND server_id IS NOT NULL)
    OR (kind = 'rag_folder_index' AND rag_folder_id IS NOT NULL));
CREATE UNIQUE INDEX rag_one_active_job ON jobs(project_id) WHERE kind = 'rag_folder_index' AND status IN ('queued', 'running', 'waiting_input');

UPDATE rag_workspaces w SET settings = jsonb_build_object(
    'max_file_bytes', coalesce((w.settings->>'max_file_bytes')::int, 2000000),
    'chunk_size', coalesce(c.chunk_size, 2000), 'chunk_overlap', coalesce(c.chunk_overlap, 200),
    'extensions', '[".md", ".markdown", ".txt", ".json"]'::jsonb,
    'embedding_enabled', coalesce(c.embeddings_enabled, false)),
    revision = w.revision + 1, updated_at = now()
FROM (SELECT w2.project_id, c2.chunk_size, c2.chunk_overlap, c2.embeddings_enabled FROM rag_workspaces w2
      LEFT JOIN LATERAL (SELECT * FROM rag_collections c3 WHERE c3.project_id = w2.project_id AND c3.deleted_at IS NULL
                         ORDER BY c3.created_at LIMIT 1) c2 ON true) c
WHERE c.project_id = w.project_id;

ALTER TABLE rag_files DROP CONSTRAINT rag_files_collection_path, DROP CONSTRAINT rag_files_collection_fk;
ALTER TABLE rag_files ADD COLUMN folder_id uuid, ADD COLUMN kind text NOT NULL DEFAULT 'file' CHECK (kind IN ('file', 'folder'));
UPDATE rag_files f SET folder_id = d.id, path = substr(c.source_path, 10) || '/' || f.path
FROM rag_collections c JOIN rag_folders d ON d.project_id = c.project_id AND d.deleted_at IS NULL AND 'rag-data/' || d.path = c.source_path
WHERE c.id = f.collection_id;
DELETE FROM rag_files WHERE folder_id IS NULL;
ALTER TABLE rag_files ALTER COLUMN kind DROP DEFAULT, ALTER COLUMN folder_id SET NOT NULL, DROP COLUMN collection_id,
    ADD FOREIGN KEY (folder_id, project_id) REFERENCES rag_folders(id, project_id),
    ADD UNIQUE (project_id, path, kind);
CREATE INDEX rag_files_folder ON rag_files(folder_id);
DROP TABLE rag_collections;
```

- [ ] **Step 5: Pin the rollback version and run the tests**

In `db/tests/run.sh`, change `[ "$rolled_back_version" = 61 ]` to `62`.

Run: `cd services/admin-api && uv run --frozen pytest tests/test_rag_collections_migration.py -q && cd ../.. && bash db/tests/run.sh`
Expected: 3 passed, and every `db/tests/run.sh` check reports PASS.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/0063_rag_collections.*.sql db/tests/run.sh services/admin-api/tests/test_rag_collections_migration.py
git commit -m "feat(db): RAG collections replace folders; convert index, jobs and grants

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: RAG `collections.py` — areas, paths, globs, walks

**Files:**
- Create: `services/rag/src/rag/collections.py`
- Test: `services/rag/tests/test_collections.py`

**Interfaces:**
- Produces, in `rag.collections`:
  - `AREAS: dict[str, tuple[str, str]]`: area → (container env, host env);
  - `source_path(value: str) -> str`: validates and returns, otherwise HTTP 400;
  - `relative_path(value: str) -> str`: a file path inside a collection; HTTP 400
    when invalid;
  - `glob_regex(pattern: str) -> re.Pattern`;
    `matches(path: str, include: list[str], exclude: list[str]) -> bool`;
  - `Walk` dataclass: `files: list[dict]` (`path`, `size`, `mtime`),
    `newest_mtime: float | None`, `skipped: dict[str, int]`, `truncated: bool`;
  - `class Areas(roots: dict[str, Path], host_roots: dict[str, str | None])`, with
    `Areas.from_env()` and these methods:
    - `container_path(source: str) -> Path`
    - `host_path(source: str) -> str | None`
    - `writable(source: str) -> bool`
    - `walk(source, include, exclude, *, limit=MAX_FILES, truncate=False) -> Walk`;
      raises `FolderMissing` when the collection folder is absent
    - `read(source, path, limit) -> bytes`
    - `write(source, path, data: bytes) -> None`
    - `delete(source, path) -> None`;
  - `class FolderMissing(HTTPException)` (status 409);
  - constants `MAX_FILES = 20_000` and `MAX_BYTES = 200_000_000`.

- [ ] **Step 1: Write the failing tests**

```python
"""Collection folders: confinement, glob matching, skips and freshness."""
import os
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from rag.collections import AREAS, Areas, FolderMissing, matches, relative_path, source_path


@pytest.fixture
def areas(tmp_path):
    roots = {area: tmp_path / area for area in AREAS}
    for root in roots.values():
        root.mkdir()
    return Areas(roots, {area: f'/srv/{area}' for area in AREAS}), roots


@pytest.mark.parametrize('value', ['rag-data/jira', 'code/payments/src', 'generated-documents', 'docs'])
def test_valid_source_paths(value):
    assert source_path(value) == value


@pytest.mark.parametrize('value', ['', 'other/x', 'rag-data/../code', 'rag-data/.hidden', '/rag-data', 'rag-data//x', 'code/a b'])
def test_invalid_source_paths(value):
    with pytest.raises(HTTPException):
        source_path(value)


@pytest.mark.parametrize('value', ['../x', 'a/../b', '/abs', 'a\\b', 'a//b', '', 'a/\x00'])
def test_invalid_relative_paths(value):
    with pytest.raises(HTTPException):
        relative_path(value)


def test_glob_matching():
    assert matches('README.md', ['**/*.md'], [])
    assert matches('a/b/c.md', ['**/*.md'], [])
    assert not matches('a/b/c.py', ['**/*.md'], [])
    assert matches('src/app.py', ['src/**/*.py'], [])
    assert not matches('svc/x/v3/README.md', ['**/*.md'], ['**/v[0-9]*/**'])
    assert matches('svc/x/README.md', ['**/*.md'], ['**/v[0-9]*/**'])
    assert not matches('tests/test_a.py', ['**/*.py'], ['tests/**'])
    assert matches('a.txt', ['*.txt'], []) and not matches('d/a.txt', ['*.txt'], [])


def test_walk_filters_skips_and_reports_newest_change(areas):
    areas, roots = areas
    repo = roots['code'] / 'payments'
    (repo / 'src').mkdir(parents=True)
    (repo / '.git').mkdir()
    (repo / 'node_modules').mkdir()
    (repo / 'src' / 'app.py').write_text('print(1)')
    (repo / 'README.md').write_text('# Payments')
    (repo / '.git' / 'config.md').write_text('x')
    (repo / 'node_modules' / 'x.md').write_text('x')
    (repo / '.env.md').write_text('SECRET=1')
    os.symlink(repo / 'README.md', repo / 'link.md')
    os.mkfifo(repo / 'pipe.md')
    old = time.time() - 3600
    os.utime(repo / 'README.md', (old, old))
    walk = areas.walk('code/payments', ['**/*.md', '**/*.py'], [])
    assert [f['path'] for f in walk.files] == ['README.md', 'src/app.py']
    assert walk.skipped == {'links': 1, 'special': 1}
    assert walk.newest_mtime >= (repo / 'src' / 'app.py').stat().st_mtime
    assert areas.container_path('code/payments') == roots['code'] / 'payments'
    assert areas.host_path('code/payments') == '/srv/code/payments'
    assert not areas.writable('code/payments') and areas.writable('rag-data/jira')


def test_deleting_a_file_moves_folder_change_time_forward(areas):
    areas, roots = areas
    folder = roots['rag-data'] / 'jira'
    folder.mkdir()
    (folder / 'A.md').write_text('a')
    (folder / 'B.md').write_text('b')
    old = time.time() - 3600
    for path in (folder, folder / 'A.md', folder / 'B.md'):
        os.utime(path, (old, old))
    before = areas.walk('rag-data/jira', ['**/*.md'], []).newest_mtime
    (folder / 'B.md').unlink()
    assert areas.walk('rag-data/jira', ['**/*.md'], []).newest_mtime > before


def test_walk_limit_and_missing_folder(areas):
    areas, roots = areas
    folder = roots['rag-data'] / 'big'
    folder.mkdir()
    for i in range(5):
        (folder / f'{i}.md').write_text('x')
    with pytest.raises(HTTPException, match='more than 3 files'):
        areas.walk('rag-data/big', ['**/*.md'], [], limit=3)
    walk = areas.walk('rag-data/big', ['**/*.md'], [], limit=3, truncate=True)
    assert walk.truncated and len(walk.files) == 3
    with pytest.raises(FolderMissing):
        areas.walk('rag-data/absent', ['**/*.md'], [])


def test_read_write_delete_confined_to_rag_data(areas):
    areas, roots = areas
    (roots['rag-data'] / 'notes').mkdir()
    areas.write('rag-data/notes', 'sub/a.md', b'# A')
    assert areas.read('rag-data/notes', 'sub/a.md', 100) == b'# A'
    with pytest.raises(HTTPException, match='size limit'):
        areas.read('rag-data/notes', 'sub/a.md', 1)
    areas.delete('rag-data/notes', 'sub/a.md')
    assert not (roots['rag-data'] / 'notes' / 'sub' / 'a.md').exists()
    (roots['code'] / 'repo').mkdir()
    with pytest.raises(HTTPException, match='read-only'):
        areas.write('code/repo', 'a.md', b'x')
    os.symlink(roots['code'], roots['rag-data'] / 'notes' / 'escape')
    with pytest.raises(OSError):
        areas.read('rag-data/notes', 'escape/repo', 100)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest tests/test_collections.py -q`
Expected: collection error, because `rag.collections` does not exist yet.

- [ ] **Step 3: Implement `services/rag/src/rag/collections.py`**

```python
"""Collection folders under DATA_ROOT: confined paths, glob selection and walks."""

import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

AREAS = {
    'code': ('CODE_ROOT', 'CODE_PATH'),
    'code-index': ('CODE_INDEX_ROOT', 'CODE_INDEX_PATH'),
    'rag-data': ('RAG_SOURCE_ROOT', 'RG_DATA'),
    'generated-documents': ('DOCUMENT_OUTPUT_ROOT', 'GENERATED_DOCS_PATH'),
    'docs': ('BOOTSTRAP_DOCS_ROOT', 'DOCS_PATH'),
}
WRITABLE_AREA = 'rag-data'
SOURCE_PATH = re.compile(r'(code|code-index|rag-data|generated-documents|docs)(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*')
SKIPPED_DIRECTORIES = {'.git', 'node_modules'}
MAX_FILES = 20_000
MAX_BYTES = 200_000_000


class FolderMissing(HTTPException):
    def __init__(self, source: str):
        super().__init__(409, f'The collection folder {source} is missing. Create it or change the collection folder.')


def source_path(value: str) -> str:
    if len(value) > 500 or not SOURCE_PATH.fullmatch(value):
        raise HTTPException(400, 'Choose a folder inside code, code-index, rag-data, generated-documents or docs')
    return value


def relative_path(value: str) -> str:
    parts = value.split('/')
    if (not value or len(value) > 1000 or value.startswith('/') or '\\' in value
            or any(part in ('', '.', '..') or len(part) > 255 or any(ord(c) < 32 for c in part) for part in parts)):
        raise HTTPException(400, 'Use a file path inside the collection folder')
    return value


@lru_cache(maxsize=512)
def glob_regex(pattern: str) -> re.Pattern:
    """Translate a path glob: ** spans folders, * and ? stay within one segment, [..] is a character class."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith('**/', i):
            out.append('(?:.*/)?'); i += 3
        elif pattern.startswith('**', i):
            out.append('.*'); i += 2
        elif pattern[i] == '*':
            out.append('[^/]*'); i += 1
        elif pattern[i] == '?':
            out.append('[^/]'); i += 1
        elif pattern[i] == '[':
            end = pattern.find(']', i + 1)
            if end == -1:
                out.append(re.escape('[')); i += 1
            else:
                body = pattern[i + 1:end]
                out.append('[' + ('^' + body[1:] if body.startswith('!') else body) + ']'); i = end + 1
        else:
            out.append(re.escape(pattern[i])); i += 1
    return re.compile(''.join(out))


def matches(path: str, include: list[str], exclude: list[str]) -> bool:
    return (any(glob_regex(p).fullmatch(path) for p in include)
            and not any(glob_regex(p).fullmatch(path) for p in exclude))


@dataclass
class Walk:
    files: list[dict] = field(default_factory=list)
    newest_mtime: float | None = None
    skipped: dict[str, int] = field(default_factory=lambda: {'links': 0, 'special': 0})
    truncated: bool = False


class Areas:
    def __init__(self, roots: dict[str, Path], host_roots: dict[str, str | None]):
        self.roots = {area: Path(root) for area, root in roots.items()}
        self.host_roots = host_roots

    @classmethod
    def from_env(cls):
        roots = {area: Path(os.environ[env]) for area, (env, _) in AREAS.items() if os.environ.get(env)}
        hosts = {area: os.environ.get(host) for area, (_, host) in AREAS.items()}
        return cls(roots, hosts)

    def _split(self, source: str) -> tuple[Path, list[str]]:
        area, *rest = source_path(source).split('/')
        root = self.roots.get(area)
        if root is None:
            raise HTTPException(503, f'The {area} folder is not mounted in the RAG service')
        return root, rest

    def container_path(self, source: str) -> Path:
        root, rest = self._split(source)
        return root.joinpath(*rest)

    def host_path(self, source: str) -> str | None:
        area, _, rest = source.partition('/')
        host = self.host_roots.get(area)
        return None if not host else host.rstrip('/') + ('/' + rest if rest else '')

    @staticmethod
    def writable(source: str) -> bool:
        return source.split('/')[0] == WRITABLE_AREA

    @contextmanager
    def _directory(self, source: str, parts: list[str], *, create: bool = False):
        root, rest = self._split(source)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for index, part in enumerate([*rest, *parts]):
                if create and index >= len(rest):
                    try:
                        os.mkdir(part, mode=0o750, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    def walk(self, source: str, include: list[str], exclude: list[str], *, limit: int = MAX_FILES, truncate: bool = False) -> Walk:
        result = Walk()

        def newer(value: float):
            result.newest_mtime = value if result.newest_mtime is None else max(result.newest_mtime, value)

        def visit(fd: int, prefix: str) -> bool:
            newer(os.fstat(fd).st_mtime)
            for name in sorted(os.listdir(fd)):
                entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                relative = f'{prefix}/{name}' if prefix else name
                if stat.S_ISLNK(entry.st_mode):
                    result.skipped['links'] += 1
                elif stat.S_ISDIR(entry.st_mode):
                    if name in SKIPPED_DIRECTORIES:
                        continue
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        if not visit(child, relative):
                            return False
                    finally:
                        os.close(child)
                elif stat.S_ISREG(entry.st_mode):
                    if name.startswith('.env') or not matches(relative, include, exclude):
                        continue
                    if len(result.files) >= limit:
                        if truncate:
                            result.truncated = True
                            return False
                        raise HTTPException(400, f'This collection selects more than {limit} files; narrow its include patterns')
                    result.files.append({'path': relative, 'size': entry.st_size, 'mtime': entry.st_mtime})
                    newer(entry.st_mtime)
                elif matches(relative, include, exclude):
                    result.skipped['special'] += 1
            return True

        try:
            with self._directory(source, []) as fd:
                visit(fd, '')
        except (FileNotFoundError, NotADirectoryError):
            raise FolderMissing(source) from None
        return result

    def read(self, source: str, path: str, limit: int) -> bytes:
        *parents, name = relative_path(path).split('/')
        with self._directory(source, parents) as fd:
            leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(leaf, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise HTTPException(400, 'Only regular files are supported')
                data = stream.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(400, f'File exceeds the configured size limit: {path}')
        return data

    def write(self, source: str, path: str, data: bytes) -> None:
        if not self.writable(source):
            raise HTTPException(400, 'This collection folder is read-only; only rag-data collections accept files')
        *parents, name = relative_path(path).split('/')
        with self._directory(source, parents, create=True) as fd:
            temporary = f'.upload-{uuid4().hex}'
            try:
                leaf = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=fd)
                with os.fdopen(leaf, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass

    def delete(self, source: str, path: str) -> None:
        if not self.writable(source):
            raise HTTPException(400, 'This collection folder is read-only; only rag-data collections accept files')
        *parents, name = relative_path(path).split('/')
        with self._directory(source, parents) as fd:
            os.unlink(name, dir_fd=fd)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/rag && uv run --frozen pytest tests/test_collections.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/rag/src/rag/collections.py services/rag/tests/test_collections.py
git commit -m "feat(rag): collection folder walking, glob selection and confined file access

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: RAG collections API, freshness and collection-scoped files

**Files:**
- Modify: `services/rag/src/rag/workspace.py`:
  - `Settings` keeps only `max_file_bytes`;
  - `Workspace` takes `areas: Areas` instead of a single root;
  - delete `folder_path`, `file_path`, `directory`, `read`, `ensure_not_deleting`,
    `cleanup_deleted_folders`, `scan`, `discover` and `snapshot`;
  - keep `digest`, `connect`, `ensure`, `settings`, `embedding_model` and `bump`;
  - add `collection(conn, project, collection_id, *, lock=False)` and
    `collection_by_name(conn, project, name)`.
- Modify: `services/rag/src/rag/workspace_api.py`: replace the tree, folders, file
  and jobs endpoints with collection endpoints, as below.
- Modify: `services/rag/src/rag/cli.py`: `serve-workspace` builds
  `Workspace.from_env()`, which now uses `Areas.from_env()`.
- Test: rewrite `services/rag/tests/test_workspace.py` (the folder tests go; the
  fixture builds five area roots) and create `services/rag/tests/test_collections_api.py`.

**Interfaces:**
- Consumes: Task 2 `Areas`, `source_path`, `relative_path`, `FolderMissing`,
  `MAX_FILES`; Task 1 schema.
- Produces the backoffice API under `/backoffice/projects/{project}`, all behind the
  `x-rag-key` header:
  - `GET collections` → `{"collections": [CollectionStatus]}`;
  - `POST collections` with `CollectionInput` → `{"id"}`; 409 on a duplicate name;
  - `GET collections/{id}` → `CollectionStatus`;
  - `PUT collections/{id}` with `CollectionInput` plus `revision` → `CollectionStatus`;
    409 when the revision is stale; bumps `revision`;
  - `DELETE collections/{id}?revision=&confirmed=true` → `{"deleted_files": n}`.
    Index rows and grants are removed, files are kept, and active jobs for the
    collection are cancelled;
  - `POST collections/{id}/sync` with `{"force": false}` → 202 `{"id"}`; 409
    `"A sync is already queued or running for this collection"`;
  - `POST collections/sync-stale` → `{"queued": [names]}`;
  - `GET collections/{id}/files` → `{"files": [{path, size, mtime, indexed_at,
    content_hash, chunks, embedded_chunks, stale}], "truncated", "skipped"}`;
  - `GET collections/{id}/chunks?path=` → `{"chunks": [{position, text, embedded}]}`;
  - `GET file?collection=<name>&path=` → `{collection, path, content, hash}`;
  - `PUT file` with `{collection, path, content, expected_hash}` → `{path, hash}`;
    rag-data collections only;
  - `DELETE file?collection=&path=&expected_hash=`;
  - `GET documents` → `[{collection, path, description, collection_description}]`;
  - `GET jobs` → `{"jobs": [{id, status, input, result, error, created_at,
    started_at, finished_at, collection, collection_id}]}`. Old folder jobs are
    listed with `collection` = the folder path;
  - `POST jobs/{id}/cancel`, for both job kinds;
  - `GET/PUT settings` keep their shapes. `settings` = `{max_file_bytes}`;
    `source_location` is replaced by `areas: [{area, container_root, host_root}]`;
  - `POST test/embedding` requires only a configured model.
- `CollectionStatus` fields:
  - all `rag_collections` columns;
  - `host_path`, `container_path`, `writable`;
  - `folder_present: bool`, `folder_changed_at: str | None` (ISO), `file_count: int`,
    `truncated: bool`, `skipped: {links, special}`;
  - `indexed_files`, `chunks`, `embedded_chunks`;
  - `job: {id, status, error, created_at, finished_at} | None` (the newest job);
  - `needs_refresh: bool`;
  - `status`: `'folder_missing' | 'syncing' | 'failed' | 'needs_refresh' | 'up_to_date'`.

- [ ] **Step 1: Rewrite the `test_workspace.py` fixture and helpers**

Replace `Workspace(tmp_path / "sources", ...)` with:

```python
from rag.collections import AREAS, Areas

    roots = {area: tmp_path / area for area in AREAS}
    for root in roots.values():
        root.mkdir()
    workspace = Workspace(Areas(roots, {area: f"/srv/{area}" for area in AREAS}), workspace_postgres + f" dbname={database}", b"k" * 32)
```

Yield `roots` as an extra element (append it to the tuple and update every
unpacking). Replace the helpers `folder`, `write`, `run` and `search` with:

```python
def collection(env, name="docs", source="rag-data/docs", **settings):
    client, prefix = env[1], env[3]
    result = client.post(prefix + "/collections", json={"name": name, "source_path": source, **settings})
    assert result.status_code == 200, result.text
    return result.json()["id"]


def write(env, path="readme.md", content="# Payments\n\nRetries use exponential backoff.", name="docs"):
    client, prefix = env[1], env[3]
    previous = client.get(prefix + "/file", params={"collection": name, "path": path})
    result = client.put(prefix + "/file", json={"collection": name, "path": path, "content": content,
                                                "expected_hash": previous.json()["hash"] if previous.status_code == 200 else None})
    assert result.status_code == 200, result.text


def sync(env, collection_id, **options):
    workspace, client, _, prefix, _, transport = env[:6]
    result = client.post(prefix + f"/collections/{collection_id}/sync", json=options)
    assert result.status_code == 202, result.text
    job = claim(workspace)
    assert job is not None
    stats = process(workspace, job, transport)
    return stats, client.get(prefix + f"/collections/{collection_id}").json()


def search(env, query="retries", collections=None, mode=None):
    client, project = env[1], env[2]
    body = {"project_id": str(project), "query": query}
    if collections is not None:
        body["collections"] = collections
    if mode is not None:
        body["mode"] = mode
    return client.post("/search", json=body)
```

Delete the folder tests that no longer have a counterpart: folder scopes, folder
descriptions as chunks, folder deletion and cleanup, and the tree. Keep and port
the tests for settings, embedding-model validation, Bedrock embedding and embedding
reuse; they move to Task 4 or stay here if they only touch settings. For example,
`configure(env, ...)` now PUTs `{"revision", "settings": {"max_file_bytes"},
"embedding_model_id"}`.

- [ ] **Step 2: Write the failing API tests in `tests/test_collections_api.py`**

Import the fixture and helpers from `test_workspace`. When the fixture is defined
in that module, use `from tests.test_workspace import env, workspace_postgres,
collection, write, sync`; otherwise move the fixtures into `tests/conftest.py`.

```python
import os
import time

from test_workspace import collection, env, sync, workspace_postgres, write  # noqa: F401


def status(env, collection_id):
    return env[1].get(env[3] + f"/collections/{collection_id}").json()


def test_create_list_and_validate(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    rows = client.get(prefix + "/collections").json()["collections"]
    assert [r["name"] for r in rows] == ["docs"]
    row = rows[0]
    assert row["host_path"] == "/srv/rag-data/docs" and row["writable"] is True
    assert row["status"] == "needs_refresh" and row["needs_refresh"] is True and row["last_synced_at"] is None
    assert client.post(prefix + "/collections", json={"name": "docs", "source_path": "rag-data/docs"}).status_code == 409
    for bad in ({"name": "Docs", "source_path": "rag-data/docs"}, {"name": "x", "source_path": "../etc"},
                {"name": "x", "source_path": "rag-data/x", "chunk_size": 300, "chunk_overlap": 300},
                {"name": "x", "source_path": "rag-data/x", "search_mode": "embedding"}):
        assert client.post(prefix + "/collections", json=bad).status_code == 400, bad
    assert status(env, created)["include"] == ["**/*.md", "**/*.markdown", "**/*.txt"]


def test_missing_folder_is_reported(env):
    created = collection(env, "absent", "rag-data/absent")
    row = status(env, created)
    assert row["status"] == "folder_missing" and row["folder_present"] is False
    result = env[1].post(env[3] + f"/collections/{created}/sync", json={})
    assert result.status_code == 409 and "missing" in result.json()["error"]["message"]


def test_sync_marks_up_to_date_and_file_edit_marks_stale(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    assert row["status"] == "up_to_date" and row["indexed_files"] == 1 and row["chunks"] >= 1
    time.sleep(0.01)
    write(env, content="# Payments\n\nRetries stop after five attempts.")
    assert status(env, created)["status"] == "needs_refresh"


def test_deleting_a_file_marks_collection_stale(env):
    roots = env[6]
    folder = roots["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env, "a.md", "# A")
    write(env, "b.md", "# B")
    sync(env, created)
    old = time.time() - 3600
    for path in (folder, folder / "a.md", folder / "b.md"):
        os.utime(path, (old, old))
    with env[0].connect() as conn:
        conn.execute("UPDATE ka.rag_collections SET last_synced_at=now() - interval '30 minutes' WHERE id=%s", (created,))
    assert status(env, created)["status"] == "up_to_date"
    (folder / "b.md").unlink()
    assert status(env, created)["status"] == "needs_refresh"


def test_settings_change_marks_collection_stale(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    updated = client.put(prefix + f"/collections/{created}", json={
        "name": "docs", "source_path": "rag-data/docs", "include": ["**/*.txt"], "revision": row["revision"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "needs_refresh"
    stale = client.put(prefix + f"/collections/{created}", json={"name": "docs", "source_path": "rag-data/docs", "revision": row["revision"]})
    assert stale.status_code == 409


def test_read_only_area_rejects_writes_and_reads_files(env):
    client, prefix, roots = env[1], env[3], env[6]
    repo = roots["code"] / "payments"
    repo.mkdir()
    (repo / "app.py").write_text("RETRIES = 5\n")
    collection(env, "payments-code", "code/payments", include=["**/*.py"])
    assert client.get(prefix + "/file", params={"collection": "payments-code", "path": "app.py"}).json()["content"] == "RETRIES = 5\n"
    result = client.put(prefix + "/file", json={"collection": "payments-code", "path": "x.py", "content": "x", "expected_hash": None})
    assert result.status_code == 400 and "read-only" in result.json()["error"]["message"]


def test_one_active_sync_per_collection_and_sync_stale(env):
    client, prefix, roots = env[1], env[3], env[6]
    for name in ("a", "b"):
        (roots["rag-data"] / name).mkdir()
        collection(env, name, f"rag-data/{name}")
    first = client.post(prefix + "/collections", json={"name": "c", "source_path": "rag-data/c"}).json()["id"]
    ids = {r["name"]: r["id"] for r in client.get(prefix + "/collections").json()["collections"]}
    assert client.post(prefix + f"/collections/{ids['a']}/sync", json={}).status_code == 202
    assert client.post(prefix + f"/collections/{ids['a']}/sync", json={}).status_code == 409
    assert client.post(prefix + f"/collections/{ids['b']}/sync", json={}).status_code == 202
    queued = client.post(prefix + "/collections/sync-stale").json()["queued"]
    assert queued == []  # a and b are already queued; c's folder is missing, so it is not queued
    assert status(env, first)["status"] == "folder_missing"


def test_delete_keeps_files_and_removes_index(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    _, row = sync(env, created)
    assert client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"]}).status_code == 400
    result = client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"], "confirmed": True})
    assert result.status_code == 200 and result.json()["deleted_files"] == 1
    assert (roots["rag-data"] / "docs" / "readme.md").exists()
    assert client.get(prefix + "/collections").json()["collections"] == []
    assert collection(env) != created  # the name can be reused
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest tests/test_collections_api.py -q`
Expected: FAIL, because the `/collections` routes return 404.

- [ ] **Step 4: Implement the workspace and API changes**

In `workspace.py`:

```python
class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_file_bytes: int = Field(2_000_000, ge=100, le=10_000_000)


class Workspace:
    def __init__(self, areas: Areas, conninfo: str, encryption_key: bytes):
        self.areas = areas
        self.conninfo = conninfo
        self.cipher = AESGCM(encryption_key)

    @classmethod
    def from_env(cls):
        return cls(Areas.from_env(), "", base64.urlsafe_b64decode(os.environ["CREDENTIAL_ENCRYPTION_KEY"]))

    def ensure(self, conn, project: UUID):
        if conn.execute("SELECT id FROM ka.projects WHERE id=%s", (project,)).fetchone() is None:
            raise HTTPException(404, "Unknown project")
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                     (project, Jsonb(Settings().model_dump())))
        self.settings(conn, project)

    def collection(self, conn, project: UUID, collection_id: UUID, *, lock: bool = False) -> dict:
        row = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND id=%s AND deleted_at IS NULL"
                           + (" FOR UPDATE" if lock else ""), (project, collection_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown collection")
        return row

    def collection_by_name(self, conn, project: UUID, name: str) -> dict:
        row = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND name=%s AND deleted_at IS NULL", (project, name)).fetchone()
        if row is None:
            raise HTTPException(404, f"Unknown collection: {name}")
        return row
```

Keep `settings`, `embedding_model` and `bump`. Add a status module function in
`workspace_api.py`. It needs these imports: `from datetime import datetime, timezone`,
`from typing import Annotated, Literal`, `import psycopg`,
`from pydantic import model_validator`, and
`from rag.collections import AREAS, FolderMissing, Walk, relative_path, source_path`.

```python
STATUS_SQL = """SELECT count(DISTINCT f.id) AS indexed_files, count(c.id) AS chunks,
    count(v.input_hash) AS embedded_chunks
    FROM ka.rag_files f LEFT JOIN ka.rag_chunks c ON c.file_id=f.id
    LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile
        AND v.input_hash=c.input_hash AND f.embedding_profile<>''
    WHERE f.collection_id=%s"""


def collection_status(workspace: Workspace, conn, row: dict) -> dict:
    counts = conn.execute(STATUS_SQL, (row["id"],)).fetchone()
    job = conn.execute("""SELECT id,status,error,created_at,finished_at FROM ka.jobs
        WHERE rag_collection_id=%s ORDER BY created_at DESC LIMIT 1""", (row["id"],)).fetchone()
    try:
        walk = workspace.areas.walk(row["source_path"], row["include"], row["exclude"], truncate=True)
        present = True
    except FolderMissing:
        walk, present = Walk(), False
    changed = datetime.fromtimestamp(walk.newest_mtime, timezone.utc) if walk.newest_mtime is not None else None
    synced = row["last_synced_at"]
    needs = present and (synced is None or row["synced_revision"] != row["revision"] or (changed is not None and changed > synced))
    if not present:
        state = "folder_missing"
    elif job is not None and job["status"] in ("queued", "running", "waiting_input"):
        state = "syncing"
    elif job is not None and job["status"] == "failed" and (synced is None or job["created_at"] > synced):
        state = "failed"
    else:
        state = "needs_refresh" if needs else "up_to_date"
    return {**row, **counts, "job": job, "host_path": workspace.areas.host_path(row["source_path"]),
            "container_path": str(workspace.areas.container_path(row["source_path"])),
            "writable": workspace.areas.writable(row["source_path"]), "folder_present": present,
            "folder_changed_at": changed, "file_count": len(walk.files), "truncated": walk.truncated,
            "skipped": walk.skipped, "needs_refresh": bool(needs), "status": state}
```

`CollectionInput`:

```python
GlobPattern = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^/\\\x00][^\\\x00]*$")]


class CollectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=60)
    description: str = Field("", max_length=2000)
    source_path: str
    search_mode: Literal["full_text", "embedding", "hybrid"] = "full_text"
    embeddings_enabled: bool = False
    include: list[GlobPattern] = Field(default_factory=lambda: ["**/*.md", "**/*.markdown", "**/*.txt"], min_length=1, max_length=50)
    exclude: list[GlobPattern] = Field(default_factory=list, max_length=50)
    chunk_size: int = Field(2000, ge=200, le=12000)
    chunk_overlap: int = Field(200, ge=0, le=2000)
    revision: int | None = None

    @model_validator(mode="after")
    def valid(self):
        source_path(self.source_path)
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.search_mode == "embedding" and not self.embeddings_enabled:
            raise ValueError("Turn on embeddings for semantic search, or choose keyword or hybrid search")
        return self
```

Routes (inside `build_api`, after `prefix`):

```python
    COLUMNS = ("name", "description", "source_path", "search_mode", "embeddings_enabled", "include", "exclude", "chunk_size", "chunk_overlap")

    @router.get(prefix + "/collections")
    def list_collections(project: UUID):
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            rows = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name", (project,)).fetchall()
            return {"collections": [collection_status(workspace, conn, row) for row in rows]}

    @router.post(prefix + "/collections")
    def create_collection(project: UUID, payload: CollectionInput):
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            try:
                row = conn.execute(f"INSERT INTO ka.rag_collections(project_id,{','.join(COLUMNS)}) VALUES (%s{',%s' * len(COLUMNS)}) RETURNING id",
                                   (project, *(getattr(payload, c) for c in COLUMNS))).fetchone()
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, "A collection with this name already exists") from None
            return row

    @router.get(prefix + "/collections/{collection_id}")
    def get_collection(project: UUID, collection_id: UUID):
        with workspace.connect() as conn:
            return collection_status(workspace, conn, workspace.collection(conn, project, collection_id))

    @router.put(prefix + "/collections/{collection_id}")
    def update_collection(project: UUID, collection_id: UUID, payload: CollectionInput):
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)
            if payload.revision != row["revision"]:
                raise HTTPException(409, "Collection changed; refresh before saving")
            try:
                row = conn.execute(f"UPDATE ka.rag_collections SET {','.join(c + '=%s' for c in COLUMNS)},revision=revision+1,updated_at=now() WHERE id=%s RETURNING *",
                                   (*(getattr(payload, c) for c in COLUMNS), collection_id)).fetchone()
            except psycopg.errors.UniqueViolation:
                raise HTTPException(409, "A collection with this name already exists") from None
            return collection_status(workspace, conn, row)

    @router.delete(prefix + "/collections/{collection_id}")
    def delete_collection(project: UUID, collection_id: UUID, revision: int, confirmed: bool = False):
        if not confirmed:
            raise HTTPException(400, "Confirm removing this collection from search; its files stay on disk")
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)
            if revision != row["revision"]:
                raise HTTPException(409, "Collection changed; refresh before deleting")
            conn.execute("""UPDATE ka.jobs SET status='cancelled',finished_at=now(),error='Collection deleted',
                worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                WHERE rag_collection_id=%s AND status IN ('queued','running','waiting_input')""", (collection_id,))
            removed = conn.execute("DELETE FROM ka.rag_files WHERE collection_id=%s", (collection_id,)).rowcount
            conn.execute("DELETE FROM ka.agent_collections WHERE collection_id=%s", (collection_id,))
            conn.execute("""DELETE FROM ka.rag_vectors v WHERE v.project_id=%s AND NOT EXISTS (
                SELECT 1 FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id
                WHERE c.project_id=v.project_id AND c.input_hash=v.input_hash AND f.embedding_profile=v.profile)""", (project,))
            conn.execute("UPDATE ka.rag_collections SET deleted_at=now(),updated_at=now() WHERE id=%s", (collection_id,))
        return {"deleted_files": removed}

    def queue_sync(conn, project: UUID, row: dict, actor: str | None, force: bool):
        # A missing folder raises FolderMissing (409) before anything is queued.
        workspace.areas.walk(row["source_path"], row["include"], row["exclude"], truncate=True)
        try:
            with conn.transaction():
                return conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,requested_by,input)
                    VALUES (%s,'rag_collection_sync',%s,%s,%s) RETURNING id""",
                    (project, row["id"], UUID(actor) if actor else None, Jsonb({"force": force}))).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "A sync is already queued or running for this collection") from None

    class SyncInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        force: bool = False

    @router.post(prefix + "/collections/{collection_id}/sync", status_code=202)
    def sync_collection(project: UUID, collection_id: UUID, payload: SyncInput, request: Request):
        with workspace.connect() as conn:
            return queue_sync(conn, project, workspace.collection(conn, project, collection_id), request.headers.get("x-rag-actor"), payload.force)

    @router.post(prefix + "/collections/sync-stale")
    def sync_stale(project: UUID, request: Request):
        queued = []
        with workspace.connect() as conn:
            for row in conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name", (project,)).fetchall():
                if collection_status(workspace, conn, row)["status"] in ("needs_refresh", "failed"):
                    try:
                        queue_sync(conn, project, row, request.headers.get("x-rag-actor"), False)
                        queued.append(row["name"])
                    except HTTPException:
                        pass
        return {"queued": queued}
```

Add the files and chunks listing, plus the file routes:

```python
    @router.get(prefix + "/collections/{collection_id}/files")
    def collection_files(project: UUID, collection_id: UUID):
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id)
            indexed = {r["path"]: r for r in conn.execute("""SELECT f.path,f.indexed_at,f.content_hash,count(c.id) AS chunks,
                count(v.input_hash) AS embedded_chunks FROM ka.rag_files f LEFT JOIN ka.rag_chunks c ON c.file_id=f.id
                LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile AND v.input_hash=c.input_hash AND f.embedding_profile<>''
                WHERE f.collection_id=%s GROUP BY f.id""", (collection_id,)).fetchall()}
        walk = workspace.areas.walk(row["source_path"], row["include"], row["exclude"], truncate=True)
        files = []
        for entry in walk.files:
            known = indexed.pop(entry["path"], None)
            changed = datetime.fromtimestamp(entry["mtime"], timezone.utc)
            files.append({**entry, "indexed_at": known and known["indexed_at"], "content_hash": known and known["content_hash"],
                          "chunks": known["chunks"] if known else 0, "embedded_chunks": known["embedded_chunks"] if known else 0,
                          "stale": known is None or changed > known["indexed_at"]})
        files += [{"path": path, "size": None, "mtime": None, **{k: known[k] for k in ("indexed_at", "content_hash", "chunks", "embedded_chunks")},
                   "stale": True} for path, known in indexed.items()]  # indexed but gone from disk
        return {"files": sorted(files, key=lambda f: f["path"]), "truncated": walk.truncated, "skipped": walk.skipped}

    @router.get(prefix + "/collections/{collection_id}/chunks")
    def collection_chunks(project: UUID, collection_id: UUID, path: str):
        with workspace.connect() as conn:
            workspace.collection(conn, project, collection_id)
            return {"chunks": conn.execute("""SELECT c.position,c.text,(v.input_hash IS NOT NULL) AS embedded
                FROM ka.rag_files f JOIN ka.rag_chunks c ON c.file_id=f.id
                LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile AND v.input_hash=c.input_hash AND f.embedding_profile<>''
                WHERE f.collection_id=%s AND f.path=%s ORDER BY c.position""", (collection_id, relative_path(path))).fetchall()}

    class FileInput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        collection: str
        path: str
        content: str = Field(max_length=10_000_000)
        expected_hash: str | None = None

    @router.get(prefix + "/file")
    def get_file(project: UUID, collection: str, path: str):
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            row = workspace.collection_by_name(conn, project, collection)
        data = workspace.areas.read(row["source_path"], path, limit)
        try:
            content = data.decode("utf-8-sig")
        except UnicodeError:
            raise HTTPException(400, "File is not UTF-8 text") from None
        return {"collection": collection, "path": path, "content": content, "hash": digest(data)}

    @router.put(prefix + "/file")
    def put_file(project: UUID, payload: FileInput):
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            row = workspace.collection_by_name(conn, project, payload.collection)
            data = payload.content.encode("utf-8")
            if len(data) > limit:
                raise HTTPException(400, "File exceeds the configured size limit")
            try:
                previous = digest(workspace.areas.read(row["source_path"], payload.path, limit))
            except FileNotFoundError:
                previous = None
            if previous != payload.expected_hash:
                raise HTTPException(409, "File already exists or has changed; open it before replacing it")
            workspace.areas.write(row["source_path"], payload.path, data)
        return {"path": payload.path, "hash": digest(data)}

    @router.delete(prefix + "/file")
    def delete_file(project: UUID, collection: str, path: str, expected_hash: str):
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            row = workspace.collection_by_name(conn, project, collection)
            if digest(workspace.areas.read(row["source_path"], path, limit)) != expected_hash:
                raise HTTPException(409, "File changed; open it again before deleting")
            workspace.areas.delete(row["source_path"], path)
        return {"message": "File deleted. Refresh the collection to remove it from search."}
```

Rewrite `documents`, `jobs` and `cancel`:

```python
    @router.get(prefix + '/documents')
    def document_catalog(project: UUID):
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            return conn.execute("""SELECT f.path, k.name AS collection,
                left(regexp_replace(coalesce(c.text, f.path), '\\s+', ' ', 'g'), 280) AS description,
                k.description AS collection_description
                FROM ka.rag_files f JOIN ka.rag_collections k ON k.id=f.collection_id AND k.deleted_at IS NULL
                LEFT JOIN LATERAL (SELECT text FROM ka.rag_chunks WHERE file_id=f.id ORDER BY position LIMIT 1) c ON true
                WHERE f.project_id=%s ORDER BY k.name, f.path""", (project,)).fetchall()

    @router.get(prefix + "/jobs")
    def jobs(project: UUID):
        with workspace.connect() as conn:
            return {"jobs": conn.execute("""SELECT j.id,j.status,j.input,j.result,j.error,j.created_at,j.started_at,j.finished_at,
                j.rag_collection_id AS collection_id, coalesce(k.name, d.path) AS collection
                FROM ka.jobs j LEFT JOIN ka.rag_collections k ON k.id=j.rag_collection_id
                LEFT JOIN ka.rag_folders d ON d.id=j.rag_folder_id
                WHERE j.project_id=%s AND j.kind IN ('rag_collection_sync','rag_folder_index')
                ORDER BY j.created_at DESC LIMIT 100""", (project,)).fetchall()}

    @router.post(prefix + "/jobs/{job_id}/cancel")
    def cancel(project: UUID, job_id: UUID):
        with workspace.connect() as conn:
            row = conn.execute("""UPDATE ka.jobs SET status='cancelled',finished_at=now(),worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                WHERE id=%s AND project_id=%s AND kind IN ('rag_collection_sync','rag_folder_index') AND status IN ('queued','running') RETURNING id""",
                (job_id, project)).fetchone()
            if row is None:
                raise HTTPException(409, "Job is already finished or does not exist")
            return row
```

In `config_response`, replace `source_location`/`source_root` with
`"areas": [{"area": a, "container_root": str(workspace.areas.roots.get(a, "")), "host_root": workspace.areas.host_roots.get(a)} for a in AREAS]`.
In `save_settings`, drop the `embedding_enabled` checks; only validate that a given
`embedding_model_id` exists. In `test_embedding`, call
`workspace.embedding_model(conn, row['embedding_model_id'])`, which already returns
409 with a clear message when the model is missing.

Delete `FolderInput`, `ProcessInput`, `/tree`, `/folders*`, and the old `/jobs`
POST. In `services/rag/src/rag/api.py:96-107`, keep the mapping from
`OSError`/`FileNotFoundError` to 404, and make its message
`"Collection folder or file does not exist"`.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd services/rag && uv run --frozen pytest tests/test_collections_api.py tests/test_workspace.py tests/test_collections.py -q`
Expected: all pass. Tests that need the sync worker are added in Task 4. If
`sync()` fails here because the worker is not converted yet, finish Task 4 Step 3
first and come back; Tasks 3 and 4 are committed separately but may be developed
together.

- [ ] **Step 6: Commit**

```bash
git add services/rag
git commit -m "feat(rag): collections API with freshness, per-collection files and sync queue

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: RAG sync worker per collection

**Files:**
- Modify: `services/rag/src/rag/workspace_worker.py`:
  - `claim` selects `kind='rag_collection_sync'`;
  - `parse_source` takes a `CollectionSettings`;
  - `process` syncs one collection;
  - `main` drops the folder cleanup loop.
- Test: `services/rag/tests/test_sync.py`

**Interfaces:**
- Consumes: Task 2 `Areas.walk/read`, `MAX_BYTES`; Task 3 `Workspace.collection`
  and `Settings`.
- Produces:
  - `claim(workspace) -> dict | None`;
  - `process(workspace, job, transport=None) -> dict | None`, which returns stats
    with these keys:
    - `hashed_files`, `skipped_files`, `parsed_files`;
    - `embedded_chunks`, `reused_vectors`;
    - `removed_entries`;
    - `skipped_binary`, `skipped_large`, `skipped_links`, `skipped_special`.
  - On success: `rag_collections.last_synced_at=now()` and
    `synced_revision=<revision read at start>`.

- [ ] **Step 1: Write the failing tests**

```python
import os

from test_workspace import collection, configure, env, search, sync, workspace_postgres, write  # noqa: F401


def test_sync_indexes_code_as_plain_text_with_patterns(env):
    roots = env[6]
    repo = roots["code"] / "payments"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "retry.py").write_text("MAX_RETRIES = 5  # payment retries\n")
    (repo / "tests" / "test_retry.py").write_text("assert MAX_RETRIES\n")
    created = collection(env, "payments-code", "code/payments", include=["**/*.py"], exclude=["tests/**"])
    stats, row = sync(env, created)
    assert stats["parsed_files"] == 1 and row["indexed_files"] == 1 and row["status"] == "up_to_date"
    hits = search(env, "MAX_RETRIES", ["payments-code"]).json()["results"]
    assert hits[0]["path"] == "src/retry.py" and hits[0]["collection"] == "payments-code"


def test_sync_skips_links_binaries_and_counts_them(env):
    roots = env[6]
    folder = roots["rag-data"] / "docs"
    folder.mkdir()
    (folder / "ok.md").write_text("# OK\n\nRetries.")
    (folder / "bin.md").write_bytes(b"\x00\x01binary")
    (folder / "latin.md").write_bytes("caf\xe9".encode("latin-1"))
    os.symlink(folder / "ok.md", folder / "link.md")
    created = collection(env)
    stats, row = sync(env, created)
    assert row["indexed_files"] == 1
    assert stats["skipped_binary"] == 2 and stats["skipped_links"] == 1


def test_sync_removes_deleted_and_unmatched_files(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env, "a.md", "# A\n\nalpha")
    write(env, "b.txt", "beta")
    _, row = sync(env, created)
    assert row["indexed_files"] == 2
    (roots["rag-data"] / "docs" / "a.md").unlink()
    client.put(prefix + f"/collections/{created}", json={"name": "docs", "source_path": "rag-data/docs",
                                                        "include": ["**/*.md"], "revision": row["revision"]})
    stats, row = sync(env, created)
    assert stats["removed_entries"] == 2 and row["indexed_files"] == 0 and row["status"] == "up_to_date"


def test_unchanged_files_are_skipped_and_vectors_reused(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    configure(env)  # selects the fixture embedding model
    created = collection(env, embeddings_enabled=True, search_mode="hybrid")
    write(env)
    first, row = sync(env, created)
    assert first["embedded_chunks"] >= 1 and row["embedded_chunks"] == row["chunks"]
    second, _ = sync(env, created)
    assert second["skipped_files"] == 1 and second["embedded_chunks"] == 0
    other = collection(env, "copy", "rag-data/docs", embeddings_enabled=True)
    third, _ = sync(env, other)
    assert third["embedded_chunks"] == 0 and third["reused_vectors"] >= 1


def test_embeddings_need_a_model(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env, embeddings_enabled=True)
    write(env)
    stats, row = sync(env, created)
    assert stats is None and row["status"] == "failed" and "embedding model" in row["job"]["error"]


def test_editing_another_collection_does_not_fail_this_sync(env):
    workspace, client, _, prefix, _, transport, roots = env
    for name in ("a", "b"):
        (roots["rag-data"] / name).mkdir()
    first = collection(env, "a", "rag-data/a")
    second = collection(env, "b", "rag-data/b")
    write(env, "x.md", "# X", name="a")
    assert client.post(prefix + f"/collections/{first}/sync", json={}).status_code == 202
    job = claim(workspace)
    row_b = client.get(prefix + f"/collections/{second}").json()
    client.put(prefix + f"/collections/{second}", json={"name": "b", "source_path": "rag-data/b", "description": "changed", "revision": row_b["revision"]})
    assert process(workspace, job, transport) is not None


def test_settings_change_during_sync_fails_that_sync(env):
    workspace, client, _, prefix, state, transport, roots = env
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    assert client.post(prefix + f"/collections/{created}/sync", json={}).status_code == 202
    job = claim(workspace)
    row = client.get(prefix + f"/collections/{created}").json()
    client.put(prefix + f"/collections/{created}", json={"name": "docs", "source_path": "rag-data/docs", "chunk_size": 1000, "revision": row["revision"]})
    assert process(workspace, job, transport) is None
    assert "Settings changed" in client.get(prefix + f"/collections/{created}").json()["job"]["error"]
```

Add `from rag.workspace_worker import claim, process` to the imports.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest tests/test_sync.py -q`
Expected: FAIL, because `claim` returns None for `rag_collection_sync` jobs.

- [ ] **Step 3: Implement the worker**

```python
class CollectionSettings(BaseModel):
    chunk_size: int
    chunk_overlap: int


def claim(workspace: Workspace):
    with workspace.connect() as conn:
        conn.execute("""UPDATE ka.jobs SET status='failed',finished_at=now(),error='Worker lease expired; refresh the collection again',
                     worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                     WHERE kind='rag_collection_sync' AND status='running' AND lease_expires_at < now()""")
        row = conn.execute("""SELECT * FROM ka.jobs WHERE kind='rag_collection_sync' AND status='queued'
                            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if row is None:
            return None
        return conn.execute("""UPDATE ka.jobs SET status='running',started_at=now(),attempt=attempt+1,
                            worker_id='rag-worker',lease_token=%s,lease_expires_at=now()+interval '2 minutes'
                            WHERE id=%s RETURNING *""", (uuid4(), row["id"])).fetchone()


def decode(data: bytes) -> str | None:
    """UTF-8 text, or None for binary content."""
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeError:
        return None


def parse_source(path: str, text: str, settings: CollectionSettings) -> list[str]:
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except ValueError:
            pass  # invalid JSON is indexed as plain text
    title = ""
    if suffix in {".md", ".markdown"}:
        doc = parse_structure(text)
        title = next((heading.text for heading in doc.headings if heading.level == 1 and heading.is_root), "")
        sections = doc.sections or [Section("", text)]
    else:
        sections = [Section("", text)]
    return [(title + "\n" + (section.heading_path if section.heading_path != "(intro)" else "") + "\n" + section.text).strip()
            for section in chunk_sections(sections, settings.chunk_size, settings.chunk_overlap)
            if section.text.strip()]


def snapshot(workspace: Workspace, collection: dict, limit: int, stats: dict) -> list[dict]:
    walk = workspace.areas.walk(collection["source_path"], collection["include"], collection["exclude"])
    stats["skipped_links"], stats["skipped_special"] = walk.skipped["links"], walk.skipped["special"]
    sources, total = [], 0
    for entry in walk.files:
        if entry["size"] > limit:
            stats["skipped_large"] += 1
            continue
        try:
            data = workspace.areas.read(collection["source_path"], entry["path"], limit)
        except FileNotFoundError:
            continue  # removed between walk and read; the manifest check catches real races
        total += len(data)
        if total > MAX_BYTES:
            raise HTTPException(400, "A sync is limited to 200 MB of text; narrow the collection's include patterns")
        text = decode(data)
        if text is None:
            stats["skipped_binary"] += 1
            continue
        sources.append({"path": entry["path"], "text": text, "content_hash": digest(data)})
    return sources
```

`process(workspace, job, transport)` follows the existing structure, with these
substitutions:

- **Start:**
  - `config = workspace.settings(conn, project, lock=False)`;
    `limit = Settings(**config["settings"]).max_file_bytes`;
  - `collection = workspace.collection(conn, project, job["rag_collection_id"])`;
    this raises 404 → message "Collection was deleted";
  - `revision = collection["revision"]`;
  - `model = workspace.embedding_model(conn, config["embedding_model_id"]) if collection["embeddings_enabled"] else None`.
    When the model is missing, the job fails with
    `"Select an enabled embedding model from LLM Settings in RAG Settings"`.
- **Snapshot and existing rows:**
  - `sources = snapshot(workspace, collection, limit, stats)`;
  - `existing = SELECT * FROM ka.rag_files WHERE collection_id=%s`.
- **Fingerprint:**
  `digest({"parser": "markdown-text-json-v3", "chunker": "sections-v1", "size": collection["chunk_size"], "overlap": collection["chunk_overlap"]})`.
- **Skip test:** compare `old["content_hash"]`, `old["processing_hash"]` and
  `old["embedding_profile"]`, unless `job["input"].get("force")`.
- **Parsing:** `texts = parse_source(source["path"], source["text"], CollectionSettings(chunk_size=..., chunk_overlap=...))`.
- **Publish:**
  - lock the collection row with `SELECT revision FROM ka.rag_collections WHERE id=%s AND deleted_at IS NULL FOR UPDATE`;
  - when it is gone or `revision` differs, fail with
    `"Settings changed while syncing; refresh the collection again"`;
  - re-run `snapshot` and compare the manifests
    (`sorted((s["path"], s["content_hash"]) for s in ...)`); on a mismatch, fail with
    `"Files changed while syncing; refresh the collection again"`.
- **Upsert:**
  ```sql
  INSERT INTO ka.rag_files(project_id,collection_id,path,content_hash,processing_hash,embedding_profile,job_id)
  VALUES (...) ON CONFLICT (collection_id,path) DO UPDATE SET content_hash=EXCLUDED.content_hash,
  processing_hash=EXCLUDED.processing_hash,embedding_profile=EXCLUDED.embedding_profile,job_id=EXCLUDED.job_id,indexed_at=now()
  RETURNING id
  ```
- **Removal:** delete the `existing` rows whose path is not in `sources`, and count
  them in `removed_entries`.
- **Vector cleanup:** unchanged (project-wide).
- **Success:** in the same transaction, run
  `UPDATE ka.rag_collections SET last_synced_at=now(), synced_revision=%s WHERE id=%s`
  with the starting `revision`, then mark the job succeeded.

Initialise `stats` with all keys from the Interfaces block set to 0. In `main()`,
delete the `rag_folders` cleanup loop. Delete `Workspace.cleanup_deleted_folders`
if Task 3 did not already.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/rag && uv run --frozen pytest tests/test_sync.py tests/test_collections_api.py tests/test_workspace.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/rag
git commit -m "feat(rag): sync one collection in place with patterns, skips and per-collection revision

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Search by collection with per-collection modes

**Files:**
- Modify: `services/rag/src/rag/workspace_search.py`
- Test: `services/rag/tests/test_collection_search.py`

**Interfaces:**
- Produces:
  - `SearchInput{project_id, query, collections: list[str] | None, mode: Literal[...] | None, top}`;
  - `search(workspace, payload, transport=None) -> {"results": [...], "fallback": [names], "collections": [names]}`.
  - Each result: `{id, text, position, path, collection, content_hash, indexed_at, job_id, score, citation}`.
  - Unknown collection names return 404 `"Unknown collection: <name>"`.
  - `collections=[]` returns no results; `None` means all non-deleted collections.

- [ ] **Step 1: Write the failing tests**

```python
from test_workspace import collection, configure, env, search, sync, workspace_postgres, write  # noqa: F401


def two_collections(env):
    roots = env[6]
    for name, text in (("docs", "# Payments\n\nRetries use exponential backoff."), ("jira", "# DEMO-1\n\nRetries fail on timeout.")):
        (roots["rag-data"] / name).mkdir()
        created = collection(env, name, f"rag-data/{name}")
        write(env, "a.md", text, name=name)
        sync(env, created)


def test_search_one_collection_or_all(env):
    two_collections(env)
    only = search(env, "retries", ["docs"]).json()
    assert {hit["collection"] for hit in only["results"]} == {"docs"} and only["collections"] == ["docs"]
    both = search(env, "retries").json()
    assert {hit["collection"] for hit in both["results"]} == {"docs", "jira"}
    assert search(env, "retries", []).json()["results"] == []
    missing = search(env, "retries", ["nope"])
    assert missing.status_code == 404 and "nope" in missing.json()["error"]["message"]


def test_semantic_collection_without_model_falls_back(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env, search_mode="hybrid")
    write(env)
    sync(env, created)
    result = search(env, "retries", ["docs"]).json()
    assert result["fallback"] == ["docs"] and result["results"]
    forced = search(env, "retries", ["docs"], mode="embedding").json()
    assert forced["fallback"] == ["docs"] and forced["results"]


def test_hybrid_uses_vectors_when_embedded(env):
    roots, state = env[6], env[4]
    (roots["rag-data"] / "docs").mkdir()
    configure(env)
    created = collection(env, embeddings_enabled=True, search_mode="hybrid")
    write(env)
    sync(env, created)
    calls = len(state["calls"])
    result = search(env, "backoff", ["docs"]).json()
    assert result["fallback"] == [] and result["results"] and len(state["calls"]) == calls + 1
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest tests/test_collection_search.py -q`
Expected: FAIL; the input model rejects `collections`.

- [ ] **Step 3: Implement the search**

```python
CollectionName = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=60)]


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    query: str = Field(min_length=1, max_length=4000)
    collections: list[CollectionName] | None = Field(default=None, max_length=200)
    mode: Literal["full_text", "embedding", "hybrid"] | None = None
    top: int = Field(8, ge=1, le=30)


BASE = """SELECT c.id,c.text,c.position,f.path,f.content_hash,f.indexed_at,f.job_id,k.name AS collection
          FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id JOIN ka.rag_collections k ON k.id=f.collection_id"""


def search(workspace, payload: SearchInput, transport=None):
    with workspace.connect() as conn:
        config = workspace.settings(conn, payload.project_id)
        rows = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name",
                            (payload.project_id,)).fetchall()
        if payload.collections is not None:
            known = {row["name"] for row in rows}
            for name in payload.collections:
                if name not in known:
                    raise HTTPException(404, f"Unknown collection: {name}")
            rows = [row for row in rows if row["name"] in set(payload.collections)]
        try:
            model = workspace.embedding_model(conn, config["embedding_model_id"]) if config["embedding_model_id"] else None
        except HTTPException:
            model = None
        text_ids, vector_ids, fallback = [], [], []
        for row in rows:
            mode = payload.mode or row["search_mode"]
            if mode in ("full_text", "hybrid"):
                text_ids.append(row["id"])
            if mode in ("embedding", "hybrid"):
                if row["embeddings_enabled"] and model is not None:
                    vector_ids.append(row["id"])
                else:
                    fallback.append(row["name"])
                    if mode == "embedding":
                        text_ids.append(row["id"])
        vector = embed(model, [payload.query], transport)[0] if vector_ids else None
        conn.commit()
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        rankings = []
        if text_ids:
            rankings.append(conn.execute(BASE + """ WHERE c.project_id=%s AND f.collection_id=ANY(%s)
                AND c.search_vector @@ websearch_to_tsquery('simple',%s)
                ORDER BY ts_rank_cd(c.search_vector,websearch_to_tsquery('simple',%s)) DESC,c.id LIMIT %s""",
                (payload.project_id, text_ids, payload.query, payload.query, payload.top * 3)).fetchall())
        if vector is not None:
            rankings.append(conn.execute(BASE + """ JOIN ka.rag_vectors v ON v.project_id=c.project_id AND v.profile=f.embedding_profile
                AND v.input_hash=c.input_hash WHERE c.project_id=%s AND f.collection_id=ANY(%s) AND v.profile=%s
                ORDER BY v.embedding <=> %s::public.vector,c.id LIMIT %s""",
                (payload.project_id, vector_ids, embedding_profile(model), json.dumps(vector), payload.top * 3)).fetchall())
    hits = {}
    for ranking in rankings:
        for position, row in enumerate(ranking):
            hit = hits.setdefault(row["id"], {**row, "score": 0.0})
            hit["score"] += 1 / (60 + position + 1)
    result = sorted(hits.values(), key=lambda row: (-row["score"], str(row["id"])))[:payload.top]
    return {"collections": [row["name"] for row in rows], "fallback": fallback,
            "results": [{**row, "citation": f"S{i + 1}"} for i, row in enumerate(result)]}
```

Remove the `Settings` and `folder_path` imports from this module.

- [ ] **Step 4: Run all rag tests**

Run: `cd services/rag && uv run --frozen pytest -q`
Expected: all pass, including the legacy SQLite tests, which are untouched until
Task 17.

- [ ] **Step 5: Commit**

```bash
git add services/rag
git commit -m "feat(rag): search selected collections with each collection's search mode

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Deployment — RAG mounts every data area

**Files:**
- Modify: `deploy/compose.yaml` (the `rag` and `rag-worker` services)
- Modify: `scripts/smoke.py` (the RAG checks at `:122-182`)
- Test: `services/docs-wiki/tests/test_deploy.py`

**Interfaces:**
- Produces: `rag` and `rag-worker` have the environment variables `CODE_ROOT`,
  `CODE_INDEX_ROOT`, `RAG_SOURCE_ROOT`, `DOCUMENT_OUTPUT_ROOT` and
  `BOOTSTRAP_DOCS_ROOT`, plus the host variables `CODE_PATH`, `CODE_INDEX_PATH`,
  `RG_DATA`, `GENERATED_DOCS_PATH` and `DOCS_PATH`. They mount the five folders at
  the container paths. Only `rag-data` is writable.

- [ ] **Step 1: Write the failing deploy test**

```python
def test_rag_reads_every_data_area_and_writes_only_rag_data():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text())["services"]
    expected = {
        "/work/code": host("CODE_PATH", "code"), "/work/code-index": host("CODE_INDEX_PATH", "code-index"),
        "/work/generated-documents": host("GENERATED_DOCS_PATH", "generated-documents"), "/work/docs": host("DOCS_PATH", "docs"),
    }
    for name in ("rag", "rag-worker"):
        service = services[name]
        mounts = {(v["target"] if isinstance(v, dict) else v.split(":")[-2] if v.endswith(":ro") else v.split(":")[-1]): v for v in service["volumes"]}
        for target, source in expected.items():
            assert mounts[target] == f"{source}:{target}:ro", (name, target)
        rag = mounts["/work/rag-data"]
        assert isinstance(rag, dict) and not rag.get("read_only") and rag["source"] == host("RG_DATA", "rag-data")
        env = service["environment"]
        assert env["CODE_ROOT"] == "/work/code" and env["RAG_SOURCE_ROOT"] == "/work/rag-data"
        assert env["DOCUMENT_OUTPUT_ROOT"] == "/work/generated-documents" and env["BOOTSTRAP_DOCS_ROOT"] == "/work/docs"
        assert env["CODE_INDEX_ROOT"] == "/work/code-index"
        for key, folder in (("CODE_PATH", "code"), ("RG_DATA", "rag-data"), ("GENERATED_DOCS_PATH", "generated-documents")):
            assert env[key] == host(key, folder)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/docs-wiki && uv run --frozen pytest tests/test_deploy.py -q -k every_data_area`
Expected: FAIL with a KeyError on `/work/code`.

- [ ] **Step 3: Update `deploy/compose.yaml`**

For both `rag` and `rag-worker`:
- keep the existing `rag-data` bind mount as it is;
- append these mounts:
  ```yaml
      - ${CODE_PATH:-${DATA_ROOT:?Run make setup DATA_ROOT=/path}/code}:/work/code:ro
      - ${CODE_INDEX_PATH:-${DATA_ROOT:?Run make setup DATA_ROOT=/path}/code-index}:/work/code-index:ro
      - ${GENERATED_DOCS_PATH:-${DATA_ROOT:?Run make setup DATA_ROOT=/path}/generated-documents}:/work/generated-documents:ro
      - ${DOCS_PATH:-${DATA_ROOT:?Run make setup DATA_ROOT=/path}/docs}:/work/docs:ro
  ```
- add to `environment`:
  - `CODE_ROOT: /work/code`, `CODE_INDEX_ROOT: /work/code-index`,
    `DOCUMENT_OUTPUT_ROOT: /work/generated-documents`, `BOOTSTRAP_DOCS_ROOT: /work/docs`;
  - the five host variables, using the same `${...:-${DATA_ROOT...}/...}`
    expressions as admin-api.

`RAG_SOURCE_ROOT` and `RG_DATA` already exist.

Update `scripts/smoke.py`:
- replace the `tree`/`folders` checks with `collections`/`collections`;
- use the search body `{"query": "documentation", "top": 1}`;
- print `"PASS: RAG settings, collections, processing history, and full-text search"`.

- [ ] **Step 4: Run the deploy tests and compose validation**

Run: `cd services/docs-wiki && uv run --frozen pytest tests/test_deploy.py -q && cd ../.. && make compose-validate`
Expected: all pass; validation is quiet.

- [ ] **Step 5: Commit**

```bash
git add deploy/compose.yaml scripts/smoke.py services/docs-wiki/tests/test_deploy.py
git commit -m "feat(deploy): RAG service reads every data area; only rag-data is writable

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: admin-api — collection grants, agent config, legacy shim and RAG proxy

**Files:**
- Create: `services/admin-api/src/knowledge_admin/collections.py`, holding the shim
  and helpers.
- Modify: `services/admin-api/src/knowledge_admin/agents.py`:
  - `get_agent_config` collections block at `:332-343`;
  - output at `:439-440`;
  - `get_paths` at `:159-166`.
- Modify: `services/admin-api/src/knowledge_admin/profiles_api.py` (`:23-26`, `:71`,
  `:127`, `:144-173`, `:198`).
- Modify: `services/admin-api/src/knowledge_admin/agent_wizard.py` (`:27`, `:83-98`,
  `:125`, `:147`, `:199`, `:244-253`).
- Modify: `packages/llm-settings/src/knowledge_llm/profiles.py:14-15`.
- Modify: `services/admin-api/src/knowledge_admin/rag_api.py` (`ALLOWED`).
- Modify: `services/admin-api/src/knowledge_admin/shell_worker.py:34-36`: add
  `('BOOTSTRAP_DOCS_ROOT','/work/docs')` to the read roots.
- Tests: update `tests/test_agents.py`, `tests/test_profiles.py`,
  `tests/test_agent_wizard.py`, `tests/test_rag.py` and `tests/test_todo.py`;
  create `tests/test_agent_collections.py`.

**Interfaces:**
- Produces, in `knowledge_admin.collections`:
  - `slug(path: str) -> str`: the same slug rule as the migration (lowercase,
    non-alphanumeric runs become `-`, trimmed);
  - `area_root(source_path: str) -> PurePosixPath`: container path from the env
    roots, using the Global Constraints mapping;
  - `document_collections(config: dict) -> tuple[bool, list[dict]]`: returns
    `(all_collections, [{name, description, path}])`. It accepts the new
    `documents.collections` config and the legacy `documents.folders` /
    `all_folders` config.
- The agent config `documents` block becomes
  `{enabled, grounded, all_collections, collections: [{id, name, description, path}]}`,
  where `path` is the container folder.
- The profile API fields `all_folders`, `include_subfolders` and `folder_ids` become
  `all_collections` and `collection_ids`. The options key `folders` becomes
  `collections: [{id, name, description}]`.
- `knowledge_llm.profile()` sets `row['collections']` to the granted collection names.
- The RAG proxy `ALLOWED` becomes:
  ```python
  ALLOWED = {
      "GET": r"settings|collections|collections/[0-9a-f-]{36}(/files|/chunks)?|file|jobs",
      "PUT": r"settings|file|collections/[0-9a-f-]{36}",
      "POST": r"collections|collections/sync-stale|collections/[0-9a-f-]{36}/sync|jobs/[0-9a-f-]{36}/cancel|test/embedding|search",
      "DELETE": r"file|collections/[0-9a-f-]{36}",
  }
  ```

- [ ] **Step 1: Write the failing tests in `tests/test_agent_collections.py`**

Seed collections with SQL against `store` (the existing admin-api fixture), using
the pattern from `tests/test_agents.py:220-240`, which seeds `rag_folders`. Set
`monkeypatch.setenv('RAG_SOURCE_ROOT', '/work/rag-data')` and
`monkeypatch.setenv('CODE_ROOT', '/work/code')`.

```python
from knowledge_admin.agents import get_agent_config, get_paths
from knowledge_admin.collections import document_collections, slug


def seed(store, *names_and_paths):
    with store.connect() as conn:
        project = conn.execute("SELECT id FROM ka.projects LIMIT 1").fetchone()['id']
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{\"max_file_bytes\":2000000}') ON CONFLICT DO NOTHING", (project,))
        return {name: conn.execute("INSERT INTO ka.rag_collections(project_id,name,description,source_path) VALUES (%s,%s,%s,%s) RETURNING id",
                                   (project, name, f'About {name}', path)).fetchone()['id'] for name, path in names_and_paths}


def test_agent_config_lists_granted_collections_with_paths(store, monkeypatch):
    monkeypatch.setenv('RAG_SOURCE_ROOT', '/work/rag-data')
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    ids = seed(store, ('jira', 'rag-data/jira'), ('payments-code', 'code/payments'))
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET all_collections=false,documents_enabled=true WHERE id='search'")
        conn.execute("INSERT INTO ka.agent_collections(agent_id,collection_id) VALUES ('search',%s)", (ids['payments-code'],))
    config = get_agent_config('search')
    assert config['documents']['all_collections'] is False
    assert [(c['name'], c['path']) for c in config['documents']['collections']] == [('payments-code', '/work/code/payments')]


def test_legacy_folder_config_maps_to_collections(monkeypatch):
    monkeypatch.setenv('RAG_SOURCE_ROOT', '/work/rag-data')
    legacy = {'documents': {'enabled': True, 'all_folders': False, 'include_subfolders': True, 'folders': [
        {'id': 'x', 'path': '/work/rag-data/demo/payments', 'description': 'd', 'search_folder': 'demo.payments'},
        {'id': 'y', 'path': '/work/rag-data/Old_Stuff', 'description': 'o', 'search_folder': 'Old_Stuff'}]}}
    everything, items = document_collections(legacy)
    assert everything is False
    assert [(i['name'], i['path']) for i in items] == [('demo', '/work/rag-data/demo'), ('old-stuff', '/work/rag-data/Old_Stuff')]
    assert document_collections({'documents': {'all_folders': True, 'folders': []}}) == (True, [])
    assert slug('Old_Stuff') == 'old-stuff'


def test_ungrounded_document_agents_read_collection_folders(monkeypatch):
    config = {'code': {'enabled': False, 'projects': []}, 'documents': {'enabled': True, 'grounded': False,
              'all_collections': False, 'collections': [{'id': 'a', 'name': 'jira', 'description': '', 'path': '/work/rag-data/jira'}]}}
    assert [str(p) for p in get_paths(config)] == ['/work/rag-data/jira']


def test_profile_api_grants_collections(client, store):
    ids = seed(store, ('jira', 'rag-data/jira'))
    options = client.get('/api/agents/search/options', headers=HEADERS).json()
    assert [c['name'] for c in options['collections']] == ['jira']
    agent = client.get('/api/agents/search', headers=HEADERS).json()
    payload = {**{k: agent[k] for k in ('name', 'description', 'when_to_use', 'enabled', 'revision')}, 'all_collections': False,
               'collection_ids': [str(ids['jira'])], **{k: agent[k] for k in agent if k.endswith('_enabled') or k in ('all_repositories', 'repository_ids', 'service_ids')}}
    saved = client.put('/api/agents/search', headers=HEADERS, json=payload)
    assert saved.status_code == 200, saved.text
    assert client.get('/api/agents/search', headers=HEADERS).json()['collection_ids'] == [str(ids['jira'])]
```

Adapt the last test's payload to the real `PUT /api/agents/{id}` shape: open
`tests/test_profiles.py` and copy its PUT helper. Use the fixtures and `HEADERS`
from `tests/test_profiles.py` (`from test_repositories import HEADERS`).

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_collections.py -q`
Expected: FAIL, because `knowledge_admin.collections` does not exist.

- [ ] **Step 3: Implement `knowledge_admin/collections.py`**

```python
"""RAG collections as agents see them, including configs saved before collections existed."""
import os
import re
from pathlib import PurePosixPath
from typing import Any

AREA_ROOTS = {'code': 'CODE_ROOT', 'code-index': 'CODE_INDEX_ROOT', 'rag-data': 'RAG_SOURCE_ROOT',
              'generated-documents': 'DOCUMENT_OUTPUT_ROOT', 'docs': 'BOOTSTRAP_DOCS_ROOT'}


def slug(path: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', path.lower()).strip('-')


def area_root(source_path: str) -> PurePosixPath:
    area, _, rest = source_path.partition('/')
    root = PurePosixPath(os.environ.get(AREA_ROOTS[area], ''))
    if root.anchor != '/' or '..' in root.parts:
        raise ValueError(f'{AREA_ROOTS[area]} must be an absolute path mounted in the agent container')
    return root / rest if rest else root


def document_collections(config: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """(all_collections, [{name, description, path}]) for new and legacy (folder-based) configs."""
    documents = config['documents']
    if 'collections' in documents:
        return bool(documents.get('all_collections')), documents['collections']
    rag_root = PurePosixPath(os.environ.get('RAG_SOURCE_ROOT', '/work/rag-data'))
    items: dict[str, dict[str, Any]] = {}
    for folder in documents.get('folders', []):
        top = folder['search_folder'].split('.')[0]
        items.setdefault(slug(top), {'name': slug(top), 'description': folder.get('description', ''), 'path': str(rag_root / top)})
    return bool(documents.get('all_folders')), list(items.values())
```

In `agents.py` `get_agent_config`, replace the `rag_root` check and the `folders`
query with:

```python
        collections = conn.execute("""SELECT c.id,c.name,c.description,c.source_path FROM ka.rag_collections c
            LEFT JOIN ka.agent_collections a ON a.collection_id=c.id AND a.agent_id=%s
            WHERE c.project_id=%s AND c.deleted_at IS NULL AND (%s OR a.collection_id IS NOT NULL)
            ORDER BY c.name""", (agent_id, project_id, agent['all_collections'])).fetchall()
        for item in collections:
            item['path'] = str(area_root(item.pop('source_path')))
```

Keep the `CODE_ROOT` validation. In the output, write
`'documents': {'enabled': ..., 'grounded': ..., 'all_collections': agent['all_collections'], 'collections': collections}`.
`get_paths` becomes:

```python
    if config['documents']['enabled'] and not documents_grounded(config):
        paths.extend(Path(item['path']) for item in document_collections(config)[1])
```

Update `profiles_api.py` and `agent_wizard.py` mechanically:

- **Field names:** `all_folders` → `all_collections`; `folder_ids` →
  `collection_ids`. Delete `include_subfolders` from the models, SQL and inserts.
- **Grant table:**
  - `agent_knowledge_folders`/`folder_id`/`rag_folders` → `agent_collections`/`collection_id`/`rag_collections`;
  - the filter becomes `' AND deleted_at IS NULL'`.
- **Options query:**
  `SELECT id,name,description FROM ka.rag_collections WHERE project_id=%s AND deleted_at IS NULL ORDER BY name`;
  the key becomes `collections`.
- **Wizard prompt** at `agent_wizard.py:27`: replace "use all_folders" with "use
  all_collections"; mention collection names instead of folder paths.
- **`knowledge_llm.profile()`:**
  ```python
  row['collections'] = [r['name'] for r in conn.execute('''SELECT c.name FROM ka.agent_collections a
      JOIN ka.rag_collections c ON c.id=a.collection_id WHERE a.agent_id=%s AND c.deleted_at IS NULL ORDER BY c.name''', (agent_id,))]
  ```
  Remove `row['folders']`.

Update the existing tests that set `all_folders`, `include_subfolders`, `folder_ids`
or `rag_folders` to the new names; `rg -l "all_folders|include_subfolders|folder_ids|agent_knowledge_folders|rag_folders" services/admin-api/tests`
lists them. Update `tests/test_rag.py` for the new proxy resources: assert that
`collections`, `collections/<uuid>/sync` and `collections/<uuid>/files` are proxied,
and that `tree` returns 404.

- [ ] **Step 4: Run the admin-api tests touched here**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_agent_collections.py tests/test_agents.py tests/test_profiles.py tests/test_agent_wizard.py tests/test_rag.py tests/test_todo.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api packages/llm-settings
git commit -m "feat(admin): agents are granted RAG collections; legacy folder configs still resolve

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: admin-api — document tools, grounding and Reader narrowing by collection

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/tools/documents.py`
- Modify: `services/admin-api/src/knowledge_admin/grounding.py` (`folder` → `collection`)
- Modify: `services/admin-api/src/knowledge_admin/reader_api.py`
  (`ReaderRunInput`, `narrow`, transcript at `:147-151`)
- Tests: update `tests/test_document_tools.py`, `tests/test_reader_api.py` and
  `tests/test_function_tools.py`.

**Interfaces:**
- Consumes: Task 7 `document_collections`; Task 5 search response; Task 3
  `GET file?collection=&path=`.
- Produces:
  - `search_documents(query, collection: str | None = None)`. Excerpts are recorded
    as `{'n', 'path', 'collection', 'text'}`, and the hit line format is
    `[n] <collection>/<path>`.
  - `read_document(collection: str, path: str)`.
  - `grounding.result` sources: `{'n', 'path', 'heading_path': <collection>, 'anchor': None}`.
    The `path` sent to the Reader is `<collection>/<path>`, which the Reader UI links
    to `/{app}/doc/<collection>/<path>`.
  - `ReaderRunInput.collections: list[CollectionName] | None`; `include_subfolders`
    and `folders` are removed.
  - `narrow(config, collections)`.
  - The transcript returns `collections: list[str] | None`.

- [ ] **Step 1: Update the tests first**

In `tests/test_document_tools.py`, change the fake RAG search handler to assert the
request body has `collections` and to return hits with `collection` keys. Add these
cases:

```python
def test_search_is_limited_to_granted_collections(...):
    # config: documents.collections = [{'name': 'jira', ...}], all_collections False
    # search_documents(query='x') posts {"collections": ["jira"], "mode" absent}
    # search_documents(query='x', collection='docs') -> 'This collection is outside the agent’s document collections.'


def test_legacy_config_searches_mapped_collections(...):
    # config with documents.folders [{'search_folder': 'demo.payments', ...}] posts {"collections": ["demo"]}


def test_read_document_uses_collection_and_path(...):
    # read_document(collection='jira', path='DEMO-1.md') GETs /file with params collection=jira&path=DEMO-1.md
    # read_document(collection='docs', ...) when not granted -> outside message; path '../x' -> 'Use a document path returned by search_documents.'
```

Write these as real tests in the file's existing style, which uses
`RAG_TRANSPORT = httpx.MockTransport(...)` and an `AgentContext` dict. Copy the
structure of the current folder tests in the same file, replacing folders with
collections. In `tests/test_reader_api.py`, replace the folder narrowing tests:

```python
def test_reader_narrows_to_selected_collections_and_never_broadens():
    config = {'documents': {'enabled': True, 'all_collections': False, 'collections': [
        {'id': '1', 'name': 'docs', 'description': '', 'path': '/work/rag-data/docs'},
        {'id': '2', 'name': 'jira', 'description': '', 'path': '/work/rag-data/jira'}]}}
    narrowed = narrow(config, ['jira', 'secret'])
    assert [c['name'] for c in narrowed['documents']['collections']] == ['jira']
    assert narrow(config, None) is config
```

For `all_collections=True` configs, narrowing to `['jira']` keeps only names in the
selection. The tool then sends exactly those names, and the RAG service returns 404
for names that do not exist; the tool reports "Document search is unavailable".
Ruling: narrowing an all-collections agent sets `all_collections=False` and
`collections=[{'name': n, 'description': '', 'path': ''} for n in selection]`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_document_tools.py tests/test_reader_api.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the changes**

`tools/documents.py`:

```python
def _allowed(config: dict[str, Any]) -> list[str] | None:
    """Collection names the agent may use; None means every collection."""
    if not config['enabled'] or not config['documents']['enabled']:
        raise ValueError('Document search is not enabled for this agent.')
    everything, items = document_collections(config)
    return None if everything else sorted({item['name'] for item in items})


def _register(state, collection: str, path: str, text: str) -> int:
    number = len(state['excerpts']) + 1
    state['excerpts'].append({'n': number, 'path': path, 'collection': collection, 'text': text})
    return number


@function_tool
async def search_documents(ctx: RunContextWrapper[AgentContext], query: str, collection: str | None = None) -> str:
    """Search the knowledge base and return numbered excerpts to cite as [n].

    Args:
        query: Words or a question to search for.
        collection: Optional collection name, such as "jira", to search only that collection.
    """
    context = ctx.context
    allowed = _allowed(context['config'])
    if collection is not None:
        collection = collection.strip()
        if allowed is not None and collection not in allowed:
            return 'This collection is outside the agent’s document collections.'
        names = [collection]
    else:
        names = allowed
    state = _state(context)
    if names == []:
        state['searches'] += 1
        state['empty_searches'] += 1
        return 'No matches in the documentation.'
    try:
        async with _client() as client:
            body = {'project_id': await _project(client), 'query': query[:4000], 'top': 8}
            if names is not None:
                body['collections'] = names[:200]
            response = await client.post('/search', json=body)
            response.raise_for_status()
            hits = response.json()['results']
    except (httpx.HTTPError, KeyError, ValueError):
        return 'Document search is unavailable right now. Try again, or tell the user the knowledge base could not be searched.'
    state['searches'] += 1
    if not hits:
        state['empty_searches'] += 1
        return 'No matches in the documentation.'
    secrets = context['credentials'].values()
    blocks = []
    for hit in hits:
        text = redact(hit['text'], secrets)
        number = _register(state, hit['collection'], hit['path'], text)
        blocks.append(f"[{number}] {hit['collection']}/{hit['path']}\n{text}")
    return '\n\n'.join(blocks)


@function_tool
async def read_document(ctx: RunContextWrapper[AgentContext], collection: str, path: str) -> str:
    """Read one knowledge-base document returned by search_documents; cite it with its [n].

    Args:
        collection: The collection name shown before the path, such as "jira".
        path: The document path inside that collection, such as "DEMO-1.md".
    """
    context = ctx.context
    allowed = _allowed(context['config'])
    collection, path = collection.strip(), path.strip()
    parts = path.split('/')
    if not path or path.startswith('/') or '\\' in path or any(part in ('', '.', '..') for part in parts):
        return 'Use a document path returned by search_documents.'
    if allowed is not None and collection not in allowed:
        return 'This document is outside the agent’s document collections.'
    try:
        async with _client() as client:
            project = await _project(client)
            response = await client.get(f'/backoffice/projects/{project}/file', params={'collection': collection, 'path': path})
            if response.status_code == 404:
                return 'Document not found.'
            response.raise_for_status()
            content = response.json()['content']
    except (httpx.HTTPError, KeyError, ValueError):
        return 'The document could not be read right now.'
    encoded = content.encode()
    text = redact(encoded[:MAX_DOCUMENT_BYTES].decode(errors='ignore'), context['credentials'].values())
    number = _register(_state(context), collection, path, text)
    return f'[{number}] {collection}/{path}\n{text}' + ('\n[truncated]' if len(encoded) > MAX_DOCUMENT_BYTES else '')
```

Remove `MAX_FOLDER_SELECTORS` and the `PurePosixPath` import.

`grounding.result`:

```python
    sources = [{'n': n, 'path': f"{by_number[n]['collection']}/{by_number[n]['path']}", 'heading_path': by_number[n]['collection'], 'anchor': None}
               for n in cited]
    ...
            'near_misses': [{'path': f"{e['collection']}/{e['path']}", 'heading_path': e['collection']} for e in state['excerpts'][:3]],
```

In `reader_api.py`:

```python
CollectionName = Annotated[str, Field(pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$', max_length=60)]


class ReaderRunInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID | None = None
    question: str = Field(min_length=1, max_length=10000)
    collections: list[CollectionName] | None = Field(default=None, max_length=200)


def narrow(config: dict[str, Any], collections: list[str] | None) -> dict[str, Any]:
    """Limit the agent's document collections to the user's selection; never broaden them."""
    if collections is None:
        return config
    chosen = set(collections)
    everything, items = document_collections(config)
    if everything:
        kept = [{'name': name, 'description': '', 'path': ''} for name in sorted(chosen)]
    else:
        kept = [item for item in items if item['name'] in chosen]
    return {**config, 'documents': {**config['documents'], 'all_collections': False, 'collections': kept}}
```

Change the `prepare=` lambda to `narrow(config, value.collections)`. The transcript
becomes:

```python
        everything, items = document_collections(saved['config'])
        return {..., 'collections': None if everything else [item['name'] for item in items], ...}
```

Use whatever variable holds the session config at `:147`, and drop
`include_subfolders`. Update `tests/test_function_tools.py`, which asserts on tool
JSON schemas, for the new parameter names.

- [ ] **Step 4: Run the admin-api suite**

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: all pass except the known existing failure
`test_environments.py::test_migration_reapply_and_populated_rollback_protection`,
which also fails on `main`.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api
git commit -m "feat(admin): document tools, grounding and Reader narrowing work on collections

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: docs-wiki — context, TOC, documents and chat by collection

**Files:**
- Modify: `services/docs-wiki/src/docs_wiki/workspace_client.py`
- Modify: `services/docs-wiki/src/docs_wiki/agent_context.py`: `AgentContext`
  becomes `{collections}`; delete `ScopedDocuments`, which nothing uses.
- Modify: `services/docs-wiki/src/docs_wiki/search_api.py` (`:47-50`, `:75-85`,
  `:146-147`, `:213`)
- Modify: `services/docs-wiki/src/docs_wiki/api.py` (TOC grouping at `:72-91`, the
  doc route)
- Modify: `services/docs-wiki/src/docs_wiki/tools/search.py`: `SearchContext` keeps
  working through `search(collection, query)`; no change is needed beyond what the
  tests show.
- Tests: update `tests/test_search_api.py`, `tests/test_reader_chat.py` and
  `tests/test_api.py` (the TOC and doc tests that use the workspace client).

**Interfaces:**
- Consumes: Task 3 `GET collections`, `GET documents`, `GET file?collection=&path=`;
  Task 5 search; Task 8 `ReaderRunInput.collections` and the transcript's
  `collections`.
- Produces:
  - `WorkspaceRagClient.collections() -> list[dict]`, the non-deleted collections
    with a present folder: `{name, description, indexed_files}`;
  - `search_collections(query, collections, top=8) -> hits`, where each hit's
    `path` is `<collection>/<path>` and `heading_path` is the collection;
  - `get_document(collection_unused, path)`, which splits `<collection>/<rest>`;
  - `list_documents(...)` rows with `path` = `<collection>/<path>` and
    `quadrant` = the collection;
  - `GET /api/{app}/context` → `{collections: [{name, description, indexed_files}], warnings}`;
  - chat forwards `{'question', 'collections'}`;
  - the transcript returns `context: {'collections': [...] | None}`.

- [ ] **Step 1: Update the tests first**

In `tests/test_search_api.py`:
- make the fake RAG transport answer `GET .../collections` with
  `{"collections": [{"name": "payments", "description": "Payments", "indexed_files": 2, "folder_present": True}, {"name": "people", ..., "folder_present": True}]}`;
- make `/search` return hits with `collection`.

Assert that:
- context lists the collections an agent may use, filtered with `all_collections`
  and `collections` from the profile;
- the Reader chat payload sent to admin-api is `{'question': ..., 'collections': [...]}`;
- the transcript `context` is `{'collections': [...]}`.

In `tests/test_api.py`, adapt the workspace-mode TOC and doc tests:
- the TOC groups by collection;
- the doc route `/api/shop/doc?path=jira/DEMO-1.md` fetches
  `file?collection=jira&path=DEMO-1.md`;
- search hits link `jira/DEMO-1.md`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/docs-wiki && uv run --frozen pytest tests/test_search_api.py tests/test_reader_chat.py tests/test_api.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the changes**

```python
class WorkspaceRagClient(RagClient):
    ...
    def collections(self) -> list[dict]:
        return [row for row in self._request('GET', self._prefix() + '/collections')['collections'] if row['folder_present']]

    def search(self, collection: str, query: str, top: int = 8) -> list[dict]:
        return self.search_collections(query, None, top)

    def search_collections(self, query: str, collections: list[str] | None, top: int = 8) -> list[dict]:
        project = self._request('GET', '/backoffice/workspace')['project_id']
        data = self._request('POST', '/search', json={'project_id': project, 'query': query, 'top': top,
                                                      **({'collections': collections} if collections is not None else {})})
        return [{**hit, 'path': f"{hit['collection']}/{hit['path']}", 'title': PurePosixPath(hit['path']).name,
                 'heading_path': hit['collection'], 'anchor': None} for hit in data['results']]

    def get_document(self, collection: str, path: str) -> dict:
        name, _, rest = path.partition('/')
        try:
            row = self._client.get(self._prefix() + '/file', params={'collection': name, 'path': rest})
            row.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 404):
                raise RagNotFound('Document not found') from exc
            raise RagError('Unable to read the RAG document') from exc
        except httpx.HTTPError as exc:
            raise RagError('RAG service unavailable') from exc
        return {**row.json(), 'path': path, 'markdown': row.json()['content'], 'title': PurePosixPath(rest).name, 'headings': [], 'front_matter': {}}

    def list_documents(self, collection: str) -> list[dict]:
        rows = self._request('GET', self._prefix() + '/documents')
        return [{**row, 'path': f"{row['collection']}/{row['path']}", 'quadrant': row['collection'],
                 'title': PurePosixPath(row['path']).name} for row in rows]
```

Delete `folders()` and `search_folders()`. In `agent_context.py`:

```python
CollectionName = Annotated[str, Field(pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$', max_length=60)]


class AgentContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # None includes every collection the agent may use; [] explicitly includes none.
    collections: list[CollectionName] | None = Field(default=None, max_length=200)

    def normalized(self):
        return self.model_copy(update={'collections': sorted(set(self.collections)) if self.collections is not None else None})
```

In `search_api.py`:

```python
    def collection_allowed(row, selected):
        return selected is None or selected['documents_enabled'] and (selected['all_collections'] or row['name'] in selected['collections'])
...
        result = {'collections': [], 'warnings': []}
        if rag is not None:
            try:
                result['collections'] = [{'name': row['name'], 'description': row.get('description', ''),
                                          'indexed_files': row.get('indexed_files', 0)} for row in rag.collections() if collection_allowed(row, selected)]
            except RagError:
                result['warnings'].append('Collections are unavailable. Try refreshing the context list.')
```

Change the chat payload to `{'question': body.question, 'collections': body.context.collections}`.
Change the transcript context to `{'collections': saved['collections']}`. The
fallback for a missing context is `AgentContext().model_dump()`. Also drop the
`repositories` key everywhere in this module.

- [ ] **Step 4: Run the docs-wiki suite**

Run: `cd services/docs-wiki && uv run --frozen pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/docs-wiki
git commit -m "feat(docs-wiki): Reader context, TOC, documents and chat use RAG collections

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: chat-ui and docs-wiki-ui — collection picker

**Files:**
- Modify: `packages/chat-ui/types.ts`, `packages/chat-ui/AdvancedContext.tsx`,
  `packages/chat-ui/ChatPanel.tsx` (context validity)
- Modify: `services/docs-wiki-ui/app/lib/api.ts` (`ContextOptions` re-export only),
  plus any route reading `context.folders`
- Tests: `services/docs-wiki-ui/app/components/__tests__/chatPanel.test.tsx`;
  `services/admin-ui/app/components/__tests__/agents.test.tsx` (it renders the
  shared panel)

**Interfaces:**
- Produces:
  ```ts
  export interface AgentContext { collections: string[] | null }
  export interface ContextOptions { collections: { name: string; description: string; indexed_files: number }[]; warnings: string[] }
  export const DEFAULT_CONTEXT: AgentContext = { collections: null };
  ```
- `AdvancedContext` shows an "All collections" checkbox and, when it is off, one
  checkbox per collection labelled with the name and the description plus the count
  of indexed files. The summary reads `All collections` or `N collections`. When the
  selection is empty, the panel shows "Select at least one collection." and asking
  is blocked.

- [ ] **Step 1: Update the chat-panel test**

In `chatPanel.test.tsx`, change the context fixtures to
`{ collections: [{ name: 'payments', description: 'Payments', indexed_files: 2 }], warnings: [] }`.
Change the saved context to `{ collections: ['payments'] }`. Assert that:
- `screen.getByRole('checkbox', { name: /payments/ })` is checked after opening
  Advanced;
- the POST body contains `"context":{"collections":["payments"]}`.

Add this test:

```tsx
test("choosing no collection blocks asking", async () => {
  const fetchMock = vi.fn(async (url: string) => url.endsWith('/context')
    ? Response.json({ collections: [{ name: 'docs', description: '', indexed_files: 1 }], warnings: [] })
    : doneResponse('x'));
  vi.stubGlobal('fetch', fetchMock);
  renderPanel({ contextEnabled: true, sources: { docs: true } });
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Advanced' }));
  await user.click(await screen.findByRole('checkbox', { name: 'All collections' }));
  expect(screen.getByText('Select at least one collection.')).toBeInTheDocument();
  await user.type(screen.getByRole('textbox'), 'Question');
  expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled();
});
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/docs-wiki-ui && pnpm install --frozen-lockfile --offline && pnpm exec vitest run app/components/__tests__/chatPanel.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement the picker**

`AdvancedContext.tsx`: replace the Documents fieldset body with:

```tsx
  const collections = options?.collections ?? [];
  const summary = value.collections === null ? "All collections" : `${value.collections.length} collections`;
  function toggle(name: string) {
    const selected = value.collections ?? [];
    onChange({ ...value, collections: selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name] });
  }
  ...
        {available.docs && <FieldSet>
          <FieldLegend>Collections</FieldLegend>
          <FieldGroup>
            <Field orientation="horizontal">
              <Checkbox id={`${id}-all`} checked={value.collections === null} disabled={locked}
                onCheckedChange={(checked) => onChange({ ...value, collections: checked ? null : [] })} />
              <FieldLabel htmlFor={`${id}-all`}>All collections</FieldLabel>
            </Field>
            {value.collections !== null && <>
              <FieldGroup className="max-h-64 overflow-y-auto" data-slot="checkbox-group">
                {collections.map((item, index) => <Field key={item.name} orientation="horizontal">
                  <Checkbox id={`${id}-c-${index}`} checked={value.collections!.includes(item.name)} disabled={locked} onCheckedChange={() => toggle(item.name)} />
                  <FieldContent>
                    <FieldLabel htmlFor={`${id}-c-${index}`}>{item.name}</FieldLabel>
                    <FieldDescription>{item.description ? `${item.description} · ` : ""}{item.indexed_files} indexed files</FieldDescription>
                  </FieldContent>
                </Field>)}
                {value.collections.filter((n) => !collections.some((c) => c.name === n)).map((n) => <p key={n} className="text-sm text-muted-foreground">{n} (unavailable)</p>)}
                {!collections.length && !loading && <p className="text-sm text-muted-foreground">No collections available.</p>}
              </FieldGroup>
              {!value.collections.length && <p className="text-sm text-destructive">Select at least one collection.</p>}
            </>}
          </FieldGroup>
        </FieldSet>}
```

Remove the `Switch` import if it is no longer used. In `ChatPanel.tsx`, set
`invalidContext = contextEnabled && context.collections?.length === 0`.

- [ ] **Step 4: Run both UI suites and the typecheck**

Run: `cd services/docs-wiki-ui && pnpm install --frozen-lockfile --offline && pnpm exec tsc --noEmit && pnpm test && cd ../admin-ui && pnpm install --frozen-lockfile --offline && pnpm exec tsc --noEmit && pnpm test`
Expected: both pass. Fix `agents.test.tsx` context fixtures from `folders` to
`collections`.

- [ ] **Step 5: Commit**

```bash
git add packages/chat-ui services/docs-wiki-ui services/admin-ui
git commit -m "feat(chat-ui): pick RAG collections instead of folders

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: admin-ui — Collections page

**Files:**
- Modify: `services/admin-ui/app/lib/rag.ts`: collection types and functions;
  remove the tree, folder and process types.
- Create: `services/admin-ui/app/components/RagCollectionsView.tsx`, holding the
  table, status and Refresh.
- Create: `services/admin-ui/app/components/RagCollectionForm.tsx`, the create and
  edit dialog.
- Create: `services/admin-ui/app/components/RagCollectionDetail.tsx`: files, chunks,
  and a file editor for writable collections.
- Create: `services/admin-ui/app/routes/ragCollections.tsx`,
  `services/admin-ui/app/routes/ragCollection.tsx`.
- Modify: `services/admin-ui/app/routes.ts`: `rag/collections`,
  `rag/collections/:collectionId`; remove `rag/files`.
- Modify: `services/admin-ui/app/components/AdminLayout.tsx`: the RAG pages entry
  `files` becomes `collections` with label "Collections" and description "Search
  units mapped to folders in the data root: sync state, settings and indexed
  content."
- Delete: `services/admin-ui/app/components/RagFilesView.tsx`,
  `services/admin-ui/app/routes/ragFiles.tsx`, and `RagSourceLocation.tsx` if
  nothing else imports it.
- Test: `services/admin-ui/app/components/__tests__/ragCollections.test.tsx`;
  remove the Files tests from `rag.test.tsx`.

**Interfaces:**
- Consumes: the Task 3 API through `/api/rag/*` (the proxy from Task 7).
- Produces, in `lib/rag.ts`:
  ```ts
  export type SearchMode = "full_text" | "embedding" | "hybrid";
  export type CollectionState = "folder_missing" | "syncing" | "failed" | "needs_refresh" | "up_to_date";
  export interface CollectionInput {
    name: string; description: string; source_path: string; search_mode: SearchMode; embeddings_enabled: boolean;
    include: string[]; exclude: string[]; chunk_size: number; chunk_overlap: number; revision?: number;
  }
  export interface RagCollection extends Required<CollectionInput> {
    id: string; synced_revision: number | null; last_synced_at: string | null; host_path: string | null;
    container_path: string; writable: boolean; folder_present: boolean; folder_changed_at: string | null;
    file_count: number; truncated: boolean; skipped: { links: number; special: number };
    indexed_files: number; chunks: number; embedded_chunks: number; needs_refresh: boolean; status: CollectionState;
    job: { id: string; status: string; error: string | null; created_at: string; finished_at: string | null } | null;
  }
  export interface CollectionFile { path: string; size: number | null; mtime: number | null; indexed_at: string | null;
    content_hash: string | null; chunks: number; embedded_chunks: number; stale: boolean }
  export const DEFAULT_COLLECTION: CollectionInput = { name: "", description: "", source_path: "rag-data/", search_mode: "full_text",
    embeddings_enabled: false, include: ["**/*.md", "**/*.markdown", "**/*.txt"], exclude: [], chunk_size: 2000, chunk_overlap: 200 };
  export const getCollections = (signal?: AbortSignal) => ragRequest<{ collections: RagCollection[] }>("collections", "GET", undefined, signal);
  export const getCollection = (id: string, signal?: AbortSignal) => ragRequest<RagCollection>(`collections/${id}`, "GET", undefined, signal);
  export const saveCollection = (value: CollectionInput, id?: string) => ragRequest<RagCollection | { id: string }>(id ? `collections/${id}` : "collections", id ? "PUT" : "POST", value);
  export const deleteCollection = (id: string, revision: number) => ragRequest<{ deleted_files: number }>(`collections/${id}?revision=${revision}&confirmed=true`, "DELETE");
  export const syncCollection = (id: string, force = false) => ragRequest<{ id: string }>(`collections/${id}/sync`, "POST", { force });
  export const syncStale = () => ragRequest<{ queued: string[] }>("collections/sync-stale", "POST");
  export const getCollectionFiles = (id: string, signal?: AbortSignal) => ragRequest<{ files: CollectionFile[]; truncated: boolean; skipped: { links: number; special: number } }>(`collections/${id}/files`, "GET", undefined, signal);
  export const getChunks = (id: string, path: string) => ragRequest<{ chunks: { position: number; text: string; embedded: boolean }[] }>(`collections/${id}/chunks?path=${encodeURIComponent(path)}`);
  export const getRagFile = (collection: string, path: string) => ragRequest<{ collection: string; path: string; content: string; hash: string }>(`file?collection=${encodeURIComponent(collection)}&path=${encodeURIComponent(path)}`);
  export const putRagFile = (collection: string, path: string, content: string, expected_hash: string | null) => ragRequest<{ path: string; hash: string }>("file", "PUT", { collection, path, content, expected_hash });
  ```
  Update `RagJob` to
  `{ id, status, input: { force?: boolean }, result, error, created_at, started_at, finished_at, collection: string, collection_id: string | null }`,
  and `RagHit`/`RagResults` to match the Task 5 search response
  (`collection` instead of `folder` and `kind`; `fallback`, `collections`).
  `RagConfig.settings = { max_file_bytes }`, and `areas` replaces `source_location`.

- [ ] **Step 1: Write the failing component tests**

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import RagCollectionsView from "../RagCollectionsView";
import type { RagCollection } from "../../lib/rag";

const base: RagCollection = {
  id: "c1", name: "jira", description: "Jira tickets", source_path: "rag-data/jira", search_mode: "full_text",
  embeddings_enabled: true, include: ["**/*.md"], exclude: [], chunk_size: 2000, chunk_overlap: 200, revision: 2,
  synced_revision: 2, last_synced_at: "2026-10-05T09:00:00Z", host_path: "/opt/ka/rag-data/jira", container_path: "/work/rag-data/jira",
  writable: true, folder_present: true, folder_changed_at: "2026-10-05T10:00:00Z", file_count: 75, truncated: false,
  skipped: { links: 0, special: 0 }, indexed_files: 75, chunks: 975, embedded_chunks: 0, needs_refresh: true,
  status: "needs_refresh", job: null,
};

test("lists collections with mapping, sync times, embeddings and Refresh", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => init?.method === "POST"
    ? Response.json({ id: "job" }, { status: 202 })
    : Response.json({ collections: [base, { ...base, id: "c2", name: "documents", source_path: "generated-documents", status: "up_to_date", needs_refresh: false, embedded_chunks: 10, chunks: 10 }] }));
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  const row = (await screen.findByText("jira")).closest("tr")!;
  expect(within(row).getByText("/opt/ka/rag-data/jira")).toBeInTheDocument();
  expect(within(row).getByText("0 / 975")).toBeInTheDocument();
  expect(within(row).getByText("Needs refresh")).toBeInTheDocument();
  const documents = screen.getByText("documents").closest("tr")!;
  expect(within(documents).queryByRole("button", { name: "Refresh" })).toBeNull();
  await userEvent.click(within(row).getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c1/sync", expect.objectContaining({ method: "POST" })));
});

test("missing folders explain themselves and failed syncs show the error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ collections: [
    { ...base, status: "folder_missing", folder_present: false },
    { ...base, id: "c3", name: "code", status: "failed", job: { id: "j", status: "failed", error: "Select an enabled embedding model", created_at: "", finished_at: "" } }] })));
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  expect(await screen.findByText("Folder missing")).toBeInTheDocument();
  expect(screen.getByText(/Select an enabled embedding model/)).toBeInTheDocument();
});

test("new collection posts the form and validates name and folder", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => init?.method === "POST"
    ? Response.json({ id: "new" }) : Response.json({ collections: [] }));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "New collection" }));
  await user.type(screen.getByLabelText("Name"), "payments-code");
  await user.clear(screen.getByLabelText("Folder"));
  await user.type(screen.getByLabelText("Folder"), "code/payments");
  await user.clear(screen.getByLabelText("Include patterns"));
  await user.type(screen.getByLabelText("Include patterns"), "**/*.py");
  await user.click(screen.getByRole("button", { name: "Create collection" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections", expect.objectContaining({ method: "POST" })));
  const body = JSON.parse((fetchMock.mock.calls.find(([, i]) => (i as RequestInit)?.method === "POST")![1] as RequestInit).body as string);
  expect(body).toMatchObject({ name: "payments-code", source_path: "code/payments", include: ["**/*.py"] });
});
```

In the detail view test (same file), render `RagCollectionDetail` for collection
`c1` at route `/rag/collections/c1`. The files response is
`[{path:"DEMO-1.md", chunks:13, embedded_chunks:0, stale:false, ...}]`. Expect
`13` and `0 embedded`. Clicking the file loads chunks and shows their text plus a
"Not embedded" badge.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/ragCollections.test.tsx`
Expected: FAIL, because the module does not exist.

- [ ] **Step 3: Implement the components**

`RagCollectionsView.tsx`:
- **Data:** load `getCollections()`. Poll every 5 s while any row is `syncing`
  (`setInterval`, cleared on unmount).
- **Header:** buttons "New collection" and "Refresh all stale" (`syncStale()`;
  show the notice "Queued: a, b" or "Nothing to refresh").
- **Table columns:**
  - Collection: a name link to `/rag/collections/:id`, with the description below;
  - Folder: `host_path ?? source_path` in `<code>`;
  - Files: `indexed_files / file_count`;
  - Embedded: `embedded_chunks / chunks`, or "Off" when `!embeddings_enabled`;
  - Folder changed and Last synced: `toLocaleString()` or "Never";
  - Status: a Badge with the label map
    `{up_to_date:"Up to date", needs_refresh:"Needs refresh", syncing:"Syncing", failed:"Failed", folder_missing:"Folder missing"}`.
    Under `failed`, show `job.error`;
  - Actions: a "Refresh" button when the status is `needs_refresh` or `failed`.
- **Refresh:** calls `syncCollection(id)`, then reloads. Errors appear in a
  destructive `Alert`.
- **Form:** "New collection" opens `RagCollectionForm` in a `Dialog`.

`RagCollectionForm.tsx`, fields:
- Name (`id="collection-name"`, label "Name");
- Description;
- Folder (label "Folder"): free text with the helper "Inside DATA_ROOT: code/…,
  code-index/…, rag-data/…, generated-documents, docs", plus quick-pick buttons for
  the five area names that set the text;
- Search mode (Select: "Keyword", "Semantic", "Hybrid");
- Embeddings (Switch);
- Include patterns and Exclude patterns: Textareas with one pattern per line, split
  on newlines and trimmed, empty lines dropped;
- Chunk size and Chunk overlap: number inputs.

Submit calls `saveCollection(value, id)`. The button reads "Create collection" or
"Save collection". Show API error messages verbatim. For edits, pass `revision`.

`RagCollectionDetail.tsx`:
- **Header:** the collection summary (same fields as the row) with Edit (opens the
  form), Refresh, a "Rebuild" button (`syncCollection(id, true)`), and Delete. Delete
  uses an AlertDialog: "Remove <name> from search? Files in the folder are kept." and
  then calls `deleteCollection` and navigates to `/rag/collections`.
- **Files table** from `getCollectionFiles`: path, chunks, embedded
  (`embedded_chunks/chunks`), indexed at, and a "Changed" badge when `stale`.
  Show the `skipped` counts and the `truncated` note.
- **Selecting a file:** shows its chunks from `getChunks`, each in a `<pre>` with a
  "Not embedded" badge when `!embedded`.
- **Writable collections:** also show an "Edit file" area with the existing hash
  semantics (`getRagFile`/`putRagFile`) and an "Upload file" input, which reads the
  file text and PUTs it with `expected_hash: null`.

Routes:
- `routes/ragCollections.tsx` renders `<RagCollectionsView />`;
- `routes/ragCollection.tsx` renders `<RagCollectionDetail id={params.collectionId!} />`;
- register both in `routes.ts`;
- make the AdminLayout `ragPage` lookup match `pathname.startsWith("/rag/collections")`.

- [ ] **Step 4: Run the admin-ui suite and typecheck**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test`
Expected: all pass. Remove the Files tests from `rag.test.tsx`; the
Jobs/Settings/Search tests are updated in Task 12. If they block this step, move
the Task 12 Step 3 type updates here.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui
git commit -m "feat(admin-ui): RAG Collections page with sync status, refresh and indexed content

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: admin-ui — Search, Settings, Jobs and agent collection pickers

**Files:**
- Modify: `services/admin-ui/app/components/RagSearchView.tsx`: multi-select of
  collection names, plus a mode select with the default "Each collection's mode".
- Modify: `services/admin-ui/app/components/RagSettingsView.tsx`: only
  `max_file_bytes`, the embedding model and the test button; the areas table lists
  the five host and container roots.
- Modify: `services/admin-ui/app/components/RagJobsView.tsx`: show `collection`;
  "Run again" POSTs `collections/{collection_id}/sync` when `collection_id` is set.
- Modify: `services/admin-ui/app/components/AgentProfileSettings.tsx:57-58`,
  `services/admin-ui/app/lib/profiles.ts:6-25`,
  `services/admin-ui/app/components/AgentWizard.tsx:38-39,104,122-124`,
  `services/admin-ui/app/lib/agentWizard.ts:16-25`:
  - `all_folders` → `all_collections`, `folder_ids` → `collection_ids`;
  - options `folders` → `collections` (label by `name`);
  - remove "Include subfolders".
- Modify: `services/admin-ui/app/components/SearchAgentView.tsx:35`:
  `documentHref` → `/rag/collections?name=` plus the collection name.
- Tests: `rag.test.tsx` (search, settings, jobs), `agentTabs.test.tsx`,
  `agentWizard.test.tsx`, `operations.test.tsx`.

**Interfaces:**
- Consumes: Task 11 `lib/rag.ts`; Task 7 profile API names.

- [ ] **Step 1: Update the tests first**

- In `rag.test.tsx`:
  - the search test selects collection `jira` (checkbox) and expects the body
    `{ query, collections: ["jira"], top }`, with no `mode` when the default is
    chosen;
  - the settings test saves `{ revision, settings: { max_file_bytes }, embedding_model_id }`;
  - the jobs test shows "jira" and "Run again" posts `/api/rag/collections/c1/sync`.
- In `agentTabs.test.tsx` and `agentWizard.test.tsx`:
  - replace `folders`/`folder_ids`/`all_folders`/`include_subfolders` with
    `collections`/`collection_ids`/`all_collections`;
  - assert the "All collections" switch and a collection checkbox labelled by name;
  - assert there is no "Include subfolders".

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd services/admin-ui && pnpm exec vitest run app/components/__tests__/rag.test.tsx app/components/__tests__/agentTabs.test.tsx app/components/__tests__/agentWizard.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

Apply the field renames in `lib/profiles.ts` and `lib/agentWizard.ts` and in the
two components. Remove the include-subfolders Field and switch. Rewrite
`RagSearchView` with:
- `getCollections()` for the checkbox list;
- a mode `Select` (`""` = each collection's mode, `full_text`, `embedding`, `hybrid`);
- a POST of `{ query, top: 10, ...(selected.length ? { collections: selected } : {}), ...(mode ? { mode } : {}) }`
  to `search`.

Results show `collection/path`, the score and the text. When `fallback` is
non-empty, show "Keyword search used for: …".

- [ ] **Step 4: Run the admin-ui suite and typecheck**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui
git commit -m "feat(admin-ui): search, settings, jobs and agent resources use RAG collections

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Part A docs and full verification

**Files:**
- Modify: `docs/rag.md`: rewrite "Source directory" as "Collections"; cover the
  model, settings, freshness, Refresh, search modes and fallback, and examples
  (`jira`, `documents`, `payments-code`).
- Modify: `docs/user-guide.md`:
  - RAG section 5.x: Collections replace Files & folders;
  - agent resources: collections;
  - Reader Advanced: collections;
  - limitations;
  - search section.
- Modify: `README.md` (the RAG paragraph), `services/rag/README.md`,
  `deploy/README.md` (the data folders table: RAG reads every area).

- [ ] **Step 1: Write the docs**

Use the spec's wording for the status meanings and the needs-refresh rules. In the
user guide, document the workflow:
1. Admin → RAG → Collections → **New collection**;
2. choose a folder;
3. **Refresh**;
4. watch Embedded go from `0 / N` to `N / N` when embeddings are on.

Also document that the `documents` collection is created automatically and that
old `v<n>/` folders are excluded.

- [ ] **Step 2: Run every suite**

Run:
```bash
cd services/rag && uv run --frozen pytest -q
cd ../admin-api && uv run --frozen pytest -q
cd ../docs-wiki && uv run --frozen pytest -q
cd ../admin-ui && pnpm exec tsc --noEmit && pnpm test
cd ../docs-wiki-ui && pnpm exec tsc --noEmit && pnpm test
cd ../.. && python3 -m unittest discover -s tests && bash db/tests/run.sh && make compose-validate
```
Expected: everything passes except the known existing admin-api failure
`test_environments.py::test_migration_reapply_and_populated_rollback_protection`.

- [ ] **Step 3: Commit**

```bash
git add docs README.md services/rag/README.md deploy/README.md
git commit -m "docs: RAG collections

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

# Part B — producers write where collections read (Tasks 14–16)

### Task 14: Jira export targets a collection

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/jira_export.py`
  (`TicketExportInput.folder` → `collection`, default `"jira"`;
  `publish_ticket(client, key, project, actor, collection, issue_key, content)`)
- Modify: `services/admin-api/src/knowledge_admin/rag_files.py`:
  `RagFiles.write_markdown(collection, filename, content)` and
  `RagFiles.collection(name) -> dict`. Delete `ensure_folder`, `queue_index` and
  `delete_file` if Task 16 removes their last users; for now keep them working on
  collections (`delete_file(collection, path, first_line)`).
- Modify: `services/admin-api/src/knowledge_admin/jira_api.py:218`
- Modify: `services/admin-ui/app/components/JiraTicketsView.tsx:31,108-112`: a
  "Target collection" select from `getCollections()`, filtered to `writable`, with
  `jira` preselected when present.
- Tests: `tests/test_jira_export.py`, `services/admin-ui/app/components/__tests__/jira.test.tsx`

**Interfaces:**
- `POST /api/jira/...export` takes `{"collection": "jira", ...}` instead of
  `folder`. When the collection is missing, it returns 404
  `"Unknown collection: <name>. Create it in RAG → Collections."`. When the
  collection is not in rag-data, it returns 400 `"This collection folder is read-only; ..."`
  (forwarded from RAG).

- [ ] **Step 1: Update `tests/test_jira_export.py` first**

Change the fake RAG (`exporting` fixture) so that:
- it serves `GET .../collections` with `{"collections": [{"name": "jira", "writable": true, ...}]}`;
- `GET/PUT file` expects the `collection` param or body key;
- there is no tree or folders route.

Assert that:
- the export writes `{"collection": "jira", "path": "DEMO-1.md", ...}`;
- the foreign-file check still returns 409;
- exporting to `docs-code` (`writable: false`) returns the read-only message.

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_jira_export.py tests/test_jira.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
    def collection(self, name: str) -> dict:
        rows = self.request('GET', 'collections')['collections']
        row = next((r for r in rows if r['name'] == name), None)
        if row is None:
            raise HTTPException(404, f'Unknown collection: {name}. Create it in RAG → Collections.')
        return row

    def write_markdown(self, collection: str, filename: str, content: str) -> dict:
        """Create or replace a file in a collection. A file whose first line differs belongs to another source and is kept."""
        self.collection(collection)
        try:
            previous = self.request('GET', 'file', params={'collection': collection, 'path': filename})
        except HTTPException as error:
            if error.status_code != 404:
                raise
            previous = None
        if previous is not None:
            if previous['content'].partition('\n')[0] != content.partition('\n')[0]:
                raise HTTPException(409, f'{collection}/{filename} belongs to another source. Choose a different collection; the existing file was preserved')
            if previous['content'] == content:
                return {'collection': collection, 'path': filename, 'status': 'unchanged', 'hash': previous['hash']}
        saved = self.request('PUT', 'file', json={'collection': collection, 'path': filename, 'content': content,
                                                  'expected_hash': previous['hash'] if previous is not None else None})
        return {'collection': collection, 'path': filename, 'status': 'updated' if previous is not None else 'created', 'hash': saved['hash']}
```

The Jira UI shows the result notice "Exported N tickets to collection jira.
Refresh the collection to update search." with a link to `/rag/collections`.

- [ ] **Step 4: Run the Jira tests, the admin-ui tests and the typecheck**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_jira_export.py tests/test_jira.py -q && cd ../admin-ui && pnpm exec tsc --noEmit && pnpm exec vitest run app/components/__tests__/jira.test.tsx`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api services/admin-ui
git commit -m "feat(jira): export tickets into a RAG collection

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Documents export the current version on every save

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/documents.py`:
  - `export_document` writes `<slug>/` (or `services/<svc>/<phase>/`) and removes
    files in that folder that the new export no longer contains, so diagrams from
    the previous version do not linger;
  - `save` exports after commit.
- Modify: `services/admin-api/src/knowledge_admin/documentation_generation.py`: the
  existing export call keeps working with the new layout.
- Tests: `tests/test_documents.py`, `tests/test_documentation_generation.py`

**Interfaces:**
- `export_document(document, *, phase_metadata=None) -> str` returns the folder
  path. The layout is `<root>/<slug>/README.md` (plus `architecture-N.d2` and
  `metadata.json`). Phase documents go to `<root>/services/<service>/<phase>/<phase>.md`.
  No `v<n>/` folder is created.
- `PUT/POST /api/documentation/documents` exports after saving. An export failure
  does not fail the save: the response adds `"export_error": "..."`.

- [ ] **Step 1: Write the failing tests**

```python
def test_saving_exports_current_version_in_place(client, tmp_path, monkeypatch):
    out = tmp_path / 'generated'
    out.mkdir()
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', str(out))
    created = client.post('/api/documentation/documents', headers=HEADERS, json={
        'slug': 'payments-runbook', 'title': 'Payments', 'markdown': '# Payments\n\n```d2\na -> b\n```\n'}).json()
    assert (out / 'payments-runbook' / 'README.md').read_text().startswith('# Payments')
    assert (out / 'payments-runbook' / 'architecture-1.d2').exists()
    client.put(f"/api/documentation/documents/{created['id']}", headers=HEADERS, json={
        'slug': 'payments-runbook', 'title': 'Payments', 'markdown': '# Payments v2', 'revision': 1})
    assert (out / 'payments-runbook' / 'README.md').read_text() == '# Payments v2\n' or (out / 'payments-runbook' / 'README.md').read_text().startswith('# Payments v2')
    assert not (out / 'payments-runbook' / 'architecture-1.d2').exists()
    assert not list(out.glob('payments-runbook/v*'))


def test_export_failure_keeps_the_saved_version(client, monkeypatch):
    monkeypatch.setenv('DOCUMENT_OUTPUT_ROOT', '/nonexistent/folder')
    response = client.post('/api/documentation/documents', headers=HEADERS, json={'slug': 'x', 'title': 'X', 'markdown': '# X'})
    assert response.status_code == 201 and 'export_error' in response.json()
```

Before writing the first test, check which file names `document_files()` produces
for a d2 block (it may be `architecture-1.d2`), and assert on the names it actually
produces. Update `tests/test_documentation_generation.py` expectations from
`.../v1/...` paths to the new layout.

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_documents.py -q -k export`
Expected: FAIL, because files land in `payments-runbook/v1/`.

- [ ] **Step 3: Implement**

In `export_document`:
- replace `folder = root / document['slug'] / f'v{document["revision"]}'` with
  `folder = root / document['slug']`;
- for phase documents, use `folder = root / 'services' / service_folder / phase`;
- after writing all files, delete the regular files in `folder` whose names are not
  in `files`, and leave directories alone. Old `v<n>/` subfolders are not touched;
  the `documents` collection excludes them:
  ```python
    for entry in folder.iterdir():
        if entry.is_file() and not entry.is_symlink() and entry.name not in files:
            entry.unlink()
  ```

In `documents.build_api` `save()`, after the commit:

```python
        try:
            with db.connect() as conn:
                document = conn.execute('''SELECT d.slug, v.markdown, d.revision FROM ka.documents d
                    JOIN ka.document_versions v ON v.id=d.current_version_id WHERE d.id=%s''', (saved['id'],)).fetchone()
            export_document({'slug': document['slug'], 'markdown': document['markdown'], 'revision': document['revision']})
        except (OSError, ValueError):
            saved = {**saved, 'export_error': 'Saved. Writing the document file failed; use Export to folder to retry.'}
        return saved
```

Match the dict keys `export_document` reads (`slug`, `markdown`, `revision`,
optional `source_commits`) to the real ones used by the existing "Export to folder"
endpoint in the same file, and reuse that endpoint's query.

- [ ] **Step 4: Run the documents and generation tests**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_documents.py tests/test_documentation_generation.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api
git commit -m "feat(documents): export the current version to <slug>/ on every save

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Remove publish-to-RAG

**Files:**
- Create: `db/migrations/0064_remove_document_publishing.up.sql`: the same
  statements as `0062_document_publishing.down.sql`.
- Create: `db/migrations/0064_remove_document_publishing.down.sql`: the same
  statements as `0062_document_publishing.up.sql`.
- Modify: `db/tests/run.sh`: pin `62` → `63`.
- Delete: `services/admin-api/src/knowledge_admin/document_publish.py`,
  `tests/test_document_publish.py`, `services/admin-ui/app/components/DocumentPublication.tsx`,
  `services/admin-ui/app/components/__tests__/documentPublication.test.tsx`.
- Modify:
  - `documents.py`: remove the publish routes, the `republish` call and the
    `document_publish` import;
  - `documentation_generation.py`: remove the `republish` block and `rag_client()`
    if unused;
  - `app.py`: remove `retry_pending` from the lifespan loop;
  - `DocumentEditorView.tsx`: remove `<DocumentPublication>`; the Version tab text
    becomes "Every save preserves a complete version and updates the documents
    collection's folder; refresh the collection to update search.";
  - `DocumentsView.tsx`: remove the Published badge;
  - `lib/documents.ts`: remove `publishDocument`/`unpublishDocument` and the
    `published_path`, `published_version`, `publish_error` and `index_pending`
    fields;
  - `deploy/compose.yaml`: remove `DOCS_WIKI_RAG_URL` and `RAG_BACKOFFICE_KEY` from
    `documentation-worker` if nothing else in the worker uses them (check with
    `rg -n "DOCS_WIKI_RAG_URL|RAG_BACKOFFICE_KEY" services/admin-api/src/knowledge_admin/documentation_generation.py`);
  - `services/docs-wiki/tests/test_deploy.py`: delete
    `test_documentation_worker_can_publish_to_rag`;
  - `docs/user-guide.md`, `docs/documentation.md`, `StorageOverviewView.tsx` (and
    its test): replace the "Publish to knowledge base" text with "Documents are
    searchable through the `documents` collection; refresh it in RAG → Collections".
- Tests: rename `tests/test_document_publish_migration.py` to cover 0064: the
  columns are gone after 0064 and back after its down migration.

**Interfaces:** none produced.

- [ ] **Step 1: Write the migration test**

```python
def test_0064_removes_publish_columns_and_down_restores_them(empty_database):
    conn = empty_database
    apply(conn, 64)
    columns = {r['column_name'] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='documents'")}
    assert not {'published_path', 'published_at', 'publish_error', 'index_pending'} & columns
    conn.execute((MIGRATIONS / '0064_remove_document_publishing.down.sql').read_text(), prepare=False)
    columns = {r['column_name'] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='ka' AND table_name='documents'")}
    assert {'published_path', 'published_at', 'publish_error', 'index_pending'} <= columns
```

Reuse the `apply` helper and the fixture name from Task 1.

- [ ] **Step 2: Run it and confirm it fails; then delete the code, write both migrations and pin `run.sh` to 63**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_document_publish_migration.py -q`
Expected: FAIL first, then PASS after the migration files exist.

- [ ] **Step 3: Run every affected suite**

Run: `cd services/admin-api && uv run --frozen pytest -q && cd ../admin-ui && pnpm exec tsc --noEmit && pnpm test && cd ../docs-wiki && uv run --frozen pytest -q && cd ../.. && bash db/tests/run.sh && make compose-validate`
Expected: all pass, apart from the known admin-api failure.

- [ ] **Step 4: Commit**

```bash
git add -A db services docs deploy
git commit -m "refactor(documents): remove publish-to-RAG; the documents collection indexes exports

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

# Part C — legacy cleanup (Task 17)

### Task 17: Remove the legacy SQLite collections code

**Files:**
- Verify first:
  `rg -n "rag.store|rag.config import|mcp_facade|retrieval import|index_collection|/collections/" services/rag/src services/docs-wiki/src services/admin-api/src deploy scripts`.
  The remaining users after Parts A and B should be:
  - `services/rag/src/rag/api.py` (legacy routes and `create_app(Config...)`);
  - `cli.py`;
  - `anchors.py`;
  - `services/docs-wiki/src/docs_wiki/rag_client.py`, the base class of
    `WorkspaceRagClient`.
- Delete:
  - `services/rag/src/rag/store/`, `retrieval.py`, `mcp_facade.py`, `content.py`,
    `anchors.py` (if only legacy code uses it), `config.py`;
  - the legacy parts of `ingest.py`: keep `chunk_sections`, `_split_oversized` and
    whatever they need, and move them to `services/rag/src/rag/chunking.py`;
  - the legacy tests: `test_store.py`, `test_retrieval.py`, `test_index.py`,
    `test_mcp.py`, `test_config.py`, `test_api.py` (legacy routes), and
    `test_ingest_warnings.py` if it is legacy-only.
- Modify:
  - `services/rag/src/rag/api.py` becomes a factory that mounts only the workspace
    router: `create_app(workspace=None, workspace_key=None)`, keeping the exception
    handlers;
  - `cli.py` keeps only `serve-workspace` and `worker`;
  - `workspace_worker.py` imports `chunk_sections` from `rag.chunking`;
  - `services/docs-wiki/src/docs_wiki/rag_client.py`: fold the HTTP helper
    (`_request`, errors) into `workspace_client.py`, then delete the legacy client.
    `webapp.py` creates `WorkspaceRagClient` only when `RAG_BACKOFFICE_KEY` and a
    RAG URL are set, and otherwise runs without RAG, as `rag_client=None` does today;
  - the `test_workspace.py` fixture uses the new `create_app` signature.
- Tests: `services/rag/tests/test_chunker.py` keeps passing against `rag.chunking`.

**Interfaces:**
- `rag.chunking.chunk_sections(sections, chunk_size, overlap)` keeps the same
  signature as `ingest.chunk_sections`.

- [ ] **Step 1: Run the verification grep and record which users remain.** Any user
  outside the listed files must be migrated first, or reported as a blocker.
- [ ] **Step 2: Move `chunk_sections` to `rag/chunking.py`**, point
  `workspace_worker.py` and `test_chunker.py` at it, and run
  `uv run --frozen pytest tests/test_chunker.py tests/test_sync.py -q`. Expected: pass.
- [ ] **Step 3: Delete the legacy modules and tests, and rewrite `api.py`/`cli.py`.**
  Run `cd services/rag && uv run --frozen pytest -q`. Expected: pass.
- [ ] **Step 4: Fold the docs-wiki client** and run
  `cd services/docs-wiki && uv run --frozen pytest -q`. Expected: pass.
- [ ] **Step 5: Run every suite and `make compose-validate`.** Update
  `services/rag/README.md` and `docs/architecture.md` to drop the legacy API.
- [ ] **Step 6: Commit**

```bash
git add -A services docs
git commit -m "refactor(rag): remove the legacy SQLite collections API

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
