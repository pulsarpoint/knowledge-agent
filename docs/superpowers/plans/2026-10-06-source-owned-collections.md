# Collections with Source Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RAG becomes an indexing service for sources. A collection is a named
search index with its own settings and a list of **paths** (folders in `DATA_ROOT`).
Sources add their folders to the collection they choose and remove them again:
Docs → Documents, Jira export and searchable code projects. Admin → RAG →
Collections shows each collection's paths and sources, edits search settings only,
and keeps Refresh.

**Architecture:**
- **Migration 0065** adds `ka.rag_collection_paths`. Each collection's
  `source_path` becomes its single path, with backfilled owners. Indexed files are
  rewritten to full `DATA_ROOT` paths and linked to their path with `path_id`
  (`ON DELETE CASCADE`). `rag_collections.source_path` is dropped.
  `ka.repositories` gains `search_collection` and `search_error`.
- **The RAG service** walks every path of a collection into one `Walk`, with full
  file paths. It adds a small source API: `PUT collections/by-name/{name}`,
  `PUT|DELETE collections/by-name/{name}/paths` and `DELETE paths`. It also
  provides settings-only edits, owner-guarded removal and a full-path file API.
  Sync, freshness and search work across all paths.
- **admin-api:**
  - `rag_collections.py` is the source-side client;
  - Jira export adds `rag-data/jira` to the chosen collection before writing;
  - code projects get `PUT /api/code-projects/{id}/search`, and folder edits and
    archiving reconcile their paths;
  - agent configs carry `paths`, with a shim for older snapshots.
- **admin-ui:**
  - Collections lists paths and sources, with no New collection and no upload or
    edit;
  - code projects get a **Searchable** switch;
  - the Jira dialog takes a free collection name.

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations;
- FastAPI and psycopg3 (`services/rag`, `services/admin-api`, `services/docs-wiki`);
- React Router 7 with shadcn/ui and Vitest (`services/admin-ui`);
- pytest. The RAG and admin-api tests run a real PostgreSQL container from the
  `knowledge-agent-v3-postgres:17.10-vector0.8.6` image.

**Spec:** `docs/superpowers/specs/2026-10-06-source-owned-collections-design.md`
(builds on `docs/superpowers/specs/2026-10-05-rag-collections-design.md`).

## Global Constraints

- Collection `name`: `^[a-z0-9]+(-[a-z0-9]+)*$`, at most 60 characters, unique among non-deleted collections; never editable.
- Collection `description`: at most 2000 characters.
- Search settings per collection: `search_mode` (`full_text` | `embedding` | `hybrid`), `embeddings_enabled`, `include`, `exclude`, `chunk_size` 200..12000, `chunk_overlap` 0..2000 and smaller than `chunk_size`.
- Defaults for a collection created by a source: search mode `full_text`, embeddings off, include `{**/*.md,**/*.markdown,**/*.txt}`, exclude `{}`, chunk size 2000, overlap 200.
- `source_path`: relative to `DATA_ROOT`, at most 500 characters, first segment one of `code`, `code-index`, `rag-data`, `generated-documents`, `docs`, each later segment `[A-Za-z0-9_-][A-Za-z0-9._-]*`.
- Path owner: `owner_kind` `documents` | `jira` | `code_project` | NULL; `owner_id` uuid or NULL; `owner_key` text (1..500 characters) or NULL; `owner_id`/`owner_key` require `owner_kind`.
- Within one collection, paths may not overlap: equal paths, or one containing the other (`a == b`, or `b` starts with `a + '/'`).
- Different collections may share paths.
- One owner (`owner_kind`, `owner_id`, `owner_key`) has at most one path in the workspace. Adding it again re-points it, also from another collection.
- `rag_files.path` is the full path under `DATA_ROOT` (for example `code/payments/src/retry.py`), unique per collection; `rag_files.path_id` → `rag_collection_paths(id)` `ON DELETE CASCADE`.
- Include and exclude patterns match a file's path **relative to its collection path**.
- Limits per collection (all paths together): 20,000 files and 200 MB per sync; 200,000 entries per sync walk and 20,000 per status walk.
- Adding or removing a path bumps the collection `revision`. Re-adding an identical path does not.
- Jira: path `rag-data/jira`, owner `jira`; default collection `jira`.
- Documents: path `generated-documents`, owner `documents`, collection `documents`.
- Code project: path `code/<folder>` per mapped folder, owner `code_project`, `owner_id` = project id, `owner_key` = folder. The default collection is the project's slug (see Decisions).
- Only `rag-data` accepts file writes (`PUT file`); there is no file delete API.
- Error texts:
  - overlap: `Path overlaps <other> in collection <name>`;
  - missing path: starts `Path <p> is missing`;
  - collection removal: `Remove this collection's sources first: <sources>`;
  - RAG outage in admin-api: `RAG service is unavailable. Retry later.`
- Migration number 0065. `db/tests/run.sh` pins the rolled-back version to `64`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12: no `PurePath.full_match`.

## Review Focus

1. **Overlapping paths in one collection.** Examples: adding `code` or
   `code/payments/src` next to `code/payments`, or exporting Jira into a collection
   that already holds `rag-data`. The result must be a 409 `Path overlaps <other>
   in collection <name>`, nothing must change, and the Jira export must show that
   message.
   Pinned in Task 3 (`test_overlapping_path_is_refused_and_nothing_changes`) and
   Task 5 (`test_export_fails_with_rag_overlap_message`).
2. **One missing (unmounted) path in a multi-path collection.**
   - The status is `folder_missing` and names the path.
   - Refresh is refused before queueing.
   - A sync that was already queued fails in the worker with `Path <p> is missing`.
   - The index keeps every file.

   Pinned in Task 3 (`test_status_reports_each_path_and_missing_paths`) and Task 4
   (`test_missing_path_fails_sync_and_keeps_index`).
3. **A source that changes its collection.** Examples: a Jira export to another
   collection, a code project's collection change, or a folder edit. The path must
   move rather than be duplicated, and the old collection's files must leave
   search. Pinned in:
   - Task 3: `test_owner_path_moves_between_collections`;
   - Task 4: `test_removing_a_path_drops_its_files_from_search`;
   - Task 6: `test_changing_collection_moves_project_paths`.
4. **Old relative paths and paths outside a collection.** Examples:
   `src/retry.py`, `code/paymentsX/...` and `code/payments/.env`. They must never
   be served. Full paths must round-trip through agents (`read_document`) and the
   Reader (`<collection>/<full path>`). Pinned in:
   - Task 3: `test_file_api_serves_only_paths_under_the_collection`;
   - Task 7: `test_read_document_passes_full_path` and
     `test_workspace_doc_reads_full_path`.
5. **RAG is unavailable during a source action.**
   - A code project change is still saved, shows the error with Retry, and Retry
     reconciles.
   - A Jira export fails with the message and writes nothing.

   Pinned in Task 6 (`test_rag_failure_keeps_project_and_retry_reconciles`) and
   Task 5 (`test_export_fails_when_rag_is_unavailable`).

## Decisions (where the spec leaves a choice)

- **Code-project searchability is stored on the repository row.**
  - Migration 0065 adds `ka.repositories.search_collection` (slug or NULL; NULL
    means not searchable) and `search_error` (the last RAG failure, or NULL).
  - `GET /api/code-projects` reads them without calling RAG. `searchable` is
    `search_collection IS NOT NULL` and `collection` is `search_collection`.
  - RAG remains the source of truth for the paths themselves.
    `reconcile()` (Task 6) lists the project's owned paths from RAG and corrects
    them, so **Retry** is idempotent.
- **Default collection for a code project:** the project name lowercased, with
  every run of other characters replaced by `-`, trimmed of `-` and cut to 60
  characters. If nothing remains, it is `code-<first 8 hex of id>`. The existing
  `repositories.slug` column is `repository-<hex>` and is not meaningful to users.
- **Paths of indexed files:**
  - `rag_files.path` holds the full `DATA_ROOT`-relative path, unique per
    collection.
  - `rag_files.path_id` has a composite FK `(path_id, collection_id)` →
    `rag_collection_paths(id, collection_id)` `ON DELETE CASCADE`. Removing a path
    therefore removes its files and their chunks, and a file can never point at
    another collection's path.
  - Orphaned vectors are then cleaned under the workspace lock, as collection
    deletion does today.
- **Overlap check:** done in Python, under the collection row lock
  (`SELECT … FOR UPDATE`, taken by `ensure_collection`), with
  `overlaps(a, b) = a == b or a.startswith(b + '/') or b.startswith(a + '/')`. A
  path being re-pointed by the same owner, or an identical unowned path being
  adopted, is skipped.
- **Owner uniqueness:**
  - A unique index on `(owner_kind, owner_id, owner_key) NULLS NOT DISTINCT WHERE
    owner_kind IS NOT NULL` gives each owner one path in the workspace, which is
    single-project.
  - `PUT …/paths` with an owner first deletes that owner's existing path, in any
    collection. That is how "choosing a different collection moves the path" works
    for Jira and code projects.
  - An identical unowned path in the target collection is adopted: its owner
    columns are set, and its files are kept.
- **Removing an owned path by `source_path` is refused:** 409 `Path <p> belongs to
  <source>; switch it off there`. Only the owner API (`owner_kind`…) removes owned
  paths, so the UI's **Detach** cannot break a source.
- **Generalising walks to many paths:**
  - `Areas.walk(…, into=walk)` adds files to a shared `Walk` under full paths,
    tagged with `source`. Patterns still match the path relative to its folder.
  - `Areas.walk_paths(sources, …)` walks each path in turn. The file and entry
    caps count the whole collection.
  - Status walks (`truncate=True`) record missing paths in `Walk.missing` and keep
    walking the others. Sync walks raise `FolderMissing` (`Path <p> is missing…`)
    on the first missing path, so nothing is removed.
  - `folder_changed_at` is the newest mtime across all paths.
- **Archiving:**
  - No archive route exists today; `archived_at` is only set in SQL.
  - Task 6 adds `POST /api/code-projects/{id}/archive`, which archives the project
    and then removes its paths from RAG. No UI button is added: the spec does not
    ask for one.
- **Down migration for collections without paths** (created by
  `PUT collections/by-name/{name}` and never given a path): `source_path` becomes
  `rag-data/<name>`, the folder the old create API would have used.
- **Edit and Remove** stay on the collection detail page, where they already are.
  The list keeps Refresh, and each row links to its detail page.
- **docs-wiki needs no code change:**
  - The Reader path is `<collection>/<full path>`.
  - `WorkspaceRagClient.get_document` splits at the first `/` and forwards the
    rest unchanged.

  Task 7 pins this with tests.

## Delivery and test boundaries

Parts A–C land together on `feature/source-owned-collections`. Migration 0065
drops `rag_collections.source_path`, so between Task 1 and Task 4 the RAG
database tests fail. Between Task 1 and Task 7, the admin-api agent-config tests
fail. Each task lists the exact tests that must pass when it lands, and Task 11
runs every suite.

---

# Part A — schema

### Task 1: Migration 0065 — collection paths, full file paths, code project search columns

**Files:**
- Create: `db/migrations/0065_rag_collection_paths.up.sql`
- Create: `db/migrations/0065_rag_collection_paths.down.sql`
- Modify: `db/tests/run.sh`: change `[ "$rolled_back_version" = 63 ]` to `64`.
- Modify: `services/admin-api/tests/test_rag_collections_migration.py`. `_seed` first rolls back 0065, so the 0063 test still starts from 0062.
- Modify: `services/admin-api/tests/test_todo.py`. Its seed inserts a collection without `source_path`.
- Test: `services/admin-api/tests/test_rag_collection_paths_migration.py`

**Interfaces:**
- Consumes: the 0063 schema (`ka.rag_collections.source_path`, `ka.rag_files(collection_id, path relative)`).
- Produces:
  - `ka.rag_collection_paths(id uuid PK, collection_id uuid → rag_collections ON DELETE CASCADE, source_path text, owner_kind text NULL, owner_id uuid NULL, owner_key text NULL, created_at timestamptz)`. It has `UNIQUE (collection_id, source_path)` and `UNIQUE (id, collection_id)`, and unique index `rag_collection_paths_owner`.
  - `ka.rag_files.path_id uuid NOT NULL`, with FK `rag_files_path_fk (path_id, collection_id)` → `rag_collection_paths(id, collection_id)` `ON DELETE CASCADE` and index `rag_files_path`. `rag_files.path` holds full paths.
  - `ka.rag_collections` without `source_path`.
  - `ka.repositories.search_collection text NULL` (slug, ≤ 60) and `ka.repositories.search_error text NULL` (≤ 2000).

- [ ] **Step 1: Write the failing migration test**

Create `services/admin-api/tests/test_rag_collection_paths_migration.py`:

```python
"""0065 moves each collection's folder into rag_collection_paths and indexes files under full paths."""
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0065_rag_collection_paths.up.sql', '0065_rag_collection_paths.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


@pytest.fixture
def at_0064(store):
    """A database rolled back to 0064 with five collections, two indexed files and their chunks."""
    with store.connect() as conn:
        run(conn, DOWN)
        project = conn.execute("SELECT id FROM ka.projects LIMIT 1").fetchone()
        if project is None:
            project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('t','T') RETURNING id").fetchone()
        project = project['id']
        conn.execute("""INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{"max_file_bytes":2000000}')
            ON CONFLICT (project_id) DO NOTHING""", (project,))
        ids = {}
        for name, source in (('jira', 'rag-data/jira'), ('jira-copy', 'rag-data/jira'), ('documents', 'generated-documents'),
                             ('payments', 'code/payments'), ('gone', 'rag-data/gone')):
            ids[name] = conn.execute("""INSERT INTO ka.rag_collections(project_id,name,source_path,synced_revision,last_synced_at)
                VALUES (%s,%s,%s,1,now()) RETURNING id""", (project, name, source)).fetchone()['id']
        conn.execute("UPDATE ka.rag_collections SET deleted_at=now() WHERE id=%s", (ids['gone'],))
        conn.execute("UPDATE ka.rag_collections SET created_at=now() + interval '1 minute' WHERE id=%s", (ids['jira-copy'],))
        for name, path in (('jira', 'DEMO-1.md'), ('payments', 'src/retry.py')):
            job = conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input,status,started_at,finished_at)
                VALUES (%s,'rag_collection_sync',%s,'{}','succeeded',now(),now()) RETURNING id""", (project, ids[name])).fetchone()['id']
            file_id = conn.execute("""INSERT INTO ka.rag_files(project_id,collection_id,path,content_hash,processing_hash,embedding_profile,job_id)
                VALUES (%s,%s,%s,repeat('a',64),repeat('b',64),'',%s) RETURNING id""", (project, ids[name], path, job)).fetchone()['id']
            conn.execute("INSERT INTO ka.rag_chunks(project_id,file_id,position,text,input_hash) VALUES (%s,%s,0,'Retry policy',repeat('c',64))",
                         (project, file_id))
        yield conn, project, ids


def test_each_collection_gets_its_folder_as_a_path_with_owners(at_0064):
    conn, _, ids = at_0064
    run(conn, UP)
    paths = {r['name']: (r['source_path'], r['owner_kind'], r['owner_id'], r['owner_key']) for r in conn.execute(
        """SELECT c.name, p.source_path, p.owner_kind, p.owner_id, p.owner_key FROM ka.rag_collection_paths p
        JOIN ka.rag_collections c ON c.id=p.collection_id""").fetchall()}
    assert paths == {
        'jira': ('rag-data/jira', 'jira', None, None),
        'jira-copy': ('rag-data/jira', None, None, None),  # one owner per path: the older collection keeps it
        'documents': ('generated-documents', 'documents', None, None),
        'payments': ('code/payments', None, None, None),
        'gone': ('rag-data/gone', None, None, None)}
    assert 'source_path' not in conn.execute("SELECT * FROM ka.rag_collections LIMIT 1").fetchone()
    row = conn.execute("SELECT revision,synced_revision FROM ka.rag_collections WHERE id=%s", (ids['jira'],)).fetchone()
    assert row['synced_revision'] == row['revision']  # same folder, same files: no refresh needed
    columns = {r['column_name'] for r in conn.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_schema='ka' AND table_name='repositories'""").fetchall()}
    assert {'search_collection', 'search_error'} <= columns


def test_indexed_files_move_to_full_paths_and_cascade_with_their_path(at_0064):
    conn, _, ids = at_0064
    run(conn, UP)
    files = {r['path']: (r['collection_id'], r['source_path']) for r in conn.execute("""SELECT f.path, f.collection_id, p.source_path
        FROM ka.rag_files f JOIN ka.rag_collection_paths p ON p.id=f.path_id""").fetchall()}
    assert files == {'rag-data/jira/DEMO-1.md': (ids['jira'], 'rag-data/jira'),
                     'code/payments/src/retry.py': (ids['payments'], 'code/payments')}
    conn.execute("DELETE FROM ka.rag_collection_paths WHERE collection_id=%s", (ids['payments'],))
    assert conn.execute("SELECT count(*) AS n FROM ka.rag_files").fetchone()['n'] == 1
    assert conn.execute("SELECT count(*) AS n FROM ka.rag_chunks").fetchone()['n'] == 1


def test_path_rules_are_enforced(at_0064):
    conn, _, ids = at_0064
    run(conn, UP)
    insert = "INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind,owner_id,owner_key) VALUES (%s,%s,%s,%s,%s)"
    for values, constraint in (
            ((ids['payments'], 'rag-data/other', 'jira', None, None), 'rag_collection_paths_owner'),
            ((ids['payments'], '../etc', None, None, None), 'rag_collection_paths_source_path_check'),
            ((ids['payments'], 'code/x', None, ids['jira'], None), 'rag_collection_paths_check'),
            ((ids['payments'], 'code/payments', None, None, None), 'rag_collection_paths_collection_id_source_path_key'),
            ((ids['payments'], 'code/y', 'wiki', None, None), 'rag_collection_paths_owner_kind_check')):
        with pytest.raises(Exception, match=constraint):
            with conn.transaction():
                conn.execute(insert, values)
    checks = {r['conname'] for r in conn.execute("""SELECT conname FROM pg_constraint
        WHERE conrelid='ka.repositories'::regclass AND contype='c'""").fetchall()}
    assert {'repositories_search_collection_check', 'repositories_search_error_check'} <= checks


def test_rollback_refuses_collections_with_several_paths(at_0064):
    conn, _, ids = at_0064
    run(conn, UP)
    conn.execute("INSERT INTO ka.rag_collection_paths(collection_id,source_path) VALUES (%s,'docs/runbooks')", (ids['payments'],))
    with pytest.raises(Exception, match='Collection payments has more than one path'):
        with conn.transaction():
            run(conn, DOWN)
    conn.execute("DELETE FROM ka.rag_collection_paths WHERE source_path='docs/runbooks'")
    run(conn, DOWN)
    rows = {r['name']: r['source_path'] for r in conn.execute("SELECT name, source_path FROM ka.rag_collections").fetchall()}
    assert rows == {'jira': 'rag-data/jira', 'jira-copy': 'rag-data/jira', 'documents': 'generated-documents',
                    'payments': 'code/payments', 'gone': 'rag-data/gone'}
    assert {r['path'] for r in conn.execute("SELECT path FROM ka.rag_files").fetchall()} == {'DEMO-1.md', 'src/retry.py'}
    assert conn.execute("SELECT to_regclass('ka.rag_collection_paths') AS t").fetchone()['t'] is None


def test_rollback_gives_pathless_collections_a_rag_data_folder(at_0064):
    conn, project, _ = at_0064
    run(conn, UP)
    conn.execute("INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,'fresh')", (project,))
    run(conn, DOWN)
    assert conn.execute("SELECT source_path FROM ka.rag_collections WHERE name='fresh'").fetchone()['source_path'] == 'rag-data/fresh'
```

Both `jira` and `jira-copy` point at `rag-data/jira`. The fixture makes `jira-copy`
a minute younger, so the oldest live collection (`jira`) deterministically keeps the
`jira` owner.

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_rag_collection_paths_migration.py -q`
Expected: FAIL, because `0065_rag_collection_paths.down.sql` does not exist yet.

- [ ] **Step 3: Write `db/migrations/0065_rag_collection_paths.up.sql`**

```sql
SET search_path = ka, public;

-- A collection indexes several folders; each may belong to the source that writes it.
CREATE TABLE rag_collection_paths (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id uuid NOT NULL REFERENCES rag_collections(id) ON DELETE CASCADE,
    source_path text NOT NULL CHECK (length(source_path) <= 500 AND source_path ~
        '^(code|code-index|rag-data|generated-documents|docs)(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$'),
    owner_kind text CHECK (owner_kind IN ('documents', 'jira', 'code_project')),
    owner_id uuid,
    owner_key text CHECK (length(owner_key) BETWEEN 1 AND 500),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (owner_kind IS NOT NULL OR (owner_id IS NULL AND owner_key IS NULL)),
    UNIQUE (collection_id, source_path),
    UNIQUE (id, collection_id)
);
-- A source owns at most one path; adding it again re-points it. The RAG workspace is single-project.
CREATE UNIQUE INDEX rag_collection_paths_owner ON rag_collection_paths(owner_kind, owner_id, owner_key)
    NULLS NOT DISTINCT WHERE owner_kind IS NOT NULL;
COMMENT ON TABLE rag_collection_paths IS 'A folder under DATA_ROOT indexed into a collection, optionally owned by the source that writes it.';

-- Each collection's folder becomes its single path. Documents and Jira folders get their
-- owner; when several live collections share such a folder, the oldest one keeps the owner.
INSERT INTO rag_collection_paths(collection_id, source_path, owner_kind, created_at)
SELECT c.id, c.source_path,
       CASE WHEN c.deleted_at IS NOT NULL OR c.rank > 1 THEN NULL
            WHEN c.source_path = 'generated-documents' THEN 'documents'
            WHEN c.source_path = 'rag-data/jira' THEN 'jira' END,
       c.created_at
FROM (SELECT *, row_number() OVER (PARTITION BY source_path, deleted_at IS NULL ORDER BY created_at, id) AS rank
      FROM rag_collections) c;

-- Indexed files keep their chunks and vectors; their paths become full DATA_ROOT paths.
ALTER TABLE rag_files ADD COLUMN path_id uuid;
UPDATE rag_files f SET path_id = p.id, path = p.source_path || '/' || f.path
FROM rag_collection_paths p WHERE p.collection_id = f.collection_id;
ALTER TABLE rag_files ALTER COLUMN path_id SET NOT NULL,
    ADD CONSTRAINT rag_files_path_fk FOREIGN KEY (path_id, collection_id)
        REFERENCES rag_collection_paths(id, collection_id) ON DELETE CASCADE;
CREATE INDEX rag_files_path ON rag_files(path_id);

ALTER TABLE rag_collections DROP COLUMN source_path;
COMMENT ON TABLE rag_collections IS 'Searchable unit with its own settings; its folders are listed in rag_collection_paths.';

-- Code projects remember whether they are searchable, in which collection, and the last RAG error.
ALTER TABLE repositories
    ADD COLUMN search_collection text CHECK (search_collection ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(search_collection) <= 60),
    ADD COLUMN search_error text CHECK (length(search_error) <= 2000);
```

- [ ] **Step 4: Write `db/migrations/0065_rag_collection_paths.down.sql`**

```sql
SET search_path = ka, public;

DO $$
DECLARE crowded text;
BEGIN
    SELECT c.name INTO crowded FROM rag_collections c JOIN rag_collection_paths p ON p.collection_id = c.id
    WHERE c.deleted_at IS NULL GROUP BY c.id, c.name HAVING count(*) > 1 ORDER BY c.name LIMIT 1;
    IF crowded IS NOT NULL THEN
        RAISE EXCEPTION 'Collection % has more than one path; remove the extra paths before rolling back 0065', crowded;
    END IF;
