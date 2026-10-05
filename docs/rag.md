# Folder RAG administration

Open the standalone admin app and choose **RAG** in the sidebar:

- **Files & folders**: create collections and nested folders, edit descriptions, upload/create/edit/delete source files, and process an exact folder or subtree.
- **Processing jobs**: inspect the latest 100 runs, live counts, errors, cancellation, and reruns.
- **Settings**: configure text processing and supported extensions, enable embeddings, and select a saved embedding model from LLM Settings. Test the selected connection here.
- **Search**: exercise scoped full-text, vector, and hybrid retrieval. Use Agents for cited answers.

## Source directory

The deployment has one workspace: the host folder `DATA_ROOT/rag-data`, or an absolute `RG_DATA` override, mounted at `/work/rag-data`. `make setup DATA_ROOT=...` creates it. Compose uses the same environment value for the actual bind mount and the path displayed in **RAG Settings** and **Files & folders**. Paths cannot be changed through the backoffice API.

For example:

```dotenv
DATA_ROOT='/opt/knowledge-agent'      # RAG sources in /opt/knowledge-agent/rag-data
```

Collections live directly inside the mount, with no application or UUID directory:

```text
/work/rag-data/
  documentation/
    overview.md
    payments/
      operations.md
```

Change `DATA_ROOT` (or set `RG_DATA`) in the deployment environment and recreate the RAG API and worker containers to use a different host folder. Move existing sources with the deployment if they should remain available. Host paths are deployment metadata and are never resolved inside the container.

Upgrading from the initial UUID layout: back up PostgreSQL and the source folder, stop the RAG API and worker, and run `python3 scripts/flatten_rag_sources.py --root /absolute/host/rag-data --project-id 716d3333-723e-5a82-99d7-db34b32570a5`. The script moves collection folders into the mount root, refuses destination conflicts, and can be repeated. Apply migrations and recreate the updated services. Indexed paths and hashes are already collection-relative, so this move does not require reprocessing.

Top-level directories are logical collections. Nested directories have independent identities, descriptions, and exact search scopes. Folders use letters, numbers, underscores and hyphens; dots separate search scopes. Files must live inside a collection folder. The initial processors support UTF-8 `.md`, `.markdown`, `.txt`, and `.json`. Unsupported files remain visible and are skipped. Symbolic links, special files, traversal, and root-level files are rejected. A workspace is limited to 5,000 entries and a processing run to 100 MB; process smaller subfolders for larger source sets.

Direct host changes appear after **Refresh folders**. Processing is explicit. Deleting source files leaves the working index available until the next successful processing run. **Delete folder** opens a shadcn confirmation dialog naming the selected folder and showing descendant/file counts. Confirming recursively deletes its sources, folder descriptions, processed files, full-text chunks, and embeddings no longer used by another folder. Overlapping jobs are cancelled, and stale workers cannot republish removed data. Deleted folders disappear from the tree immediately. A minimal deleted folder identity/path remains for immutable job history; recreating the same path gets a new ID. An ordinary processing run that would remove all indexed files requires **Allow removing every indexed file**.

## Embedding model

Create a connection in **LLM Settings → Add model → Model type: Embeddings**.
Choose OpenAI compatible (base URL and optional API key) or AWS Bedrock
(region and Bedrock API key), then set the model ID, vector dimensions, and version.
Native Bedrock currently supports `amazon.titan-embed-text-v2:0` with 256, 512,
or 1024 dimensions. Test it, then select it in **RAG → Settings** and enable
embedding processing. Chat connections are excluded from this selector; embedding
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
`rag-workspace-worker`. The admin landing page opens Files & folders. Startup and
`make smoke` do not enqueue processing jobs or rebuild indexes.

The worker hashes all selected files using SHA-256 before expensive processing. A successful file with the same content hash, parser/chunker fingerprint, and embedding profile is skipped entirely: no parsing, chunking, full-text writes, or embedding call. Folder descriptions use the same mechanism. A model/profile change reuses saved text chunks; a parser/chunk setting change reparses sources. Identical chunk inputs reuse vectors within the workspace and embedding profile. Credential rotation does not invalidate text or embeddings. Increment the model version when changing the model behind an existing name.

Each run has a durable PostgreSQL job and a renewable worker lease. Only one run may be queued or running. Results are prepared before publication; a transaction rechecks the lease, workspace revision, and a second disk manifest, then publishes changed files, removes obsolete entries, and marks the job successful. Model errors, malformed vectors, invalid source content, cancellation, worker expiry, or detected concurrent edits leave the previous index intact. Retry creates a new job. Force rebuild reparses and regenerates embeddings for the selected scope.

