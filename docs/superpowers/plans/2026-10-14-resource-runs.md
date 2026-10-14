# Resource Chats, Proposals Wizard and Debug Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chats on servers, services and environments are ordinary runs tagged with the resource (many per resource,
`?chat=` in the URL), proposals come only from a Propose wizard and have their own page, and every chat has a Debug tab
showing exactly what the model receives.

**Architecture:** Migration 0106 adds `agent_sessions.tags`, turns `discovery` runs into `purpose='resource'` runs
tagged `servers/<id>`, makes `discovery_proposals.server_id` nullable and seeds three assistant prompts. `sessions/start`
accepts service/environment starts and request tags; proposal tools are on only when the request turns them on. A
debug route and the preview reuse `agents/runner.get_prompt` and the SDK tool list. The UI gets resource pages
(Info + Chats, chat hidden until asked, 2xl two columns, tabs below), a proposals page, the Propose wizard and a Debug
tab in `AgentChat`.

**Tech Stack:** PostgreSQL 17 migrations, Python 3.14 / FastAPI / psycopg 3 / pytest; React Router 7 / TypeScript /
Tailwind / vitest.

**Spec:** `docs/superpowers/specs/2026-10-14-resource-runs-design.md`.

## Global Constraints

- Work only in the worktree `.claude/worktrees/resource-runs` (branch `feature/resource-runs`). Never `make`, never
  touch `.env`/`.local`, never `git stash`, never touch the running Docker stack; pytest starts its own throwaway
  PostgreSQL (fixture IndexError at `tests/conftest.py:39` — rerun).
- UI checks need `ln -s ../../../../../services/admin-ui/node_modules services/admin-ui/node_modules` once (git-ignored;
  never commit it, never `pnpm install`).