END $$;

ALTER TABLE repositories DROP COLUMN search_error, DROP COLUMN search_collection;

-- A collection without a path gets the rag-data folder the old create API would have used.
ALTER TABLE rag_collections ADD COLUMN source_path text;
UPDATE rag_collections c SET source_path = coalesce(
    (SELECT p.source_path FROM rag_collection_paths p WHERE p.collection_id = c.id ORDER BY p.created_at, p.source_path LIMIT 1),
    'rag-data/' || c.name);
ALTER TABLE rag_collections ALTER COLUMN source_path SET NOT NULL,
    ADD CONSTRAINT rag_collections_source_path_check CHECK (length(source_path) <= 500 AND source_path ~
        '^(code|code-index|rag-data|generated-documents|docs)(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$');

-- Only deleted collections can still have files under a second path; those rows cannot be represented.
DELETE FROM rag_files f USING rag_collections c, rag_collection_paths p
WHERE c.id = f.collection_id AND p.id = f.path_id AND p.source_path <> c.source_path;
UPDATE rag_files f SET path = substr(f.path, length(p.source_path) + 2)
FROM rag_collection_paths p WHERE p.id = f.path_id;
ALTER TABLE rag_files DROP COLUMN path_id;
DROP TABLE rag_collection_paths;
COMMENT ON TABLE rag_collections IS 'Searchable unit: one folder under DATA_ROOT indexed in place with its own settings.';
```

- [ ] **Step 5: Keep the older migration test and the seeds working**

In `services/admin-api/tests/test_rag_collections_migration.py`, make the first
line of `_seed(conn)` roll back 0065 before 0063:

```python
def _seed(conn):
    conn.execute((MIGRATIONS / '0065_rag_collection_paths.down.sql').read_text(), prepare=False)
    conn.execute((MIGRATIONS / '0063_rag_collections.down.sql').read_text(), prepare=False)
```

The rest of `_seed` is unchanged.

In `services/admin-api/tests/test_todo.py`
(`test_delete_all_resource_joins_preserves_shared_catalogs`), replace the
collection insert with:

```python
        collection = conn.execute("INSERT INTO ka.rag_collections(project_id,name) VALUES (%s,'shared') RETURNING id", (project,)).fetchone()['id']
```

In `db/tests/run.sh`, change `[ "$rolled_back_version" = 63 ]` to
`[ "$rolled_back_version" = 64 ]`.

- [ ] **Step 6: Run the migration tests and the database suite**

Run:
```bash
cd services/admin-api && uv run --frozen pytest tests/test_rag_collection_paths_migration.py tests/test_rag_collections_migration.py \
  "tests/test_todo.py::test_delete_all_resource_joins_preserves_shared_catalogs" -q
cd ../.. && bash db/tests/run.sh
```
Expected: 5 + 3 + 1 passed, and every `db/tests/run.sh` check prints PASS.

- [ ] **Step 7: Commit**

```bash
git add db/migrations/0065_rag_collection_paths.*.sql db/tests/run.sh \
  services/admin-api/tests/test_rag_collection_paths_migration.py services/admin-api/tests/test_rag_collections_migration.py \
  services/admin-api/tests/test_todo.py
git commit -m "feat(db): collections hold several source paths; files indexed under full paths

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

# Part B — RAG service (Tasks 2–4)

### Task 2: RAG `collections.py` — walks over many paths

**Files:**
- Modify: `services/rag/src/rag/collections.py`
- Test: `services/rag/tests/test_collections.py`

**Interfaces:**
- Produces, in `rag.collections`:
  - `Walk` gains `missing: list[str]` (paths not found during a truncating walk)
    and `visited: int` (entries visited, shared across paths);
  - `FolderMissing(source)`: HTTP 409 with detail `Path <source> is missing. Nothing
    was removed from the index; restore the folder or remove the path from the
    collection.` It exposes `.source`;
  - `overlaps(a: str, b: str) -> bool`;
  - `Areas.walk(source, include, exclude, *, limit=MAX_FILES, truncate=False,
    max_entries=None, into: Walk | None = None) -> Walk`:
    - every file entry is `{path, source, size, mtime}`;
    - `path` is relative to `source` when `into is None`, otherwise the full
      `DATA_ROOT` path `f'{source}/{relative}'`;
    - patterns always match the relative path;
  - `Areas.walk_paths(sources: list[str], include, exclude, *, limit=MAX_FILES,
    truncate=False, max_entries=None) -> Walk`: one result with full paths, caps
    counted across all paths. With `truncate`, missing paths go to `Walk.missing`;
    without it, the first missing path raises `FolderMissing`.

- [ ] **Step 1: Write the failing tests**

Change the import line of `services/rag/tests/test_collections.py` and append the
tests:

```python
from rag.collections import (AREAS, Areas, FolderMissing, glob_regex, indexable, matches, overlaps, relative_path,
                             source_path, visible)


def test_walk_paths_merges_full_paths_and_reports_missing(areas):
    areas, roots = areas
    repo = roots['code'] / 'payments'
    (repo / 'src').mkdir(parents=True)
    (repo / 'tests').mkdir()
    (repo / 'src' / 'retry.py').write_text('x')
    (repo / 'tests' / 'test_retry.py').write_text('x')
    (roots['rag-data'] / 'jira').mkdir()
    (roots['rag-data'] / 'jira' / 'DEMO-1.md').write_text('# D')
    walk = areas.walk_paths(['code/payments', 'rag-data/jira', 'docs/absent'], ['**/*.py', '**/*.md'], ['tests/**'], truncate=True)
    assert [(f['path'], f['source']) for f in walk.files] == [
        ('code/payments/src/retry.py', 'code/payments'), ('rag-data/jira/DEMO-1.md', 'rag-data/jira')]
    assert walk.missing == ['docs/absent'] and not walk.truncated
    with pytest.raises(FolderMissing, match='Path docs/absent is missing') as error:
        areas.walk_paths(['code/payments', 'docs/absent'], ['**/*.py'], [])
    assert error.value.status_code == 409 and error.value.source == 'docs/absent'


def test_walk_paths_limits_count_the_whole_collection(areas):
    areas, roots = areas
    for name in ('a', 'b'):
        folder = roots['rag-data'] / name
        folder.mkdir()
        for i in range(3):
            (folder / f'{i}.md').write_text('x')
    with pytest.raises(HTTPException, match='more than 4 files'):
        areas.walk_paths(['rag-data/a', 'rag-data/b'], ['**/*.md'], [], limit=4)
    walk = areas.walk_paths(['rag-data/a', 'rag-data/b'], ['**/*.md'], [], limit=4, truncate=True)
    assert walk.truncated and len(walk.files) == 4
    with pytest.raises(HTTPException, match='more than 5 entries'):
        areas.walk_paths(['rag-data/a', 'rag-data/b'], ['**/*.md'], [], max_entries=5)


def test_walk_paths_newest_change_spans_paths(areas):
    areas, roots = areas
    old, new = time.time() - 7200, time.time() - 60
    for name, stamp in (('a', old), ('b', new)):
        folder = roots['rag-data'] / name
        folder.mkdir()
        (folder / 'x.md').write_text('x')
        for path in (folder / 'x.md', folder):
            os.utime(path, (stamp, stamp))
    walk = areas.walk_paths(['rag-data/a', 'rag-data/b'], ['**/*.md'], [])
    assert walk.newest_mtime == pytest.approx(new)
    assert areas.walk_paths([], ['**/*.md'], []).files == []


def test_overlapping_paths():
    assert overlaps('code/a', 'code/a') and overlaps('code/a', 'code/a/b') and overlaps('code', 'code/a')
    assert not overlaps('code/a', 'code/ab') and not overlaps('code/a', 'rag-data/a') and not overlaps('code/a/b', 'code/a/c')


def test_single_walk_keeps_relative_paths_and_names_its_source(areas):
    areas, roots = areas
    (roots['rag-data'] / 'notes').mkdir()
    (roots['rag-data'] / 'notes' / 'a.md').write_text('# A')
    assert areas.walk('rag-data/notes', ['**/*.md'], []).files[0] | {'size': 0, 'mtime': 0} == {
        'path': 'a.md', 'source': 'rag-data/notes', 'size': 0, 'mtime': 0}
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest tests/test_collections.py -q`
Expected: FAIL with `ImportError: cannot import name 'overlaps'`.

- [ ] **Step 3: Implement the changes in `services/rag/src/rag/collections.py`**

Replace `FolderMissing` and add `overlaps` below `source_path()`:

```python
class FolderMissing(HTTPException):
    def __init__(self, source: str):
        self.source = source
        super().__init__(409, f'Path {source} is missing. Nothing was removed from the index; '
                              'restore the folder or remove the path from the collection.')


def overlaps(a: str, b: str) -> bool:
    """Whether one collection path contains the other; equal paths overlap."""
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')
```

Extend `Walk`:

```python
@dataclass
class Walk:
    files: list[dict] = field(default_factory=list)
    newest_mtime: float | None = None
    skipped: dict[str, int] = field(default_factory=lambda: {'links': 0, 'special': 0, 'unreadable': 0, 'invalid_names': 0})
    truncated: bool = False
    missing: list[str] = field(default_factory=list)  # paths absent during a truncating walk
    visited: int = 0  # entries visited, counted across every path of a collection
```

Replace `Areas.walk` and add `Areas.walk_paths` directly after it. The body of
`visit` is unchanged except where noted.

```python
    def walk(self, source: str, include: list[str], exclude: list[str], *, limit: int = MAX_FILES,
             truncate: bool = False, max_entries: int | None = None, into: Walk | None = None) -> Walk:
        """List indexable files of one folder; patterns match paths relative to it.

        `limit` caps matching files; `max_entries` caps every entry visited. With `truncate` either cap
        stops the walk and marks it truncated; otherwise it fails. With `into`, files are added to that
        walk under their full DATA_ROOT path and the caps count everything already in it.
        """
        result = Walk() if into is None else into
        if max_entries is None:
            max_entries = MAX_STATUS_ENTRIES if truncate else MAX_SYNC_ENTRIES

        def newer(value: float):
            result.newest_mtime = value if result.newest_mtime is None else max(result.newest_mtime, value)

        def visit(fd: int, prefix: str) -> bool:
            newer(os.fstat(fd).st_mtime)
            try:
                names = sorted(os.listdir(fd))
            except PermissionError:
                result.skipped['unreadable'] += 1
                return True
            for name in names:
                result.visited += 1
                if result.visited > max_entries:
                    if truncate:
                        result.truncated = True
                        return False
                    raise HTTPException(400, f'This collection has more than {max_entries} entries in its folders; '
                                             'narrow its paths or include patterns')
                # ... unchanged: name encoding, os.stat, links, directories (recursion), specials ...
                elif stat.S_ISREG(entry.st_mode):
                    if not indexable(relative, include, exclude):
                        continue
                    if len(result.files) >= limit:
                        if truncate:
                            result.truncated = True
                            return False
                        raise HTTPException(400, f'This collection selects more than {limit} files; narrow its include patterns')
                    result.files.append({'path': relative if into is None else f'{source}/{relative}', 'source': source,
                                         'size': entry.st_size, 'mtime': entry.st_mtime})
                    newer(entry.st_mtime)
                elif indexable(relative, include, exclude):
                    result.skipped['special'] += 1
            return True

        with ExitStack() as stack:
            try:
                fd = stack.enter_context(self._directory(source, []))
            except (FileNotFoundError, NotADirectoryError):
                raise FolderMissing(source) from None
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise HTTPException(409, f'The collection folder {source} is a link; point the collection at a real folder') from None
                raise
            visit(fd, '')
        return result

    def walk_paths(self, sources: list[str], include: list[str], exclude: list[str], *, limit: int = MAX_FILES,
                   truncate: bool = False, max_entries: int | None = None) -> Walk:
        """Walk every path of a collection into one result with full DATA_ROOT-relative file paths.

        The caps apply to the collection as a whole. With `truncate` (status and listings) a missing
        path is recorded in `missing` and the other paths are still walked; otherwise the first missing
        path raises FolderMissing, so a sync never runs while a folder is absent.
        """
        result = Walk()
        for source in sources:
            if result.truncated:
                break
            try:
                self.walk(source, include, exclude, limit=limit, truncate=truncate, max_entries=max_entries, into=result)
            except FolderMissing:
                if not truncate:
                    raise
                result.missing.append(source)
        return result
```

Delete the local `visited = 0` and `nonlocal visited` lines from the old `walk`: the
counter now lives on `result`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd services/rag && uv run --frozen pytest tests/test_collections.py -q`
Expected: all pass. The existing `test_walk_caps_visited_entries` still matches
`more than 5 entries`, and `test_walk_limit_and_missing_folder` still raises
`FolderMissing`.

- [ ] **Step 5: Commit**

```bash
git add services/rag/src/rag/collections.py services/rag/tests/test_collections.py
git commit -m "feat(rag): walk every path of a collection with shared limits

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: RAG paths API, settings-only edit, guarded removal and full-path file API

**Files:**
- Modify: `services/rag/src/rag/workspace.py`: add `Workspace.paths()` and
  `Workspace.ensure_collection()`.
- Modify: `services/rag/src/rag/workspace_api.py`: full replacement below.
- Modify: `services/rag/src/rag/collections.py`: delete `Areas.delete` (there is no
  file delete API any more).
- Modify: `services/rag/tests/test_workspace.py`:
  - new helpers `collection`, `update`, `write`, `chunks`;
  - `test_single_workspace_and_cross_project_isolation` uses `by-name`;
  - delete `test_file_paths_stay_inside_the_collection` and
    `test_symlinks_and_stale_file_writes_rejected`, which move to
    `test_paths_api.py`.
- Modify: `services/rag/tests/test_collections_api.py`: delete the tests replaced
  by `test_paths_api.py`: `test_create_list_and_validate`,
  `test_read_only_area_rejects_writes_and_reads_files`,
  `test_rag_data_collection_folder_is_created`,
  `test_file_api_serves_only_indexable_files` and
  `test_file_writes_refuse_hidden_and_skipped_paths`.
- Modify: `services/rag/tests/test_collections.py`:
  - in `test_read_write_delete_confined_to_rag_data`, delete the two `areas.delete`
    lines and rename it `test_read_write_confined_to_rag_data`;
  - in `test_write_and_delete_refuse_hidden_and_skipped_paths`, delete the
    `areas.delete` block and rename it `test_write_refuses_hidden_and_skipped_paths`.
- Test: `services/rag/tests/test_paths_api.py` (new).

**Interfaces:**
- Consumes:
  - from Task 2: `Areas.walk_paths`, `Walk.missing`, `FolderMissing`, `overlaps`;
  - from Task 1: the `rag_collection_paths` schema.
- Produces, in the backoffice API under `/backoffice/projects/{project}` (all routes
  need `x-rag-key`):
  - `PUT collections/by-name/{name}` with `{description?}` → `CollectionSummary`;
    creates the collection if missing and never changes an existing one;
  - `PUT collections/by-name/{name}/paths` with `{source_path, owner_kind?,
    owner_id?, owner_key?}` → `CollectionSummary` of `{name}`:
    - creates the collection if missing;
    - idempotent;
    - the same owner is re-pointed, here or from another collection;
    - 409 `Path overlaps <other> in collection <name>`;
    - creates the folder when it is under `rag-data`;
  - `DELETE collections/by-name/{name}/paths?source_path=` or
    `?owner_kind=&owner_id=&owner_key=` → `{"removed": n}`:
    - idempotent;
    - 409 when `source_path` names an owned path;
    - 400 unless exactly one of `source_path` and `owner_kind` is given;
  - `DELETE paths?owner_kind=&owner_id=&owner_key=` → `{"removed": n}`, across
    every collection. When `owner_key` is omitted it matches every key of that
    owner;
  - `PUT collections/{id}` with `CollectionSettings` (description, search_mode,
    embeddings_enabled, include, exclude, chunk_size, chunk_overlap, revision) →
    `CollectionStatus`. Any other field gives 400;
  - `DELETE collections/{id}?revision=&confirmed=true`: 409 `Remove this
    collection's sources first: Jira, Code project (api)` when any path is owned;
    otherwise as today, and its paths are deleted too;
  - `GET collections[?status=false]` and `GET collections/{id}`: rows as below;
  - `GET collections/{id}/files` → `{files: [{path (full), source, size, mtime,
    indexed_at, content_hash, chunks, embedded_chunks, stale}], truncated,
    skipped, missing_paths}`;
  - `GET collections/{id}/chunks?path=<full path>`;
  - `GET file?collection=&path=<full path>`: 404 unless the path lies under one of
    the collection's paths and is indexable, with patterns matched relative to that
    path;
  - `PUT file` with `{path, content, expected_hash}`, where `path` is a full path
    under `rag-data/<folder>/…` → `{path, hash}`;
  - removed: `POST collections` (405) and `DELETE file` (405).
- `CollectionSummary` (`status=false`):
  - every `rag_collections` column;
  - `indexed_files`, `chunks`, `embedded_chunks`;
  - `paths: [CollectionPath]`;
  - `folder_present: bool` (every path present);
  - `missing_paths: [str]`.
- `CollectionPath`: `{id, collection_id, source_path, owner_kind, owner_id,
  owner_key, created_at, files (indexed files under it), host_path,
  container_path, present}`.
- `CollectionStatus` = `CollectionSummary` plus `job`, `folder_changed_at`
  (newest across paths), `file_count`, `truncated`, `skipped`, `needs_refresh` and
  `status`. The status is `folder_missing` when any path is missing.
- The worker still uses `collection["source_path"]` until Task 4, so this task's
  tests avoid running syncs.

- [ ] **Step 1: Replace the shared helpers in `tests/test_workspace.py`**

Replace the helpers `collection`, `write` and `chunks`, and add `update`:

```python
SETTINGS = ("description", "search_mode", "embeddings_enabled", "include", "exclude", "chunk_size", "chunk_overlap")


def update(env, collection_id, **changes):
    """PUT the collection's current search settings with `changes` applied."""
    client, prefix = env[1], env[3]
    row = client.get(prefix + f"/collections/{collection_id}").json()
    return client.put(prefix + f"/collections/{collection_id}",
                      json={**{key: row[key] for key in SETTINGS}, **changes, "revision": row["revision"]})


def collection(env, name="docs", *sources, **settings):
    """Ensure collection `name` holding `sources` as unowned paths (default rag-data/<name>), then apply settings."""
    client, prefix = env[1], env[3]
    for source in sources or (f"rag-data/{name}",):
        added = client.put(prefix + f"/collections/by-name/{name}/paths", json={"source_path": source})
        assert added.status_code == 200, added.text
    collection_id = added.json()["id"]
    if settings:
        saved = update(env, collection_id, **settings)
        assert saved.status_code == 200, saved.text
    return collection_id


def write(env, path="readme.md", content="# Payments\n\nRetries use exponential backoff.", name="docs"):
    """Create or replace rag-data/<name>/<path> through the rag-data writer."""
    client, prefix = env[1], env[3]
    full = f"rag-data/{name}/{path}"
    previous = client.get(prefix + "/file", params={"collection": name, "path": full})
    result = client.put(prefix + "/file", json={"path": full, "content": content,
                                                "expected_hash": previous.json()["hash"] if previous.status_code == 200 else None})
    assert result.status_code == 200, result.text


def chunks(env, collection_id, path="rag-data/docs/readme.md"):
    client, prefix = env[1], env[3]
    return [row["text"] for row in client.get(prefix + f"/collections/{collection_id}/chunks", params={"path": path}).json()["chunks"]]
```

Existing callers keep working, because `collection(env, "a", "rag-data/a")` and
`collection(env, embeddings_enabled=True)` have the same meaning. In
`test_single_workspace_and_cross_project_isolation`, replace the `POST …/collections`
line with:

```python
    assert client.put(f"/backoffice/projects/{other}/collections/by-name/x", json={}).status_code == 404
```

Delete `test_file_paths_stay_inside_the_collection` and
`test_symlinks_and_stale_file_writes_rejected`. Their cases are covered below.

- [ ] **Step 2: Write the failing API tests in `tests/test_paths_api.py`**

