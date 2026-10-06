# RAG-managed collections, file log and polling — design

Date: 2026-10-06. Status: draft for review. Supersedes the "sources own their paths"
part of `2026-10-06-source-owned-collections-design.md`. The multi-folder model from
that design stays.

## Goal

RAG owns the configuration. An administrator, or an external tool through the RAG
API, creates collections and connects folders under `DATA_ROOT` to them. Sources
(Jira export, Docs → Documents, Git sync of code projects, or anything outside the
application) only write files into their folders. RAG polls connected folders at a
set interval and keeps a per-file log (path → hash → status), so unchanged files are
never processed again and every skipped or failed file is visible.

## Part 1 — RAG-side configuration

### What changes

| Area | Before (source-owned) | After (RAG-managed) |
| --- | --- | --- |
| Creating collections | Sources create them by name | RAG → Collections → **New collection**, or `POST collections` |
| Connecting folders | Sources add their own paths (with owner) | **Add folder** on a collection, or `POST collections/{id}/paths`; any folder under `DATA_ROOT` |
| Ownership | `owner_kind/owner_id/owner_key`; owned paths cannot be detached; Remove guarded | No owners. Any folder can be added or removed; a collection can always be removed (files kept) |
| Jira export | Chooses a target collection | Always writes `rag-data/jira/<KEY>.md`; no collection field |
| Docs → Documents | Registers `generated-documents` into `documents` at startup | Only writes `generated-documents/…`; no registration |
| Code projects | **Searchable** switch + collection; reconcile on save, pull and archive | Only clone and pull into `code/…`; no switch, no RAG calls |

### Model (migration 0066)

- `ka.rag_collection_paths` loses `owner_kind`, `owner_id`, `owner_key` and the
  owner index.
- `ka.repositories` loses `search_collection` and `search_error`.
- Kept from 0065:
  - a collection has several folders (`source_path` relative to `DATA_ROOT`, same
    validation);
  - folders in one collection may not overlap;
  - indexed files use full paths with `path_id` cascade.
- Existing data keeps every connection. `documents` keeps `generated-documents` and
  `jira` keeps `rag-data/jira`, now as ordinary folders.
- The down migration re-adds the columns as NULL.

### RAG API (backoffice, `x-rag-key`; the same calls serve the admin UI and external tools)

| Call | Meaning |
| --- | --- |
| `POST collections` with `{name, description?, settings…}` | Create a collection. 409 on a duplicate name |
| `PUT collections/{id}` | Search settings (full body, as now) |
| `DELETE collections/{id}?revision&confirmed=true` | Remove the collection: index, folders, file log and agent grants go; files stay on disk |
| `POST collections/{id}/paths` with `{source_path}` | Connect a folder. 409 on overlap, 400 on an invalid path. A missing folder under `rag-data` is created; a missing folder elsewhere returns 409 "Folder does not exist". Bumps the revision |
| `DELETE collections/{id}/paths/{path_id}` | Disconnect a folder: its indexed files and log rows go, files stay. Bumps the revision |
| `GET folders?path=` | List subfolders of a `DATA_ROOT` area folder, for the folder browser. Hidden and skipped folders are not listed; the path rules are the same as `source_path` |
| `PUT file`, internal | Still used by Jira export to write `rag-data/jira/<KEY>.md` by full path |

**Removed:** the `by-name` endpoints, `DELETE paths?owner…`, and creation-only
settings.

### admin-api and admin UI

- **admin-api:**
  - removes `code_project_search.py`, the Searchable route and archive releasing,
    worker reconciliation, Documents' startup registration, and the owner client
    functions;
  - Jira export drops `collection` and writes to `rag-data/jira`;
  - the archive route stays as a plain archive.
- **Admin RAG proxy:** allows `POST collections`,
  `POST/DELETE collections/<id>/paths…` and `GET folders`; the by-name routes are
  dropped.
- **Admin UI:**
  - Collections gets **New collection**, a form with name, description and search
    settings;
  - the detail page has **Add folder** (a folder browser over the `DATA_ROOT` areas,
    or a typed path) and **Remove folder** on each folder;
  - Remove collection is always available, with confirmation;
  - the Searchable switch is removed from Code projects and the collection field from
    the Jira dialog;
  - the Source column and badges are removed.

## Part 2 — File log

### Table `ka.rag_file_log`

| Column | Meaning |
| --- | --- |
| `collection_id`, `path_id`, `path` | The full path under `DATA_ROOT`; unique per collection |
| `size`, `mtime` | From the last time the file was seen |
| `content_hash` | sha256 of the content, NULL until the file is read |
| `fingerprint` | The collection processing settings (patterns, chunking, embedding profile) the file was last processed with |
| `status` | `indexed`, `skipped_binary`, `skipped_too_large`, `skipped_unreadable`, `failed` or `removed` |
| `error` | Message for `failed` and `skipped_unreadable` |
| `first_seen_at`, `last_seen_at`, `indexed_at`, `removed_at` | Timestamps |

