# Agent Access Phase 4 (Cleanup) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove everything the access model made unnecessary: the run config's per-kind sections (the access object
becomes the only description of what a run may use), the old ad-hoc building path (`ephemeral.base_config`,
`run_resources.resolve`), `run_resources`/`chosen_resources`, constant API fields, old-session fallbacks, dead code and
stale docs.

**Architecture:** A run's pinned config keeps model, system prompt, instructions, `grounded`, `access`, experts,
`started_from`, `start_notes` and project id. Access entries carry what tools need to connect (server SSH fields and key
reference, MCP URL and token reference) besides paths; `get_available_resources` and Debug show entries without those
connection fields. Saved-agent and ad-hoc runs are built by the same function (validate picks → load items → add
children → `run_access`).

**Tech Stack:** PostgreSQL 17, Python 3.14 / FastAPI / psycopg 3 / openai-agents 0.19 / pytest; React Router 7 /
TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-16-agent-access-design.md` (Phase 4: cleanup, requested by the user
2026-10-16: "cleanup all code that can be removed since we add this functionality"). Phases 1–3 merged (033f1a4).

## Global Constraints

- **Simple code, no unnecessary abstractions; rules testable in isolation.** Behaviour visible to people and agents does
  not change, except the listed removals.
- Work only in `.claude/worktrees/agent-access-4` (branch `refactor/agent-access-cleanup`). Never `make`, never touch
  `.env`/`.local`, never `git stash` (WIP commit instead), never touch the running Docker stack, never kill processes you
  did not start. UI checks: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once
  (never commit it, never `pnpm install`).
- Format: `uv run --frozen ruff format <files>`, `pnpm format`. Commits end with `Co-Authored-By: Claude Opus 5.5
  <noreply@anthropic.com>`; never `git add` `.superpowers` or `node_modules`.
- Development version, test data: no shims, no compatibility with sessions pinned before this branch (resetting the stack
  is the user's action); migrations correct up and down. Next migration: **0116**.
- Secrets never enter access entries: only references (`private_key_ref`, `bearer_token_ref`), as today's sections.
  `get_available_resources` and the Debug tab never show connection fields (host, port, user, key/token references).
- Nothing sandbox-related.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback); `cd services/admin-ui && pnpm exec vitest run &&
  pnpm exec tsc --noEmit && pnpm format:check` (rerun slow tests alone); `cd services/rag && uv run --frozen pytest -q
  -p no:warnings` if touched.

## Review Focus

1. Every tool still works for every kind of run (saved, ad-hoc resource run, Documents → New, Ask agent, Propose wizard,
   handler, subagent, expert) — the access matrix and the run tests pass unchanged in meaning.
2. No pinned config carries `code`, `documents`, `ssh`, `services`, `skills`, `mcp`, `document_access`, `run_resources`
   or `chosen_resources` after Task 2/3 (grep the writers and a test asserting the pinned keys).
3. Connection fields reach the tools but never `get_available_resources`, Debug or the prompt.
4. "Start new chat with these settings" and a new document's `built_for` keep using what the person chose (an
   environment, not what it brings).
5. Nothing removed is still referenced (grep each removed name across services/, packages/, docs/).

---

### Task 1: Dead code, constant fields, stale docs

**Files:** `agents/runner.py` (`shell_enabled` at ~:119/:158, `agent()` docstring, `get_paths`, the `get_agent_config`
re-export), `capabilities/context.py` (`shell_enabled`), `capabilities/shell/client.py` (:57), tools' `config['enabled']`
checks (`files.py:40`, `documents.py:25,135`, `ssh.py:244`, `code_graph.py:180`) — `create_agent` already refuses a
disabled agent; `knowledge_agents/subagents.py:93` (`pop('tools')`); `sessions/start.py` (`resources_fixed` ~:900);
`servers/discovery.py:150-158` (pre-`started_from` fallback); `config.sections` (`config.get('access') or {}`);
`tests/test_clone_agents_migration.py:178-227` (dead body after `return` — remove); test names/fixtures saying
`per_run` (`test_run_resources.py:26`, `test_expert_calls.py:27`, `test_code_graphs_config.py:54`); docs:
`services/admin-api/README.md:126-127`, `tools/README.md:48,97` (and `PerRunExpertTask` in `tools/sdk.py:47` → a plain
name), `docs/agents.md:180-184`, `docs/mcp-servers.md:10-16`, `docs/server-operations.md:10-14`.

- [ ] Step 1: tests — a disabled agent cannot start (already covered: point to it); `resources_fixed` absent from
  `/sessions/options`. Step 2: run. Step 3: remove/rename; grep each removed name. Step 4: full backend + UI suites.
  Step 5: commit `refactor: remove dead code and constant fields left by the access model`.

### Task 2: The access object is the only description of what a run may use

**Files:** `sessions/run_access.py` (entries built from loaded items, not from config sections; server entries add
`host`, `port`, `username`, `allow_sudo`, `private_key_ref`… exactly the fields today's `ssh.servers` items carry; MCP
entries add `url`, `bearer_token_ref`, `enabled`; skill entries keep `description`), `access.py` (`listing` returns entries
without connection fields — one small `PUBLIC` field list), `knowledge_agents/config.py` (`get_agent_config` returns
`model`, `system_prompt`, `instructions`, `grounded`, `access` (held), `experts`…; no per-kind sections; `load_items`
stays as the loader), `agents/runner.py` (`code_paths`, `document_paths`, `get_ssh`, `load_credentials`,
`documents_grounded` → read access entries / `config['grounded']`), `capabilities/ssh.py`, `capabilities/inventory.py`
(:137), `capabilities/mcp.py` (`selected_servers`), `config.mcp_definitions`, `config.credential_references`,
`capabilities/code_graph.py` (load the graph record by the entry's code project id with `load_graphs`),
`capabilities/documents.py:167`, `knowledge_agents/native_sessions.py:216`, `sessions/start.py` (`agent_options.server_state`,
`document_access()` returns access entries directly), `sessions/debug.py`, `sessions/review.py:139-141`,
`agents/wizard.py:200-205`, `knowledge_agents/subagents.py` (`_listed`/`_narrow` → `run_access.cut_to`, skills-off line),
`rag/collections.py` (legacy folder-era code removed, or the module removed if nothing needs it), `tests/access_helpers.py`
(`with_access`/`access_of` take explicit entries), the 26 test files using them.

- [ ] Step 1: failing tests — a pinned saved-agent config and an ad-hoc config contain none of the old section keys
  (Review Focus 2); server/MCP entries carry the connection fields and `get_available_resources`/Debug/prompt never show
  them (Review Focus 3); ssh_run, MCP, code_graph, file and document tools work from entries alone. Step 2: fail.
  Step 3: implement module by module, re-running each module's tests. Step 4: matrix, full backend suite. Step 5: commit
  `refactor(access): the access object is the only description of a run's resources`.