```python
"""Collection paths: ensure by name, add/re-point/remove paths, guarded removal and the full-path file API."""
import os
import time
from datetime import datetime
from uuid import uuid4

import pytest
from test_workspace import collection, update

# env and workspace_postgres come from conftest.py


def ensure(env, name, **body):
    return env[1].put(env[3] + f"/collections/by-name/{name}", json=body)


def add(env, name, source, **owner):
    return env[1].put(env[3] + f"/collections/by-name/{name}/paths", json={"source_path": source, **owner})


def listing(env):
    return {row["name"]: row for row in env[1].get(env[3] + "/collections").json()["collections"]}


def status(env, collection_id):
    return env[1].get(env[3] + f"/collections/{collection_id}").json()


def sources(row):
    return [(p["source_path"], p["owner_kind"], p["owner_key"]) for p in row["paths"]]


def test_ensure_creates_once_and_never_changes_settings(env):
    created = ensure(env, "payments", description="Payments knowledge")
    assert created.status_code == 200, created.text
    row = created.json()
    assert row["name"] == "payments" and row["description"] == "Payments knowledge" and row["paths"] == []
    assert row["include"] == ["**/*.md", "**/*.markdown", "**/*.txt"] and row["search_mode"] == "full_text"
    assert update(env, row["id"], chunk_size=1000).status_code == 200
    again = ensure(env, "payments", description="Other text").json()
    assert again["id"] == row["id"] and again["description"] == "Payments knowledge" and again["chunk_size"] == 1000
    for bad in ("Payments", "a--b", "x" * 61):
        assert ensure(env, bad).status_code == 400, bad


def test_add_path_is_idempotent_and_bumps_revision_once(env):
    roots = env[6]
    (roots["code"] / "payments").mkdir()
    first = add(env, "payments", "code/payments")
    assert first.status_code == 200, first.text
    row = first.json()
    assert sources(row) == [("code/payments", None, None)]
    assert row["paths"][0]["host_path"] == "/srv/code/payments" and row["paths"][0]["present"] is True
    assert row["paths"][0]["files"] == 0
    again = add(env, "payments", "code/payments").json()
    assert again["revision"] == row["revision"] and len(again["paths"]) == 1
    added = add(env, "payments", "rag-data/jira", owner_kind="jira").json()
    assert added["revision"] == row["revision"] + 1
    assert sources(added) == [("code/payments", None, None), ("rag-data/jira", "jira", None)]
    assert (roots["rag-data"] / "jira").is_dir()  # rag-data paths are created for the source that writes there
    assert add(env, "payments", "docs/absent").status_code == 200  # missing folders are allowed and reported
    assert not (roots["docs"] / "absent").exists()
    for bad in ({"source_path": "../etc"}, {"source_path": "tmp/x"}, {"source_path": "code/x", "owner_key": "x"},
                {"source_path": "code/x", "owner_kind": "wiki"}):
        assert env[1].put(env[3] + "/collections/by-name/payments/paths", json=bad).status_code == 400, bad


def test_overlapping_path_is_refused_and_nothing_changes(env):
    roots = env[6]
    (roots["code"] / "payments" / "src").mkdir(parents=True)
    row = add(env, "payments", "code/payments").json()
    for source in ("code/payments/src", "code"):
        refused = add(env, "payments", source)
        assert refused.status_code == 409, source
        assert refused.json()["error"]["message"] == "Path overlaps code/payments in collection payments"
    owned = add(env, "payments", "code", owner_kind="jira")
    assert owned.status_code == 409 and "overlaps code/payments" in owned.json()["error"]["message"]
    after = listing(env)["payments"]
    assert sources(after) == [("code/payments", None, None)] and after["revision"] == row["revision"]
    assert add(env, "other", "code/payments/src").status_code == 200  # another collection may share it


def test_owner_path_moves_between_collections(env):
    owner = {"owner_kind": "code_project", "owner_id": str(uuid4()), "owner_key": "a"}
    first = add(env, "one", "code/a", **owner).json()
    repointed = add(env, "one", "code/b", **owner).json()
    assert sources(repointed) == [("code/b", "code_project", "a")] and repointed["revision"] == first["revision"] + 1
    moved = add(env, "two", "code/b", **owner).json()
    assert sources(moved) == [("code/b", "code_project", "a")]
    rows = listing(env)
    assert rows["one"]["paths"] == [] and rows["one"]["revision"] == repointed["revision"] + 1
    adopted = add(env, "three", "docs/runbooks").json()
    claimed = add(env, "three", "docs/runbooks", owner_kind="jira").json()
    assert sources(claimed) == [("docs/runbooks", "jira", None)] and claimed["revision"] == adopted["revision"]


def test_remove_by_source_path_or_owner_is_idempotent(env):
    client, prefix = env[1], env[3]
    add(env, "ops", "docs/runbooks")
    add(env, "ops", "rag-data/jira", owner_kind="jira")
    remove = lambda **params: client.delete(prefix + "/collections/by-name/ops/paths", params=params)
    owned = remove(source_path="rag-data/jira")
    assert owned.status_code == 409
    assert owned.json()["error"]["message"] == "Path rag-data/jira belongs to Jira; switch it off there"
    before = listing(env)["ops"]["revision"]
    assert remove(source_path="docs/runbooks").json() == {"removed": 1}
    assert listing(env)["ops"]["revision"] == before + 1
    assert remove(source_path="docs/runbooks").json() == {"removed": 0}
    assert remove(owner_kind="jira").json() == {"removed": 1}
    assert client.delete(prefix + "/collections/by-name/nope/paths", params={"owner_kind": "jira"}).json() == {"removed": 0}
    assert remove().status_code == 400
    assert remove(source_path="docs/x", owner_kind="jira").status_code == 400
    assert listing(env)["ops"]["paths"] == []


def test_owner_removal_clears_every_collection(env):
    client, prefix = env[1], env[3]
    project_id = str(uuid4())
    add(env, "one", "code/a", owner_kind="code_project", owner_id=project_id, owner_key="a")
    add(env, "two", "code/b", owner_kind="code_project", owner_id=project_id, owner_key="b")
    add(env, "two", "docs/keep")
    one_key = client.delete(prefix + "/paths", params={"owner_kind": "code_project", "owner_id": project_id, "owner_key": "a"})
    assert one_key.json() == {"removed": 1}
    assert client.delete(prefix + "/paths", params={"owner_kind": "code_project", "owner_id": project_id}).json() == {"removed": 1}
    rows = listing(env)
    assert rows["one"]["paths"] == [] and sources(rows["two"]) == [("docs/keep", None, None)]
    assert client.delete(prefix + "/paths", params={"owner_kind": "code_project", "owner_id": project_id}).json() == {"removed": 0}
    assert client.delete(prefix + "/paths").status_code == 400


def test_settings_only_edit_and_remove_guarded_by_owners(env):
    client, prefix = env[1], env[3]
    row = add(env, "jira", "rag-data/jira", owner_kind="jira").json()
    for extra in ({"name": "other"}, {"source_path": "rag-data/x"}):
        assert client.put(prefix + f"/collections/{row['id']}", json={**extra, "revision": row["revision"]}).status_code == 400
    saved = update(env, row["id"], description="Tickets", include=["**/*.md"])
    assert saved.status_code == 200 and saved.json()["name"] == "jira" and saved.json()["include"] == ["**/*.md"]
    assert saved.json()["status"] == "needs_refresh"
    refused = client.delete(prefix + f"/collections/{row['id']}", params={"revision": saved.json()["revision"], "confirmed": True})
    assert refused.status_code == 409
    assert refused.json()["error"]["message"] == "Remove this collection's sources first: Jira"
    client.delete(prefix + "/collections/by-name/jira/paths", params={"owner_kind": "jira"})
    revision = listing(env)["jira"]["revision"]
    assert client.delete(prefix + f"/collections/{row['id']}", params={"revision": revision, "confirmed": True}).status_code == 200
    assert "jira" not in listing(env)


def test_removed_endpoints(env):
    client, prefix = env[1], env[3]
    assert client.post(prefix + "/collections", json={"name": "x", "source_path": "rag-data/x"}).status_code == 405
    assert client.delete(prefix + "/file", params={"collection": "x", "path": "rag-data/x/a.md", "expected_hash": "h"}).status_code == 405


def test_status_reports_each_path_and_missing_paths(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    (roots["rag-data"] / "docs" / "a.md").write_text("# A")
    created = collection(env, "docs", "rag-data/docs", "docs/absent")
    row = status(env, created)
    assert row["status"] == "folder_missing" and row["folder_present"] is False and row["missing_paths"] == ["docs/absent"]
    assert {p["source_path"]: p["present"] for p in row["paths"]} == {"docs/absent": False, "rag-data/docs": True}
    assert row["file_count"] == 1
    summary = env[1].get(env[3] + "/collections", params={"status": "false"}).json()["collections"][0]
    assert summary["missing_paths"] == ["docs/absent"] and "status" not in summary
    queued = env[1].post(env[3] + f"/collections/{created}/sync", json={})
    assert queued.status_code == 409 and "Path docs/absent is missing" in queued.json()["error"]["message"]


def test_folder_changed_is_newest_across_paths_and_path_changes_need_refresh(env):
    roots = env[6]
    old, new = time.time() - 7200, time.time() - 60
    for folder, stamp in ((roots["rag-data"] / "a", old), (roots["docs"] / "b", new)):
        folder.mkdir()
        (folder / "x.md").write_text("# X")
        for path in (folder / "x.md", folder):
            os.utime(path, (stamp, stamp))
    created = collection(env, "mixed", "rag-data/a", "docs/b")
    row = status(env, created)
    assert datetime.fromisoformat(row["folder_changed_at"]).timestamp() == pytest.approx(new, abs=1)
    assert row["file_count"] == 2
    with env[0].connect() as conn:  # as if a sync had just finished
        conn.execute("UPDATE ka.rag_collections SET last_synced_at=now(),synced_revision=revision WHERE id=%s", (created,))
    assert status(env, created)["status"] == "up_to_date"
    (roots["docs"] / "c").mkdir()
    add(env, "mixed", "docs/c")
    assert status(env, created)["status"] == "needs_refresh"


def test_file_api_serves_only_paths_under_the_collection(env):
    roots = env[6]
    repo = roots["code"] / "payments"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "retry.py").write_text("RETRIES = 5\n")
    (repo / ".env").write_text("SECRET=1")
    (roots["code"] / "other").mkdir()
    (roots["code"] / "other" / "x.py").write_text("print(2)\n")
    collection(env, "payments", "code/payments", include=["**/*.py", "**/.env"])
    client, prefix = env[1], env[3]
    get = lambda path: client.get(prefix + "/file", params={"collection": "payments", "path": path})
    found = get("code/payments/src/retry.py").json()
    assert found == {"collection": "payments", "path": "code/payments/src/retry.py", "content": "RETRIES = 5\n", "hash": found["hash"]}
    for path in ("src/retry.py", "code/other/x.py", "code/payments/.env", "code/paymentsX/src/retry.py", "code/payments"):
        response = get(path)
        assert response.status_code == 404, path
        assert "SECRET" not in response.text
    for path in ("../code/payments/src/retry.py", "/code/payments/src/retry.py", "code//payments/src/retry.py", ""):
        assert get(path).status_code == 400, path
    assert client.get(prefix + "/file", params={"collection": "nope", "path": "code/payments/src/retry.py"}).status_code == 404


def test_symlinked_files_are_never_served(env, tmp_path):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("outside-secret")
    (roots["rag-data"] / "docs" / "link.txt").symlink_to(secret)
    collection(env)
    response = env[1].get(env[3] + "/file", params={"collection": "docs", "path": "rag-data/docs/link.txt"})
    assert response.status_code == 400 and "outside-secret" not in response.text


def test_rag_data_writer_uses_full_paths(env):
    roots = env[6]
    client, prefix = env[1], env[3]
    put = lambda path, content="# A", expected=None: client.put(prefix + "/file", json={"path": path, "content": content, "expected_hash": expected})
    saved = put("rag-data/jira/DEMO-1.md", "# DEMO-1")
    assert saved.status_code == 200 and saved.json()["path"] == "rag-data/jira/DEMO-1.md"
    assert (roots["rag-data"] / "jira" / "DEMO-1.md").read_text() == "# DEMO-1"
    assert put("rag-data/jira/DEMO-1.md", "# stale").status_code == 409
    assert put("rag-data/jira/DEMO-1.md", "# DEMO-1 v2", saved.json()["hash"]).status_code == 200
    for path in ("code/payments/a.md", "rag-data", "rag-data/jira/.env", "rag-data/.git/config", "rag-data/node_modules/a.md", "../x.md"):
        response = put(path)
        assert response.status_code == 400, path
    assert not (roots["code"] / "payments").exists()
    legacy = client.put(prefix + "/file", json={"collection": "jira", "path": "rag-data/jira/a.md", "content": "x"})
    assert legacy.status_code == 400
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest tests/test_paths_api.py -q`
Expected: FAIL. The `PUT …/collections/by-name/{name}/paths` route does not exist
(405), and the API still selects `source_path`.

- [ ] **Step 4: Add the workspace helpers in `services/rag/src/rag/workspace.py`**

Append these methods to `Workspace`:

```python
    def paths(self, conn, collection_id: UUID) -> list[dict]:
        return conn.execute("SELECT * FROM ka.rag_collection_paths WHERE collection_id=%s ORDER BY source_path",
                            (collection_id,)).fetchall()

    def ensure_collection(self, conn, project: UUID, name: str, description: str = "") -> dict:
        """The named collection, created with default settings when missing; never changes an existing one.

        The row is locked, so path changes to one collection are serialized with its edits and syncs.
        """
        conn.execute("""INSERT INTO ka.rag_collections(project_id,name,description) VALUES (%s,%s,%s)
            ON CONFLICT (project_id,name) WHERE deleted_at IS NULL DO NOTHING""", (project, name, description))
        return conn.execute("""SELECT * FROM ka.rag_collections WHERE project_id=%s AND name=%s AND deleted_at IS NULL
            FOR UPDATE""", (project, name)).fetchone()
```

In `services/rag/src/rag/collections.py`, delete `Areas.delete`.

- [ ] **Step 5: Replace `services/rag/src/rag/workspace_api.py`**

The functions marked "unchanged" keep today's code exactly: `get_workspace`,
`document_catalog`, `config_response`, `get_settings`, `save_settings`,
`test_embedding`, `list_collections`, `get_collection`, `sync_collection`,
`sync_stale`, `collection_chunks`, `jobs`, `cancel` and `search_route`.

```python
"""Authenticated backoffice API; area roots come from the deployment, never from input.

Sources own their folders: they add paths to the collection they choose and remove them again.
Administrators change search settings, refresh, detach unowned paths and remove unowned collections.
"""

import hmac
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.collections import AREAS, WRITABLE_AREA, FolderMissing, indexable, overlaps, refuse_hidden, relative_path, source_path
from rag.workspace import Settings, Workspace, digest
from rag.workspace_models import embed
from rag.workspace_search import SearchInput, search


JobStatus = Literal["queued", "running", "waiting_input", "succeeded", "failed", "cancelled"]
OwnerKind = Literal["documents", "jira", "code_project"]
CollectionName = Annotated[str, Path(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=60)]
OWNER_LABELS = {"documents": "Documents", "jira": "Jira", "code_project": "Code project"}


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    settings: Settings
    embedding_model_id: UUID | None = None


GlobPattern = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^/\\\x00][^\\\x00]*$")]


class CollectionSettings(BaseModel):
    """Search settings. A collection's name never changes, and its paths come from sources."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field("", max_length=2000)
    search_mode: Literal["full_text", "embedding", "hybrid"] = "full_text"
    embeddings_enabled: bool = False
    include: list[GlobPattern] = Field(default_factory=lambda: ["**/*.md", "**/*.markdown", "**/*.txt"], min_length=1, max_length=50)
    exclude: list[GlobPattern] = Field(default_factory=list, max_length=50)
    chunk_size: int = Field(2000, ge=200, le=12000)
    chunk_overlap: int = Field(200, ge=0, le=2000)
    revision: int

    @model_validator(mode="after")
    def valid(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.search_mode == "embedding" and not self.embeddings_enabled:
            raise ValueError("Turn on embeddings for semantic search, or choose keyword or hybrid search")
        return self


class EnsureInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str | None = Field(None, max_length=2000)


class PathInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_path: str
    owner_kind: OwnerKind | None = None
    owner_id: UUID | None = None
    owner_key: str | None = Field(None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def valid(self):
        source_path(self.source_path)
        if self.owner_kind is None and (self.owner_id is not None or self.owner_key is not None):
            raise ValueError("owner_id and owner_key need an owner_kind")
        return self


class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False


class FileInput(BaseModel):
    """A file under rag-data, addressed by its full DATA_ROOT path (the Jira export writes here)."""
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str = Field(max_length=10_000_000)
    expected_hash: str | None = None


STATUS_SQL = """SELECT count(DISTINCT f.id) AS indexed_files, count(c.id) AS chunks,
    count(v.input_hash) AS embedded_chunks
    FROM ka.rag_files f LEFT JOIN ka.rag_chunks c ON c.file_id=f.id
    LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile
        AND v.input_hash=c.input_hash AND f.embedding_profile<>''
    WHERE f.collection_id=%s"""
ORPHAN_VECTORS_SQL = """DELETE FROM ka.rag_vectors v WHERE v.project_id=%s AND NOT EXISTS (
    SELECT 1 FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id
    WHERE c.project_id=v.project_id AND c.input_hash=v.input_hash AND f.embedding_profile=v.profile)"""
SETTINGS_COLUMNS = ("description", "search_mode", "embeddings_enabled", "include", "exclude", "chunk_size", "chunk_overlap")
ACTIVE = ("queued", "running", "waiting_input")


def owner_label(row: dict) -> str:
    label = OWNER_LABELS[row["owner_kind"]]
    return f"{label} ({row['owner_key']})" if row["owner_key"] else label


def owner_match(alias: str, kind: str, owner_id: UUID | None, key: str | None, *, every_key: bool = False) -> tuple[str, list]:
    """SQL matching one owner's path; `every_key` matches all keys of that owner (a whole code project)."""
    sql = f"{alias}.owner_kind=%s AND {alias}.owner_id IS NOT DISTINCT FROM %s"
    params: list = [kind, owner_id]
    if not every_key:
        sql += f" AND {alias}.owner_key IS NOT DISTINCT FROM %s"
        params.append(key)
    return sql, params


def bump(conn, collection_ids: set) -> None:
    """A path change makes the collection show Needs refresh and fails a sync that is running."""
    if collection_ids:
        conn.execute("UPDATE ka.rag_collections SET revision=revision+1,updated_at=now() WHERE id=ANY(%s)", (list(collection_ids),))


def path_rows(workspace: Workspace, conn, collection_id: UUID) -> list[dict]:
    """The collection's paths with indexed file counts, host paths and presence; no walk."""
    rows = conn.execute("""SELECT p.*, count(f.id) AS files FROM ka.rag_collection_paths p
        LEFT JOIN ka.rag_files f ON f.path_id=p.id WHERE p.collection_id=%s
        GROUP BY p.id ORDER BY p.source_path""", (collection_id,)).fetchall()
    return [{**row, "host_path": workspace.areas.host_path(row["source_path"]),
             "container_path": str(workspace.areas.container_path(row["source_path"])),
             "present": workspace.areas.present(row["source_path"])} for row in rows]


def collection_summary(workspace: Workspace, conn, row: dict) -> dict:
    """Collection row, index counts and paths with presence; no folder walk."""
    counts = conn.execute(STATUS_SQL, (row["id"],)).fetchone()
    paths = path_rows(workspace, conn, row["id"])
    missing = [p["source_path"] for p in paths if not p["present"]]
    return {**row, **counts, "paths": paths, "folder_present": not missing, "missing_paths": missing}


def collection_status(workspace: Workspace, conn, row: dict) -> dict:
    counts = conn.execute(STATUS_SQL, (row["id"],)).fetchone()
    job = conn.execute("""SELECT id,status,error,created_at,finished_at FROM ka.jobs
        WHERE rag_collection_id=%s ORDER BY created_at DESC LIMIT 1""", (row["id"],)).fetchone()
    paths = path_rows(workspace, conn, row["id"])
    walk = workspace.areas.walk_paths([p["source_path"] for p in paths], row["include"], row["exclude"], truncate=True)
    for path in paths:
        path["present"] = path["source_path"] not in walk.missing
    present = not walk.missing
    changed = datetime.fromtimestamp(walk.newest_mtime, timezone.utc) if walk.newest_mtime is not None else None
    synced = row["last_synced_at"]
    needs = present and (synced is None or row["synced_revision"] != row["revision"] or (changed is not None and changed > synced))
    if not present:
        state = "folder_missing"
    elif job is not None and job["status"] in ACTIVE:
        state = "syncing"
    elif job is not None and job["status"] == "failed" and (synced is None or job["created_at"] > synced):
        state = "failed"
    else:
        state = "needs_refresh" if needs else "up_to_date"
    return {**row, **counts, "job": job, "paths": paths, "folder_present": present, "missing_paths": walk.missing,
            "folder_changed_at": changed, "file_count": len(walk.files), "truncated": walk.truncated,
            "skipped": walk.skipped, "needs_refresh": bool(needs), "status": state}


def build_api(workspace: Workspace, key: str, transport=None):
    def authenticate(request: Request):
        if not key or not hmac.compare_digest(request.headers.get("x-rag-key", ""), key):
            raise HTTPException(403, "RAG service authentication required")
    router = APIRouter(dependencies=[Depends(authenticate)])
    prefix = "/backoffice/projects/{project}"

    def drop_orphan_vectors(conn, project: UUID) -> None:
        # Lock order collection -> workspace matches the worker's publish, which
        # re-checks reused vectors under the workspace lock.
        workspace.settings(conn, project, lock=True)
        conn.execute(ORPHAN_VECTORS_SQL, (project,))

    # get_workspace, document_catalog, config_response, get_settings, save_settings,
    # test_embedding and list_collections: unchanged.

    @router.put(prefix + "/collections/by-name/{name}")
    def ensure_collection(project: UUID, name: CollectionName, payload: EnsureInput):
        """Create the collection with default settings when missing; never changes an existing one."""
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            return collection_summary(workspace, conn, workspace.ensure_collection(conn, project, name, payload.description or ""))

    @router.put(prefix + "/collections/by-name/{name}/paths")
    def add_path(project: UUID, name: CollectionName, payload: PathInput):
        """Add a folder to the collection, creating the collection if missing. Idempotent.

        An owned path is re-pointed: the owner's previous path, here or in another collection, is
        replaced, so a source owns one path. An identical unowned path is adopted with its files.
        """
        owner = (payload.owner_kind, payload.owner_id, payload.owner_key)
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            collection = workspace.ensure_collection(conn, project, name)
            paths = workspace.paths(conn, collection["id"])
            same = next((p for p in paths if p["source_path"] == payload.source_path), None)
            if same is not None and (same["owner_kind"], same["owner_id"], same["owner_key"]) == owner:
                return collection_summary(workspace, conn, collection)
            if same is not None and (same["owner_kind"] is not None or payload.owner_kind is None):
                raise HTTPException(409, f"Path overlaps {same['source_path']} in collection {name}")
            owned = payload.owner_kind is not None
            for path in paths:
                if path is same or (owned and (path["owner_kind"], path["owner_id"], path["owner_key"]) == owner):
                    continue  # adopted below, or re-pointed below
                if overlaps(path["source_path"], payload.source_path):
                    raise HTTPException(409, f"Path overlaps {path['source_path']} in collection {name}")
            changed, removed = set(), False
            if owned:
                where, params = owner_match("p", *owner)
                rows = conn.execute(f"""DELETE FROM ka.rag_collection_paths p USING ka.rag_collections c
                    WHERE c.id=p.collection_id AND c.project_id=%s AND {where} RETURNING p.collection_id""",
                                    [project, *params]).fetchall()
                changed |= {row["collection_id"] for row in rows}
                removed = bool(rows)
            if same is not None:
                conn.execute("UPDATE ka.rag_collection_paths SET owner_kind=%s,owner_id=%s,owner_key=%s WHERE id=%s",
                             (*owner, same["id"]))
            else:
                conn.execute("""INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind,owner_id,owner_key)
                    VALUES (%s,%s,%s,%s,%s)""", (collection["id"], payload.source_path, *owner))
                changed.add(collection["id"])
            bump(conn, changed)
            if removed:
                drop_orphan_vectors(conn, project)
            workspace.areas.ensure_folder(payload.source_path)  # rag-data only; a failure rolls everything back
            return collection_summary(workspace, conn, workspace.collection(conn, project, collection["id"]))

    @router.delete(prefix + "/collections/by-name/{name}/paths")
    def remove_path(project: UUID, name: CollectionName, source_path: str | None = Query(None, max_length=500),
                    owner_kind: OwnerKind | None = None, owner_id: UUID | None = None,
                    owner_key: str | None = Query(None, max_length=500)):
        """Remove one path and its indexed files; files on disk stay. Idempotent."""
        if (source_path is None) == (owner_kind is None):
            raise HTTPException(400, "Give either source_path or owner_kind")
        with workspace.connect() as conn:
            row = conn.execute("SELECT * FROM ka.rag_collections WHERE project_id=%s AND name=%s AND deleted_at IS NULL FOR UPDATE",
                               (project, name)).fetchone()
            if row is None:
                return {"removed": 0}
            if source_path is not None:
                target = conn.execute("SELECT * FROM ka.rag_collection_paths WHERE collection_id=%s AND source_path=%s",
                                      (row["id"], source_path)).fetchone()
                if target is None:
                    return {"removed": 0}
                if target["owner_kind"] is not None:
                    raise HTTPException(409, f"Path {source_path} belongs to {owner_label(target)}; switch it off there")
                where, params = "p.id=%s", [target["id"]]
            else:
                where, params = owner_match("p", owner_kind, owner_id, owner_key, every_key=owner_key is None)
            removed = conn.execute(f"DELETE FROM ka.rag_collection_paths p WHERE p.collection_id=%s AND {where}",
                                   [row["id"], *params]).rowcount
            if removed:
                bump(conn, {row["id"]})
                drop_orphan_vectors(conn, project)
            return {"removed": removed}

    @router.delete(prefix + "/paths")
    def remove_owner_paths(project: UUID, owner_kind: OwnerKind, owner_id: UUID | None = None,
                           owner_key: str | None = Query(None, max_length=500)):
        """Remove a source's paths from every collection, e.g. when a code project stops being searchable."""
        where, params = owner_match("p", owner_kind, owner_id, owner_key, every_key=owner_key is None)
        with workspace.connect() as conn:
            rows = conn.execute(f"""DELETE FROM ka.rag_collection_paths p USING ka.rag_collections c
                WHERE c.id=p.collection_id AND c.project_id=%s AND {where} RETURNING p.collection_id""",
                                [project, *params]).fetchall()
            if rows:
                bump(conn, {row["collection_id"] for row in rows})
                drop_orphan_vectors(conn, project)
        return {"removed": len(rows)}

    # get_collection: unchanged.

    @router.put(prefix + "/collections/{collection_id}")
    def update_collection(project: UUID, collection_id: UUID, payload: CollectionSettings):
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)
            if payload.revision != row["revision"]:
                raise HTTPException(409, "Collection changed; refresh before saving")
            row = conn.execute(f"""UPDATE ka.rag_collections SET {','.join(c + '=%s' for c in SETTINGS_COLUMNS)},
                revision=revision+1,updated_at=now() WHERE id=%s RETURNING *""",
                               (*(getattr(payload, c) for c in SETTINGS_COLUMNS), collection_id)).fetchone()
            return collection_status(workspace, conn, row)

    @router.delete(prefix + "/collections/{collection_id}")
    def delete_collection(project: UUID, collection_id: UUID, revision: int, confirmed: bool = False):
        if not confirmed:
            raise HTTPException(400, "Confirm removing this collection from search; its files stay on disk")
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)
            if revision != row["revision"]:
                raise HTTPException(409, "Collection changed; refresh before deleting")
            owned = conn.execute("""SELECT * FROM ka.rag_collection_paths WHERE collection_id=%s AND owner_kind IS NOT NULL
                ORDER BY owner_kind, owner_key""", (collection_id,)).fetchall()
            if owned:
                raise HTTPException(409, "Remove this collection's sources first: " + ", ".join(dict.fromkeys(owner_label(r) for r in owned)))
            workspace.settings(conn, project, lock=True)
            conn.execute("""UPDATE ka.jobs SET status='cancelled',finished_at=now(),error='Collection deleted',
                worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                WHERE rag_collection_id=%s AND status IN ('queued','running','waiting_input')""", (collection_id,))
            removed = conn.execute("DELETE FROM ka.rag_files WHERE collection_id=%s", (collection_id,)).rowcount
            conn.execute("DELETE FROM ka.rag_collection_paths WHERE collection_id=%s", (collection_id,))
            conn.execute("DELETE FROM ka.agent_collections WHERE collection_id=%s", (collection_id,))
            conn.execute(ORPHAN_VECTORS_SQL, (project,))
            conn.execute("UPDATE ka.rag_collections SET deleted_at=now(),updated_at=now() WHERE id=%s", (collection_id,))
        return {"deleted_files": removed}

    def queue_sync(conn, project: UUID, row: dict, actor: str | None, force: bool):
        sources = [p["source_path"] for p in workspace.paths(conn, row["id"])]
        missing = workspace.areas.walk_paths(sources, row["include"], row["exclude"], truncate=True).missing
        if missing:
            raise FolderMissing(missing[0])  # 409 before anything is queued
        try:
            with conn.transaction():
                return conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,requested_by,input)
                    VALUES (%s,'rag_collection_sync',%s,%s,%s) RETURNING id""",
                    (project, row["id"], UUID(actor) if actor else None, Jsonb({"force": force}))).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "A sync is already queued or running for this collection") from None

    # sync_collection and sync_stale: unchanged (they call queue_sync).

    @router.get(prefix + "/collections/{collection_id}/files")
    def collection_files(project: UUID, collection_id: UUID):
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id)
            sources = [p["source_path"] for p in workspace.paths(conn, collection_id)]
            indexed = {r["path"]: r for r in conn.execute("""SELECT f.path,f.indexed_at,f.content_hash,count(c.id) AS chunks,
                count(v.input_hash) AS embedded_chunks FROM ka.rag_files f LEFT JOIN ka.rag_chunks c ON c.file_id=f.id
                LEFT JOIN ka.rag_vectors v ON v.project_id=f.project_id AND v.profile=f.embedding_profile AND v.input_hash=c.input_hash AND f.embedding_profile<>''
                WHERE f.collection_id=%s GROUP BY f.id""", (collection_id,)).fetchall()}
        walk = workspace.areas.walk_paths(sources, row["include"], row["exclude"], truncate=True)
        files = []
        for entry in walk.files:
            known = indexed.pop(entry["path"], None)
            changed = datetime.fromtimestamp(entry["mtime"], timezone.utc)
            files.append({**entry, "indexed_at": known and known["indexed_at"], "content_hash": known and known["content_hash"],
                          "chunks": known["chunks"] if known else 0, "embedded_chunks": known["embedded_chunks"] if known else 0,
                          "stale": known is None or changed > known["indexed_at"]})
        files += [{"path": path, "source": None, "size": None, "mtime": None,
                   **{k: known[k] for k in ("indexed_at", "content_hash", "chunks", "embedded_chunks")},
                   "stale": True} for path, known in indexed.items()]  # indexed but gone from disk
        return {"files": sorted(files, key=lambda f: f["path"]), "truncated": walk.truncated, "skipped": walk.skipped,
                "missing_paths": walk.missing}

    # collection_chunks: unchanged; `path` is now the full path.

    @router.get(prefix + "/file")
    def get_file(project: UUID, collection: str, path: str):
        path = relative_path(path)
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            row = workspace.collection_by_name(conn, project, collection)
            roots = [p["source_path"] for p in workspace.paths(conn, row["id"])]
        root = next((r for r in roots if path.startswith(r + "/")), None)
        # Serve only what sync would index: under one of the collection's paths, never .env*, hidden or
        # skipped folders, or files its patterns (matched relative to that path) leave out.
        if root is None or not indexable(path[len(root) + 1:], row["include"], row["exclude"]):
            raise HTTPException(404, "Document not found")
        area, _, rest = path.partition("/")
        try:
            data = workspace.areas.read(area, rest, limit)
        except FileNotFoundError:
            raise HTTPException(404, "Document not found") from None
        try:
            content = data.decode("utf-8-sig")
        except UnicodeError:
            raise HTTPException(400, "File is not UTF-8 text") from None
        return {"collection": collection, "path": path, "content": content, "hash": digest(data)}

    @router.put(prefix + "/file")
    def put_file(project: UUID, payload: FileInput):
        path = relative_path(payload.path)
        area, _, rest = path.partition("/")
        if area != WRITABLE_AREA or "/" not in rest:
            raise HTTPException(400, "Only files inside a rag-data folder can be written; other folders are read-only")
        refuse_hidden(rest)
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
        data = payload.content.encode("utf-8")
        if len(data) > limit:
            raise HTTPException(400, "File exceeds the configured size limit")
        try:
            previous = digest(workspace.areas.read(area, rest, limit))
        except FileNotFoundError:
            previous = None
        if previous != payload.expected_hash:
            raise HTTPException(409, "File already exists or has changed; open it before replacing it")
        workspace.areas.write(area, rest, data)
        return {"path": path, "hash": digest(data)}

    # jobs, cancel and search_route: unchanged.

    return router
```