Filesystem changes made through the API serialize with publication. Direct bind-mount writers should publish completed files using an atomic rename and avoid edits during processing. The two-manifest check detects changes during the run; it cannot lock an unrelated host process writing after the final check.

## Database

Migration `0006_rag_workspace.up.sql` enables pgvector and adds:

- `rag_workspaces`: file-processing settings, revision, and `embedding_model_id` referencing `llm_models`.
- `llm_models`: named chat/embedding connections, encrypted credentials, dimensions, and embedding versions.
- `rag_folders`: stable UUIDs, relative paths, descriptions.
- `rag_files`: last successfully processed file/description hashes, processing/profile fingerprints, job provenance.
- `rag_chunks`: ordered extracted text and PostgreSQL full-text vectors.
- `rag_vectors`: model-profile and content-hash keyed pgvector embeddings.

Collections share physical tables; folder keys scope their data. Full-text retrieval uses PostgreSQL's `simple` configuration. Vector search uses exact cosine distance. Hybrid retrieval combines ranks; without configured embeddings, it reports `full_text`. This first implementation uses exact vector scans rather than an approximate index, suitable for initial local corpora.

PostgreSQL stays on major version 17 with the existing volume. `deploy/Dockerfile.postgres` adds pgvector 0.8.6 to the same 17.10 base image. Build it before running database-dependent tests directly; `make db-test` builds it automatically.

## API

Browser/admin API prefix: `/api/rag`. All these routes require a Keycloak user in `docs-admins`. The backoffice binds every request to the existing workspace's database identity and authenticates to the internal RAG service using `RAG_BACKOFFICE_KEY`; the service key never reaches the browser.

| Method | Relative route | Purpose |
| --- | --- | --- |
| GET / PUT | `/settings` | Read/save runtime settings; save includes `revision`, a complete processing `settings` object, and nullable `embedding_model_id` |
| POST | `/test/embedding` | Test the saved model endpoint (makes a small model request) |
| GET | `/tree` | Discover folders/files and inspect last successful indexing times |
| POST | `/folders` | Create a path with `description` and workspace `revision` |
| PUT | `/folders/{id}` | Save description with existing `path` and workspace `revision` |
| DELETE | `/folders/{id}?revision=...&confirmed=true` | Permanently remove a folder/subtree and its source/indexed data |
| GET | `/file?path=...` | Read UTF-8 content and its current SHA-256 |
| PUT | `/file` | Save `{path, content, expected_hash}`; omit/null hash only for a new file |
| DELETE | `/file?path=...&expected_hash=...` | Delete a source file if its hash still matches |
| POST | `/jobs` | Queue `{folder_id, recursive, force, allow_empty}` |
| GET | `/jobs` | Last 100 processing jobs |
| POST | `/jobs/{id}/cancel` | Cancel queued/running work |
| POST | `/search` | Retrieve scoped evidence |

The internal service exposes administration under `/backoffice/projects/{project_id}` and generic `/search` at its root. All new service routes require `X-Rag-Key`. Generic service requests additionally require `project_id`; admin clients omit it because the gateway supplies the fixed workspace identity.

Example browser/backoffice search body:

```json
{"query":"payment retries", "folder":"documentation.payments.*", "mode":"full_text", "top":8}
```

`folder` accepts `*` (all collections), `documentation.payments` (exact folder), or `documentation.payments.*` (folder and descendants). `mode` is `full_text`, `embedding`, or `hybrid`. Results include file/folder kind, relative path, text, rank-fusion score, indexed content hash, processing job, timestamp, and citation ID. The score is reciprocal rank, not a probability. Answer generation belongs to the search agent; use Administration → Agents to test it.

Keys are AES-256-GCM encrypted with the existing `CREDENTIAL_ENCRYPTION_KEY`, a fresh 12-byte nonce, and project/purpose-bound associated data. The stored byte value contains nonce followed by ciphertext and authentication tag. Keys are never returned. Omitted key fields retain existing keys; explicit null removes one. Changing an endpoint with a stored key requires re-entering or removing it. Back up the encryption key with PostgreSQL and the source directory.

The search agent in both applications retrieves from this PostgreSQL workspace. It receives the document catalog on every turn, then uses `/search` and document reads to ground its answer. There is no separate `/rag` answer endpoint. Test the agent under Administration → Agents.