### Task 3: One way to build a run; `run_resources` and `chosen_resources` go

**Files:** `knowledge_agents/ephemeral.py` (removed), `knowledge_agents/run_resources.py` (`RunResources`, `resolve`,
`_selection`, `_fresh`, `_usable_server`, `RunResourceError`, `NOT_USED`/`NOT_ALLOWED`, `picked_ids` removed; `bring`,
`FOUND`/`NOT_AVAILABLE` checks, `picks`, `KINDS` move into `sessions/run_access.py` or stay as the one small module —
whichever leaves fewer files), `sessions/start.py` (`build()` uses the same path as saved agents: validate picks, load
items, add children, `run_access`; `:468-479` server dropping/patching gone; `chosen_resources` gone — the person's
choice is the access entries with `source='given'`, plus `built_for` override for an inventory-document run as a single
explicit field on the run (e.g. `started_from`/`new_document.built_for`); `context_snapshot` `{}` for saved agents),
`sessions/run_access.py` (`built_for` from given entries), `sessions/rows.py` (no `run_resources`/`chosen_resources`; the
session detail returns the given entries for "start new chat with these settings"), `db/migrations/0116_drop_run_resources.up.sql`/
`.down.sql` (drop `ka.agent_sessions.run_resources`; down re-adds it nullable), UI: `features/sessions/api.ts:105-122`,
`agentSettings.ts:127-128`, `lib/resources.ts:47-51`, `features/agents/api.ts:38` and their tests; docs:
`docs/documentation.md:196-201`, `services/admin-api/README.md:70-87`.

- [ ] Step 1: failing tests — an ad-hoc server/service/environment run and a saved-agent run are built by the same
  function and produce the same access as before (children, sources, content editor); "start new chat with these
  settings" restores an environment pick as an environment; an inventory-document run's built_for is its owner; the
  column is gone and comes back on down. Step 2: fail. Step 3: implement; grep every removed name (Review Focus 5).
  Step 4: full backend, rag (if touched) and UI suites. Step 5: commit
  `refactor(sessions): one way to build a run; run_resources and chosen_resources removed`.