Notes for the implementer:
- `create_collection`, the old `CollectionInput` and `delete_file` are gone. With
  their routes removed, `POST collections` and `DELETE file` answer 405, because
  `GET collections` and `GET/PUT file` still exist.
- Keep `COLUMNS` out. It was the create/update column list, and
  `SETTINGS_COLUMNS` replaces it.
- `put_file` requires a folder below `rag-data` (`"/" not in rest` rejects
  `rag-data/a.md`). This matches the Jira export, which writes
  `rag-data/jira/<KEY>.md`.

- [ ] **Step 6: Delete the replaced tests**

Delete the tests listed under **Files** from `tests/test_collections_api.py`,
`tests/test_workspace.py` and `tests/test_collections.py`. In `tests/test_collections.py`, the
two renamed tests keep their `write`/`read` assertions and lose only the
`areas.delete` calls.

- [ ] **Step 7: Run the tests that do not need the worker**

Run: `cd services/rag && uv run --frozen pytest tests/test_paths_api.py tests/test_collections.py tests/test_workspace.py -q -k "not documents_catalog"`
Expected: all pass. `test_documents_catalog_and_workspace_lookup`,
`tests/test_collections_api.py`, `tests/test_sync.py` and
`tests/test_collection_search.py` run syncs, so they pass only after Task 4.

- [ ] **Step 8: Commit**

```bash
git add services/rag/src/rag/workspace.py services/rag/src/rag/workspace_api.py services/rag/src/rag/collections.py \
  services/rag/tests/test_paths_api.py services/rag/tests/test_workspace.py services/rag/tests/test_collections_api.py \
  services/rag/tests/test_collections.py
git commit -m "feat(rag): sources add and remove collection paths; settings-only edits; full-path file API

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: RAG sync over every path; port the sync and search suites

**Files:**
- Modify: `services/rag/src/rag/workspace_worker.py`
- Modify: `services/rag/tests/test_sync.py`, `services/rag/tests/test_collections_api.py`,
  `services/rag/tests/test_collection_search.py`, `services/rag/tests/test_workspace.py`

**Interfaces:**
- Consumes: from Task 2, `Areas.walk_paths` and `FolderMissing`; from Task 3,
  `Workspace.paths()` and the test helpers `collection`, `update`, `write` and `chunks`.
- Produces:
  - `snapshot(workspace, collection, sources: list[str], limit, stats) ->
    list[{path (full), source, text, content_hash}]`;
  - each `rag_files` row is written with `path_id` = its collection path's id;
  - a missing path fails the job with the `FolderMissing` message
    (`Path <p> is missing…`) and publishes nothing.
  - Search needs no code change: `rag_files.path` is already the full path, so hits
    carry `collection` and `code/payments/src/retry.py`-style paths.

- [ ] **Step 1: Write the failing tests**

Append to `services/rag/tests/test_sync.py`. Add `update` to its `test_workspace`
import and `from rag.workspace_worker import claim, process` is already there.

```python
def test_sync_indexes_two_paths_under_full_paths(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["code"] / "payments" / "src").mkdir(parents=True)
    (roots["code"] / "payments" / "src" / "retry.py").write_text("MAX_RETRIES = 5  # payment retries\n")
    (roots["rag-data"] / "jira").mkdir()
    (roots["rag-data"] / "jira" / "PAY-1.md").write_text("# PAY-1\n\nRetries fail on timeout.")
    created = collection(env, "payments", "code/payments", "rag-data/jira", include=["**/*.py", "**/*.md"])
    stats, row = sync(env, created)
    assert stats["parsed_files"] == 2 and row["indexed_files"] == 2 and row["status"] == "up_to_date"
    files = client.get(prefix + f"/collections/{created}/files").json()["files"]
    assert [(f["path"], f["source"]) for f in files] == [
        ("code/payments/src/retry.py", "code/payments"), ("rag-data/jira/PAY-1.md", "rag-data/jira")]
    assert {p["source_path"]: p["files"] for p in row["paths"]} == {"code/payments": 1, "rag-data/jira": 1}
    assert chunks(env, created, "code/payments/src/retry.py") == ["MAX_RETRIES = 5  # payment retries"]


def test_missing_path_fails_sync_and_keeps_index(env):
    workspace, client, _, prefix, _, transport, roots = env
    for name in ("a", "b"):
        (roots["rag-data"] / name).mkdir()
        (roots["rag-data"] / name / "x.md").write_text(f"# {name}\n\nretries {name}")
    created = collection(env, "both", "rag-data/a", "rag-data/b")
    sync(env, created)
    (roots["rag-data"] / "b" / "x.md").unlink()
    (roots["rag-data"] / "b").rmdir()
    row = client.get(prefix + f"/collections/{created}").json()
    assert row["status"] == "folder_missing" and row["missing_paths"] == ["rag-data/b"]
    refused = client.post(prefix + f"/collections/{created}/sync", json={})
    assert refused.status_code == 409 and "Path rag-data/b is missing" in refused.json()["error"]["message"]
    # A sync queued while the folder existed fails in the worker and publishes nothing.
    (roots["rag-data"] / "b").mkdir()
    assert client.post(prefix + f"/collections/{created}/sync", json={}).status_code == 202
    job = claim(workspace)
    (roots["rag-data"] / "b").rmdir()
    assert process(workspace, job, transport) is None
    row = client.get(prefix + f"/collections/{created}").json()
    assert "Path rag-data/b is missing" in row["job"]["error"] and row["indexed_files"] == 2
    hits = search(env, "retries", ["both"]).json()["results"]
    assert {hit["path"] for hit in hits} == {"rag-data/a/x.md", "rag-data/b/x.md"}


def test_removing_a_path_drops_its_files_from_search(env):
    client, prefix, roots = env[1], env[3], env[6]
    for name in ("a", "b"):
        (roots["rag-data"] / name).mkdir()
        (roots["rag-data"] / name / "x.md").write_text(f"# {name}\n\nretries {name}")
    created = collection(env, "both", "rag-data/a", "rag-data/b")
    sync(env, created)
    removed = client.delete(prefix + "/collections/by-name/both/paths", params={"source_path": "rag-data/b"})
    assert removed.json() == {"removed": 1}
    assert {hit["path"] for hit in search(env, "retries", ["both"]).json()["results"]} == {"rag-data/a/x.md"}
    row = client.get(prefix + f"/collections/{created}").json()
    assert row["indexed_files"] == 1 and row["status"] == "needs_refresh"
    assert (roots["rag-data"] / "b" / "x.md").is_file()  # files on disk are never deleted
```

Append to `services/rag/tests/test_collection_search.py`:

```python
def test_hits_carry_collection_and_full_path(env):
    two_collections(env)
    hits = search(env, "retries").json()["results"]
    assert {(hit["collection"], hit["path"]) for hit in hits} == {("docs", "rag-data/docs/a.md"), ("jira", "rag-data/jira/a.md")}
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest tests/test_sync.py tests/test_collection_search.py -q`
Expected: FAIL with `KeyError: 'source_path'` in `workspace_worker.snapshot`.

- [ ] **Step 3: Implement the worker changes in `services/rag/src/rag/workspace_worker.py`**

Replace `snapshot`:

```python
def snapshot(workspace: Workspace, collection: dict, sources: list[str], limit: int, stats: dict) -> list[dict]:
    """Read every indexable file under the collection's paths; a missing path raises FolderMissing."""
    walk = workspace.areas.walk_paths(sources, collection["include"], collection["exclude"])
    stats["skipped_links"], stats["skipped_special"] = walk.skipped["links"], walk.skipped["special"]
    stats["skipped_unreadable"], stats["skipped_invalid_names"] = walk.skipped["unreadable"], walk.skipped["invalid_names"]
    files, total = [], 0
    for entry in walk.files:
        if entry["size"] > limit:
            stats["skipped_large"] += 1
            continue
        area, _, rest = entry["path"].partition("/")
        try:
            data = workspace.areas.read(area, rest, limit)
        except FileNotFoundError:
            continue  # removed between walk and read; the manifest check catches real races
        total += len(data)
        if total > MAX_BYTES:
            raise HTTPException(400, "A sync is limited to 200 MB of text; narrow the collection's include patterns")
        text = decode(data)
        if text is None:
            stats["skipped_binary"] += 1
            continue
        files.append({"path": entry["path"], "source": entry["source"], "text": text, "content_hash": digest(data)})
    return files
```

In `process()`:
1. After `revision = …` and its check, read the paths in the same connection:
   ```python
            path_ids = {row["source_path"]: row["id"] for row in workspace.paths(conn, collection["id"])}
   ```
2. Replace `sources = snapshot(workspace, collection, limit, stats)` with
   `sources = snapshot(workspace, collection, list(path_ids), limit, stats)`.
3. Replace the manifest re-check
   `check = snapshot(workspace, collection, limit, dict.fromkeys(STATS, 0))` with
   `check = snapshot(workspace, collection, list(path_ids), limit, dict.fromkeys(STATS, 0))`.
4. Replace the `rag_files` upsert with:
   ```python
                row = conn.execute("""INSERT INTO ka.rag_files(project_id,collection_id,path_id,path,content_hash,processing_hash,embedding_profile,job_id)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (collection_id,path) DO UPDATE
                                    SET path_id=EXCLUDED.path_id,content_hash=EXCLUDED.content_hash,processing_hash=EXCLUDED.processing_hash,
                                    embedding_profile=EXCLUDED.embedding_profile,job_id=EXCLUDED.job_id,indexed_at=now() RETURNING id""",
                                   (project, collection["id"], path_ids[source["source"]], source["path"], source["content_hash"],
                                    fingerprint, profile, job["id"])).fetchone()
   ```

The fingerprint string stays `markdown-text-json-v3`. Migrated files keep their
`processing_hash`, so 0065 needs no reprocessing. A path added or removed during a
sync changes the collection revision, and the existing publish check fails the sync
with "Settings changed while syncing; refresh the collection again". `FolderMissing`
is an `HTTPException`, so the existing `except` stores its message as the job error.

- [ ] **Step 4: Port the remaining tests to full paths and settings-only edits**

`services/rag/tests/test_sync.py`:
- `test_sync_indexes_code_as_plain_text_with_patterns`:
  - `hits[0]["path"] == "code/payments/src/retry.py"`;
  - `chunks(env, created, "code/payments/src/retry.py")` and
    `chunks(env, created, "code/payments/tests/test_retry.py")`.
- `test_sync_removes_deleted_and_unmatched_files`: replace the `client.put(…name…source_path…)`
  call with `update(env, created, include=["**/*.md"])`.
- `test_editing_another_collection_does_not_fail_this_sync`: replace the `client.put(…)`
  with `update(env, second, description="changed")`. Drop the now unused `row_b`.
- `test_settings_change_during_sync_fails_that_sync`: replace the `client.put(…)` with
  `update(env, created, chunk_size=1000)`. Drop the now unused `row`.
- `test_unchanged_files_are_not_parsed_and_deletions_are_published`:
  - replace the `document = client.get(…"other.txt"…)` line and the `client.delete(…/file…)`
    assertion with `(roots["rag-data"] / "docs" / "other.txt").unlink()`;
  - `chunks(env, created, "rag-data/docs/other.txt") == []`.
- `test_json_is_pretty_printed_and_large_files_are_skipped`:
  `chunks(env, created, "rag-data/docs/data.json")`.
- `test_cancel_and_external_edit_cannot_publish`: replace the `client.put(…embeddings_enabled…)`
  with `update(env, created, embeddings_enabled=True)`.

`services/rag/tests/test_collections_api.py`:
- `test_missing_folder_is_reported`: also assert `row["missing_paths"] == ["docs/absent"]`.
- `test_settings_change_marks_collection_stale`:
  - `updated = update(env, created, include=["**/*.txt"])`;
  - the stale save becomes
    `client.put(prefix + f"/collections/{created}", json={"revision": row["revision"]})`,
    which still expects 409.
- `test_one_active_sync_per_collection_and_sync_stale`: replace
  `client.post(prefix + "/collections", json={"name": "c", "source_path": "docs/c"}).json()["id"]`
  with `collection(env, "c", "docs/c")`.
- `test_files_listing_shows_stale_and_removed_files`:
  - expected paths `{"rag-data/docs/a.md", "rag-data/docs/b.md", "rag-data/docs/c.md"}`;
  - use the same full paths for the `files[...]` lookups and for the chunks request
    (`params={"path": "rag-data/docs/a.md"}`);
  - assert `files["rag-data/docs/b.md"]["source"] is None` (gone from disk).
- `test_list_without_status_skips_the_walk`: replace
  `rows["docs"]["writable"] is True` with
  `[p["source_path"] for p in rows["docs"]["paths"]] == ["rag-data/docs"]`, and add
  `rows["absent"]["missing_paths"] == ["docs/absent"]`.

`services/rag/tests/test_workspace.py`, `test_documents_catalog_and_workspace_lookup`:
the expected catalog row is `("docs", "rag-data/docs/readme.md", "Operations documentation")`.

- [ ] **Step 5: Run the whole RAG suite**

Run: `cd services/rag && uv run --frozen pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add services/rag/src/rag/workspace_worker.py services/rag/tests
git commit -m "feat(rag): sync every collection path under full paths; a missing path keeps the index

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

# Part C — admin-api and docs-wiki (Tasks 5–7)

### Task 5: admin-api RAG client, Jira export into a chosen collection, proxy allowlist

**Files:**
- Create: `services/admin-api/src/knowledge_admin/rag_collections.py`
- Modify: `services/admin-api/src/knowledge_admin/rag_files.py`:
  - drop `collection()`, `_collections` and `delete_file`;
  - `write_markdown` takes a full path;
  - generic error texts.
- Modify: `services/admin-api/src/knowledge_admin/jira_export.py`: slug-pattern
  `collection`, `JIRA_PATH`, `publish_ticket` adds the path first.
- Modify: `services/admin-api/src/knowledge_admin/rag_api.py`: `ALLOWED`.
- Test: create `services/admin-api/tests/test_rag_collections_client.py`; rewrite the RAG
  mock and the collection tests in `services/admin-api/tests/test_jira_export.py`;
  update `services/admin-api/tests/test_rag.py`.

**Interfaces:**
- Consumes: the Task 3 RAG API (`PUT collections/by-name/{name}`,
  `PUT|DELETE collections/by-name/{name}/paths`, `DELETE paths`,
  `GET collections?status=false`, `GET file`, `PUT file`).
- Produces, in `knowledge_admin.rag_collections`:
  - `Owner(kind: str, id: UUID | None = None, key: str | None = None)`, with
    `.params() -> dict[str, str]`;
  - `RagCollections(client, key, project, actor)`, a subclass of `RagFiles`:
    - `ensure_collection(name, description=None) -> dict`;
    - `add_path(collection, source_path, owner=None) -> dict`;
    - `remove_path(collection, owner) -> int`;
    - `remove_paths(owner) -> int`;
    - `owned_paths(kind, owner_id) -> list[{collection, source_path, owner_kind,
      owner_id, owner_key}]`.
- Produces, in `knowledge_admin.rag_files.RagFiles`:
  - `request(method, resource, **kwargs) -> dict`: 502 `RAG service is
    unavailable. Retry later.`; RAG errors keep their status and message;
  - `get_file(collection, path) -> dict | None`;
  - `write_markdown(collection, path, content) -> {collection, path, status, hash}`,
    where `path` is the full path.
- Produces, in `knowledge_admin.jira_export`:
  - `JIRA_PATH = 'rag-data/jira'`;
  - `TicketExportInput.collection` matches `^[a-z0-9]+(-[a-z0-9]+)*$`, at most 60
    characters, default `jira`;
  - `publish_ticket(...)` returns `{collection, path: 'rag-data/jira/<KEY>.md',
    status, hash}`.
- Proxy `ALLOWED`:
  - the browser may no longer `POST collections`, `PUT file` or `DELETE file`;
  - it may `DELETE collections/by-name/{name}/paths` (detach an unowned path);
  - adding paths stays server-side.

- [ ] **Step 1: Write the failing client tests**

Create `services/admin-api/tests/test_rag_collections_client.py`:

```python
"""The admin-side RAG client for collections and source paths."""
import json
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException

from knowledge_admin.rag_collections import Owner, RagCollections

PROJECT = UUID('00000000-0000-4000-8000-000000000001')
CODE = UUID('00000000-0000-4000-8000-0000000000c0')


def backend(handler):
    return httpx.Client(base_url='http://rag.test', transport=httpx.MockTransport(handler))


def test_paths_are_added_and_removed_by_owner():
    calls = []

    def rag(request):
        calls.append((request.method, request.url.path, dict(request.url.params),
                      json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={'removed': 1} if request.method == 'DELETE' else {'name': 'payments', 'paths': []})
    with backend(rag) as client:
        rag_client = RagCollections(client, 'k', PROJECT, None)
        rag_client.ensure_collection('payments')
        rag_client.add_path('payments', 'code/api', Owner('code_project', CODE, 'api'))
        assert rag_client.remove_path('payments', Owner('jira')) == 1
        assert rag_client.remove_paths(Owner('code_project', CODE)) == 1
    prefix = f'/backoffice/projects/{PROJECT}/'
    assert calls == [
        ('PUT', prefix + 'collections/by-name/payments', {}, {}),
        ('PUT', prefix + 'collections/by-name/payments/paths', {},
         {'source_path': 'code/api', 'owner_kind': 'code_project', 'owner_id': str(CODE), 'owner_key': 'api'}),
        ('DELETE', prefix + 'collections/by-name/payments/paths', {'owner_kind': 'jira'}, None),
        ('DELETE', prefix + 'paths', {'owner_kind': 'code_project', 'owner_id': str(CODE)}, None)]


def test_owned_paths_lists_one_owner_without_status_walks():
    def rag(request):
        assert request.url.params['status'] == 'false'
        return httpx.Response(200, json={'collections': [
            {'name': 'payments', 'paths': [
                {'source_path': 'code/api', 'owner_kind': 'code_project', 'owner_id': str(CODE), 'owner_key': 'api'},
                {'source_path': 'rag-data/jira', 'owner_kind': 'jira', 'owner_id': None, 'owner_key': None}]},
            {'name': 'other', 'paths': [
                {'source_path': 'code/x', 'owner_kind': 'code_project', 'owner_id': str(PROJECT), 'owner_key': 'x'}]}]})
    with backend(rag) as client:
        owned = RagCollections(client, 'k', PROJECT, None).owned_paths('code_project', CODE)
    assert owned == [{'collection': 'payments', 'source_path': 'code/api', 'owner_kind': 'code_project',
                      'owner_id': str(CODE), 'owner_key': 'api'}]


def test_rag_errors_keep_their_message_and_outages_are_502():
    conflict = lambda request: httpx.Response(409, json={'error': {'message': 'Path overlaps code in collection payments'}})
    with backend(conflict) as client:
        with pytest.raises(HTTPException) as error:
            RagCollections(client, 'k', PROJECT, None).add_path('payments', 'code/api')
    assert (error.value.status_code, error.value.detail) == (409, 'Path overlaps code in collection payments')

    def down(request):
        raise httpx.ConnectError('refused')
    with backend(down) as client:
        with pytest.raises(HTTPException) as error:
            RagCollections(client, 'k', PROJECT, None).remove_paths(Owner('jira'))
    assert (error.value.status_code, error.value.detail) == (502, 'RAG service is unavailable. Retry later.')
```

- [ ] **Step 2: Rewrite the Jira export tests against the new RAG calls**

In `services/admin-api/tests/test_jira_export.py`:

1. In the `exporting` fixture, replace `"collections": [...]` in `state` with
   `"jira_collection": None, "rag_error": None`. Replace the `rag(request)` handler
   with:

```python
    def rag(request):
        state["rag_calls"].append(request)
        assert request.headers["x-rag-key"] == "rag-export-key"
        assert request.headers["x-rag-actor"]
        with store.connect() as conn:
            assert str(store.project(conn, "shop")) in request.url.path
        if state["rag_error"] is not None:
            error = state["rag_error"]
            if isinstance(error, Exception):
                raise error
            return httpx.Response(error[0], json={"error": {"message": error[1]}})
        if request.url.path.endswith("/paths"):
            assert request.method == "PUT"
            assert json.loads(request.content) == {"source_path": "rag-data/jira", "owner_kind": "jira"}
            state["jira_collection"] = request.url.path.split("/by-name/")[1].split("/")[0]  # RAG re-points the owner
            return httpx.Response(200, json={"name": state["jira_collection"], "paths": []})
        assert request.url.path.endswith("/file")
        if request.method == "GET":
            assert request.url.params["collection"] == state["jira_collection"]
            result = state["files"].get(request.url.params["path"])
            return httpx.Response(200, json=result) if result is not None else httpx.Response(404, json={"error": {"message": "Document not found"}})
        assert request.method == "PUT"
        body = json.loads(request.content)
        assert set(body) == {"path", "content", "expected_hash"} and body["path"].startswith("rag-data/jira/")
        previous = state["files"].get(body["path"])
        assert body["expected_hash"] == (previous["hash"] if previous is not None else None)
        result = {"path": body["path"], "content": body["content"], "hash": hashlib.sha256(body["content"].encode()).hexdigest()}
        state["files"][body["path"]] = result
        return httpx.Response(200, json={"path": result["path"], "hash": result["hash"]})
```

2. In `test_export_full_issue_all_comment_pages_and_incremental_content` and
   `test_failed_refresh_preserves_existing_file_and_unrelated_files_are_protected`,
   replace every `state["files"]["DEMO-1.md"]` with
   `state["files"]["rag-data/jira/DEMO-1.md"]`. Also make these replacements:
   - `events[0]["details"]["path"] == "DEMO-1.md"` becomes
     `events[0]["details"]["path"] == "rag-data/jira/DEMO-1.md"`;
   - `puts[0]["collection"] == "jira" and puts[0]["path"] == "DEMO-1.md"` becomes
     `puts[0]["path"] == "rag-data/jira/DEMO-1.md" and "collection" not in puts[0]`;
   - after the first export, assert
     `response.json()["collection"] == "jira" and response.json()["path"] == "rag-data/jira/DEMO-1.md"`.
3. Extend the parametrize list of `test_export_rejects_unsafe_collection_before_any_outbound_request`
   with `"Jira"`, `"jira_tickets"`, `"a--b"` and `"x" * 61`.
4. Delete `test_export_to_read_only_or_unknown_collection_is_rejected`,
   `test_rag_files_lists_collections_once_without_status` and
   `test_export_lists_collections_without_status_walks`. Keep the MOCK filter
   assertions that sit at the end of the first one by moving them into a new
   `test_mock_filter_examples()`:

```python
def test_mock_filter_examples():
    assert [issue["key"] for issue in MOCK["filter_issues"]('key = DEMO-1')] == ["DEMO-1"]
    assert MOCK["filter_issues"]('text ~ "nonexistent text"') == []
    assert len(MOCK["filter_issues"]('text ~ "final pagination evidence"')) == 75
    for query in ['project = DEMO OR status = Open', 'status in (Open, Done)', 'project = DEMO AND', 'ORDER BY created ASC']:
        with pytest.raises(ValueError):
            MOCK["filter_issues"](query)
```

5. Append:

```python
def test_export_adds_the_jira_path_to_the_chosen_collection(exporting):
    client, snapshot, state = exporting
    response = export(client, snapshot, collection="support")
    assert response.status_code == 200, response.text
    assert response.json()["collection"] == "support" and state["jira_collection"] == "support"
    paths = [call for call in state["rag_calls"] if call.url.path.endswith("/paths")]
    assert len(paths) == 1 and paths[0].url.path.endswith("/collections/by-name/support/paths")
    assert state["rag_calls"].index(paths[0]) == 0  # the path is in place before anything is read or written
    assert not [call for call in state["rag_calls"] if call.url.path.endswith("/collections")]


def test_export_to_another_collection_moves_the_jira_path(exporting):
    client, snapshot, state = exporting
    assert export(client, snapshot).status_code == 200
    moved = export(client, snapshot, collection="support")
    assert moved.status_code == 200 and moved.json()["status"] == "unchanged"
    assert [call.url.path.split("/by-name/")[1] for call in state["rag_calls"] if call.url.path.endswith("/paths")] == [
        "jira/paths", "support/paths"]
    assert list(state["files"]) == ["rag-data/jira/DEMO-1.md"]  # one folder, now indexed by support


def test_export_fails_with_rag_overlap_message(exporting):
    client, snapshot, state = exporting
    state["rag_error"] = (409, "Path overlaps rag-data in collection support")
    response = export(client, snapshot, collection="support")
    assert response.status_code == 409 and "Path overlaps rag-data in collection support" in response.text
    assert state["files"] == {}


def test_export_fails_when_rag_is_unavailable(exporting, store):
    client, snapshot, state = exporting
    state["rag_error"] = httpx.ConnectError("refused")
    response = export(client, snapshot)
    assert response.status_code == 502 and "RAG service is unavailable. Retry later." in response.text
    assert state["files"] == {}
    with store.connect() as conn:
        assert conn.execute("SELECT 1 FROM ka.audit_events WHERE action='jira.ticket_exported'").fetchone() is None
```

- [ ] **Step 3: Update the proxy test in `services/admin-api/tests/test_rag.py`**

Extend the forbidden tuple list with:

```python
                                 ("POST", "collections"), ("PUT", "file"), ("DELETE", "file"), ("DELETE", "paths"),
                                 ("PUT", "collections/by-name/jira"), ("PUT", "collections/by-name/jira/paths"),
                                 ("DELETE", "collections/by-name/Jira/paths")
```

In the allowed list, remove `("POST", "collections")` and add
`("DELETE", "collections/by-name/jira/paths")`.

- [ ] **Step 4: Run them and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_rag_collections_client.py tests/test_jira_export.py tests/test_rag.py -q`
Expected: FAIL. `knowledge_admin.rag_collections` is missing, the export still lists
collections, and the proxy still allows `POST collections`.

- [ ] **Step 5: Implement**

Create `services/admin-api/src/knowledge_admin/rag_collections.py`:

```python
"""Collections and their source paths in the RAG service, for sources that own folders (Jira, code projects)."""
from dataclasses import dataclass
from uuid import UUID

from knowledge_admin.rag_files import RagFiles


@dataclass(frozen=True)
class Owner:
    """The source that owns a path: kind 'documents' | 'jira' | 'code_project', plus an optional id and key."""
    kind: str
    id: UUID | None = None
    key: str | None = None

    def params(self) -> dict[str, str]:
        values = {'owner_kind': self.kind}
        if self.id is not None:
            values['owner_id'] = str(self.id)
        if self.key is not None:
            values['owner_key'] = self.key
        return values


class RagCollections(RagFiles):
    """One project's collections, acting for one principal. Collection names are validated slugs."""

    def ensure_collection(self, name: str, description: str | None = None) -> dict:
        return self.request('PUT', f'collections/by-name/{name}', json={} if description is None else {'description': description})

    def add_path(self, collection: str, source_path: str, owner: Owner | None = None) -> dict:
        """Add a folder to `collection` (created if missing). The same owner's path is re-pointed, also from another collection."""
        return self.request('PUT', f'collections/by-name/{collection}/paths',
                            json={'source_path': source_path, **(owner.params() if owner else {})})

    def remove_path(self, collection: str, owner: Owner) -> int:
        return self.request('DELETE', f'collections/by-name/{collection}/paths', params=owner.params())['removed']

    def remove_paths(self, owner: Owner) -> int:
        """Remove the owner's paths from every collection; without a key, every path of that owner id."""
        return self.request('DELETE', 'paths', params=owner.params())['removed']

    def owned_paths(self, kind: str, owner_id: UUID) -> list[dict]:
        rows = self.request('GET', 'collections', params={'status': 'false'})['collections']
        return [{**path, 'collection': row['name']} for row in rows for path in row['paths']
                if path['owner_kind'] == kind and path['owner_id'] == str(owner_id)]
```

`owned_paths` passes each path row through unchanged and adds `collection`. The real
RAG rows also carry `host_path`, `present` and `files`. Callers use only
`collection`, `source_path` and the `owner_*` fields.

Replace `services/admin-api/src/knowledge_admin/rag_files.py`:

```python
"""Read and write files under rag-data through the RAG backoffice API."""
from uuid import UUID

import httpx
from fastapi import HTTPException


class RagFiles:
    """One project's RAG service, acting for one principal."""

    def __init__(self, client: httpx.Client, key: str, project: UUID, actor: UUID | None):
        self.client, self.prefix = client, f'/backoffice/projects/{project}/'
        self.headers = {'x-rag-key': key, **({'x-rag-actor': str(actor)} if actor else {})}

    def request(self, method: str, resource: str, **kwargs: object) -> dict:
        try:
            response = self.client.request(method, self.prefix + resource, headers=self.headers, timeout=30, **kwargs)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError('Invalid RAG response')
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, 'RAG service is unavailable. Retry later.') from None
        if response.status_code >= 300:
            raise HTTPException(response.status_code, result.get('error', {}).get('message') or 'RAG request failed')
        return result

    def get_file(self, collection: str, path: str) -> dict | None:
        try:
            return self.request('GET', 'file', params={'collection': collection, 'path': path})
        except HTTPException as error:
            if error.status_code == 404:
                return None
            raise

    def write_markdown(self, collection: str, path: str, content: str) -> dict:
        """Create or replace `path` (full, under rag-data) indexed by `collection`.

        A file whose first line differs belongs to another source and is kept.
        """
        previous = self.get_file(collection, path)
        if previous is not None:
            if previous['content'].partition('\n')[0] != content.partition('\n')[0]:
                raise HTTPException(409, f'{path} belongs to another source. The existing file was preserved')
            if previous['content'] == content:
                return {'collection': collection, 'path': path, 'status': 'unchanged', 'hash': previous['hash']}
        saved = self.request('PUT', 'file', json={'path': path, 'content': content,
                                                  'expected_hash': previous['hash'] if previous is not None else None})
        return {'collection': collection, 'path': path, 'status': 'updated' if previous is not None else 'created', 'hash': saved['hash']}