- Format before committing: `uv run --frozen ruff format <files>` (services/admin-api), `pnpm format`
  (services/admin-ui). Commits end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`; never
  `git add` `.superpowers` or `node_modules`.
- Development version: no redirects for removed URLs, no compatibility shims for removed routes/fields; stack data is
  test data (migrations must still be correct up and down).
- Tags: resource tags `servers/<uuid>`, `services/<uuid>`, `environments/<uuid>`; wizard runs also `proposals`; a tag
  matches `^[a-z0-9_-]+(/[a-z0-9_-]+)*$`, ≤ 120 chars, ≤ 10 per run.
- Run purpose for chats on resources: `resource`.
- Copy (exact): buttons `Chat`, `New chat`, `Propose…`, `Start`; tabs `Info`, `Chat`, `Debug`; link `Proposals`;
  wizard steps `What`, `Options`, `Review`; Debug sections `Instructions`, `Prompt`, `Tools`, `Resources`, `Model`;
  Debug prompt source for wizard runs `Written in the Propose wizard`.
- Layout breakpoint: Tailwind `2xl` (≥ 1536 px) shows Info left and chat right; below it, tabs Info / Chat.
- Full suites: `cd services/admin-api && uv run --frozen pytest -q -p no:warnings` (known failures: code-graph tests in
  worktrees, test_deploy docs command, test_environments rollback; SSH real-server tests can error under load — rerun
  alone); `cd services/admin-ui && pnpm exec vitest run && pnpm exec tsc --noEmit && pnpm exec react-router routes &&
  pnpm format:check` (long UI tests can time out under load — rerun those files alone).

## Review Focus

1. A plain chat on a server must never get `propose_service` / `propose_document`, even if the agent's saved tools
   include them; a wizard run gets exactly the proposal tools it asked for.
2. Debug must show the instructions the runner actually sends: preview and debug must call the same code as the
   runner (no second copy of the prompt assembly).
3. `?chat=<id>` for a chat that belongs to another resource (or does not exist) must not open it on this page.
4. Proposals from a service or environment run, accepted and published, must be built for and attached to that
   service/environment — not to a server.
5. The migration's tag for old discovery runs must make them appear in their server's Chats list.
6. `get_resource` must refuse every resource the run was not given, including one linked from a given resource; the
   prompt must no longer carry server details (hostname, username, instructions).

---

### Task 1: Sessions — tags, resource purpose, service/environment starts, assistant prompts, gated proposal tools

**Files:**
- Create: `db/migrations/0106_resource_runs.up.sql`, `.down.sql`; `services/admin-api/tests/test_resource_runs.py`,
  `tests/test_resource_runs_migration.py`
- Modify: `services/admin-api/src/knowledge_admin/sessions/start.py` (started_from service/environment, tags, purpose
  `resource`, default prompt per resource type, readiness for service/environment, `chosen_resources`),
  `sessions/api.py` (`?tag=` filter replaces the server/discovery filter; tags in run JSON), `sessions/rows.py`,
  `tools/catalog.py` (proposal tools need an explicit request: replace the `started_from_server` need of
  `propose_service` with a run-only need satisfied only when the run request turned the tool on and the run started from
  a server; `propose_document` the same for any resource run), `capabilities/discovery.py` (resolve the run's resource
  from its tag/pinned start, allow service/environment runs for documents), everything that reads
  `purpose = 'discovery'` (grep) → `resource`
- Modify: older migration tests that roll back past 0106 (add its down first, as earlier migrations did)

**Interfaces:**
- Produces:
  - `ka.agent_sessions.tags text[] NOT NULL DEFAULT '{}'` (GIN index), purpose value `resource` (constraint updated;
    `discovery` removed), `ka.agent_sessions.service_id`? **No** — the resource is the tag plus the pinned
    `started_from` (`pinned['started_from'] = {'type': 'server'|'service'|'environment', 'id', 'name'}`), and
    `agent_sessions.server_id` stays set for server runs only.
  - `StartInput.started_from`: `{server_id}` | `{service_id}` | `{environment_id}` (plus the existing document forms);
    `StartInput.tags: list[str]` (validated as in Global Constraints); the run's tags = request tags + resource tag.
  - `GET /api/sessions?tag=<tag>` → runs having that tag (open first, then last activity desc).
  - Seeded prompts `Server assistant`, `Service assistant`, `Environment assistant` (`input_kind` server / service /
    environment; texts from the spec); ids `md5('knowledge-agent-v3:prompts:<type>-assistant:' || workspace)::uuid`
    like the other seeded prompts; seeded for existing workspaces and by trigger for new ones.
  - `discovery_proposals.server_id` nullable; data: `UPDATE agent_sessions SET purpose='resource', tags =
    ARRAY['servers/' || server_id] WHERE purpose='discovery'`.

- [ ] **Step 1: Failing tests** (`test_resource_runs.py`, `test_resource_runs_migration.py`): start from a service and
  from an environment → `purpose='resource'`, tag `services/<id>` / `environments/<id>`, pinned brought resources,
  `chosen_resources` = the resource, default prompt = that type's assistant prompt; start from a server → tag
  `servers/<id>`, `server_id` set; request tags added and validated (bad tag → 422, 11 tags → 422); `GET
  /api/sessions?tag=` returns exactly those runs; a plain server chat has no proposal tools even when the saved agent
  lists them (Review Focus 1); a request with `tools: ['propose_service','propose_document']` on a server run has both;
  `propose_service` requested on a service run → 400 naming the tool; migration: discovery rows become `resource` with
  the server tag (Review Focus 5), assistant prompts seeded, down restores `discovery` and refuses when an assistant
  prompt was edited.
- [ ] **Step 2: Run** them — expected failures.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new files, `tests/test_sessions*.py`, `tests/test_discovery*.py`, then the full backend suite.
- [ ] **Step 5: Commit** `feat(sessions): chats on servers, services and environments are tagged resource runs; proposal tools only on request`

---

### Task 2: Proposals across runs, Debug route and preview instructions

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/servers/discovery.py` (new `GET /api/discovery/proposals?tag=&status=`;
  accept for service/environment runs builds the document for that service/environment and attaches on publish),
  `documents/store.py` (publish attach for service/environment proposals if needed), `sessions/api.py` (`GET
  /api/sessions/{id}/debug`; preview gains `instructions` and `tools`), `agents/runner.py` (expose the pieces the debug
  route needs without duplicating `get_prompt`)
- Test: `tests/test_resource_proposals.py`, `tests/test_session_debug.py`

**Interfaces:**
- Consumes: Task 1 (tags, `pinned['started_from']`, proposal tool gating).
- Produces:
  - `GET /api/discovery/proposals?tag=<resource tag>&status=pending|accepted|rejected` → `{runs: [{session_id, title,
    started_at, started_by, proposals: [...same proposal JSON as today...]}]}` newest run first.
  - `GET /api/sessions/{id}/debug` → `{instructions: str, prompt: {id, name, version} | null, prompt_source: 'library'
    | 'wizard', agent_instructions: str, skills: [{name}], tools: [{name, description}], resources: {...pinned
    resource lists, names only...}, model: {id, name}}`; `POST /api/sessions/preview` adds `instructions` and `tools`
    (same builders).
  - Builders: `instructions` = `agents.runner.get_prompt(...)` with the pinned configuration exactly as the runner
    calls it; `tools` = the SDK tool objects the runner would build for that configuration (name + description). No
    secrets (use run-record redaction on the text).

