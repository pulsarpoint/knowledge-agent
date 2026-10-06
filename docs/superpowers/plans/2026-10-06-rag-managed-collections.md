# RAG-Managed Collections, File Log and Polling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RAG owns the configuration. An administrator, or an external tool through
the RAG API, creates collections and connects folders under `DATA_ROOT` to them.
Sources (Jira export, Docs → Documents, Git pulls of code projects, anything outside
the application) only write files into their folders. RAG keeps a per-file log
(path → size, mtime, hash, status), so unchanged files are never opened again and
every skipped or failed file is visible. Collections with auto-sync on are polled
and synced once their folders have been quiet for two minutes.

**Architecture:**
- **The RAG service** gains `POST collections`, `POST collections/{id}/paths`,
  `DELETE collections/{id}/paths/{path_id}` and `GET folders` (a folder browser).
  The `by-name` source API, `DELETE paths?owner…` and creation-only settings are
  removed. `GET file` without `collection` reads a `rag-data` file by full path,
  which is how the Jira export finds its previous ticket file.
- **admin-api** stops talking to RAG on behalf of sources:
  - `code_project_search.py` and `rag_collections.py` are deleted;
  - the Searchable route, archive releasing, worker reconciliation and
    Documents' startup registration are removed;
  - the Jira export always writes `rag-data/jira/<KEY>.md` and takes no
    collection;
  - the RAG proxy allows the new routes.
- **Migration 0066** drops the owner columns and index from
  `ka.rag_collection_paths` and `search_collection` and `search_error` from
  `ka.repositories`. It adds `ka.rag_file_log` (backfilled from `rag_files`) and
  `auto_sync`, `poll_interval_minutes` and `last_polled_at` on
  `ka.rag_collections`.
- **File log (`rag/file_log.py`)**:
  - the sync worker decides per file from the log: not opened, hashed but not
    parsed, processed, or `skipped_*` / `failed`;
  - one bad file is logged and the sync continues;
  - the log is written in the publishing transaction, so a failed sync leaves it
    unchanged;
  - `GET collections/{id}/log` pages it.
- **Polling (`rag/workspace_poll.py`)**: when no sync is queued, the RAG worker
  claims due collections with `UPDATE … FOR UPDATE SKIP LOCKED`. It walks them
  (size and mtime only) and compares the walk with the log. It queues
  `{"auto": true}` once the newest change is at least 2 minutes old.
- **admin-ui**:
  - Collections gets **New collection**;
  - the detail page gets **Add folder** (a folder browser or a typed path),
    **Remove folder**, an always-available **Remove**, the file log table and
    the status counts;
  - the form gains auto-sync settings, and the list shows "Auto · every N min" or
    "Manual";
  - the Jobs page shows mode "Auto";
  - the Searchable switch and the Jira collection field are removed.

**Tech Stack:**
- PostgreSQL 17 with pgvector, golang-migrate SQL migrations;
- FastAPI and psycopg3 (`services/rag`, `services/admin-api`);
- React Router 7 with shadcn/ui, TanStack Table and Vitest (`services/admin-ui`);
- pytest. The RAG and admin-api tests run a real PostgreSQL container from the
  `knowledge-agent-v3-postgres:17.10-vector0.8.6` image.

**Spec:** `docs/superpowers/specs/2026-10-06-rag-managed-collections-design.md`
(binding). It supersedes the ownership parts of
`docs/superpowers/specs/2026-10-06-source-owned-collections-design.md`; the
multi-folder model from that design stays.

## Global Constraints

- Collection `name`: `^[a-z0-9]+(-[a-z0-9]+)*$`, at most 60 characters, unique among non-deleted collections; never editable. A duplicate gives 409 `Collection <name> already exists`.
- Collection `description`: at most 2000 characters.
- Settings per collection: `search_mode` (`full_text` | `embedding` | `hybrid`), `embeddings_enabled`, `include` (1..50 patterns), `exclude` (0..50), `chunk_size` 200..12000, `chunk_overlap` 0..2000 and smaller than `chunk_size`, `auto_sync` (boolean), `poll_interval_minutes` 1..1440.
- Defaults for `POST collections`: search mode `full_text`, embeddings off, include `{**/*.md,**/*.markdown,**/*.txt}`, exclude `{}`, chunk size 2000, overlap 200, `auto_sync` false, `poll_interval_minutes` 5.
- `source_path`: relative to `DATA_ROOT`, at most 500 characters, first segment one of `code`, `code-index`, `rag-data`, `generated-documents`, `docs`, each later segment `[A-Za-z0-9_-][A-Za-z0-9._-]*`. No leading or trailing `/`.
- Within one collection, folders may not overlap (`a == b`, or one starts with the other plus `/`). Different collections may share folders.
- Adding a folder: a missing folder under `rag-data` is created (mode 0750, no links followed); a missing folder elsewhere gives 409 `Folder does not exist`. Overlap gives 409 `Folder overlaps <other> in collection <name>`. An invalid path gives 400.
- Removing a folder that is not in the collection gives 404 `Unknown folder`.
- Adding or removing a folder bumps the collection `revision`. A settings save that changes only `auto_sync` and `poll_interval_minutes` does not.
- Removing a collection removes its index, folders, file log and agent grants; files stay on disk. It is always allowed (no owner guard).
- `GET folders?path=` lists visible child folders whose names match `[A-Za-z0-9_-][A-Za-z0-9._-]*` (never links, hidden folders, `.git` or `node_modules`), sorted, at most 1000 (`truncated: true` beyond); without `path` it lists the mounted areas; a missing folder gives 404 `Folder does not exist`, a bad path 400.
- File log `status`: `indexed`, `skipped_binary`, `skipped_too_large`, `skipped_unreadable`, `failed` or `removed`. `error` holds at most 2000 characters.
- File log fingerprint: sha256 of `{processing, profile, include, exclude, max_file_bytes}`. `processing` is today's `processing_hash`, and `profile` the embedding profile (`""` when embeddings are off).
- Log error texts: parse failure `Could not parse this file: <message>`; unreadable file starts `Cannot read this file`.
- `GET collections/{id}/log`: `status` ∈ `indexed` | `skipped` | `failed` | `removed`. The default is every status except `removed`. `q` (≤ 200 characters) matches the path case-insensitively and literally. `limit` 1..100 (default 25), `offset` ≥ 0. The response is `{files, total}`, ordered by path.
- Summary counts `log_counts`: `{indexed, skipped, failed}`.
- Polling quiet period: `QUIET_SECONDS = 120`. An automatic job has `requested_by` NULL and input `{"auto": true}`. The Jobs page mode label is `Auto`.
- Limits (unchanged): 20,000 files and 200 MB per sync, and 200,000 entries per sync walk. The poller walks with those caps and truncates instead of failing.
- Jira: always `rag-data/jira/<KEY>.md`; the export body is `{"connection_revision": n}` only (extra keys give 400).
- Error text for a RAG outage in admin-api: `RAG service is unavailable. Retry later.`
- Migration number 0066 (`0066_rag_managed_collections`). `db/tests/run.sh` pins the rolled-back version to `65`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Python stays compatible with 3.12.

## Review Focus

1. **Files whose mtime does not move forward.** Examples: `rsync -a`, `cp -p`,
   unzipped archives and restored backups write new content with an old
   modification time. Sync must still re-read a file whose size or mtime differs
   from its log row, whichever direction it moved. The poller compares the walk
   with the log, not "newest mtime after last sync", so it must notice such copies.
   Pinned in:
   - Task 4: `test_changed_content_is_reindexed_even_with_an_older_mtime`;
   - Task 6: `test_files_copied_with_old_mtimes_are_noticed`.
2. **Settings changes that leave files untouched.** Examples: raising the size
   limit after a file was skipped as too large, chunking or pattern edits, and a
   new embedding model. Size and mtime are unchanged, so only the fingerprint
   forces re-processing. Pinned in Task 4:
   - `test_raising_the_size_limit_reprocesses_skipped_files`;
   - `test_settings_change_reprocesses_unchanged_files`.
3. **One bad file among many.** Examples: a binary `.md`, non-UTF-8 text, a file
   without read permission, or a parser crash. Each is logged with its status and
   error, and the job succeeds. Fixing permissions changes no mtime, so unreadable
   files must be retried. Pinned in Task 4:
   - `test_parse_failure_is_logged_and_the_sync_succeeds`;
   - `test_binary_large_and_unreadable_files_are_logged`.
4. **Folders still being written, and concurrent pollers.** Examples: a Jira
   export of 200 tickets, a Git pull, or a document export. A change less than 2
   minutes old must not queue a sync. A queued or running sync, or a second
   worker polling at the same moment, must not produce a second job. Pinned in
   Task 6:
   - `test_a_change_less_than_two_minutes_old_waits_for_the_quiet_period`;
   - `test_an_active_sync_prevents_a_second_one`;
   - `test_two_pollers_queue_one_sync`.
5. **Folder paths typed by administrators or external tools.** Examples: a
   trailing `/`, `..`, `.git`, a symlinked folder, a folder inside or around an
   existing one, a missing folder outside `rag-data`, and hidden folders in the
   browser. Each must give its exact error and change nothing. Pinned in:
   - Task 1: `test_add_folder_rejects_overlap_missing_and_bad_paths_and_changes_nothing`;
   - Task 1: `test_folder_browser_lists_visible_subfolders_and_rejects_bad_paths`;
   - Task 7: `a typed path is trimmed of slashes`.

## Decisions (where the spec leaves a choice)

- **One migration.** 0066 holds Part 1 (drop owner and search columns), Part 2
  (`ka.rag_file_log`) and Part 3 (polling columns), so an upgrade is one step and
  one rollback.
- **The log backfill.** 0066 inserts one `indexed` log row per `rag_files` row,
  with `size`, `mtime` and `fingerprint` NULL. The first sync after the upgrade
  therefore reads and hashes each file once. Unchanged content still matches
  `rag_files` (content hash, processing hash, profile) and is not parsed or
  embedded again. The log table shows files right after the upgrade.
- **How sync uses the log** (`workspace_worker.decide`):
  - **Not opened:** the walk entry's size and mtime equal the log row, the
    fingerprint equals the current one, and the status is not `removed` or
    `skipped_unreadable`. Unreadable files are always retried, because fixing
    permissions changes no mtime. The log status is carried over.
  - **Read and hashed:**
    - too large, by walk size or on read, gives `skipped_too_large`;
    - an `OSError` or a non-regular-file error on read gives
      `skipped_unreadable` with `Cannot read this file: …`;
    - NUL bytes or non-UTF-8 give `skipped_binary`;
    - the same hash and fingerprint as the log row carry the log status (not
      parsed);
    - otherwise the file goes to today's pipeline. There, an unchanged
      `rag_files` row (content, processing hash, profile) is skipped, the same
      content with a new profile re-embeds stored chunk texts, and anything else
      is parsed.
  - **`parse_source` raising any exception:** the file is logged `failed` with
    `Could not parse this file: <message>`, `failed_files` is counted, and the
    sync continues.
  - **Embedding errors** (`embed` raising, a model that cannot be decrypted or
    selected) stay collection-level and fail the sync, as today. Every embedding
    error is a model or endpoint problem, never a problem of one file's content.
    This resolves the spec's "a parse or embedding error gives failed" against
    "an embedding model that is unavailable fails the sync".
  - **Index rows:** only files whose decision is `indexed` keep `rag_files`
    rows. A file that becomes `skipped_*` or `failed` leaves search, as a binary
    or oversized file does today.
  - **Stats keys:** today's keys plus `unopened_files` (not opened) and
    `failed_files`. `skipped_files` counts every unchanged file, opened or not;
    `hashed_files` counts every file read; `removed_entries` counts deleted
    `rag_files` rows.
  - **Publish re-check (`recheck`):** a second walk under the collection and
    workspace locks is compared with the first walk by path, size and mtime.
    Only files whose size or mtime moved are read again and hashed against the
    hash this sync used, or the logged hash for unopened files. Any difference
    fails the sync with `Files changed while syncing; refresh the collection
    again`.
  - **The log is written in the publishing transaction** (`file_log.record`):
    an upsert per walked file, `removed` for log rows not seen, and `indexed_at`
    copied from `rag_files`. A failed sync therefore leaves the log as it was.
- **Folder and collection removal:**
  - `rag_file_log` has FK `(path_id, collection_id)` →
    `rag_collection_paths(id, collection_id)` `ON DELETE CASCADE`, so log rows go
    with their folder. Collection removal deletes the folders, so its log goes
    too.
  - Vectors no longer used are deleted with the existing `ORPHAN_VECTORS_SQL`,
    under the workspace lock (lock order: collection, then workspace).
- **Polling plugs into the worker loop:**
  - `workspace_worker.step()` claims a queued sync and, when there is none,
    calls `workspace_poll.poll()`. `main()` loops over `step()`.
  - `poll()` claims due collections in one statement. The statement is
    `UPDATE ka.rag_collections SET last_polled_at=now() WHERE id IN (SELECT …
    auto_sync AND (last_polled_at IS NULL OR last_polled_at <= now() -
    make_interval(mins => poll_interval_minutes)) … FOR UPDATE SKIP LOCKED LIMIT
    20) RETURNING *`. A second worker either skips the locked rows or sees the
    new `last_polled_at`, so it never checks a collection twice.
  - For each claimed collection, `check()` returns:
    - `active` when a sync is queued or running;
    - `empty` when it has no folders;
    - `missing` when a folder is absent (not synced; the status stays Folder
      missing);
    - `fresh` when the walk equals the log and the collection was synced at its
      current revision;
    - `changing` when the newest mtime of any walked file or folder is less than
      `QUIET_SECONDS` old;
    - otherwise it inserts the job with `ON CONFLICT DO NOTHING` (the existing
      partial unique index `rag_collection_one_active_job` makes a concurrent
      manual refresh win) and returns `queued`.
  - A truncated walk queues, so the sync reports the limit.
  - A collection whose last sync failed is retried every interval, until it
    succeeds or auto-sync is switched off.
  - Workspace-level changes (size limit, embedding model) are not detected by
    the poller, as they are not by today's status; Refresh covers them.
- **Poll settings and the revision:** a save that changes only `auto_sync` or
  `poll_interval_minutes` does not bump the revision. Switching auto-sync on
  therefore does not show Needs refresh or fail a running sync. Any other change
  bumps it as today.
- **Response shapes:**
  - `POST collections` answers 201 with the collection summary;
  - `POST collections/{id}/paths` and `DELETE collections/{id}/paths/{path_id}`
    answer 200 with the summary;
  - removing a folder that is not in that collection gives 404 `Unknown folder`
    (not idempotent, so a wrong id is noticed).
- **The folder browser** hides names that can never be a `source_path` (for
  example names with spaces), as well as hidden and skipped folders. Typing the
  path is the escape hatch.
- **`GET collections/{id}/files`** (the walk-based listing) stays in the API and
  the proxy. The UI uses the log instead.
- **admin-api wiring:**
  - `repository_api.build_api(app_name, store)` drops `backoffice_client`;
  - `repository_worker.run_once(store, code_root)` drops `rag_client`;
  - the `repository-worker` Compose service loses `DOCS_WIKI_RAG_URL` and
    `RAG_BACKOFFICE_KEY`;
  - the plain archive route returns `{"id": …}`, and code project edits return
    `{"id": …}`.
- **Fresh installs** get no `documents` collection. The docs tell the
  administrator to create one and add `generated-documents`. Upgraded installs
  keep theirs from 0063 to 0065.
- **UI:**
  - Remove folder needs no confirmation (re-adding and Refresh restores it);
  - Remove collection keeps its confirmation;
  - the folder sheet's submit button is **Connect folder**, so it is not
    confused with the page's **Add folder**;
  - a typed path is trimmed of whitespace and leading and trailing `/`.

## Delivery and test boundaries

Tasks land in order on `feature/rag-managed-collections`, and every suite is green
after each task:
- Task 1 removes the RAG ownership API but keeps working on the 0065 schema
  (owner columns stay NULL).
- Task 2 stops admin-api from using it, and the search columns become unused.
- Task 3's migration then drops columns that no code reads any more.
- Tasks 4–6 need 0066, and the UI tasks (7–10) need the RAG and admin-api routes.

Each task lists the exact tests to run; Task 11 runs every suite.

---

# Part A — RAG configuration API (Task 1)

### Task 1: RAG — create collections, connect and disconnect folders, folder browser; remove the ownership API

**Files:**
- Modify: `services/rag/src/rag/collections.py`: add `FOLDER_NAME`, `MAX_FOLDERS` and `Areas.subfolders()`.
- Modify: `services/rag/src/rag/workspace.py`: delete `create_collection()` and `ensure_collection()`.
- Modify: `services/rag/src/rag/workspace_api.py` (code below).
- Modify: `services/rag/tests/test_workspace.py`: new helpers `create`, `add_folder`; `collection` uses them; the isolation test posts a collection.
- Modify: `services/rag/tests/test_collections_api.py`: `test_missing_folder_is_reported` and `test_one_active_sync_per_collection_and_sync_stale` create the `docs` folder before adding it and remove it afterwards.
- Modify: `services/rag/tests/test_sync.py`: `test_removing_a_path_drops_its_files_from_search` uses `DELETE collections/{id}/paths/{path_id}`.
- Modify: `services/rag/tests/test_collections.py`: add `test_subfolders_hide_hidden_skipped_links_and_unaddable_names`.
- Replace: `services/rag/tests/test_paths_api.py` (full content below).

**Interfaces:**
- Consumes: the 0065 schema (`ka.rag_collection_paths` with nullable owner columns, which this task never writes).
- Produces, under `/backoffice/projects/{project}` (all routes need `x-rag-key`):
  - `POST collections` with `NewCollection` `{name, description?, search_mode?, embeddings_enabled?, include?, exclude?, chunk_size?, chunk_overlap?}` → 201, the `CollectionSummary` (`{…row, indexed_files, chunks, embedded_chunks, paths, folder_present, missing_paths}`). 409 `Collection <name> already exists`.
  - `POST collections/{id}/paths` with `{source_path}` → 200, `CollectionSummary`. 409 `Folder overlaps <other> in collection <name>` or `Folder does not exist`; 400 for an invalid path.
  - `DELETE collections/{id}/paths/{path_id}` → 200, `CollectionSummary`. 404 `Unknown folder`.
  - `GET folders?path=` → `{path: str | null, folders: [{name, path}], truncated: bool}`.
  - `GET file?path=<rag-data path>` without `collection` → `{collection: null, path, content, hash}`. 404 `Document not found` outside `rag-data/<folder>/…` or for hidden files.
  - `DELETE collections/{id}` no longer refuses owned folders.
  - Removed: `PUT collections/by-name/{name}`, `PUT|DELETE collections/by-name/{name}/paths`, `DELETE paths`.
- Produces, in `rag.collections`: `Areas.subfolders(source: str, limit: int = MAX_FOLDERS) -> tuple[list[str], bool]` (names, truncated). It raises `HTTPException(404, "Folder does not exist")` for a missing folder.
- Produces, in `services/rag/tests/test_workspace.py`: `create(env, name, **settings) -> dict`, `add_folder(env, collection_id, source) -> Response`, `collection(env, name="docs", *sources, **settings) -> collection id`.

- [ ] **Step 1: Write the failing tests**

In `services/rag/tests/test_workspace.py`, replace `collection()` with these three
helpers:

```python
def create(env, name, **settings):
    """POST a new collection with `settings`; returns its summary."""
    response = env[1].post(env[3] + "/collections", json={"name": name, **settings})
    assert response.status_code == 201, response.text
    return response.json()


def add_folder(env, collection_id, source):
    return env[1].post(env[3] + f"/collections/{collection_id}/paths", json={"source_path": source})


def collection(env, name="docs", *sources, **settings):
    """Create collection `name` with `settings` and connect `sources` (default rag-data/<name>); returns its id."""
    collection_id = create(env, name, **settings)["id"]
    for source in sources or (f"rag-data/{name}",):
        added = add_folder(env, collection_id, source)
        assert added.status_code == 200, added.text
    return collection_id
```

In `test_single_workspace_and_cross_project_isolation`, replace the `by-name` line
with:

```python
    assert client.post(f"/backoffice/projects/{other}/collections", json={"name": "x"}).status_code == 404
```

In `services/rag/tests/test_collections_api.py`:

```python
def test_missing_folder_is_reported(env):
    absent = env[6]["docs"] / "absent"
    absent.mkdir()  # only existing folders outside rag-data can be added
    created = collection(env, "absent", "docs/absent")
    absent.rmdir()
    row = status(env, created)
    assert row["status"] == "folder_missing" and row["folder_present"] is False
    assert row["missing_paths"] == ["docs/absent"]
    result = env[1].post(env[3] + f"/collections/{created}/sync", json={})
    assert result.status_code == 409 and "missing" in result.json()["error"]["message"]
```

and in `test_one_active_sync_per_collection_and_sync_stale` replace
`first = collection(env, "c", "docs/c")` with:

```python
    (roots["docs"] / "c").mkdir()
    first = collection(env, "c", "docs/c")
    (roots["docs"] / "c").rmdir()
```

In `services/rag/tests/test_sync.py`, `test_removing_a_path_drops_its_files_from_search`,
replace the two lines starting `removed = client.delete(` with:

```python
    path_id = next(p["id"] for p in client.get(prefix + f"/collections/{created}").json()["paths"]
                   if p["source_path"] == "rag-data/b")
    removed = client.delete(prefix + f"/collections/{created}/paths/{path_id}")
    assert removed.status_code == 200 and [p["source_path"] for p in removed.json()["paths"]] == ["rag-data/a"]
```

Append to `services/rag/tests/test_collections.py`:

```python
def test_subfolders_hide_hidden_skipped_links_and_unaddable_names(areas):
    areas, roots = areas
    for name in ('b', 'a', '.git', '.env.d', 'node_modules', 'two words'):
        (roots['code'] / name).mkdir()
    (roots['code'] / 'file.md').write_text('x')
    os.symlink(roots['code'] / 'a', roots['code'] / 'link')
    assert areas.subfolders('code') == (['a', 'b'], False)
    assert areas.subfolders('code', limit=1) == (['a'], True)
    with pytest.raises(HTTPException) as missing:
        areas.subfolders('code/absent')
    assert missing.value.status_code == 404 and missing.value.detail == 'Folder does not exist'
```

Replace `services/rag/tests/test_paths_api.py` with:

```python
"""Collections managed in RAG: create, connect and disconnect folders, the folder browser and the file API."""
import os
import threading
import time
from datetime import datetime
from uuid import uuid4

import pytest
from test_workspace import add_folder, collection, create, sync

# env and workspace_postgres come from conftest.py


def listing(env):
    return {row["name"]: row for row in env[1].get(env[3] + "/collections").json()["collections"]}


def status(env, collection_id):
    return env[1].get(env[3] + f"/collections/{collection_id}").json()


def folders(row):
    return [p["source_path"] for p in row["paths"]]


def test_create_collection_with_settings_and_refuse_duplicates(env):
    client, prefix = env[1], env[3]
    created = client.post(prefix + "/collections", json={"name": "payments", "description": "Payments knowledge",
                                                          "include": ["**/*.py"]})
    assert created.status_code == 201, created.text
    row = created.json()
    assert (row["name"], row["description"], row["include"], row["paths"]) == ("payments", "Payments knowledge", ["**/*.py"], [])
    assert (row["search_mode"], row["embeddings_enabled"], row["exclude"], row["chunk_size"], row["chunk_overlap"]) == (
        "full_text", False, [], 2000, 200)
    duplicate = client.post(prefix + "/collections", json={"name": "payments"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["message"] == "Collection payments already exists"
    for bad in ({"name": "Payments"}, {"name": "a--b"}, {"name": "x" * 61}, {"name": "ok", "chunk_size": 100},
                {"name": "ok", "include": []}, {"name": "ok", "source_path": "rag-data/ok"}, {}):
        assert client.post(prefix + "/collections", json=bad).status_code == 400, bad
    assert set(listing(env)) == {"payments"}


def test_add_folder_creates_rag_data_folders_and_bumps_revision(env):
    roots = env[6]
    (roots["code"] / "payments").mkdir()
    row = create(env, "payments")
    added = add_folder(env, row["id"], "code/payments")
    assert added.status_code == 200, added.text
    assert folders(added.json()) == ["code/payments"] and added.json()["revision"] == row["revision"] + 1
    path = added.json()["paths"][0]
    assert path["host_path"] == "/srv/code/payments" and path["present"] is True and path["files"] == 0
    tickets = add_folder(env, row["id"], "rag-data/jira/tickets").json()
    assert folders(tickets) == ["code/payments", "rag-data/jira/tickets"]
    assert (roots["rag-data"] / "jira" / "tickets").is_dir()  # a missing rag-data folder is created
    assert add_folder(env, uuid4(), "code/payments").status_code == 404


def test_add_folder_rejects_overlap_missing_and_bad_paths_and_changes_nothing(env):
    roots = env[6]
    (roots["code"] / "payments" / "src").mkdir(parents=True)
    os.symlink(roots["code"] / "payments", roots["code"] / "linked")
    row = add_folder(env, create(env, "payments")["id"], "code/payments").json()
    for source in ("code/payments", "code/payments/src", "code"):
        refused = add_folder(env, row["id"], source)
        assert refused.status_code == 409, source
        assert refused.json()["error"]["message"] == "Folder overlaps code/payments in collection payments"
    for source in ("docs/absent", "code/linked"):
        missing = add_folder(env, row["id"], source)
        assert missing.status_code == 409 and missing.json()["error"]["message"] == "Folder does not exist", source
    assert not (roots["docs"] / "absent").exists()
    for source in ("../etc", "tmp/x", "code/payments/", "/code/payments", "code/a b", "code/.git", "code/../docs", ""):
        assert add_folder(env, row["id"], source).status_code == 400, source
    owned = env[1].post(env[3] + f"/collections/{row['id']}/paths", json={"source_path": "docs", "owner_kind": "jira"})
    assert owned.status_code == 400
    after = status(env, row["id"])
    assert folders(after) == ["code/payments"] and after["revision"] == row["revision"]
    other = create(env, "other")
    assert folders(add_folder(env, other["id"], "code/payments/src").json()) == ["code/payments/src"]  # collections may share


def test_remove_folder_only_removes_a_folder_of_that_collection(env):
    roots = env[6]
    (roots["docs"] / "runbooks").mkdir()
    one = collection(env, "one", "docs/runbooks")
    two = collection(env, "two", "docs/runbooks")
    row = status(env, one)
    path_id = row["paths"][0]["id"]
    wrong = env[1].delete(env[3] + f"/collections/{two}/paths/{path_id}")
    assert wrong.status_code == 404 and wrong.json()["error"]["message"] == "Unknown folder"
    assert folders(status(env, one)) == ["docs/runbooks"]
    removed = env[1].delete(env[3] + f"/collections/{one}/paths/{path_id}")
    assert removed.status_code == 200 and removed.json()["paths"] == [] and removed.json()["revision"] == row["revision"] + 1
    assert env[1].delete(env[3] + f"/collections/{one}/paths/{path_id}").status_code == 404
    assert (roots["docs"] / "runbooks").is_dir()


def test_remove_collection_is_always_allowed_and_keeps_files(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "jira").mkdir()
    (roots["rag-data"] / "jira" / "DEMO-1.md").write_text("# DEMO-1\n\nretries")
    created = collection(env, "jira", "rag-data/jira")
    _, row = sync(env, created)
    removed = client.delete(prefix + f"/collections/{created}", params={"revision": row["revision"], "confirmed": True})
    assert removed.status_code == 200 and removed.json() == {"deleted_files": 1}
    assert "jira" not in listing(env) and (roots["rag-data"] / "jira" / "DEMO-1.md").is_file()
    with env[0].connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.rag_collection_paths WHERE collection_id=%s",
                            (created,)).fetchone()["n"] == 0


def test_folder_browser_lists_visible_subfolders_and_rejects_bad_paths(env):
    client, prefix, roots = env[1], env[3], env[6]
    for name in ("payments", "billing", ".git", ".hidden", "node_modules", "with space"):
        (roots["code"] / name).mkdir()
    (roots["code"] / "README.md").write_text("x")
    os.symlink(roots["code"] / "payments", roots["code"] / "link")
    top = client.get(prefix + "/folders").json()
    assert top["path"] is None and [f["path"] for f in top["folders"]] == [
        "code", "code-index", "rag-data", "generated-documents", "docs"]
    code = client.get(prefix + "/folders", params={"path": "code"}).json()
    assert code == {"path": "code", "truncated": False, "folders": [
        {"name": "billing", "path": "code/billing"}, {"name": "payments", "path": "code/payments"}]}
    for bad in ("../etc", "code/../docs", "/code", "code/.git", "tmp", "code/"):
        assert client.get(prefix + "/folders", params={"path": bad}).status_code == 400, bad
    missing = client.get(prefix + "/folders", params={"path": "code/absent"})
    assert missing.status_code == 404 and missing.json()["error"]["message"] == "Folder does not exist"


def test_ownership_endpoints_are_gone(env):
    client, prefix = env[1], env[3]
    assert client.put(prefix + "/collections/by-name/jira", json={}).status_code in (404, 405)
    assert client.put(prefix + "/collections/by-name/jira/paths", json={"source_path": "rag-data/jira"}).status_code in (404, 405)
    assert client.delete(prefix + "/collections/by-name/jira/paths", params={"owner_kind": "jira"}).status_code in (404, 405)
    assert client.delete(prefix + "/paths", params={"owner_kind": "jira"}).status_code in (404, 405)
    assert client.delete(prefix + "/file", params={"path": "rag-data/x/a.md"}).status_code == 405


def test_status_reports_each_path_and_missing_paths(env):
    roots = env[6]
    (roots["rag-data"] / "docs").mkdir()
    (roots["rag-data"] / "docs" / "a.md").write_text("# A")
    (roots["docs"] / "absent").mkdir()
    created = collection(env, "docs", "rag-data/docs", "docs/absent")
    (roots["docs"] / "absent").rmdir()
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
    add_folder(env, created, "docs/c")
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


def test_rag_data_files_are_read_by_full_path_without_a_collection(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "jira").mkdir()
    (roots["rag-data"] / "jira" / "DEMO-1.md").write_text("# DEMO-1")
    (roots["rag-data"] / "jira" / ".env").write_text("SECRET=1")
    (roots["rag-data"] / "top.md").write_text("top")
    (roots["code"] / "x.md").write_text("code")
    found = client.get(prefix + "/file", params={"path": "rag-data/jira/DEMO-1.md"})
    assert found.status_code == 200 and found.json()["content"] == "# DEMO-1" and found.json()["collection"] is None
    for path in ("rag-data/jira/.env", "code/x.md", "rag-data/jira/absent.md", "rag-data/top.md"):
        response = client.get(prefix + "/file", params={"path": path})
        assert response.status_code == 404, path
        assert "SECRET" not in response.text


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
        assert put(path).status_code == 400, path
    assert not (roots["code"] / "payments").exists()


def test_status_presence_is_per_path_even_when_the_walk_stops(env, monkeypatch):
    import rag.collections
    roots = env[6]
    (roots["code"] / "big").mkdir()
    for index in range(5):
        (roots["code"] / "big" / f"{index}.md").write_text("# x")
    (roots["docs"] / "absent").mkdir()
    created = collection(env, "big", "code/big", "docs/absent")
    (roots["docs"] / "absent").rmdir()
    monkeypatch.setattr(rag.collections, "MAX_STATUS_ENTRIES", 2)
    row = status(env, created)
    assert row["truncated"] is True
    assert row["status"] == "folder_missing" and row["missing_paths"] == ["docs/absent"]
    assert {p["source_path"]: p["present"] for p in row["paths"]} == {"code/big": True, "docs/absent": False}


def wait_for_lock(env, text):
    """Block until a backend waits on a lock in a statement containing `text`."""
    for _ in range(200):
        with env[0].connect() as conn:
            if conn.execute("SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE %s",
                            (f"%{text}%",)).fetchone():
                return
        time.sleep(.05)
    raise AssertionError(f"Nothing waited on {text}")


def test_removing_a_folder_waits_for_a_publishing_sync_instead_of_deadlocking(env):
    """A publishing sync locks its collection, then the workspace; folder removal takes the same order."""
    roots = env[6]
    (roots["code"] / "api").mkdir()
    created = collection(env, "one", "code/api")
    path_id = status(env, created)["paths"][0]["id"]
    client, prefix, project = env[1], env[3], env[2]
    result = {}
    with env[0].connect() as publishing:
        publishing.execute("SELECT * FROM ka.rag_collections WHERE id=%s FOR UPDATE", (created,))
        thread = threading.Thread(target=lambda: result.setdefault(
            "response", client.delete(prefix + f"/collections/{created}/paths/{path_id}")))
        thread.start()
        wait_for_lock(env, "ka.rag_collections")
        publishing.execute("SELECT * FROM ka.rag_workspaces WHERE project_id=%s FOR UPDATE", (project,))
    thread.join(10)
    assert result["response"].status_code == 200, result["response"].text
    assert status(env, created)["paths"] == []
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest tests/test_paths_api.py tests/test_collections.py -q`
Expected: FAIL. `POST collections` answers 405, `GET folders` 404, and
`Areas.subfolders` does not exist.

- [ ] **Step 3: Add `Areas.subfolders` to `services/rag/src/rag/collections.py`**

Below `MAX_BYTES = 200_000_000` add:

```python
MAX_FOLDERS = 1000  # the folder browser lists at most this many children
FOLDER_NAME = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9._-]*')  # a name that can be a source_path segment
```

Add this method to `Areas`, after `ensure_folder`:

```python
    def subfolders(self, source: str, limit: int = MAX_FOLDERS) -> tuple[list[str], bool]:
        """Child folders that could be connected to a collection, sorted, and whether more exist.

        Links are never followed or listed; hidden folders, .git, node_modules and names that are not
        valid source_path segments are left out.
        """
        try:
            with self._directory(source, []) as fd:
                found = []
                for name in sorted(os.listdir(fd)):
                    if not visible(name) or not FOLDER_NAME.fullmatch(name):
                        continue
                    try:
                        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISDIR(entry.st_mode):
                        if len(found) == limit:
                            return found, True
                        found.append(name)
                return found, False
        except (FileNotFoundError, NotADirectoryError):
            raise HTTPException(404, 'Folder does not exist') from None
```

- [ ] **Step 4: Remove creation helpers from `services/rag/src/rag/workspace.py`**

Delete `create_collection()` and `ensure_collection()`. Keep `collection()`,
`collection_by_name()` (the file API uses it) and `paths()`.

- [ ] **Step 5: Change `services/rag/src/rag/workspace_api.py`**

1. Replace the module docstring with:

```python
"""Authenticated backoffice API; area roots come from the deployment, never from input.

RAG owns the configuration: administrators and external tools create collections and connect
folders under DATA_ROOT to them. Sources only write files into their folders.
"""
```

2. Imports: drop `Path` from the `fastapi` import, and import from
   `rag.collections`:
   `AREAS, WRITABLE_AREA, FolderMissing, indexable, overlaps, refuse_hidden, relative_path, source_path, visible`.

3. Delete `OwnerKind`, `OWNER_LABELS`, the `Path`-based `CollectionName`,
   `CreationSettings`, `EnsureInput`, `PathInput`, `owner_label()` and
   `owner_match()`. Replace the `CollectionSettings` class with:

```python
CollectionName = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=60)]


class CollectionFields(BaseModel):
    """Settings shared by create and edit. A collection's name never changes."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field("", max_length=2000)
    search_mode: Literal["full_text", "embedding", "hybrid"] = "full_text"
    embeddings_enabled: bool = False
    include: list[GlobPattern] = Field(default_factory=lambda: ["**/*.md", "**/*.markdown", "**/*.txt"], min_length=1, max_length=50)
    exclude: list[GlobPattern] = Field(default_factory=list, max_length=50)
    chunk_size: int = Field(2000, ge=200, le=12000)
    chunk_overlap: int = Field(200, ge=0, le=2000)

    @model_validator(mode="after")
    def valid(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if self.search_mode == "embedding" and not self.embeddings_enabled:
            raise ValueError("Turn on embeddings for semantic search, or choose keyword or hybrid search")
        return self


class NewCollection(CollectionFields):
    name: CollectionName


class CollectionSettings(CollectionFields):
    revision: int


class FolderInput(BaseModel):
    """A folder under DATA_ROOT to connect; validated with source_path() in the route."""
    model_config = ConfigDict(extra="forbid")
    source_path: str = Field(max_length=500)
```

4. Delete the routes `ensure_collection` (`PUT …/by-name/{name}`), `add_path`,
   `add_path_once`, `remove_path` and `remove_owner_paths`. In their place, after
   `list_collections`, add:

```python
    @router.post(prefix + "/collections", status_code=201)
    def create_collection(project: UUID, payload: NewCollection):
        """Create an empty collection with its settings; folders are connected with POST collections/{id}/paths."""
        columns = ["project_id", "name", *SETTINGS_COLUMNS]
        with workspace.connect() as conn:
            workspace.ensure(conn, project)
            row = conn.execute(f"""INSERT INTO ka.rag_collections({','.join(columns)})
                VALUES ({','.join(['%s'] * len(columns))})
                ON CONFLICT (project_id,name) WHERE deleted_at IS NULL DO NOTHING RETURNING *""",
                [project, payload.name, *(getattr(payload, c) for c in SETTINGS_COLUMNS)]).fetchone()
            if row is None:
                raise HTTPException(409, f"Collection {payload.name} already exists")
            return collection_summary(workspace, conn, row)

    @router.post(prefix + "/collections/{collection_id}/paths")
    def add_folder(project: UUID, collection_id: UUID, payload: FolderInput):
        """Connect a folder. A missing folder under rag-data is created; elsewhere it must exist."""
        new = source_path(payload.source_path)
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)  # serializes with edits and syncs
            for path in workspace.paths(conn, collection_id):
                if overlaps(path["source_path"], new):
                    raise HTTPException(409, f"Folder overlaps {path['source_path']} in collection {row['name']}")
            if not workspace.areas.present(new):
                if not workspace.areas.writable(new):
                    raise HTTPException(409, "Folder does not exist")
                workspace.areas.ensure_folder(new)
            conn.execute("INSERT INTO ka.rag_collection_paths(collection_id,source_path) VALUES (%s,%s)", (collection_id, new))
            bump(conn, {collection_id})
            return collection_summary(workspace, conn, workspace.collection(conn, project, collection_id))

    @router.delete(prefix + "/collections/{collection_id}/paths/{path_id}")
    def remove_folder(project: UUID, collection_id: UUID, path_id: UUID):
        """Disconnect a folder: its indexed files (and file log rows) go; files on disk stay."""
        with workspace.connect() as conn:
            # Lock order collection -> workspace, as a publishing sync.
            workspace.collection(conn, project, collection_id, lock=True)
            removed = conn.execute("DELETE FROM ka.rag_collection_paths WHERE id=%s AND collection_id=%s",
                                   (path_id, collection_id)).rowcount
            if not removed:
                raise HTTPException(404, "Unknown folder")
            bump(conn, {collection_id})
            drop_orphan_vectors(conn, project)
            return collection_summary(workspace, conn, workspace.collection(conn, project, collection_id))

    @router.get(prefix + "/folders")
    def folders(project: UUID, path: str | None = Query(None, max_length=500)):
        """Subfolders of a DATA_ROOT folder for the folder browser; without `path`, the mounted areas."""
        if path is None:
            return {"path": None, "truncated": False,
                    "folders": [{"name": area, "path": area} for area in AREAS if area in workspace.areas.roots]}
        names, truncated = workspace.areas.subfolders(source_path(path))
        return {"path": path, "truncated": truncated, "folders": [{"name": name, "path": f"{path}/{name}"} for name in names]}
```

5. In `delete_collection`, delete the `owned = …` query and its `if owned:` block.
   The rest stays.

6. Replace `get_file` with:

```python
    @router.get(prefix + "/file")
    def get_file(project: UUID, path: str, collection: str | None = None):
        """An indexable file of `collection` by its full path. Without `collection`, a visible file under
        rag-data/<folder>/ (the Jira export reads its previous ticket file this way)."""
        path = relative_path(path)
        area, _, rest = path.partition("/")
        with workspace.connect() as conn:
            limit = Settings(**workspace.settings(conn, project)["settings"]).max_file_bytes
            if collection is None:
                allowed = area == WRITABLE_AREA and "/" in rest and visible(rest)
            else:
                row = workspace.collection_by_name(conn, project, collection)
                roots = [p["source_path"] for p in workspace.paths(conn, row["id"])]
                root = next((r for r in roots if path.startswith(r + "/")), None)
                # Only what sync would index: under one of the collection's folders, never .env*, hidden or
                # skipped folders, or files its patterns (matched relative to that folder) leave out.
                allowed = root is not None and indexable(path[len(root) + 1:], row["include"], row["exclude"])
        if not allowed:
            raise HTTPException(404, "Document not found")
        try:
            data = workspace.areas.read(area, rest, limit)
        except FileNotFoundError:
            raise HTTPException(404, "Document not found") from None
        try:
            content = data.decode("utf-8-sig")
        except UnicodeError:
            raise HTTPException(400, "File is not UTF-8 text") from None
        return {"collection": collection, "path": path, "content": content, "hash": digest(data)}
```

- [ ] **Step 6: Run the RAG suite**

Run: `cd services/rag && uv run --frozen pytest -q`
Expected: everything passes, including the 15 tests of the new `test_paths_api.py`.

- [ ] **Step 7: Commit**

```bash
git add services/rag/src/rag/collections.py services/rag/src/rag/workspace.py services/rag/src/rag/workspace_api.py \
  services/rag/tests/test_workspace.py services/rag/tests/test_collections_api.py services/rag/tests/test_sync.py \
  services/rag/tests/test_collections.py services/rag/tests/test_paths_api.py
git commit -m "feat(rag): create collections and connect folders in RAG; folder browser; no source ownership

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

# Part B — admin-api: sources only write files (Task 2)

### Task 2: admin-api — remove ownership, Searchable, startup registration and the Jira collection

**Files:**
- Delete: `services/admin-api/src/knowledge_admin/code_project_search.py`
- Delete: `services/admin-api/src/knowledge_admin/rag_collections.py`
- Delete: `services/admin-api/tests/test_code_project_search.py`, `services/admin-api/tests/test_documents_source.py`, `services/admin-api/tests/test_rag_collections_client.py`
- Modify: `services/admin-api/src/knowledge_admin/rag_files.py`: files by full path, no collection.
- Modify: `services/admin-api/src/knowledge_admin/jira_export.py`, `jira_api.py`
- Modify: `services/admin-api/src/knowledge_admin/repository_api.py`, `api.py`, `repositories.py`, `repository_worker.py`
- Modify: `services/admin-api/src/knowledge_admin/app.py`
- Modify: `services/admin-api/src/knowledge_admin/rag_api.py`
- Modify: `deploy/compose.yaml` (`repository-worker` environment), `deploy/README.md` (the `repository-worker` bullet)
- Test: `services/admin-api/tests/test_jira_export.py`, `services/admin-api/tests/test_rag.py`, `services/admin-api/tests/test_repository_worker.py`

**Interfaces:**
- Consumes, from Task 1: `GET file?path=` without `collection`, `PUT file`, and the new RAG routes the proxy forwards.
- Produces:
  - `RagFiles.get_file(path: str) -> dict | None` and
    `RagFiles.write_markdown(path: str, content: str) -> {path, status, hash}`;
  - `jira_export.TicketExportInput {connection_revision}` (`extra="forbid"`);
  - `publish_ticket(client, key, project, actor, issue_key, content) -> {path, status, hash}`;
  - `POST /api/jira/tickets/{id}/export` → `{path, status, hash, issue_key, comments}`;
  - `PUT /api/code-projects/{id}` → `{"id": …}`;
    `POST /api/code-projects/{id}/archive` → `{"id": …}`;
    `GET /api/code-projects` rows have no `searchable`, `collection` or
    `search_error`; `PUT /api/code-projects/{id}/search` is gone;
  - `repository_api.build_api(app_name, store)`;
    `repository_worker.run_once(store, code_root) -> bool`;
  - the proxy `ALLOWED`:
    - `GET`: `settings|collections|collections/<uuid>(/files|/chunks|/log)?|file|jobs|folders`;
    - `PUT`: `settings|collections/<uuid>`;
    - `POST`: `collections|collections/sync-stale|collections/<uuid>/(sync|paths)|jobs/<uuid>/cancel|test/embedding|search`;
    - `DELETE`: `collections/<uuid>(/paths/<uuid>)?`.

- [ ] **Step 1: Write the failing tests**

In `services/admin-api/tests/test_jira_export.py`:
- In the `exporting` fixture's `state`, drop `"jira_collection": None`.
- In `rag(request)`, delete the whole `if request.url.path.endswith("/paths"):` block.
- In the `GET` branch, replace the `collection` assertion with
  `assert "collection" not in request.url.params`.
- `export()` becomes:

```python
def export(client, snapshot, **payload):
    return client.post(f"/api/jira/tickets/{snapshot['tickets'][0]['id']}/export", headers=HEADERS,
                       json={"connection_revision": snapshot["connection"]["revision"], **payload})
```

- In `test_export_requires_admin_identity_current_connection_and_saved_ticket`, use
  `body = {"connection_revision": 1}`.
- Delete these tests:
  - `test_export_rejects_unsafe_collection_before_any_outbound_request`;
  - `test_export_adds_the_jira_path_to_the_chosen_collection`;
  - `test_export_to_another_collection_moves_the_jira_path`;
  - `test_export_fails_with_rag_overlap_message`.
- Append:

```python
def test_export_writes_rag_data_jira_without_a_collection(exporting):
    client, snapshot, state = exporting
    response = export(client, snapshot)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == "rag-data/jira/DEMO-1.md" and body["status"] == "created" and "collection" not in body
    assert [(call.method, call.url.path.rsplit("/", 1)[-1]) for call in state["rag_calls"]] == [("GET", "file"), ("PUT", "file")]
    assert list(state["files"]) == ["rag-data/jira/DEMO-1.md"]


def test_export_refuses_a_collection_field(exporting):
    client, snapshot, state = exporting
    assert export(client, snapshot, collection="jira").status_code == 400
    assert state["jira_calls"] == [] and state["rag_calls"] == []
```

In `services/admin-api/tests/test_rag.py`:
- In `test_rag_proxy_auth_project_binding_and_allowlist`, the 404 loop's tuple
  becomes:

```python
        for method, resource in (("GET", "tree"), ("POST", "folders"), ("POST", "jobs"), ("PUT", f"folders/{collection}"),
                                 ("DELETE", f"folders/{collection}"), ("GET", f"collections/{collection}/other"),
                                 ("POST", f"collections/{collection}/files"), ("DELETE", "collections"),
                                 ("PUT", "file"), ("DELETE", "file"), ("DELETE", "paths"),
                                 ("PUT", f"collections/{collection}/paths"), ("DELETE", f"collections/{collection}/paths"),
                                 ("PUT", "collections/by-name/jira"), ("PUT", "collections/by-name/jira/paths"),
                                 ("DELETE", "collections/by-name/jira/paths")):
```

- The 200 loop's tuple becomes:

```python
        for method, resource in (("GET", "collections"), ("POST", "collections"), ("GET", f"collections/{collection}"),
                                 ("PUT", f"collections/{collection}"), ("DELETE", f"collections/{collection}"),
                                 ("POST", f"collections/{collection}/paths"),
                                 ("DELETE", f"collections/{collection}/paths/{collection}"),
                                 ("POST", f"collections/{collection}/sync"), ("POST", "collections/sync-stale"),
                                 ("GET", f"collections/{collection}/files"), ("GET", f"collections/{collection}/chunks"),
                                 ("GET", f"collections/{collection}/log"), ("GET", "folders"),
                                 ("POST", f"jobs/{collection}/cancel"), ("GET", "jobs")):
```

- Replace `test_rag_proxy_never_removes_source_owned_paths` with (add
  `import time` to the imports):

```python
def test_startup_and_code_projects_never_call_rag(store, tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_BACKOFFICE_KEY", "private-service-key")
    root = tmp_path / "code"
    for folder in ("api", "shared"):
        (root / folder).mkdir(parents=True)
    monkeypatch.setenv("CODE_ROOT", str(root))
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={})
    cfg = Config(apps=[App(name="docs", app_root=None)], llm=None)
    server = ServerConfig(cors_origin=None, session_retention_days=90, state_dir=str(tmp_path))
    with httpx.Client(base_url="http://rag.test", transport=httpx.MockTransport(upstream)) as backend:
        with TestClient(create_app(cfg, server=server, repository_store=store, backoffice_client=backend)) as client:
            created = client.post("/api/code-projects", headers=HEADERS, json={
                "name": "Payments", "description": "", "remote_url": None, "folder_paths": ["api"]})
            assert created.status_code == 201, created.text
            project_id = created.json()["id"]
            row = next(p for p in client.get("/api/code-projects", headers=HEADERS).json()["projects"] if p["id"] == project_id)
            assert not {"searchable", "collection", "search_error"} & set(row)
            edited = client.put(f"/api/code-projects/{project_id}", headers=HEADERS, json={
                "name": "Payments", "description": "", "remote_url": None, "folder_paths": ["api", "shared"],
                "revision": row["revision"]})
            assert edited.status_code == 200 and edited.json() == {"id": project_id}
            assert client.put(f"/api/code-projects/{project_id}/search", headers=HEADERS,
                              json={"enabled": True}).status_code in (404, 405)
            archived = client.post(f"/api/code-projects/{project_id}/archive", headers=HEADERS)
            assert archived.status_code == 200 and archived.json() == {"id": project_id}
            assert all(p["id"] != project_id for p in client.get("/api/code-projects", headers=HEADERS).json()["projects"])
            assert client.post(f"/api/code-projects/{project_id}/archive", headers=HEADERS).status_code == 404
            time.sleep(0.2)  # startup background tasks have had time to run
    assert calls == []
