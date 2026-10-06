# RAG collections

A **RAG collection** is a named search index with its own settings and one or more folders
under `DATA_ROOT`. It is the unit that is processed, searched and granted to agents. RAG
indexes the files in those folders where they already are; nothing is copied.

RAG owns the configuration: administrators (or external tools, through the API) create
collections and connect folders. Sources only write files: Docs → Documents writes
`generated-documents/…`, the Jira export writes `jira/<KEY>.md` (`JIRA_FOLDER`), and Git pulls
update `code/…`. See [Folders](#folders).

Open the standalone admin app and choose **RAG** in the sidebar:

- **Collections**: shows each collection's folders, files, embedded chunks, last synced, status and
  **Sync** ("Auto · every N min" or "Manual"). **New collection** creates one; **Refresh** one
  collection or **Refresh all stale**. Open a collection to see its settings, folders, status
  counts and **file log**. **Edit** changes the settings, including Auto-sync and Check every.
  **Add folder** takes a folder from the folder browser or a typed path, and **Remove** (per
  folder) disconnects it. **Refresh**, **Rebuild** and **Remove** (the collection; always
  available, with confirmation) are on the same page. There is no file upload or edit.
- **Processing jobs**: a paged, searchable table of every sync run with status and collection filters, mode (Auto, Incremental or Force rebuild), live counts, errors, cancellation, and reruns.
- **Settings**: the global maximum file size, the global embedding model (test it here) and the read-only area roots.
- **Search**: exercise retrieval across selected collections, with a search-mode override. Use Agents for cited answers.

## Collections

A collection has:

| Field | Meaning |
| --- | --- |
| Name | Lowercase letters, digits and single hyphens (`payments-code`), at most 60 characters, unique among collections |
| Description | Up to 2,000 characters; shown to agents and Reader users |
| Folders | One or more `source_path` values, relative to `DATA_ROOT`. A folder is any path relative to `DATA_ROOT` whose segments use letters, digits, `.`, `_` and `-` and do not start with `.`. Added and removed on the collection detail page or through the API, not in the Edit form |
| Search mode | `full_text` (default), `embedding` or `hybrid` |
| Embeddings | On or off per collection; needs the global embedding model |
| Include / exclude | Glob patterns. Default include: `**/*.md`, `**/*.markdown`, `**/*.txt`; default exclude: none |
| Chunk size / overlap | 2000 / 200 by default (200-12000 and 0-2000, overlap smaller than size) |
| Auto-sync | Off by default; when on, the worker refreshes the collection when its folders change (see [Automatic sync](#automatic-sync)) |
| Check every | 1-1440 minutes, default 5; used only when Auto-sync is on |

The name cannot be changed after creation.

Examples:

| Collection | Folders | Notes |
| --- | --- | --- |
| `documents` | `generated-documents` | The Docs → Documents exports; add `*/v[0-9]*/**` and `services/*/*/v[0-9]*/**` to the exclude patterns so old `<slug>/v<n>/` version folders are not indexed |
| `jira` | `jira` | One Markdown file per ticket |
| `payments` | `code/payments`, `jira` and `docs/runbooks` | One collection searching code, tickets and runbooks |

Migration `0063` converted every top-level folder of `rag-data` into a collection (folder
`X` became collection `X` with folder `rag-data/X`) and rewrote indexed files and agent
grants, so search keeps working until each collection is refreshed. Deleting a collection
removes its index rows, folders, file log and agent grants and keeps the files on disk.

Add folder never creates a folder: a missing folder is refused with `Folder does not exist`. A collection whose folder later disappears shows
**Folder missing**.

## Folders

- Within one collection, folders may not overlap (one may not contain another), so a file
  never appears twice: `Folder overlaps <other> in collection <name>`.
- Different collections may share a folder.
- Include and exclude patterns are matched relative to each folder.
- Adding or removing a folder makes the collection show **Needs refresh**.
- Removing a folder drops its indexed files and file log rows from the collection. The
  files stay on disk.
- Removing a collection drops its index, folders, file log and agent grants. The files stay
  on disk.
- The folder browser lists visible subfolders only: no hidden folders, `.git`,
  `node_modules` or links.
- A typed folder any of whose segments RAG skips (hidden, `.git`, `node_modules`) is refused
  with "Folder <path> is skipped by RAG", both when adding it and when browsing it.

Where sources write:

| Source | Writes to |
| --- | --- |
| Docs → Documents | `generated-documents` |
| Jira export | `<JIRA_FOLDER>/<KEY>.md` (default `jira/<KEY>.md`) |
| Code projects | `code/<folder>` (clone and pull only) |

On a fresh install there are no collections. Create a `documents` collection and add
`generated-documents` to search generated documents, and add `jira` to a collection
to search Jira tickets. To search code, add the project's folders (`code/<folder>`) to a
collection.

### Upgrading to 0065

- Each collection's folder becomes its one path.
- `generated-documents` is owned by Documents, and the oldest live `rag-data/jira`
  collection is owned by Jira; every other path is unowned.
- Indexed files are rewritten to full paths with no reprocessing, so collections stay
  **Up to date**.
- Rolling back refuses while any collection has more than one folder; remove the extra folders
  first.
- Jira exports made before `0065` into another `rag-data` collection (for example
  `rag-data/tickets`) stay there as an unowned path, while new exports go to `rag-data/jira`.
  Both are searched, so tickets can appear twice: remove the old folder on the collection
  detail page, or remove that collection.

### Upgrading to 0066

- Folder owners and the code-project **Searchable** settings are removed. Every existing
  connection stays as an ordinary folder: `documents` keeps `generated-documents`, `jira`
  keeps `rag-data/jira`, and code folders stay in their collections.
- The Jira export no longer asks for a collection.
- Each indexed file starts in the [file log](#file-log) as indexed. Every collection with
  indexed files shows **Needs refresh** until it is refreshed once (**Refresh all stale**);
  the first sync reads and hashes each file but does not re-embed unchanged content.
- Rolling back re-adds the owner and search columns empty.

### Upgrading to 0067

- Collection paths `rag-data/jira` and `rag-data/jira/...` become `jira` and `jira/...`,
  and the indexed files and file log rows under them are rewritten without reprocessing.
  `make setup` moves the files from `DATA_ROOT/rag-data/jira` to `JIRA_FOLDER` once; the
  migration assumes the default `JIRA_FOLDER` (with another value the collection shows
  **Folder missing** until its folder is replaced). Other `rag-data/...` folders are not
  configured folders: move them out of `DATA_ROOT`; a collection that used them shows
  **Folder missing**.
- The `source_path` check accepts any top-level folder instead of the five fixed areas.
- Saved agent versions holding `/work/rag-data/jira`, `/work/code`, `/work/code-index`,
  `/work/generated-documents` or `/work/docs` paths are rewritten to `/work/data/...`.
- Rolling back moves the Jira paths and agent paths back and restores the five-area check
  as `NOT VALID`: folders already added elsewhere stay, and only new ones are refused.

### Upgrading to collections

After upgrading:

1. On startup admin-api re-exports every document whose current export (`<slug>/README.md`
   or `services/<svc>/<phase>/<phase>.md`) is missing into `generated-documents`. This runs in
   the background, is idempotent and only logs failures.
2. Every collection shows **Needs refresh**: migrated collections keep their last sync time
   but their files carry the old processing fingerprint, and `documents` was never synced.
3. **Refresh** each collection (or **Refresh all stale**). Expect a full re-parse and, with
   embeddings on, a full re-embed (for example about 975 chunks for `jira`).

## DATA_ROOT and mounts

`DATA_ROOT` is mounted once, read-only, at `/work/data` in `rag` and `rag-worker`. The only
writable folder for RAG is `JIRA_FOLDER` (default `jira`), mounted over the base at
`/work/data/<JIRA_FOLDER>` and exposed to the service as `JIRA_EXPORT_ROOT`; `rag-worker`
writes nothing.

| Variable | Meaning |
| --- | --- |
| `DATA_ROOT_DIR` | Container path of the data folder (`/work/data`) |
| `DATA_ROOT` | Host path of the data folder; display metadata, never resolved inside the container |
| `JIRA_EXPORT_ROOT` | Container path of the writable Jira folder (`/work/data/<JIRA_FOLDER>`) |

Host paths shown in the UI are `DATA_ROOT/<folder>`. The folder browser starts at the top
level of `DATA_ROOT` and hides hidden folders and links, so RAG can reach every folder in
`DATA_ROOT`: keep it for the configured folders and folders added for RAG, and keep
backups and logs in a sibling folder. Each folder of a collection is walked with a
descriptor-based, no-follow traversal that starts at `/work/data`. Only the Jira export
writes files (through the internal `PUT /file`).

## What is indexed

A sync keeps files that match the include patterns and no exclude pattern. Markdown uses the
section parser and chunker, JSON is pretty-printed, and every other included text file is
split as plain text.

Skipped and counted in the job result (the sync does not fail):

- symbolic links, special files, entries that cannot be read while walking and file names
  that are not valid: counted only, not listed in the file log (the collection detail page
  shows them as "Not listed: …");
- binary (not UTF-8) files, files larger than the global maximum file size and files that
  cannot be opened: listed in the [file log](#file-log) as skipped;
- files that fail to parse (logged `failed`; the other files still sync);
- always: hidden files and folders (any path segment starting with `.`, which covers
  `.git`, `.env*` and scratch folders such as `.documentation-update-*`) and `node_modules`.

Limits per sync are 20,000 matching files, 200,000 visited entries (files and folders) and
200 MB of text, counted as the sizes of the walked files (files over the per-file limit are
excluded). Exceeding them fails the job so you can narrow the include patterns or the folder.
The sync, the collection status and the automatic poller share this one rule, so such a
collection shows **Too large to sync** with Refresh disabled, **Refresh all stale** skips it,
and Auto-sync queues nothing for it.

The file API (`GET /file?collection=&path=`, used by the Reader's document view and the agents'
`read_document`) takes the full path under `DATA_ROOT` (for example `jira/DEMO-1.md`).
It serves only files a sync would index, under one of the collection's paths: a hidden,
`.env*`, `.git` or `node_modules` path or a file outside the paths or the include/exclude
patterns answers 404 "Document not found", exactly like a missing file.

## Status, freshness and Refresh

Each collection reports its file count, indexed files, chunks, embedded chunks (for example
`0 / 975`), when it was last synced, and when its folders last changed (`folder_changed_at`, the newest
change across all paths: the newest mtime among matching files and the directories walked,
so deletions count). Each path shows its own presence. The status walk uses the sync limits:
it stops after 20,000 matching files or 200,000 visited entries (files and folders, matching
or not) and marks the result `truncated`. `too_large` is true when the walk was truncated or
the walked text exceeds 200 MB; a collection that a sync can walk is never too large.

`GET /collections?status=false` skips the walk: it returns the collection rows, the index
counts (`indexed_files`, `chunks`, `embedded_chunks`) and `folder_present` only. The Reader's
context panel and Jira export use it.

Status meanings:

| Status | Meaning |
| --- | --- |
| Up to date | Synced, folders match the file log, settings unchanged |
| Needs refresh | Never synced, or the settings changed or a folder was added or removed since the last sync (revision changed), or the folders differ from the file log: files added or removed, or a size or mtime changed. This is the rule the automatic poller uses (a collection migrated by `0063` has no synced revision, so it needs one refresh) |
| Syncing | A sync job is queued or running |
| Failed | The latest sync job failed; the previous index stays |
| Folder missing | Any folder does not exist; shown as an error naming the folder instead of Refresh |
| Too large to sync | Over 20,000 matching files, over 200,000 walked entries, or over 200 MB of text (`too_large`); Refresh is disabled and Auto-sync queues nothing until the include patterns or folders are narrowed |

Refresh is refused while a folder is missing (`Path <p> is missing…`), and the existing index
is kept, so an unmounted folder never empties it. **Refresh** queues an incremental sync for
one collection; **Refresh all stale** queues every collection that needs it; **Rebuild**
re-reads and re-processes every file. With **Auto-sync** on, the worker does the same
automatically; see [Automatic sync](#automatic-sync).

## File log

Each collection keeps a per-file log (`rag_file_log`) of what the last syncs did with every
file in its folders. Statuses:

| Status | Meaning |
| --- | --- |
| Indexed | The file is in the search index |
| Skipped | Not indexed (binary, over the maximum file size, or could not be opened); removed from search. Links, special files and invalid names are counted, not listed |
| Failed | The file could not be parsed (`Could not parse this file: …`); removed from search |
| Removed | The file was deleted or no longer matches; removed from search |

How a sync uses the log:

- **Size and mtime shortcut.** A file whose size, mtime and fingerprint match its log row is
  not opened at all.
- **Content-hash shortcut.** A file that is opened and has the same SHA-256 and fingerprint is not
  parsed, chunked or embedded again.
- **Fingerprint.** The processing settings (parser, chunking), the embedding profile and the
  maximum file size. Include and exclude patterns are not part of it: they decide which files
  are in the sync, not how a file is processed.
- **Always retried.** Unreadable and removed files are retried at every sync. A file that the
  log says is indexed but that is missing from the index is re-indexed.
- **Rebuild** re-reads and re-processes everything.
- A parse failure logs the file `failed` and the sync still succeeds, with a failed count.
- Any embedding error (for example an unavailable model) fails the sync and leaves the log
  and the index unchanged.

The collection detail page shows the log as a table: a status filter (Indexed, Skipped,
Failed, Removed), a path search, paging, and the chunks of indexed files. API:
`GET /collections/{id}/log`.

## Automatic sync

Turn on **Auto-sync** in the collection's settings and choose **Check every** (1-1440
minutes, default 5; Auto-sync is off by default).

- The worker checks due collections by comparing file sizes and mtimes with the file log, so
  copies that keep old mtimes are noticed. This is the same rule as **Needs refresh**.
- A change must be quiet for 2 minutes before a sync is queued; a collection that is still
  changing is checked again about 2 minutes later.
- No second job is queued while one is queued or running.
- A collection with a missing folder, or one that is too large to sync (over 20,000 files,
  200,000 walked entries or 200 MB of text), queues nothing.
- A failed sync is retried at each interval.
- Changing only the auto-sync settings does not change the collection's revision, so it stays
  **Up to date**.
- Automatic jobs show the mode **Auto** on the Processing jobs page.

Not in this step: inotify, S3, and per-folder intervals.

## Search modes and fallback

Search input is `collections: [name]`; omitting it means every collection the caller may
use. An unknown name answers 404 unless `ignore_unknown: true` is sent: then unknown names
are dropped and returned as `unknown: [name]` (all unknown gives empty results). Agents and
the Reader send `ignore_unknown`, so a renamed or deleted granted collection does not break
their search; the Admin search page keeps the 404 to catch typos. Per collection, full-text ranking applies to `full_text` and `hybrid`, and vector ranking
applies to `embedding` and `hybrid` only when that collection has embeddings enabled and a
global embedding model is configured. Otherwise the collection falls back to full text and
the response lists the collections that fell back, which the Search page shows as "Keyword search used for: ...". Results from all selected collections are
fused with reciprocal-rank fusion (k=60). Each hit carries `collection`, `path` (the full path
under `DATA_ROOT`, such as `code/payments/src/retry.py`), `text`, `score` and `citation`. The score is reciprocal rank, not a
probability. The Admin search page may override the mode for testing.

Example body:

```json
{"query":"payment retries", "collections":["documents","jira"], "mode":"hybrid", "top":8}
```

## Embedding model

Create a connection in **LLM Settings → Add model → Model type: Embeddings**.
Choose OpenAI compatible (base URL and optional API key) or AWS Bedrock
(region and Bedrock API key), then set the model ID, vector dimensions, and version.
Native Bedrock currently supports `amazon.titan-embed-text-v2:0` with 256, 512,
or 1024 dimensions. Test it, then select it in **RAG → Settings**, then turn on
embeddings for each collection that should use vectors. Chat connections are excluded from this selector; embedding
connections are excluded from agent model selectors. Connection tests validate
both access and the returned vector dimensions.

Migration `0037_embedding_models` moves an existing configured RAG model and its
encrypted credential into `ka.llm_models`, retaining its selection and vector
profile. An unconfigured workspace remains unconfigured. Keys stay encrypted
using the same master key; no secrets pass through the browser during migration.
Stop the old RAG API/worker and admin API while applying this migration and start
the updated images together. Rollback refuses to discard saved embedding models.

## Incremental processing and publication

Docker starts the API with `rag serve-workspace`; processing runs in the separate
`rag-workspace-worker`. The admin landing page opens Collections. Startup and
`make smoke` do not enqueue processing jobs or rebuild indexes.

The worker uses the [file log](#file-log) to skip unchanged files without opening them, and hashes the rest using SHA-256 before expensive processing. A successful file with the same content hash and the same processing fingerprint is skipped entirely: no parsing, chunking, full-text writes, or embedding call. The fingerprint covers the parser, chunking, the embedding profile and the maximum file size. A model/profile change reuses saved text chunks; a chunk setting change reparses sources. One bad file no longer fails the sync: it is logged `failed` and the rest continue. Identical chunk inputs reuse vectors across collections for the same embedding profile. Credential rotation does not invalidate text or embeddings. Increment the model version when changing the model behind an existing name.

Each sync is a durable PostgreSQL job (`rag_collection_sync`) with a renewable worker lease. One job may be active per collection, so several collections can be queued and the worker runs them one after another. Results are prepared before publication; a transaction rechecks the lease, the collection's revision and the disk (sizes and mtimes are compared again, and only the files that moved are re-hashed), then publishes changed files, removes files that are gone or no longer match, and marks the job successful, setting `last_synced_at`. Editing another collection does not affect a running sync; editing this collection's settings fails it with "Settings changed; refresh again". Any embedding error, malformed vectors, cancellation, worker expiry or detected concurrent edits leave the previous index intact. Retry creates a new job. Force rebuild reparses and regenerates embeddings.

Direct bind-mount writers should publish completed files using an atomic rename and avoid edits during a sync. The two-manifest check detects changes during the run; it cannot lock an unrelated host process writing after the final check.

## Database

Collections are stored in `ka.rag_collections` (name, description, search mode, embeddings flag, include/exclude, chunking, `revision`, `synced_revision`, `last_synced_at`, `auto_sync`, `poll_interval_minutes`, `last_polled_at`, soft `deleted_at`). Supporting tables:

- `rag_collection_paths`: a collection's folders (`collection_id`, `source_path`); no owner columns.
- `rag_file_log`: per-file status for each collection (see [File log](#file-log)).
- `rag_workspaces`: global `max_file_bytes` and `embedding_model_id` referencing `llm_models`.
- `llm_models`: named chat/embedding connections, encrypted credentials, dimensions, and embedding versions.
- `rag_files`: per-collection files (`collection_id`, full `path` under `DATA_ROOT`, `path_id`), hashes and fingerprints, job provenance.
- `rag_chunks`: ordered extracted text and PostgreSQL full-text vectors.
- `rag_vectors`: model-profile and content-hash keyed pgvector embeddings, shared across collections.
- `agent_collections` and `agents.all_collections`: which collections an agent may use.
- `rag_folders`: kept read-only for old job history; nothing new writes to it.

Full-text retrieval uses PostgreSQL's `simple` configuration. Vector search uses exact cosine distance. This implementation uses exact vector scans rather than an approximate index, suitable for initial local corpora.

PostgreSQL stays on major version 17 with the existing volume. `deploy/Dockerfile.postgres` adds pgvector 0.8.6 to the same 17.10 base image. Build it before running database-dependent tests directly; `make db-test` builds it automatically.

## API

Browser/admin API prefix: `/api/rag`. All these routes require a Keycloak user in `docs-admins`. The backoffice binds every request to the existing workspace's database identity and authenticates to the internal RAG service using `RAG_BACKOFFICE_KEY`; the service key never reaches the browser.

| Method | Relative route | Purpose |
| --- | --- | --- |
| GET / PUT | `/settings` | Read/save global settings (`max_file_bytes`, `embedding_model_id`) with `revision`; `GET` also returns `data_root` (`container_root`, `host_root`, `jira_folder`) |
| POST | `/test/embedding` | Test the saved model endpoint (makes a small model request) |
| GET | `/collections` | List collections with freshness, counts and `paths` |
| POST | `/collections` | Create a collection; 409 `Collection <name> already exists` |
| GET / PUT | `/collections/{id}` | Read / update settings (with its `revision`), including `auto_sync` and `poll_interval_minutes`; saving only those does not bump the revision; the name is not editable |
| DELETE | `/collections/{id}?revision=...&confirmed=true` | Delete index rows, folders, file log and grants; files stay on disk. Always allowed |
| POST | `/collections/{id}/paths` | Add a folder with `{source_path}`; a missing folder is refused with 409 `Folder does not exist`; 409 on overlap |
| DELETE | `/collections/{id}/paths/{path_id}` | Remove a folder and its indexed files and log rows |
| GET | `/folders?path=` | List visible subfolders for the folder browser; without `path` the top-level folders of `DATA_ROOT` |
| GET | `/collections/{id}/log?status=&q=&limit=&offset=` | File log; `status` is `indexed`, `skipped`, `failed` or `removed`, `q` searches the path |
| POST | `/collections/{id}/sync` | Queue a sync (optionally forced) |
| POST | `/collections/sync-stale` | Queue a sync for every collection that needs refresh |
| GET | `/collections/{id}/files` | Indexed files with chunk and embedding counts |
| GET | `/collections/{id}/chunks?path=...` | Chunks of one file |
| GET | `/file?collection=&path=` | Read an indexable file by its full path under one of the collection's folders |
| GET | `/jobs` | Sync jobs, newest first: `{jobs, total}`; query `status`, `collection`, `q` (collection or error text), `limit` (1–100, default 25), `offset` |
| POST | `/jobs/{id}/cancel` | Cancel queued/running work |
| POST | `/search` | Retrieve evidence across collections |

External tools use the same backoffice routes with `X-Rag-Key`.

**Service-only file API (`X-Rag-Key`)**, used by the Jira export:

| Call | Purpose |
| --- | --- |
| `GET file?path=<JIRA_FOLDER>/…` | Read a file under the Jira folder by its full path, without a collection (needs `X-Rag-Key`); elsewhere 404 `Document not found` |
| `PUT file {path, content, expected_hash}` | Write a file inside `JIRA_FOLDER` only; 403 elsewhere |

The internal service exposes administration under `/backoffice/projects/{project_id}` and generic `/search` at its root. All service routes require `X-Rag-Key`. Generic service requests additionally require `project_id`; admin clients omit it because the gateway supplies the fixed workspace identity.

Keys are AES-256-GCM encrypted with the existing `CREDENTIAL_ENCRYPTION_KEY`, a fresh 12-byte nonce, and project/purpose-bound associated data. The stored byte value contains nonce followed by ciphertext and authentication tag. Keys are never returned. Omitted key fields retain existing keys; explicit null removes one. Changing an endpoint with a stored key requires re-entering or removing it. Back up the encryption key with PostgreSQL and the source directory.

The search agent in both applications retrieves from this PostgreSQL workspace. It receives the catalog of its granted collections on every turn, then uses `search_documents` (collection names) and `read_document(collection, path)` to ground its answer. `path` is the full path, such as `jira/DEMO-1.md`; the Reader document path is `<collection>/<full path>`. Agents that are not grounded read every path of their granted collections. Agents are granted whole collections ("All collections" or a picked list); there are no subfolder grants. Frozen agent versions that still hold old folder selectors are mapped to the collection of their top-level segment when loaded. The Reader's Advanced panel narrows a chat to chosen collections and never broadens beyond what the agent allows. There is no separate `/rag` answer endpoint. Test the agent under Administration → Agents.