- [ ] **Step 1: Failing tests:** proposals list across two runs of one server, grouped and filtered by status;
  service-run document proposal accepted → draft built for the service; published → attached to the service (Review
  Focus 4); same for an environment; debug `instructions` equals what the runner computes for the same pinned
  configuration (build via the runner's own call path, compare strings) (Review Focus 2); preview returns the same
  for a not-yet-started request; a configuration with an SSH key or model key never shows the secret.
- [ ] **Step 2: Run** them — expected failures.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new files, discovery and sessions tests, then the full backend suite.
- [ ] **Step 5: Commit** `feat(sessions): proposals across a resource's runs; Debug route and preview show the exact instructions and tools`

---

### Task 3: Resource context — one prompt line, `get_resource`, fixed tool schemas

Spec section "Resource context: one line and `get_resource`". Done before the UI so the assistant prompts, the
Propose wizard's tool list and the Debug tab show the final tools.

**Files:**
- Modify: `services/admin-api/src/knowledge_admin/capabilities/inventory.py` (one `get_resource(context, resource_type, resource_id)`
  built from the existing `_server_*`/`_service_*`/`_project_info` queries plus an environment query; a malformed
  uuid is refused; access through the existing allowlist checks `_given`/`_first_given`, by id instead of name;
  remove the six old functions), `tools/catalog.py` (one `get_resource` ToolSpec in group `inventory`, needs `None`;
  remove the six specs, `InventoryHost`/`InventoryName`, their usage texts; `ssh_run` unchanged), `agents/runner.py` `get_prompt` (the `Resource:` line from
  `pinned['started_from']`; servers and services as index entries), `tools/sdk.py` (drop `choices`, the enum and the
  description list; `function_tool(spec, usage)`), `capabilities/document_tools.py` (refusal names the editable
  documents), `sessions/start.py` (any default tool lists naming the old tools), `tools/README.md`, `docs/agents.md`,
  `docs/user-guide.md`
- Modify: `db/migrations/0106_resource_runs.up.sql`/`.down.sql` (not merged yet — edit in place): assistant prompt
  texts use `get_resource`; a new version of the seeded Server discovery prompt (first step `get_resource`; down
  restores the previous version when unedited); `ka.agent_tools`: delete rows of the six tools, insert
  `get_resource` `on` for agents that had any of them on (down: the reverse — `get_server_info_basic` on for agents
  with `get_resource` on)
- Test: new `tests/test_get_resource.py`; update `test_inventory_tools.py` (fold into the new file or delete),
  `test_discovery_tools.py`, `test_resource_runs.py`, `test_session_debug.py`, `test_scoped_reads.py`, the sessions
  tests and migration tests that name the old tools; UI fixtures `agentOptionsFixture.ts`, `agentSettings.test.ts`,
  `agentPane.test.tsx` (tool names only)

**Interfaces:**
- Consumes: Task 1 (`pinned['started_from']`, tags), Task 2 (`instructions_and_tools`, Debug builders — unchanged, they
  pick up the new prompt and tools).
- Produces:
  - `get_resource(resource_type: Literal['server', 'service', 'environment', 'code_project'], resource_id: str)` →
    JSON per the spec; index and link entries `{type, id, name, ...}`; refusal text
    `The <type> <id> is not available in this run.`
  - `function_tool(spec, usage)` (no `choices`).

- [ ] **Step 1: Failing tests:** `get_resource` for a server, a service, an environment and a code project returns
  the spec's fields and links; every link's `type` and `id` are accepted by `get_resource` when the run was given it; a
  resource not given to the run, a linked resource outside the allowlist and a bad uuid are refused with the exact
  text, a type outside the enum fails schema validation; a server run's instructions contain `Resource: server <id> (<name>)` and the index entry, and no
  hostname, username or server instructions (Review Focus 6); `ssh_run` with the index entry's `id` runs; the catalog has no old inventory tools; migration up replaces the rows (agent with `get_server_docs` on →
  `get_resource` on), down reverses; the assistant and Server discovery prompts name `get_resource`; two runs with
  different editable documents get identical tool schemas; an edit tool given a non-editable id names the editable
  ones.
- [ ] **Step 2: Run** them — expected failures.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new and updated files, then the full backend suite and the UI checks (fixtures).
- [ ] **Step 5: Commit** `feat(inventory): one get_resource tool; resources named in the prompt, details on demand; fixed tool schemas`

---

### Task 4: UI — resource pages, chats by tag, `?chat=`, layout; remove the discovery route