```

In `services/admin-api/src/knowledge_admin/jira_export.py`:
- replace the import with `from knowledge_admin.rag_collections import Owner, RagCollections`;
- add `JIRA_PATH = "rag-data/jira"` under the imports;
- change the field to
  `collection: str = Field(default="jira", max_length=60, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")`;
- replace `publish_ticket` with:

```python
def publish_ticket(client: httpx.Client, key: str, project: UUID, actor: UUID,
                   collection: str, issue_key: str, content: str) -> dict:
    """Make sure rag-data/jira is a path of `collection` (moving it from another collection), then write the ticket."""
    rag = RagCollections(client, key, project, actor)
    rag.add_path(collection, JIRA_PATH, Owner("jira"))
    return rag.write_markdown(collection, f"{JIRA_PATH}/{issue_key}.md", content)
```

`jira_api.export_ticket` needs no change. Its audit event already records
`result["path"]`, which is now the full path.

In `services/admin-api/src/knowledge_admin/rag_api.py`:

```python
ALLOWED = {
    "GET": r"settings|collections|collections/[0-9a-f-]{36}(/files|/chunks)?|file|jobs",
    "PUT": r"settings|collections/[0-9a-f-]{36}",
    "POST": r"collections/sync-stale|collections/[0-9a-f-]{36}/sync|jobs/[0-9a-f-]{36}/cancel|test/embedding|search",
    # Admins detach unowned paths; sources add and remove their own paths server-side.
    "DELETE": r"collections/[0-9a-f-]{36}|collections/by-name/[a-z0-9]+(-[a-z0-9]+)*/paths",
}
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_rag_collections_client.py tests/test_jira_export.py tests/test_rag.py tests/test_jira.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add services/admin-api/src/knowledge_admin/rag_collections.py services/admin-api/src/knowledge_admin/rag_files.py \
  services/admin-api/src/knowledge_admin/jira_export.py services/admin-api/src/knowledge_admin/rag_api.py \
  services/admin-api/tests/test_rag_collections_client.py services/admin-api/tests/test_jira_export.py services/admin-api/tests/test_rag.py
git commit -m "feat(admin): Jira export adds rag-data/jira to the chosen collection; RAG collections client

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: admin-api — searchable code projects

**Files:**
- Create: `services/admin-api/src/knowledge_admin/code_project_search.py`
- Modify: `services/admin-api/src/knowledge_admin/repository_api.py`:
  - `build_api(app_name, store, backoffice_client=None)`;
  - `PUT /code-projects/{id}/search`;
  - `POST /code-projects/{id}/archive`;
  - edits reconcile.
- Modify: `services/admin-api/src/knowledge_admin/repositories.py`: `REPOSITORY_FIELDS`
  adds `searchable`, `collection` and `search_error`.
- Modify: `services/admin-api/src/knowledge_admin/api.py`: pass `backoffice_client`
  to `repository_api.build_api`.
- Test: `services/admin-api/tests/test_code_project_search.py`

**Interfaces:**
- Consumes:
  - from Task 5: `RagCollections.add_path`, `remove_paths`, `owned_paths` and
    `Owner`;
  - from Task 1: `ka.repositories.search_collection` and `search_error`.
- Produces:
  - `GET /api/code-projects` rows add `searchable: bool`, `collection: str | None`
    and `search_error: str | None`;
  - `PUT /api/code-projects/{id}/search` with `{enabled: bool, collection?: slug}`
    → `{searchable, collection, search_error}`. It always returns 200 when the
    project exists. A RAG failure is stored and returned as `search_error`, and
    the saved choice is kept;
  - `PUT /api/code-projects/{id}` → `{id, search_error}`. A searchable project's
    folders are reconciled after the save is committed;
  - `POST /api/code-projects/{id}/archive` → `{id, search_error}`. It archives the
    project, then removes its paths from every collection;
  - in `knowledge_admin.code_project_search`:
    - `default_collection(name, project_id) -> str`;
    - `top_folders(folders) -> list[str]`;
    - `sync_paths(rag, project_id, collection, folders) -> None`;
    - `reconcile(store, app_name, client, repository_id, actor, *, release=False)
      -> str | None`.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-api/tests/test_code_project_search.py`:

```python
"""Searchable code projects keep one RAG path per mapped folder in their chosen collection."""
import json
from uuid import UUID

import httpx
import pytest

from knowledge_admin.code_project_search import default_collection, top_folders
from test_api import _client
from test_repositories import HEADERS


class FakeRag:
    """Owned paths as the RAG service keeps them: one path per owner, re-pointed when added again."""

    def __init__(self):
        self.paths: dict[tuple, dict] = {}
        self.down = False
        self.calls: list[tuple] = []

    def __call__(self, request):
        self.calls.append((request.method, request.url.path, dict(request.url.params)))
        if self.down:
            raise httpx.ConnectError('refused')
        path = request.url.path
        if request.method == 'GET' and path.endswith('/collections'):
            names = sorted({p['collection'] for p in self.paths.values()})
            return httpx.Response(200, json={'collections': [{'name': name, 'paths': [
                {'source_path': p['source_path'], 'owner_kind': k[0], 'owner_id': k[1], 'owner_key': k[2]}
                for k, p in self.paths.items() if p['collection'] == name]} for name in names]})
        if request.method == 'PUT' and path.endswith('/paths'):
            body = json.loads(request.content)
            name = path.split('/by-name/')[1].split('/')[0]
            self.paths[(body['owner_kind'], body.get('owner_id'), body.get('owner_key'))] = {
                'collection': name, 'source_path': body['source_path']}
            return httpx.Response(200, json={'name': name, 'paths': []})
        if request.method == 'DELETE' and path.endswith('/paths'):
            query = request.url.params
            gone = [k for k in self.paths if k[0] == query['owner_kind'] and k[1] == query.get('owner_id')
                    and ('owner_key' not in query or k[2] == query['owner_key'])]
            for k in gone:
                del self.paths[k]
            return httpx.Response(200, json={'removed': len(gone)})
        raise AssertionError(path)

    def owned(self, project_id: str) -> set[tuple[str, str]]:
        return {(p['collection'], p['source_path']) for k, p in self.paths.items() if k[1] == project_id}


@pytest.fixture
def searchable(store, tmp_path, monkeypatch):
    root = tmp_path / 'code'
    root.mkdir()
    for folder in ('api', 'shared', 'other'):
        (root / folder).mkdir()
    monkeypatch.setenv('CODE_ROOT', str(root))
    monkeypatch.setenv('RAG_BACKOFFICE_KEY', 'rag-key')
    rag = FakeRag()
    with httpx.Client(base_url='http://rag.test', transport=httpx.MockTransport(rag)) as backend:
        client = _client(tmp_path, repository_store=store, backoffice_client=backend)
        created = client.post('/api/code-projects', headers=HEADERS, json={
            'name': 'Payments API', 'description': '', 'remote_url': None, 'folder_paths': ['api', 'shared']})
        assert created.status_code == 201, created.text
        yield client, rag, created.json()['id']


def project(client, project_id):
    return next(p for p in client.get('/api/code-projects', headers=HEADERS).json()['projects'] if p['id'] == project_id)


def search(client, project_id, **body):
    return client.put(f'/api/code-projects/{project_id}/search', headers=HEADERS, json=body)


def edit_folders(client, project_id, folders):
    row = project(client, project_id)
    return client.put(f'/api/code-projects/{project_id}', headers=HEADERS, json={
        'name': row['name'], 'description': row['description'], 'remote_url': None, 'folder_paths': folders, 'revision': row['revision']})


def test_defaults():
    assert default_collection('Payments API', UUID(int=1)) == 'payments-api'
    assert default_collection('  ### ', UUID('12345678-0000-4000-8000-000000000000')) == 'code-12345678'
    assert len(default_collection('x' * 80, UUID(int=1))) == 60
    assert top_folders(['api', 'api/src', 'shared', 'apis']) == ['api', 'shared', 'apis']


def test_searchable_on_adds_every_folder_to_the_default_collection(searchable):
    client, rag, project_id = searchable
    row = project(client, project_id)
    assert (row['searchable'], row['collection'], row['search_error']) == (False, None, None)
    response = search(client, project_id, enabled=True)
    assert response.status_code == 200, response.text
    assert response.json() == {'searchable': True, 'collection': 'payments-api', 'search_error': None}
    assert rag.owned(project_id) == {('payments-api', 'code/api'), ('payments-api', 'code/shared')}
    row = project(client, project_id)
    assert (row['searchable'], row['collection'], row['search_error']) == (True, 'payments-api', None)


def test_searchable_off_removes_the_project_paths_everywhere(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    response = search(client, project_id, enabled=False)
    assert response.json() == {'searchable': False, 'collection': None, 'search_error': None}
    assert rag.owned(project_id) == set()
    assert ('DELETE', 'code_project', project_id) in {(m, q.get('owner_kind'), q.get('owner_id')) for m, _, q in rag.calls}
    assert project(client, project_id)['searchable'] is False


def test_folder_edit_adds_and_removes_paths(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    edited = edit_folders(client, project_id, ['shared', 'other'])
    assert edited.status_code == 200 and edited.json()['search_error'] is None
    assert rag.owned(project_id) == {('payments-api', 'code/other'), ('payments-api', 'code/shared')}


def test_changing_collection_moves_project_paths(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    moved = search(client, project_id, enabled=True, collection='payments')
    assert moved.json()['collection'] == 'payments'
    assert rag.owned(project_id) == {('payments', 'code/api'), ('payments', 'code/shared')}


def test_archive_removes_paths_and_hides_the_project(searchable):
    client, rag, project_id = searchable
    search(client, project_id, enabled=True)
    archived = client.post(f'/api/code-projects/{project_id}/archive', headers=HEADERS)
    assert archived.status_code == 200 and archived.json() == {'id': project_id, 'search_error': None}
    assert rag.owned(project_id) == set()
    assert all(p['id'] != project_id for p in client.get('/api/code-projects', headers=HEADERS).json()['projects'])
    assert client.post(f'/api/code-projects/{project_id}/archive', headers=HEADERS).status_code == 404


def test_rag_failure_keeps_project_and_retry_reconciles(searchable):
    client, rag, project_id = searchable
    rag.down = True
    response = search(client, project_id, enabled=True)
    assert response.status_code == 200
    assert response.json() == {'searchable': True, 'collection': 'payments-api', 'search_error': 'RAG service is unavailable. Retry later.'}
    edited = edit_folders(client, project_id, ['api'])
    assert edited.status_code == 200 and edited.json()['search_error'] == 'RAG service is unavailable. Retry later.'
    row = project(client, project_id)
    assert row['folder_paths'] == ['api'] and row['searchable'] is True and row['search_error'] == 'RAG service is unavailable. Retry later.'
    rag.down = False
    retried = search(client, project_id, enabled=True, collection='payments-api')
    assert retried.json()['search_error'] is None
    assert rag.owned(project_id) == {('payments-api', 'code/api')}
    assert project(client, project_id)['search_error'] is None


def test_invalid_collection_names_are_rejected_before_rag(searchable):
    client, rag, project_id = searchable
    for bad in ('Payments', 'a--b', 'x' * 61, '../x', 'jira_tickets'):
        assert search(client, project_id, enabled=True, collection=bad).status_code == 400, bad
    assert search(client, '00000000-0000-4000-8000-000000000000', enabled=True).status_code == 404
    assert rag.calls == []


def test_projects_that_are_not_searchable_never_call_rag(searchable):
    client, rag, project_id = searchable
    assert edit_folders(client, project_id, ['other']).status_code == 200
    assert rag.calls == []
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_code_project_search.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'knowledge_admin.code_project_search'`.

- [ ] **Step 3: Implement**

Create `services/admin-api/src/knowledge_admin/code_project_search.py`:

```python
"""Searchable code projects: each mapped folder is a path, owned by the project, of its chosen RAG collection."""
import os
import re
from uuid import UUID

import httpx
from fastapi import HTTPException

from knowledge_admin.rag_collections import Owner, RagCollections
from knowledge_admin.repositories import RepositoryStore

OWNER_KIND = 'code_project'


def default_collection(name: str, project_id: UUID) -> str:
    """The project's slug: lowercase letters, digits and single hyphens, at most 60 characters."""
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60].strip('-')
    return slug or f'code-{project_id.hex[:8]}'


def top_folders(folders: list[str]) -> list[str]:
    """Folders not inside another mapped folder; a collection may not hold overlapping paths."""
    return [f for f in folders if not any(f != other and f.startswith(other + '/') for other in folders)]


def sync_paths(rag: RagCollections, project_id: UUID, collection: str | None, folders: list[str]) -> None:
    """Make RAG hold exactly code/<folder> for each folder in `collection`; None removes them everywhere."""
    if collection is None:
        rag.remove_paths(Owner(OWNER_KIND, project_id))
        return
    wanted = top_folders(folders)
    for row in rag.owned_paths(OWNER_KIND, project_id):
        if row['owner_key'] not in wanted:
            rag.remove_paths(Owner(OWNER_KIND, project_id, row['owner_key']))
    for folder in wanted:  # adding an owned path again moves it here from another collection
        rag.add_path(collection, f'code/{folder}', Owner(OWNER_KIND, project_id, folder))


def reconcile(store: RepositoryStore, app_name: str, client: httpx.Client | None, repository_id: UUID,
              actor: UUID, *, release: bool = False) -> str | None:
    """Bring RAG in line with the committed project row and record the outcome in search_error.

    Projects that are not searchable are left alone unless `release` (Searchable switched off, or a
    searchable project archived). Returns None, or the message the page shows with Retry; a failure
    never rolls back the project.
    """
    with store.connect() as conn:
        project = store.project(conn, app_name)
        row = conn.execute('''SELECT r.search_collection, r.archived_at,
            ARRAY(SELECT f.path::text FROM ka.code_project_folders f WHERE f.code_project_id=r.id ORDER BY f.path) AS folders
            FROM ka.repositories r WHERE r.id=%s AND r.project_id=%s''', (repository_id, project)).fetchone()
    collection = row['search_collection'] if row['archived_at'] is None else None
    if collection is None and not release:
        return None
    key = os.environ.get('RAG_BACKOFFICE_KEY')
    error = None
    try:
        if client is None or not key:
            raise HTTPException(503, 'RAG administration is not configured')
        sync_paths(RagCollections(client, key, project, actor), repository_id, collection, row['folders'])
    except HTTPException as exc:
        error = str(exc.detail)[:2000]
    with store.connect() as conn:
        conn.execute('UPDATE ka.repositories SET search_error=%s WHERE id=%s', (error, repository_id))
    return error
```

In `services/admin-api/src/knowledge_admin/repositories.py`, add these columns to
`REPOSITORY_FIELDS`, after `r.revision,`:

```python
    (r.search_collection IS NOT NULL) AS searchable, r.search_collection AS collection, r.search_error,
```

In `services/admin-api/src/knowledge_admin/api.py`, change the repository router line to:

```python
    router.include_router(repository_api.build_api(app_name, repository_store, backoffice_client))
```

In `services/admin-api/src/knowledge_admin/repository_api.py`:
1. Imports: add `import httpx`, `from pydantic import BaseModel, ConfigDict, Field` and
   `from knowledge_admin.code_project_search import default_collection, reconcile`.
2. Add the input model above `build_api`:

```python
class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    enabled: bool
    collection: str | None = Field(default=None, max_length=60, pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')
```

3. Change the signature to
   `def build_api(app_name: str, store: RepositoryStore | None, backoffice_client: httpx.Client | None = None) -> APIRouter:`
   and add this helper after `repository_store()`:

```python
    def refresh_search(repository_id: UUID, request: Request, *, release: bool = False) -> str | None:
        selected = repository_store()
        with selected.connect() as conn:
            actor = selected.actor(conn, *request_actor(request))
        return reconcile(selected, app_name, backoffice_client, repository_id, actor, release=release)
```

4. Add the routes after `process_project`:

```python
    @router.put('/code-projects/{repository_id}/search')
    def set_search(repository_id: UUID, value: SearchInput, request: Request):
        """Searchable on adds every mapped folder to the collection (default: the project's slug); off removes them."""
        selected = repository_store()
        with selected.connect() as conn:
            project = selected.project(conn, app_name)
            repo = conn.execute('''SELECT id,name FROM ka.repositories WHERE id=%s AND project_id=%s AND archived_at IS NULL
                FOR UPDATE''', (repository_id, project)).fetchone()
            if repo is None:
                raise HTTPException(404, 'Code project not found')
            collection = (value.collection or default_collection(repo['name'], repository_id)) if value.enabled else None
            conn.execute('UPDATE ka.repositories SET search_collection=%s,search_error=NULL WHERE id=%s', (collection, repository_id))
        error = refresh_search(repository_id, request, release=True)
        return {'searchable': collection is not None, 'collection': collection, 'search_error': error}

    @router.post('/code-projects/{repository_id}/archive')
    def archive_project(repository_id: UUID, request: Request):
        """Hide the project everywhere; a searchable project's folders leave every collection."""
        selected = repository_store()
        with selected.connect() as conn:
            project = selected.project(conn, app_name)
            repo = conn.execute('''SELECT id,search_collection FROM ka.repositories WHERE id=%s AND project_id=%s
                AND archived_at IS NULL FOR UPDATE''', (repository_id, project)).fetchone()
            if repo is None:
                raise HTTPException(404, 'Code project not found')
            selected.require_idle(conn, repository_id)
            conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (repository_id,))
        error = refresh_search(repository_id, request, release=repo['search_collection'] is not None)
        return {'id': repository_id, 'search_error': error}
```

5. In `edit_repository`, return after the save:

```python
        repository_store().save(app_name, value, *request_actor(request), repository_id=repository_id)
        return {"id": repository_id, "search_error": refresh_search(repository_id, request)}
```

`SearchInput` validation errors return 400 through the existing admin-api
validation handler, as the Jira export's `collection` does.

- [ ] **Step 4: Run the tests and the neighbouring suites**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_code_project_search.py tests/test_code_projects.py tests/test_repositories.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/code_project_search.py services/admin-api/src/knowledge_admin/repository_api.py \
  services/admin-api/src/knowledge_admin/repositories.py services/admin-api/src/knowledge_admin/api.py \
  services/admin-api/tests/test_code_project_search.py
git commit -m "feat(admin): searchable code projects add their folders to a RAG collection

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Agents, Reader and docs-wiki use collection paths

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/collections.py`: the shim yields `paths`.
- Modify: `services/admin-api/src/knowledge_admin/agents.py`:
  - `get_agent_config` builds `paths` from `rag_collection_paths`;
  - `get_paths` reads every path.
- Modify: `services/admin-api/src/knowledge_admin/tools/files.py`: `_roots` reads every path.
- Modify: `services/admin-api/src/knowledge_admin/reader_api.py`: `narrow` emits `paths: []`.
- Modify: `services/admin-api/src/knowledge_admin/tools/documents.py`: the `read_document` docstring.
- Modify: `services/admin-api/src/knowledge_admin/documentation_updates.py`: the
  comparison agent's collection item uses `paths`.
- Test:
  - `services/admin-api/tests/test_agent_collections.py`;
  - `services/admin-api/tests/test_agents.py`;
  - `services/admin-api/tests/test_reader_api.py`;
  - `services/admin-api/tests/test_document_tools.py`;
  - `services/admin-api/tests/test_documentation_updates.py`;
  - `services/docs-wiki/tests/test_api.py`.

**Interfaces:**
- Consumes: from Task 1, `ka.rag_collection_paths`; from Task 3, RAG hits and the
  file API with full paths.
- Produces:
  - `documents.collections` items are `{id, name, description, paths:
    [container paths]}`, sorted by `source_path`;
  - `document_collections(config) -> (bool, [{…, paths}])`:
    - an item saved with `path` becomes `paths: [path]`, or `[]` when it is empty;
    - folder-era configs become `paths: [<RAG_SOURCE_ROOT>/<top>]`;
  - `get_paths(config)` and `tools.files._roots` include every path of every
    granted collection, for non-grounded document agents;
  - `read_document(collection, path)` forwards the full path unchanged;
  - the docs-wiki Reader path is `<collection>/<full path>`. This needs no code
    change.

- [ ] **Step 1: Update the tests first**

`services/admin-api/tests/test_agent_collections.py`:

```python
def seed(store, *names_and_paths):
    """Collections with their paths: ('name', 'area/path', ...)."""
    with store.connect() as conn:
        project = store.project(conn, 'shop')
        conn.execute("INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{\"max_file_bytes\":2000000}') ON CONFLICT DO NOTHING", (project,))
        ids = {}
        for name, *paths in names_and_paths:
            ids[name] = conn.execute("INSERT INTO ka.rag_collections(project_id,name,description) VALUES (%s,%s,%s) RETURNING id",
                                     (project, name, f'About {name}')).fetchone()['id']
            for path in paths:
                conn.execute("INSERT INTO ka.rag_collection_paths(collection_id,source_path) VALUES (%s,%s)", (ids[name], path))
        return ids


def test_agent_config_lists_granted_collections_with_paths(store, monkeypatch):
    monkeypatch.setenv('RAG_SOURCE_ROOT', '/work/rag-data')
    monkeypatch.setenv('CODE_ROOT', '/work/code')
    monkeypatch.setenv('BOOTSTRAP_DOCS_ROOT', '/work/docs')
    ids = seed(store, ('jira', 'rag-data/jira'), ('payments', 'docs/payments', 'code/payments'))
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET all_collections=false,documents_enabled=true WHERE id='search'")
        conn.execute("INSERT INTO ka.agent_collections(agent_id,collection_id) VALUES ('search',%s)", (ids['payments'],))
    config = get_agent_config('search', conninfo=store.conninfo)
    assert config['documents']['all_collections'] is False
    assert [(c['name'], c['paths']) for c in config['documents']['collections']] == [
        ('payments', ['/work/code/payments', '/work/docs/payments'])]
    assert set(config['documents']['collections'][0]) == {'id', 'name', 'description', 'paths'}
    with store.connect() as conn:
        conn.execute("UPDATE ka.agents SET all_collections=true WHERE id='search'")
        conn.execute("UPDATE ka.rag_collections SET deleted_at=now() WHERE name='payments'")
    config = get_agent_config('search', conninfo=store.conninfo)
    assert [(c['name'], c['paths']) for c in config['documents']['collections']] == [('jira', ['/work/rag-data/jira'])]


def test_path_era_items_map_to_paths():
    saved = {'documents': {'all_collections': False, 'collections': [
        {'id': 'a', 'name': 'jira', 'description': '', 'path': '/work/rag-data/jira'},
        {'name': 'docs', 'description': '', 'path': ''}]}}
    assert document_collections(saved) == (False, [
        {'id': 'a', 'name': 'jira', 'description': '', 'paths': ['/work/rag-data/jira']},
        {'name': 'docs', 'description': '', 'paths': []}])
    current = {'documents': {'all_collections': True, 'collections': [{'id': 'a', 'name': 'x', 'description': '', 'paths': ['/a', '/b']}]}}
    assert document_collections(current) == (True, current['documents']['collections'])


def test_ungrounded_document_agents_read_every_collection_path(monkeypatch):
    config = {'code': {'enabled': False, 'projects': []}, 'documents': {'enabled': True, 'grounded': False,
              'all_collections': False, 'collections': [
                  {'id': 'a', 'name': 'payments', 'description': '', 'paths': ['/work/code/payments', '/work/rag-data/jira']},
                  {'id': 'b', 'name': 'old', 'description': '', 'path': '/work/rag-data/old'}]}}
    assert [str(p) for p in get_paths(config)] == ['/work/code/payments', '/work/rag-data/jira', '/work/rag-data/old']
```

Make these further edits to the same file:
- Delete the old `test_ungrounded_document_agents_read_collection_folders`.
- `test_legacy_folder_config_maps_to_collections`: the item assertion becomes
  `[(i['name'], i['paths']) for i in items] == [('demo', ['/work/rag-data/demo']), ('old-stuff', ['/work/rag-data/Old_Stuff'])]`.
- `test_legacy_folders_without_search_folder_use_their_path`: the expected value
  becomes `(False, [{'name': 'runbooks', 'description': 'r', 'paths': ['/work/rag-data/Runbooks']}])`.
- `test_profile_api_grants_collections`: `seed(store, ('jira', 'rag-data/jira'))` keeps
  working with the new `seed`.

`services/admin-api/tests/test_agents.py`:
- In the `saved_agent` fixture, replace the collection loop with:
  ```python
        for name, path in (('payments', 'payments'), ('runbooks', 'payments/runbooks'), ('payments-old', 'payments-old'), ('private', 'private')):
            collection = conn.execute('INSERT INTO ka.rag_collections(project_id,name,description) VALUES (%s,%s,%s) RETURNING id',
                (project, name, f'Description of {path}')).fetchone()['id']
            conn.execute('INSERT INTO ka.rag_collection_paths(collection_id,source_path) VALUES (%s,%s)', (collection, 'rag-data/' + path))
  ```
- In `test_complete_config_active_prompt_and_secret_locations`, the collections
  assertion becomes
  `[(c['name'], c['paths']) for c in config['documents']['collections']] == [('payments', ['/work/rag-data/payments'])]`.
- In `test_disabled_capabilities_preserve_selections_and_all_scope_is_current`, the set
  becomes
  `{p for c in config['documents']['collections'] for p in c['paths']} == {…same three paths…}`.
- In `test_paths_use_runtime_mounts_without_host_resolution_or_database_changes`:
  - `config['documents']['collections'][0]['paths'] == ['/agent/documents/payments']`;
  - the database check becomes:
  ```python
        assert conn.execute("""SELECT p.source_path FROM ka.rag_collection_paths p JOIN ka.rag_collections c ON c.id=p.collection_id
            WHERE c.name='runbooks'""").fetchone()['source_path'] == 'rag-data/payments/runbooks'
  ```

`services/admin-api/tests/test_documentation_updates.py`: replace the three
`Path(config['documents']['collections'][0]['path'])` reads with
`Path(config['documents']['collections'][0]['paths'][0])`. The comparison agent's
config now uses the current shape (see Step 3).

`services/admin-api/tests/test_reader_api.py`,
`test_narrow_all_collections_agent_uses_the_selection`: the expected list becomes
`[{'name': 'docs', 'description': '', 'paths': []}, {'name': 'jira', 'description': '', 'paths': []}]`.

`services/admin-api/tests/test_document_tools.py`: append

```python
def test_read_document_passes_full_path(rag):
    calls, _ = rag
    context = run_context(config(collections=[{'name': 'jira', 'description': '', 'paths': []}]))
    result = invoke(read_document, context, collection='jira', path='rag-data/jira/DEMO-1.md')
    assert result == '[1] jira/rag-data/jira/DEMO-1.md\nFull retry policy.'
    assert dict(calls[-1].url.params) == {'collection': 'jira', 'path': 'rag-data/jira/DEMO-1.md'}
    assert context['grounding']['excerpts'][0]['path'] == 'rag-data/jira/DEMO-1.md'
    assert invoke(read_document, context, collection='jira', path='rag-data/jira/.env') == 'Document not found.'
```

`services/docs-wiki/tests/test_api.py`:
1. In `_workspace_client`, use full paths:
   - `/documents` returns `{'collection': 'jira', 'path': 'rag-data/jira/DEMO-1.md', …}`
     and `{'collection': 'wiki', 'path': 'rag-data/wiki/a/b.md', …}`;
   - the `/file` branch compares
     `request.url.params['path'] == 'rag-data/jira/unselected.py'` and returns
     `{'collection': 'jira', 'path': 'rag-data/jira/DEMO-1.md', 'content': '# Demo'}`;
   - `/search` returns `{'collection': 'jira', 'path': 'rag-data/jira/DEMO-1.md', 'text': 'x', 'score': 1}`.
2. `test_workspace_toc_groups_by_collection`: assert
   `body[0]["docs"][0]["path"] == "jira/rag-data/jira/DEMO-1.md"` and
   `body[0]["docs"][0]["title"] == "DEMO-1.md"`.
3. Rename `test_workspace_doc_reads_collection_file` to `test_workspace_doc_reads_full_path`:

```python
def test_workspace_doc_reads_full_path(tmp_path):
    calls = []
    client = _client(tmp_path, rag_client=_workspace_client(calls))
    body = client.get("/api/shop/doc", params={"path": "jira/rag-data/jira/DEMO-1.md"}).json()
    assert body["path"] == "jira/rag-data/jira/DEMO-1.md" and body["markdown"] == "# Demo" and body["title"] == "DEMO-1.md"
    last = [c for c in calls if c.url.path.endswith("/file")][-1]
    assert dict(last.url.params) == {"collection": "jira", "path": "rag-data/jira/DEMO-1.md"}
```

4. `test_workspace_doc_never_serves_hidden_or_unindexed_files`:
   - the hidden paths become `"jira/rag-data/jira/.env"`,
     `"jira/rag-data/jira/.env.local"`, `"jira/rag-data/jira/.git/config"` and
     `"jira/rag-data/jira/node_modules/x/README.md"`;
   - the unselected file becomes `"jira/rag-data/jira/unselected.py"`.
5. `test_workspace_search_links_collection_path`: assert
   `body[0]["path"] == "jira/rag-data/jira/DEMO-1.md"`.

- [ ] **Step 2: Run them and confirm they fail**

Run:
```bash
cd services/admin-api && uv run --frozen pytest tests/test_agent_collections.py tests/test_agents.py tests/test_reader_api.py tests/test_document_tools.py tests/test_documentation_updates.py -q
cd ../docs-wiki && uv run --frozen pytest tests/test_api.py -q
```
Expected:
- admin-api FAILS: `get_agent_config` still selects `c.source_path`, and items
  have no `paths`.
- docs-wiki PASSES: the Reader path round-trips unchanged. This pins Review Focus
  item 4.
- If a docs-wiki test fails, fix `WorkspaceRagClient.get_document` or
  `list_documents` so that `<collection>/<full path>` round-trips.

- [ ] **Step 3: Implement**

`services/admin-api/src/knowledge_admin/collections.py`:
- in the legacy branch of `document_collections`, replace the `items.setdefault(…)`
  value with
  `{'name': slug(top), 'description': folder.get('description', ''), 'paths': [str(rag_root / top)]}`;
- add `_with_paths` and route the current branch through it:

```python
def _with_paths(item: dict[str, Any]) -> dict[str, Any]:
    """Configs saved between collections and collection paths hold one container `path` per collection."""
    if 'paths' in item:
        return item
    rest = {key: value for key, value in item.items() if key != 'path'}
    return {**rest, 'paths': [item['path']] if item.get('path') else []}


def document_collections(config: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """(all_collections, [{id?, name, description, paths}]) for current, path-era and folder-era configs."""
    documents = config['documents']
    if 'collections' in documents:
        return bool(documents.get('all_collections')), [_with_paths(item) for item in documents['collections']]
    # … folder-era branch unchanged except for 'paths' as above …
```

`services/admin-api/src/knowledge_admin/agents.py`:
- in `get_paths`, use
  `paths.extend(Path(path) for item in document_collections(config)[1] for path in item['paths'])`;
- in `get_agent_config`, replace the collections query and loop with:

```python
        collections = conn.execute("""SELECT c.id,c.name,c.description,
            ARRAY(SELECT p.source_path FROM ka.rag_collection_paths p WHERE p.collection_id=c.id ORDER BY p.source_path) AS source_paths
            FROM ka.rag_collections c
            LEFT JOIN ka.agent_collections a ON a.collection_id=c.id AND a.agent_id=%s
            WHERE c.project_id=%s AND c.deleted_at IS NULL AND (%s OR a.collection_id IS NOT NULL)
            ORDER BY c.name""", (agent_id, project_id, agent['all_collections'])).fetchall()
        for item in collections:
            item['paths'] = [str(area_root(path)) for path in item.pop('source_paths')]
```

- update the docstring sentence on roots to: "CODE_ROOT and the roots of the areas
  that granted collections' paths live in (RAG_SOURCE_ROOT, ...) must match
  absolute mounts in the agent's container."

`services/admin-api/src/knowledge_admin/tools/files.py`, in `_roots`:

```python
    if config['documents']['enabled']:
        paths.extend(Path(path) for item in document_collections(config)[1] for path in item['paths'])
```

`services/admin-api/src/knowledge_admin/reader_api.py`, in `narrow`:

```python
        kept = [{'name': name, 'description': '', 'paths': []} for name in sorted(chosen)]
```

`services/admin-api/src/knowledge_admin/documentation_updates.py`: the comparison
agent's collection item (around line 299) becomes
`{'id': 'comparison', 'name': 'comparison', 'description': 'Commit comparison and existing documents', 'paths': [str(root)]}`.

`services/admin-api/src/knowledge_admin/tools/documents.py`, the `read_document` docstring `Args`:

```python
    Args:
        collection: The collection name shown before the path, such as "jira".
        path: The document's full path as shown after the collection, such as "rag-data/jira/DEMO-1.md".
```

- [ ] **Step 4: Run the admin-api and docs-wiki suites**

Run:
```bash
cd services/admin-api && uv run --frozen pytest -q
cd ../docs-wiki && uv run --frozen pytest -q
```
Expected: all pass. If an earlier plan recorded a pre-existing failure, confirm that
it also fails on `main` before accepting it. Earlier plans recorded
`test_environments.py::test_migration_reapply_and_populated_rollback_protection`.

- [ ] **Step 5: Commit**

```bash
git add services/admin-api/src/knowledge_admin/collections.py services/admin-api/src/knowledge_admin/agents.py \
  services/admin-api/src/knowledge_admin/tools/files.py services/admin-api/src/knowledge_admin/reader_api.py \
  services/admin-api/src/knowledge_admin/tools/documents.py services/admin-api/src/knowledge_admin/documentation_updates.py \
  services/admin-api/tests services/docs-wiki/tests/test_api.py
git commit -m "feat(agents): collections carry all their paths; documents read by full path

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

# Part D — admin-ui (Tasks 8–10)

### Task 8: admin-ui — Jira export takes a collection name

**Files:**
- Modify: `services/admin-ui/app/lib/rag.ts`: add the path and summary types and
  `getCollectionNames`. These are additive; Task 9 reshapes `RagCollection`.
- Modify: `services/admin-ui/app/components/JiraTicketsView.tsx`
- Test: `services/admin-ui/app/components/__tests__/jira.test.tsx`

**Interfaces:**
- Consumes:
  - from Task 5, `POST /api/jira/tickets/{id}/export` with `{collection,
    connection_revision}` → `JiraExportResult`, where `path` is now
    `rag-data/jira/<KEY>.md`;
  - `GET /api/rag/collections?status=false` (Task 3 summaries through the proxy).
- Produces, added to `lib/rag.ts` and used by Tasks 9 and 10:

```ts
export type OwnerKind = "documents" | "jira" | "code_project";
export interface CollectionPath {
  id: string; collection_id: string; source_path: string; owner_kind: OwnerKind | null; owner_id: string | null;
  owner_key: string | null; created_at: string; files: number; host_path: string | null; container_path: string; present: boolean;
}
export interface CollectionSettings {
  description: string; search_mode: SearchMode; embeddings_enabled: boolean;
  include: string[]; exclude: string[]; chunk_size: number; chunk_overlap: number;
}
export interface CollectionSummary extends CollectionSettings {
  id: string; name: string; revision: number; synced_revision: number | null; last_synced_at: string | null;
  paths: CollectionPath[]; folder_present: boolean; missing_paths: string[];
  indexed_files: number; chunks: number; embedded_chunks: number;
}
export const getCollectionNames = (signal?: AbortSignal) => ragRequest<{ collections: CollectionSummary[] }>("collections?status=false", "GET", undefined, signal);
```

- Produces, in the view:
  - the target collection is a free text input (`#jira-collection`) with a
    datalist of existing names. It defaults to `jira`, and an invalid slug disables
    Export;
  - the summary links to `/rag/collections?name=<collection>`.

- [ ] **Step 1: Update the tests first**

In `services/admin-ui/app/components/__tests__/jira.test.tsx`:
- replace every `url.endsWith("/api/rag/collections")` with
  `url.startsWith("/api/rag/collections")`;
- in "export sends only selected IDs and destination, then links to RAG processing":
  - replace the `target` lines with:
    ```tsx
      const target = await screen.findByLabelText("Target collection");
      expect(target).toHaveValue("jira");
      expect(document.querySelectorAll("#jira-collection-names option")).toHaveLength(2);
    ```
  - change the link assertion to
    `expect(screen.getByRole("link", { name: "jira" })).toHaveAttribute("href", "/rag/collections?name=jira");`.
- append:

```tsx
test("a new collection name is sent and invalid names disable export", async () => {
  const fetch = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith("/export")
    ? { issue_key: "DEMO-1", collection: "support", path: "rag-data/jira/DEMO-1.md", status: "created", hash: "hash", comments: 0 }
    : url.startsWith("/api/rag/collections") ? { collections } : twoTickets)));
  vi.stubGlobal("fetch", fetch);
  render(<MemoryRouter><JiraTicketsView /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select DEMO-1" }));
  const target = await screen.findByLabelText("Target collection");
  fireEvent.change(target, { target: { value: "Support Tickets" } });
  expect(screen.getByText("Use lowercase letters, digits and single hyphens.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Export selected (1)" })).toBeDisabled();
  fireEvent.change(target, { target: { value: "support" } });
  fireEvent.click(screen.getByRole("button", { name: "Export selected (1)" }));
  expect(await screen.findByText("Export complete")).toBeInTheDocument();
  const call = fetch.mock.calls.find(([url]) => url.endsWith("/export")) as unknown as [string, RequestInit];
  expect(JSON.parse(call[1].body as string)).toEqual({ collection: "support", connection_revision: 2 });
  expect(screen.getByRole("link", { name: "support" })).toHaveAttribute("href", "/rag/collections?name=support");
});
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd services/admin-ui && pnpm test -- jira`
Expected: FAIL. The target is still a Select, and there is no
`#jira-collection-names` datalist.

- [ ] **Step 3: Implement**

Add the `lib/rag.ts` declarations above under `SkippedCounts`. In `JiraTicketsView.tsx`:

- Change the import to `import { getCollectionNames } from "../lib/rag";`.
- Add `const COLLECTION_NAME = /^[a-z0-9]+(-[a-z0-9]+)*$/;` under `DEFAULT_JQL`.
- Replace the state and the loading effect:
  - `const [collection, setCollection] = useState("jira");`
  - replace `const [writable, setWritable] = useState<string[]>([]);` with
    `const [known, setKnown] = useState<string[]>([]);`;
  - the effect calls
    `getCollectionNames(controller.signal).then(({ collections }) => { if (!controller.signal.aborted) setKnown((collections ?? []).map((row) => row.name)); }).catch(() => { if (!controller.signal.aborted) setKnown([]); });`.
- Add `const validCollection = COLLECTION_NAME.test(collection) && collection.length <= 60;`
  next to `busy`.
- Replace the Target collection field:

```tsx
          <FieldGroup className="max-w-md flex-1"><Field><FieldLabel htmlFor="jira-collection">Target collection</FieldLabel>
            <Input id="jira-collection" list="jira-collection-names" maxLength={60} value={collection} disabled={busy} aria-invalid={!validCollection}
              onChange={(event) => setCollection(event.target.value.trim())} />
            <datalist id="jira-collection-names">{known.map((name) => <option key={name} value={name} />)}</datalist>
            {!validCollection && <FieldDescription className="text-destructive">Use lowercase letters, digits and single hyphens.</FieldDescription>}
          </Field></FieldGroup>
```

- The Export button is disabled when
  `busy || selectedTickets.length === 0 || !validCollection`.
- The form's `FieldDescription` reads: "Each selected ticket becomes
  rag-data/jira/<KEY>.md, a Markdown file with its description and all comments
  visible to the Jira user. Exporting adds the rag-data/jira folder to the chosen
  collection, which is created if it does not exist; choosing another collection
  later moves the folder there. Re-exporting refreshes that ticket's file. Then
  refresh the collection to update search."
- In the summary, replace the last paragraph with:

```tsx
          {exportSummary.results.length > 0 && <p>Refresh <Link className="underline underline-offset-4" to={`/rag/collections?name=${encodeURIComponent(exportSummary.collection)}`}>{exportSummary.collection}</Link> to update search.</p>}
```

- Keep the `Select` imports. The project and status filters still use them.

- [ ] **Step 4: Run the admin-ui suite and the typecheck**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/rag.ts services/admin-ui/app/components/JiraTicketsView.tsx services/admin-ui/app/components/__tests__/jira.test.tsx
git commit -m "feat(admin-ui): Jira export targets any collection by name

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: admin-ui — Collections list, detail and settings-only form

**Files:**
- Modify: `services/admin-ui/app/lib/rag.ts`:
  - `RagCollection` extends Task 8's `CollectionSummary`;
  - `CollectionFile.source`;
  - add `OWNER_LABEL`, `ownerLabel`, `saveCollectionSettings` and `detachPath`;
  - `getCollectionFiles` returns `missing_paths`;
  - remove `CollectionInput`, `DEFAULT_COLLECTION`, `saveCollection`,
    `getRagFile` and `putRagFile`.
- Modify: `services/admin-ui/app/components/RagCollectionsView.tsx`:
  - paths with sources;
  - no New collection;
  - export `PathSource` and `useProjectNames`.
- Modify: `services/admin-ui/app/components/RagCollectionDetail.tsx`:
  - a paths table with Detach for unowned paths;
  - read-only files;
  - Remove only when no path is owned.
- Modify: `services/admin-ui/app/components/RagCollectionForm.tsx`: search settings only.
- Test: rewrite `services/admin-ui/app/components/__tests__/ragCollections.test.tsx`.
- No change: `SearchAgentView.tsx`. Its `documentHref` takes the collection from
  `<collection>/<full path>` (`source.path.split("/")[0]`).
- No change: `RagSearchView.tsx`. It uses only collection names, and its hits now
  show full paths.

**Interfaces:**
- Consumes: the Task 3 API through the Task 5 proxy:
  - `GET collections` and `GET collections?status=false`;
  - `GET collections/{id}`, `GET collections/{id}/files` and
    `GET collections/{id}/chunks`;
  - `PUT collections/{id}` with settings;
  - `DELETE collections/{id}`;
  - `DELETE collections/by-name/{name}/paths?source_path=`.
- Consumes: `getCodeProjects()` from `lib/codeProjects.ts`, to show code project names.
- Consumes, from Task 8: `OwnerKind`, `CollectionPath`, `CollectionSettings`,
  `CollectionSummary` and `getCollectionNames`. They appear again below so the
  final shape of `lib/rag.ts` is in one place.
- Produces, in `lib/rag.ts` (final state):

```ts
export type OwnerKind = "documents" | "jira" | "code_project";
export interface CollectionPath {
  id: string; collection_id: string; source_path: string; owner_kind: OwnerKind | null; owner_id: string | null;
  owner_key: string | null; created_at: string; files: number; host_path: string | null; container_path: string; present: boolean;
}
export interface CollectionSettings {
  description: string; search_mode: SearchMode; embeddings_enabled: boolean;
  include: string[]; exclude: string[]; chunk_size: number; chunk_overlap: number;
}
export interface CollectionSummary extends CollectionSettings {
  id: string; name: string; revision: number; synced_revision: number | null; last_synced_at: string | null;
  paths: CollectionPath[]; folder_present: boolean; missing_paths: string[];
  indexed_files: number; chunks: number; embedded_chunks: number;
}
export interface RagCollection extends CollectionSummary {
  folder_changed_at: string | null; file_count: number; truncated: boolean; skipped: SkippedCounts;
  needs_refresh: boolean; status: CollectionState;
  job: { id: string; status: string; error: string | null; created_at: string; finished_at: string | null } | null;
}
export interface CollectionFile {
  path: string; source: string | null; size: number | null; mtime: number | null; indexed_at: string | null;
  content_hash: string | null; chunks: number; embedded_chunks: number; stale: boolean;
}
export const OWNER_LABEL: Record<OwnerKind, string> = { documents: "Documents", jira: "Jira", code_project: "Code project" };
export const ownerLabel = (path: CollectionPath) => path.owner_kind ? OWNER_LABEL[path.owner_kind] : "Unowned";
export const getCollections = (signal?: AbortSignal) => ragRequest<{ collections: RagCollection[] }>("collections", "GET", undefined, signal);
export const getCollectionNames = (signal?: AbortSignal) => ragRequest<{ collections: CollectionSummary[] }>("collections?status=false", "GET", undefined, signal);
export const getCollection = (id: string, signal?: AbortSignal) => ragRequest<RagCollection>(`collections/${id}`, "GET", undefined, signal);
export const saveCollectionSettings = (id: string, value: CollectionSettings & { revision: number }) => ragRequest<RagCollection>(`collections/${id}`, "PUT", value);
export const deleteCollection = (id: string, revision: number) => ragRequest<{ deleted_files: number }>(`collections/${id}?revision=${revision}&confirmed=true`, "DELETE");
export const detachPath = (collection: string, sourcePath: string) =>
  ragRequest<{ removed: number }>(`collections/by-name/${encodeURIComponent(collection)}/paths?source_path=${encodeURIComponent(sourcePath)}`, "DELETE");
export const syncCollection = (id: string, force = false) => ragRequest<{ id: string }>(`collections/${id}/sync`, "POST", { force });
export const syncStale = () => ragRequest<{ queued: string[] }>("collections/sync-stale", "POST");
export const getCollectionFiles = (id: string, signal?: AbortSignal) => ragRequest<{ files: CollectionFile[]; truncated: boolean; skipped: SkippedCounts; missing_paths: string[] }>(`collections/${id}/files`, "GET", undefined, signal);
export const getChunks = (id: string, path: string) => ragRequest<{ chunks: { position: number; text: string; embedded: boolean }[] }>(`collections/${id}/chunks?path=${encodeURIComponent(path)}`);
```

`RagSettings`, `RagArea`, `RagConfig`, `RagJob`, `RagHit`, `RagResults`, `ragRequest`,
`SearchMode`, `CollectionState` and `SkippedCounts` stay as they are.

- [ ] **Step 1: Rewrite the component tests**

Replace `services/admin-ui/app/components/__tests__/ragCollections.test.tsx`:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import RagCollectionsView from "../RagCollectionsView";
import RagCollectionDetail from "../RagCollectionDetail";
import type { CollectionPath, RagCollection } from "../../lib/rag";

const jiraPath: CollectionPath = {
  id: "p1", collection_id: "c1", source_path: "rag-data/jira", owner_kind: "jira", owner_id: null, owner_key: null,
  created_at: "2026-10-05T09:00:00Z", files: 75, host_path: "/opt/ka/rag-data/jira", container_path: "/work/rag-data/jira", present: true,
};
const codePath: CollectionPath = { ...jiraPath, id: "p2", source_path: "code/payments", owner_kind: "code_project", owner_id: "proj-1",
  owner_key: "payments", files: 12, host_path: "/opt/ka/code/payments", container_path: "/work/code/payments" };
const notesPath: CollectionPath = { ...jiraPath, id: "p3", source_path: "rag-data/notes", owner_kind: null, files: 3,
  host_path: "/opt/ka/rag-data/notes", container_path: "/work/rag-data/notes" };
const base: RagCollection = {
  id: "c1", name: "jira", description: "Jira tickets", search_mode: "full_text", embeddings_enabled: true, include: ["**/*.md"],
  exclude: [], chunk_size: 2000, chunk_overlap: 200, revision: 2, synced_revision: 2, last_synced_at: "2026-10-05T09:00:00Z",
  paths: [codePath, jiraPath, notesPath], folder_present: true, missing_paths: [], folder_changed_at: "2026-10-05T10:00:00Z",
  file_count: 90, truncated: false, skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 },
  indexed_files: 90, chunks: 975, embedded_chunks: 0, needs_refresh: true, status: "needs_refresh", job: null,
};
const unowned: RagCollection = { ...base, id: "c2", name: "notes", paths: [notesPath] };
const projects = { projects: [{ id: "proj-1", name: "Payments API" }] };
const noFiles = { files: [], truncated: false, skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 }, missing_paths: [] };

