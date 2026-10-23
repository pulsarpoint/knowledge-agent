# Remove RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the RAG service and everything that exists only for it; move the memory embedding setting to memory;
replace `search_documents` with a database search.

**Architecture:** Replacements first (memory setting, database search), then removals from the outside in (collections,
exports, then the service, stack, UI and tables), so every commit is green. The spec's single migration is split into
`0132_memory_settings`, `0133_document_search` and `0134_rag_gone` for that reason.

**Tech Stack:** PostgreSQL 17; Python 3.14 / FastAPI / psycopg 3 / pytest / uv; docker compose; React Router 7 /
TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-23-remove-rag-design.md`

## Global Constraints

- **Simple code; remove, don't shim.** No compatibility layers for RAG; stored access objects with `collection`
  entries load (entries ignored) until 0134 deletes them.
- Old migrations, old specs and plans, and `docs/migrations.md` history entries are not edited.
- After the last task, a grep finds no `rag`/`RAG`/`collection` (as the RAG concept) in `services/admin-api/src`,
  `services/admin-ui/app`, `deploy/` (except migration history), `Makefile`, `scripts/`, `tests/`, current docs.
- Unchanged behaviour: Jira pull/tickets/events/replies, code projects and code graph, the litellm gateway, memory
  search results, documents/drafts flows, inventory.
- Work only in `.claude/worktrees/remove-rag` (branch `feature/remove-rag`). Never `make`, never touch `.env`/`.local`,
  never `git stash` in any form, never touch the running Docker stack, never kill processes you did not start, never
  spawn subagents. UI: `node_modules` is a symlink (never commit it, never `pnpm install`). Do not run `uv sync` for
  removed packages against the running stack; `uv run --frozen` in admin-api is fine (update `uv.lock` only if a
  workspace member is removed and the lock references it — then `uv lock` is allowed).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees incl. test_code_processing, test_deploy docs command, test_environments rollback);
  `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm format:check` (documentSaves and
  skills are slow: rerun alone); root `tests/` with `uv run --frozen pytest -q tests` if they ran before (check how the
  Makefile runs them).

## Review Focus

1. Memory search with an embedding model set before the migration still uses it after (Task 1).
2. `search_documents` never returns a document the run may not read, nor drafts (Task 2).
3. An agent saved with collections in its access still loads, runs and shows no collection (Task 3).
4. Jira pull still stores tickets and fires events after the export is gone (Task 4).
5. The stack config (`deploy/compose.yaml`) has no service depending on `rag`, and `tests/test_setup.py` passes
   (Task 5).

---

### Task 1: The memory embedding model moves to memory

**Files:** `db/migrations/0132_memory_settings.{up,down}.sql` (table `ka.memory_settings(project_id PK,
embedding_model_id → llm_models, updated_at)`, filled from `rag_workspaces`), `memory/embedding.py`, `memory/search.py`
(+ a small settings read/write in the memory API), the Memory page (`features/memory/MemoryView.tsx`: Embedding model
select + its API), the RAG Settings page stops offering the embedding model, tests (`test_memory_*`,
`test_embedding_models`, a migration test, UI memory test), `docs/llm-settings.md` / user guide memory section.

- [ ] Failing tests (Review Focus 1, the Memory page setting) → implement → full suites → commit
  `feat(memory): the embedding model is a memory setting`.

### Task 2: search_documents searches the database; RAG reading tools go

**Files:** `db/migrations/0133_document_search.{up,down}.sql` (generated `tsvector` column or expression GIN index on
live documents: title A, description B, markdown C), `capabilities/document_tools.py` or `capabilities/documents.py`
(new `search_documents(query, limit=10)`: `websearch_to_tsquery`, rank, `ts_headline` snippet, access-filtered, drafts
excluded; returns document_id, title, description_short, tags, linked resources, snippet), remove `read_document`,
`report_not_covered`, the RAG `search_documents`, `RAG_TRANSPORT` / `_client`, `sessions/grounding.py` and the agent
`grounded` mode (config, prompts, UI switch if any), `tools/tools.json`, `tools/texts.py`, `tools/README.md`, schema
golden, matrix, `knowledge_agents/native_sessions.py` labels, tests (`test_document_tools.py` RAG parts, new
`test_document_search.py`), `docs/agents.md`, `docs/documentation.md`.

- [ ] Failing tests (Review Focus 2, ranking, snippet, limit) → implement → full suites → commit
  `feat(documents): search_documents searches the database; RAG reading tools and grounded mode go`.

### Task 3: The collection resource type goes

**Files:** `access.py` (`TYPES`), `knowledge_agents/config.py` (`COLLECTIONS`), `sessions/run_access.py`,
`sessions/start.py`, `sessions/rows.py`, `agents/profiles_api.py`, `agents/wizard.py`, `capabilities/subagents.py`,
`tools/sdk.py`, `capabilities/files.py`, `capabilities/shell/client.py`, `agents/runner.py`, `data_root.py` comments;
UI `lib/resources.ts`, `features/agents/api.ts`, `wizard.ts`, `features/sessions/agentSettings.ts`, `api.ts`,
`DebugPanel.tsx`; tests listed in the spec map (access, agent access, profiles, wizard, run resources, sessions,
UI fixtures); `docs/agents.md`, `docs/agent-profiles.md`.

- [ ] Failing tests (Review Focus 3; file/shell tools with code projects only) → implement → full suites → commit
  `refactor(access): the collection resource type is gone`.

### Task 4: The Jira and document file exports go

**Files:** `jira/export.py` (delete), `jira/api.py` (the export endpoints / 503 path), `documents/api.py`
(`export_document`, `export_location`, `remove_export`, `backfill_exports` and callers), `documents/drafts.py`
callers, `app.py` startup backfill, settings for `GENERATED_DOCS_FOLDER` / `DOCUMENT_OUTPUT_ROOT` / `JIRA_FOLDER` if
only exports used them, `storage.py` and `components/StorageOverviewView.tsx`, `features/documents/DocumentationFoldersView.tsx`
(collection names), `features/jira/JiraSettingsView.tsx` wording, `code_projects/api.py` RAG guard, tests
(`test_jira_export`, document export tests, `test_code_project_remove`, storage), `docs/jira.md`,
`docs/documentation.md`, user guide.

- [ ] Failing tests (Review Focus 4; publish no longer writes files; code project removal without the guard) →
  implement → full suites → commit `refactor: the Jira and document file exports are gone`.

### Task 5: The RAG service, stack, UI and tables go

**Files:** delete `services/rag/`, `deploy/Dockerfile.rag`; `deploy/compose.yaml` (services, env, depends_on,
volumes/mounts only they used), `.env.example`, `Makefile` (build-api, test, index), `scripts/setup.py`,
`scripts/smoke.py`, `tests/test_setup.py`, `ruff.toml`, root `pyproject.toml` / `uv.lock` workspace members if rag is
one; admin-api `rag/`, `config.resolve_rag`, `cli.py` startup check, `app.py` / `api.py` `backoffice_client` plumbing,
`test_rag*.py`, `test_agent_collections.py`; `db/migrations/0134_rag_gone.{up,down}.sql` (drop the `rag_*` tables,
jobs kinds/column, `collection` access entries) + migration test; `db/tests/schema.sql` stale fixtures; UI
`features/rag/`, rag routes in `routes.ts` and `routes/`, `AdminLayout.tsx` nav and titles, `routes/home.tsx` →
`/documents`, LLM settings links/wording, rag UI tests; docs: delete `docs/rag.md`, rewrite RAG parts of `README.md`,
`deploy/README.md`, `docs/architecture.md`, `docs/user-guide.md`, `docs/database.md`, `docs/llm-settings.md`,
`docs/code-processing.md`; a grep test for leftovers (Global Constraints).

- [ ] Failing tests (Review Focus 5; grep test; home redirect) → remove → full suites (+ root tests) → commit
  `refactor: RAG is gone`.