```

In `services/admin-api/tests/test_repository_worker.py`, delete
`test_pull_of_a_searchable_project_updates_its_rag_paths` and
`test_pull_of_a_project_that_is_not_searchable_never_calls_rag`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_jira_export.py tests/test_rag.py -q`
Expected: FAIL. The export still calls `collections/by-name/jira/paths` and
refuses no `collection`, the proxy refuses `POST collections`, and the code
project rows still carry `searchable`.

- [ ] **Step 3: Files by full path in `rag_files.py`**

Replace `get_file` and `write_markdown` with:

```python
    def get_file(self, path: str) -> dict | None:
        """A file under rag-data by its full DATA_ROOT path, or None when it does not exist."""
        try:
            return self.request('GET', 'file', params={'path': path})
        except HTTPException as error:
            if error.status_code == 404:
                return None
            raise

    def write_markdown(self, path: str, content: str) -> dict:
        """Create or replace `path` (full, under rag-data). A file whose first line differs belongs to
        another source and is kept."""
        previous = self.get_file(path)
        if previous is not None:
            if previous['content'].partition('\n')[0] != content.partition('\n')[0]:
                raise HTTPException(409, f'{path} belongs to another source. The existing file was preserved')
            if previous['content'] == content:
                return {'path': path, 'status': 'unchanged', 'hash': previous['hash']}
        saved = self.request('PUT', 'file', json={'path': path, 'content': content,
                                                  'expected_hash': previous['hash'] if previous is not None else None})
        return {'path': path, 'status': 'updated' if previous is not None else 'created', 'hash': saved['hash']}
```

- [ ] **Step 4: Jira export without a collection**

In `jira_export.py`, replace the `rag_collections` import with
`from knowledge_admin.rag_files import RagFiles`. Make `TicketExportInput`:

```python
class TicketExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    connection_revision: int = Field(ge=1, strict=True)
```

and `publish_ticket`:

```python
def publish_ticket(client: httpx.Client, key: str, project: UUID, actor: UUID, issue_key: str, content: str) -> dict:
    """Write the ticket to rag-data/jira/<KEY>.md; collections that hold rag-data/jira index it at their next sync."""
    return RagFiles(client, key, project, actor).write_markdown(f"{JIRA_PATH}/{issue_key}.md", content)
```

In `jira_api.py`, the call becomes
`result = publish_ticket(backoffice_client, rag_key, config["project_id"], actor, issue["key"], content)`.

- [ ] **Step 5: Code projects never call RAG**

`repository_api.py`:
- Delete the `httpx`, `pydantic` and `code_project_search` imports, `SearchInput`,
  `refresh_search()` and the `set_search` route.
- The signature becomes
  `def build_api(app_name: str, store: RepositoryStore | None) -> APIRouter:`.
- `archive_project` becomes:

```python
    @router.post('/code-projects/{repository_id}/archive')
    def archive_project(repository_id: UUID):
        """Hide the project everywhere. Its folders stay connected to any RAG collection until an admin removes them."""
        selected = repository_store()
        with selected.connect() as conn:
            project = selected.project(conn, app_name)
            repo = conn.execute('''SELECT id FROM ka.repositories WHERE id=%s AND project_id=%s AND archived_at IS NULL
                FOR UPDATE''', (repository_id, project)).fetchone()
            if repo is None:
                raise HTTPException(404, 'Code project not found')
            selected.require_idle(conn, repository_id)
            conn.execute('UPDATE ka.repositories SET archived_at=now() WHERE id=%s', (repository_id,))
        return {'id': repository_id}
```

- `edit_repository` ends with `return {"id": repository_id}`.

In `api.py`, line 38 becomes
`router.include_router(repository_api.build_api(app_name, repository_store))`.

In `repositories.py`, delete the `REPOSITORY_FIELDS` line
`(r.search_collection IS NOT NULL) AS searchable, r.search_collection AS collection, r.search_error,`.

In `repository_worker.py`:
- delete `import httpx` and the `code_project_search` import;
- `run_once(store: RepositoryStore, code_root: Path) -> bool`;
- delete the two lines `if error is None and job['kind'] == 'repository_sync':` /
  `update_search(...)` and the `update_search()` function;
- in `main()`, delete `rag_url`/`rag_client` and call `run_once(store, code_root)`.

- [ ] **Step 6: No Documents registration at startup**

In `app.py`:
- delete `import os`, the `rag_collections` import, `ensure_documents_source()`
  and `ensure_documents_collection()`;
- in `lifespan`, delete the `documents = asyncio.create_task(...)` line and
  `documents.cancel()`;
- the gather becomes `await asyncio.gather(expiry, backfill, *tasks, return_exceptions=True)`.

`backfill_document_exports` stays: it writes files and never calls RAG.

- [ ] **Step 7: Proxy allowlist**

In `rag_api.py`, replace `ALLOWED` and delete `OWNER_PARAMS` and the `DELETE … /paths` owner check:

```python
ALLOWED = {
    "GET": r"settings|collections|collections/[0-9a-f-]{36}(/files|/chunks|/log)?|file|jobs|folders",
    "PUT": r"settings|collections/[0-9a-f-]{36}",
    "POST": r"collections|collections/sync-stale|collections/[0-9a-f-]{36}/(sync|paths)|jobs/[0-9a-f-]{36}/cancel|test/embedding|search",
    # Admins create collections and connect or disconnect folders; sources only write files.
    "DELETE": r"collections/[0-9a-f-]{36}(/paths/[0-9a-f-]{36})?",
}
```

- [ ] **Step 8: Repository worker deployment**

In `deploy/compose.yaml`, `repository-worker.environment`, delete the comment line
`# After a pull, a searchable code project's folders become its RAG collection paths.`
and the `DOCS_WIKI_RAG_URL` and `RAG_BACKOFFICE_KEY` lines.

In `deploy/README.md`, the `repository-worker` bullet goes back to:

```markdown
- `repository-worker` has no host port and owns the writable `/work/code` bind
  mount. It claims PostgreSQL jobs, clones and periodically pulls Git source into named folders. The admin API
  and source browser mount the same folder read-only.
```

- [ ] **Step 9: Delete the ownership modules and their tests**

```bash
git rm services/admin-api/src/knowledge_admin/code_project_search.py services/admin-api/src/knowledge_admin/rag_collections.py \
  services/admin-api/tests/test_code_project_search.py services/admin-api/tests/test_documents_source.py \
  services/admin-api/tests/test_rag_collections_client.py
```

- [ ] **Step 10: Run the admin-api suite and check for leftovers**

Run: `cd services/admin-api && uv run --frozen pytest -q`
Expected: everything passes.

Run: `git grep -n "rag_collections import\|code_project_search\|ensure_documents\|search_collection\|search_error\|by-name\|owner_kind" -- services/admin-api/src deploy`
Expected: no output.

- [ ] **Step 11: Commit**

```bash
git add -A services/admin-api deploy/compose.yaml deploy/README.md
git commit -m "feat(admin): sources only write files; no RAG ownership calls from Jira, Documents or code projects

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

# Part C — schema (Task 3)

### Task 3: Migration 0066 — no owners, file log, auto-sync columns

**Files:**
- Create: `db/migrations/0066_rag_managed_collections.up.sql`
- Create: `db/migrations/0066_rag_managed_collections.down.sql`
- Modify: `db/tests/run.sh`: change `[ "$rolled_back_version" = 64 ]` to `65`.
- Modify: `services/admin-api/tests/test_rag_collection_paths_migration.py`: the `at_0064` fixture first rolls back 0066.
- Modify: `services/admin-api/tests/test_rag_collections_migration.py`: `_seed` first rolls back 0066.
- Test: `services/admin-api/tests/test_rag_managed_collections_migration.py` (new)

**Interfaces:**
- Consumes: the 0065 schema.
- Produces:
  - `ka.rag_collection_paths` without `owner_kind`, `owner_id`, `owner_key` and
    without index `rag_collection_paths_owner`;
  - `ka.repositories` without `search_collection` and `search_error`;
  - `ka.rag_collections.auto_sync boolean NOT NULL DEFAULT false`,
    `poll_interval_minutes integer NOT NULL DEFAULT 5` (1..1440) and
    `last_polled_at timestamptz`;
  - `ka.rag_file_log`:
    - columns `id uuid PK, collection_id uuid, path_id uuid, path text,
      size bigint NULL, mtime double precision NULL, content_hash text NULL,
      fingerprint text NULL, status text, error text NULL, first_seen_at,
      last_seen_at, indexed_at NULL, removed_at NULL`;
    - `UNIQUE (collection_id, path)`, FK `(path_id, collection_id)` →
      `rag_collection_paths(id, collection_id)` `ON DELETE CASCADE`, and indexes
      `rag_file_log_status (collection_id, status, path)` and
      `rag_file_log_path (path_id)`;
    - backfilled with one `indexed` row per `rag_files` row.

- [ ] **Step 1: Write the failing migration test**

Create `services/admin-api/tests/test_rag_managed_collections_migration.py`:

```python
"""0066 removes folder owners and code-project search settings, and adds the file log and auto-sync."""
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
UP, DOWN = '0066_rag_managed_collections.up.sql', '0066_rag_managed_collections.down.sql'


def run(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text(), prepare=False)


def columns(conn, table: str) -> set[str]:
    return {r['column_name'] for r in conn.execute("""SELECT column_name FROM information_schema.columns
        WHERE table_schema='ka' AND table_name=%s""", (table,)).fetchall()}


@pytest.fixture
def at_0065(store):
    """A database rolled back to 0065 with owned and unowned folders, an indexed file and a searchable code project."""
    with store.connect() as conn:
        run(conn, DOWN)
        project = conn.execute("SELECT id FROM ka.projects LIMIT 1").fetchone()
        if project is None:
            project = conn.execute("INSERT INTO ka.projects(slug,name) VALUES ('t','T') RETURNING id").fetchone()
        project = project['id']
        conn.execute("""INSERT INTO ka.rag_workspaces(project_id,settings) VALUES (%s,'{"max_file_bytes":2000000}')
            ON CONFLICT (project_id) DO NOTHING""", (project,))
        ids = {name: conn.execute("""INSERT INTO ka.rag_collections(project_id,name,synced_revision,last_synced_at)
            VALUES (%s,%s,1,now()) RETURNING id""", (project, name)).fetchone()['id'] for name in ('documents', 'jira', 'payments')}
        paths = {}
        for name, source, owner in (('documents', 'generated-documents', 'documents'), ('jira', 'rag-data/jira', 'jira'),
                                    ('payments', 'code/payments', None), ('payments', 'docs/runbooks', None)):
            paths[source] = conn.execute("""INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind)
                VALUES (%s,%s,%s) RETURNING id""", (ids[name], source, owner)).fetchone()['id']
        job = conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,input,status,started_at,finished_at)
            VALUES (%s,'rag_collection_sync',%s,'{}','succeeded',now(),now()) RETURNING id""", (project, ids['jira'])).fetchone()['id']
        conn.execute("""INSERT INTO ka.rag_files(project_id,collection_id,path_id,path,content_hash,processing_hash,
            embedding_profile,job_id,indexed_at) VALUES (%s,%s,%s,'rag-data/jira/DEMO-1.md',repeat('a',64),repeat('b',64),'',%s,
            '2026-10-01T00:00:00Z')""", (project, ids['jira'], paths['rag-data/jira'], job))
        conn.execute("""INSERT INTO ka.repositories(project_id,slug,name,search_collection,search_error)
            VALUES (%s,'repository-1','Payments','payments','RAG service is unavailable. Retry later.')""", (project,))
        yield conn, project, ids, paths


def test_owners_and_search_columns_go_and_every_connection_stays(at_0065):
    conn, _, ids, _ = at_0065
    run(conn, UP)
    assert not {'owner_kind', 'owner_id', 'owner_key'} & columns(conn, 'rag_collection_paths')
    assert not {'search_collection', 'search_error'} & columns(conn, 'repositories')
    assert conn.execute("SELECT to_regclass('ka.rag_collection_paths_owner') AS i").fetchone()['i'] is None
    kept = {(r['name'], r['source_path']) for r in conn.execute("""SELECT c.name, p.source_path FROM ka.rag_collection_paths p
        JOIN ka.rag_collections c ON c.id=p.collection_id""").fetchall()}
    assert kept == {('documents', 'generated-documents'), ('jira', 'rag-data/jira'),
                    ('payments', 'code/payments'), ('payments', 'docs/runbooks')}
    row = conn.execute("SELECT * FROM ka.rag_collections WHERE id=%s", (ids['jira'],)).fetchone()
    assert (row['auto_sync'], row['poll_interval_minutes'], row['last_polled_at'], row['synced_revision']) == (False, 5, None, 1)


def test_indexed_files_start_in_the_log_and_cascade_with_their_folder(at_0065):
    conn, _, _, paths = at_0065
    run(conn, UP)
    log = conn.execute("SELECT * FROM ka.rag_file_log").fetchall()
    assert [(r['path'], r['status'], r['size'], r['mtime'], r['fingerprint'], r['content_hash']) for r in log] == [
        ('rag-data/jira/DEMO-1.md', 'indexed', None, None, None, 'a' * 64)]
    assert log[0]['path_id'] == paths['rag-data/jira'] and log[0]['indexed_at'] is not None
    conn.execute("DELETE FROM ka.rag_collection_paths WHERE id=%s", (paths['rag-data/jira'],))
    assert conn.execute("SELECT count(*) AS n FROM ka.rag_file_log").fetchone()['n'] == 0


def test_log_and_polling_rules_are_enforced(at_0065):
    conn, _, ids, paths = at_0065
    run(conn, UP)
    insert = "INSERT INTO ka.rag_file_log(collection_id,path_id,path,status) VALUES (%s,%s,%s,%s)"
    for values, constraint in (
            ((ids['jira'], paths['rag-data/jira'], 'rag-data/jira/DEMO-1.md', 'indexed'), 'rag_file_log_collection_id_path_key'),
            ((ids['jira'], paths['rag-data/jira'], 'rag-data/jira/x.md', 'pending'), 'rag_file_log_status_check'),
            ((ids['payments'], paths['rag-data/jira'], 'rag-data/jira/y.md', 'failed'), 'rag_file_log_path_id_collection_id_fkey')):
        with pytest.raises(Exception, match=constraint):
            with conn.transaction():
                conn.execute(insert, values)
    for minutes in (0, 1441):
        with pytest.raises(Exception, match='rag_collections_poll_interval_minutes_check'):
            with conn.transaction():
                conn.execute("UPDATE ka.rag_collections SET poll_interval_minutes=%s WHERE id=%s", (minutes, ids['jira']))


def test_rollback_restores_empty_owner_and_search_columns(at_0065):
    conn, _, ids, paths = at_0065
    run(conn, UP)
    run(conn, DOWN)
    owners = {r['source_path']: (r['owner_kind'], r['owner_id'], r['owner_key'])
              for r in conn.execute("SELECT * FROM ka.rag_collection_paths").fetchall()}
    assert owners == {source: (None, None, None) for source in paths}
    row = conn.execute("SELECT search_collection, search_error FROM ka.repositories").fetchone()
    assert (row['search_collection'], row['search_error']) == (None, None)
    assert conn.execute("SELECT to_regclass('ka.rag_file_log') AS t").fetchone()['t'] is None
    assert not {'auto_sync', 'poll_interval_minutes', 'last_polled_at'} & columns(conn, 'rag_collections')
    conn.execute("INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind) VALUES (%s,'rag-data/x','jira')", (ids['jira'],))
    with pytest.raises(Exception, match='rag_collection_paths_owner'):
        with conn.transaction():
            conn.execute("INSERT INTO ka.rag_collection_paths(collection_id,source_path,owner_kind) VALUES (%s,'rag-data/y','jira')",
                         (ids['payments'],))
```

In `services/admin-api/tests/test_rag_collection_paths_migration.py`, make
`at_0064` roll back 0066 before 0065:

```python
    with store.connect() as conn:
        run(conn, '0066_rag_managed_collections.down.sql')
        run(conn, DOWN)
```

In `services/admin-api/tests/test_rag_collections_migration.py`, make the first
line of `_seed(conn)`:

```python
    conn.execute((MIGRATIONS / '0066_rag_managed_collections.down.sql').read_text(), prepare=False)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/admin-api && uv run --frozen pytest tests/test_rag_managed_collections_migration.py -q`
Expected: FAIL, because `0066_rag_managed_collections.down.sql` does not exist.

- [ ] **Step 3: Write `db/migrations/0066_rag_managed_collections.up.sql`**

```sql
SET search_path = ka, public;

-- RAG owns the configuration: folders have no owners, and code projects no search settings.
DROP INDEX rag_collection_paths_owner;
ALTER TABLE rag_collection_paths DROP COLUMN owner_kind, DROP COLUMN owner_id, DROP COLUMN owner_key;
COMMENT ON TABLE rag_collection_paths IS 'A folder under DATA_ROOT connected to a collection in RAG; sources only write files into it.';
ALTER TABLE repositories DROP COLUMN search_collection, DROP COLUMN search_error;

-- Automatic sync per collection.
ALTER TABLE rag_collections
    ADD COLUMN auto_sync boolean NOT NULL DEFAULT false,
    ADD COLUMN poll_interval_minutes integer NOT NULL DEFAULT 5 CHECK (poll_interval_minutes BETWEEN 1 AND 1440),
    ADD COLUMN last_polled_at timestamptz;

-- What the last published sync decided for every file it found; rag_files and rag_chunks stay the index.
CREATE TABLE rag_file_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id uuid NOT NULL,
    path_id uuid NOT NULL,
    path text NOT NULL,
    size bigint CHECK (size >= 0),
    mtime double precision,
    content_hash text CHECK (length(content_hash) = 64),
    fingerprint text CHECK (length(fingerprint) = 64),
    status text NOT NULL CHECK (status IN ('indexed', 'skipped_binary', 'skipped_too_large', 'skipped_unreadable', 'failed', 'removed')),
    error text CHECK (length(error) <= 2000),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    indexed_at timestamptz,
    removed_at timestamptz,
    UNIQUE (collection_id, path),
    FOREIGN KEY (path_id, collection_id) REFERENCES rag_collection_paths(id, collection_id) ON DELETE CASCADE
);
CREATE INDEX rag_file_log_status ON rag_file_log(collection_id, status, path);
CREATE INDEX rag_file_log_path ON rag_file_log(path_id);
COMMENT ON TABLE rag_file_log IS 'Per-file sync history of a collection: size, mtime, content hash, processing fingerprint and status.';

-- Indexed files start as indexed. Size, mtime and fingerprint are unknown, so the first sync after the
-- upgrade reads each file once; unchanged content is not parsed or embedded again.
INSERT INTO rag_file_log(collection_id, path_id, path, content_hash, status, first_seen_at, last_seen_at, indexed_at)
SELECT collection_id, path_id, path, content_hash, 'indexed', indexed_at, indexed_at, indexed_at FROM rag_files;
```

- [ ] **Step 4: Write `db/migrations/0066_rag_managed_collections.down.sql`**

```sql
SET search_path = ka, public;

DROP TABLE rag_file_log;
ALTER TABLE rag_collections DROP COLUMN last_polled_at, DROP COLUMN poll_interval_minutes, DROP COLUMN auto_sync;

-- The columns come back empty: no folder has an owner and no code project is searchable.
ALTER TABLE repositories
    ADD COLUMN search_collection text CHECK (search_collection ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND length(search_collection) <= 60),
    ADD COLUMN search_error text CHECK (length(search_error) <= 2000);
ALTER TABLE rag_collection_paths
    ADD COLUMN owner_kind text CHECK (owner_kind IN ('documents', 'jira', 'code_project')),
    ADD COLUMN owner_id uuid,
    ADD COLUMN owner_key text CHECK (length(owner_key) BETWEEN 1 AND 500),
    ADD CONSTRAINT rag_collection_paths_check CHECK (owner_kind IS NOT NULL OR (owner_id IS NULL AND owner_key IS NULL));
CREATE UNIQUE INDEX rag_collection_paths_owner ON rag_collection_paths(owner_kind, owner_id, owner_key)
    NULLS NOT DISTINCT WHERE owner_kind IS NOT NULL;
COMMENT ON TABLE rag_collection_paths IS 'A folder under DATA_ROOT indexed into a collection, optionally owned by the source that writes it.';
```

- [ ] **Step 5: Pin the rolled-back version**

In `db/tests/run.sh`, change `[ "$rolled_back_version" = 64 ]` to
`[ "$rolled_back_version" = 65 ]`.

- [ ] **Step 6: Run the migration tests, both service suites and the database suite**

Run:
```bash
cd services/admin-api && uv run --frozen pytest tests/test_rag_managed_collections_migration.py \
  tests/test_rag_collection_paths_migration.py tests/test_rag_collections_migration.py -q
uv run --frozen pytest -q
cd ../rag && uv run --frozen pytest -q
cd ../.. && bash db/tests/run.sh
```
Expected:
- 4 + 5 + 3 passed;
- the admin-api and RAG suites pass, since no code reads the dropped columns
  after Tasks 1–2;
- every `db/tests/run.sh` check prints PASS.

- [ ] **Step 7: Commit**

```bash
git add db/migrations/0066_rag_managed_collections.*.sql db/tests/run.sh \
  services/admin-api/tests/test_rag_managed_collections_migration.py \
  services/admin-api/tests/test_rag_collection_paths_migration.py services/admin-api/tests/test_rag_collections_migration.py
git commit -m "feat(db): 0066 drops folder owners and code-project search; adds the file log and auto-sync columns

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
# Part D — RAG file log and polling (Tasks 4–6)

### Task 4: File log — sync decides per file, one bad file never fails the sync

**Files:**
- Create: `services/rag/src/rag/file_log.py` (full code below)
- Modify: `services/rag/src/rag/workspace_worker.py`: replace everything from `STATS` through the end of `process()` (code below). `claim`, `heartbeat`, `CollectionSettings`, `decode`, `parse_source` and `main` stay.
- Test: `services/rag/tests/test_file_log.py` (new)

**Interfaces:**
- Consumes:
  - from Task 3, `ka.rag_file_log`;
  - from Task 1, `DELETE collections/{id}/paths/{path_id}` and the test helpers
    `collection`, `sync`, `update`, `write`, `chunks`, `configure` and `search`.
- Produces, in `rag.file_log`:
  - `STATUSES`, `GROUPS: dict[str, tuple[str, ...]]` (`indexed`, `skipped`,
    `failed`, `removed`) and `PRESENT` (every status but `removed`);
  - `fingerprint(processing, profile, include, exclude, max_file_bytes) -> str`;
  - `load(conn, collection_id) -> dict[path, row]`;
  - `unchanged(entry, row, current) -> bool`;
  - `record(conn, collection_id, path_ids, decisions, current) -> int` (rows
    marked removed).
- Produces, in `rag.workspace_worker`:
  - `STATS` with today's keys plus `unopened_files` and `failed_files`;
  - `decide(workspace, walk, log, current, limit, force, stats) -> (decisions, sources)`.
    A decision is `{source, size, mtime, content_hash, status, error}`, keyed by
    full path;
  - `recheck(workspace, collection, sources, walk, decisions, limit) -> None`;
  - `process()` keeps its signature and return value (stats, or None on failure).

- [ ] **Step 1: Write the failing tests**

Create `services/rag/tests/test_file_log.py`:

```python
"""The file log: unchanged files are not opened, one bad file never fails a sync, every decision is visible."""
import os
import time

import pytest
from test_workspace import chunks, collection, configure, search, sync, update, write

from rag import workspace_worker

# env and workspace_postgres come from conftest.py


def log(env, collection_id) -> dict[str, dict]:
    with env[0].connect() as conn:
        return {r["path"]: r for r in conn.execute("SELECT * FROM ka.rag_file_log WHERE collection_id=%s",
                                                   (collection_id,)).fetchall()}


def spy_reads(env, monkeypatch) -> list[str]:
    """Record every file the worker opens, as a full DATA_ROOT path."""
    reads, original = [], env[0].areas.read

    def read(area, path, limit):
        reads.append(f"{area}/{path}")
        return original(area, path, limit)
    monkeypatch.setattr(env[0].areas, "read", read)
    return reads


def set_limit(env, max_file_bytes):
    client, prefix = env[1], env[3]
    config = client.get(prefix + "/settings").json()
    saved = client.put(prefix + "/settings", json={"revision": config["revision"], "settings": {"max_file_bytes": max_file_bytes},
                                                   "embedding_model_id": config["embedding_model_id"]})
    assert saved.status_code == 200, saved.text


def test_unchanged_files_are_not_opened(env, monkeypatch):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env)
    write(env, "other.md", "# Other\n\nSecond file.")
    first, _ = sync(env, created)
    assert first["hashed_files"] == 2 and first["parsed_files"] == 2
    entries = log(env, created)
    readme = entries["rag-data/docs/readme.md"]
    assert readme["status"] == "indexed" and readme["error"] is None and readme["indexed_at"] is not None
    assert readme["size"] == (folder / "readme.md").stat().st_size and readme["mtime"] == (folder / "readme.md").stat().st_mtime
    assert len(readme["fingerprint"]) == 64 and len(readme["content_hash"]) == 64
    reads = spy_reads(env, monkeypatch)
    second, row = sync(env, created)
    assert reads == []
    assert (second["unopened_files"], second["skipped_files"], second["hashed_files"]) == (2, 2, 0)
    assert row["status"] == "up_to_date" and row["indexed_files"] == 2
    assert log(env, created)["rag-data/docs/readme.md"]["last_seen_at"] > readme["last_seen_at"]


def test_touched_file_with_the_same_content_is_not_reparsed(env, monkeypatch):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env)
    sync(env, created)
    later = time.time() + 5
    os.utime(folder / "readme.md", (later, later))
    monkeypatch.setattr(workspace_worker, "parse_source", lambda *a: pytest.fail("The same content must not be parsed"))
    stats, _ = sync(env, created)
    assert (stats["hashed_files"], stats["parsed_files"], stats["skipped_files"], stats["unopened_files"]) == (1, 0, 1, 0)
    assert log(env, created)["rag-data/docs/readme.md"]["mtime"] == (folder / "readme.md").stat().st_mtime


def test_changed_content_is_reindexed_even_with_an_older_mtime(env):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env, content="# Payments\n\nRetries use exponential backoff.")
    sync(env, created)
    readme = folder / "readme.md"
    old = readme.stat().st_mtime - 86400
    readme.write_text("# Payments\n\nRetries use a fixed 30s backoff.")
    os.utime(readme, (old, old))  # as rsync -a or cp -p leave it
    stats, row = sync(env, created)
    assert stats["parsed_files"] == 1 and row["indexed_files"] == 1
    assert "fixed 30s" in chunks(env, created)[0]


def test_settings_change_reprocesses_unchanged_files(env):
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    sync(env, created)
    update(env, created, chunk_size=1000)
    stats, _ = sync(env, created)
    assert stats["unopened_files"] == 0 and stats["parsed_files"] == 1
    update(env, created, include=["**/*.md", "**/*.txt", "**/*.json"])
    stats, _ = sync(env, created)  # new patterns: read again; the indexed content is unchanged, so not parsed
    assert (stats["unopened_files"], stats["hashed_files"], stats["parsed_files"]) == (0, 1, 0)


def test_raising_the_size_limit_reprocesses_skipped_files(env):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    (folder / "big.md").write_text("# Big\n\n" + "x" * 600)
    set_limit(env, 500)
    created = collection(env)
    stats, row = sync(env, created)
    assert stats["skipped_large"] == 1 and row["indexed_files"] == 0
    assert log(env, created)["rag-data/docs/big.md"]["status"] == "skipped_too_large"
    set_limit(env, 5000)  # the file itself is untouched
    stats, row = sync(env, created)
    assert stats["parsed_files"] == 1 and row["indexed_files"] == 1
    assert log(env, created)["rag-data/docs/big.md"]["status"] == "indexed"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files without permission")
def test_binary_large_and_unreadable_files_are_logged(env):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    (folder / "ok.md").write_text("# OK\n\nRetries.")
    (folder / "bin.md").write_bytes(b"\x00\x01binary")
    (folder / "big.md").write_text("x" * 600)
    locked = folder / "locked.md"
    locked.write_text("# Locked\n\nPermissions.")
    locked.chmod(0)
    set_limit(env, 500)
    created = collection(env)
    try:
        stats, row = sync(env, created)
        assert row["job"]["status"] == "succeeded" and row["status"] == "up_to_date" and row["indexed_files"] == 1
        entries = log(env, created)
        assert {path.rsplit("/", 1)[1]: entry["status"] for path, entry in entries.items()} == {
            "ok.md": "indexed", "bin.md": "skipped_binary", "big.md": "skipped_too_large", "locked.md": "skipped_unreadable"}
        assert entries["rag-data/docs/locked.md"]["error"].startswith("Cannot read this file")
        assert (stats["skipped_binary"], stats["skipped_large"], stats["skipped_unreadable"]) == (1, 1, 1)
        locked.chmod(0o644)  # fixing permissions does not change the mtime
        stats, row = sync(env, created)
        assert log(env, created)["rag-data/docs/locked.md"]["status"] == "indexed" and row["indexed_files"] == 2
    finally:
        locked.chmod(0o644)


def test_parse_failure_is_logged_and_the_sync_succeeds(env, monkeypatch):
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env, "good.md", "# Good\n\nretries are fine")
    write(env, "bad.md", "# Bad\n\nretries crash the parser")
    parse = workspace_worker.parse_source

    def broken(path, text, settings):
        if path.endswith("bad.md"):
            raise ValueError("unbalanced table")
        return parse(path, text, settings)
    monkeypatch.setattr(workspace_worker, "parse_source", broken)
    stats, row = sync(env, created)
    assert (stats["failed_files"], stats["parsed_files"]) == (1, 1)
    assert row["job"]["status"] == "succeeded" and row["status"] == "up_to_date" and row["indexed_files"] == 1
    bad = log(env, created)["rag-data/docs/bad.md"]
    assert (bad["status"], bad["error"], bad["indexed_at"]) == ("failed", "Could not parse this file: unbalanced table", None)
    assert {hit["path"] for hit in search(env, "retries", ["docs"]).json()["results"]} == {"rag-data/docs/good.md"}
    monkeypatch.setattr(workspace_worker, "parse_source", parse)
    stats, _ = sync(env, created)  # same content and settings: the failure is not retried
    assert stats["failed_files"] == 0 and log(env, created)["rag-data/docs/bad.md"]["status"] == "failed"
    stats, row = sync(env, created, force=True)  # Rebuild retries it
    assert log(env, created)["rag-data/docs/bad.md"]["status"] == "indexed" and row["indexed_files"] == 2


def test_a_deleted_file_is_marked_removed_and_returns_when_restored(env):
    folder = env[6]["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    write(env, "a.md", "# A")
    write(env, "b.md", "# B")
    sync(env, created)
    (folder / "b.md").unlink()
    stats, row = sync(env, created)
    gone = log(env, created)["rag-data/docs/b.md"]
    assert gone["status"] == "removed" and gone["removed_at"] is not None and gone["indexed_at"] is None
    assert stats["removed_entries"] == 1 and row["indexed_files"] == 1
    write(env, "b.md", "# B again")
    sync(env, created)
    back = log(env, created)["rag-data/docs/b.md"]
    assert back["status"] == "indexed" and back["removed_at"] is None


def test_rebuild_rereads_every_file(env, monkeypatch):
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env, "a.md", "# A")
    write(env, "b.md", "# B")
    sync(env, created)
    reads = spy_reads(env, monkeypatch)
    stats, _ = sync(env, created, force=True)
    assert sorted(reads) == ["rag-data/docs/a.md", "rag-data/docs/b.md"]
    assert stats["parsed_files"] == 2 and stats["unopened_files"] == 0


def test_a_failed_sync_leaves_the_log_unchanged(env):
    state = env[4]
    (env[6]["rag-data"] / "docs").mkdir()
    configure(env)
    created = collection(env, embeddings_enabled=True)
    write(env)
    sync(env, created)
    before = log(env, created)["rag-data/docs/readme.md"]
    write(env, content="# Payments\n\nA retry policy that cannot be embedded right now.")
    state["fail"] = True
    stats, row = sync(env, created)
    assert stats is None and row["job"]["status"] == "failed"
    after = log(env, created)["rag-data/docs/readme.md"]
    assert (after["content_hash"], after["mtime"], after["last_seen_at"]) == (before["content_hash"], before["mtime"], before["last_seen_at"])


def test_files_backfilled_without_size_and_mtime_are_read_once_and_not_parsed(env, monkeypatch):
    """Migration 0066 backfills indexed files with no size, mtime or fingerprint."""
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    write(env)
    sync(env, created)
    with env[0].connect() as conn:
        conn.execute("UPDATE ka.rag_file_log SET size=NULL,mtime=NULL,fingerprint=NULL WHERE collection_id=%s", (created,))
    monkeypatch.setattr(workspace_worker, "parse_source", lambda *a: pytest.fail("Backfilled files must not be parsed again"))
    stats, row = sync(env, created)
    assert (stats["hashed_files"], stats["parsed_files"]) == (1, 0) and row["status"] == "up_to_date"
    assert log(env, created)["rag-data/docs/readme.md"]["size"] is not None
    assert sync(env, created)[0]["unopened_files"] == 1


def test_removing_a_folder_or_the_collection_deletes_its_log_rows(env):
    client, prefix, roots = env[1], env[3], env[6]
    for name in ("a", "b"):
        (roots["rag-data"] / name).mkdir()
        (roots["rag-data"] / name / "x.md").write_text(f"# {name}\n\nretries {name}")
    created = collection(env, "both", "rag-data/a", "rag-data/b")
    _, row = sync(env, created)
    path_id = next(p["id"] for p in row["paths"] if p["source_path"] == "rag-data/b")
    assert client.delete(prefix + f"/collections/{created}/paths/{path_id}").status_code == 200
    assert set(log(env, created)) == {"rag-data/a/x.md"}
    revision = client.get(prefix + f"/collections/{created}").json()["revision"]
    assert client.delete(prefix + f"/collections/{created}", params={"revision": revision, "confirmed": True}).status_code == 200
    assert log(env, created) == {}
    assert (roots["rag-data"] / "b" / "x.md").is_file()
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest tests/test_file_log.py -q`
Expected: FAIL. The log stays empty (`KeyError` on `rag-data/docs/readme.md`) and
`unopened_files` is not a stats key.

- [ ] **Step 3: Create `services/rag/src/rag/file_log.py`**

```python
"""The per-file sync log: what the last published sync decided for every file it found.