test("lists collections with their paths and sources, without New collection", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (init?.method === "POST") return Response.json({ id: "job" }, { status: 202 });
    return Response.json({ collections: [base, { ...unowned, status: "up_to_date", needs_refresh: false }] });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  const row = (await screen.findByRole("link", { name: "jira" })).closest("tr")!;
  expect(within(row).getByText("/opt/ka/rag-data/jira")).toBeInTheDocument();
  expect(within(row).getByText("Jira")).toBeInTheDocument();
  expect(await within(row).findByRole("link", { name: "Code project Payments API" })).toHaveAttribute("href", "/code-projects");
  expect(within(row).getAllByText("Unowned")).toHaveLength(1);
  expect(within(row).getByText("75 files")).toBeInTheDocument();
  expect(within(row).getByText("0 / 975")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New collection" })).toBeNull();
  const notes = screen.getByRole("link", { name: "notes" }).closest("tr")!;
  expect(within(notes).queryByRole("button", { name: "Refresh" })).toBeNull();
  await userEvent.click(within(row).getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c1/sync", expect.objectContaining({ method: "POST" })));
});

test("missing paths are named and failed syncs show the error", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/api/code-projects" ? Response.json(projects) : Response.json({ collections: [
    { ...base, status: "folder_missing", folder_present: false, missing_paths: ["rag-data/jira"], paths: [{ ...jiraPath, present: false }] },
    { ...unowned, status: "failed", job: { id: "j", status: "failed", error: "Select an enabled embedding model", created_at: "", finished_at: "" } }] })));
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  expect(await screen.findByText("Folder missing")).toBeInTheDocument();
  expect(screen.getByText("Missing: rag-data/jira")).toBeInTheDocument();
  expect(screen.getByText(/Select an enabled embedding model/)).toBeInTheDocument();
});

test("detail lists full paths and chunks, with no upload or file editor", async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (url.includes("/chunks")) return Response.json({ chunks: [{ position: 0, text: "First chunk text", embedded: false }] });
    if (url.endsWith("/files")) return Response.json({ ...noFiles, files: [{ path: "rag-data/jira/DEMO-1.md", source: "rag-data/jira", size: 10, mtime: 1,
      indexed_at: "2026-10-05T09:00:00Z", content_hash: "abc", chunks: 13, embedded_chunks: 0, stale: false }] });
    return Response.json(base);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  const file = await screen.findByRole("button", { name: "rag-data/jira/DEMO-1.md" });
  expect(within(file.closest("tr")!).getByText("13")).toBeInTheDocument();
  await user.click(file);
  expect(await screen.findByText("First chunk text")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/rag/collections/c1/chunks?path=rag-data%2Fjira%2FDEMO-1.md"), expect.anything());
  expect(screen.queryByLabelText("Upload file")).toBeNull();
  expect(screen.queryByLabelText("File path")).toBeNull();
});

test("Edit changes search settings only", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (url.endsWith("/files")) return Response.json(noFiles);
    return Response.json(init?.method === "PUT" ? { ...base, revision: 3 } : base);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  expect(screen.queryByLabelText("Name")).toBeNull();
  expect(screen.queryByLabelText("Folder")).toBeNull();
  await user.clear(screen.getByLabelText("Include patterns"));
  await user.type(screen.getByLabelText("Include patterns"), "**/*.py");
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit)?.method === "PUT")).toBe(true));
  const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit)?.method === "PUT")!;
  expect(put[0]).toBe("/api/rag/collections/c1");
  expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ description: "Jira tickets", search_mode: "full_text",
    embeddings_enabled: true, include: ["**/*.py"], exclude: [], chunk_size: 2000, chunk_overlap: 200, revision: 2 });
});

test("Remove appears only when no source owns a path", async () => {
  let current = base;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (url.endsWith("/files")) return Response.json(noFiles);
    if (init?.method === "DELETE") return Response.json({ deleted_files: 3 });
    return Response.json(current);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  const view = render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  await screen.findByRole("button", { name: "Edit" });
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  expect(screen.getByText(/sources own paths in this collection/)).toBeInTheDocument();
  view.unmount();
  current = unowned;
  render(<MemoryRouter initialEntries={["/rag/collections/c2"]}><Routes>
    <Route path="/rag/collections/:id" element={<RagCollectionDetail id="c2" />} />
    <Route path="/rag/collections" element={<div>collections page</div>} /></Routes></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "Remove" }));
  await user.click(screen.getByRole("button", { name: "Remove collection" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c2?revision=2&confirmed=true", expect.objectContaining({ method: "DELETE" })));
  expect(await screen.findByText("collections page")).toBeInTheDocument();
});

test("an unowned path can be detached; owned paths cannot", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (url.endsWith("/files")) return Response.json(noFiles);
    if (init?.method === "DELETE") return Response.json({ removed: 1 });
    return Response.json(base);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "Detach rag-data/notes" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/by-name/jira/paths?source_path=rag-data%2Fnotes",
    expect.objectContaining({ method: "DELETE" })));
  expect(await screen.findByText("Path detached. Its files were removed from search.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Detach rag-data/jira" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Detach code/payments" })).toBeNull();
});

test("?name= opens the matching collection", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ collections: [base] })));
  render(<MemoryRouter initialEntries={["/rag/collections?name=jira"]}><Routes>
    <Route path="/rag/collections" element={<RagCollectionsView />} /><Route path="/rag/collections/:id" element={<div>detail page</div>} /></Routes></MemoryRouter>);
  expect(await screen.findByText("detail page")).toBeInTheDocument();
});

test("a revision conflict reloads the collection so the next edit uses the new revision", async () => {
  let revision = 2;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/code-projects") return Response.json(projects);
    if (init?.method === "PUT") return Response.json({ error: { message: "Collection changed; refresh before saving" } }, { status: 409 });
    if (url.endsWith("/files")) return Response.json(noFiles);
    const row = { ...base, revision }; revision = 5; return Response.json(row);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionDetail id="c1" /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  expect(await screen.findByText("Collection changed; refresh before saving")).toBeInTheDocument();
  await waitFor(() => expect(fetchMock.mock.calls.filter(([u, i]) => String(u).endsWith("/collections/c1") && !(i as RequestInit)?.method?.match(/PUT/)).length).toBeGreaterThan(1));
  await user.keyboard("{Escape}");
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "PUT")).toHaveLength(2));
  const puts = fetchMock.mock.calls.filter(([, i]) => (i as RequestInit)?.method === "PUT");
  expect(JSON.parse((puts[1][1] as RequestInit).body as string).revision).toBe(5);
});
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd services/admin-ui && pnpm test -- ragCollections`
Expected: FAIL. "New collection" is still shown, and there are no path rows,
Detach buttons or code project names.

- [ ] **Step 3: Implement**

1. Apply the `lib/rag.ts` changes listed under **Interfaces**.

2. Replace `services/admin-ui/app/components/RagCollectionForm.tsx`:

```tsx
import { useState } from "react";
import { ApiError } from "../lib/api";
import { saveCollectionSettings, type RagCollection, type SearchMode } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean);

export default function RagCollectionForm({ open, collection, onOpenChange, onSaved, onFailed }: {
  open: boolean; collection: RagCollection; onOpenChange: (open: boolean) => void; onSaved: () => void; onFailed?: () => void;
}) {
  return <Sheet open={open} onOpenChange={onOpenChange}><SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-xl">
    <SheetHeader><SheetTitle>Edit {collection.name}</SheetTitle>
      <SheetDescription>Search settings for this collection. Its paths are added and removed by their sources.</SheetDescription></SheetHeader>
    {open && <SettingsFields collection={collection} onSaved={onSaved} onFailed={onFailed} />}
  </SheetContent></Sheet>;
}

