# RAG collections — design

Date: 2026-10-05. Status: draft for review.

## Goal

Make the **RAG collection** the unit that is processed, searched and granted. A
collection is a name, one folder inside `DATA_ROOT`, and its own processing and
search settings. RAG indexes the files in that folder where they already are.

This removes the copy step: nothing is copied into `rag-data` so that it can be
searched. Jira exports, document exports, Git clones and uploaded files are each
indexed in the folder their producer already writes to.

## What the user asked for

- Each collection can be searched on its own, for example only documents.
- Admin → RAG lists every collection with:
  - its folder mapping in `DATA_ROOT`;
  - **last synced**, stored in the database;
  - **folder last changed**, the newest filesystem timestamp in the folder;
  - **Refresh** when the folder changed after the last sync.
- A collection may map to **any folder in `DATA_ROOT`**, so code projects work the
  same way as documents.
- Per-collection settings:
  - search mode;
  - embeddings on or off;
  - include and exclude patterns;
  - chunking.
- Agents are granted whole collections, and the Reader's filter picks whole
  collections. There are no subfolder grants.
- Code is searchable by Reader users as well as agents.

## Out of scope

- Reading Jira or documents directly from PostgreSQL. Both stay file-based: their
  producers already write files.
- Code-aware chunking and line-number citations. Code is indexed as plain text
  for now; line ranges are a later step.
- Change detection other than filesystem timestamps, such as content-hash
  polling or file watchers.
- Moving the database, Keycloak or Phoenix volumes.

## Data model

### `ka.rag_collections` (new)

| Column | Type | Rule |
| --- | --- | --- |
| `id` | uuid PK | |
| `project_id` | uuid → `rag_workspaces` | |
| `name` | text | `^[a-z0-9]+(-[a-z0-9]+)*$`, ≤ 60 characters, unique per project among non-deleted collections |
| `description` | text | ≤ 2000 characters; shown to agents and Reader users |
| `source_path` | text | Relative to `DATA_ROOT`. Its first segment is one of `code`, `code-index`, `rag-data`, `generated-documents`, `docs`; the remaining segments match `[A-Za-z0-9._-]+`, with no `.` or `..`. Example: `rag-data/jira`. |
| `search_mode` | text | `full_text` \| `embedding` \| `hybrid`; default `full_text` |
| `embeddings_enabled` | bool | default false |
| `include` | text[] | glob patterns; default `{**/*.md,**/*.markdown,**/*.txt}` |
| `exclude` | text[] | glob patterns; default `{}` |
| `chunk_size` / `chunk_overlap` | int | Same bounds as today: 200..12000 and 0..2000, with overlap < size. Defaults 2000 and 200. |
| `revision` | bigint | Bumped on every settings change |
| `synced_revision` | bigint null | Collection revision used by the last successful sync |
| `last_synced_at` | timestamptz null | Set when a sync job succeeds |
| `deleted_at` | timestamptz null | Soft delete, because job history references collections |
| `created_at`, `updated_at` | timestamptz | |

Several collections may point at the same folder. For example, `payments-code`
could include only `**/*.py` from a repository, while `payments-docs` takes only
its Markdown.

### Changes to existing tables

- **`rag_files`:**
  - add `collection_id`;
  - `path` becomes relative to the collection folder;
  - the unique key becomes `(collection_id, path)`;
  - the `kind='folder'` pseudo-rows go: folder descriptions are no longer indexed
    as chunks; the collection description is metadata.
- **`rag_chunks`:** unchanged; it still cascades from `rag_files`.
- **`rag_vectors`:** unchanged. It stays keyed by `(project, profile, input_hash)`,
  so identical text is embedded once and shared across collections.
- **`jobs`:**
  - new kind `rag_collection_sync` with a `rag_collection_id` column;
  - the immutability trigger and check constraints are extended;
  - the "one active RAG job per project" index is replaced by **one active job per
    collection**, so several collections can be queued at once and the worker
    runs them one after another;
  - old `rag_folder_index` rows stay valid, because history is immutable.
- **`rag_workspaces.settings`:** keeps only the global `max_file_bytes`. The
  embedding model stays global in `embedding_model_id`. `extensions`,
  `chunk_size`, `chunk_overlap` and `embedding_enabled` move to collections.
- **`rag_folders`:** kept read-only for old job rows; nothing new writes to it.
- **Agent grants:**
  - `agent_knowledge_folders` is replaced by `agent_collections(agent_id, collection_id)`;
  - `agents.all_folders` is renamed `all_collections`;
  - `agents.include_subfolders` is dropped.
- **`documents`:** the publish columns from `0062` are dropped (`published_path`,
  `published_at`, `publish_error`, `index_pending`). `published_version_id`
  predates `0062` and stays.

### Data migration

