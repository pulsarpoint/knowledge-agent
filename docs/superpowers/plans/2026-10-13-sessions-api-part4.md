# One Sessions API, Part 4 (Discovery; Reads Follow the Allowlist) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Server discovery is an ordinary run started from a server in the server page's shared chat, and every run's inventory and document reads follow its allowlist.

**Architecture:** `allowed()` gains `service` and `code_project`; the inventory tools and `get_document` check it. `sessions/start.py` accepts `started_from.server_id`, pins the server and `started_from_server`, and records purpose `discovery`; the proposal tools find their run through `config['session_id']`. Migration 0102 rewrites the uncustomized "Server discovery" prompt and closes open discovery runs. The UI adds a `server` chat scope on the server page with a proposals panel; the discovery run page goes.

**Tech Stack:** FastAPI + psycopg 3 (pytest with a throwaway Postgres), golang-migrate SQL, React Router 7 + shadcn + vitest.

**Spec:** `docs/superpowers/specs/2026-10-13-sessions-api-part4-design.md`

## Global Constraints

- Work only in the worktree `.claude/worktrees/sessions-part4` (branch `feature/sessions-part4`). Never run `make` targets (incl. `db/tests/run.sh` — edit only), never touch `.env` or `.local`, never `git stash`, never touch the running Docker stack or its database.
- Backend tests: `cd services/admin-api && uv run --frozen pytest -q <files>`. Known failures to ignore: code-graph tests in worktrees, `test_deploy::test_docs_commands_serve_the_docs_folder_inside_the_data_root`, `test_environments::test_migration_reapply_and_populated_rollback_protection`.
- UI: `cd services/admin-ui && pnpm exec vitest run`, `pnpm exec tsc --noEmit`, `pnpm exec react-router routes`. Once, from the worktree root: `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` (never commit it; never `pnpm install`). `skills.test.tsx` may flake under load (rerun alone).
- Texts, verbatim: `This {kind} is not available in this run.` (kinds `server`, `service`, `code_project` → shown as `server`, `service`, `code project`); `The proposal tools work only in a run started from a server.`; readiness: `Set up SSH for this server first (SSH settings).`, `Turn on SSH allowed for this server first.`, `Server not found.`; `Closed by an update; start a new chat.`; context `Server: {name} (hostname {hostname}, id {id}).`
- Behaviour comes from the prompt: `DISCOVERY_SYSTEM_PROMPT` and `DISCOVERY_FINAL` are deleted; code appends only the start's `context` under `# Context`.
- Commit after each task; messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Never `git add` `.superpowers` or `node_modules`.

## Review Focus

1. A run asking the inventory about a server, service or code project it was not given must be refused, for ad-hoc, saved-agent and discovery runs alike — Task 1 pins all three.
2. A document attached to a run's server must stay readable with `get_document`; one attached only to another server must not — Task 1 pins both.
3. Proposals of runs started before the update must stay reviewable after 0102 closes them — Task 2 pins it.
4. Ask agent and new-document runs keep working with scoped reads (their chosen resources are what they read) — Task 1 runs their suites.
5. A run link `/servers/:id/discovery/:runId` must open the server page with the chat on that run — Task 4 pins it.

---

