# Remove Claude Code Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One kind of agent (native): remove the Claude Code runtime, worker, SDK and UI, and rename what still says
"claude code" (table, API, modules, UI lib and components).

**Architecture:** Six tasks, each leaving both suites green: (1) delete the worker/CLI/SDK/container; (2) agents are
native only in the backend (API, config, wizard, tools) while the DB columns still exist; (3) migration 0083 converts
data, drops the columns and renames the table, and the code follows; (4) rename backend modules, API prefix and audit
actions; (5) the UI; (6) docs.

**Tech Stack:** FastAPI, psycopg3, pydantic v2, golang-migrate SQL (admin-api); React Router 7, shadcn/ui, vitest
(admin-ui); docker compose.

**Spec:** `docs/superpowers/specs/2026-10-08-remove-claude-code-design.md`

**Inventories (read the section for your task before editing; line numbers are from `7a70276` and drift):**
- Backend code: `/private/tmp/claude-501/-Users-graovic-fincore-knowledge-agent-v3/12fb3e0c-ebe9-492a-84fa-83be30e65101/scratchpad/inv-backend.md`
- Backend tests + schema: `…/scratchpad/inv-tests-schema.md` (raw live schema: `…/scratchpad/schema_raw.txt`)
- UI: `…/scratchpad/inv-ui.md`

Inventory notes are guidance, not authority: a "DELETE" verdict that turns out to cover native behaviour is a
"rewrite for native". The spec wins over the inventory. Exception (ruling): the inventory suggests rewriting old audit
rows `claude_code.session.*`; the spec says existing audit rows keep their names — do not touch them.

## Global Constraints