`rag_files` and `rag_chunks` stay the search index; the log is the history behind it. A file whose
size, mtime and processing fingerprint equal its log row is not opened again.
"""
from rag.workspace import digest

STATUSES = ("indexed", "skipped_binary", "skipped_too_large", "skipped_unreadable", "failed", "removed")
GROUPS = {"indexed": ("indexed",), "skipped": ("skipped_binary", "skipped_too_large", "skipped_unreadable"),
          "failed": ("failed",), "removed": ("removed",)}
PRESENT = tuple(status for status in STATUSES if status != "removed")
# Fixing permissions does not change a file's mtime, so unreadable files are always tried again.
RETRIED = ("removed", "skipped_unreadable")

UPSERT = """INSERT INTO ka.rag_file_log(collection_id,path_id,path,size,mtime,content_hash,fingerprint,status,error)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (collection_id,path) DO UPDATE SET
    path_id=EXCLUDED.path_id,size=EXCLUDED.size,mtime=EXCLUDED.mtime,content_hash=EXCLUDED.content_hash,
    fingerprint=EXCLUDED.fingerprint,status=EXCLUDED.status,error=EXCLUDED.error,last_seen_at=now(),removed_at=NULL"""


def fingerprint(processing: str, profile: str, include: list[str], exclude: list[str], max_file_bytes: int) -> str:
    """Everything besides a file's content that decides how it is processed."""
    return digest({"processing": processing, "profile": profile, "include": list(include),
                   "exclude": list(exclude), "max_file_bytes": max_file_bytes})


def load(conn, collection_id) -> dict[str, dict]:
    return {row["path"]: row for row in conn.execute(
        "SELECT * FROM ka.rag_file_log WHERE collection_id=%s", (collection_id,)).fetchall()}


def unchanged(entry: dict, row: dict | None, current: str) -> bool:
    """The walk entry matches its log row exactly, so the file need not be opened."""
    return (row is not None and row["status"] not in RETRIED and row["fingerprint"] == current
            and row["size"] == entry["size"] and row["mtime"] == entry["mtime"])


def record(conn, collection_id, path_ids: dict, decisions: dict[str, dict], current: str) -> int:
    """Write a sync's decisions, mark files it did not see removed and copy index times from rag_files.

    Runs in the publishing transaction, so a failed sync leaves the log as it was. Returns how many
    rows became removed.
    """
    if decisions:
        with conn.cursor() as cursor:
            cursor.executemany(UPSERT, [(collection_id, path_ids[d["source"]], path, d["size"], d["mtime"],
                                         d["content_hash"], current, d["status"], d["error"])
                                        for path, d in decisions.items()])
    removed = conn.execute("""UPDATE ka.rag_file_log SET status='removed',removed_at=now(),error=NULL
        WHERE collection_id=%s AND status<>'removed' AND NOT (path = ANY(%s))""", (collection_id, list(decisions))).rowcount
    conn.execute("""UPDATE ka.rag_file_log l SET indexed_at=CASE WHEN l.status='indexed' THEN
        (SELECT f.indexed_at FROM ka.rag_files f WHERE f.collection_id=l.collection_id AND f.path=l.path) END
        WHERE l.collection_id=%s""", (collection_id,))
    return removed
```

- [ ] **Step 4: Sync with the log in `services/rag/src/rag/workspace_worker.py`**

Update the module docstring and imports:

```python
"""Durable incremental sync of one collection, published in one transaction.

Every file the walk finds gets a decision in the collection's file log. A file whose size, mtime and
processing fingerprint equal its log row is not opened; one whose content hash and fingerprint match
is not parsed. A file that cannot be read or parsed is logged and the sync continues. Collection-level
problems (a missing folder, limits, a settings change, an unavailable embedding model) fail the sync,
publish nothing and leave the log unchanged.
"""

import json
import logging
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from rag import file_log
from rag.chunking import chunk_sections
from rag.collections import MAX_BYTES, Walk
from rag.structure import Section, parse_structure
from rag.workspace import Settings, Workspace, digest
from rag.workspace_models import embed, embedding_profile
```

Replace `STATS` and `snapshot()` with:

```python
STATS = ("hashed_files", "skipped_files", "unopened_files", "parsed_files", "failed_files", "embedded_chunks",
         "reused_vectors", "removed_entries", "skipped_binary", "skipped_large", "skipped_links", "skipped_special",
         "skipped_unreadable", "skipped_invalid_names")
CHANGED = "Files changed while syncing; refresh the collection again"
```

and add, after `parse_source()`:

```python
def read(workspace: Workspace, path: str, limit: int) -> bytes:
    area, _, rest = path.partition("/")
    return workspace.areas.read(area, rest, limit)


def decide(workspace: Workspace, walk: Walk, log: dict, current: str, limit: int, force: bool,
           stats: dict) -> tuple[dict[str, dict], list[dict]]:
    """The log decision for every walked file, and the text files that need processing.

    A file matching its log row (size, mtime, fingerprint) is not opened; a file whose content hash and
    fingerprint match its row is not parsed. Rebuild (`force`) reads and processes every file.
    """
    decisions, sources, total = {}, [], 0
    for entry in walk.files:
        path, old = entry["path"], log.get(entry["path"])
        decision = {"source": entry["source"], "size": entry["size"], "mtime": entry["mtime"],
                    "content_hash": None, "status": "indexed", "error": None}
        if not force and file_log.unchanged(entry, old, current):
            decision.update(content_hash=old["content_hash"], status=old["status"], error=old["error"])
            decisions[path] = decision
            stats["skipped_files"] += 1
            stats["unopened_files"] += 1
            continue
        if entry["size"] > limit:
            decisions[path] = {**decision, "status": "skipped_too_large"}
            stats["skipped_large"] += 1
            continue
        try:
            data = read(workspace, path, limit)
        except FileNotFoundError:
            continue  # deleted after the walk; the publish re-check fails the sync
        except (OSError, HTTPException) as error:
            message = error.detail if isinstance(error, HTTPException) else f"Cannot read this file: {error.strerror or error}"
            decisions[path] = {**decision, "status": "skipped_unreadable", "error": str(message)[:2000]}
            stats["skipped_unreadable"] += 1
            continue
        total += len(data)
        if total > MAX_BYTES:
            raise HTTPException(400, "A sync is limited to 200 MB of text; narrow the collection's include patterns")
        decision["content_hash"] = digest(data)
        decisions[path] = decision
        stats["hashed_files"] += 1
        text = decode(data)
        if text is None:
            decision["status"] = "skipped_binary"
            stats["skipped_binary"] += 1
        elif (not force and old is not None and old["status"] != "removed"
              and old["content_hash"] == decision["content_hash"] and old["fingerprint"] == current):
            decision.update(status=old["status"], error=old["error"])  # touched, same content: not parsed
            stats["skipped_files"] += 1
        else:
            sources.append({"path": path, "source": entry["source"], "text": text, "content_hash": decision["content_hash"]})
    return decisions, sources


def recheck(workspace: Workspace, collection: dict, sources: list[str], walk: Walk, decisions: dict, limit: int) -> None:
    """Refuse to publish when a file changed after the first walk.

    Sizes and mtimes are compared with the first walk; only files whose size or mtime moved are read
    again and hashed against the hash this sync used (the logged hash for files it did not open).
    """
    again = workspace.areas.walk_paths(sources, collection["include"], collection["exclude"])
    before = {entry["path"]: (entry["size"], entry["mtime"]) for entry in walk.files}
    after = {entry["path"]: (entry["size"], entry["mtime"]) for entry in again.files}
    if before.keys() != after.keys():
        raise HTTPException(409, CHANGED)
    for path, stat in after.items():
        if stat == before[path]:
            continue
        used = decisions.get(path, {}).get("content_hash")
        try:
            now = digest(read(workspace, path, limit))
        except (OSError, HTTPException):
            now = None
        if used is None or now != used:
            raise HTTPException(409, CHANGED)
```

Replace `process()` with:

```python
def process(workspace: Workspace, job: dict, transport=None):
    stats = dict.fromkeys(STATS, 0)
    project = job["project_id"]
    force = bool(job["input"].get("force"))
    try:
        with workspace.connect() as conn:
            config = workspace.settings(conn, project, lock=False)
            limit = Settings(**config["settings"]).max_file_bytes
            try:
                collection = workspace.collection(conn, project, job["rag_collection_id"])
            except HTTPException:
                raise HTTPException(409, "Collection was deleted") from None
            revision = job.get("collection_revision", collection["revision"])
            if collection["revision"] != revision:
                raise HTTPException(409, "Settings changed while syncing; refresh the collection again")
            path_ids = {row["source_path"]: row["id"] for row in workspace.paths(conn, collection["id"])}
            model = workspace.embedding_model(conn, config["embedding_model_id"]) if collection["embeddings_enabled"] else None
            existing = conn.execute("SELECT * FROM ka.rag_files WHERE collection_id=%s", (collection["id"],)).fetchall()
            log = file_log.load(conn, collection["id"])
        settings = CollectionSettings(chunk_size=collection["chunk_size"], chunk_overlap=collection["chunk_overlap"])
        processing = digest({"parser": "markdown-text-json-v3", "chunker": "sections-v1",
                             "size": settings.chunk_size, "overlap": settings.chunk_overlap})
        profile = embedding_profile(model)
        log_fingerprint = file_log.fingerprint(processing, profile, collection["include"], collection["exclude"], limit)
        walk = workspace.areas.walk_paths(list(path_ids), collection["include"], collection["exclude"])  # FolderMissing, limits
        for key in ("links", "special", "unreadable", "invalid_names"):
            stats["skipped_" + key] = walk.skipped[key]
        decisions, sources = decide(workspace, walk, log, log_fingerprint, limit, force, stats)
        previous = {row["path"]: row for row in existing}
        updates, vectors, reused = [], {}, set()
        for source in sources:
            old = previous.get(source["path"])
            same = (old is not None and old["content_hash"] == source["content_hash"]
                    and old["processing_hash"] == processing and not force)
            if same and old["embedding_profile"] == profile:
                stats["skipped_files"] += 1
                continue
            if same:
                with workspace.connect() as conn:
                    texts = [row["text"] for row in conn.execute("SELECT text FROM ka.rag_chunks WHERE file_id=%s ORDER BY position", (old["id"],)).fetchall()]
            else:
                try:
                    texts = parse_source(source["path"], source["text"], settings)
                except Exception as error:  # noqa: BLE001 - one file that cannot be parsed never fails the collection
                    decisions[source["path"]].update(status="failed", error=f"Could not parse this file: {error}"[:2000])
                    stats["failed_files"] += 1
                    continue
                stats["parsed_files"] += 1
            chunks = [{"text": text, "input_hash": digest(text)} for text in texts]
            heartbeat(workspace, job, stats)
            if profile:
                missing = {}
                with workspace.connect() as conn:
                    for chunk in chunks:
                        value = chunk["input_hash"]
                        if value in vectors:
                            stats["reused_vectors"] += 1
                        elif not force and conn.execute("SELECT 1 FROM ka.rag_vectors WHERE project_id=%s AND profile=%s AND input_hash=%s", (project, profile, value)).fetchone():
                            stats["reused_vectors"] += 1
                            reused.add(value)
                        else:
                            missing[value] = chunk["text"]
                pending = list(missing.items())
                # Titan takes one text per request; renew the lease between requests.
                batch_size = 1 if model.provider == 'bedrock' else 32
                for offset in range(0, len(pending), batch_size):
                    batch = pending[offset:offset + batch_size]
                    values = embed(model, [text for _, text in batch], transport)  # a model failure fails the sync
                    vectors.update(zip([value for value, _ in batch], values, strict=True))
                    stats["embedded_chunks"] += len(values)
                    heartbeat(workspace, job, stats)
            updates.append((source, chunks))
        # The collection row lock serializes publication with collection edits and
        # deletion. A second walk catches edits made directly on the host.
        with workspace.connect() as conn:
            current = conn.execute("SELECT revision FROM ka.rag_collections WHERE id=%s AND deleted_at IS NULL FOR UPDATE", (collection["id"],)).fetchone()
            running = conn.execute("SELECT id FROM ka.jobs WHERE id=%s AND status='running' AND lease_token=%s AND lease_expires_at>now() FOR UPDATE", (job["id"], job["lease_token"])).fetchone()
            if running is None:
                raise HTTPException(409, "Sync was cancelled or its lease expired")
            # The workspace revision also moves when the file size limit or the embedding model changes.
            # Every publish and collection deletion takes the workspace lock before
            # vector cleanup, so reused vectors checked here stay until commit.
            settings_revision = workspace.settings(conn, project, lock=True)["revision"]
            if current is None or current["revision"] != revision or settings_revision != config["revision"]:
                raise HTTPException(409, "Settings changed while syncing; refresh the collection again")
            if reused:
                present = conn.execute("SELECT count(*) AS n FROM ka.rag_vectors WHERE project_id=%s AND profile=%s AND input_hash=ANY(%s)",
                                       (project, profile, list(reused))).fetchone()["n"]
                if present != len(reused):
                    raise HTTPException(409, "Embeddings changed while syncing; refresh the collection again")
            recheck(workspace, collection, list(path_ids), walk, decisions, limit)
            for value, vector in vectors.items():
                conn.execute("INSERT INTO ka.rag_vectors(project_id,profile,input_hash,embedding) VALUES (%s,%s,%s,%s::public.vector) ON CONFLICT (project_id,profile,input_hash) DO UPDATE SET embedding=EXCLUDED.embedding", (project, profile, value, json.dumps(vector)))
            for source, chunks in updates:
                row = conn.execute("""INSERT INTO ka.rag_files(project_id,collection_id,path_id,path,content_hash,processing_hash,embedding_profile,job_id)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (collection_id,path) DO UPDATE
                                    SET path_id=EXCLUDED.path_id,content_hash=EXCLUDED.content_hash,processing_hash=EXCLUDED.processing_hash,
                                    embedding_profile=EXCLUDED.embedding_profile,job_id=EXCLUDED.job_id,indexed_at=now() RETURNING id""",
                                   (project, collection["id"], path_ids[source["source"]], source["path"], source["content_hash"],
                                    processing, profile, job["id"])).fetchone()
                conn.execute("DELETE FROM ka.rag_chunks WHERE file_id=%s", (row["id"],))
                for position, chunk in enumerate(chunks):
                    conn.execute("INSERT INTO ka.rag_chunks(project_id,file_id,position,text,input_hash) VALUES (%s,%s,%s,%s,%s)", (project, row["id"], position, chunk["text"], chunk["input_hash"]))
            # Only files whose decision is indexed stay searchable; skipped and failed files leave the index.
            indexed = {path for path, decision in decisions.items() if decision["status"] == "indexed"}
            for old in existing:
                if old["path"] not in indexed:
                    conn.execute("DELETE FROM ka.rag_files WHERE id=%s", (old["id"],))
                    stats["removed_entries"] += 1
            file_log.record(conn, collection["id"], path_ids, decisions, log_fingerprint)
            conn.execute("""DELETE FROM ka.rag_vectors v WHERE v.project_id=%s AND NOT EXISTS (
                            SELECT 1 FROM ka.rag_chunks c JOIN ka.rag_files f ON f.id=c.file_id
                            WHERE c.project_id=v.project_id AND c.input_hash=v.input_hash AND f.embedding_profile=v.profile)""", (project,))
            conn.execute("UPDATE ka.rag_collections SET last_synced_at=now(),synced_revision=%s WHERE id=%s", (revision, collection["id"]))
            conn.execute("""UPDATE ka.jobs SET status='succeeded',finished_at=now(),result=%s,
                         worker_id=NULL,lease_token=NULL,lease_expires_at=NULL WHERE id=%s""", (Jsonb(stats), job["id"]))
        return stats
    except Exception as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else "Sync failed; the previous index remains available. Check the collection's files and service health."
        with workspace.connect() as conn:
            conn.execute("""UPDATE ka.jobs SET status='failed',finished_at=now(),error=%s,result=%s,
                         worker_id=NULL,lease_token=NULL,lease_expires_at=NULL
                         WHERE id=%s AND status='running' AND lease_token=%s""", (message, Jsonb(stats), job["id"], job["lease_token"]))
        return None