**Files:**
- Modify: `services/admin-ui/app/routes.ts` (remove `servers/:serverId/discovery/:runId`; add `services/:serviceId`,
  `environments/:environmentId`, and `servers|services|environments/:id/proposals` (page built in Task 4)),
  `features/servers/ServerPage.tsx` (Info + Chats + chat panel), new `features/servers/ServicePage.tsx`,
  `features/servers/EnvironmentPage.tsx`, a shared `features/sessions/ResourceChats.tsx` (Chats list + New chat +
  `?chat=` handling + chat panel visibility + 2xl/tab layout), `features/sessions/api.ts` (`started_from` service/
  environment, `tags`, `getSessions({tag})`), `ServicesView.tsx` / `EnvironmentsView.tsx` (names link to their pages);
  delete `routes/discoveryRun.tsx` and remove inline proposals from the run view
- Test: `app/components/__tests__/resourceChats.test.tsx` (new), `serverPage.test.tsx`, `discoveryRun.test.tsx`
  (delete or fold into the new tests)

**Interfaces:**
- Consumes: Task 1 API (`started_from`, `tags`, `GET /api/sessions?tag=`).
- Produces: `ResourceChats` props `{ resource: {type: 'server'|'service'|'environment', id, name}, info: ReactNode,
  actions?: ReactNode }` (Task 5 passes the Propose button and Proposals link through `actions`).

- [ ] **Step 1: Failing UI tests:** each resource page shows Info and the Chats list (`GET /api/sessions?tag=…`),
  no chat panel until `Chat` / `New chat` / a list item is clicked; clicking sets `?chat=<id>`; loading the page with
  `?chat=<id>` opens it; a `?chat=` of another resource's run (tags don't include this resource) shows "This chat
  belongs to another page." and no chat (Review Focus 3); at 2xl the Info and chat are side by side (assert the layout
  class), below it tabs `Info` / `Chat`; New chat starts with `started_from` of the resource; the services and
  environments lists link to their pages; the old discovery route no longer exists.
- [ ] **Step 2: Run** them — expected failures.
- [ ] **Step 3: Implement**, update the existing server-page tests.
- [ ] **Step 4: Run** all UI checks.
- [ ] **Step 5: Commit** `feat(admin-ui): resource pages with chats by tag; chat opens with ?chat=; Info/Chat layout`

---

### Task 5: UI — proposals page, Propose wizard, Debug tab

**Files:**
- Create: `features/sessions/ProposeWizard.tsx`, `features/servers/ProposalsPage.tsx` (used for all three resource
  types), `features/sessions/DebugPanel.tsx`; routes for `…/:id/proposals`
- Modify: `features/sessions/AgentChat.tsx` (tabs conversation / `Debug`), `features/sessions/api.ts`
  (`getSessionDebug`, preview `instructions`/`tools`, `getResourceProposals`), the resource pages (pass `Propose…` and
  `Proposals` through `ResourceChats` `actions`)
- Test: `app/components/__tests__/proposeWizard.test.tsx`, `proposalsPage.test.tsx`, `debugPanel.test.tsx` (new)

**Interfaces:**
- Consumes: Task 2 API (`GET /api/discovery/proposals?tag=`, `GET /api/sessions/{id}/debug`, preview `instructions`/
  `tools`), Task 3 tools (`get_resource`), Task 4 `ResourceChats`.

- [ ] **Step 1: Failing UI tests:**
  - Wizard: server offers `Services on this server` and `Documents`, service/environment only `Documents`; Options per
    the spec (update existing / find new services; overview document; one per service; aspects checkboxes all on;
    extra instructions); Review shows the generated prompt (editable textarea) and the tools that will be on
    (`propose_service` only when services chosen, `propose_document` only when documents chosen); `Start` posts
    `POST /api/sessions` with `started_from` the resource, the prompt text as system prompt, `tools` = the proposal
    tools chosen, `tags` = [`proposals`], and opens `?chat=<new id>`.
  - Proposals page: runs grouped newest first with their proposals, status filter, Accept/Reject call the existing
    routes, accepted documents link to their draft / document.
  - Debug tab: before the first message it calls preview and shows `Instructions` (with Copy), `Prompt` (name, version,
    link to Prompts; `Written in the Propose wizard` for wizard runs), `Tools` (names + descriptions), `Resources`,
    `Model`; for an existing run it calls `GET /api/sessions/{id}/debug`.
- [ ] **Step 2: Run** them — expected failures.
- [ ] **Step 3: Implement.** The wizard builds the prompt from fixed text blocks (the Server discovery prompt's steps,
  one block per chosen option, then the extra instructions) in a pure function `buildProposalPrompt(resourceType,
  options): string` with its own unit tests.
- [ ] **Step 4: Run** all UI checks.
- [ ] **Step 5: Commit** `feat(admin-ui): Propose wizard, proposals page per resource, Debug tab in every chat`
