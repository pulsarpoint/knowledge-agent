# Admin CRUD and TODO Execution Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans after the user reviews this plan. Track the steps below. Do not implement during the planning request.

**Goal:** Keep configuration CRUD, tools and chat/test interfaces; replace agent execution with TODO responses.

**Architecture:** Existing React/shadcn UI → FastAPI → PostgreSQL. Agent execution handlers return a constant response. Use golang-migrate for schema changes. No new framework or service layer.

**Tech stack:** FastAPI, psycopg, PostgreSQL, golang-migrate, existing React/shadcn, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-11-admin-configuration-only-design.md`.

## Constraints

- Planning only; source code, dependencies, database and running containers remain untouched at this stage.
- Preserve tools, chat/test pages, route URLs, Git sync, Jira import/export, RAG processing/search and connection tests.
- Remove agent runners and orchestration, not the retained application/service directories wholesale.
- Keep existing data, encrypted secrets, prompt rules and resource relationships.
- No global parent agent, new agent types, factories, callback framework, replacement runtime, or cosmetic module reshuffle.
- Work in `/Users/graovic/fincore/knowledge-agent-v3`; preserve unrelated uncommitted changes.

## 1. Use golang-migrate

**Files:** `db/migrate.sh`, `db/migrations/`, `db/tests/run.sh`, `db/tests/schema.sql`, `deploy/compose.yaml`, admin test migration fixtures, `Makefile` and migration documentation.

- [x] Convert numbered SQL migrations to golang-migrate's `<version>_<name>.up.sql` naming, preserving their SQL order. Keep needed transaction boundaries explicit. Update test fixtures to execute only up migrations.
- [x] Replace the custom migration runner with the standard golang-migrate CLI/container, using a pinned version selected at implementation. No Go application code is needed.
- [x] Use a separate ledger name, `golang_schema_migrations`, to avoid colliding with the existing checksum table. Preserve the old ledger as adoption evidence.
- [x] For existing databases, compare every applied legacy version/checksum with the original SQL and verify a contiguous applied sequence. Only after that check, baseline golang-migrate to that version once; do not replay old SQL. A fresh database starts at version zero.
- [x] Supply reversible down SQL for new reversible changes. Do not invent destructive historical rollbacks or silently report a rollback that did nothing.
- [x] Test a fresh database, a verified populated database, a second no-change run, and a failed migration/dirty-version state using disposable PostgreSQL. Confirm the populated database retains its records.

Use the standard CLI directly, for example in the isolated test environment:

```sh
migrate -path db/migrations -database "$MIGRATION_TEST_URL" up
migrate -path db/migrations -database "$MIGRATION_TEST_URL" version
```

The PostgreSQL URL uses the chosen migration-table option. Keep URLs containing secrets out of logs. Migration naming and CLI behavior follow the [official migration format](https://github.com/golang-migrate/migrate/blob/master/MIGRATIONS.md) and [CLI documentation](https://github.com/golang-migrate/migrate/blob/master/cmd/migrate/README.md).

## 2. Keep CRUD independent of the runner

**Files:** `services/admin-api/src/knowledge_admin/{app,api,cli,operations_api,operations_store,profiles_api,prompts_api,llm_api,mcp_api}.py`; existing CRUD tests and forms.

- [x] Change `operations_api.build_api` to receive the existing store directly. Construct `OperationsStore(repository_store, app_name, agent_id)` where configuration requests need it; remove the dependency on `OperationsAgent.store`.
- [x] Remove runtime construction and its background-task lifespan from admin startup. Keep configuration, connection tests and normal integration clients registered.
- [x] Preserve existing configuration payloads, URLs and tables. Keep secrets write-only, omitted replacement secrets unchanged, stale writes rejected and prompts required.
- [x] Verify create/list/edit/disable/delete behavior for agent definitions and associated configuration. Add missing deletion actions with the existing shadcn confirmation pattern, without expanding this task into unrelated catalog features.
- [x] Deleting an agent removes its owned settings and selections, never shared resources. Preserve history: return a clear conflict when retained history blocks deletion. Keep active prompt protection for surviving agents. Any needed FK adjustment goes through a new golang-migrate migration.
- [x] Retain tests for prompt drafts/activation, many-to-many skills, per-agent SSH overrides, resource isolation, encryption and revision conflicts. Replace fixture construction that unnecessarily starts an agent with direct database/configuration setup.

Result: creating or editing configuration works without importing or constructing an agent runner.

## 3. Keep execution routes and return TODO

**Files:** admin `operations_api.py`, `agent_api.py`, `code_api.py`; `services/code-agent/src/code_agent/api.py`; `services/docs-wiki/src/docs_wiki/{api,search_api}.py`.

Retain the existing request models and access checks. Replace the execution body directly:

```python
from fastapi.responses import PlainTextResponse

