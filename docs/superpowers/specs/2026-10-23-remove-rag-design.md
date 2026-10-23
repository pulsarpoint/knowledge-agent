# Remove RAG — Design

Approved direction: 2026-10-23 (conversation). Documents live in the database and agents read and draft them with the
document tools; the RAG service (collections, file indexing, embeddings search) is no longer needed.

## Decisions (from the conversation)

1. **RAG goes entirely:** the service, its stack, its tables, its API proxy, its tools, its UI and the *collection*
   resource type.
2. **The Jira Markdown export goes.** Tickets stay in the database (pull, tickets, events, replies unchanged).
3. **The memory embedding model moves to memory** (it is read from RAG's settings today).
4. **The document file export goes** (`GENERATED_DOCS_FOLDER`, its backfill and the folder setting).
5. **`search_documents` becomes a database search** over the documents table.

## 1. Removed

- **Service and stack:** `services/rag/`, `deploy/Dockerfile.rag`, the `rag` and `rag-worker` compose services, their
  env (`RAG_BACKOFFICE_KEY`, `DOCS_WIKI_RAG_URL`), `depends_on: rag` everywhere, `make index`, the rag parts of
  `build-api` / `test`, `scripts/setup.py` (key generation, rag-data moves, legacy checks), `scripts/smoke.py` rag
  checks, `tests/test_setup.py` rag assertions, `.env.example` lines, `ruff.toml` comment. The `jira-rw` and
  generated-docs mounts go where only RAG or the exports used them.
- **admin-api:** `rag/` (proxy and client), `config.resolve_rag`, the startup check and `backoffice_client` plumbing,
  `read_document` and `report_not_covered`, the RAG `search_documents`, `sessions/grounding.py` and the agent `grounded`
  mode (with its prompt and citations), the tool entries and labels, `jira/export.py` (Markdown files) and its API's
  503 path, the code-project removal guard that asked RAG about collections, the document file export
  (`export_document`, `export_location`, `remove_export`, `backfill_exports`, their callers and settings), storage
  overview entries for removed folders.
- **The collection resource type:** `access.TYPES`, run access (`collection_ids`), agent profiles and the wizard,
  subagents and experts, the SDK tool wiring, file and shell tools' collection roots (they keep code projects), the
  agent runner. Stored access objects that still carry collection entries load without error (entries ignored).
- **Database (migration `0132_rag_gone`):** drop `rag_vectors`, `rag_chunks`, `rag_file_log`, `rag_files`,
  `rag_collection_paths`, `rag_collections`, `rag_folders`, `rag_workspaces` (after moving the embedding setting, §2);
  delete jobs of kinds `rag_folder_index` / `rag_collection_sync` and drop `jobs.rag_collection_id` and the kinds from
  its check; remove `collection` entries from stored access (agents and sessions). Down: recreate the tables empty in
  their 0131 shape and restore the column and check (no data).
- **admin-ui:** `features/rag/`, the five rag routes, the RAG sidebar group and titles, collection picks and labels
  (agents, run settings, Debug, resources), RAG wording on other pages (storage overview, code projects, LLM settings,
  Jira, memory, documentation folders); `DocumentationFoldersView`'s collection names. The home route redirects to
  `/documents`.
- **Docs:** `docs/rag.md` deleted; RAG sections removed or rewritten in `README.md`, `deploy/README.md`,
  `docs/user-guide.md`, `architecture.md`, `jira.md`, `llm-settings.md`, `agents.md`, `agent-profiles.md`,
  `documentation.md`, `code-processing.md`, `database.md`, `migrations.md` (history entries stay). Old specs and plans
  stay as history.

## 2. Memory embedding model

- `ka.memory_settings` (one row per project: `embedding_model_id` → `ka.llm_models`, nullable) in `0132`, filled from
  `rag_workspaces.embedding_model_id` before that table is dropped.
- `memory/embedding.py` and `memory/search.py` read it from there; behaviour unchanged (no model → full-text only, as
  today when RAG had none).
- The setting moves to the Memory page (**Embedding model** select, with the existing test-embedding check if one
  exists today in RAG Settings — move it, else none). `packages/llm-settings` stays (memory uses it).

## 3. search_documents (database)

- `search_documents(query, limit?)`: Postgres full-text search over published documents (title weighted above
  description above content; `websearch_to_tsquery`), limited to documents the run may read (its access rule). Returns
  for each: `document_id`, title, short description, tags, the resources it is linked to, and a short snippet
  (`ts_headline`). Agents then call `get_document(document_id)`.
- An index: a generated `tsvector` column (or expression index) on `ka.documents` for live rows, in `0132`.
- Drafts are not searched (agents see their own drafts through `get_document` / `list_documents`).
- Tool description in the documents style (purpose, fields, example).

## 4. Everything else unchanged

Jira pull and tickets, events, code projects and code graph, the litellm gateway (chat and embeddings), memory, the
documents and drafts flows, the inventory.

## Testing

- Migration `0132` up/down: tables gone, embedding setting moved, collection access entries removed, jobs cleaned.
- `search_documents`: ranking (title over content), access filtering, drafts excluded, snippet.
- Memory embeddings read the new setting; the Memory page sets it.
- Old access objects with collections load; no `collection` type left (grep), no `rag` in `services/admin-api/src`,
  `services/admin-ui/app`, `deploy/`, `Makefile`, `scripts/` (grep test), except history.
- UI: no RAG nav or routes, home goes to `/documents`, agent settings without collections.
- `tests/test_setup.py` passes without RAG.