`rag_files` and `rag_chunks` stay the search index. The log is the per-file history
of what sync decided.

### Sync with the log

For every file the walk finds (include/exclude, hidden and skip rules as today):

1. **Size and mtime equal the log, and the fingerprint equals the current one:** the
   file is not opened. Only `last_seen_at` is updated.
2. **Size or mtime differ:** read and hash.
   - Same hash and same fingerprint: update size, mtime and `last_seen_at`; no
     parsing.
   - Otherwise process it as today. Binary, too large or unreadable gives the
     matching `skipped_*` status. A parse or embedding error gives `failed` with the
     message, and the rest of the sync continues. Success gives `indexed`.
3. **Fingerprint differs** (settings changed): process again, even if the content is
   unchanged. Vectors are still reused by input hash.
4. **Files in the log that were not seen:** `removed`, and their index rows deleted.

- **Rebuild** (`force`) ignores the size/mtime shortcut and re-reads every file.
- The publish safety from today stays: the revision check and the manifest
  re-check, now done against the log's size and mtime plus hashes for changed files.
- **One file failing no longer fails the whole sync.** The job succeeds with a
  `failed` count; it fails only on collection-level errors (missing folder, limits,
  settings changed, embedding model unavailable).

### API and UI

- `GET collections/{id}/log?status=&q=&limit=&offset=` returns `{files, total}`, with
  each file's path, status, size, indexed time, error and chunk count. The default is
  everything except `removed`.
- The collection detail page shows the log in a paged data table (the existing
  `DataTable`), filtered by status (Indexed, Skipped, Failed, Removed) and searchable
  by path. Selecting an indexed file shows its chunks (existing view).
- Status counts appear in the collection summary: indexed, skipped, failed.

## Part 3 — Polling

- **Per collection:** `auto_sync` (boolean, default off) and
  `poll_interval_minutes` (1..1440, default 5), edited with the search settings and
  shown in the list as "Auto · every 5 min" or "Manual".
- **The RAG worker loop** also checks collections that are due: `auto_sync` on, and
  `last_polled_at` older than the interval. For each due collection it:
  - records `last_polled_at = now()`;
  - computes freshness, using the walk with size/mtime only (no reads);
  - queues a sync (`requested_by` NULL, input `{"auto": true}`) if the collection
    needs refresh and the newest change across its folders is at least 2 minutes old
    (the quiet period);
  - skips it if a sync for the collection is already queued or running.
- **Missing folders:** a collection with a missing folder is not synced; its status
  stays Folder missing.
- **Manual Refresh, Refresh all stale and Rebuild** stay. The Jobs page shows
  automatic syncs with mode "Auto".
- **Not in this step:** inotify, S3, and per-folder intervals.

## Errors

| Problem | Behaviour |
| --- | --- |
| Adding a folder that overlaps another folder in the collection | 409 "Folder overlaps <other> in collection <name>" |
| Adding a missing folder outside `rag-data` | 409 "Folder does not exist" |
| A file that fails to parse or embed | Logged `failed` with its error; the sync continues and succeeds |
| An embedding model that is unavailable | The sync fails (as today); files keep their previous state |
| The poller finds a folder still changing | Waits until it has been quiet for 2 minutes |

## Testing

- **Migration 0066:** owner columns gone, connections kept, down migration restores
  NULL columns.
- **RAG API:**
  - create collection, add folder (overlap, missing, `rag-data` folder created),
    remove folder (index and log rows removed, files kept), remove collection;
  - the folder browser hides hidden folders and rejects bad paths;
  - removed endpoints return 404 or 405.
- **File log:**
  - an unchanged file is not opened (a test spy on read);
  - a touched file with the same content is not re-parsed;
  - changed content is re-indexed;
  - a settings change re-processes files;
  - binary, too-large and unreadable files are logged with their status;
  - a parse failure is logged and the sync still succeeds;
  - a deleted file is marked removed;
  - Rebuild re-reads everything;
  - the log API filters and pages.
- **Polling:**
  - a due collection with a quiet change is queued;
  - one changed less than 2 minutes ago is not;
  - one that isn't due is not checked;
  - an active job prevents a second one;
  - `auto_sync` off is never polled;
  - the interval is validated.
- **admin-api:**
  - Jira export writes `rag-data/jira` with no collection field;
  - no RAG calls from code projects or at startup;
  - the proxy allowlist.
- **UI:**
  - New collection;
  - Add folder through the browser and by typed path;
  - Remove folder;
  - Remove collection;
  - the log table with filters and paging;
  - auto-sync settings;
  - the Searchable switch and the Jira collection field are gone.
- **Docs:** `docs/rag.md`, the user guide, `docs/jira.md`, and the code-project docs.