function SettingsFields({ collection, onSaved, onFailed }: { collection: RagCollection; onSaved: () => void; onFailed?: () => void }) {
  const [description, setDescription] = useState(collection.description);
  const [searchMode, setSearchMode] = useState<SearchMode>(collection.search_mode);
  const [embeddings, setEmbeddings] = useState(collection.embeddings_enabled);
  const [include, setInclude] = useState(collection.include.join("\n"));
  const [exclude, setExclude] = useState(collection.exclude.join("\n"));
  const [chunkSize, setChunkSize] = useState(collection.chunk_size);
  const [chunkOverlap, setChunkOverlap] = useState(collection.chunk_overlap);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit() {
    setBusy(true); setError("");
    try {
      await saveCollectionSettings(collection.id, {
        description, search_mode: searchMode, embeddings_enabled: embeddings, include: lines(include), exclude: lines(exclude),
        chunk_size: chunkSize, chunk_overlap: chunkOverlap, revision: collection.revision,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the collection"); setBusy(false);
      if (e instanceof ApiError && e.status === 409) onFailed?.();
    }
  }
  return <form className="flex flex-col gap-5 p-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <FieldGroup>
      <Field><FieldLabel htmlFor="collection-description">Description</FieldLabel><Textarea id="collection-description" value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      <Field><FieldLabel htmlFor="collection-mode">Search mode</FieldLabel>
        <Select value={searchMode} onValueChange={(v) => setSearchMode(v as SearchMode)}><SelectTrigger id="collection-mode"><SelectValue /></SelectTrigger>
          <SelectContent><SelectGroup><SelectItem value="full_text">Keyword</SelectItem><SelectItem value="embedding">Semantic</SelectItem><SelectItem value="hybrid">Hybrid</SelectItem></SelectGroup></SelectContent></Select></Field>
      <Field orientation="horizontal"><Switch id="collection-embeddings" checked={embeddings} onCheckedChange={setEmbeddings} /><FieldLabel htmlFor="collection-embeddings">Embeddings</FieldLabel></Field>
      <Field><FieldLabel htmlFor="collection-include">Include patterns</FieldLabel><Textarea id="collection-include" value={include} onChange={(e) => setInclude(e.target.value)} />
        <FieldDescription>One glob pattern per line, matched against each file's path inside its folder.</FieldDescription></Field>
      <Field><FieldLabel htmlFor="collection-exclude">Exclude patterns</FieldLabel><Textarea id="collection-exclude" value={exclude} onChange={(e) => setExclude(e.target.value)} /></Field>
      <div className="grid gap-5 sm:grid-cols-2">
        <Field><FieldLabel htmlFor="collection-chunk-size">Chunk size</FieldLabel><Input id="collection-chunk-size" type="number" required min={200} max={12000} value={chunkSize} onChange={(e) => setChunkSize(Number(e.target.value))} /></Field>
        <Field><FieldLabel htmlFor="collection-chunk-overlap">Chunk overlap</FieldLabel><Input id="collection-chunk-overlap" type="number" required min={0} max={2000} value={chunkOverlap} onChange={(e) => setChunkOverlap(Number(e.target.value))} /></Field>
      </div>
    </FieldGroup>
    <Button className="self-start" type="submit" disabled={busy}>Save collection</Button>
  </form>;
}
```

3. In `services/admin-ui/app/components/RagCollectionsView.tsx`:

- Remove: the `Plus` import, `creating` state, the "New collection" button, and
  the `<RagCollectionForm …/>` element and its import.
- Imports become:

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { RefreshCw } from "lucide-react";
import { getCodeProjects } from "../lib/codeProjects";
import { getCollections, ownerLabel, syncCollection, syncStale, type CollectionPath, type CollectionState, type RagCollection } from "../lib/rag";
```

- Add these exports below `when`:

```tsx
/** Code project names by id, for path sources; errors leave the names empty. */
export function useProjectNames() {
  const [names, setNames] = useState<Record<string, string>>({});
  useEffect(() => {
    const controller = new AbortController();
    getCodeProjects(controller.signal)
      .then(({ projects }) => { if (!controller.signal.aborted) setNames(Object.fromEntries(projects.map((p) => [p.id, p.name]))); })
      .catch(() => {});
    return () => controller.abort();
  }, []);
  return names;
}

export function PathSource({ path, projects }: { path: CollectionPath; projects: Record<string, string> }) {
  if (path.owner_kind === "code_project") {
    const name = (path.owner_id ? projects[path.owner_id] : undefined) ?? path.owner_key ?? "";
    return <Link className="underline underline-offset-4" to="/code-projects">Code project {name}</Link>;
  }
  return <span>{ownerLabel(path)}</span>;
}
```

- In the component, add `const projects = useProjectNames();`.
- The intro paragraph becomes: "Sources decide what is indexed. Docs → Documents,
  Jira exports and searchable code projects add their folders to a collection.
  Refresh re-reads changed files."
- The table header becomes Collection, Paths, Files, Embedded, Last synced,
  Status and Actions. The "Folder" and "Folder changed" columns go.
- The Paths cell:

```tsx
        <TableCell className="align-top"><ul className="flex flex-col gap-1">
          {c.paths.map((p) => <li key={p.id} className="flex flex-wrap items-center gap-2 text-xs">
            <code className="break-all">{p.host_path ?? p.source_path}</code>
            <Badge variant="outline"><PathSource path={p} projects={projects} /></Badge>
            <span className="text-muted-foreground">{p.files} files</span>
            {!p.present && <Badge variant="destructive">Missing</Badge>}
          </li>)}
          {c.paths.length === 0 && <li className="text-xs text-muted-foreground">No paths yet</li>}
        </ul></TableCell>
```

- In the Status cell, after the failed-job error, add
  `{c.status === "folder_missing" && <p className="mt-1 text-xs text-destructive">Missing: {c.missing_paths.join(", ")}</p>}`.
- The empty row (`colSpan={7}`) reads: "No collections yet. Docs → Documents, Jira
  export and searchable code projects add their folders here."

4. Replace `services/admin-ui/app/components/RagCollectionDetail.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { deleteCollection, detachPath, getChunks, getCollection, getCollectionFiles, syncCollection, type CollectionFile, type RagCollection, type SkippedCounts } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import RagCollectionForm from "./RagCollectionForm";
import { PathSource, STATUS_LABEL, statusVariant, useProjectNames, when } from "./RagCollectionsView";

type Chunk = { position: number; text: string; embedded: boolean };
interface Files { files: CollectionFile[]; truncated: boolean; skipped: SkippedCounts; missing_paths: string[] }

export default function RagCollectionDetail({ id }: { id: string }) {
  const navigate = useNavigate();
  const projects = useProjectNames();
  const [collection, setCollection] = useState<RagCollection | null>(null);
  const [files, setFiles] = useState<Files | null>(null);
  const [selected, setSelected] = useState("");
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [row, list] = await Promise.all([getCollection(id, signal), getCollectionFiles(id, signal)]);
      if (signal?.aborted) return;
      setCollection(row); setFiles(list); setError("");
    } catch (e) { if (!signal?.aborted) setError(e instanceof Error ? e.message : "Could not load the collection"); }
  }, [id]);
  useEffect(() => { const controller = new AbortController(); load(controller.signal); return () => controller.abort(); }, [load]);
  async function run(action: () => Promise<void>, reload = true) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); if (reload) await load(); } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); } finally { setBusy(false); }
  }
  async function select(path: string) {
    setSelected(path); setChunks([]);
    await run(async () => { setChunks((await getChunks(id, path)).chunks); }, false);
  }
  if (!collection) return <div className="flex flex-col gap-4">{error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-64" />}</div>;
  const c = collection;
  const owned = c.paths.some((p) => p.owner_kind !== null);
  return <div className="flex flex-col gap-6">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    <section className="flex flex-col gap-3 rounded-lg border p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-lg font-semibold">{c.name}</h2>{c.description && <p className="text-sm text-muted-foreground">{c.description}</p>}</div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>Edit</Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(id); setNotice("Refresh queued."); })}>Refresh</Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(id, true); setNotice("Rebuild queued."); })}>Rebuild</Button>
          {!owned && <Button variant="destructive" size="sm" disabled={busy} onClick={() => setConfirmDelete(true)}>Remove</Button>}
        </div>
      </div>
      {owned && <p className="text-xs text-muted-foreground">Remove is unavailable while sources own paths in this collection. Switch them off at their source first.</p>}
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
        <dt className="text-muted-foreground">Status</dt><dd><Badge variant={statusVariant(c.status)}>{STATUS_LABEL[c.status]}</Badge>
          {c.status === "folder_missing" && <span className="ml-2 text-destructive">Missing: {c.missing_paths.join(", ")}</span>}
          {c.status === "failed" && c.job?.error && <span className="ml-2 text-destructive">{c.job.error}</span>}</dd>
        <dt className="text-muted-foreground">Files indexed</dt><dd>{c.indexed_files} / {c.file_count}</dd>
        <dt className="text-muted-foreground">Embedded chunks</dt><dd>{c.embeddings_enabled ? `${c.embedded_chunks} / ${c.chunks}` : "Off"}</dd>
        <dt className="text-muted-foreground">Folders changed</dt><dd>{when(c.folder_changed_at)}</dd>
        <dt className="text-muted-foreground">Last synced</dt><dd>{when(c.last_synced_at)}</dd>
      </dl>
    </section>
    <section className="flex flex-col gap-3">
      <h3 className="text-base font-semibold">Paths</h3>
      <Table><TableHeader><TableRow><TableHead>Path</TableHead><TableHead>Source</TableHead><TableHead>Indexed files</TableHead><TableHead>Folder</TableHead><TableHead /></TableRow></TableHeader><TableBody>
        {c.paths.map((p) => <TableRow key={p.id}>
          <TableCell><code className="break-all text-xs">{p.source_path}</code>{p.host_path && <p className="break-all text-xs text-muted-foreground">{p.host_path}</p>}</TableCell>
          <TableCell><PathSource path={p} projects={projects} /></TableCell>
          <TableCell>{p.files}</TableCell>
          <TableCell>{p.present ? "Present" : <Badge variant="destructive">Missing</Badge>}</TableCell>
          <TableCell>{p.owner_kind === null && <Button variant="outline" size="sm" disabled={busy} aria-label={`Detach ${p.source_path}`}
            onClick={() => run(async () => { await detachPath(c.name, p.source_path); setNotice("Path detached. Its files were removed from search."); })}>Detach</Button>}</TableCell>
        </TableRow>)}
        {c.paths.length === 0 && <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">No paths. Sources add their folders to this collection.</TableCell></TableRow>}
      </TableBody></Table>
    </section>
    <section className="flex flex-col gap-3">
      <h3 className="text-base font-semibold">Indexed files</h3>
      {files && <p className="text-xs text-muted-foreground">Skipped: {files.skipped.links} links, {files.skipped.special} special, {files.skipped.unreadable} unreadable, {files.skipped.invalid_names} invalid names.{files.truncated && " The list is truncated; only the first files are shown."}</p>}
      <Table><TableHeader><TableRow><TableHead>Path</TableHead><TableHead>Chunks</TableHead><TableHead>Embedded</TableHead><TableHead>Indexed</TableHead><TableHead /></TableRow></TableHeader><TableBody>
        {files?.files.map((f) => <TableRow key={f.path} data-state={f.path === selected ? "selected" : undefined}>
          <TableCell><Button variant="link" className="h-auto p-0 break-all" onClick={() => select(f.path)}>{f.path}</Button></TableCell>
          <TableCell>{f.chunks}</TableCell><TableCell>{f.embedded_chunks} embedded</TableCell><TableCell className="text-xs">{when(f.indexed_at)}</TableCell>
          <TableCell>{f.stale && <Badge variant="outline">Changed</Badge>}</TableCell>
        </TableRow>)}
        {files && files.files.length === 0 && <TableRow><TableCell colSpan={5} className="py-8 text-center text-muted-foreground">No files in these folders match the patterns.</TableCell></TableRow>}
      </TableBody></Table>
    </section>
    {selected && <section className="flex flex-col gap-3"><h3 className="text-base font-semibold">Chunks of {selected}</h3>
      {chunks.map((chunk) => <article key={chunk.position} className="flex flex-col gap-2 rounded-lg border p-3"><div className="flex items-center gap-2 text-xs text-muted-foreground">#{chunk.position}{!chunk.embedded && <Badge variant="outline">Not embedded</Badge>}</div><pre className="whitespace-pre-wrap text-sm">{chunk.text}</pre></article>)}
    </section>}
    <RagCollectionForm open={editing} collection={c} onOpenChange={setEditing} onSaved={() => { setEditing(false); load(); }} onFailed={() => load()} />
    <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Remove collection?</AlertDialogTitle>
      <AlertDialogDescription>Remove {c.name} from search? Files on disk are kept.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction onClick={() => run(async () => { await deleteCollection(id, c.revision); navigate("/rag/collections"); }, false)}>Remove collection</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <Link className="text-sm underline" to="/rag/collections">Back to collections</Link>
  </div>;
}
```

- [ ] **Step 4: Run the admin-ui suite and the typecheck**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test`
Expected: no type errors, and all tests pass. `JiraTicketsView.tsx` no longer reads
`writable` after Task 8, so removing it from `RagCollection` is safe.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/rag.ts services/admin-ui/app/components/RagCollectionsView.tsx \
  services/admin-ui/app/components/RagCollectionDetail.tsx services/admin-ui/app/components/RagCollectionForm.tsx \
  services/admin-ui/app/components/__tests__/ragCollections.test.tsx
git commit -m "feat(admin-ui): collections show their paths and sources; settings-only edit; owner-guarded remove

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: admin-ui — Searchable switch on code projects

**Files:**
- Modify: `services/admin-ui/app/lib/codeProjects.ts`: `CodeProject` fields and
  `setCodeProjectSearch`.
- Modify: `services/admin-ui/app/components/CodeProjectsView.tsx`: a Search column
  with a switch, collection field, Move and Retry.
- Test: `services/admin-ui/app/components/__tests__/codeProjects.test.tsx`

**Interfaces:**
- Consumes: from Task 6, `PUT /api/code-projects/{id}/search` and the
  `searchable`/`collection`/`search_error` list fields; from Task 8,
  `getCollectionNames()`.
- Produces:

```ts
export interface CodeProject { /* …existing fields… */ searchable: boolean; collection: string | null; search_error: string | null }
export interface CodeProjectSearch { searchable: boolean; collection: string | null; search_error: string | null }
export const setCodeProjectSearch = (id: string, enabled: boolean, collection?: string) => request<CodeProjectSearch>(
  `/api/code-projects/${encodeURIComponent(id)}/search`,
  { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled, ...(collection ? { collection } : {}) }) });
export const collectionSlug = (name: string) =>
  name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60).replace(/-+$/, '');
```

- [ ] **Step 1: Write the failing tests**

In `services/admin-ui/app/components/__tests__/codeProjects.test.tsx`:
- add `searchable:false, collection:null, search_error:null` to the `project` fixture;
- in `api()`, add a branch before the directory fallback:
  `else if (url.pathname === '/api/rag/collections') body = { collections: [{ name: 'payments-docs' }] };`.

Then append:

```tsx
const putBody = (fetch: ReturnType<typeof api>, path: string) => {
  const call = fetch.mock.calls.find(([url, init]) => url === path && init?.method === 'PUT');
  return call ? JSON.parse(call[1]!.body as string) : undefined;
};

test('Searchable switch adds the project to the chosen collection', async () => {
  const user = userEvent.setup(); const fetch = api([project]); render(<CodeProjectsView />);
  const field = await screen.findByLabelText('Search collection for Payments');
  expect(field).toHaveValue('payments');
  await user.clear(field); await user.type(field, 'payments-code');
  await user.click(screen.getByRole('switch', { name: 'Searchable Payments' }));
  await waitFor(() => expect(putBody(fetch, '/api/code-projects/code-1/search')).toEqual({ enabled: true, collection: 'payments-code' }));
});

test('turning Searchable off removes the project from search', async () => {
  const user = userEvent.setup();
  const fetch = api([{ ...project, searchable: true, collection: 'payments' }]); render(<CodeProjectsView />);
  await user.click(await screen.findByRole('switch', { name: 'Searchable Payments' }));
  await waitFor(() => expect(putBody(fetch, '/api/code-projects/code-1/search')).toEqual({ enabled: false }));
});

test('a searchable project can move to another collection', async () => {
  const user = userEvent.setup();
  const fetch = api([{ ...project, searchable: true, collection: 'payments' }]); render(<CodeProjectsView />);
  const field = await screen.findByLabelText('Search collection for Payments');
  expect(screen.queryByRole('button', { name: /^Move to/ })).toBeNull();
  await user.clear(field); await user.type(field, 'payments-docs');
  await user.click(screen.getByRole('button', { name: 'Move to payments-docs' }));
  await waitFor(() => expect(putBody(fetch, '/api/code-projects/code-1/search')).toEqual({ enabled: true, collection: 'payments-docs' }));
});

test('a RAG failure shows the error with Retry, which repeats the saved choice', async () => {
  const user = userEvent.setup();
  const fetch = api([{ ...project, searchable: true, collection: 'payments', search_error: 'RAG service is unavailable. Retry later.' }]);
  render(<CodeProjectsView />);
  expect(await screen.findByText('RAG service is unavailable. Retry later.')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Retry search for Payments' }));
  await waitFor(() => expect(putBody(fetch, '/api/code-projects/code-1/search')).toEqual({ enabled: true, collection: 'payments' }));
});
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `cd services/admin-ui && pnpm test -- codeProjects`
Expected: FAIL. There is no "Search collection for Payments" field.

- [ ] **Step 3: Implement**

1. Apply the `lib/codeProjects.ts` additions under **Interfaces**.
2. In `CodeProjectsView.tsx`:
   - imports: add `collectionSlug` and `setCodeProjectSearch` to the
     `../lib/codeProjects` import, and add
     `import { getCollectionNames } from '../lib/rag';`;
   - state and loading, next to the other `useState`s and effects:

```tsx
  const [collectionNames, setCollectionNames] = useState<string[]>([]);
  const [searchDrafts, setSearchDrafts] = useState<Record<string, string>>({});
  useEffect(() => {
    const controller = new AbortController();
    getCollectionNames(controller.signal)
      .then(value => { if (!controller.signal.aborted) setCollectionNames((value.collections ?? []).map(row => row.name)); })
      .catch(() => {});
    return () => controller.abort();
  }, [refresh]);
  const searchDraft = (project: CodeProject) => searchDrafts[project.id] ?? project.collection ?? collectionSlug(project.name);
  async function setSearch(project: CodeProject, enabled: boolean, collection: string | null) {
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await setCodeProjectSearch(project.id, enabled, collection || undefined);
      if (!result.search_error) setNotice(result.searchable
        ? `${project.name} is searchable in ${result.collection}. Refresh that collection in RAG to update search.`
        : `${project.name} was removed from search.`);
      setSearchDrafts(drafts => { const next = { ...drafts }; delete next[project.id]; return next; });
      setRefresh(n => n + 1);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  }
```

   - in the projects table header, add `<TableHead>Search</TableHead>` before
     `<TableHead>Actions</TableHead>`. Add a cell in the same position:

```tsx
        <TableCell className="max-w-xs whitespace-normal"><div className="flex flex-col items-start gap-2">
          <div className="flex items-center gap-2"><Switch aria-label={`Searchable ${project.name}`} checked={project.searchable} disabled={busy}
            onCheckedChange={checked => void setSearch(project, checked, checked ? searchDraft(project) : null)} />
            <span className="text-sm">{project.searchable ? 'Searchable' : 'Not searchable'}</span></div>
          <Input aria-label={`Search collection for ${project.name}`} list="code-search-collections" className="h-8" maxLength={60} disabled={busy}
            value={searchDraft(project)} onChange={e => setSearchDrafts(drafts => ({ ...drafts, [project.id]: e.target.value.trim() }))} />
          {project.searchable && searchDraft(project) !== project.collection && <Button size="sm" variant="outline" disabled={busy}
            onClick={() => void setSearch(project, true, searchDraft(project))}>Move to {searchDraft(project)}</Button>}
          {project.searchable && project.collection && <a className="text-xs underline" href={`/rag/collections?name=${encodeURIComponent(project.collection)}`}>Open collection {project.collection}</a>}
          {project.search_error && <><span className="text-sm text-destructive">{project.search_error}</span>
            <Button size="sm" variant="outline" aria-label={`Retry search for ${project.name}`} disabled={busy}
              onClick={() => void setSearch(project, project.searchable, project.collection)}>Retry</Button></>}
        </div></TableCell>
```

   - render `<datalist id="code-search-collections">{collectionNames.map(name => <option key={name} value={name} />)}</datalist>`
     once, right after the projects `<Table>`.
   - The Card description gains: "Turn on Searchable to add a project's folders to a
     RAG collection; agents and Reader users then find its code."
   - Use a plain `<a>` for the collection link. The tests render `CodeProjectsView`
     without a router.

- [ ] **Step 4: Run the admin-ui suite and the typecheck**

Run: `cd services/admin-ui && pnpm exec tsc --noEmit && pnpm test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/admin-ui/app/lib/codeProjects.ts services/admin-ui/app/components/CodeProjectsView.tsx \
  services/admin-ui/app/components/__tests__/codeProjects.test.tsx
git commit -m "feat(admin-ui): Searchable switch and collection for code projects

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

# Part E — documentation and verification (Task 11)

### Task 11: Docs and full verification

**Files:**
- Modify: `docs/rag.md`
- Modify: `docs/user-guide.md` (sections 5.3, 5.4, 5.8 and F)
- Modify: `docs/jira.md` ("Markdown export and RAG processing")

**Interfaces:**
- Consumes: the behaviour delivered by Tasks 1–10. The documentation repeats the
  Global Constraints' values and error texts exactly.

- [ ] **Step 1: Write the docs**

`docs/rag.md`:
- Intro and the RAG pages list:
  - a collection is a named search index with its own settings and one or more
    paths (folders in `DATA_ROOT`);
  - sources add and remove their paths;
  - Collections shows paths and sources, Edit changes search settings, Refresh,
    and Detach for unowned paths;
  - there is no New collection and no upload or edit.
- `## Collections`:
  - the field table loses "Folder" and gains "Paths";
  - state that the name cannot be changed;
  - replace "Several collections may point at the same folder" with the path rules:
    no overlap within a collection, sharing across collections allowed, include and
    exclude patterns matched relative to each path;
  - examples:
    - `documents`: `generated-documents`, owned by Documents;
    - `jira`: `rag-data/jira`, owned by Jira;
    - `payments`: `code/payments` (Code project) plus `rag-data/jira` (Jira) plus
      `docs/runbooks` (unowned).
- New `## Paths and sources`:
  - the table of sources (Docs → Documents, Jira, code project) with the path each
    adds and its collection;
  - an owner has one path, and adding it again moves it;
  - removing a path removes its indexed files, and files on disk stay;
  - Remove is refused with `Remove this collection's sources first: …` while a
    source owns a path.
- New `### Upgrading to 0065`:
  - each collection's folder becomes its one path;
  - `generated-documents` is owned by Documents, and the oldest live `rag-data/jira`
    collection is owned by Jira;
  - indexed files are rewritten to full paths with no reprocessing, so collections
    stay Up to date;
  - rolling back refuses while any collection has more than one path.
- `## Status, freshness and Refresh`:
  - `folder_changed_at` is the newest change across all paths;
  - the status is Folder missing when any path is missing, and each path shows its
    own presence;
  - adding or removing a path shows Needs refresh;
  - Refresh is refused while a path is missing (`Path <p> is missing…`), and the
    index is kept.
- `## API`:
  - the admin table drops `POST /collections` and `PUT/DELETE /file`;
  - `PUT /collections/{id}` is settings only;
  - add `DELETE /collections/by-name/{name}/paths?source_path=`;
  - add a second table, "Source API (service only, `X-Rag-Key`)", with
    `PUT collections/by-name/{name}`, `PUT|DELETE collections/by-name/{name}/paths`,
    `DELETE paths`, `GET file?collection=&path=<full path>` and
    `PUT file {path, content, expected_hash}` (rag-data only).
- Agents paragraph:
  - `read_document(collection, path)` takes the full path, such as
    `rag-data/jira/DEMO-1.md`;
  - the Reader document path is `<collection>/<full path>`;
  - non-grounded agents read every path of their granted collections.

`docs/user-guide.md`:
- 5.3:
  - replace "New collection" and file upload/edit with the sources workflow and the
    Collections columns (paths with sources, files, embedded chunks, last synced,
    status, Refresh);
  - Edit shows settings only;
  - Remove appears only when no source owns a path;
  - Detach applies to unowned paths.
- 5.4: the **Searchable** switch and collection name:
  - the default is the project's slug, and an existing name can be picked;
  - Move to another collection;
  - folder edits and archiving keep RAG in sync;
  - an error shows with **Retry**;
  - refresh the collection afterwards.
- 5.8 and F:
  - the Jira dialog's Target collection is any valid name, default `jira`;
  - the export adds `rag-data/jira` to that collection (moving it from a previous
    one);
  - the notice links to the collection.

`docs/jira.md` "Markdown export and RAG processing":
- tickets are written to `rag-data/jira/<KEY>.md`;
- the export first adds `rag-data/jira` (owner Jira) to the chosen collection,
  creating it if missing;
- a RAG error, such as an overlap 409 or the service being unavailable, fails the
  export with its message and writes nothing;
- choosing another collection later moves the folder.

- [ ] **Step 2: Run every suite**

Run:
```bash
cd services/rag && uv run --frozen pytest -q
cd ../admin-api && uv run --frozen pytest -q
cd ../docs-wiki && uv run --frozen pytest -q
cd ../admin-ui && pnpm exec tsc --noEmit && pnpm test
cd ../.. && python3 -m unittest discover -s tests && bash db/tests/run.sh
```
Expected: everything passes. For any failure, check out `main` in a separate
worktree and run the same test. Accept it only if it also fails there, and record it
in the PR description.

- [ ] **Step 3: Check for leftovers**

Run: `git grep -n "source_path" -- services/admin-ui services/admin-api/src services/docs-wiki`
Expected:
- admin-ui: only `CollectionPath.source_path` and its uses;
- admin-api: only `area_root(source_path)` in `collections.py`;
- docs-wiki: no `source_path` at all.

Run: `git grep -n "putRagFile\|getRagFile\|saveCollection(\|DEFAULT_COLLECTION\|delete_file\|/collections\", json={\"name\"" -- services`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add docs/rag.md docs/user-guide.md docs/jira.md
git commit -m "docs: collections with source paths

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
