# RAG collections

A **RAG collection** is the unit that is processed, searched and granted to agents. It is a
name, one folder inside `DATA_ROOT`, and its own processing and search settings. RAG indexes
the files in that folder where they already are; nothing is copied into `rag-data` so that it
can be searched.

Open the standalone admin app and choose **RAG** in the sidebar:

- **Collections**: list collections with status, folder, file and embedding counts; create, edit and delete them; **Refresh** one or all stale collections; open a collection to see indexed files and chunks; upload and edit files when the folder is under `rag-data`.
- **Processing jobs**: inspect the latest 100 runs, live counts, errors, cancellation, and reruns.
- **Settings**: the global maximum file size, the global embedding model (test it here) and the read-only area roots.
- **Search**: exercise retrieval across selected collections, with a search-mode override. Use Agents for cited answers.

## Collections

A collection has:

| Field | Meaning |
| --- | --- |
| Name | Lowercase letters, digits and single hyphens (`payments-code`), at most 60 characters, unique among collections |
| Description | Up to 2,000 characters; shown to agents and Reader users |
| Folder | `source_path`, relative to `DATA_ROOT`. The first segment is one of the five data areas (`code`, `code-index`, `rag-data`, `generated-documents`, `docs`); deeper segments use letters, digits, `.`, `_` and `-` |
| Search mode | `full_text` (default), `embedding` or `hybrid` |
| Embeddings | On or off per collection; needs the global embedding model |
| Include / exclude | Glob patterns. Default include: `**/*.md`, `**/*.markdown`, `**/*.txt`; default exclude: none |
| Chunk size / overlap | 2000 / 200 by default (200-12000 and 0-2000, overlap smaller than size) |

Several collections may point at the same folder with different patterns, for example
`payments-code` (include `**/*.py` from a repository under `code/`) and `payments-docs`
(only its Markdown).

Examples:

| Collection | Folder | Notes |
| --- | --- | --- |
| `jira` | `rag-data/jira` | One Markdown file per ticket |
| `documents` | `generated-documents` | Created automatically by migration `0063`; excludes `**/v[0-9]*/**` so old `v<n>/` version folders are not indexed. It starts unsynced and shows **Refresh** |
| `payments-code` | `code/payments-service` | Include `**/*.py`; code is indexed as plain text, with no code-aware chunking or line numbers |

Migration `0063` converted every top-level folder of `rag-data` into a collection (folder
`X` became collection `X` with folder `rag-data/X`) and rewrote indexed files and agent
grants, so search keeps working without reprocessing. Deleting a collection removes its
index rows and agent grants and keeps the files on disk.

## Data areas and mounts

All five areas are mounted into `rag` and `rag-worker` at their existing container paths.
`rag-data` is read-write, so uploads and edits still work; the other four are read-only.

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

Uploads and file edits are accepted only for collections under `rag-data`. A collection's
folder is walked with a descriptor-based, no-follow traversal that starts at the collection
folder.

## What is indexed

A sync keeps files that match the include patterns and no exclude pattern. Markdown uses the
section parser and chunker, JSON is pretty-printed, and every other included text file is
split as plain text.

Skipped and counted in the job result (the sync does not fail):

- symbolic links and special files;
- unreadable files and file names that are not valid;
- binary (not UTF-8) files and files larger than the global maximum file size;
- always: `.git`, `node_modules` and `.env*`.

Limits per sync are 20,000 files and 200 MB. Exceeding them fails the job with the counts so
you can narrow the include patterns.

## Status, freshness and Refresh

Each collection reports its file count, indexed files, chunks, embedded chunks (for example
`0 / 975`), when it was last synced, and when the folder last changed (the newest mtime
among matching files and the directories walked, so deletions count). The walk stops at
20,000 entries and marks the result `truncated`.

Status meanings:

| Status | Meaning |
| --- | --- |
| Up to date | Synced, folder unchanged since, settings unchanged |
| Needs refresh | Never synced, or the folder changed after the last sync, or the collection's settings changed since the last sync |
| Syncing | A sync job is queued or running |
| Failed | The latest sync job failed; the previous index stays |
| Folder missing | The folder does not exist; shown as an error instead of Refresh, and sync is refused |

A collection **needs refresh** when it was never synced, `folder_changed_at > last_synced_at`,
or its settings revision differs from the revision used by the last successful sync.
**Refresh** queues a sync for one collection; **Refresh all stale** queues every collection
that needs it. Nothing syncs automatically.

## Search modes and fallback

Search input is `collections: [name]`; an empty list means every collection the caller may
use. Per collection, full-text ranking applies to `full_text` and `hybrid`, and vector ranking
applies to `embedding` and `hybrid` only when that collection has embeddings enabled and a
global embedding model is configured. Otherwise the collection falls back to full text and
the response lists the collections that fell back, which the Search page shows as "Keyword search used for: ...". Results from all selected collections are
fused with reciprocal-rank fusion (k=60). Each hit carries `collection`, `path` (relative to
the collection), `text`, `score` and `citation`. The score is reciprocal rank, not a
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

Collections are stored in `ka.rag_collections` (name, description, `source_path`, search mode, embeddings flag, include/exclude, chunking, `revision`, `synced_revision`, `last_synced_at`, soft `deleted_at`). Supporting tables:

- `rag_workspaces`: global `max_file_bytes` and `embedding_model_id` referencing `llm_models`.
- `llm_models`: named chat/embedding connections, encrypted credentials, dimensions, and embedding versions.
- `rag_files`: per-collection files (`collection_id`, collection-relative `path`), hashes and fingerprints, job provenance.
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
| GET / POST | `/collections` | List collections with freshness and counts / create one |
| GET / PUT | `/collections/{id}` | Read / update a collection (with its `revision`) |
| DELETE | `/collections/{id}?revision=...&confirmed=true` | Delete index rows and grants; files stay on disk |
| POST | `/collections/{id}/sync` | Queue a sync (optionally forced) |
| POST | `/collections/sync-stale` | Queue a sync for every collection that needs refresh |
| GET | `/collections/{id}/files` | Indexed files with chunk and embedding counts |
| GET | `/collections/{id}/chunks?path=...` | Chunks of one file |
| GET / PUT / DELETE | `/file` | Read, save or delete a source file, addressed by collection and path (rag-data collections only for writes) |
| GET | `/jobs` | Last 100 processing jobs |
| POST | `/jobs/{id}/cancel` | Cancel queued/running work |
| POST | `/search` | Retrieve evidence across collections |

The internal service exposes administration under `/backoffice/projects/{project_id}` and generic `/search` at its root. All service routes require `X-Rag-Key`. Generic service requests additionally require `project_id`; admin clients omit it because the gateway supplies the fixed workspace identity.

Keys are AES-256-GCM encrypted with the existing `CREDENTIAL_ENCRYPTION_KEY`, a fresh 12-byte nonce, and project/purpose-bound associated data. The stored byte value contains nonce followed by ciphertext and authentication tag. Keys are never returned. Omitted key fields retain existing keys; explicit null removes one. Changing an endpoint with a stored key requires re-entering or removing it. Back up the encryption key with PostgreSQL and the source directory.

The search agent in both applications retrieves from this PostgreSQL workspace. It receives the catalog of its granted collections on every turn, then uses `search_documents` (collection names) and `read_document(collection, path)` to ground its answer. Agents are granted whole collections ("All collections" or a picked list); there are no subfolder grants. Frozen agent versions that still hold old folder selectors are mapped to the collection of their top-level segment when loaded. The Reader's Advanced panel narrows a chat to chosen collections and never broadens beyond what the agent allows. There is no separate `/rag` answer endpoint. Test the agent under Administration → Agents.