- Work only in the worktree for this plan; never `git commit -a` (stage named paths only).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd services/admin-api && uv run pytest -q` (≈5 min; `-x` and file filters while iterating).
  Known pre-existing failures that do not block: `tests/test_environments.py::test_migration_reapply_and_populated_rollback_protection`;
  `tests/test_deploy.py::test_docs_commands_serve_the_docs_folder_inside_the_data_root` (environment-dependent).
- UI tests: `cd services/admin-ui && npx vitest run` and `npx tsc --noEmit`. `skills.test.tsx` may flake under load; rerun alone.
- Unrelated `runtime` names stay: service deployments (`runtime`, `runtime_name`), `bedrock-runtime`, `model_adapter`.
- `scripts/benchmarks/` and old `docs/superpowers/` specs/plans stay untouched.
- Native behaviour does not change: same tools, same prompts, same session flow.
- Error copy: an enabled-model refusal stays `'Choose an enabled model compatible with this agent.'`; Generate
  documents keeps `NO_MODEL = 'Choose an enabled model.'`.

## Review Focus

1. **Sessions pinned before this change** carry `runtime` / `claude_code_tools` keys inside `agent_config` JSON. Loading
   and running such a session must not fail (read with `.get`, never validate the pinned JSON against a strict shape).
   Task 3 adds a test that runs a turn of a session whose pinned config has both keys.
2. **Old clients sending `runtime` or `claude_code_tools`** to profile save / wizard create / wizard generate get a
   validation error (422 or 400, as the app's validation handler returns), not a silent ignore. Task 2 tests it.
3. **A converted Claude Code agent** runs natively right away: it has tool rows for its enabled capabilities, keeps its
   model, prompt and resources, and its Test tab works. Task 3's migration test checks the tool rows against
   `enabled_capability_tools` for the same flags.
4. **Rolling back** (`0083` down then up) leaves a working schema; every older migration test that runs a historical
   down first runs `0083` down. Task 3 covers it.
5. **Bookmarked pages** `/documentation/generations` and `/documentation/generations/:id` keep working after the API
   moves to `/api/generations`; Task 5 tests the page fetches the new paths.

---

### Task 1: Delete the Claude Code worker, CLI, SDK and container

**Files:**
- Delete: `services/admin-api/src/knowledge_admin/claude_code_worker.py`, `services/admin-api/src/knowledge_admin/claude_code_cli.py`
- Delete tests: `services/admin-api/tests/test_claude_code_worker.py`, `services/admin-api/tests/test_claude_code_runner.py`, `services/admin-api/tests/claude_code_fakes.py`
- Create: `services/admin-api/tests/run_helpers.py` (helpers moved out of `test_claude_code_runner.py`)
- Modify: `services/admin-api/pyproject.toml`, `services/admin-api/uv.lock`, `deploy/compose.yaml`, `deploy/Dockerfile.admin-api`, `deploy/README.md`
- Modify: `services/admin-api/src/knowledge_admin/knowledge_agents/session_worker.py` (drop `reclaim` hook if only the worker used it), `services/admin-api/src/knowledge_admin/knowledge_agents/native_sessions.py` (comments)
- Modify tests: `tests/native_fakes.py`, `tests/test_native_sessions.py`, `tests/test_session_worker.py`, `tests/test_subagents.py`, `tests/test_test_session_worker.py`, `tests/test_native_session_events.py`, `tests/test_deploy.py`, any other importer found by grep

**Interfaces:**
- Produces: `tests/run_helpers.py` exporting `events`, `post`, `session_row`, `wait_for` with the same signatures and
  behaviour as today's `test_claude_code_runner.py` versions (`session_row` keeps reading `ka.agent_sessions_cc` until
  Task 3). `tests/native_fakes.py` defines `Recorder` itself (moved from `claude_code_fakes.py`).
- `session_worker.claim(store, worker_id, active, free, runtime)` keeps its signature in this task (the column still
  exists; native passes `'openai'`).

- [ ] **Step 1: Move the shared test helpers first.** Copy `events`, `post`, `session_row`, `wait_for` (and their
  imports) from `tests/test_claude_code_runner.py` into `tests/run_helpers.py` verbatim. Move class `Recorder` from
  `tests/claude_code_fakes.py` into `tests/native_fakes.py` (replace `from claude_code_fakes import Recorder as _Recorder`
  with the class body, keeping the name the file uses). Point every importer at the new modules:
  `grep -rn "test_claude_code_runner\|claude_code_fakes" services/admin-api/tests`.
- [ ] **Step 2: Run the moved-helper importers** — `uv run pytest -q tests/test_native_sessions.py tests/test_subagents.py tests/test_test_session_worker.py tests/test_native_session_events.py` — expected PASS (pure move).
- [ ] **Step 3: Delete the worker tests and the tests that need the Claude Code worker.** Delete
  `test_claude_code_worker.py`, `test_claude_code_runner.py`, `claude_code_fakes.py`. In `test_native_sessions.py`
  delete `test_both_workers_run_only_their_own_sessions` and the `claude_code_worker.reclaim_slot` monkeypatch in the
  `native` fixture. In `test_session_worker.py` delete `test_each_worker_claims_only_sessions_of_its_agent_type` and
  `test_claude_code_worker_keeps_its_names`; rewrite `test_finish_turn_commits_with_a_successful_turn_only` and
  `test_comments_reach_only_the_running_turn…` to claim with `claim(store, worker, …, 'openai')` on a native session
  (use `session_rows.add_ad_hoc_session` or the `sessions` fixture's `start`) instead of `claude_code_worker.claim` +
  `claude_code_session`. In `test_deploy.py` delete `test_claude_code_worker_shares_a_network_only_with_postgres` and
  remove `claude-code-worker` / `claude-code-home` / `claude-code` from the expected service, volume and network sets.
- [ ] **Step 4: Delete the production pieces.** Delete `claude_code_worker.py`, `claude_code_cli.py`. In
  `pyproject.toml` remove `"claude-agent-sdk==0.2.163",` and the scripts `knowledge-claude-code-worker` and
  `knowledge-claude-cli`; run `uv lock` in `services/admin-api`. In `deploy/compose.yaml` delete the
  `claude-code-worker` service, the `claude-code` network (and `claude-code` from postgres' `networks:` plus its
  comment), and the `claude-code-home` volume; reword the agent-session-worker comment to "Generate documents and Test
  sessions". In `deploy/Dockerfile.admin-api` delete the comment block and `RUN` that create the `claude` slot users
  and check `MAX_SLOTS` / `claude_agent_sdk`. In `session_worker.py`, if `serve(..., reclaim=…)` is now only ever
  called without `reclaim`, remove the parameter and its docstring mention of Claude Code. Fix comments in
  `native_sessions.py` that cite `claude_code_worker` (inventory §13). Update `deploy/README.md` (inventory §21:
  remove the worker/network/volume lines; keep the `/api/claude-code` mention for Task 4).
- [ ] **Step 5: Verify nothing imports the deleted modules.**
  Run: `grep -rn "claude_code_worker\|claude_code_cli\|claude_agent_sdk\|claude-code-worker\|claude-code-home" services deploy --include='*.py' --include='*.toml' --include='*.yaml' --include='Dockerfile*' | grep -v .venv`
  Expected: no output (benchmarks are outside `services`/`deploy`).
- [ ] **Step 6: Full backend suite** — `uv run pytest -q` — expected: only the known failures. Also
  `docker compose -f deploy/compose.yaml config --quiet` (with the repo's usual env file if it needs one) exits 0.
- [ ] **Step 7: Commit**
```bash
git add -A services/admin-api/src/knowledge_admin services/admin-api/tests services/admin-api/pyproject.toml services/admin-api/uv.lock deploy/compose.yaml deploy/Dockerfile.admin-api deploy/README.md
git commit -m "chore: remove the Claude Code worker, CLI, SDK and container"
```

---

### Task 2: Agents are native only (backend)

The DB columns `agents.runtime` (default `'openai'`), `agents.claude_code_tools` and `agent_sessions_cc.runtime` still
exist in this task; code stops reading and writing the agent columns and keeps inserting sessions with
`runtime='openai'` (that column's default is `'claude_code'`).

**Files:**
- Modify: `knowledge_agents/config.py`, `profiles_api.py`, `agent_wizard.py`, `agent_clones.py`, `tools_api.py`,
  `tools/catalog.py`, `llm_api.py`, `agent_sessions.py`, `claude_code_api.py`, `claude_code.py`,
  `knowledge_agents/ephemeral.py`, `knowledge_agents/run_resources.py`, `knowledge_agents/__init__.py`
  (all under `services/admin-api/src/knowledge_admin/`; inventory §1–§8, §11, §16)
- Delete tests: `tests/test_claude_code_agents.py` (after moving anything native it uniquely covers, see Step 1)
- Modify tests: `test_claude_code_sessions.py`, `test_agent_tools_api.py`, `test_agent_tools_config.py`,
  `test_agent_wizard.py`, `test_subagent_switch.py`, `test_knowledge_agents_config.py`, `test_subagent_config.py`,
  `test_tool_catalog.py`, `test_code_graphs_config.py`, `test_ad_hoc_agents.py`, `test_test_sessions.py`,
  `test_profiles.py` (and any importer of `claude_code_agent`, `CHOOSE_CLAUDE`, `CLAUDE_CODE_TOOLS`, `require_native`,
  `normalize_tools`, `ensure_native_tools`)

**Interfaces:**
- Produces: `model_fits(model: Mapping[str, Any]) -> bool` in `knowledge_agents/config.py` — False for embeddings,
  else `code_compatible(model['provider'], model['api_format'], model['model'])` (same argument order the current
  native branch uses). Every caller drops the runtime argument.
- Produces: `check_model(conn, model_id) -> None` in `profiles_api.py` (runtime argument removed; message always
  `'Choose an enabled model compatible with this agent.'`).
- Produces: AgentConfig without `runtime` / `claude_code_tools`; `tools_api.flags(row)` without `'runtime'`;
  `native_tools(conn, agent_id)` without the runtime parameter; `sections()` without the `'type'` key.
- Removed: `CHOOSE_CLAUDE`, `ClaudeCodeTool`, `CLAUDE_CODE_TOOLS`, `NATIVE_ONLY`, `require_native`, `normalize_tools`,
  `Runtime`, `tools_api.CLAUDE_CODE`, `tools_api.ensure_native_tools`; from `claude_code.py`: `SYSTEM_PROMPT`,
  `BUILT_IN_PROMPT_TITLE`, `ALLOWED_TOOLS`, `DISALLOWED_TOOLS` (keep `FINAL_ANSWER`, `IDLE_CLOSED`,
  `agent_system_prompt`, `close_idle`).
- Kept for Task 3: `enabled_capability_tools(config)` and `implied_tools(config)` in `tools/catalog.py` (without the
  runtime branch).

- [ ] **Step 1: Salvage native coverage from `test_claude_code_agents.py`.** Its
  `test_save_details_sets_the_model_with_the_type` also covers native model saving (set, keep when omitted, clear with
  null, audit `model_id` from/to, unknown id 400). Move that coverage to `tests/test_profiles.py` as
  `test_save_details_sets_the_model` with GPT and Claude connections on a native agent, without the type switch:
```python
def test_save_details_sets_the_model(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    claude, gpt = models(client)  # copy models() from test_claude_code_agents.py into test_profiles.py
    agent = create(client, 'One save')
    with_gpt = update(client, agent, model_id=gpt)
    assert with_gpt.status_code == 200 and with_gpt.json()['model_id'] == gpt, with_gpt.text
    with_claude = update(client, with_gpt.json(), model_id=claude)
    assert with_claude.status_code == 200 and with_claude.json()['model_id'] == claude, with_claude.text
    value = {field: with_claude.json()[field] for field in ProfileInput.model_fields if field != 'model_id'}
    kept = client.put(f"/api/agents/{agent['id']}", headers=HEADERS, json={**value, 'description': 'kept'})
    assert kept.status_code == 200 and kept.json()['model_id'] == claude, kept.text
    cleared = update(client, kept.json(), model_id=None)
    assert cleared.status_code == 200 and cleared.json()['model_id'] is None, cleared.text
    with store.connect() as conn:
        details = conn.execute("""SELECT details FROM ka.audit_events WHERE action='agent.updated' AND details->>'agent_id'=%s
            ORDER BY created_at DESC,id DESC LIMIT 1""", (agent['id'],)).fetchone()['details']
    assert details == {'agent_id': agent['id'], 'model_id': {'from': claude, 'to': None}}
    for bad in (str(uuid4()), 'not-a-uuid'):
        assert update(client, cleared.json(), model_id=bad).status_code == 400, bad
```
  Also keep a native version of `test_claude_code_agent_without_sessions_is_deleted` if no native deletion test
  exists (`grep -n "def test.*delet" tests/test_profiles.py tests/test_delete_agent_history.py`). Then delete the file.
- [ ] **Step 2: Write the failing tests for the new contract.** In `tests/test_profiles.py`:
```python
def test_agents_have_no_type(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    agent = create(client, 'Plain')
    assert 'runtime' not in agent and 'claude_code_tools' not in agent
    for bad in ({'runtime': 'openai'}, {'claude_code_tools': ['Read']}):
        assert update(client, agent, **bad).status_code in (400, 422), bad
    wizard = {**wizard_payload('Typed'), 'runtime': 'openai'}
    assert client.post('/api/agents/wizard', headers=HEADERS, json=wizard).status_code in (400, 422)
    generate = client.post('/api/agents/wizard/generate', headers=HEADERS,
                           json={'role': 'x', 'model_id': str(uuid4()), 'runtime': 'openai'})
    assert generate.status_code in (400, 422)
    config = client.get(f"/api/agents/{agent['id']}/config", headers=HEADERS).json()
    assert 'runtime' not in config and 'claude_code_tools' not in config
```
  Use whichever status the app's validation handler returns for extra fields (check `test_profiles.py` for an existing
  extra-field assertion) and assert that exact code instead of the tuple. If the wizard's create or generate input model
  does not already forbid extra fields (`model_config = ConfigDict(extra='forbid')`), add it so an old client's
  `runtime` is refused rather than ignored. Run it: expected FAIL (`runtime` present).
- [ ] **Step 3: Implement** following inventory §1–§8, §11, §16:
  - `config.py`: delete the removed names; `model_fits(model)`; AgentConfig fields; `native_tools(conn, agent_id)`;
    `sections()` without `'type'`; the agents SELECT without `runtime,claude_code_tools`; graphs condition without
    runtime. Reword docstrings that say "both types".
  - `profiles_api.py`: `ProfileInput` without `runtime` / `claude_code_tools`; `check_model(conn, model_id)`; update
    handler without runtime/tools/switch code (`switch` audit keeps `resources` and `model_id`); the UPDATE without
    `runtime=%s,claude_code_tools=%s`; `subagents` read/write without the runtime condition; responses without the two
    fields (if a query uses `a.*`, pop them before returning). `tools_summary` call without `claude_code_tools`.
  - `agent_wizard.py`: CreateInput / GenerateInput without `runtime` / `claude_code_tools`; offered models
    `model_fits(m)`; designer task without `agent_type`; delete the Claude Code checks; INSERT without the two
    columns; tool seeding always runs (dedent the `if value.runtime == 'openai':` block); audit without `runtime`.
    Check the designer prompt/schema text for "agent type"/"Claude Code" and remove those sentences.
  - `agent_clones.py`: `CONFIGURATION` without `'runtime', 'claude_code_tools'`.
  - `tools_api.py`: `AGENT` SELECT without the two columns; `flags()` without `'runtime'`; delete the Claude Code
    branches (GET returns `{'revision', 'tools'}` — keep a `'runtime': 'openai'` key only if a UI test needs it; the UI
    drops it in Task 5, so prefer removing it now); delete `ensure_native_tools` and `CLAUDE_CODE`.
  - `tools/catalog.py`: `implied_tools` and `tools_summary` without the runtime branches and docstring clauses.
  - `llm_api.py`: delete the "used by a Claude Code agent" block and the `claude_model` / `CHOOSE_CLAUDE` imports.
  - `agent_sessions.py`: delete `require_native(config)`; `model_fits(model)`. Keep inserting `runtime` `'openai'`.
  - `claude_code_api.py`: `model_fits(model)`. Keep inserting `runtime` `'openai'`.
  - `claude_code.py`: delete `SYSTEM_PROMPT`, `BUILT_IN_PROMPT_TITLE`, `ALLOWED_TOOLS`, `DISALLOWED_TOOLS` and the
    `CLAUDE_CODE_TOOLS` import; reword the module docstring and `agent_system_prompt`'s docstring.
  - `ephemeral.py`: drop the `'runtime'` / `'claude_code_tools'` keys; `run_resources.py:183`: drop
    `and effective['runtime'] == 'openai'`; `knowledge_agents/__init__.py` docstring.
- [ ] **Step 4: Fix the tests** (inventory B.2 per-test verdicts): delete the Claude Code-only tests in
  `test_agent_tools_api.py`, `test_agent_tools_config.py`, `test_agent_wizard.py`, `test_subagent_switch.py`,
  `test_knowledge_agents_config.py`, `test_test_sessions.py` (the "refused for a Claude Code agent" part); replace
  `claude_code_agent(...)` with a native agent where a test only needed "another agent"; remove `runtime` keys from
  config dicts in `test_subagent_config.py`, `test_tool_catalog.py`, `test_code_graphs_config.py`,
  `test_ad_hoc_agents.py`; `test_ensure_native_tools_follows_the_resources_mode` is deleted with the function (its
  rule is re-tested by Task 3's migration test). In `test_claude_code_sessions.py` the `sessions` fixture creates a
  native agent (`native_agent` in the same file) instead of `claude_code_agent`; keep `claude_code_session` (it
  inserts rows directly) only if a remaining test still uses it, else delete it. Migration tests that read
  `agents.runtime` keep working in this task (the column exists).
- [ ] **Step 5: Run** `uv run pytest -q tests/test_profiles.py` (new tests PASS), then the full suite — only the known failures.
  `grep -rn "claude_code_tools\|CHOOSE_CLAUDE\|require_native\|normalize_tools\|ClaudeCodeTool\|'claude_code'" services/admin-api/src`
  → only `claude_code_api`/`claude_code.py` module names and the sessions `runtime` INSERT literal remain.
- [ ] **Step 6: Commit**
```bash
git add -A services/admin-api/src services/admin-api/tests
git commit -m "feat(admin-api): agents are native only; the agent type and Claude Code tools are gone"
```

---

### Task 3: Migration 0083 — convert, drop the columns, rename the sessions table

**Files:**
- Create: `db/migrations/0083_remove_claude_code.up.sql`, `db/migrations/0083_remove_claude_code.down.sql`
- Create: `services/admin-api/tests/test_remove_claude_code_migration.py`
- Modify: every `src` file naming `ka.agent_sessions_cc` (inventory §22: `claude_code.py`, `claude_code_api.py`,
  `agent_sessions.py`, `agent_reviews.py`, `profiles_api.py`, `knowledge_agents/session_worker.py`,
  `knowledge_agents/native_sessions.py`, `knowledge_agents/history.py`), and the writers of the dropped columns
- Modify: `db/tests/run.sh` (line 61 `= 81` → `= 82`; lines 84-91 assert `ka.agent_sessions` exists with a `purpose`
  column and `ka.agent_sessions_cc` does not)
- Modify tests: `tests/session_rows.py`, `tests/run_helpers.py`, and every test naming the old table or dropped
  columns (inventory B.2: `test_native_sessions`, `test_session_worker`, `test_test_sessions`,
  `test_test_sessions_migration`, `test_delete_agent_history`, `test_agent_apply`, `test_tool_counts`,
  `test_run_resources`, `test_test_session_feedback`, `test_claude_code_sessions`, `test_native_session_events`,
  `test_subagents`, `test_ad_hoc_sessions_migration`)
- Modify migration tests that run historical downs (inventory B.5): prepend `0083_remove_claude_code.down.sql`
  (newest first) to every chain; delete the tests that only test Claude Code migrations:
  `test_claude_code_migration.py` (0070), `test_knowledge_agents_migration.py` (0072),
  `test_native_sessions_migration.py` (0074); delete Claude Code-only cases inside `test_agent_tools_migration.py`,
  `test_clone_agents_migration.py`, `test_test_sessions_migration.py`, `test_ad_hoc_sessions_migration.py`.

**Interfaces:**
- Consumes: Task 2's code no longer reads `agents.runtime` / `claude_code_tools`; `catalog.enabled_capability_tools`.
- Produces: table `ka.agent_sessions` (no `runtime`, `claude_session_id`, `cost_usd`; `agent_config NOT NULL`);
  `session_worker.claim(store, worker_id, active, free)` (runtime parameter and `s.runtime=%s` predicate gone);
  `finish_turn(...)` without `claude_session_id`; usage dicts without `cost_usd`.

Ruling (deviates in detail from the spec, stronger): after the native-config check goes with `runtime`, every session
is native, so `agent_config` becomes `NOT NULL` and the ad-hoc check is `agent_id IS NOT NULL OR purpose = 'documents'`.

- [ ] **Step 1: Write the failing migration test** `tests/test_remove_claude_code_migration.py`. It must cover, on
  a `store` DB:
  1. Run `0083 down` (columns back), insert a Claude Code agent per flag combination (`repositories_enabled`,
     `documents_enabled`, `documents_grounded`, `ssh_enabled` — all 16) with `runtime='claude_code'` and no
     `agent_tools` rows, one Claude Code agent that already has one tool row, a Claude Code session (with an input and
     a review) and a native documents session with an input; run `0083 up`.
  2. Each converted agent's `agent_tools` rows equal
     `enabled_capability_tools({'code': {'enabled': r}, 'documents': {'enabled': d, 'grounded': g}, 'ssh': {'enabled': s}})`
     (import from `knowledge_admin.tools.catalog`); the agent that had a row keeps exactly that row.
  3. The Claude Code session, its input and review are gone; the native session and its input remain in
     `ka.agent_sessions`; `to_regclass('ka.agent_sessions_cc') IS NULL`; `ka.agents` has no `runtime` /
     `claude_code_tools` columns; constraint and index names on `ka.agent_sessions` contain no `_cc_`.
  4. `INSERT` of a session with `agent_id NULL` and `purpose='test'` fails (ad-hoc check), and with
     `agent_config NULL` fails.
  5. `0083 down` then `0083 up` again succeeds with the native session still present.
  6. Review Focus 1: a session whose pinned `agent_config` contains `"runtime": "claude_code"` and
     `"claude_code_tools": [...]` keys still loads through `GET /api/claude-code/sessions/{id}` (prefix renamed in
     Task 4; update the URL there).
  Use the helpers in `session_rows.py` (after Step 3 they insert into the new table; in the "down" state insert with
  explicit SQL against `ka.agent_sessions_cc`). Run: expected FAIL (migration file missing).
- [ ] **Step 2: Write the migration.** `db/migrations/0083_remove_claude_code.up.sql`:
```sql
BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Claude Code is gone: its sessions are deleted (inputs and reviews cascade; sessions point to documents, so saved
-- documents stay).
DELETE FROM agent_sessions_cc WHERE runtime = 'claude_code';

-- Claude Code agents become native with the tools their capabilities give, as a switch to native did
-- (knowledge_admin.tools.catalog.enabled_capability_tools; per run and fixed give the same tools here).
INSERT INTO agent_tools(agent_id, tool)
SELECT a.id, t.tool
FROM agents a
CROSS JOIN LATERAL (VALUES
    ('list_files',         a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('read_file',          a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('search_files',       a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('shell_run',          a.repositories_enabled OR (a.documents_enabled AND NOT a.documents_grounded)),
    ('code_search',        a.repositories_enabled),
    ('ast_grep',           a.repositories_enabled),
    ('code_graph',         a.repositories_enabled),
    ('search_documents',   a.documents_enabled),
    ('read_document',      a.documents_enabled),
    ('report_not_covered', a.documents_enabled AND a.documents_grounded),
    ('ssh_run',            a.ssh_enabled)
) AS t(tool, offered)
WHERE a.runtime = 'claude_code' AND t.offered
  AND NOT EXISTS (SELECT 1 FROM agent_tools x WHERE x.agent_id = a.id);

-- Every agent is native: the type and the Claude Code tools go (their checks go with them).
ALTER TABLE agents DROP COLUMN runtime, DROP COLUMN claude_code_tools;

-- Every session is native and pins its config.
ALTER TABLE agent_sessions_cc
    DROP CONSTRAINT agent_sessions_cc_ad_hoc,
    DROP CONSTRAINT agent_sessions_cc_native_config,
    DROP CONSTRAINT agent_sessions_cc_test_native,
    DROP CONSTRAINT agent_sessions_cc_runtime,
    DROP COLUMN runtime,
    DROP COLUMN claude_session_id,
    DROP COLUMN cost_usd,
    ALTER COLUMN agent_config SET NOT NULL;

ALTER TABLE agent_sessions_cc RENAME TO agent_sessions;
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'ka.agent_sessions'::regclass AND conname LIKE 'agent\_sessions\_cc\_%' LOOP
        EXECUTE format('ALTER TABLE ka.agent_sessions RENAME CONSTRAINT %I TO %I',
                       r.conname, 'agent_sessions_' || substr(r.conname, 19));
    END LOOP;
    FOR r IN SELECT indexname FROM pg_indexes
             WHERE schemaname = 'ka' AND tablename = 'agent_sessions' AND indexname LIKE 'agent\_sessions\_cc\_%' LOOP
        EXECUTE format('ALTER INDEX ka.%I RENAME TO %I', r.indexname, 'agent_sessions_' || substr(r.indexname, 19));
    END LOOP;
END $$;

ALTER TABLE agent_sessions
    ADD CONSTRAINT agent_sessions_ad_hoc CHECK (agent_id IS NOT NULL OR purpose = 'documents');

COMMIT;
```
  (`substr(name, 19)` drops the 18-character prefix `agent_sessions_cc_`.) `db/migrations/0083_remove_claude_code.down.sql`:
```sql
BEGIN;
SET search_path = ka, public;
SET CONSTRAINTS ALL IMMEDIATE;

-- Deleted Claude Code sessions are not restored; every agent and session comes back as native.
ALTER TABLE agent_sessions DROP CONSTRAINT agent_sessions_ad_hoc;
ALTER TABLE agent_sessions RENAME TO agent_sessions_cc;
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'ka.agent_sessions_cc'::regclass AND conname LIKE 'agent\_sessions\_%'
               AND conname NOT LIKE 'agent\_sessions\_cc\_%' LOOP
        EXECUTE format('ALTER TABLE ka.agent_sessions_cc RENAME CONSTRAINT %I TO %I',
                       r.conname, 'agent_sessions_cc_' || substr(r.conname, 16));
    END LOOP;
    FOR r IN SELECT indexname FROM pg_indexes
             WHERE schemaname = 'ka' AND tablename = 'agent_sessions_cc' AND indexname LIKE 'agent\_sessions\_%'
               AND indexname NOT LIKE 'agent\_sessions\_cc\_%' LOOP
        EXECUTE format('ALTER INDEX ka.%I RENAME TO %I', r.indexname, 'agent_sessions_cc_' || substr(r.indexname, 16));
    END LOOP;
END $$;

ALTER TABLE agent_sessions_cc
    ALTER COLUMN agent_config DROP NOT NULL,
    ADD COLUMN claude_session_id text CONSTRAINT agent_sessions_cc_claude_session_id_check
        CHECK (char_length(claude_session_id) <= 200),
    ADD COLUMN cost_usd numeric(14,6) NOT NULL DEFAULT 0 CONSTRAINT agent_sessions_cc_cost_usd_check CHECK (cost_usd >= 0),
    ADD COLUMN runtime text NOT NULL DEFAULT 'openai',
    ADD CONSTRAINT agent_sessions_cc_runtime CHECK (runtime IN ('openai', 'claude_code')),
    ADD CONSTRAINT agent_sessions_cc_native_config CHECK ((runtime = 'openai') = (agent_config IS NOT NULL)),
    ADD CONSTRAINT agent_sessions_cc_test_native CHECK (purpose = 'documents' OR runtime = 'openai'),
    ADD CONSTRAINT agent_sessions_cc_ad_hoc CHECK (agent_id IS NOT NULL
        OR (purpose = 'documents' AND runtime = 'openai' AND agent_config IS NOT NULL));
ALTER TABLE agent_sessions_cc ALTER COLUMN runtime SET DEFAULT 'claude_code';  -- as 0074 left it
COMMENT ON COLUMN agent_sessions_cc.runtime IS 'The agent type the session runs with: openai (native) or claude_code.';

ALTER TABLE agents
    ADD COLUMN runtime text NOT NULL DEFAULT 'openai' CONSTRAINT agents_runtime_check
        CHECK (runtime IN ('openai', 'claude_code')),
    ADD COLUMN claude_code_tools text[] NOT NULL DEFAULT '{Read,Grep,Glob,Bash}' CONSTRAINT claude_code_tools_known
        CHECK (claude_code_tools <@ ARRAY['Read', 'Grep', 'Glob', 'Bash']);

COMMIT;
```
  (`substr(name, 16)` drops `agent_sessions_`.) Compare the result of up→down with `schema_raw.txt` (column defaults
  and check definitions) and fix any difference; column order may differ.
- [ ] **Step 3: Make the code follow.** Replace `ka.agent_sessions_cc` with `ka.agent_sessions` in every `src` SQL
  string (`grep -rn "agent_sessions_cc" services/admin-api/src`); drop `runtime` from the session INSERTs in
  `claude_code_api.py` and `agent_sessions.py`; drop `s.runtime` and `s.cost_usd` from `SUMMARY`/`DETAIL`/`STATE`;
  `session_worker.claim` without the runtime parameter and predicate (native caller in `native_sessions.serve` /
  `shared.serve` updated; `serve(..., runtime=…)` loses `runtime`); `finish_turn` without `claude_session_id` and
  `cost_usd`; `NO_USAGE` and native usage dicts without `cost_usd`; `add_usage` without `cost_usd`. Tests: same
  replacements in `session_rows.py`, `run_helpers.py` and the files listed above; remove `cost_usd` from expected
  usage and session tuples; `test_run_resources.TABLES` / constraint names become the new names.
- [ ] **Step 4: Fix the historical migration tests** (inventory B.5). Every chain that runs a down ≤ 0082 on the
  latest schema first runs `0083_remove_claude_code.down.sql`. Delete the three Claude Code migration test files
  listed above and the Claude Code-only cases in the four mixed files.
- [ ] **Step 5: Run** the new test, then the full suite — only the known failures. Then the db script if docker is
  available: `bash db/tests/run.sh` — expected PASS (it asserts rollback to 82 and the renamed table).
- [ ] **Step 6: Commit**
```bash
git add db/migrations/0083_remove_claude_code.up.sql db/migrations/0083_remove_claude_code.down.sql db/tests/run.sh
git add -A services/admin-api/src services/admin-api/tests
git commit -m "feat(db): 0083 converts Claude Code agents, drops their columns and renames sessions to ka.agent_sessions"
```

---

### Task 4: Rename the generation modules, API prefix and audit actions

**Files:**
- Rename: `services/admin-api/src/knowledge_admin/claude_code_api.py` → `generations_api.py`;
  `services/admin-api/src/knowledge_admin/claude_code.py` → `session_text.py` (use `git mv`)
- Modify: `api.py`, `agent_sessions.py`, `knowledge_agents/session_worker.py`, `knowledge_agents/native_sessions.py`
  (docstrings), `services/admin-api/README.md`, `deploy/README.md`
- Rename tests: `tests/test_claude_code_sessions.py` → `tests/test_generations.py` (update every importer)
- Modify tests: every test using `/api/claude-code/` URLs or importing the renamed modules

**Interfaces:**
- Produces: router `APIRouter(prefix='/generations')` in `generations_api.py` (mounted under `/api`), same endpoints:
  `POST /api/generations/context`, `POST /api/generations/sessions`, `GET /api/generations/sessions`,
  `GET /api/generations/sessions/{id}`, `POST …/messages`, `POST …/stop`, `POST …/document`, `POST …/close`,
  `GET …/events/history`, `GET …/events`.
- Produces: audit actions `generation.session.created`, `generation.session.stopped`,
  `generation.session.document_saved`, `generation.session.closed`; `created` details drop the `runtime` key (keep
  `ephemeral`, `model_id`, `prompt_id`).

- [ ] **Step 1: Failing test** in `tests/test_generations.py` (after the rename):
```python
def test_the_generation_api_moved(sessions):
    client = sessions[0]  # adapt to the fixture's shape
    assert client.get('/api/generations/sessions', headers=HEADERS).status_code == 200
    assert client.get('/api/claude-code/sessions', headers=HEADERS).status_code == 404
    assert client.post('/api/claude-code/context', headers=HEADERS, json={}).status_code == 404
```
  and change the existing create-session audit assertion to `generation.session.created` with
  `{'ephemeral': True, 'model_id': …, 'prompt_id': …}`. Run: expected FAIL.
- [ ] **Step 2: Rename and update.** `git mv` both modules; update imports (`api.py`, `agent_sessions.py`,
  `session_worker.py`, tests); prefix `/generations`; the four audit literals; docstrings that say Claude Code. Replace
  `/api/claude-code/` with `/api/generations/` in all tests and in `services/admin-api/README.md` and
  `deploy/README.md`.
- [ ] **Step 3: Verify** — `grep -rni "claude.code\|claude_code\|claudecode" services/admin-api/src services/admin-api/tests deploy services/admin-api/README.md`
  → no hits except migration-file names referenced in migration tests and the `claude_model` helper in
  `knowledge_llm` if still used natively (`model_adapter`). Full suite — only the known failures.
- [ ] **Step 4: Commit**
```bash
git add -A services/admin-api deploy/README.md
git commit -m "refactor(admin-api): Generate documents sessions live at /api/generations in generations_api and session_text"
```

---

### Task 5: The UI has no Claude Code

**Files (inventory `inv-ui.md` §1–§7):**
- Rename (`git mv`): `app/lib/claudeCode.ts` → `app/lib/generations.ts`; `app/components/ClaudeCodeSessionView.tsx` →
  `GenerationView.tsx`; `app/components/ClaudeCodeSessionsView.tsx` → `GenerationsView.tsx`; tests
  `claudeCodeSession.test.tsx` → `generation.test.tsx`, `claudeCodeSessions.test.tsx` → `generations.test.tsx`
- Modify: `lib/profiles.ts`, `lib/agentWizard.ts`, `lib/tools.ts`, `lib/useSessionEvents.ts`, `lib/testSessions.ts`,
  `lib/turns.ts`, `components/AgentWizard.tsx`, `AgentProfileSettings.tsx`, `AgentDetailView.tsx`,
  `AgentToolsView.tsx`, `AgentsView.tsx`, `AgentTestView.tsx`, `RunsView.tsx`, `RunWorkspace.tsx`,
  `DocumentGenerationForm.tsx`, `routes/documentationGeneration.tsx`, `routes/documentationGenerations.tsx`
- Modify tests listed in inventory §7

**Interfaces:**
- Consumes: Task 4's `/api/generations/...`; Task 2/3's responses without `runtime`, `claude_code_tools`, `cost_usd`.
- Produces: `lib/generations.ts` with every export of today's `claudeCode.ts` except `claudeModel`,
  `CATALOG_TOOLS_NEXT`, `claudeCodeNotes`, `sessionTypeLabel`; `SessionSummary` / `SessionDetail` / `SessionState`
  without `runtime`, `cost_usd`, `prompt_snapshot.claude_code_tools`. `sentNotice(working: boolean)`;
  `generateAgent(role, model_id)`. `RunWorkspace` keeps its `agentType` prop only if a non-generation caller passes a
  meaningful value; otherwise remove the prop and the header text it renders (check `grep -rn "agentType=" app`).

- [ ] **Step 1: Failing tests first.** Update `generations.test.tsx` and `generation.test.tsx` to import the renamed
  components, mock `/api/generations/...`, drop `runtime`/`cost_usd` from fixtures, and expect the model name without
  "· Claude Code"; `documentGenerationForm.test.tsx` mocks `/api/generations/context` and `/api/generations/sessions`;
  `agentWizard.test.tsx` expects step 1 to have exactly `['wizard-agent-model', 'wizard-resources']` as its first
  comboboxes, no "Agent type", `generateAgent` called with two arguments, the summary label "Model and resources" with
  text like `Claude · Fixed`, and always a Tools tab. Run: expected FAIL.
- [ ] **Step 2: Implement** per inventory §1–§6: rename files and imports; four API paths in `generations.ts`; delete
  Agent type, `chooseType`, `runtime` state and `claudeModel` filter in the wizard (Agent model defaults to the first
  catalog model; the review summary is `<model name or 'No model'> · <resources>` with aria-label
  `Model and resources`); General without Agent type and Claude Code tools, `fits(model)` one rule, May start
  subagents always shown; AgentDetailView without badge/notes/Test card (`view === 'test'` always renders the native
  test chat); AgentToolsView without the Claude Code card; AgentsView without the type in the filter/row (and without
  a type column if there is one); RunsView `KINDS` without `claude_code`; RunWorkspace always shows Model calls and
  "Waiting for the agent session worker. …"; `sentNotice` keeps the native texts.
- [ ] **Step 3: Fix remaining tests** (inventory §7): delete the Claude Code-only cases named there; drop `runtime`
  and `cost_usd` from fixtures; replace `/api/claude-code/` with `/api/generations/`; `agentTypes.test.tsx` becomes
  `agentGeneral.test.tsx` (`git mv`) with only native cases.
- [ ] **Step 4: Verify.** `npx tsc --noEmit` clean; `npx vitest run` all pass;
  `grep -rni "claude.code\|claudecode\|claude_code" services/admin-ui/app` → no hits.
- [ ] **Step 5: Commit**
```bash
git add -A services/admin-ui/app
git commit -m "feat(admin-ui): no Claude Code agents; Generate documents uses lib/generations and /api/generations"
```

---

### Task 6: Docs

**Files:** `docs/user-guide.md`, `docs/agents.md`, `docs/agent-profiles.md`, `docs/architecture.md`,
`docs/documentation.md`, `docs/prompts/api-documentation-workflow.md`, `tools/README.md` (if it mentions Claude Code
agents), root `README.md` / `CLAUDE.md` if they mention the worker.

- [ ] **Step 1:** `grep -rn -i "claude code\|claude-code\|claude_code\|agent_sessions_cc\|agent type" docs/*.md docs/prompts tools/README.md README.md CLAUDE.md`
  (skip `docs/superpowers/`).
- [ ] **Step 2:** Rewrite each hit: Claude Code is not a current feature anywhere; agent creation has Agent model and
  Resources; General has Model (no Agent type, no Claude Code tools); the sessions table is `ka.agent_sessions`; the
  API is `/api/generations/...`; the deploy has no claude-code-worker. Add one line to `docs/agent-profiles.md` (or the
  file that lists migrations/history) that migration 0083 converted Claude Code agents to native agents and deleted
  their sessions.
- [ ] **Step 3:** Re-run the grep → only intentional history lines remain (the 0083 note).
- [ ] **Step 4: Commit**
```bash
git add docs/user-guide.md docs/agents.md docs/agent-profiles.md docs/architecture.md docs/documentation.md docs/prompts/api-documentation-workflow.md
git commit -m "docs: Claude Code agents are gone; Generate documents API and sessions table renamed"
```
  (add any other file Step 1 found.)