- **Collections:** each non-deleted top-level `rag_folders` path `X` becomes a
  collection `X` with `source_path = rag-data/X`. Today that is `demo`, `jira`,
  and the leftover test folders.
  - Its settings are copied from the current workspace settings: extensions
    become include globs, plus chunking and embeddings.
  - `last_synced_at` is the newest `indexed_at` of its files.
- **Indexed files:** existing `rag_files` rows are rewritten into their collection
  (`jira/DEMO-1.md` → collection `jira`, path `DEMO-1.md`). Search keeps working
  without reprocessing.
- **`documents` collection:** created with `source_path = generated-documents` and
  exclude `**/v[0-9]*/**`, so old version folders are not indexed. It starts
  unsynced and shows **Refresh**.
- **Agent grants:** each granted folder maps to the collection of its top-level
  segment. `all_folders` maps to `all_collections`.
- **The down migration** restores folders, grants and the publish columns from
  the same data.

## Files and mounts

- **Container paths stay as they are today.** Each data folder keeps its existing
  container path and setting:

  | Area | Container path | Setting |
  | --- | --- | --- |
  | `code` | `/work/code` | `CODE_ROOT` |
  | `code-index` | `/work/code-index` | `CODE_INDEX_ROOT` |
  | `rag-data` | `/work/rag-data` | `RAG_SOURCE_ROOT` |
  | `generated-documents` | `/work/generated-documents` | `DOCUMENT_OUTPUT_ROOT` |
  | `docs` | `/work/docs` | `BOOTSTRAP_DOCS_ROOT` |

  admin-api's Storage page already maps the same areas.
- **`rag` and `rag-worker` mount all five.** `rag-data` stays read-write so uploads
  and edits still work; the other four are read-only. Both services also receive
  the host paths (`CODE_PATH` etc.) so the UI can show where each collection lives.
- **Collection folders:** a collection's folder is the area root of its first
  `source_path` segment, plus the remaining segments.
- Paths are confined as they are today: the fd-based `O_NOFOLLOW` walk starts at
  the collection folder.
  - Symlinks and special files are **skipped and counted**; they no longer fail
    the whole scan.
  - These are always skipped: `.git`, `node_modules`, `.env*`, binary files (not
    UTF-8), and files larger than `max_file_bytes`.
- Limits per sync: 20,000 files and 200 MB. Exceeding them fails the job with
  the counts, so the user can narrow the include patterns.

## Processing (sync job)

A sync processes one collection:

1. Walk the folder and keep files that match the include patterns and no exclude
   pattern.
2. Parse each file:
   - Markdown with the existing section parser and chunker;
   - JSON pretty-printed, as today;
   - every other included file as plain text, split by the same chunker.
3. Skip unchanged files. The skip check compares the content hash and a processing
   fingerprint. The fingerprint now also covers the collection's include and
   exclude patterns, chunking and embedding profile.
4. Create embeddings only when the collection has `embeddings_enabled` and a
   global model is configured. Vectors are reused by `input_hash`.
5. Remove indexed files that are no longer present or no longer match the
   patterns.
6. Publish atomically, as today. The concurrency check uses the **collection's**
   revision, so editing another collection no longer fails this job. On success,
   set `last_synced_at` and `synced_revision`.

## Freshness and Refresh

For each collection, `GET collections` returns:

- `folder_changed_at`: the newest mtime among matching files **and** the
  directories walked, so deletions also count;
- `file_count`;
- `indexed_files`, `chunks` and `embedded_chunks`;
- `last_synced_at`, plus the latest job status.

The walk is capped at the same 20,000 entries; when the cap is hit, the response
is marked `truncated`.

A collection **needs refresh** when any of these is true:

- it was never synced;
- `folder_changed_at > last_synced_at`;
- `revision != synced_revision`, meaning its settings changed;
- the folder is missing, which is shown as an error instead of Refresh.

**Refresh** queues a sync; there is also **Refresh all stale**. Nothing syncs
automatically in this step.

## Search

The search input changes from `folders`/`folder` to `collections: [name]`, where
an empty list means all collections the caller may use.

Per collection, the rankers that apply are:

- full-text, for `full_text` and `hybrid`;
- vector, for `embedding` and `hybrid`, but only when the collection has
  embeddings enabled and a model is configured. Otherwise it falls back to
  full-text, and the response lists the collections that fell back.

Results from all selected collections are fused with RRF (k=60), as today. Each
hit carries:

- `collection`;
- `path`, relative to the collection;
- `text`, `score`, `citation`.

The Admin search page may override the mode for testing.

## Consumers

- **Agents:**
  - config `documents.collections` is a list of `{id, name, description}`, built
    from `all_collections` and the grants;
  - `search_documents` passes collection names;
  - `read_document(collection, path)` reads through the RAG file API, confined to
    granted collections;
  - non-grounded document agents get the collection folders as read roots.