### Task 1: Reads follow the allowlist

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/capabilities/context.py` (`ACCESS` kinds `service`, `code_project`; display names)
- Modify: `services/admin-api/src/knowledge_admin/capabilities/inventory.py` (each tool checks the asked entry)
- Modify: `services/admin-api/src/knowledge_admin/capabilities/document_tools.py` (`_readable`)
- Test: `services/admin-api/tests/test_scoped_reads.py` (create); update `tests/test_inventory_tools.py`, `tests/test_discovery_tools.py`, `tests/test_document_draft_reads.py` where they read unpinned entries

**Interfaces:**
- `ACCESS['service'] = lambda c: c['services']['items'] if c['services']['enabled'] else []`; `ACCESS['code_project'] = lambda c: c['code']['projects'] if c['code']['enabled'] else []`. `NOT_AVAILABLE.format(kind=…)` uses `KIND_NAMES = {'server': 'server', 'document': 'document', 'service': 'service', 'code_project': 'code project'}`.
- Inventory tools resolve the asked server (hostname or name), service (name) or project (name) as today, then call `allowed(config, kind, str(row['id']))`; refused → return (not raise) `This {name} is not available in this run.` — the same string a missing entry now returns, so a run cannot tell "exists elsewhere" from "does not exist". Answers about an allowed entry are unchanged (lists inside them still name what the inventory records).
- `get_document`: `_readable(conn, config, document)` = pinned in `document_access`, or attached (`ka.inventory_documents`) to a server, service or code project the run was given (`allowed` for that kind on the attachment's `server_id` / `service_id` / `code_project_id`). The workspace-wide fallback goes. Not readable → `This document is not available in this run.`

- [ ] **Step 1: Write the failing tests** (`tests/test_scoped_reads.py`): build configs the way `tests/test_inventory_tools.py`'s fixture does (read it first), with two servers `web-1` and `db-1`, a service on each, a document attached to each server:
  - a config pinning only `web-1` (ssh enabled, servers=[web-1], services enabled with only web-1's service, code enabled with no projects): `get_server_info_basic(hostname='web-1')` answers; `(hostname='db-1')` returns `This server is not available in this run.`; `get_service_info` for db-1's service returns `This service is not available in this run.`; `get_project_info` for any project returns `This code project is not available in this run.`; `get_server_docs('web-1')` lists web-1's document; `get_document` on web-1's attached document reads it; on db-1's attached document returns `This document is not available in this run.`
  - the same checks driven through a saved agent's pinned config (use `tests/test_sessions_saved_agent.py`'s `agent` fixture, preview its config) and an ad-hoc start's preview config.
- [ ] **Step 2: Run them to verify they fail**, **Step 3: implement**, **Step 4: run** `tests/test_scoped_reads.py tests/test_inventory_tools.py tests/test_discovery_tools.py tests/test_document_draft_reads.py tests/test_document_edit_tools.py tests/test_new_document_runs.py tests/test_sessions_start.py tests/test_sessions_saved_agent.py` then the full suite — PASS apart from known failures (update tests that relied on unpinned reads to pin what they read). **Step 5: commit** `feat(agents): inventory and document reads follow the run's allowlist`.

---