```

The differences from today are:
- the walk and `decide()` replace `snapshot()`;
- `processing` replaces the local name `fingerprint`;
- the `parse_source` try/except;
- `recheck()` replaces the manifest comparison;
- `indexed` replaces `retained`;
- `file_log.record()` runs before vector cleanup.

- [ ] **Step 5: Run the file log tests and the RAG suite**

Run: `cd services/rag && uv run --frozen pytest tests/test_file_log.py -q && uv run --frozen pytest -q`
Expected: 12 passed in `test_file_log.py` (11 when tests run as root); then the
whole RAG suite passes. In `test_sync.py`, `test_cancel_and_external_edit_cannot_publish`
still fails its sync with `Files changed`, and
`test_unchanged_files_are_not_parsed_and_deletions_are_published` still sees
`skipped_files == 2`.

- [ ] **Step 6: Commit**

```bash
git add services/rag/src/rag/file_log.py services/rag/src/rag/workspace_worker.py services/rag/tests/test_file_log.py
git commit -m "feat(rag): per-file sync log; unchanged files are not opened; one bad file no longer fails a sync

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: File log API and status counts

**Files:**
- Modify: `services/rag/src/rag/workspace_api.py`: `contains()` helper, `LOG_COUNTS_SQL`, `log_counts` in both summaries, `GET collections/{id}/log`.
- Test: `services/rag/tests/test_file_log.py` (append)

**Interfaces:**
- Consumes: from Task 4, `file_log.GROUPS` and `file_log.PRESENT`.
- Produces:
  - `GET collections/{id}/log?status=&q=&limit=&offset=` → `{files: [{path,
    status, size, mtime, error, indexed_at, last_seen_at, removed_at, chunks}],
    total}`;
  - `log_counts: {indexed, skipped, failed}` in `GET collections`,
    `GET collections?status=false`, `GET collections/{id}` and the summaries
    returned by create and folder changes;
  - `contains(text) -> str`, a LIKE pattern that matches `text` literally (the
    jobs search uses it too).

- [ ] **Step 1: Write the failing test**

Append to `services/rag/tests/test_file_log.py` (add `from uuid import uuid4` to the imports):

```python
def test_log_api_filters_searches_and_pages(env):
    client, prefix, roots = env[1], env[3], env[6]
    folder = roots["rag-data"] / "docs"
    folder.mkdir()
    created = collection(env)
    for name in ("a", "b", "c", "d", "e"):
        write(env, f"{name}.md", f"# {name.upper()}\n\nText {name}.")
    (folder / "bin.md").write_bytes(b"\x00binary")
    sync(env, created)
    (folder / "e.md").unlink()
    sync(env, created)
    get = lambda **params: client.get(prefix + f"/collections/{created}/log", params=params)
    default = get().json()
    assert default["total"] == 5
    assert [f["path"].rsplit("/", 1)[1] for f in default["files"]] == ["a.md", "b.md", "bin.md", "c.md", "d.md"]
    first = default["files"][0]
    assert first["status"] == "indexed" and first["chunks"] >= 1 and first["indexed_at"] and first["size"] == (folder / "a.md").stat().st_size
    assert [f["path"] for f in get(status="skipped").json()["files"]] == ["rag-data/docs/bin.md"]
    removed = get(status="removed").json()["files"]
    assert [f["path"] for f in removed] == ["rag-data/docs/e.md"] and removed[0]["chunks"] == 0
    assert get(status="failed").json() == {"files": [], "total": 0}
    page = get(limit=2, offset=2).json()
    assert page["total"] == 5 and [f["path"].rsplit("/", 1)[1] for f in page["files"]] == ["bin.md", "c.md"]
    assert [f["path"] for f in get(q="B").json()["files"]] == ["rag-data/docs/b.md", "rag-data/docs/bin.md"]
    assert get(q="%").json()["total"] == 0  # LIKE wildcards match literally
    for bad in ({"status": "pending"}, {"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 201}):
        assert get(**bad).status_code == 400, bad
    assert client.get(prefix + f"/collections/{uuid4()}/log").status_code == 404
    assert client.get(prefix + f"/collections/{created}").json()["log_counts"] == {"indexed": 4, "skipped": 1, "failed": 0}
    summary = client.get(prefix + "/collections", params={"status": "false"}).json()["collections"][0]
    assert summary["log_counts"] == {"indexed": 4, "skipped": 1, "failed": 0}
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd services/rag && uv run --frozen pytest tests/test_file_log.py::test_log_api_filters_searches_and_pages -q`
Expected: FAIL with 404 for `GET collections/{id}/log`.

- [ ] **Step 3: Add the log API to `services/rag/src/rag/workspace_api.py`**

Import `from rag import file_log`. Below `ORPHAN_VECTORS_SQL` add:

```python
LOG_COUNTS_SQL = """SELECT count(*) FILTER (WHERE status='indexed') AS indexed,
    count(*) FILTER (WHERE status IN ('skipped_binary','skipped_too_large','skipped_unreadable')) AS skipped,
    count(*) FILTER (WHERE status='failed') AS failed
    FROM ka.rag_file_log WHERE collection_id=%s"""


def contains(text: str) -> str:
    """A LIKE pattern matching `text` anywhere, with LIKE wildcards escaped so they match literally."""
    return "%" + text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
```

In `collection_summary()` and `collection_status()`, add
`"log_counts": conn.execute(LOG_COUNTS_SQL, (row["id"],)).fetchone()` to the
returned dict.

In `jobs()`, replace the inline pattern with `pattern = contains(q)`.

After `collection_chunks`, add:

```python
    @router.get(prefix + "/collections/{collection_id}/log")
    def collection_log(project: UUID, collection_id: UUID, status: Literal["indexed", "skipped", "failed", "removed"] | None = None,
                       q: str | None = Query(None, max_length=200), limit: int = Query(25, ge=1, le=100),
                       offset: int = Query(0, ge=0)):
        """The collection's file log by path; without `status`, every file except removed ones."""
        where = ["l.collection_id=%s", "l.status = ANY(%s)"]
        params: list = [collection_id, list(file_log.GROUPS[status] if status else file_log.PRESENT)]
        if q and q.strip():
            where.append("l.path ILIKE %s")
            params.append(contains(q))
        scope = "FROM ka.rag_file_log l WHERE " + " AND ".join(where)
        with workspace.connect() as conn:
            workspace.collection(conn, project, collection_id)
            total = conn.execute("SELECT count(*) AS n " + scope, params).fetchone()["n"]
            files = conn.execute("""SELECT l.path,l.status,l.size,l.mtime,l.error,l.indexed_at,l.last_seen_at,l.removed_at,
                (SELECT count(*) FROM ka.rag_files f JOIN ka.rag_chunks c ON c.file_id=f.id
                 WHERE f.collection_id=l.collection_id AND f.path=l.path) AS chunks """ + scope
                + ' ORDER BY l.path COLLATE "C" LIMIT %s OFFSET %s', [*params, limit, offset]).fetchall()
        return {"files": files, "total": total}
```

- [ ] **Step 4: Run the RAG suite**

Run: `cd services/rag && uv run --frozen pytest -q`
Expected: everything passes, including
`test_collections_api.py::test_jobs_filter_by_status_collection_and_text`, which
now goes through `contains()`.

- [ ] **Step 5: Commit**

```bash
git add services/rag/src/rag/workspace_api.py services/rag/tests/test_file_log.py
git commit -m "feat(rag): file log API and status counts

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Polling — auto-sync settings and the worker's poller

**Files:**
- Create: `services/rag/src/rag/workspace_poll.py` (full code below)
- Modify: `services/rag/src/rag/file_log.py`: add `differs()`.
- Modify: `services/rag/src/rag/workspace_api.py`: `auto_sync` and `poll_interval_minutes` in `CollectionFields`, `POLL_COLUMNS`, create, and update without a revision bump for poll-only changes.
- Modify: `services/rag/src/rag/workspace_worker.py`: `step()` and `main()`.
- Modify: `services/rag/tests/test_workspace.py`: `SETTINGS` gains `"auto_sync", "poll_interval_minutes"`.
- Test: `services/rag/tests/test_polling.py` (new)

**Interfaces:**
- Consumes: from Task 4, `file_log.load` and `Walk`; from Task 3, `ka.rag_collections.auto_sync`, `poll_interval_minutes` and `last_polled_at`.
- Produces:
  - `CollectionFields.auto_sync: bool = False` and
    `poll_interval_minutes: int = Field(5, ge=1, le=1440)`. They are accepted by
    `POST collections` and `PUT collections/{id}` and returned in every
    collection row (with `last_polled_at`);
  - `file_log.differs(walk, log) -> bool`;
  - `workspace_poll.QUIET_SECONDS = 120`;
  - `workspace_poll.due(workspace) -> list[dict]` (claimed collection rows);
  - `workspace_poll.check(workspace, row, now: float) -> str`, one of `active`,
    `empty`, `missing`, `fresh`, `changing` or `queued`;
  - `workspace_poll.poll(workspace, now: float | None = None) -> dict[str, str]`
    (collection name → outcome; `error` when a collection could not be checked);
  - `workspace_worker.step(workspace, transport=None) -> bool` (True when a sync
    ran).

- [ ] **Step 1: Write the failing tests**

In `services/rag/tests/test_workspace.py`, extend `SETTINGS`:

```python
SETTINGS = ("description", "search_mode", "embeddings_enabled", "include", "exclude", "chunk_size", "chunk_overlap",
            "auto_sync", "poll_interval_minutes")
```

Create `services/rag/tests/test_polling.py`:

```python
"""Automatic sync: due collections are walked without reading files and queued once their folders are quiet."""
import os
import threading
import time

import pytest
from test_workspace import collection, sync, update

from rag.workspace_poll import QUIET_SECONDS, poll
from rag.workspace_worker import claim, process, step

# env and workspace_postgres come from conftest.py


def age(path, seconds):
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def jobs(env, collection_id):
    with env[0].connect() as conn:
        return conn.execute("SELECT * FROM ka.jobs WHERE rag_collection_id=%s ORDER BY created_at",
                            (collection_id,)).fetchall()


def synced(env, name="docs"):
    """A collection with auto-sync on, synced once, whose folder and files are older than the quiet period."""
    folder = env[6]["rag-data"] / name
    folder.mkdir()
    (folder / "a.md").write_text("# A\n\nalpha")
    for path in (folder / "a.md", folder):
        age(path, 600)  # before the sync, so the log holds these mtimes
    created = collection(env, name, f"rag-data/{name}")
    sync(env, created)
    saved = update(env, created, auto_sync=True, poll_interval_minutes=5)
    assert saved.status_code == 200, saved.text
    return created, folder


def change(folder, name="b.md", seconds=300):
    (folder / name).write_text(f"# {name}\n\nnew")
    age(folder / name, seconds)
    age(folder, seconds)


def test_a_due_collection_with_a_quiet_change_is_queued_and_runs(env):
    created, folder = synced(env)
    change(folder)
    assert poll(env[0]) == {"docs": "queued"}
    queued = jobs(env, created)[-1]
    assert (queued["status"], queued["input"], queued["requested_by"]) == ("queued", {"auto": True}, None)
    with env[0].connect() as conn:
        assert conn.execute("SELECT last_polled_at FROM ka.rag_collections WHERE id=%s", (created,)).fetchone()["last_polled_at"]
    job = claim(env[0])
    assert process(env[0], job, env[5])["parsed_files"] == 1
    listed = env[1].get(env[3] + "/jobs").json()["jobs"][0]
    assert listed["input"] == {"auto": True} and listed["status"] == "succeeded"


def test_an_unchanged_collection_is_not_queued(env):
    created, _ = synced(env)
    assert poll(env[0]) == {"docs": "fresh"} and len(jobs(env, created)) == 1


def test_files_copied_with_old_mtimes_are_noticed(env):
    created, folder = synced(env)
    change(folder, "copied.md", seconds=86400)  # rsync -a keeps the source's day-old mtime, also on the folder
    assert poll(env[0]) == {"docs": "queued"}


def test_a_change_less_than_two_minutes_old_waits_for_the_quiet_period(env):
    created, folder = synced(env)
    (folder / "b.md").write_text("# B\n\nstill being written")
    assert QUIET_SECONDS == 120
    assert poll(env[0]) == {"docs": "changing"} and len(jobs(env, created)) == 1
    with env[0].connect() as conn:  # the next interval
        conn.execute("UPDATE ka.rag_collections SET last_polled_at=NULL WHERE id=%s", (created,))
    assert poll(env[0], now=time.time() + QUIET_SECONDS + 1) == {"docs": "queued"}


def test_a_collection_that_is_not_due_is_not_checked(env, monkeypatch):
    created, folder = synced(env)
    change(folder)
    with env[0].connect() as conn:
        conn.execute("UPDATE ka.rag_collections SET last_polled_at=now() - interval '4 minutes' WHERE id=%s", (created,))
    monkeypatch.setattr(env[0].areas, "walk_paths", lambda *a, **k: pytest.fail("A collection that is not due must not be walked"))
    assert poll(env[0]) == {}
    monkeypatch.undo()
    with env[0].connect() as conn:
        conn.execute("UPDATE ka.rag_collections SET last_polled_at=now() - interval '6 minutes' WHERE id=%s", (created,))
    assert poll(env[0]) == {"docs": "queued"}


def test_an_active_sync_prevents_a_second_one(env):
    created, folder = synced(env)
    change(folder)
    assert env[1].post(env[3] + f"/collections/{created}/sync", json={}).status_code == 202
    assert poll(env[0]) == {"docs": "active"} and len(jobs(env, created)) == 2


def test_two_pollers_queue_one_sync(env):
    created, folder = synced(env)
    change(folder)
    results = []
    threads = [threading.Thread(target=lambda: results.append(poll(env[0]))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(results, key=len) == [{}, {"docs": "queued"}]
    assert [job["status"] for job in jobs(env, created)] == ["succeeded", "queued"]


def test_auto_sync_off_is_never_polled(env, monkeypatch):
    (env[6]["rag-data"] / "docs").mkdir()
    created = collection(env)
    monkeypatch.setattr(env[0].areas, "walk_paths", lambda *a, **k: pytest.fail("Manual collections are never walked"))
    assert poll(env[0]) == {}
    with env[0].connect() as conn:
        assert conn.execute("SELECT last_polled_at FROM ka.rag_collections WHERE id=%s", (created,)).fetchone()["last_polled_at"] is None


def test_a_missing_folder_is_not_synced(env):
    created, folder = synced(env)
    (folder / "a.md").unlink()
    folder.rmdir()
    assert poll(env[0]) == {"docs": "missing"} and len(jobs(env, created)) == 1
    assert env[1].get(env[3] + f"/collections/{created}").json()["status"] == "folder_missing"


def test_poll_interval_is_validated_and_poll_settings_do_not_mark_the_collection_stale(env):
    created, _ = synced(env)
    for minutes in (0, 1441):
        assert update(env, created, poll_interval_minutes=minutes).status_code == 400, minutes
    row = env[1].get(env[3] + f"/collections/{created}").json()
    assert (row["auto_sync"], row["poll_interval_minutes"], row["status"]) == (True, 5, "up_to_date")
    fast = env[1].post(env[3] + "/collections", json={"name": "fast", "auto_sync": True, "poll_interval_minutes": 1})
    assert fast.status_code == 201 and (fast.json()["auto_sync"], fast.json()["poll_interval_minutes"]) == (True, 1)
    assert env[1].post(env[3] + "/collections", json={"name": "slow", "poll_interval_minutes": 1441}).status_code == 400


def test_the_worker_polls_when_no_sync_is_queued(env):
    created, folder = synced(env)
    change(folder)
    assert step(env[0], env[5]) is False  # nothing queued: the poller queues the change
    assert step(env[0], env[5]) is True  # the automatic sync runs
    row = env[1].get(env[3] + f"/collections/{created}").json()
    assert row["indexed_files"] == 2 and row["job"]["status"] == "succeeded"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/rag && uv run --frozen pytest tests/test_polling.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rag.workspace_poll'`.

- [ ] **Step 3: Add `differs()` to `services/rag/src/rag/file_log.py`**

```python
def differs(walk, log: dict[str, dict]) -> bool:
    """Whether the folders differ from the log: a new, changed or deleted file, by size and mtime only.

    Equality, not "newer than the last sync", so files copied with old modification times (rsync -a,
    cp -p, archives, restored backups) still count as changed.
    """
    seen = {entry["path"]: entry for entry in walk.files}
    known = {path: row for path, row in log.items() if row["status"] != "removed"}
    if seen.keys() != known.keys():
        return True
    return any(row["size"] != seen[path]["size"] or row["mtime"] != seen[path]["mtime"] for path, row in known.items())
```

- [ ] **Step 4: Create `services/rag/src/rag/workspace_poll.py`**

```python
"""Automatic sync: the RAG worker polls collections with auto-sync on.

A due collection is walked with sizes and mtimes only (no reads) and compared with its file log. A
sync is queued when something changed and the newest change is at least QUIET_SECONDS old, so a
folder that a source is still writing is left alone until it settles.
"""
import logging
import time

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from rag import file_log
from rag.collections import MAX_FILES, MAX_SYNC_ENTRIES

logger = logging.getLogger(__name__)

QUIET_SECONDS = 120
ACTIVE = ["queued", "running", "waiting_input"]


def due(workspace) -> list[dict]:
    """Claim the collections whose poll interval has passed: record last_polled_at and return them.

    SKIP LOCKED and the committed timestamp keep two workers from checking one collection twice.
    """
    with workspace.connect() as conn:
        return conn.execute("""UPDATE ka.rag_collections c SET last_polled_at=now() WHERE c.id IN (
            SELECT id FROM ka.rag_collections WHERE deleted_at IS NULL AND auto_sync
              AND (last_polled_at IS NULL OR last_polled_at <= now() - make_interval(mins => poll_interval_minutes))
            ORDER BY last_polled_at NULLS FIRST, id FOR UPDATE SKIP LOCKED LIMIT 20)
            RETURNING c.*""").fetchall()


def needs_sync(row: dict, walk, log: dict) -> bool:
    if row["last_synced_at"] is None or row["synced_revision"] != row["revision"]:
        return True
    return walk.truncated or file_log.differs(walk, log)


def check(workspace, row: dict, now: float) -> str:
    """Queue a sync for one due collection when it needs one."""
    with workspace.connect() as conn:
        if conn.execute("""SELECT 1 FROM ka.jobs WHERE rag_collection_id=%s AND kind='rag_collection_sync'
                AND status = ANY(%s)""", (row["id"], ACTIVE)).fetchone():
            return "active"
        sources = [path["source_path"] for path in workspace.paths(conn, row["id"])]
        log = file_log.load(conn, row["id"])
    if not sources:
        return "empty"
    if not all(workspace.areas.present(source) for source in sources):
        return "missing"  # never synced while a folder is absent; the status shows Folder missing
    walk = workspace.areas.walk_paths(sources, row["include"], row["exclude"], limit=MAX_FILES, truncate=True,
                                      max_entries=MAX_SYNC_ENTRIES)
    if not needs_sync(row, walk, log):
        return "fresh"
    if walk.newest_mtime is not None and walk.newest_mtime > now - QUIET_SECONDS:
        return "changing"
    with workspace.connect() as conn:
        # The partial unique index rag_collection_one_active_job lets a concurrent manual Refresh win.
        queued = conn.execute("""INSERT INTO ka.jobs(project_id,kind,rag_collection_id,requested_by,input)
            VALUES (%s,'rag_collection_sync',%s,NULL,%s) ON CONFLICT DO NOTHING RETURNING id""",
            (row["project_id"], row["id"], Jsonb({"auto": True}))).fetchone()
    return "queued" if queued else "active"


def poll(workspace, now: float | None = None) -> dict[str, str]:
    """Check every due collection; returns each one's outcome by name."""
    now = time.time() if now is None else now
    outcomes = {}
    for row in due(workspace):
        try:
            outcomes[row["name"]] = check(workspace, row, now)
        except (HTTPException, OSError) as error:
            logger.warning("Could not poll collection %s: %s", row["name"], getattr(error, "detail", error))
            outcomes[row["name"]] = "error"
    return outcomes
```

- [ ] **Step 5: Auto-sync settings in `services/rag/src/rag/workspace_api.py`**

Add to `CollectionFields`:

```python
    auto_sync: bool = False
    poll_interval_minutes: int = Field(5, ge=1, le=1440)
```

Below `SETTINGS_COLUMNS` add:

```python
# Auto-sync settings do not change what is indexed: saving only these leaves the revision alone.
POLL_COLUMNS = ("auto_sync", "poll_interval_minutes")
```

In `create_collection`, use `columns = ["project_id", "name", *SETTINGS_COLUMNS, *POLL_COLUMNS]`
and pass `*(getattr(payload, c) for c in (*SETTINGS_COLUMNS, *POLL_COLUMNS))`.

Replace the body of `update_collection` with:

```python
        with workspace.connect() as conn:
            row = workspace.collection(conn, project, collection_id, lock=True)
            if payload.revision != row["revision"]:
                raise HTTPException(409, "Collection changed; refresh before saving")
            columns = (*SETTINGS_COLUMNS, *POLL_COLUMNS)
            bump_revision = int(any(getattr(payload, c) != row[c] for c in SETTINGS_COLUMNS))
            row = conn.execute(f"""UPDATE ka.rag_collections SET {','.join(c + '=%s' for c in columns)},
                revision=revision+%s,updated_at=now() WHERE id=%s RETURNING *""",
                               (*(getattr(payload, c) for c in columns), bump_revision, collection_id)).fetchone()
            return collection_status(workspace, conn, row)
```

- [ ] **Step 6: The worker loop in `services/rag/src/rag/workspace_worker.py`**

Import `from rag.workspace_poll import poll` and replace `main()` with:

```python
def step(workspace: Workspace, transport=None) -> bool:
    """One worker iteration: run a queued sync, or poll due collections. Returns whether a sync ran."""
    job = claim(workspace)
    if job is not None:
        process(workspace, job, transport)
        return True
    poll(workspace)
    return False


def main():
    logging.basicConfig(level=logging.INFO)
    workspace = Workspace.from_env()
    while True:
        try:
            if step(workspace):
                continue
        except (psycopg.Error, OSError):
            logger.error("RAG worker cannot reach its database or collection folders")
        time.sleep(2)
```

- [ ] **Step 7: Run the polling tests and the RAG suite**

Run: `cd services/rag && uv run --frozen pytest tests/test_polling.py -q && uv run --frozen pytest -q`
Expected: 11 passed in `test_polling.py`; then the whole RAG suite passes.

- [ ] **Step 8: Commit**

```bash
git add services/rag/src/rag/workspace_poll.py services/rag/src/rag/file_log.py services/rag/src/rag/workspace_api.py \
  services/rag/src/rag/workspace_worker.py services/rag/tests/test_workspace.py services/rag/tests/test_polling.py
git commit -m "feat(rag): auto-sync polls due collections and queues a sync after a quiet period

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
# Part E — admin UI (Tasks 7–10)

### Task 7: admin-ui — New collection, Add folder with a folder browser, Remove folder, Remove always

**Files:**
- Modify: `services/admin-ui/app/lib/rag.ts` (full content below)
- Modify: `services/admin-ui/app/components/RagCollectionForm.tsx` (full content below)
- Create: `services/admin-ui/app/components/RagFolderPicker.tsx` (full content below)
- Modify: `services/admin-ui/app/components/RagCollectionsView.tsx` (full content below)
- Modify: `services/admin-ui/app/components/RagCollectionDetail.tsx` (full content below)
- Replace: `services/admin-ui/app/components/__tests__/ragCollections.test.tsx` (full content below)
- Test: `services/admin-ui/app/components/__tests__/ragFolderPicker.test.tsx` (new)

**Interfaces:**
- Consumes, from Task 1 through the admin proxy (Task 2): `POST /api/rag/collections`, `POST /api/rag/collections/{id}/paths`, `DELETE /api/rag/collections/{id}/paths/{pathId}` and `GET /api/rag/folders[?path=]`.
- Produces, in `app/lib/rag.ts`:
  - `CollectionPath` without owner fields;
  - `DEFAULT_SETTINGS: CollectionSettings`;
  - `FolderEntry {name, path}` and `FolderListing {path, folders, truncated}`;
  - `createCollection(value: CollectionSettings & { name: string }) -> CollectionSummary`;
  - `addFolder(id, sourcePath) -> CollectionSummary`;
  - `removeFolder(id, pathId) -> CollectionSummary`;
  - `getFolders(path: string | null, signal?) -> FolderListing`;
  - `normalizeFolder(value) -> string`.
  `OwnerKind`, `OWNER_LABEL`, `ownerLabel` and `detachPath` are removed.
- Produces components:
  - `RagCollectionForm({open, collection?, onOpenChange, onSaved(saved: {id}), onFailed?})`.
    Without `collection` it creates: a Name field and a **Create collection**
    button. With `collection` it edits: **Save collection**;
  - `RagFolderPicker({open, collection: {id, name}, onOpenChange, onAdded})`:
    browses `getFolders`, takes a typed **Folder path** and submits with
    **Connect folder**.

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/ragFolderPicker.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RagFolderPicker from "../RagFolderPicker";

function api(add: () => Response = () => Response.json({ id: "c1", paths: [] })) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") return add();
    if (url === "/api/rag/folders") return Response.json({ path: null, truncated: false, folders: [{ name: "code", path: "code" }, { name: "rag-data", path: "rag-data" }] });
    if (url === "/api/rag/folders?path=code") return Response.json({ path: "code", truncated: false, folders: [{ name: "payments", path: "code/payments" }] });
    return Response.json({ path: "code/payments", truncated: false, folders: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const posted = (fetchMock: ReturnType<typeof api>) => fetchMock.mock.calls
  .filter(([, init]) => (init as RequestInit | undefined)?.method === "POST")
  .map(([url, init]) => [url, JSON.parse((init as RequestInit).body as string)]);
const picker = (onAdded = vi.fn()) => render(<RagFolderPicker open collection={{ id: "c1", name: "payments" }} onOpenChange={() => {}} onAdded={onAdded} />);

test("browse into a folder and connect it", async () => {
  const fetchMock = api();
  const onAdded = vi.fn();
  const user = userEvent.setup();
  picker(onAdded);
  await user.click(await screen.findByRole("button", { name: "code" }));
  await user.click(await screen.findByRole("button", { name: "payments" }));
  expect(screen.getByLabelText("Folder path")).toHaveValue("code/payments");
  expect(await screen.findByText("No subfolders.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Connect folder" }));
  await waitFor(() => expect(onAdded).toHaveBeenCalled());
  expect(posted(fetchMock)).toEqual([["/api/rag/collections/c1/paths", { source_path: "code/payments" }]]);
});

test("Up returns to the parent folder", async () => {
  const fetchMock = api();
  const user = userEvent.setup();
  picker();
  await user.click(await screen.findByRole("button", { name: "code" }));
  await screen.findByRole("button", { name: "payments" });
  await user.click(screen.getByRole("button", { name: "Up" }));
  expect(await screen.findByRole("button", { name: "rag-data" })).toBeInTheDocument();
  expect(screen.getByLabelText("Folder path")).toHaveValue("");
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/rag/folders", "/api/rag/folders?path=code", "/api/rag/folders"]);
});

test("a typed path is trimmed of slashes", async () => {
  const fetchMock = api();
  const user = userEvent.setup();
  picker();
  await user.type(await screen.findByLabelText("Folder path"), " /rag-data/notes/ ");
  await user.click(screen.getByRole("button", { name: "Connect folder" }));
  await waitFor(() => expect(posted(fetchMock)).toEqual([["/api/rag/collections/c1/paths", { source_path: "rag-data/notes" }]]));
});

test("a refused folder shows the RAG message and nothing is added", async () => {
  api(() => Response.json({ error: { message: "Folder overlaps code/payments in collection payments" } }, { status: 409 }));
  const onAdded = vi.fn();
  const user = userEvent.setup();
  picker(onAdded);
  await user.type(await screen.findByLabelText("Folder path"), "code/payments/src");
  await user.click(screen.getByRole("button", { name: "Connect folder" }));
  expect(await screen.findByText("Folder overlaps code/payments in collection payments")).toBeInTheDocument();
  expect(onAdded).not.toHaveBeenCalled();
});
```

Replace `services/admin-ui/app/components/__tests__/ragCollections.test.tsx` with:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import RagCollectionsView from "../RagCollectionsView";
import RagCollectionDetail from "../RagCollectionDetail";
import type { CollectionPath, RagCollection } from "../../lib/rag";

const jiraPath: CollectionPath = {
  id: "p1", collection_id: "c1", source_path: "rag-data/jira", created_at: "2026-10-05T09:00:00Z", files: 75,
  host_path: "/opt/ka/rag-data/jira", container_path: "/work/rag-data/jira", present: true,
};
const codePath: CollectionPath = { ...jiraPath, id: "p2", source_path: "code/payments", files: 12,
  host_path: "/opt/ka/code/payments", container_path: "/work/code/payments" };
const notesPath: CollectionPath = { ...jiraPath, id: "p3", source_path: "rag-data/notes", files: 3,
  host_path: "/opt/ka/rag-data/notes", container_path: "/work/rag-data/notes" };
const base: RagCollection = {
  id: "c1", name: "jira", description: "Jira tickets", search_mode: "full_text", embeddings_enabled: true, include: ["**/*.md"],
  exclude: [], chunk_size: 2000, chunk_overlap: 200, revision: 2, synced_revision: 2, last_synced_at: "2026-10-05T09:00:00Z",
  paths: [codePath, jiraPath, notesPath], folder_present: true, missing_paths: [], folder_changed_at: "2026-10-05T10:00:00Z",
  file_count: 90, truncated: false, skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 },
  indexed_files: 90, chunks: 975, embedded_chunks: 0, needs_refresh: true, status: "needs_refresh", job: null,
};
const notes: RagCollection = { ...base, id: "c2", name: "notes", paths: [notesPath] };
const noFiles = { files: [], truncated: false, skipped: { links: 0, special: 0, unreadable: 0, invalid_names: 0 }, missing_paths: [] };

function detail(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock);
  return render(<MemoryRouter initialEntries={["/rag/collections/c1"]}><Routes>
    <Route path="/rag/collections/:id" element={<RagCollectionDetail id="c1" />} />
    <Route path="/rag/collections" element={<div>collections page</div>} /></Routes></MemoryRouter>);
}