- **Older agent snapshots:** frozen agent versions and sessions still hold
  `documents.folders` with dotted selectors. A compatibility shim maps each one
  to the collection of its top-level segment when the config is loaded, so
  published versions keep working until they are saved again.
- **Reader:**
  - the docs-wiki `/context` lists collections;
  - the chat panel's Advanced section picks collections (all, or a selection);
  - `reader_api` narrows by collection names and never broadens;
  - the Reader's document list and TOC are grouped by collection.
- **Jira export:** "Export to RAG" chooses a target collection, default `jira`.
  - The collection must live under `rag-data`.
  - Each ticket is written to `<collection folder>/<KEY>.md`, keeping the existing
    first-line ownership check.
  - The collection then shows Refresh.
- **Documents:**
  - Every saved version, whether from the editor or a workflow, writes the
    current files to `generated-documents/<slug>/` (`README.md`, diagrams,
    `metadata.json`), replacing the previous files.
  - Code-phase documents use `services/<service>/<phase>/` in the same way.
  - Version folders `v<n>/` are no longer written; the database keeps history.
  - The `documents` collection indexes this folder.
- **Publish removed:** the "Publish to knowledge base" button, API, auto-republish,
  retry loop and `rag_files.py` folder helpers are removed. The Jira export keeps
  only the file-write helper.
- **Legacy code removed:** the SQLite `/collections` API in `services/rag`
  (`config.Collection`, `store/`, `retrieval.py`, `mcp_facade.py`) and the
  docs-wiki `RagClient` for it. These are only used when no RAG backoffice key is
  configured. The name would clash, and nothing in the deployed stack uses them.
  The plan verifies this before deleting.

## Admin UI

The **RAG → Collections** page replaces "Files & folders". It has:

- **A table:**
  - name and description;
  - folder (`DATA_ROOT/...` host path);
  - files on disk and indexed files;
  - embedded chunks, for example `0 / 975`;
  - folder last changed and last synced;
  - status (`Up to date`, `Needs refresh`, `Syncing`, `Failed`, `Folder missing`);
  - a **Refresh** button.
- **New and edit collection:**
  - name, description and folder;
  - the folder is picked from the five data folders and then their subfolders,
    with free text for deeper paths;
  - search mode, embeddings, include and exclude, chunking.
- **Collection detail:**
  - the indexed files, with chunk count and embedding state per file;
  - a file's chunks;
  - a read-only file browser, plus upload and edit only when the folder is under
    `rag-data`.
- **Delete collection:** removes the index rows and grants and keeps the files on
  disk. Deleting files is no longer a RAG action.

The Processing jobs, Search and Settings pages stay. Settings keeps only the
global embedding model and `max_file_bytes`.

Agent resources and the wizard pick collections. The Jira dialog picks a target
collection.

## Errors

Each problem is shown to the user, and none of them break other collections:

| Problem | Behaviour |
| --- | --- |
| Folder missing | Status `Folder missing`; sync refused with a clear message |
| Too many files | Sync fails with the counts |
| Unreadable file | Skipped and counted in the job result |
| Embedding failure | Job fails; the previous index stays |
| Settings edited during a sync | That sync fails with "Settings changed; refresh again" |
| Another collection edited during a sync | Does not affect the running sync |

## Testing

- **Migration:**
  - folders, files and grants map to collections;
  - the `documents` collection exists;
  - the publish columns are dropped;
  - the down migration restores the old state;
  - pinned in `db/tests/run.sh`.
- **rag:**
  - include/exclude matching;
  - skipped symlinks and binaries;
  - plain-text code files;
  - per-collection revision isolation;
  - freshness fields and needs-refresh rules;
  - per-collection search modes and fallback;
  - one active job per collection;
  - limits.
- **admin-api:**
  - grants and `all_collections`;
  - the old-snapshot shim;
  - document tools confined to collections;
  - the Reader narrowing by collection;
  - Jira export to a collection;
  - documents exported to `<slug>/` on every save;
  - publish removed.
- **docs-wiki:** context and TOC by collection; Reader chat narrowing.
- **UI:**
  - the Collections table, status and Refresh;
  - the collection form;
  - collection pickers in agent settings, the wizard, the chat panel and the Jira
    dialog.
- **Deploy:** rag and rag-worker mount all five areas at their existing container
  paths, with `rag-data` the only writable one, and receive the host paths.

## Delivery order

1. Schema, migration and the rag service (collections API, sync, freshness,
   search), with the admin proxy and the Collections page.
2. Agents and Reader move to collections (grants, tools, shim, docs-wiki, chat
   panel).
3. Jira export targets a collection; documents export current-only to
   `<slug>/`; publish is removed.
4. Legacy SQLite collections code is removed; docs are updated.

Steps 1 and 2 merge together, because search stops accepting folder selectors in
step 1. Steps 3 and 4 can each merge separately.
