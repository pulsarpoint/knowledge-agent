# RAG collections

A **RAG collection** is a named search index with its own settings and one or more paths
(folders in `DATA_ROOT`). It is the unit that is processed, searched and granted to agents.
RAG indexes the files in those folders where they already are; nothing is copied. Sources
(Docs → Documents, Jira, code projects) add and remove their own paths; see
[Paths and sources](#paths-and-sources).

Open the standalone admin app and choose **RAG** in the sidebar:

- **Collections**: shows each collection's paths and sources, files, embedded chunks, last synced and status. **Edit** changes search settings only. **Refresh** one or all stale collections. **Remove** is offered only when no source owns a path, and **Detach** removes an unowned path. Open a collection to see its indexed files (with full paths) and chunks. There is no New collection and no file upload or edit.
- **Processing jobs**: a paged, searchable table of every sync run with status and collection filters, live counts, errors, cancellation, and reruns.
- **Settings**: the global maximum file size, the global embedding model (test it here) and the read-only area roots.
- **Search**: exercise retrieval across selected collections, with a search-mode override. Use Agents for cited answers.

## Collections

A collection has:

| Field | Meaning |
| --- | --- |
| Name | Lowercase letters, digits and single hyphens (`payments-code`), at most 60 characters, unique among collections |
| Description | Up to 2,000 characters; shown to agents and Reader users |
| Paths | One or more `source_path` values, relative to `DATA_ROOT`. The first segment is one of the five data areas (`code`, `code-index`, `rag-data`, `generated-documents`, `docs`); deeper segments use letters, digits, `.`, `_` and `-`. Added by sources or through the source API, not edited here |
| Search mode | `full_text` (default), `embedding` or `hybrid` |
| Embeddings | On or off per collection; needs the global embedding model |
| Include / exclude | Glob patterns. Default include: `**/*.md`, `**/*.markdown`, `**/*.txt`; default exclude: none |
| Chunk size / overlap | 2000 / 200 by default (200-12000 and 0-2000, overlap smaller than size) |

The name cannot be changed after creation.

Path rules:

- within one collection, paths may not overlap (one may not contain another), so a file
  never appears twice;
- different collections may share a path;
- include and exclude patterns are matched relative to each path.

Examples:

| Collection | Paths (source) | Notes |
| --- | --- | --- |
| `jira` | `rag-data/jira` (Jira) | One Markdown file per ticket |
| `payments` | `code/payments` (Code project), `rag-data/jira` (Jira), `docs/runbooks` (unowned) | One collection searching code, tickets and runbooks |
| `documents` | `generated-documents` (Documents) | Created automatically by migration `0063`; excludes `*/v[0-9]*/**` and `services/*/*/v[0-9]*/**` so old `<slug>/v<n>/` and `services/<svc>/<phase>/v<n>/` version folders are not indexed (a slug that itself starts with `v<digit>`, such as `v2-api-design`, is). It starts unsynced and shows **Refresh** |

Migration `0063` converted every top-level folder of `rag-data` into a collection (folder
`X` became collection `X` with folder `rag-data/X`) and rewrote indexed files and agent
grants, so search keeps working until each collection is refreshed. Deleting a collection
removes its index rows and agent grants and keeps the files on disk.

Folders are never created by RAG except `rag-data/jira`, which the Jira export creates when
it writes the first ticket. A missing path shows **Folder missing**.

## Paths and sources

Sources own their files and decide which collection they go into:

| Source | Path it adds | Collection |
| --- | --- | --- |
| Docs → Documents | `generated-documents` | `documents` |
| Jira | `rag-data/jira` | Chosen in the Jira export dialog, default `jira`; created if missing |
| Code project | each mapped folder, `code/<path>` | Chosen on the code project page when **Searchable** is on, default the project's slug; created if missing |

- An owner has one path per collection; adding it again moves (re-points) it.
- Removing a path removes its indexed files from the index. Files on disk stay.
- **Remove** collection is refused with `Remove this collection's sources first: …` while a
  source owns a path.
- Unowned paths can be detached from the collection detail page.

### Upgrading to 0065

- Each collection's folder becomes its one path.
- `generated-documents` is owned by Documents, and the oldest live `rag-data/jira`
  collection is owned by Jira; every other path is unowned.
- Indexed files are rewritten to full paths with no reprocessing, so collections stay
  **Up to date**.
- Rolling back refuses while any collection has more than one path; remove the extra paths
  first.

### Upgrading to collections

After upgrading:

1. On startup admin-api re-exports every document whose current export (`<slug>/README.md`
   or `services/<svc>/<phase>/<phase>.md`) is missing into `generated-documents`. This runs in
   the background, is idempotent and only logs failures.
2. Every collection shows **Needs refresh**: migrated collections keep their last sync time
   but their files carry the old processing fingerprint, and `documents` was never synced.
3. **Refresh** each collection (or **Refresh all stale**). Expect a full re-parse and, with
   embeddings on, a full re-embed (for example about 975 chunks for `jira`).

## Data areas and mounts

All five areas are mounted into `rag` and `rag-worker` at their existing container paths.
`rag-data` is read-write only for Jira exports (RAG writes them through its internal file endpoint); the other four are read-only.

| Area | Container path | Setting |
| --- | --- | --- |
| `code` | `/work/code` | `CODE_ROOT` |
| `code-index` | `/work/code-index` | `CODE_INDEX_ROOT` |
| `rag-data` | `/work/rag-data` | `RAG_SOURCE_ROOT` |
| `generated-documents` | `/work/generated-documents` | `DOCUMENT_OUTPUT_ROOT` |
| `docs` | `/work/docs` | `BOOTSTRAP_DOCS_ROOT` |

The services also receive the host paths (`CODE_PATH`, `RG_DATA`, ...) so the UI can show where a
collection lives. Host paths are metadata and are never resolved inside the container.
Change `DATA_ROOT` (or an override) and recreate the RAG API and worker to use another host folder.

Only the Jira export writes into `rag-data` (through the internal `PUT /file`). Each path of
a collection is walked with a descriptor-based, no-follow traversal that starts at that path.

## What is indexed

A sync keeps files that match the include patterns and no exclude pattern. Markdown uses the
section parser and chunker, JSON is pretty-printed, and every other included text file is
split as plain text.

Skipped and counted in the job result (the sync does not fail):

- symbolic links and special files;
- unreadable files and file names that are not valid;
- binary (not UTF-8) files and files larger than the global maximum file size;
- always: hidden files and folders (any path segment starting with `.`, which covers
  `.git`, `.env*` and scratch folders such as `.documentation-update-*`) and `node_modules`.

Limits per sync are 20,000 matching files, 200,000 visited entries (files and folders) and
200 MB. Exceeding them fails the job so you can narrow the include patterns or the folder.

The file API (`GET /file?collection=&path=`, used by the Reader's document view and the agents'
`read_document`) takes the full path under `DATA_ROOT` (for example `rag-data/jira/DEMO-1.md`).
It serves only files a sync would index, under one of the collection's paths: a hidden,
`.env*`, `.git` or `node_modules` path or a file outside the paths or the include/exclude
patterns answers 404 "Document not found", exactly like a missing file.

## Status, freshness and Refresh

Each collection reports its file count, indexed files, chunks, embedded chunks (for example
`0 / 975`), when it was last synced, and when its folders last changed (`folder_changed_at`, the newest
change across all paths: the newest mtime among matching files and the directories walked,
so deletions count). Each path shows its own presence. The walk stops after
visiting 20,000 entries (files and folders, matching or not) and marks the result `truncated`.

`GET /collections?status=false` skips the walk: it returns the collection rows, the index
counts (`indexed_files`, `chunks`, `embedded_chunks`) and `folder_present` only. The Reader's
context panel and Jira export use it.

Status meanings:

| Status | Meaning |
| --- | --- |
| Up to date | Synced, folder unchanged since, settings unchanged |
| Needs refresh | Never synced, or a folder changed after the last sync, or the collection's settings changed or a path was added or removed since the last sync (a collection migrated by `0063` has no synced revision, so it needs one refresh) |
| Syncing | A sync job is queued or running |
| Failed | The latest sync job failed; the previous index stays |
| Folder missing | Any path does not exist; shown as an error naming the path instead of Refresh |

A collection **needs refresh** when it was never synced, `folder_changed_at > last_synced_at`,
or its settings revision differs from the revision used by the last successful sync.
Refresh is refused while a path is missing (`Path <p> is missing…`), and the existing index
is kept, so an unmounted folder never empties it. **Refresh** queues a sync for one collection; **Refresh all stale** queues every collection
that needs it. Nothing syncs automatically.

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

The worker hashes all selected files using SHA-256 before expensive processing. A successful file with the same content hash and the same processing fingerprint is skipped entirely: no parsing, chunking, full-text writes, or embedding call. The fingerprint covers the parser, the collection's include/exclude patterns, chunking and the embedding profile. A model/profile change reuses saved text chunks; a pattern or chunk setting change reparses sources. Identical chunk inputs reuse vectors across collections for the same embedding profile. Credential rotation does not invalidate text or embeddings. Increment the model version when changing the model behind an existing name.

Each sync is a durable PostgreSQL job (`rag_collection_sync`) with a renewable worker lease. One job may be active per collection, so several collections can be queued and the worker runs them one after another. Results are prepared before publication; a transaction rechecks the lease, the collection's revision and a second disk manifest, then publishes changed files, removes files that are gone or no longer match, and marks the job successful, setting `last_synced_at`. Editing another collection does not affect a running sync; editing this collection's settings fails it with "Settings changed; refresh again". Embedding errors, malformed vectors, cancellation, worker expiry or detected concurrent edits leave the previous index intact. Retry creates a new job. Force rebuild reparses and regenerates embeddings.

Direct bind-mount writers should publish completed files using an atomic rename and avoid edits during a sync. The two-manifest check detects changes during the run; it cannot lock an unrelated host process writing after the final check.

## Database

Collections are stored in `ka.rag_collections` (name, description, search mode, embeddings flag, include/exclude, chunking, `revision`, `synced_revision`, `last_synced_at`, soft `deleted_at`). Supporting tables:

- `rag_collection_paths`: a collection's paths (`collection_id`, `source_path`, optional owner `owner_kind` / `owner_id` / `owner_key`).
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
| GET / PUT | `/settings` | Read/save global settings (`max_file_bytes`, `embedding_model_id`) with `revision` |
| POST | `/test/embedding` | Test the saved model endpoint (makes a small model request) |
| GET | `/collections` | List collections with freshness, counts and `paths` |
| GET / PUT | `/collections/{id}` | Read / update search settings only (with its `revision`); the name is not editable |
| DELETE | `/collections/{id}?revision=...&confirmed=true` | Delete index rows and grants; files stay on disk. 409 while a source owns a path |
| DELETE | `/collections/by-name/{name}/paths?source_path=` | Detach a path and its indexed files |
| POST | `/collections/{id}/sync` | Queue a sync (optionally forced) |
| POST | `/collections/sync-stale` | Queue a sync for every collection that needs refresh |
| GET | `/collections/{id}/files` | Indexed files with chunk and embedding counts |
| GET | `/collections/{id}/chunks?path=...` | Chunks of one file |
| GET | `/file?collection=&path=` | Read an indexable file by its full path under one of the collection's paths |
| GET | `/jobs` | Sync jobs, newest first: `{jobs, total}`; query `status`, `collection`, `q` (collection or error text), `limit` (1–100, default 25), `offset` |
| POST | `/jobs/{id}/cancel` | Cancel queued/running work |
| POST | `/search` | Retrieve evidence across collections |

**Source API (service only, `X-Rag-Key`)**, used by admin-api for Docs, Jira and code projects:

| Call | Purpose |
| --- | --- |
| `PUT collections/by-name/{name}` | Create the collection if missing (default settings) and return it; idempotent, never changes an existing collection's settings |
| `PUT collections/by-name/{name}/paths` with `{source_path, owner_kind?, owner_id?, owner_key?}` | Add a path, creating the collection if missing; the same owner's path is replaced; 409 `Path overlaps <other> in collection <name>` |
| `DELETE collections/by-name/{name}/paths?source_path=` or `?owner_kind=&owner_id=&owner_key=` | Remove a path and its indexed files; idempotent |
| `DELETE paths?owner_kind=&owner_id=&owner_key=` | Remove an owner's path from every collection |
| `GET file?collection=&path=<full path>` | Read an indexable file |
| `PUT file {path, content, expected_hash}` | Write a file under `rag-data` only (Jira export) |

The internal service exposes administration under `/backoffice/projects/{project_id}` and generic `/search` at its root. All service routes require `X-Rag-Key`. Generic service requests additionally require `project_id`; admin clients omit it because the gateway supplies the fixed workspace identity.

Keys are AES-256-GCM encrypted with the existing `CREDENTIAL_ENCRYPTION_KEY`, a fresh 12-byte nonce, and project/purpose-bound associated data. The stored byte value contains nonce followed by ciphertext and authentication tag. Keys are never returned. Omitted key fields retain existing keys; explicit null removes one. Changing an endpoint with a stored key requires re-entering or removing it. Back up the encryption key with PostgreSQL and the source directory.

The search agent in both applications retrieves from this PostgreSQL workspace. It receives the catalog of its granted collections on every turn, then uses `search_documents` (collection names) and `read_document(collection, path)` to ground its answer. `path` is the full path, such as `rag-data/jira/DEMO-1.md`; the Reader document path is `<collection>/<full path>`. Agents that are not grounded read every path of their granted collections. Agents are granted whole collections ("All collections" or a picked list); there are no subfolder grants. Frozen agent versions that still hold old folder selectors are mapped to the collection of their top-level segment when loaded. The Reader's Advanced panel narrows a chat to chosen collections and never broadens beyond what the agent allows. There is no separate `/rag` answer endpoint. Test the agent under Administration → Agents.