test("lists collections with their folders and no source badges", async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") return Response.json({ id: "job" }, { status: 202 });
    return Response.json({ collections: [base, { ...notes, status: "up_to_date", needs_refresh: false }] });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  const row = (await screen.findByRole("link", { name: "jira" })).closest("tr")!;
  expect(within(row).getByText("/opt/ka/rag-data/jira")).toBeInTheDocument();
  expect(within(row).getByText("75 files")).toBeInTheDocument();
  expect(within(row).getByText("0 / 975")).toBeInTheDocument();
  expect(screen.queryByText("Unowned")).toBeNull();
  expect(screen.queryByText(/Code project/)).toBeNull();
  expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/code-projects"))).toBe(false);
  const notesRow = screen.getByRole("link", { name: "notes" }).closest("tr")!;
  expect(within(notesRow).queryByRole("button", { name: "Refresh" })).toBeNull();
  await userEvent.click(within(row).getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c1/sync", expect.objectContaining({ method: "POST" })));
});

test("New collection creates a collection and opens it", async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => init?.method === "POST"
    ? Response.json({ ...base, id: "c9", name: "payments", paths: [] }, { status: 201 })
    : Response.json({ collections: [] }));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/rag/collections"]}><Routes>
    <Route path="/rag/collections" element={<RagCollectionsView />} />
    <Route path="/rag/collections/:id" element={<div>detail page</div>} /></Routes></MemoryRouter>);
  expect(await screen.findByText(/No collections yet/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "New collection" }));
  await user.type(screen.getByLabelText("Name"), "payments");
  await user.click(screen.getByRole("button", { name: "Create collection" }));
  expect(await screen.findByText("detail page")).toBeInTheDocument();
  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "POST")!;
  expect(post[0]).toBe("/api/rag/collections");
  expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({ name: "payments", description: "", search_mode: "full_text",
    embeddings_enabled: false, include: ["**/*.md", "**/*.markdown", "**/*.txt"], exclude: [], chunk_size: 2000, chunk_overlap: 200 });
});

test("an invalid name disables Create collection", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ collections: [] })));
  const user = userEvent.setup();
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "New collection" }));
  await user.type(screen.getByLabelText("Name"), "Payments");
  expect(screen.getByText("Use lowercase letters, digits and single hyphens, up to 60 characters.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create collection" })).toBeDisabled();
});

test("missing folders are named and failed syncs show the error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ collections: [
    { ...base, status: "folder_missing", folder_present: false, missing_paths: ["rag-data/jira"], paths: [{ ...jiraPath, present: false }] },
    { ...notes, status: "failed", job: { id: "j", status: "failed", error: "Select an enabled embedding model", created_at: "", finished_at: "" } }] })));
  render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  expect(await screen.findByText("Folder missing")).toBeInTheDocument();
  expect(screen.getByText("Missing: rag-data/jira")).toBeInTheDocument();
  expect(screen.getByText(/Select an enabled embedding model/)).toBeInTheDocument();
});

test("detail lists full paths and chunks, with no upload or file editor", async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes("/chunks")) return Response.json({ chunks: [{ position: 0, text: "First chunk text", embedded: false }] });
    if (url.endsWith("/files")) return Response.json({ ...noFiles, files: [{ path: "rag-data/jira/DEMO-1.md", source: "rag-data/jira", size: 10, mtime: 1,
      indexed_at: "2026-10-05T09:00:00Z", content_hash: "abc", chunks: 13, embedded_chunks: 0, stale: false }] });
    return Response.json(base);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  const file = await screen.findByRole("button", { name: "rag-data/jira/DEMO-1.md" });
  expect(within(file.closest("tr")!).getByText("13")).toBeInTheDocument();
  await user.click(file);
  expect(await screen.findByText("First chunk text")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/rag/collections/c1/chunks?path=rag-data%2Fjira%2FDEMO-1.md"), expect.anything());
  expect(screen.queryByLabelText("Upload file")).toBeNull();
});

test("Edit changes search settings only", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/files")) return Response.json(noFiles);
    return Response.json(init?.method === "PUT" ? { ...base, revision: 3 } : base);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  await user.click(await screen.findByRole("button", { name: "Edit" }));
  expect(screen.queryByLabelText("Name")).toBeNull();
  await user.clear(screen.getByLabelText("Include patterns"));
  await user.type(screen.getByLabelText("Include patterns"), "**/*.py");
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit)?.method === "PUT")).toBe(true));
  const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit)?.method === "PUT")!;
  expect(put[0]).toBe("/api/rag/collections/c1");
  expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ description: "Jira tickets", search_mode: "full_text",
    embeddings_enabled: true, include: ["**/*.py"], exclude: [], chunk_size: 2000, chunk_overlap: 200, revision: 2 });
});

test("Remove collection is always available and asks for confirmation", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/files")) return Response.json(noFiles);
    if (init?.method === "DELETE") return Response.json({ deleted_files: 3 });
    return Response.json(base);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  await user.click(await screen.findByRole("button", { name: "Remove" }));
  expect(screen.getByText(/files on disk are kept/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Remove collection" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c1?revision=2&confirmed=true", expect.objectContaining({ method: "DELETE" })));
  expect(await screen.findByText("collections page")).toBeInTheDocument();
});

test("Add folder connects a typed folder and Remove folder disconnects one", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/files")) return Response.json(noFiles);
    if (url.startsWith("/api/rag/folders")) return Response.json({ path: null, truncated: false, folders: [] });
    return Response.json(base);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  await user.click(await screen.findByRole("button", { name: "Add folder" }));
  await user.type(await screen.findByLabelText("Folder path"), "docs/runbooks");
  await user.click(screen.getByRole("button", { name: "Connect folder" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c1/paths",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ source_path: "docs/runbooks" }) })));
  expect(await screen.findByText("Folder added. Refresh the collection to index it.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Remove folder rag-data/notes" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/rag/collections/c1/paths/p3", expect.objectContaining({ method: "DELETE" })));
  expect(await screen.findByText("Folder removed. Its files left search; files on disk are kept.")).toBeInTheDocument();
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
    if (init?.method === "PUT") return Response.json({ error: { message: "Collection changed; refresh before saving" } }, { status: 409 });
    if (url.endsWith("/files")) return Response.json(noFiles);
    const row = { ...base, revision }; revision = 5; return Response.json(row);
  });
  const user = userEvent.setup();
  detail(fetchMock);
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

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm test -- ragCollections ragFolderPicker`
Expected: FAIL. `RagFolderPicker` does not exist, there is no **New collection**
button, and **Remove** is hidden while folders exist.

- [ ] **Step 3: Replace `services/admin-ui/app/lib/rag.ts`**

```ts
import { request } from "./api";

export interface RagSettings { max_file_bytes: number }
export interface RagArea { area: string; container_root: string; host_root: string | null }
export interface RagConfig { revision: number; settings: RagSettings; embedding_model_id: string | null; areas: RagArea[] }
export interface RagJob {
  id: string; status: string; input: { force?: boolean }; result: Record<string, number>;
  error: string | null; created_at: string; started_at: string | null; finished_at: string | null;
  collection: string; collection_id: string | null;
}
export interface RagHit {
  id: string; text: string; position: number; path: string; collection: string; score: number;
  citation: string; content_hash: string; indexed_at: string; job_id: string;
}
export interface RagResults { collections: string[]; fallback: string[]; results: RagHit[] }
export function ragRequest<T>(resource: string, method = "GET", body?: unknown, signal?: AbortSignal) {
  return request<T>(`/api/rag/${resource}`, {
    method, signal, ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
}

export type SearchMode = "full_text" | "embedding" | "hybrid";
export type CollectionState = "folder_missing" | "syncing" | "failed" | "needs_refresh" | "up_to_date";
export interface SkippedCounts { links: number; special: number; unreadable: number; invalid_names: number }
export interface CollectionPath {
  id: string; collection_id: string; source_path: string; created_at: string; files: number;
  host_path: string | null; container_path: string; present: boolean;
}
export interface CollectionSettings {
  description: string; search_mode: SearchMode; embeddings_enabled: boolean;
  include: string[]; exclude: string[]; chunk_size: number; chunk_overlap: number;
}
export const DEFAULT_SETTINGS: CollectionSettings = {
  description: "", search_mode: "full_text", embeddings_enabled: false,
  include: ["**/*.md", "**/*.markdown", "**/*.txt"], exclude: [], chunk_size: 2000, chunk_overlap: 200,
};
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
export interface FolderEntry { name: string; path: string }
export interface FolderListing { path: string | null; folders: FolderEntry[]; truncated: boolean }

/** A folder path as RAG expects it: relative to DATA_ROOT, no surrounding whitespace or slashes. */
export const normalizeFolder = (value: string) => value.trim().replace(/^\/+|\/+$/g, "");
export const getCollections = (signal?: AbortSignal) => ragRequest<{ collections: RagCollection[] }>("collections", "GET", undefined, signal);
export const getCollectionNames = (signal?: AbortSignal) => ragRequest<{ collections: CollectionSummary[] }>("collections?status=false", "GET", undefined, signal);
export const getCollection = (id: string, signal?: AbortSignal) => ragRequest<RagCollection>(`collections/${id}`, "GET", undefined, signal);
export const createCollection = (value: CollectionSettings & { name: string }) => ragRequest<CollectionSummary>("collections", "POST", value);
export const saveCollectionSettings = (id: string, value: CollectionSettings & { revision: number }) => ragRequest<RagCollection>(`collections/${id}`, "PUT", value);
export const deleteCollection = (id: string, revision: number) => ragRequest<{ deleted_files: number }>(`collections/${id}?revision=${revision}&confirmed=true`, "DELETE");
export const addFolder = (id: string, sourcePath: string) => ragRequest<CollectionSummary>(`collections/${id}/paths`, "POST", { source_path: sourcePath });
export const removeFolder = (id: string, pathId: string) => ragRequest<CollectionSummary>(`collections/${id}/paths/${pathId}`, "DELETE");
export const getFolders = (path: string | null, signal?: AbortSignal) =>
  ragRequest<FolderListing>(path === null ? "folders" : `folders?path=${encodeURIComponent(path)}`, "GET", undefined, signal);
export const syncCollection = (id: string, force = false) => ragRequest<{ id: string }>(`collections/${id}/sync`, "POST", { force });
export const syncStale = () => ragRequest<{ queued: string[] }>("collections/sync-stale", "POST");
export const getCollectionFiles = (id: string, signal?: AbortSignal) => ragRequest<{ files: CollectionFile[]; truncated: boolean; skipped: SkippedCounts; missing_paths: string[] }>(`collections/${id}/files`, "GET", undefined, signal);
export const getChunks = (id: string, path: string) => ragRequest<{ chunks: { position: number; text: string; embedded: boolean }[] }>(`collections/${id}/chunks?path=${encodeURIComponent(path)}`);
```

- [ ] **Step 4: Replace `services/admin-ui/app/components/RagCollectionForm.tsx`**

```tsx
import { useState } from "react";
import { ApiError } from "../lib/api";
import { createCollection, DEFAULT_SETTINGS, saveCollectionSettings, type CollectionSettings, type RagCollection, type SearchMode } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";
import { Switch } from "~/components/ui/switch";
import { Textarea } from "~/components/ui/textarea";

const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean);
const NAME = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/** Creates a collection (no `collection`) or edits its settings. */
export default function RagCollectionForm({ open, collection, onOpenChange, onSaved, onFailed }: {
  open: boolean; collection?: RagCollection; onOpenChange: (open: boolean) => void;
  onSaved: (saved: { id: string }) => void; onFailed?: () => void;
}) {
  return <Sheet open={open} onOpenChange={onOpenChange}><SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-xl">
    <SheetHeader><SheetTitle>{collection ? `Edit ${collection.name}` : "New collection"}</SheetTitle>
      <SheetDescription>{collection
        ? "Search settings for this collection. Add and remove folders on the collection page."
        : "Name the collection and choose its search settings, then add folders to it."}</SheetDescription></SheetHeader>
    {open && <SettingsFields collection={collection} onSaved={onSaved} onFailed={onFailed} />}
  </SheetContent></Sheet>;
}

function SettingsFields({ collection, onSaved, onFailed }: { collection?: RagCollection; onSaved: (saved: { id: string }) => void; onFailed?: () => void }) {
  const initial: CollectionSettings = collection ?? DEFAULT_SETTINGS;
  const [name, setName] = useState("");
  const [description, setDescription] = useState(initial.description);
  const [searchMode, setSearchMode] = useState<SearchMode>(initial.search_mode);
  const [embeddings, setEmbeddings] = useState(initial.embeddings_enabled);
  const [include, setInclude] = useState(initial.include.join("\n"));
  const [exclude, setExclude] = useState(initial.exclude.join("\n"));
  const [chunkSize, setChunkSize] = useState(initial.chunk_size);
  const [chunkOverlap, setChunkOverlap] = useState(initial.chunk_overlap);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const nameInvalid = !collection && (name.length > 60 || !NAME.test(name));
  async function submit() {
    setBusy(true); setError("");
    const value: CollectionSettings = { description, search_mode: searchMode, embeddings_enabled: embeddings,
      include: lines(include), exclude: lines(exclude), chunk_size: chunkSize, chunk_overlap: chunkOverlap };
    try {
      onSaved(collection
        ? await saveCollectionSettings(collection.id, { ...value, revision: collection.revision })
        : await createCollection({ name, ...value }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the collection"); setBusy(false);
      if (e instanceof ApiError && e.status === 409) onFailed?.();
    }
  }
  return <form className="flex flex-col gap-5 p-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <FieldGroup>
      {!collection && <Field><FieldLabel htmlFor="collection-name">Name</FieldLabel>
        <Input id="collection-name" required maxLength={60} value={name} aria-invalid={name !== "" && nameInvalid} onChange={(e) => setName(e.target.value.trim())} />
        {name !== "" && nameInvalid
          ? <FieldDescription className="text-destructive">Use lowercase letters, digits and single hyphens, up to 60 characters.</FieldDescription>
          : <FieldDescription>For example payments-code. The name cannot be changed later.</FieldDescription>}</Field>}
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
    <Button className="self-start" type="submit" disabled={busy || nameInvalid}>{collection ? "Save collection" : "Create collection"}</Button>
  </form>;
}
```

- [ ] **Step 5: Create `services/admin-ui/app/components/RagFolderPicker.tsx`**

```tsx
import { useEffect, useState } from "react";
import { ChevronRight, CornerLeftUp, Folder } from "lucide-react";
import { addFolder, getFolders, normalizeFolder, type FolderListing } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "~/components/ui/sheet";

const parent = (path: string) => path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : null;

/** Browse the DATA_ROOT areas or type a folder path, then connect the folder to the collection. */
export default function RagFolderPicker({ open, collection, onOpenChange, onAdded }: {
  open: boolean; collection: { id: string; name: string }; onOpenChange: (open: boolean) => void; onAdded: () => void;
}) {
  return <Sheet open={open} onOpenChange={onOpenChange}><SheetContent className="flex w-full flex-col overflow-y-auto sm:max-w-xl">
    <SheetHeader><SheetTitle>Add folder to {collection.name}</SheetTitle>
      <SheetDescription>Choose a folder under DATA_ROOT or type its path, such as code/payments. A missing folder under rag-data is created.</SheetDescription></SheetHeader>
    {open && <Picker collectionId={collection.id} onAdded={onAdded} />}
  </SheetContent></Sheet>;
}

function Picker({ collectionId, onAdded }: { collectionId: string; onAdded: () => void }) {
  const [browsing, setBrowsing] = useState<string | null>(null);
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [value, setValue] = useState("");
  const [browseError, setBrowseError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setListing(null); setBrowseError("");
    getFolders(browsing, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setListing(result); })
      .catch((e) => { if (!controller.signal.aborted) setBrowseError(e instanceof Error ? e.message : "Could not list folders"); });
    return () => controller.abort();
  }, [browsing]);
  function browse(path: string | null) { setBrowsing(path); setValue(path ?? ""); }
  async function submit() {
    setBusy(true); setError("");
    try { await addFolder(collectionId, normalizeFolder(value)); onAdded(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not add the folder"); setBusy(false); }
  }
  return <form className="flex flex-col gap-4 p-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-muted-foreground">Browsing</span><code>{browsing ?? "DATA_ROOT"}</code>
        {browsing !== null && <Button type="button" variant="ghost" size="sm" onClick={() => browse(parent(browsing))}><CornerLeftUp data-icon="inline-start" />Up</Button>}
      </div>
      {browseError && <p className="text-sm text-destructive">{browseError}</p>}
      <ul className="flex flex-col rounded-md border">
        {listing?.folders.map((folder) => <li key={folder.path}>
          <Button type="button" variant="ghost" className="w-full justify-start" onClick={() => browse(folder.path)}>
            <Folder data-icon="inline-start" />{folder.name}<ChevronRight className="ml-auto" /></Button></li>)}
        {listing && listing.folders.length === 0 && <li className="p-3 text-sm text-muted-foreground">No subfolders.</li>}
      </ul>
      {listing?.truncated && <p className="text-xs text-muted-foreground">Only the first 1,000 folders are shown; type the path instead.</p>}
    </div>
    <Field><FieldLabel htmlFor="folder-path">Folder path</FieldLabel>
      <Input id="folder-path" value={value} placeholder="code/payments" onChange={(e) => setValue(e.target.value)} />
      <FieldDescription>Relative to DATA_ROOT; starts with code, code-index, rag-data, generated-documents or docs.</FieldDescription></Field>
    <Button className="self-start" type="submit" disabled={busy || normalizeFolder(value) === ""}>Connect folder</Button>
  </form>;
}
```

- [ ] **Step 6: Replace `services/admin-ui/app/components/RagCollectionsView.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { Plus, RefreshCw } from "lucide-react";
import { getCollections, syncCollection, syncStale, type CollectionState, type RagCollection } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import RagCollectionForm from "./RagCollectionForm";

export const STATUS_LABEL: Record<CollectionState, string> = {
  up_to_date: "Up to date", needs_refresh: "Needs refresh", syncing: "Syncing", failed: "Failed", folder_missing: "Folder missing",
};
export const statusVariant = (status: CollectionState) => status === "failed" || status === "folder_missing" ? "destructive" as const : status === "up_to_date" ? "secondary" as const : "outline" as const;
export const when = (value: string | null) => value ? new Date(value).toLocaleString() : "Never";

export default function RagCollectionsView() {
  const [collections, setCollections] = useState<RagCollection[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const wanted = params.get("name");
  const load = useCallback(async (signal?: AbortSignal) => {
    try { const result = await getCollections(signal); if (!signal?.aborted) { setCollections(result.collections); setError(""); } }
    catch (e) { if (!signal?.aborted) setError(e instanceof Error ? e.message : "Could not load collections"); }
  }, []);
  useEffect(() => { const controller = new AbortController(); load(controller.signal); return () => controller.abort(); }, [load]);
  useEffect(() => {
    const match = wanted ? collections.find((c) => c.name === wanted) : undefined;
    if (match) navigate(`/rag/collections/${match.id}`, { replace: true });
  }, [wanted, collections, navigate]);
  const syncing = collections.some((c) => c.status === "syncing");
  useEffect(() => {
    if (!syncing) return;
    const controller = new AbortController();
    const timer = setInterval(() => load(controller.signal), 5000);
    return () => { clearInterval(timer); controller.abort(); };
  }, [syncing, load]);
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); await load(); } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); } finally { setBusy(false); }
  }
  return <div className="flex flex-col gap-4">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted-foreground">A collection indexes the folders you connect to it. Sources such as Docs → Documents, the Jira export and Git pulls only write files into their folders. Refresh re-reads changed files.</p>
      <div className="flex gap-2">
        <Button variant="outline" disabled={busy} onClick={() => run(async () => { const { queued } = await syncStale(); setNotice(queued.length ? `Queued: ${queued.join(", ")}` : "Nothing to refresh"); })}><RefreshCw />Refresh all stale</Button>
        <Button onClick={() => setCreating(true)}><Plus />New collection</Button>
      </div>
    </div>
    <Table><TableHeader><TableRow><TableHead>Collection</TableHead><TableHead>Folders</TableHead><TableHead>Files</TableHead><TableHead>Embedded</TableHead><TableHead>Last synced</TableHead><TableHead>Status</TableHead><TableHead>Actions</TableHead></TableRow></TableHeader><TableBody>
      {collections.map((c) => <TableRow key={c.id}>
        <TableCell className="align-top"><Link className="font-medium underline-offset-4 hover:underline" to={`/rag/collections/${c.id}`}>{c.name}</Link>{c.description && <p className="text-xs text-muted-foreground">{c.description}</p>}</TableCell>
        <TableCell className="align-top"><ul className="flex flex-col gap-1">
          {c.paths.map((p) => <li key={p.id} className="flex flex-wrap items-center gap-2 text-xs">
            <code className="break-all">{p.host_path ?? p.source_path}</code>
            <span className="text-muted-foreground">{p.files} files</span>
            {!p.present && <Badge variant="destructive">Missing</Badge>}
          </li>)}
          {c.paths.length === 0 && <li className="text-xs text-muted-foreground">No folders yet</li>}
        </ul></TableCell>
        <TableCell className="align-top">{c.indexed_files} / {c.file_count}</TableCell>
        <TableCell className="align-top">{c.embeddings_enabled ? `${c.embedded_chunks} / ${c.chunks}` : "Off"}</TableCell>
        <TableCell className="align-top text-xs">{when(c.last_synced_at)}</TableCell>
        <TableCell className="align-top"><Badge variant={statusVariant(c.status)}>{STATUS_LABEL[c.status]}</Badge>{c.status === "failed" && c.job?.error && <p className="mt-1 max-w-xs text-xs text-destructive">{c.job.error}</p>}{c.status === "folder_missing" && <p className="mt-1 text-xs text-destructive">Missing: {c.missing_paths.join(", ")}</p>}</TableCell>
        <TableCell className="align-top">{(c.status === "needs_refresh" || c.status === "failed") && <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(c.id); })}>Refresh</Button>}</TableCell>
      </TableRow>)}
      {collections.length === 0 && <TableRow><TableCell colSpan={7} className="py-12 text-center text-muted-foreground">No collections yet. Choose New collection, then add folders to it.</TableCell></TableRow>}
    </TableBody></Table>
    <RagCollectionForm open={creating} onOpenChange={setCreating} onSaved={(saved) => { setCreating(false); navigate(`/rag/collections/${saved.id}`); }} />
  </div>;
}
```

- [ ] **Step 7: Replace `services/admin-ui/app/components/RagCollectionDetail.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { deleteCollection, getChunks, getCollection, getCollectionFiles, removeFolder, syncCollection, type CollectionFile, type CollectionPath, type RagCollection, type SkippedCounts } from "../lib/rag";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "~/components/ui/alert-dialog";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Skeleton } from "~/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";
import RagCollectionForm from "./RagCollectionForm";
import RagFolderPicker from "./RagFolderPicker";
import { STATUS_LABEL, statusVariant, when } from "./RagCollectionsView";