# Inside each existing execution handler, after access/input validation:
return PlainTextResponse("TODO")
```

- [x] Apply the response to `/api/operations/runs` POST, run cancellation, admin code ask, admin search chat, reader chat/escalation, and code service `/repos/{name}/ask`.
- [x] Return HTTP 200, content type `text/plain`, body exactly `TODO`; do not return 202 or a fake run/session ID.
- [x] Keep history/detail GET endpoints as database reads. Keep configuration and connection-test endpoints functional. Status/context endpoints read configuration without constructing a model or runner; they must not block placeholder submission on runtime availability.
- [x] Do not decrypt execution credentials, contact a provider, proxy to another service, create a background task or insert a run record for a TODO response.
- [x] Add endpoint tests using existing fixtures, including this regression pattern:

```python
def test_run_returns_todo_without_creating_a_run(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    with store.connect() as conn:
        before = conn.execute("SELECT count(*) AS n FROM ka.operations_runs").fetchone()["n"]
    response = client.post(
        "/api/operations/runs", headers=HEADERS,
        json={"task": "Inspect the server"},
    )
    assert response.status_code == 200
    assert response.text == "TODO"
    assert response.headers["content-type"].startswith("text/plain")
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.operations_runs").fetchone()["n"] == before
```

Use `_client` from `tests/test_api.py` and `HEADERS` from `tests/test_repositories.py`. Cover every retained execution route, non-admin denial, invalid input, and no outbound execution calls.

## 4. Keep chat/test UI and remove the unused execution machinery

**Files:** admin `OperationsView.tsx`, `SearchAgentView.tsx`, `CodeSearchAgentView.tsx`, `app/lib/{operations,agents,codeAgent}.ts`; shared `packages/chat-ui/` and reader chat clients; backend runner modules and their import sites; dependency manifests and Dockerfiles.

- [x] Keep all existing tabs, forms, tool files, chat components and history display.
- [x] Update execution clients to read the plain-text response. Display TODO, end the loading state, and allow another submission. Remove the assumption that a new submission returns a run ID or SSE stream.
- [x] Do not poll run history/details after TODO. Existing historical rows remain readable; old running-status rows must not cause indefinite polling while execution is unimplemented.
- [x] Remove SDK runner functions, prompt assembly for execution, task orchestration, authorization callbacks, runtime model gateway and execution MCP proxy. Inspect mixed modules before deleting: preserve configuration helpers and types actually needed by retained tools.
- [x] Keep tool implementations in their folder, disconnected from agent routes. Do not finish the interrupted tool refactor or add a new dispatcher.
- [x] Remove dependencies, Docker build steps and startup hooks only if no retained tool/integration needs them. A library still required by a retained tool is not an unused runtime dependency.
- [x] Keep reader and code service APIs where their retained UI/URLs or source browsing need them; remove their runner initialization. Do not delete service directories or UI packages wholesale.
- [x] Keep tests for tools and CRUD. Replace obsolete agent-execution tests with TODO-response tests. Verify that each chat/test screen displays TODO once, leaves loading state, preserves its inputs and does not start polling.

## 5. Verify the change and update documentation

- [x] Run retained admin API tests with disposable PostgreSQL, both UI test/build suites, and Git/Jira/RAG/connection-test regressions using isolated fixtures.
- [x] Confirm fresh and adopted databases work through golang-migrate and no user records or credentials were lost.
- [x] Verify every execution URL returns TODO and cannot reach an SDK runner or invoke a tool; verify configuration requests still save/read the expected records.
- [x] Update README and relevant agent/migration docs to state that execution is intentionally unimplemented, while configuration and integration features work.
- [x] Build the changed services, validate Compose without printing secrets, then update the local environment after implementation. Preserve volumes and check for active work before any restart.

## Plan review

The corrected plan retains tools, chat/test UI, all execution URLs and the selected integration features. FastAPI stays; golang-migrate replaces the custom SQL migration runner. The user-visible execution result is exactly TODO. No new agent framework or project is introduced.

Implemented after user approval. The local environment is updated; see docs/runtime-verification.md for migration, data-preservation, API and browser verification.


## Final verification

- Admin API: 238 tests passed, plus the expanded six-test TODO/deletion suite.
- Reader API: 165 tests passed, plus the targeted workspace escalation check.
- Code API: 54 tests passed. RAG: 217 tests passed. Setup: 14 tests passed.
- Admin UI: 118 tests passed. Reader UI: 76 tests passed. Both production builds passed.
- Fresh/adopted migration tests passed. Live adoption preserved all 53 pre-existing table counts.
- Live SSO/integration smoke and Playwright TODO/retry/confirmation checks passed.
- Keycloak, Phoenix, database volumes and existing user records are retained.