### Task 2: Discovery is an ordinary start; migration 0102

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/sessions/start.py` (`StartedFrom.server_id`, pin `started_from_server`, options `server_id`), `sessions/api.py` (insert purpose `discovery` + `server_id`; list `server_id` with proposal counts; options `server_id`), `capabilities/discovery.py` (`_run` via `config['session_id']`), `tools/catalog.py` (need `'started_from_server'` for the propose tools; `needs_met`; `REQUIRES`/`NEED_REASONS` entry `'a run started from a server'`; add to `RUN_ONLY_NEEDS`), `discovery.py` (delete `DISCOVERY_SYSTEM_PROMPT`, `DISCOVERY_FINAL`, `discovery_config`, the start and list routes, `PROPOSAL_TOOLS` if unused)
- Create: `db/migrations/0102_discovery_runs.up.sql`, `.down.sql`, `tests/test_discovery_runs_migration.py`; edit `db/tests/run.sh`; `docs/migrations.md`
- Modify tests: `tests/test_discovery_runs.py`, `tests/test_discovery_review.py`, `tests/discovery_world.py`, `tests/test_discovery_tools.py` (start through `/api/sessions`)

**Interfaces:**
- `StartedFrom` = exactly one of `document_id`, `new_document`, `server_id` (message `Choose one starting point: a document, a new document or a server.`). With `server_id`: the server must exist (`Server not found.`, 404) and be ready (`readiness()` from `discovery.py`, 400); it is added to `resources.server_ids` when missing; pinned `config['started_from_server'] = {'id', 'name'}`; context set to `Server: {name} (hostname {hostname}, id {id}).` when the request's context is empty (appended under `# Context` as usual); purpose `discovery`, `server_id` column, `target_document_id` NULL, `title` = the message's first line.
- Propose tools: need `'started_from_server'`, met when `config.get('started_from_server')`; `subagent_allowed=False` when pinned; `_run(conn, config)`: `config.get('session_id')` and the session row `WHERE id=%s AND purpose='discovery' FOR UPDATE`; none → `The proposal tools work only in a run started from a server.`
- `GET /api/sessions?server_id=…`: discovery runs of that server, newest first, each row the list columns plus `pending`, `accepted`, `rejected` proposal counts (today's `RUNS` counts). `GET /api/sessions/options?server_id=…`: as for an ad-hoc start, prompts tagged `server-discovery` (default id `md5('knowledge-agent-v3:prompts:server-discovery:' || project)`), servers with `linked: true` for this server and its readiness `reason`, tools including the propose tools. Combining starting points in options → 400 with the message above.
- Migration 0102: if the seeded prompt (`md5('knowledge-agent-v3:prompts:server-discovery:'||project)`) is uncustomized (revision 1, next_version 2, one version — 0097's guard), add version 2 (`md5('knowledge-agent-v3:prompts:server-discovery:v2:'||project)`) with the text below, activate it, `next_version=3`; also redefine `seed_server_discovery_prompt` so new workspaces get version 2's text as version 1. Close open discovery runs and discard their queued inputs. Down: when version 2 is active and untouched, re-activate version 1 and delete version 2 (`next_version=2`); restore 0097's seed function body.

```
Explore the server named in the context and propose what the inventory should record about it.

1. Read what is already recorded first: get_server_info_basic, get_server_services and get_server_docs. Read the documents that matter with get_document.
2. Explore with read-only commands only, with ssh_run on that server: for example systemctl list-units --type=service --state=running, ss -tlnp, ps aux, docker ps, nginx -T, package versions (dpkg -l or rpm -qa) and configuration under /etc. Never change, install, restart or delete anything.
3. Never copy passwords, keys, tokens or other secrets into a proposal. Describe how something is configured instead.
4. Propose every service you find with propose_service, with its deployment on this server. A service that is already recorded is proposed as an update: keep what is right and correct what is not.
5. Propose one overview document for the server with propose_document: its role, operating system, the services on it and how they connect, ports, important paths and logs.
6. Propose one document for each significant service (about_service): what it does, how it runs, its configuration, its logs and how to check that it is healthy.
7. When a proposal is refused, the result says why: correct it and call again.
8. Finish with a short report: what you found, what you proposed and what you could not check.
```

- [ ] **Step 1: Write the failing tests** — a discovery start via `POST /api/sessions` (`started_from.server_id`, model, system_prompt, message): purpose `discovery`, `server_id`, server pinned in `config['ssh']['servers']`, `started_from_server`, context line in the system prompt, no `discovery` key; readiness refusals (not set up / not allowed / unknown); propose tools work in that run (call through the catalog with the pinned config plus `session_id`) and refuse with the message in a run without `started_from_server`; the list with counts; the options; the removed routes answer 404/405; migration tests (rewrite only when uncustomized; customized left alone; open discovery runs closed with inputs discarded; their proposals still listed by `/api/discovery/{id}/proposals`; down/up round trip).
- [ ] **Step 2: fail**, **Step 3: implement**, **Step 4: run** the discovery test files and the full backend suite — PASS apart from known failures. **Step 5: document 0102** in `docs/migrations.md` (prompt rewritten when untouched; what to add to a customized one; open runs closed). **Step 6: commit** `feat(agents): discovery runs start from a server through /api/sessions (0102)`.

---

### Task 3: The server chat scope and the proposals panel component

**Files:**
- Modify: `services/admin-ui/app/lib/sessions.ts` (`ChatScope` adds `{ kind: 'server'; id: string; name: string }`; `getSessions({ serverId })`; `getSessionOptions({ serverId })`; `StartInput.started_from` accepts `{ server_id }`; list rows gain optional `pending/accepted/rejected`), `lib/agentSettings.ts` (storage key `agent-settings:server:{id}`; `startInput` for a server: `started_from: { server_id }`, `resources` with the server in `server_ids`, `context: ''`), `components/AgentChat.tsx` (server scope: run list by `serverId`, foreign = not `discovery` or another server, empty text `Ask the agent to explore this server.`), `components/AgentSettingsSheet.tsx` (server scope: the server locked on under Servers)
- Create: `services/admin-ui/app/components/RunProposals.tsx` (the proposals list with Accept/Reject from `DiscoveryRunView.tsx`, moved; props `{ runId: string | null; version: string; onChanged?: () => void }`; polls/reloads as today's view does and when `version` changes)
- Test: `lib/__tests__/agentSettings.test.ts`, `components/__tests__/runProposals.test.tsx` (create; move the proposal tests from `discoveryRun.test.tsx`)

- [ ] **Step 1: tests** — `startInput` for a server scope (exact body); `RunProposals` lists pending/accepted/rejected, Accept and Reject call today's endpoints (copy assertions from `discoveryRun.test.tsx`), shows `Proposals appear here as the agent makes them.` when empty. **Step 2: fail**, **Step 3: implement**, **Step 4: run** the UI suite, `tsc` (PASS; existing pane tests unchanged), **Step 5: commit** `feat(admin-ui): a server chat scope and the proposals panel component`.

---

### Task 4: The server page uses the shared chat; the discovery run page goes

**Files:**
- Modify: `services/admin-ui/app/components/ServerPage.tsx` (header **Ask agent** toggles the chat beside the server, full-width 2:1 via `useWidePage`; `RunProposals` above the server details for the chat's open run, with the pending count; replaces "Ask agent to collect info" and the Discovery runs section), `app/routes.ts` (`servers/:serverId` with child `discovery/:runId` → a null route file), `components/SessionView.tsx` ("Started from the server" links to `/servers/{id}/discovery/{runId}`)
- Delete: `components/DiscoveryRunView.tsx`, `components/DiscoverySheet.tsx`, `routes/discoveryRun.tsx` (replace with a null child route file like `routes/documentAgentRun.tsx`), `lib/discovery.ts`' run start/list calls that are now unused (keep `getServerPage`, proposals calls)
- Test: `components/__tests__/serverPage.test.tsx`, `discoveryRun.test.tsx` (rewrite as server-page run-link tests)

- [ ] **Step 1: tests** — the Ask agent button opens the chat (region `Ask agent`) and the page goes wide; sending posts `started_from.server_id` with the server in `resources.server_ids`; the proposals panel shows the open run's proposals; `/servers/s1/discovery/r1` opens the page with the chat on run `r1`; Earlier runs lists the server's runs; a server whose SSH is not ready shows its reason and blocks the first message.
- [ ] **Step 2: fail**, **Step 3: implement** (follow `DocumentEditorView`'s pane, child-route and URL-follow pattern), **Step 4: run** the UI suite, `tsc`, routes — PASS; `grep -rn "DiscoveryRunView\|DiscoverySheet\|collect info" services/admin-ui/app` → nothing. **Step 5: commit** `feat(admin-ui): discovery runs in the server page's shared chat`.

---

### Task 5: Docs and the final check

**Files:** `docs/user-guide.md` (server page and discovery sections; the scoped-reads rule in the Ask agent / agents sections), `docs/agents.md` (allowlist kinds, scoped inventory reads, proposal tools need), `docs/documentation.md` if it describes discovery runs, `deploy/README.md` (removed routes in the upgrade notes), `services/admin-api/src/knowledge_admin/tools/README.md` (inventory tools are scoped).

- [ ] **Step 1: update the docs** in plain, short sentences.
- [ ] **Step 2: final search**

```bash
git grep -n "DISCOVERY_SYSTEM_PROMPT\|DISCOVERY_FINAL\|discovery_config\|servers/{server_id}/discovery\|/discovery'\|DiscoveryRunView\|DiscoverySheet\|collect info\|\['discovery'\]" -- . ':!docs/superpowers' ':!db/migrations'
```

Expected: only deliberate 404 assertions and history notes (list them).
- [ ] **Step 3: run everything** (backend full suite, UI checks) — PASS apart from known failures. **Step 4: commit** `docs: discovery in the server page's chat; reads follow the allowlist`.