type Chunk = { position: number; text: string; embedded: boolean };
interface Files { files: CollectionFile[]; truncated: boolean; skipped: SkippedCounts; missing_paths: string[] }

export default function RagCollectionDetail({ id }: { id: string }) {
  const navigate = useNavigate();
  const [collection, setCollection] = useState<RagCollection | null>(null);
  const [files, setFiles] = useState<Files | null>(null);
  const [selected, setSelected] = useState("");
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [editing, setEditing] = useState(false);
  const [picking, setPicking] = useState(false);
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
  const disconnect = (path: CollectionPath) => run(async () => {
    await removeFolder(id, path.id); setNotice("Folder removed. Its files left search; files on disk are kept.");
  });
  if (!collection) return <div className="flex flex-col gap-4">{error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : <Skeleton className="h-64" />}</div>;
  const c = collection;
  return <div className="flex flex-col gap-6">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    <section className="flex flex-col gap-3 rounded-lg border p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-lg font-semibold">{c.name}</h2>{c.description && <p className="text-sm text-muted-foreground">{c.description}</p>}</div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>Edit</Button>
          <Button variant="outline" size="sm" onClick={() => setPicking(true)}>Add folder</Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(id); setNotice("Refresh queued."); })}>Refresh</Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => { await syncCollection(id, true); setNotice("Rebuild queued."); })}>Rebuild</Button>
          <Button variant="destructive" size="sm" disabled={busy} onClick={() => setConfirmDelete(true)}>Remove</Button>
        </div>
      </div>
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
      <h3 className="text-base font-semibold">Folders</h3>
      <Table><TableHeader><TableRow><TableHead>Path</TableHead><TableHead>Indexed files</TableHead><TableHead>Folder</TableHead><TableHead /></TableRow></TableHeader><TableBody>
        {c.paths.map((p) => <TableRow key={p.id}>
          <TableCell><code className="break-all text-xs">{p.source_path}</code>{p.host_path && <p className="break-all text-xs text-muted-foreground">{p.host_path}</p>}</TableCell>
          <TableCell>{p.files}</TableCell>
          <TableCell>{p.present ? "Present" : <Badge variant="destructive">Missing</Badge>}</TableCell>
          <TableCell><Button variant="outline" size="sm" disabled={busy} aria-label={`Remove folder ${p.source_path}`} onClick={() => void disconnect(p)}>Remove</Button></TableCell>
        </TableRow>)}
        {c.paths.length === 0 && <TableRow><TableCell colSpan={4} className="py-8 text-center text-muted-foreground">No folders yet. Choose Add folder to connect one.</TableCell></TableRow>}
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
    <RagFolderPicker open={picking} collection={c} onOpenChange={setPicking}
      onAdded={() => { setPicking(false); setNotice("Folder added. Refresh the collection to index it."); load(); }} />
    <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Remove collection?</AlertDialogTitle>
      <AlertDialogDescription>Remove {c.name} from search? Its index, folders, file log and agent grants are removed; files on disk are kept.</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction onClick={() => run(async () => { await deleteCollection(id, c.revision); navigate("/rag/collections"); }, false)}>Remove collection</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <Link className="text-sm underline" to="/rag/collections">Back to collections</Link>
  </div>;
}
```

- [ ] **Step 8: Run the tests and the type check**

Run: `cd services/admin-ui && pnpm test -- ragCollections ragFolderPicker && pnpm exec tsc --noEmit`
Expected: 10 + 4 passed; `tsc` reports no errors. `ownerLabel`, `OWNER_LABEL`,
`detachPath` and `PathSource` have no other users; `git grep -n "ownerLabel\|detachPath\|PathSource\|useProjectNames" services/admin-ui/app`
prints nothing.

- [ ] **Step 9: Commit**

```bash
git add services/admin-ui/app/lib/rag.ts services/admin-ui/app/components/RagCollectionForm.tsx \
  services/admin-ui/app/components/RagFolderPicker.tsx services/admin-ui/app/components/RagCollectionsView.tsx \
  services/admin-ui/app/components/RagCollectionDetail.tsx services/admin-ui/app/components/__tests__/ragCollections.test.tsx \
  services/admin-ui/app/components/__tests__/ragFolderPicker.test.tsx
git commit -m "feat(admin-ui): New collection, Add folder with a folder browser, Remove folder, Remove always available

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 8: admin-ui — the collection's file log table

**Files:**
- Modify: `services/admin-ui/app/lib/rag.ts`: log types, `LOG_STATUS_LABEL`, `getFileLog`, `log_counts`; remove `CollectionFile` and `getCollectionFiles`.
- Create: `services/admin-ui/app/components/RagFileLog.tsx` (full content below)
- Modify: `services/admin-ui/app/components/RagCollectionDetail.tsx`: the log replaces "Indexed files", and the summary shows status counts.
- Modify: `services/admin-ui/app/components/__tests__/ragCollections.test.tsx`: `log_counts` in the fixture; the detail tests answer `/log` instead of `/files`.
- Test: `services/admin-ui/app/components/__tests__/ragFileLog.test.tsx` (new)

**Interfaces:**
- Consumes: from Task 5, `GET /api/rag/collections/{id}/log` and `log_counts`.
- Produces, in `app/lib/rag.ts`:
  - `LogStatus`, `LogFilter = "indexed" | "skipped" | "failed" | "removed"`;
  - `LogCounts {indexed, skipped, failed}`;
  - `LogFile {path, status, size, mtime, error, indexed_at, last_seen_at, removed_at, chunks}`;
  - `LOG_STATUS_LABEL: Record<LogStatus, string>`;
  - `getFileLog(id, {status?, q?, limit, offset}, signal?) -> {files, total}`;
  - `CollectionSummary.log_counts: LogCounts`.
- Produces `RagFileLog({collectionId, selected, onSelect, reload?})`: a paged
  `DataTable` with a **Search files** box and a **File status** select (All
  files, Indexed, Skipped, Failed, Removed). Only indexed paths are buttons
  (they open chunks).

- [ ] **Step 1: Write the failing tests**

Create `services/admin-ui/app/components/__tests__/ragFileLog.test.tsx`:

```tsx
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RagFileLog from "../RagFileLog";
import type { LogFile } from "../../lib/rag";

const indexed: LogFile = { path: "rag-data/jira/DEMO-1.md", status: "indexed", size: 2048, mtime: 1, error: null,
  indexed_at: "2026-10-05T09:00:00Z", last_seen_at: "2026-10-05T09:00:00Z", removed_at: null, chunks: 13 };
const failed: LogFile = { ...indexed, path: "rag-data/jira/DEMO-2.md", status: "failed", indexed_at: null, chunks: 0,
  error: "Could not parse this file: unbalanced table" };

function api(files: LogFile[] = [indexed, failed], total = files.length) {
  const fetchMock = vi.fn(async () => Response.json({ files, total }));
  vi.stubGlobal("fetch", fetchMock);
  return () => fetchMock.mock.calls.map(([url]) => String(url));
}

test("shows each file's status, size, chunks and error; only indexed files open chunks", async () => {
  api();
  const onSelect = vi.fn();
  const user = userEvent.setup();
  render(<RagFileLog collectionId="c1" selected="" onSelect={onSelect} />);
  const bad = (await screen.findByText("rag-data/jira/DEMO-2.md")).closest("tr")!;
  expect(within(bad).getByText("Failed")).toBeInTheDocument();
  expect(within(bad).getByText("Could not parse this file: unbalanced table")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "rag-data/jira/DEMO-2.md" })).toBeNull();
  const ok = screen.getByRole("button", { name: "rag-data/jira/DEMO-1.md" });
  const row = ok.closest("tr")!;
  expect(within(row).getByText("Indexed")).toBeInTheDocument();
  expect(within(row).getByText("2.0 KB")).toBeInTheDocument();
  expect(within(row).getByText("13")).toBeInTheDocument();
  await user.click(ok);
  expect(onSelect).toHaveBeenCalledWith("rag-data/jira/DEMO-1.md");
});

test("the first request asks for every file except removed ones, page one", async () => {
  const urls = api();
  render(<RagFileLog collectionId="c1" selected="" onSelect={() => {}} />);
  await screen.findByText("Showing 1–2 of 2");
  expect(urls()[0]).toBe("/api/rag/collections/c1/log?limit=25&offset=0");
});

test("paging, the status filter and the path search are sent to the API", async () => {
  const urls = api([indexed], 60);
  const user = userEvent.setup();
  render(<RagFileLog collectionId="c1" selected="" onSelect={() => {}} />);
  await screen.findByText("Showing 1–25 of 60");
  await user.click(screen.getByRole("button", { name: "Next page" }));
  await waitFor(() => expect(urls().at(-1)).toBe("/api/rag/collections/c1/log?limit=25&offset=25"));
  await user.click(screen.getByRole("combobox", { name: "File status" }));
  await user.click(screen.getByRole("option", { name: "Failed" }));
  await waitFor(() => expect(urls().at(-1)).toBe("/api/rag/collections/c1/log?status=failed&limit=25&offset=0"));
  await user.type(screen.getByRole("searchbox", { name: "Search files" }), "DEMO");
  await waitFor(() => expect(urls().at(-1)).toBe("/api/rag/collections/c1/log?status=failed&q=DEMO&limit=25&offset=0"));
});

test("an empty log explains how to fill it", async () => {
  api([], 0);
  render(<RagFileLog collectionId="c1" selected="" onSelect={() => {}} />);
  expect(await screen.findByText("No files yet. Refresh the collection to index its folders.")).toBeInTheDocument();
});
```

In `services/admin-ui/app/components/__tests__/ragCollections.test.tsx`:
- Add `log_counts: { indexed: 90, skipped: 2, failed: 1 },` to `base`.
- Replace `noFiles` with `const noLog = { files: [], total: 0 };`, and in every
  detail mock replace `if (url.endsWith("/files")) return Response.json(noFiles);`
  with `if (url.includes("/log?")) return Response.json(noLog);`.
- Replace the `detail lists full paths and chunks…` test with:

```tsx
test("detail shows status counts, the file log and a file's chunks", async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url.includes("/chunks")) return Response.json({ chunks: [{ position: 0, text: "First chunk text", embedded: false }] });
    if (url.includes("/log?")) return Response.json({ total: 1, files: [{ path: "rag-data/jira/DEMO-1.md", status: "indexed", size: 10,
      mtime: 1, error: null, indexed_at: "2026-10-05T09:00:00Z", last_seen_at: "2026-10-05T09:00:00Z", removed_at: null, chunks: 13 }] });
    return Response.json(base);
  });
  const user = userEvent.setup();
  detail(fetchMock);
  expect(await screen.findByText("90 indexed · 2 skipped · 1 failed")).toBeInTheDocument();
  const file = await screen.findByRole("button", { name: "rag-data/jira/DEMO-1.md" });
  expect(within(file.closest("tr")!).getByText("13")).toBeInTheDocument();
  await user.click(file);
  expect(await screen.findByText("First chunk text")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/rag/collections/c1/chunks?path=rag-data%2Fjira%2FDEMO-1.md"), expect.anything());
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/files"))).toBe(false);
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm test -- ragFileLog ragCollections`
Expected: FAIL. `RagFileLog` does not exist, and the detail page has no status
counts.

- [ ] **Step 3: Log types and client in `services/admin-ui/app/lib/rag.ts`**

Delete `CollectionFile` and `getCollectionFiles`. Add `log_counts: LogCounts;` to
`CollectionSummary`, and add:

```ts
export type LogStatus = "indexed" | "skipped_binary" | "skipped_too_large" | "skipped_unreadable" | "failed" | "removed";
export type LogFilter = "indexed" | "skipped" | "failed" | "removed";
export interface LogCounts { indexed: number; skipped: number; failed: number }
export interface LogFile {
  path: string; status: LogStatus; size: number | null; mtime: number | null; error: string | null;
  indexed_at: string | null; last_seen_at: string; removed_at: string | null; chunks: number;
}
export const LOG_STATUS_LABEL: Record<LogStatus, string> = {
  indexed: "Indexed", skipped_binary: "Skipped · binary", skipped_too_large: "Skipped · too large",
  skipped_unreadable: "Skipped · unreadable", failed: "Failed", removed: "Removed",
};
export function getFileLog(id: string, query: { status?: LogFilter; q?: string; limit: number; offset: number }, signal?: AbortSignal) {
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  if (query.q) params.set("q", query.q);
  params.set("limit", String(query.limit));
  params.set("offset", String(query.offset));
  return ragRequest<{ files: LogFile[]; total: number }>(`collections/${id}/log?${params}`, "GET", undefined, signal);
}
```

- [ ] **Step 4: Create `services/admin-ui/app/components/RagFileLog.tsx`**

```tsx
import { useEffect, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { getFileLog, LOG_STATUS_LABEL, type LogFile, type LogFilter, type LogStatus } from "../lib/rag";
import DataTable from "./DataTable";
import { when } from "./RagCollectionsView";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Badge } from "~/components/ui/badge";
import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";

const ALL = "all";
const FILTERS: { value: LogFilter | typeof ALL; label: string }[] = [
  { value: ALL, label: "All files" }, { value: "indexed", label: "Indexed" }, { value: "skipped", label: "Skipped" },
  { value: "failed", label: "Failed" }, { value: "removed", label: "Removed" },
];
const bytes = (n: number) => n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
const variant = (status: LogStatus) => status === "failed" ? "destructive" as const : status === "indexed" ? "secondary" as const : "outline" as const;

/** The collection's file log: what the last sync decided for each file, paged and filtered on the server. */
export default function RagFileLog({ collectionId, selected, onSelect, reload = 0 }: {
  collectionId: string; selected: string; onSelect: (path: string) => void; reload?: number;
}) {
  const [status, setStatus] = useState<LogFilter | typeof ALL>(ALL);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [files, setFiles] = useState<LogFile[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getFileLog(collectionId, { status: status === ALL ? undefined : status, q: q.trim() || undefined, limit: size, offset: (page - 1) * size }, controller.signal)
      .then((result) => { if (!controller.signal.aborted) { setFiles(result.files); setTotal(result.total); setError(""); } })
      .catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not load the file log"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [collectionId, status, q, page, size, reload]);
  const columns: ColumnDef<LogFile, unknown>[] = [
    { id: "path", header: "Path", cell: ({ row }) => row.original.status === "indexed"
      ? <Button variant="link" className="h-auto p-0 break-all" aria-pressed={row.original.path === selected} onClick={() => onSelect(row.original.path)}>{row.original.path}</Button>
      : <span className="break-all">{row.original.path}</span> },
    { id: "status", header: "Status", cell: ({ row }) => <Badge variant={variant(row.original.status)}>{LOG_STATUS_LABEL[row.original.status]}</Badge> },
    { id: "size", header: "Size", cell: ({ row }) => row.original.size === null ? "—" : bytes(row.original.size) },
    { id: "chunks", header: "Chunks", cell: ({ row }) => row.original.chunks },
    { id: "indexed", header: "Indexed at", cell: ({ row }) => <span className="whitespace-nowrap text-xs">{when(row.original.indexed_at)}</span> },
    { id: "error", header: "Error", cell: ({ row }) => row.original.error && <span className="text-xs text-destructive">{row.original.error}</span> },
  ];
  const toolbar = <div className="flex flex-wrap items-center gap-2">
    <Input type="search" aria-label="Search files" placeholder="Search by path…" className="h-9 w-64" value={q}
      onChange={(e) => { setQ(e.target.value); setPage(1); }} />
    <Select value={status} onValueChange={(value) => { setStatus(value as LogFilter | typeof ALL); setPage(1); }}>
      <SelectTrigger className="h-9 w-40" aria-label="File status"><SelectValue /></SelectTrigger>
      <SelectContent><SelectGroup>{FILTERS.map((filter) => <SelectItem key={filter.value} value={filter.value}>{filter.label}</SelectItem>)}</SelectGroup></SelectContent>
    </Select>
  </div>;
  return <div className="flex flex-col gap-2">
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    <DataTable columns={columns} data={files} total={total} page={page} pageSize={size} rowId={(file) => file.path} loading={loading}
      onPageChange={setPage} onPageSizeChange={(value) => { setSize(value); setPage(1); }} toolbar={toolbar}
      empty={status === ALL && !q.trim() ? "No files yet. Refresh the collection to index its folders." : "No files match these filters."} />
  </div>;
}
```

The column header is "Indexed at" so it never collides with the "Indexed" status
badge or filter option.

- [ ] **Step 5: Use the log on the detail page**

In `services/admin-ui/app/components/RagCollectionDetail.tsx`:
- imports: drop `getCollectionFiles`, `CollectionFile` and `SkippedCounts`; add
  `import RagFileLog from "./RagFileLog";`;
- delete the `Files` interface and the `files` state; add
  `const [logVersion, setLogVersion] = useState(0);`;
- `load` becomes:

```tsx
  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const row = await getCollection(id, signal);
      if (signal?.aborted) return;
      setCollection(row); setLogVersion((value) => value + 1); setError("");
    } catch (e) { if (!signal?.aborted) setError(e instanceof Error ? e.message : "Could not load the collection"); }
  }, [id]);
```

- in the `<dl>`, replace the "Files indexed" pair with:

```tsx
        <dt className="text-muted-foreground">Files</dt><dd>{c.log_counts.indexed} indexed · {c.log_counts.skipped} skipped · {c.log_counts.failed} failed</dd>
        <dt className="text-muted-foreground">Files found</dt><dd>{c.file_count}{c.truncated && " (or more)"}</dd>
```

- replace the whole "Indexed files" `<section>` with:

```tsx
    <section className="flex flex-col gap-3">
      <h3 className="text-base font-semibold">File log</h3>
      <p className="text-xs text-muted-foreground">Not listed: {c.skipped.links} links, {c.skipped.special} special files, {c.skipped.unreadable} unreadable entries, {c.skipped.invalid_names} invalid names.</p>
      <RagFileLog collectionId={id} selected={selected} onSelect={(path) => void select(path)} reload={logVersion} />
    </section>
```

- [ ] **Step 6: Run the tests and the type check**

Run: `cd services/admin-ui && pnpm test -- ragFileLog ragCollections && pnpm exec tsc --noEmit`
Expected: 4 + 10 passed; `tsc` reports no errors (`getCollectionFiles` has no
other users).

- [ ] **Step 7: Commit**

```bash
git add services/admin-ui/app/lib/rag.ts services/admin-ui/app/components/RagFileLog.tsx \
  services/admin-ui/app/components/RagCollectionDetail.tsx services/admin-ui/app/components/__tests__/ragFileLog.test.tsx \
  services/admin-ui/app/components/__tests__/ragCollections.test.tsx
git commit -m "feat(admin-ui): collection file log table with status filter, path search and paging

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: admin-ui — auto-sync settings, Auto/Manual label, Auto job mode

**Files:**
- Modify: `services/admin-ui/app/lib/rag.ts`: `auto_sync`, `poll_interval_minutes` and `syncLabel()`; `RagJob.input.auto`.
- Modify: `services/admin-ui/app/components/RagCollectionForm.tsx`: Auto-sync switch and interval.
- Modify: `services/admin-ui/app/components/RagCollectionsView.tsx`: a **Sync** column.
- Modify: `services/admin-ui/app/components/RagCollectionDetail.tsx`: a **Sync** row in the summary.
- Modify: `services/admin-ui/app/components/RagJobsView.tsx`: mode `Auto`.
- Test: `services/admin-ui/app/components/__tests__/ragCollections.test.tsx`, `services/admin-ui/app/components/__tests__/ragJobs.test.tsx`

**Interfaces:**
- Consumes: from Task 6, `auto_sync`, `poll_interval_minutes` (1..1440) and the job input `{"auto": true}`.
- Produces:
  - `CollectionSettings.auto_sync: boolean`,
    `CollectionSettings.poll_interval_minutes: number`, and
    `DEFAULT_SETTINGS` with `false` / `5`;
  - `syncLabel(c) -> "Auto · every N min" | "Manual"`;
  - `RagJob.input: { force?: boolean; auto?: boolean }`.

- [ ] **Step 1: Write the failing tests**

In `services/admin-ui/app/components/__tests__/ragCollections.test.tsx`:
- add `auto_sync: false, poll_interval_minutes: 5,` to `base`;
- in `New collection creates…`, the expected body gains
  `auto_sync: false, poll_interval_minutes: 5`;
- in `Edit changes search settings only`, the expected PUT body gains
  `auto_sync: false, poll_interval_minutes: 5`;
- append:

```tsx
test("auto-sync settings are saved and the list shows Auto or Manual", async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/log?")) return Response.json(noLog);
    if (url === "/api/rag/collections") return Response.json({ collections: [{ ...base, auto_sync: true, poll_interval_minutes: 15 }, notes] });
    return Response.json(init?.method === "PUT" ? { ...base, revision: 3 } : base);
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  const list = render(<MemoryRouter><RagCollectionsView /></MemoryRouter>);
  const auto = (await screen.findByRole("link", { name: "jira" })).closest("tr")!;
  expect(within(auto).getByText("Auto · every 15 min")).toBeInTheDocument();
  expect(within(screen.getByRole("link", { name: "notes" }).closest("tr")!).getByText("Manual")).toBeInTheDocument();
  list.unmount();
  detail(fetchMock);
  expect(await screen.findByText("Manual")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Check every (minutes)")).toBeDisabled();
  await user.click(screen.getByRole("switch", { name: "Auto-sync" }));
  await user.clear(screen.getByLabelText("Check every (minutes)"));
  await user.type(screen.getByLabelText("Check every (minutes)"), "15");
  await user.click(screen.getByRole("button", { name: "Save collection" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit)?.method === "PUT")).toBe(true));
  const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit)?.method === "PUT")!;
  expect(JSON.parse((put[1] as RequestInit).body as string)).toMatchObject({ auto_sync: true, poll_interval_minutes: 15, revision: 2 });
});
```

In `services/admin-ui/app/components/__tests__/ragJobs.test.tsx`, append:

```tsx
test("automatic syncs show mode Auto", async () => {
  setup("/rag/jobs", [{ ...job, input: { auto: true } }]);
  const row = (await screen.findByText("jira", { selector: "td *, td" })).closest("tr")!;
  expect(within(row).getByText("Auto")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm test -- ragCollections ragJobs`
Expected: FAIL. There is no "Auto · every 15 min", no Auto-sync switch, and the
mode shows "Incremental".

- [ ] **Step 3: Types in `services/admin-ui/app/lib/rag.ts`**

- `RagJob.input` becomes `{ force?: boolean; auto?: boolean }`.
- `CollectionSettings` gains `auto_sync: boolean; poll_interval_minutes: number;`.
- `DEFAULT_SETTINGS` gains `auto_sync: false, poll_interval_minutes: 5`.
- `CollectionSummary` gains `last_polled_at: string | null;`.
- Add:

```ts
export const syncLabel = (c: Pick<CollectionSettings, "auto_sync" | "poll_interval_minutes">) =>
  c.auto_sync ? `Auto · every ${c.poll_interval_minutes} min` : "Manual";
```

In `ragCollections.test.tsx`, also add `last_polled_at: null,` to `base`.

- [ ] **Step 4: The form**

In `RagCollectionForm.tsx` `SettingsFields`, add state:

```tsx
  const [autoSync, setAutoSync] = useState(initial.auto_sync);
  const [pollInterval, setPollInterval] = useState(initial.poll_interval_minutes);
```

Add `auto_sync: autoSync, poll_interval_minutes: pollInterval` to `value` in
`submit()`. After the chunk size and overlap grid, add:

```tsx
      <Field orientation="horizontal"><Switch id="collection-auto-sync" checked={autoSync} onCheckedChange={setAutoSync} /><FieldLabel htmlFor="collection-auto-sync">Auto-sync</FieldLabel></Field>
      <Field><FieldLabel htmlFor="collection-poll-interval">Check every (minutes)</FieldLabel>
        <Input id="collection-poll-interval" type="number" required min={1} max={1440} disabled={!autoSync} value={pollInterval} onChange={(e) => setPollInterval(Number(e.target.value))} />
        <FieldDescription>RAG checks the folders at this interval and syncs changes once they have been quiet for 2 minutes.</FieldDescription></Field>
```

- [ ] **Step 5: List, detail and jobs**

- `RagCollectionsView.tsx`: import `syncLabel`. Add `<TableHead>Sync</TableHead>`
  after "Embedded", and `<TableCell className="align-top text-xs">{syncLabel(c)}</TableCell>`
  in each row. The empty row's `colSpan` becomes 8.
- `RagCollectionDetail.tsx`: import `syncLabel`; after the "Embedded chunks" pair
  add `<dt className="text-muted-foreground">Sync</dt><dd>{syncLabel(c)}</dd>`.
- `RagJobsView.tsx`: the mode column becomes
  `cell: ({ row }) => row.original.input.auto ? "Auto" : row.original.input.force ? "Force rebuild" : "Incremental"`.

- [ ] **Step 6: Run the tests and the type check**

Run: `cd services/admin-ui && pnpm test -- ragCollections ragJobs && pnpm exec tsc --noEmit`
Expected: every test in both files passes; `tsc` reports no errors.

- [ ] **Step 7: Commit**

```bash
git add services/admin-ui/app/lib/rag.ts services/admin-ui/app/components/RagCollectionForm.tsx \
  services/admin-ui/app/components/RagCollectionsView.tsx services/admin-ui/app/components/RagCollectionDetail.tsx \
  services/admin-ui/app/components/RagJobsView.tsx services/admin-ui/app/components/__tests__/ragCollections.test.tsx \
  services/admin-ui/app/components/__tests__/ragJobs.test.tsx
git commit -m "feat(admin-ui): auto-sync settings, Auto/Manual label and Auto job mode

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: admin-ui — remove the Searchable switch and the Jira collection field

**Files:**
- Modify: `services/admin-ui/app/lib/codeProjects.ts`: drop `searchable`, `collection`, `search_error`, `CodeProjectSearch`, `setCodeProjectSearch` and `collectionSlug`.
- Modify: `services/admin-ui/app/components/CodeProjectsView.tsx`: drop the Search column and its state.
- Modify: `services/admin-ui/app/lib/api.ts`: `exportJiraTicket(ticketId, connectionRevision, signal?)`; `JiraExportResult` without `collection`.
- Modify: `services/admin-ui/app/components/JiraTicketsView.tsx`: drop the Target collection field.
- Modify: `services/admin-ui/app/components/StorageOverviewView.tsx`: the RAG notes.
- Test: `services/admin-ui/app/components/__tests__/codeProjects.test.tsx`, `jira.test.tsx`, `storageOverview.test.tsx`

**Interfaces:**
- Consumes: from Task 2, `POST /api/jira/tickets/{id}/export` with `{connection_revision}` → `{path, status, hash, issue_key, comments}`; code project rows without search fields.
- Produces:
  - `exportJiraTicket(ticketId: string, connectionRevision: number, signal?: AbortSignal)`;
  - `JiraExportResult {issue_key, path, status, hash, comments}`.

- [ ] **Step 1: Write the failing tests**

`codeProjects.test.tsx`:
- remove `searchable:false, collection:null, search_error:null` from `project`;
- delete `putBody` and the seven Searchable tests (`Searchable switch adds…`
  through `turning Searchable on with an empty field…`);
- append:

```tsx
test('code projects have no Searchable switch or collection field and never call RAG', async () => {
  const fetch = api([project]); render(<CodeProjectsView />);
  expect((await screen.findAllByText('Payments')).length).toBeGreaterThan(0);
  expect(screen.queryByRole('switch', { name: /Searchable/ })).toBeNull();
  expect(screen.queryByLabelText(/Search collection/)).toBeNull();
  expect(fetch.mock.calls.some(([url]) => String(url).startsWith('/api/rag/'))).toBe(false);
});
```

`jira.test.tsx`:
- In `export sends only selected IDs and destination, then links to RAG processing`
  (rename it `export sends only the selected ticket and links to RAG collections`):
  - delete the `Target collection` lines and the datalist assertion;
  - the export body expectation becomes `toEqual({ connection_revision: 2 })`;
  - the summary text becomes
    `"0 files saved, 1 unchanged in rag-data/jira. 5 comments included."`;
  - the link assertion becomes
    `expect(screen.getByRole("link", { name: "RAG → Collections" })).toHaveAttribute("href", "/rag/collections");`;
  - add
    `expect(fetch.mock.calls.some(([url]) => String(url).startsWith("/api/rag/"))).toBe(false);`.
- Delete `a new collection name is sent and invalid names disable export`.
- Append:

```tsx
test("the export has no collection field", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(twoTickets))));
  render(<MemoryRouter><JiraTicketsView /></MemoryRouter>);
  await screen.findByText("Worker retries");
  expect(screen.queryByLabelText("Target collection")).toBeNull();
  expect(screen.getByText(/Each selected ticket becomes rag-data\/jira\/<KEY>\.md/)).toBeInTheDocument();
});
```

`storageOverview.test.tsx`: the RAG note expectation becomes
`expect(screen.getByText(/Jira exports go to rag-data\/jira/)).toBeInTheDocument();`.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd services/admin-ui && pnpm test -- codeProjects jira storageOverview`
Expected: FAIL. The Searchable switch and the Target collection field still
render, and the export still sends `collection`.

- [ ] **Step 3: Code projects**

- `lib/codeProjects.ts`: delete `searchable: boolean; collection: string | null; search_error: string | null;`
  from `CodeProject`, and delete `CodeProjectSearch`, `setCodeProjectSearch` and
  `collectionSlug`.
- `CodeProjectsView.tsx`:
  - drop `collectionSlug`, `setCodeProjectSearch` and `getCollectionNames` from
    the imports, and delete `COLLECTION_NAME`, `collectionNames`, `searchDrafts`,
    their effect, `searchDraft`, `searchTarget`, `searchInvalid` and
    `setSearch()`;
  - delete the `<TableHead>Search</TableHead>`, the whole Search `<TableCell>`
    and the `code-search-collections` datalist;
  - the card description ends after "…as a separate action." Add: "Git pulls
    update the project's folders; connect them to a RAG collection in RAG →
    Collections to make the code searchable."

- [ ] **Step 4: Jira export**

`lib/api.ts`:

```ts
export interface JiraExportResult {
  issue_key: string; path: string; status: "created" | "updated" | "unchanged"; hash: string; comments: number;
}
export const exportJiraTicket = (ticketId: string, connectionRevision: number, signal?: AbortSignal) =>
  request<JiraExportResult>(`/api/jira/tickets/${encodeURIComponent(ticketId)}/export`, {
    method: "POST", signal, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connection_revision: connectionRevision }),
  });
```

`JiraTicketsView.tsx`:
- delete the `getCollectionNames` import, `COLLECTION_NAME`, the `collection` and
  `known` state, `validCollection`, and the collection-names effect;
- `exportSummary` becomes `{ results: JiraExportResult[]; failures: string[] } | null`;
- in `exportTickets`, drop `destination`, call
  `exportJiraTicket(ticket.id, data.connection.revision, controller.signal)` and
  `setExportSummary({ results, failures })`;
- delete the Target collection `FieldGroup`;
- the export button becomes `disabled={busy || selectedTickets.length === 0}`;
- the `FieldDescription` becomes:
  "Each selected ticket becomes rag-data/jira/&lt;KEY&gt;.md, a Markdown file with
  its description and all comments visible to the Jira user. Re-exporting
  refreshes that ticket’s file. Collections that hold rag-data/jira index the
  tickets at their next sync.";
- the summary paragraph becomes:

```tsx
          <p>{exportSummary.results.filter((result) => result.status !== "unchanged").length} files saved, {exportSummary.results.filter((result) => result.status === "unchanged").length} unchanged in rag-data/jira. {exportSummary.results.reduce((count, result) => count + result.comments, 0)} comments included.</p>
```

- the follow-up paragraph becomes:

```tsx
          {exportSummary.results.length > 0 && <p>Add rag-data/jira to a collection in <Link className="underline underline-offset-4" to="/rag/collections">RAG → Collections</Link> if it is not in one yet, then refresh that collection to update search.</p>}
```

`StorageOverviewView.tsx` line 120 becomes:

```tsx
        <Alert><AlertDescription>Documents are written to generated-documents and Jira exports go to rag-data/jira. Connect these folders to a collection in RAG → Collections (upgraded installs already have documents) and refresh it to make them searchable.</AlertDescription></Alert>
```

In line 18, the `rag` entry's `purpose` becomes
`'Files for search, including Jira ticket exports (rag-data/jira). Processing creates searchable text and embeddings in PostgreSQL.'`,
and its `layout` becomes
`'Folders → subfolders → source files. Connect folders to collections in RAG → Collections.'`.

- [ ] **Step 5: Run the tests, the type check and the leftover check**

Run: `cd services/admin-ui && pnpm test -- codeProjects jira storageOverview && pnpm exec tsc --noEmit`
Expected: all pass; no type errors.

Run: `git grep -n "searchable\|setCodeProjectSearch\|collectionSlug\|search_error\|Target collection\|jira-collection" -- services/admin-ui/app`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add services/admin-ui/app/lib/codeProjects.ts services/admin-ui/app/lib/api.ts services/admin-ui/app/components/CodeProjectsView.tsx \
  services/admin-ui/app/components/JiraTicketsView.tsx services/admin-ui/app/components/StorageOverviewView.tsx \
  services/admin-ui/app/components/__tests__/codeProjects.test.tsx services/admin-ui/app/components/__tests__/jira.test.tsx \
  services/admin-ui/app/components/__tests__/storageOverview.test.tsx
git commit -m "feat(admin-ui): remove the Searchable switch and the Jira collection field

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
# Part F — documentation and verification (Task 11)

### Task 11: Docs and full verification

**Files:**
- Modify: `docs/rag.md`
- Modify: `docs/user-guide.md` (sections 3, 5.3, 5.4, 5.8, 5.14, 6.A, 6.F and 8)
- Modify: `docs/jira.md` (API table and "Markdown export and RAG processing")
- Modify: `docs/documentation.md` (lines about the `documents` collection)
- Modify: `docs/database.md` (the RAG tables row)
- Modify: `README.md` ("Content and model configuration")

**Interfaces:**
- Consumes: the behaviour delivered by Tasks 1–10. The documentation repeats the
  Global Constraints' values and error texts exactly.

- [ ] **Step 1: Write the docs**

`docs/rag.md`:
- **Intro:**
  - a collection is a named search index with its own settings and one or more
    folders under `DATA_ROOT`;
  - RAG owns the configuration: administrators (or external tools through the
    API) create collections and connect folders;
  - sources only write files: Docs → Documents writes `generated-documents/…`,
    the Jira export `rag-data/jira/<KEY>.md`, and Git pulls `code/…`.
- **RAG pages list:**
  - Collections has **New collection**, Refresh and Refresh all stale, and shows
    "Auto · every N min" or "Manual";
  - the detail page has Edit, **Add folder** (folder browser or typed path),
    **Remove** per folder, Refresh, Rebuild and Remove (always available, with
    confirmation), the status counts and the **file log**;
  - Processing jobs shows mode Auto, Incremental or Force rebuild.
- **`## Collections`:**
  - field table: "Paths" becomes "Folders" (added and removed on the detail
    page or through the API); add **Auto-sync** (default off) and **Check
    every** (1–1440 minutes, default 5);
  - the name cannot be changed;
  - examples: `documents` → `generated-documents`; `jira` → `rag-data/jira`;
    `payments` → `code/payments`, `rag-data/jira` and `docs/runbooks`;
  - delete the sentence "Folders are never created by RAG except
    `rag-data/jira`…". Instead: adding a missing folder under `rag-data`
    creates it; a missing folder elsewhere is refused with `Folder does not
    exist`.
- **Replace `## Paths and sources` with `## Folders`:**
  - rules: no overlap within a collection (`Folder overlaps <other> in
    collection <name>`), sharing across collections allowed, patterns matched
    relative to each folder;
  - adding or removing a folder shows Needs refresh;
  - removing a folder drops its indexed files and file log rows, and the files
    stay on disk;
  - removing a collection drops its index, folders, file log and agent grants,
    and the files stay;
  - the folder browser lists visible subfolders only (no hidden folders, `.git`,
    `node_modules` or links);
  - a table of where sources write: Docs → Documents → `generated-documents`;
    Jira → `rag-data/jira/<KEY>.md`; code projects → `code/<folder>`;
  - on a fresh install, create a `documents` collection and add
    `generated-documents`, and add `rag-data/jira` to a collection to search
    Jira tickets.
- **New `### Upgrading to 0066`:**
  - folder owners and code-project **Searchable** settings are removed, and
    every existing connection stays as an ordinary folder (`documents` keeps
    `generated-documents`, `jira` keeps `rag-data/jira`, and code folders stay
    in their collections);
  - the Jira export no longer asks for a collection;
  - each indexed file starts in the file log as indexed, and the first sync
    after the upgrade reads each file once without re-parsing unchanged
    content;
  - rolling back re-adds the owner and search columns empty.
- Keep `### Upgrading to 0065` and `### Upgrading to collections`, but in 0065
  replace the advice to "detach the old path" with "remove the old folder on the
  collection detail page".
- **New `## File log`:**
  - the statuses and their meaning;
  - the size/mtime shortcut, the content-hash shortcut, and what the fingerprint
    covers (processing settings, patterns, embedding profile, maximum file size);
  - Rebuild re-reads everything;
  - unreadable files are retried at each sync;
  - a parse failure is logged `failed` (`Could not parse this file: …`) and the
    sync succeeds with a failed count;
  - an unavailable embedding model fails the sync and leaves the log unchanged;
  - the detail page table: filters, path search, paging, chunks for indexed
    files.
- **New `## Automatic sync`:**
  - auto-sync and the interval;
  - the worker checks due collections by size and mtime against the file log,
    so copies with old mtimes are noticed;
  - a change must be quiet for 2 minutes;
  - no second job while one is queued or running;
  - a missing folder is never synced;
  - failed syncs are retried each interval;
  - changing only auto-sync settings keeps the collection Up to date;
  - not in this step: inotify, S3 and per-folder intervals.
- **`## Status, freshness and Refresh`:** mention Auto next to Refresh, Refresh
  all stale and Rebuild.
- **`## Incremental processing and publication`:**
  - one bad file no longer fails the sync;
  - the publish re-check compares sizes and mtimes and re-hashes only the files
    that moved.
- **`## Database`:** add `rag_file_log` (per-file status) and the
  `rag_collections` columns `auto_sync`, `poll_interval_minutes` and
  `last_polled_at`. `rag_collection_paths` has no owner columns.
- **`## API`:**
  - admin table rows:
    - `POST /collections` (create, 409 `Collection <name> already exists`);
    - `POST /collections/{id}/paths` with `{source_path}`;
    - `DELETE /collections/{id}/paths/{path_id}`;
    - `GET /folders?path=`;
    - `GET /collections/{id}/log?status=&q=&limit=&offset=`;
    - `PUT /collections/{id}` now also takes `auto_sync` and
      `poll_interval_minutes`;
    - `DELETE /collections/{id}` with no owner refusal;
  - delete the `by-name` row, the "Source API" table and the proxy note about
    `owner_*`;
  - add "Service-only file API (`X-Rag-Key`)" with
    `GET file?path=<rag-data path>` (no collection) and
    `PUT file {path, content, expected_hash}` (rag-data only), both used by the
    Jira export;
  - state that external tools use the same backoffice routes with `X-Rag-Key`.

`docs/user-guide.md`:
- **§3 step 3:** "Create collections in **RAG → Collections** and add folders:
  `generated-documents` for documents, `rag-data/jira` for Jira, `code/<folder>`
  for code; then **Refresh**, or switch on **Auto-sync**."
- **§5.3:**
  - rewrite the intro and Workflow: New collection → Add folder (browse or type)
    → Refresh or Auto-sync → watch Embedded;
  - Collections bullets: the Sync column; Refresh all stale; Auto-sync checks
    every N minutes and waits 2 quiet minutes;
  - Edit shows settings including Auto-sync and Check every;
  - the detail page: folders with Remove, the status counts, the file log
    (filters Indexed, Skipped, Failed, Removed; path search; paging; chunks of
    indexed files);
  - Remove is always available and keeps the files;
  - delete "There is no New collection", Detach and the owner refusal;
  - "skipped and counted" now says these files are listed in the file log, and
    one failing file no longer fails the sync.
- **§5.4:** delete the **Searchable** paragraph. Instead: "Code projects only
  clone and pull into `code/…`. To search code, add the project's folders to a
  collection in RAG → Collections; archiving a project does not change RAG."
- **§5.8 Tickets:**
  - step 3: "**Export selected** writes each ticket to `rag-data/jira/<KEY>.md`
    …";
  - step 4: "Make sure a collection holds `rag-data/jira` (RAG → Collections →
    Add folder), then Refresh it or let Auto-sync pick it up."
- **§5.14 and §8:** "searchable through a collection that holds
  `generated-documents` (`documents` on upgraded installs)".
- **§6.A step 8 and §6.F:** the same wording; §6.F drops "into the target
  collection (default `jira`)".

`docs/jira.md`:
- API table: `POST /api/jira/tickets/{ticket_id}/export` takes
  `{ "connection_revision": 1 }` and returns path, status, hash, issue key and
  comment count.
- "Markdown export and RAG processing" step 2:
  - tickets are written to `rag-data/jira/<KEY>.md`, with no collection choice;
  - a RAG error (such as the service being unavailable) fails that ticket's
    export with its message and writes nothing;
  - step 3: add `rag-data/jira` to a collection once (RAG → Collections → Add
    folder), then Refresh it or turn on Auto-sync.

`docs/documentation.md` (lines 107 and 195): "The collection that holds
`generated-documents` (`documents` on upgraded installs; create it on a fresh
install) indexes this folder".

`docs/database.md` line 31: add `rag_file_log` to the RAG tables, and "folders in
`ka.rag_collection_paths`".

`README.md` "Content and model configuration": replace "Sources add their own
paths: …" with "Administrators create collections and connect folders in RAG →
Collections; sources only write files. Auto-sync checks folders at an interval."

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
worktree and run the same test. Accept it only if it also fails there, and record
it in the PR description.

- [ ] **Step 3: Check for leftovers**

Run: `git grep -n "owner_kind\|owner_key\|search_collection\|search_error\|by-name\|ensure_documents\|code_project_search\|setCodeProjectSearch\|Searchable\|Target collection\|Detach" -- services deploy README.md docs/rag.md docs/user-guide.md docs/jira.md docs/documentation.md docs/database.md`
Expected: only these hits:
- `services/admin-api/tests/test_rag_collection_paths_migration.py` and
  `services/admin-api/tests/test_rag_managed_collections_migration.py`, which
  check the 0065 and 0066 schemas;
- the "Upgrading to 0065" and "Upgrading to 0066" sections of `docs/rag.md`.

Run: `sed -n '/^  repository-worker:/,/depends_on:/p' deploy/compose.yaml | grep -c "RAG"`
Expected: `0`. The repository worker no longer talks to RAG.

- [ ] **Step 4: Commit**

```bash
git add docs/rag.md docs/user-guide.md docs/jira.md docs/documentation.md docs/database.md README.md
git commit -m "docs: RAG-managed collections, file log and polling

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
