# Standalone runtime verification

## 2026-09-12: specialist migration to Agents SDK

- Branch: `codex/migrate-to-agents-sdk`.
- `knowledge_admin.agents.agent()` now creates an Agents SDK `Agent` and runs
  `Runner.run_streamed()`. It returns native `RunResultStreaming` results; the
  Test API, board, wizard, and tuning reviewer consume the new result contract.
- File tools and SSH are registered directly from `knowledge_admin.tools`.
  Selected HTTP MCP servers use native SDK connections with qualified names.
  SSH keys remain in backend memory. Local shell is not registered in the
  administration process; it requires an isolated worker.
- Coordinator and specialists share direct model connections for Responses,
  Chat Completions, Anthropic, and Bedrock. Removed `openai-codex`, its bundled
  CLI dependency, and the temporary Responses proxy.
- Successful sessions persist private `history.json` files including tool calls
  and results. Existing conversations without SDK history resume from their
  stored user/assistant messages. Close/expiry and version pinning are preserved.
- Validation: 78 focused tests passed with real disposable PostgreSQL and SSH,
  protocol fixtures, coordinator delegation, cancellation, history, structured
  suggestions, MCP name collisions, and Phoenix parent/child spans. Admin UI
  TypeScript check passed. Existing upstream deprecation warnings remain.
- Rebuilt and restarted only the local admin API after verifying no active test
  or board runs. Health returned 200; `openai_codex` is absent from its environment.
- Live smoke with saved Bedrock model `eu.anthropic.claude-opus-5`: actual file
  tool call, streamed activity, follow-up recall, and validated Pydantic structured
  output all passed. Temporary fixture/session files were removed; saved agents,
  model credentials, and user conversations were not modified by the smoke test.


## 2026-09-11 — Shared conversation UI and optional response feedback

- Replaced the Test page's per-turn cards and permanent tuning form with a
  conversation timeline and bottom composer. Enter sends, Shift+Enter inserts a
  newline, and the composer offers Stop during execution. History, configuration
  and saved suggestions open in side panels. Each finished response has an
  optional Add feedback action.
- Added `packages/chat-ui/Conversation.tsx`, used by the Test page and the board's
  coordinator conversation. It accepts messages and callbacks without knowing
  agent/session APIs. The existing reader can adopt this export independently;
  its older ChatPanel integration is retained. Official shadcn Message, Bubble
  and MessageScroller components supply the message layout and scroll behavior.
- Feedback can carry a response ID. The API validates that it belongs to the
  selected session, includes it in the reviewer's full conversation context,
  and retains the association in the existing review JSON. No migration or
  automatic prompt/configuration update is needed.
- Focused UI tests passed (51); the final layout/tab checks passed (15), and
  session/review API checks passed (4). Production TypeScript and both admin
  container builds passed. The shared-package reader lockfile was refreshed.
- Browser verification restored the existing session
  `59baf0ae-0fd4-4d10-867a-99352287d2a3`, checked the long-response layout and
  bottom composer, opened response feedback and history, and returned to that
  same session. No new agent/SSH task or tuning review was submitted. Both admin
  services are healthy.

## 2026-09-11 — Persistent Test conversations and tuning reviews

- Applied golang-migrate version 0033 after a PostgreSQL backup in
  `.local/backups/agent-sessions-20260911-XXXXXX.dump`. The migration ledger is
  clean at version 33. Existing run records and agent settings are preserved.
- Direct Test messages now share a persistent native Codex thread and workspace.
  Browser reload/disconnect does not stop a turn; `session_id` restores it.
  Explicit closure or one hour of inactivity removes runtime files, while the
  PostgreSQL transcript and saved reviews remain. Private SSH key files are
  removed after each turn, including while a session stays open.
- SDK/direct execution checks passed (21); session/direct/operations integration
  checks passed (17); focused UI checks passed (50). Tests cover new SDK process
  resume, disconnect/reconnect, scope isolation, concurrent-message rejection,
  close/cancel, idle expiry, whole-conversation review and no automatic settings
  changes. Migration rollback/reapply, adoption and concurrency checks passed.
  Production admin API/UI Docker builds passed and both services are healthy.
- Browser QA used a temporary agent with all resource capabilities disabled and
  the saved native Bedrock model. Session
  `4e18f58f-056e-4998-9b6a-341224f1f619` remembered “Amber Fox” in a follow-up
  after reload. The tuning reviewer received both turns and full configuration,
  generated concrete prompt/instruction/memory-note proposals, and retained its
  feedback and suggestions after another reload. The current configuration was
  byte-for-byte equivalent as JSON to the initial snapshot afterward.
- Closed that session with the confirmation dialog and verified its runtime
  directory was removed. A new session,
  `e8de0dbb-85b7-42ff-a5a1-fa1237cb4906`, answered “Not provided.” to the same
  fact question, confirming fresh context. Removed only the temporary QA agent,
  its three run records, two sessions, and one review after verification.
  Existing agent history, including the previous SSH verification, remains.

## 2026-09-11 — New Agent Wizard

- Added role-based design through the existing official Codex SDK function,
  followed by an editable proposal and atomic creation using existing tables.
  The generator receives a schema and metadata, without private keys or tokens;
  no specialist resources or execution capabilities are enabled for it.
- Verified all resource joins, first active prompt creation, model assignments,
  server defaults and overrides, shared credential preservation, invalid/stale
  selections, malformed provider output, bounded correction, and administrator
  access. The final wizard suite passed 17 tests; the related profile/config/SSH
  regression run passed 21 additional tests. All 16 focused UI tests passed.
- Production TypeScript and container builds passed. Browser QA caught and fixed
  a PostgreSQL domain-array serialization issue in folder paths. Native provider
  argument wrappers and extra annotations are normalized before validating the
  proposal. The admin OAuth proxy timeout is now 300 seconds, covering the
  generator's total 240-second deadline.
- Live browser verification used the saved Bedrock model and only proposed the
  public Hello-World project, with SSH, MCP, documents, skills, and services off.
  Reviewed the prompt and selected project, edited the generated instructions,
  and created the temporary agent. The normal editor and config API confirmed the
  model, edited instructions, active prompt v1, and absolute mounted code path.
  Removed the temporary agent afterward. Existing agents remain unchanged;
  no migration was required. Both admin services are healthy.

## 2026-09-11 — Permanent coordinator availability and visible limits

- Removed the coordinator enable switch and added a PostgreSQL constraint plus
  an API validation error preventing disablement. Migration 32 also re-enables
  any previously disabled coordinator. Specialists keep their enable switches.
- General now shows maximum model turns, maximum subagent calls per task, and
  timeout above the notes field. The call limit counts all delegations, including
  follow-ups. Existing saved limits were preserved at 8 turns, 20 calls, and 1800
  seconds in the local environment.
- Passed 15 backend and 13 UI tests. Migration tests verified upgrading a disabled
  coordinator, downgrade/reapply, adoption, and transactional failure handling.
  Production TypeScript/container builds passed. Browser and deployed API checks
  confirmed the removed switch, editable limits, and rejected disable requests.
- Backup: `.local/backups/coordinator-enabled-20260911-190748/postgres.sql` (0600).
  Applied migration 32; both admin services are healthy.

## 2026-09-11 — Coordinator in the agent catalog

- Migration 31 creates one protected `ka.agents.is_coordinator` record and moves
  the existing model and prompt into the normal agent and prompt-version tables.
  Coordinator notes and limits remain in the linked settings row. PostgreSQL
  blocks deleting the coordinator or changing its flag; the API also rejects
  removal and selecting it as a subagent.
- Saved backup `.local/backups/coordinator-agent-20260911-184855/postgres.sql`
  with mode 0600. Compared the model ID, active prompt hash, execution limits,
  and every specialist record before and after migration: all preserved.
- Focused backend checks: 28 passed. The full API run passed 298 of 300 tests;
  the two failures were old assertions that expected three seeded agents. After
  updating those assertions for the coordinator, all six tests in that file
  passed. UI: 23 tests passed for agent details, protected deletion, notes,
  prompt tabs, model selection, board navigation, and subagent filtering.
- Real SDK fixture tests verified that activated prompt versions and saved notes
  reach the coordinator, drafts do not apply before activation, queued tasks keep
  their snapshots, and disabling the coordinator blocks new submissions.
  Production TypeScript/container builds and fresh/rollback/reapply/adoption
  migration checks passed.
- Browser verification showed the coordinator first in the list with its badge
  and no remove action. General exposes identity, model, notes, and limits;
  Prompts shows the original active version and draft workflow. A temporary note
  was saved, verified after reload, then cleared. No test prompt versions remain.
  Both admin services are healthy and migration 31 is clean.

## 2026-09-11 — Dedicated Agents SDK coordinator

- Added `knowledge_admin/coordinator.py` with explicit inputs, no database access,
  and one `run_subagent` function tool. The board owns persistence and delegates
  specialist execution to the existing official Codex SDK function.
- The board's coordinator has independent model, prompt, and limit settings.
  Migration 30 adds `ka.coordinator_settings` and permits null agent IDs for
  coordinator tasks while requiring specialist IDs for subtasks. Backup:
  `.local/backups/coordinator-20260911-174906/postgres.sql` (mode 0600).
- All 36 focused backend tests passed, covering actual SDK delegation, follow-up
  tasks, single-specialist execution, clarification, selection/subtask/turn limits,
  revisions, cancellation, restart recovery, activity, adapters, and redaction.
  All six board UI tests passed, including settings editing and required prompts.
  Production TypeScript and container builds passed. Disposable PostgreSQL checks
  passed fresh migration, rollback to 29, reapply, concurrency, failure rollback,
  and legacy migration adoption.
- Live verification used the saved native Bedrock model with Agents SDK
  `LitellmModel` for the coordinator and the existing Responses adapter for one
  Codex specialist. The temporary specialist had SSH/MCP disabled and only the
  public Hello-World code folder selected. It read README using a command and the
  coordinator returned `Hello World!` and the requested verification marker.
- Browser QA verified the coordinator's streamed reply, specialist work panel,
  command output with exit code 0, and independent coordinator settings. Phoenix
  trace `d5bc02fe48f81548db305319aedefc18` contains seven successful spans with the
  hierarchy coordinator → delegation → specialist → Codex SDK → commands.
  Saved credentials were absent from the task/activity and trace records.
- Removed the temporary specialist and its two QA task rows; audit records and
  the Phoenix trace remain. Both admin services are healthy and PostgreSQL is at
  migration 30 with a clean ledger. Existing agent configurations were unchanged.

## 2026-09-11 — Live board activity and Phoenix tracing

- Applied reversible golang-migrate migration 29 (trace/span IDs on board tasks).
  Database backup: `.local/backups/agent-board-tracing-20260911-162833/postgres.sql`.
- Backend: 35 focused SDK, adapter, orchestration, streaming and redaction tests
  passed across the final targeted runs. UI: 5 board tests; production TypeScript
  build passed. Fresh migration, rollback to 28, reapply and adoption checks passed.
- Ran the official Codex SDK with the saved Bedrock model through the existing
  adapter. Three temporary agents used one coordinator and two subagents. Only
  the reader had access to the public Hello-World folder; SSH/MCP were disabled
  for all three. The coordinator delegated both subtasks in its first decision.
- Main task `f34cafa3-35a4-499f-b756-93cf4fc0fae7` started at `2026-09-11T14:30:47.667311+00:00` and
  completed at `2026-09-11T14:31:08.320329+00:00`. The browser displayed live updates and
  the subagent work sheet showed `Hello World!` with command exit code 0. The
  final response combined `TRACE_READ_OK`, `TRACE_SUMMARY_OK`, and file content.
- Phoenix persisted trace `50f31d721890a239025a304e32285987` with 9 spans: 3 agent task spans,
  4 SDK turns, and 2 command spans. Verified parent/child relationships, command
  output, and absence of the saved model credential in activity and trace data.
  Phoenix's span redirect opened its login page as expected in the signed-out
  browser. New traces appear after the first completed span is exported.
- Removed only the three temporary agents and three board test rows. Existing
  agent/model settings were preserved. Audit records and the Phoenix QA trace
  remain for verification.
- Live SSE resumes new activity after reconnect; disconnected intervals are
  inspected in Phoenix. No duplicate durable event-history table was added.


## 2026-09-11: one main task with subagents

The board now starts one parent task with a chosen coordinator and selected
subagents. It persists delegated assignments and each agent's result, supplies
prior results for follow-up work, and displays the coordinator's combined answer.
Each participant uses its own saved Codex SDK configuration. The application owns
delegation; SDK-native collaboration tools are disabled.

Validation passed: 33 SDK/configuration/board checks, 34 board/API checks after
credential-redaction changes, 21 board/adapter checks including malformed-plan
correction, and 12 adapter checks after complete structured-response conversion.
Six UI checks, production builds, and the golang-migrate integration suite passed.
Coverage includes actual SDK delegation and follow-up calls against local models,
team validation, credential separation, partial failures, parent cancellation,
three concurrent SDK calls, dispatcher election, and restart recovery.

Migration 28 was applied after a backup at
`.local/backups/agent-board-teams-20260911-154343/postgres.sql`. Initial native
Bedrock runs returned malformed structured decisions or damaged agent IDs and
were rejected before delegation. The adapter now converts complete structured
responses into SDK events, avoiding partial synthetic-tool argument conversion;
ordinary text/tool calls retain their existing streaming behavior. Invalid JSON
gets one format-correction attempt within the overall decision limit.

The final browser-submitted Bedrock run completed from 13:54:11 to 13:54:28 UTC.
The coordinator made two calls: delegation, then synthesis. Both subagents
started at 13:54:18 UTC and finished at 13:54:22 UTC, returning TEAM_ALPHA_42 and
TEAM_BETA_42. Both results and the combined answer appeared in the board, and the
individual result sheet showed its model and active prompt version. All three
QA agents had every resource capability disabled. The real code agent's saved
model assignment was unchanged. Temporary QA agents and task rows were removed
after verification; append-only audit history remains.

## 2026-09-11: multi-agent board

`/agent-board` is available under the Agents menu. Separate tasks run through the
existing Codex SDK function and publish their own persisted results. The board
shows queued/running tasks, completed results and individual failures, with Stop,
Markdown result details and history pagination. Existing Test/chat routes remain
`TODO`.

Twenty-six focused API/configuration tests and six UI tests passed. The new board
suite includes real SDK calls against an isolated Responses API, per-agent model
credentials, independent failures, cancellation, a three-run concurrency limit,
single-dispatcher election, restart recovery and preserved history. The seven
board API tests were rerun after the result-persistence fallback change and passed.
Production UI builds and the golang-migrate integration checks passed, including
fresh migration, down/up, legacy adoption and dirty migration handling.

Migration 27 was applied cleanly after saving
`.local/backups/agent-board-20260911-151633/postgres.sql`. Admin API and web images
were rebuilt and deployed healthy. Two temporary agents with all resource
capabilities disabled used the existing Bedrock model and started concurrently
at 13:19:27 UTC. They returned `BOARD_ALPHA_OK` and `BOARD_BETA_OK` at 13:19:30 UTC.
Both results appeared automatically in the authenticated browser, and the full
result panel showed the saved model and prompt version. Temporary tasks and agent
definitions were removed after verification; existing configurations were kept.

## 2026-09-11: model API adapter

The callable Codex SDK agent now supports the saved native Bedrock connection,
native Anthropic connections and OpenAI Chat Completions connections through a
temporary authenticated Responses endpoint. Responses-compatible models still
connect directly. LiteLLM handles provider conversion; each run fixes its model,
endpoint and credentials and removes the adapter when it ends.

Sixty-three focused API/configuration/SDK/adapter tests and nine deployment tests
passed. The SDK and adapter suite was rerun after adding the command sandbox
regression: all 22 tests passed. These cover real SDK tool calls, fragmented
streamed arguments, tool-result matching, credential isolation, failure handling
and cancellation against local model fixtures.

The admin API image was rebuilt and deployed healthy. Live verification used the
saved Claude/Bedrock model, including a command-tool round trip inside Docker
reading `/work/code/Hello-World (public test)/README`. The sandbox explicitly grants
read access to the SDK's bundled runtime package so its command tool can start.
The saved model assignment was unchanged; no SSH server was contacted.

Execution HTTP endpoints still return `TODO`; these checks call `agent()` directly.

## 2026-09-11: callable Codex SDK agent

`knowledge_admin.agents.agent(config, task, credentials=...)` uses the official
`openai-codex==0.154.0` Python SDK directly. It starts an ephemeral thread with
the active system prompt, enabled resource context, native HTTP MCP connections
and selected SSH connection instructions/key files, then returns `TurnResult`.
The runtime does not query PostgreSQL. Temporary files are removed after calls.

Thirty-eight focused API/configuration/SDK tests and nine deployment tests passed.
The SDK tests use the actual pinned runtime against an isolated Responses API and
MCP server, verifying model/task/instructions, disabled selections, MCP bearer
authentication, secret exclusion and cleanup. The admin image was rebuilt and
deployed healthy. A container test completed a real SDK thread against a local
fixture with the saved mounted folder paths; no external model or SSH host was
contacted and the saved model assignment was unchanged.

HTTP execution endpoints remain `TODO`. The callable entry point currently
requires a Responses-compatible model; the saved native Claude/Bedrock model
requires an adapter and is explicitly rejected rather than substituted.

## 2026-09-11: absolute agent paths

The generated configuration uses absolute code paths under `CODE_ROOT` and
document paths under `RAG_SOURCE_ROOT`. Document entries retain their dotted
search identifier in `search_folder`. Service checkout paths use the code mount;
remote deployment log paths stay unchanged. Database paths remain relative, and
credential references remain references without creating key files.

Twenty-one PostgreSQL/API tests and nine deployment tests passed. The admin API
image was rebuilt and deployed; `admin-api` and `code-agent` are healthy with
matching read-only code and RAG mounts. Live Show config displayed absolute paths
for both selected code projects and all four selected document folders. Those
same paths were verified as readable inside `code-agent`. Agent execution remains
`TODO`; the future SDK runner must use these same mount destinations.

## 2026-09-11: code project configuration output

`get_agent_config()` now emits `code: {enabled, all_projects, projects}` in place
of the old `git.repositories` section. Projects expose ID, name, description and
mapped folder paths. Only Git-backed projects have `remote_git_url`. Sync status,
checkout/clone paths, requested refs, commits and selection markers are omitted
from these entries. Administration records and selection controls are unchanged.
Fourteen focused PostgreSQL/API tests passed, including mixed local/Git projects,
explicit/all selections, archived resources, credentials and read-only previews.
The admin API image was built and deployed. Live Show config verified both selected
projects: the local project has folder paths only, and the Git project includes
`remote_git_url`, with no old clone/sync fields in either entry.

## 2026-09-11: code project folder selection tree

Add/Edit code project now has an expandable checkbox tree inside the form.
Projects can select multiple folders at any depth under CODE_ROOT. Collapsing
branches retains selections; editing expands selected-folder ancestors. The
existing folder mappings and schema are reused. The file-list endpoint accepts
`folders_only=true` and filters directories before its 200-entry pagination.

Seven UI tests and seven PostgreSQL/API tests passed, covering nested and sibling
selections, editing/removal, selected-path config output, folder pagination and
mount boundaries. Production UI/Docker builds passed. Live browser checks used
temporary empty folders to verify expansion, selection and collapse/reopen; the
form was cancelled and the folders removed without saving a test project.
Screenshot: `output/playwright/code-folder-tree.png`.

## 2026-09-11: project names for code folders

The existing public example was moved from the legacy workspace/repository/job
UUID layout to `/work/code/Hello-World (public test)/`. Its repository ID,
settings and commit were retained; `clone_path`, `checkout_path` and folder
mappings now use the named folder. Byte hashes of every checkout file matched
before/after. The old cache/layout and full database/code backup are outside the
code mount at `.local/backups/named-code-folders-20260911-123215/`.

The Add project form now prefills the clone folder from the project name and
keeps custom folder names when the project name changes. Existing projects keep
their saved paths when renamed. The five code-project UI tests and production
Docker build passed. The updated UI is deployed.

## 2026-09-11: code projects

- Replaced GitHub Projects with Code projects: browse the mounted code root,
  preview text, map multiple existing folders, or configure an HTTPS Git clone
  with a chosen folder and optional periodic pulls.
- Applied golang-migrate version 26 cleanly. Existing repository, credential,
  agent selection and service link counts were preserved. The existing checkout
  was backfilled into the new folder mapping table.
- Database backup: `.local/backups/code-projects-20260911-120257/`.
- Admin API: 254 tests passed against disposable PostgreSQL. This includes
  migration preservation, multiple folder mappings, credential handling, stale
  revisions, folder traversal and real Git clone/scheduled-pull tests. Dirty
  working files stop pulls and are preserved.
- Source browser: 55 tests passed, including local multi-folder projects and
  excluding unselected folders. Affected admin UI suites: 27 tests passed.
- Production UI and Docker images built. Admin API/web and source service are
  healthy; the repository worker is running.
- Authenticated browser checks passed for nested folder navigation, README
  preview, clone choice, pull interval, cancellation, config folder mappings and
  redirecting the old GitHub Projects URL. No test project was saved in the live
  database. Screenshots are under `output/playwright/code-projects*.png`.
- Agent execution remains `TODO`.

## 2026-09-11: configuration administration and TODO execution

The local environment now runs the configuration-only agent implementation.
FastAPI serves CRUD and integration actions. Chat/Test execution URLs return
HTTP 200 text/plain `TODO`, without SDK runners, tool calls, new runs or sessions.
Earlier agent-execution results below describe the previous implementation.

- golang-migrate verified and adopted all 24 legacy checksums, applied version 25,
  and reported no change on a second run. All 53 existing table counts were preserved.
- A PostgreSQL backup is saved under `.local/backups/admin-todo-20260911-102859/`.
- Backend tests cover configuration, credentials, prompts, shared-resource deletion,
  TODO responses, repository browsing, Jira, SSH/MCP/model connection tests and RAG.
- Live SSO checks passed for both applications. RAG full-text search, document
  browsing, source inventory, database version and Phoenix health passed.
- Browser checks passed for all three default agents and reader chat: TODO appears
  once, inputs remain, retries work, and no new session URL or run polling begins.
- Agent deletion uses a shadcn confirmation; cancellation keeps the agent. Database
  tests verify owned settings/keys are cleaned while shared resources remain.


Verified locally on 2026-09-08 after the selective port from v2 commit
`2f0507fd52774b3cc163907962761c410e7c5874`.

| Check | Result |
| --- | --- |
| Frozen Python dependency installation | Passed for all four services |
| Reader/API regression suite | 257 passed, including removed admin endpoint checks |
| Standalone admin API suite | 126 passed, including group enforcement, reader-route exclusion, PostgreSQL server/service/Jira/credential/job tests, real Git checkouts, and Jira HTTP error/pagination tests |
| RAG regression suite | 275 passed |
| Code-agent regression suite | 157 passed |
| Reader React UI suite | 96 passed across 13 files |
| Admin React UI suite | 31 passed across 7 files, including repository credentials/pulls, service links, server edits/search, Jira settings, connection checks, ticket limits and error states |
| Local setup preservation tests | 2 passed |
| Local admin command tests | 7 passed: account creation, private credentials, repeat preservation, explicit reset, local scope, disabled/missing-group rejection, recovery file |
| Go extractor suites | All 4 packages passed |
| PostgreSQL tests | Schema workflows, concurrent jobs, rollback and checksum protection passed |
| Production UI build | Passed |
| Docker builds | Application images and repository worker built from v3 source |
| Compose startup | Thirteen long-running services started; health-checked services healthy; migration and Keycloak client configuration exited successfully |
| Unauthenticated gateway access | Rejected, including spoofed admin headers |
| Real Keycloak login | Login form, callback, application page and admin-group propagation passed |
| Additional local administrator | `local-admin` created after startup; repeating the command preserved its password; SSO and protected admin API passed |
| Browser admin panel | Signed in as `local-admin`; standalone administration on port 8183 rendered initialization results and index status |
| Admin initialization | Markdown index, source cards built successfully |
| Reading and search | Indexed Markdown returned and body-text query found results |
| PostgreSQL startup | Initial schema, repository-checkout, service-asset, server-inventory and Jira-panel migrations present in migration ledger |
| Phoenix tracing | Synthetic `v3.startup-check` span exported and retrieved through Phoenix's API |
| V2 isolation | No v2 runtime path/mount dependency; original v2 working tree unchanged |

Native Python regression suites ran on Python 3.14.2; the Docker runtime and its
end-to-end checks ran on Python 3.12.13. Lockfiles retain the v2 dependency
versions. Existing framework deprecation and frontend bundle-size/sourcemap
warnings did not fail tests or builds.

The smoke client mirrors browsers' Secure-cookie allowance for HTTP localhost
only. It does not disable issuer validation or alter production cookie policy.
No external model call was made. Real embeddings, chat/source answers, and
provider credentials remain to be checked against the operator's chosen model
services. Documentation generation/upload and server access settings remain
unimplemented; GitHub repository settings, service/deployment inventory,
server inventory with descriptions/tags, Jira settings/ticket snapshots and pull jobs use PostgreSQL.

After the application split, live checks confirmed separate reader/admin logins,
reader session rejection by the admin gateway, 404s for old reader admin pages
and APIs, and 404s for reader APIs on the admin service. A temporary ordinary
Keycloak user successfully entered the reader and received HTTP 403 at the admin
login callback. Spoofed admin headers without a session were rejected. The test
account was removed afterward. Browser inspection confirmed the two independent
navigation shells. The full smoke check passed with the existing `local-admin`.

The GitHub Projects browser flow was verified as `local-admin`: saved
`octocat/Hello-World` without a token, queued a pull, observed Synced, and verified
the downloaded README and Git HEAD on the host at the path recorded in PostgreSQL.
The public example is left in the local admin list as `Hello-World (public test)`.
The host checkout resolves commit `7fd1a60b01f91b314f59955a4e4d4e80d8edf11d`.
The authenticated smoke check passed again after deployment. A real private
GitHub token was not supplied: encryption, rotation/removal, askpass behavior,
and token-free Git configuration were tested with synthetic credentials locally.
Repository integration tests always create a disposable PostgreSQL container;
they never run against the configured local application database.

The Services browser flow was verified through the authenticated admin gateway:
created an application, linked the existing downloaded repository, registered a
server with SSH/sudo disabled, saved runtime/port/log/version details, and added
a second environment on that server. Editing retained the first deployment's ID
and incremented the service revision. The JSON context returned repository
checkout provenance and server/deployment metadata without credentials. Temporary
service, association, deployment and server records were removed after the check;
the existing GitHub example and application data were preserved. The independent
reader/admin authentication smoke test passed again afterward. No server command,
health probe, or agent/model invocation was made.

On 2026-09-09 the Servers page was verified through the authenticated admin
gateway: created a temporary loopback host with description and mixed-case,
duplicate tags; verified normalization and deduplication, case-insensitive tag
search and the no-results state; edited hostname, description and tags; reloaded
and confirmed persistence. PostgreSQL retained the same server ID at revision 2
with SSH and sudo disabled. The test record was removed afterward and its audit
history retained. Migration 0004 is applied, all 94 admin API tests and 25 UI tests
passed, and the independent reader/admin authentication smoke check passed.
Database integration tests additionally verify that server edits preserve SSH
configuration and deployment IDs, update nested agent context, reject stale and
cross-project edits, and exclude connection secrets from public responses.

The Jira panel was verified on 2026-09-09 through the authenticated admin browser.
Saved the isolated mock URL/user/PAT, observed the cleared password field, and
passed both current-user and ticket-search checks. Fetching 25 produced 25 of 75
fixtures despite the mock's 10-result page cap. Fetching 5 replaced the visible
selection; stopping the mock and fetching again showed a connection error while
retaining those five results. Restarting the mock and requesting 100 returned all
75 available tickets. The default 25-ticket selection and valid mock connection
are left configured for interactive testing. No real Jira server was contacted.

The mock container is non-root, read-only, has no published ports, and belongs to
an internal Docker network without external connectivity. The optional mock adds
one container to the baseline stack. Migration 0005, production admin builds,
126 API tests, 31 UI tests, schema workflow checks and the independent reader/admin
authentication smoke check passed. HTTP tests cover redirects, token/permission
errors, rate limiting, malformed/oversized pages, timeouts, partial pagination,
credential rotation/removal, and stale settings/fetch results.

## Folder RAG administration — 2026-09-09

- Added and applied migration 0006 on the existing PostgreSQL 17 volume with pgvector 0.8.6. Saved a private pre-migration database backup under `.local/backups`.
- RAG: 275 legacy tests plus 16 PostgreSQL/pgvector workspace tests passed, including unchanged-file parse bypass, embedding reuse, profile changes, atomic failure, cancellation, external edits, folder/project scoping, descriptions, empty replacement, authentication, credentials, and path confinement.
- Admin API: 126 existing tests plus the new proxy authorization/project-binding test passed.
- Admin UI: 34 tests passed; production TypeScript and Docker builds passed. Setup tests and schema migration/checksum/rollback tests passed.
- Deployed the RAG API, durable RAG worker, admin API/UI, and PostgreSQL image. All health-checked services healthy; reader/admin SSO isolation smoke passed.
- Browser: created `demo/payments`, saved a folder description, created `operations.md`, queued processing, repeated it with zero parsing/embedding, and retrieved the retry policy using `demo.payments.*`. Inspected the model and processing settings page. The sample is explicitly fictional and can be removed from the admin UI.
- Real provider credentials were not configured. Embedding response validation, vector search, model failure handling, and retrieved evidence were verified using isolated HTTP mocks and real pgvector. Full-text search was verified in the deployed stack.
- The separate reader continues to use its existing compatibility corpus; the new folder workspace is available through the RAG admin section and its generic APIs.

## Recursive RAG folder deletion — 2026-09-09

- Applied migration 0007 after saving a private database backup. Deleted folder identities retain job provenance while descriptions and searchable content are purged.
- Added the shadcn AlertDialog confirmation, subtree/file counts, Cancel, a destructive confirmation action, busy/error handling, and removal of deleted folders from the tree. Deleted job targets cannot be rerun.
- Verification: 30 folder-workspace integration tests, 275 legacy RAG tests, 127 admin API tests, and 38 admin UI tests passed. Schema migration/reapply/rollback checks and production Docker/TypeScript builds passed.
- Deletion tests cover recursive sources and descriptions, full-text chunks, pgvector cleanup, retained shared vectors, application/sibling isolation, queued/running/ancestor jobs, deletion during embedding, failed filesystem cleanup and recovery, symlink confinement, stale revisions, database rollback, and explicit confirmation.
- Browser verification used only a temporary `delete-check-3489983b` folder with an indexed child/file. Cancel preserved it; confirmation removed its sources and PostgreSQL text immediately. The folder and child disappeared from the tree, scoped search returned no results, and pending cleanup was clear. Existing `demo`, `demo/payments`, and `fdsaf` folders remained intact.

## Single workspace and direct RAG source mount (2026-09-09)

Removed the admin application selector and application labels. Admin pages now use `/platform`, `/github-projects`, `/services`, `/servers`, `/jira/...`, `/rag/...`, and `/activity/...`. Existing `/docs/...` page URLs redirect to these routes. Public admin APIs omit the application segment and `/api/session` replaces application discovery; every route still requires the exact administrator group. Startup rejects zero or multiple configured workspaces. Existing database identities remain internal so repositories, credentials, Jira tickets, servers, services and audit history are retained.

`RG_DATA` supplies both the Docker bind-mount source and displayed host path. The API and worker use `/work/rag-data` directly. Migration 0008 enforces one RAG workspace. The local `demo` collection was moved out of the UUID directory after stopping the API and worker. Source SHA-256 hashes, indexed IDs, relative paths and processing profiles were compared before/after and matched. PostgreSQL and source backups were created at `.local/backups/single-workspace-20260909-101845`.

Validation: setup/source-migration tests (7), RAG workspace tests (33), admin UI tests (38), admin API regression tests and final route/configuration tests (27), TypeScript and production Docker builds, and all database migration/workflow checks. The independent reader/admin login smoke test passes. Browser verification confirmed legacy page redirects, removal of the application selector, the RG_DATA display, visible source folders and unchanged input/button alignment. Live unprefixed APIs returned repositories, services, servers, Jira tickets and RAG folders; the moved file was readable and existing full-text retrieval returned evidence without processing again.

## Jira filters and Markdown exports — 2026-09-09

Added saved JQL queries, local text/project/status filters, selection, and per-ticket
Markdown export to a configurable relative RAG folder. Export fetches fresh issue
content and all visible comment pages, converts Jira wiki markup, and publishes
through the existing confined RAG API. Stable content skips writes; changed files
use compare-and-set hashes. Failures leave previous files intact; stale settings
and unrelated destination files are rejected. No schema migration was needed.

Validation: 150 admin API tests and 45 admin UI tests passed, along with TypeScript,
production Docker builds, and the separate reader/admin login smoke check. The
mock now supports a documented JQL subset and five comments per issue with two
comments per page. Tests cover complete and failed pagination, changed/deleted
comments, stable hashes, stale configuration/selection, source ownership, unsafe
paths, admin identity, JQL failure preservation, selection, partial export errors,
and stopping remaining exports when the page closes.

Live browser QA fetched two Open mock tickets (25 total JQL matches), exported
`DEMO-3.md` and `DEMO-6.md` with all ten comments into `jira/mock-demo`, then repeated
the export. Both files retained their exact bytes and modification timestamps.
RAG processing parsed two files; a second run parsed zero and skipped both. Scoped
full-text search found the fifth-comment evidence in both files. These synthetic
samples remain available in RAG Files. The original default Jira query and
25-ticket selection were restored. Embeddings remain disabled in this local
configuration; this run verified source export and full-text processing/search.

## Per-server SSH permission — 2026-09-09

Added the shadcn **SSH allowed** switch to server registration/editing, an On/Off
inventory column, and the permission in linked service details and agent context.
Migration 0009 adds `ssh_allowed boolean NOT NULL DEFAULT false`. Strict boolean
validation, current revisions, and audit events cover updates. Connection setup,
credentials and sudo remain independent; saving this policy runs no SSH commands.
A private pre-migration backup was saved under
`.local/backups/server-ssh-policy-20260909-121500`.

Validation: 39 server/service API tests, 15 server/service UI tests, database
migration/reapply/checksum/rollback checks, production TypeScript/Docker builds,
and reader/admin authentication smoke passed. Browser QA registered a temporary
host with SSH allowed on, reopened it, saved off, and reopened it again. The two
saved values were confirmed in PostgreSQL and audit history, with no SSH jobs or
connection activation. The QA host was deleted afterward; the original inventory
host remains with the new permission off. Admin API and web containers are healthy.

## Search agent and retrieval-only workspace — 2026-09-09

- Added `/agents` to the separate admin application, sharing the reader's chat component and agent tool loop. Admin tests require `docs-admins`; conversation keys are scoped to the signed-in subject. Usage continues to populate Activity and Phoenix tracing remains enabled.
- Agent prompts receive the indexed document catalog on every turn, with short descriptions from processed text. Tool calls retrieve through `/search`, read documents, discover synced repositories, and request read-only source analysis.
- Removed graph extraction, graph loading/query modules, graph MCP tools, source selectors, and the Ladybug dependency. Old local graph caches were archived and removed.
- Removed the separate `/rag` answer endpoint and duplicate answer-model settings. Applied migration 0010 after backing up PostgreSQL to `.local/backups/search-agent-20260909-141053/postgres.dump`.
- Reader Python suite: 250 passed. RAG suite: 252 passed. Code-agent suite: 158 passed. Admin API suite: 154 passed, followed by the expanded 31-test API suite with four additional agent authorization checks. Reader UI: 96 passed. Admin UI: 47 passed; final navigation/agent subset: 4 passed. Go catalog tests passed across four packages. Production container builds passed.
- Deployed checks: 78 indexed document descriptions discovered, one published GitHub checkout available, full-text retrieval successful, reader catalog/document endpoints return 200, removed `/rag` returns 404, and missing administrator access returns 403. Keycloak smoke tests confirmed separate reader/admin sessions and spoofed-header rejection.
- The Agents page was inspected in the local browser. Live model answers remain unverified because search/code model names and provider credentials are not configured; isolated model transports verified catalog refresh, both source tools, citations, errors, and conversation ownership.

## Advanced search-agent context — 2026-09-09

- Added the shared **Advanced** controls to admin Agents and reader Chat: all/selected document folders, include subfolders, Git search on/off, and all/selected synced repositories. Folder descriptions and direct indexed-file counts are visible; choices remain usable while the model is unconfigured.
- Context and source selection are persisted with each user-scoped conversation. Reload restores the selection. Follow-up calls reuse it or reject changes with HTTP 409. Empty conversations are registered before streaming so their keys remain valid after a source/model failure.
- The selected folders constrain the prompt catalog, document reads, and retrieval. `/search` accepts a union of folder selectors and applies it before full-text/vector ranking and the result limit. Repository discovery and code-agent calls respect selected repository IDs. Documents disabled means no document catalog fetch or descriptions in the prompt.
- Reader Python: 262 passed; final context/session subset: 24 passed. RAG: 253 passed, followed by three full-text/embedding/hybrid folder-union cases (255 total test cases). Admin API: 158 passed, including context-route authorization. Reader UI: 96 passed, followed by expanded API/chat suites (98 total cases). Admin UI: 48 passed. Both production UI builds and all affected container builds passed.
- Deployed `rag`, `docs-wiki`, `admin-api`, `admin-web`, and `web` containers are healthy. Authentication smoke passed. Authenticated reader/admin context endpoints returned matching lists of four folders and one synced repository with no warnings. Deployed full-text retrieval returned only evidence in the requested folder union.
- Browser QA used a separate tab to select a document folder and repository, switch Git search off/on, inspect descriptions/index counts, and open Advanced in both applications. The Git preference was restored. Live model answers remain unverified because model configuration is still absent; isolated transports cover the scoped model/tool loop.

## Skill library — 2026-09-09

- Added Administration → Skills with creation, listing, name/description/status filters, Markdown editing and preview, and enable/disable switches. Disabled skills retain their content and can be edited or re-enabled.
- Migration 0011 adds `ka.agent_skills` with stable IDs, unique names, descriptions, Markdown instructions, enabled flags, revisions, timestamps, and administrator identity. Updates and status changes check revisions under row locks. Audit events record metadata without instruction bodies. Agent assignment and remote execution remain subsequent work.
- Admin API: 184 passed, including 26 new skill tests covering persistence, validation, authorization, concurrent updates, conflicts, workspace boundaries, and audit records. Admin UI: 53 passed. Production TypeScript/UI and container builds passed. Database tests passed migration/reapply, concurrency, rollback, and checksum checks; the migration-count assertion was updated from its stale value to eleven.
- Backed up PostgreSQL to `.local/backups/skills-20260909-145912/postgres.dump`, then applied migration 0011. Admin API and admin web are healthy. Reader/admin authentication and gateway separation smoke tests passed.
- Browser QA created a temporary skill, previewed Markdown, disabled it, edited instructions while disabled, and verified the Enabled filter excludes it. Authenticated API reads and PostgreSQL confirmed revision 3, unchanged disabled status, preserved Markdown whitespace, and expected audit events. The temporary skill was removed after verification; its audit history was retained.


## LLM Settings — 2026-09-09

- Added database migration 0012 for encrypted named model connections and per-agent assignments.
- OpenAI-compatible Responses / Chat Completions, Anthropic, and AWS Bedrock bearer API keys are configurable in the separate admin application.
- Admin API suite: 197 passed, including encryption, nonce uniqueness, key preservation/replacement, assignment compatibility, stale revisions, authorization, and error redaction.
- Reader suite: 261 passed before updating the deployment credential contract; all 42 affected agent/search/deployment tests then passed, including native Anthropic / Bedrock streaming with isolated transports.
- Code agent suite: 160 passed, including managed Bedrock credential injection and removal of database/master keys from child environments.
- Admin UI suite: 57 passed, including saving assignments without read-only timestamps, key preservation, disable, stale edit, and explicit connection testing.
- Database migration, idempotent reapply, concurrent job creation, migration failure rollback, and checksum checks passed against disposable PostgreSQL.
- Built reader, admin API, code agent, and admin web images. Applied migration 0012 after creating `.local/backups/llm-settings-20260909-173947/postgres.dump` (mode 0600).
- Auth smoke verified separate reader/admin logins, isolated sessions, and disjoint APIs. No live provider inference was used for verification.
- Browser verification: created a temporary Bedrock profile, selected it for both agents, saved assignments successfully, reopened it with the key hidden, and disabled it while preserving the stored key. Both deployed agent services decrypted the saved profile without restart; admin/reader status reflected assignment and disable changes immediately. Cleared assignments and deleted only the temporary QA model; its audit history remains.


## Retired Platform initialization — 2026-09-09

Removed the Platform page, navigation, client calls and initialization proxy routes,
along with the environment-driven catalog/docs/code initializer, its SQLite job
history, and the Go catalog tool. Docker starts `rag serve-workspace` against the
existing PostgreSQL folder pipeline and fixed source mount. The admin root opens
`/rag/files`; processing remains explicit through RAG administration.

RAG and admin API suites passed; the admin UI passed 55 tests and its production
build. Setup passed 14 tests and deployment configuration passed 8 tests. Rebuilt
and restarted admin-web, admin-api, rag and rag-worker; all reported healthy.
Browser verification confirmed the RAG landing page and navigation without Platform.
The full local smoke check passed SSO/session isolation, workspace settings/tree/jobs,
Markdown retrieval, full-text search, PostgreSQL migrations and Phoenix without
model calls. Deployed retired initialization endpoints return 404. All 78 source
files and every folder, processed-file, chunk and vector row matched their
pre-restart fingerprints. Existing data volumes were not deleted.

## Versioned prompts and Mermaid validation — 2026-09-09

- Applied migration 0013 after a private PostgreSQL backup. Search and Code analysis each have an initial active version; new versions are drafts until explicitly activated. Runtime prompt selection is loaded per request in the reader, admin search, and code agent.
- Prompt API integration tests passed draft editing, immutable publication, rollback to an archived version, concurrent activation, stale revisions, authorization, and input validation. The full reader API/agent suite passed 269 tests; focused code prompt/runtime tests passed 38 tests.
- Admin UI passed 66 tests and reader UI passed 101 tests. Diagram tests cover delayed rendering while streaming, safe failure output, and citation formatting that preserves Mermaid labels and inline code. Both Markdown suites passed again after removing the diagram's code-block wrapper.
- Real Node parser tests passed valid flow/sequence/state diagrams, invalid syntax diagnostics, and input limits. The deployed admin container passed active database prompt loading, valid/invalid parsing, and final-answer fallback checks.
- Both production UI builds and all affected Docker builds passed. Reader, admin API, code agent, reader web, and admin web are healthy. Migration reapply, concurrency, rollback, and checksum tests passed in disposable PostgreSQL. Independent reader/admin SSO and workspace smoke checks passed.
- Browser verification confirmed the Prompts history, active version, draft editor, and cancellation without saving a test version. Published defaults remain active for both agents.
- Live Bedrock search used only the existing fictional payment documentation. Its stored tool calls include `search_docs`, `read_doc`, and a successful `validate_mermaid` call. The browser rendered the resulting diagram. A second live query verified the deployed font/wrapper fix: full node labels remain readable instead of inheriting the code block's monospace font and clipping. Source files and indexes were not reprocessed.

## Inactive prompt version deletion — 2026-09-09

- Migration 0014 permits deleting drafts and archived versions while preserving database protection for the active version and immutable published content. A per-agent counter prevents reuse of deleted version numbers.
- The admin table offers Delete only for inactive versions, with a shadcn confirmation dialog and in-dialog failure feedback. Successful deletion removes the row and returns its selected preview to the active version. Deletion records an audit event without retaining deleted prompt content there.
- All 10 prompt API integration tests passed, including draft/archive deletion, unchanged active instructions, version numbering, authentication, confirmation, stale revisions, direct database protection, and concurrent activation/deletion. All five prompt UI tests passed; the production admin build and schema reapply/rollback/concurrency/checksum tests passed.
- Saved a private database backup, applied migration 0014, and deployed admin-api/admin-web. Live browser verification showed Delete for archived search v1, no Delete for active search v2, and the correct confirmation. Cancelled without deleting existing versions.


## Direct specialist Test execution — 2026-09-11

- Wired the existing agent-scoped operations run endpoint to the saved configuration
  loader, credential resolution and official Codex SDK function. No coordinator or
  board task is created. Test UI streams activity and displays the final answer,
  errors, cancellation and durable history. No database migration was needed.
- Integration tests used disposable PostgreSQL and an isolated Responses server:
  actual SDK execution, selected prompt/model, redaction, authorization, duplicate
  request IDs, cancellation, stream disconnect cleanup and interrupted-run recovery.
  Direct execution tests passed (8); existing operations/board tests passed (14).
  Focused UI/client tests passed (45); production UI build passed.
- Deployed healthy admin-api/admin-web containers. Browser smoke used a temporary
  agent with only the public Hello World code folder and the saved native Bedrock
  model. It read `/work/code/Hello-World (public test)/README`, streamed commands,
  and returned `Hello World!`. A second sleeping task was cancelled in the UI;
  its SDK process stopped and PostgreSQL retained `cancelled`. No SSH server was
  contacted and neither test created a board task.
- Phoenix recorded Codex SDK turns (traces `739dd5d49bd54b10ac52263516260b06`
  and `3351a5afa6179eaae30acb5ff7c5428c`). Removed the temporary QA agent and its
  two run records after verification; existing agents and their data were preserved.


## Inherited SSH key formatting — 2026-09-11

- Agent `9e87f51a-fb03-46d7-9441-d5bc0776a23b` correctly inherited the global
  `commoncrawl2` connection and credential. The saved OpenSSH key lacked its final
  newline: AsyncSSH accepted it, but OpenSSH rejected the runtime file with
  `error in libcrypto`. Adding the newline made OpenSSH accept the same key.
- The Codex runtime now adds a missing final newline when writing its temporary
  mode-0600 key file. Stored ciphertext, credential references, and agent/server
  settings remain unchanged. Regression tests check both newline variants with
  real OpenSSH key parsing, matching public identity, secret-free model context,
  and temporary-file cleanup. All 42 SDK/configuration/wizard tests passed.
- Deployed admin-api and verified this exact agent through its direct Test
  endpoint. Run `70d121b5-c20c-42e5-b4ef-a4ddc11a5687` completed successfully:
  `id -un; hostname` returned `agent` and `commoncrawl2`, exit 0. Host-key
  verification passed. No sudo or service changes were performed. The successful
  verification is retained in the agent's Recent runs.

## Complete agent configuration versions — 2026-09-11

- Added migration 0034 with configuration snapshots, an active version pointer,
  monotonic numbering and session/version ownership constraints. Backed up the
  local database before applying it. Fresh migration, rollback/reapply, legacy
  adoption and concurrency checks passed in disposable PostgreSQL.
- API checks cover complete resource/credential-reference snapshots, inherited
  and explicit server instructions, inactive drafts, atomic activation/rollback,
  stale sources, cross-agent rejection, disabled resources and pinned native SDK
  conversations. The focused UI suite passed 49 tests; the final chat/version
  subset passed 36 after the activity label update. Production builds passed.
- Deployed admin-api/admin-web. Live native Bedrock checks used a temporary agent
  with all resource capabilities disabled: v1 returned `BASELINE`, v2 returned
  `CANDIDATE`. After activating v2 in the UI, both sessions continued concurrently;
  v1 still returned `BASELINE`, and v2 recalled its earlier word `amber`.
- The actual tuning endpoint returned structured instruction replacements. The
  browser's **Save as new version** action prefilled the draft editor; saving
  created inactive v3. A new Test conversation for v3 returned the requested single
  bullet. Its URL included both version_id and session_id, and the UI showed the
  pinned agent version. Activation did not alter older session configurations.
- Bedrock may JSON-encode nested structured-output arguments. The review boundary
  decodes that observed format, then validates the supported fields. Invalid
  output gets one bounded correction request; it is never saved as configuration.
- Closed and removed the temporary QA agent and its three sessions, runs, review,
  and versions. Audit evidence remains. Existing agents and conversations were
  preserved; no existing SSH server was used for this verification.

## Native agents as coordinator tools — 2026-09-12

- The board opens every selected specialist with `agents.create_agent()` and gives
  the coordinator one native `Agent.as_tool()` entry per specialist. The generic
  `run_subagent(agent_id, task)` dispatcher has been removed from runtime code.
- Real SDK integration checks passed for repeated/parallel delegation, total
  subtask limits, failure reporting, cancellation of running and queued work,
  per-specialist credentials and folder boundaries, authenticated MCP calls,
  streamed child activity, separate usage counters, and nested Phoenix spans.
  Direct execution, model adapters, sessions, configuration versions, tuning,
  and wizard regression checks also passed in disposable test environments.
- Rebuilt and restarted only admin-api after confirming there was no queued or
  running agent work. Image:
  `sha256:6539e54558807a0cd97e6e1f5a2ad8e384a35963e1143d910cad3bc65ae1dfae`.
  Container health passed.
- A live smoke check used the saved Bedrock connection and current coordinator
  system prompt with two temporary in-memory specialists. The coordinator called
  both native agent tools and combined their `cedar` and `pine` answers. No saved
  agent configuration or board records were changed, and no SSH host was contacted.

## Native tracing and final summary — 2026-09-12

- Simplified execution to a native orchestrator with `Agent.as_tool()` specialists,
  followed by a tool-free summary agent using the same selected model. The summary
  prompt is editable in Coordinator → General and snapshotted for each board task.
  Direct specialist Test/chat runs continue without the summary stage.
- Replaced manual runtime spans with native SDK `trace()` scopes and the pinned
  OpenInference Agents integration. Phoenix receives agent/model/tool spans;
  application activity hooks retain live UI previews and Phoenix link IDs. Export
  filtering redacts known credentials and raw provider errors. OpenAI's default
  trace exporter remains disabled.
- All 75 backend regression tests passed, covering native delegation, isolation,
  model adapters, sessions, configuration versions, tuning, wizard output,
  cancellation, and trace redaction. All 18 focused UI tests and the production
  UI build passed. After fixing the observed Bedrock summary handoff, the 14 board
  and coordinator tests passed again.
- Saved a private PostgreSQL backup and applied migration 0035 using
  golang-migrate. Disposable database checks passed fresh migration, rollback to
  34, reapply, coordinator protection, concurrent jobs, and legacy adoption.
  Rebuilt and deployed healthy admin-api/admin-web containers. Final API image:
  `sha256:d360a0c688ac2e50966f4962e7772264445444f7dd35bcb2e30676aace30481c`.
- Browser verification confirmed the editable final-summary prompt and its
  saved default on the coordinator page. Existing agent settings were preserved.
- A live check with the saved Bedrock model called two temporary in-memory
  specialists and streamed the combined final summary. Bedrock requires the
  summarizer's input to end with a user message; the handoff now appends an
  explicit summary request after the returned conversation. Phoenix API confirmed
  all 12 spans had OK status in trace `95446a956f3fc5f7310a93c5accae3e4`, including
  both specialists, their delegation tools, the coordinator, and the summary.
  No saved agents or board tasks were created, and no SSH server was contacted.

## Shared agent sessions — 2026-09-12

- Replaced the test-only session table with `ka.agent_sessions`, using `is_test`
  to distinguish conversations. Both explicit creation and a first message call
  the same `create_session()` function. Agent-scoped run routes use the same
  version pinning, cancellation, close, and one-hour inactivity lifecycle.
- Successful turns store the complete SDK input history in PostgreSQL, atomically
  with their completed run. Failed or cancelled turns preserve the previous
  committed history. Runtime filesystem history has been removed; temporary
  directories are used only for tool workspaces.
- Focused backend checks passed for sessions, runs, migration/import, versions,
  board and operations (37 tests), with the additional creation/deletion and TODO
  checks passing (7 tests). The initial SSH fixture failure passed on an isolated
  rerun. The focused UI suite passed 42 tests; the final API-client suite passed
  7 after adding the session-filter check. The production UI build passed.
- Disposable PostgreSQL checks passed fresh migration, rollback/reapply,
  coordinator protection, concurrent migrations, dirty-ledger handling and legacy
  adoption. Saved a private database backup, stopped the API and applied migration
  0036 with golang-migrate. All three existing sessions were preserved as test
  sessions; the one-time importer restored complete SDK history from one existing
  file. Source history files remain as backups. The old table no longer exists.
- Deployed healthy admin-api/admin-web containers. API image:
  `sha256:6eafc3282668e3a1e74d28f128800a679a33ef51a73f684d175a2fb4af1d0953`.
  Web image:
  `sha256:c97901fe3c55280881d48d8a897200ba52c3d9cd67a6213d3aa377a527eb829c`.
- A temporary agent using the saved Bedrock model completed two turns in a live
  API session and two in the Test UI. Both recalled the previous message. The Test
  conversation also survived a browser reload with the same `session_id`, and
  its History panel excluded the live conversation. PostgreSQL held four SDK
  history items per session; neither created a filesystem history file.
- Closed and removed only that temporary agent, its two sessions and four runs.
  Existing agents, sessions, model settings and audit evidence were preserved.
  No SSH server was contacted. Reader-facing chat remains unchanged; this step
  connects the shared backend and existing Test tab only.

## Tuning review import format — 2026-09-12

- Phoenix traces for session `b6143f1a-ae86-44eb-972d-c13bfc4d7a32` showed
  Bedrock returning the nested `changes` object as a JSON string. Native SDK
  output validation rejected it on both attempts. The tuning-output schema now
  decodes that field once before validating supported fields and server IDs.
  Draft request validation remains unchanged.
- All 19 focused version/session tests passed, including the native SDK output
  validator, encoded server instructions, saving a reviewed draft, and rejection
  of malformed JSON or unsupported fields. Deployed healthy admin-api image
  `sha256:24760b60de2ec5da0e362887c1b37ec965a46c59c324f020641faa51915794ca`.
- Retried the original feedback against the saved conversation using its model.
  Review `b06cd915-c38f-4e76-a970-d6efeb97d51e` returned HTTP 200 with validated
  instruction changes; loading it as a draft source also returned HTTP 200.
  Browser verification confirmed **Suggestions (1)** and **Save as new version**
  populated the system prompt, additional instructions and server instructions.
  The suggestion is saved for the user. No agent version was created or activated.

## Test session token usage — 2026-09-12

- The Test conversation shows recorded totals for input, output and total tokens,
  model-call count, and cache/reasoning details where reported. Totals update when
  a reply completes, include repeated context and tool-loop model calls, and
  exclude tuning reviews. Missing historical usage is shown as unavailable;
  sessions mixing known and missing usage are explicitly marked partial.
- Native Bedrock streaming previously omitted usage because `include_usage` was
  unset. Model settings now request streaming usage for native and compatible
  connections. No database migration is needed: per-turn SDK usage already has
  a persisted field. Historical zero counters were not replaced with estimates.
- All 18 focused model-adapter/run tests and all 36 session/operations UI tests
  passed, as did the production UI build. Coverage includes multi-call totals,
  duplicate completion events, cache/reasoning breakdowns, pending replies,
  missing data, session reset and native provider usage reporting.
- Deployed healthy API and web containers. A temporary Bedrock agent with all
  resource capabilities disabled completed two turns. The browser updated from
  155 to 328 total tokens, with 278 input tokens, 50 output tokens, 42 reasoning
  tokens included in output, and two model calls. PostgreSQL held matching totals.
  The existing user session showed unavailable historical usage as expected.
- Removed only the temporary QA agent and its two-turn conversation. No SSH server
  was contacted or existing configuration changed. API image:
  `sha256:d9fcbe770bfbfe7a68de1660c67d1350577d930d6676d1b1a55897b2e81ef5a6`.
  Web image:
  `sha256:100fd5dca78f855e53b8a03afc96b311062e8589318b4a9dcc3cd2332f0cf86c`.

## Shared RAG embedding connections (2026-09-12)

- Added Embeddings to LLM Settings; RAG Settings selects a shared model and retains
  only file-processing options and the embedding enable switch.
- 30 admin API tests passed across model settings, embedding connections, and agent
  profiles. These cover encrypted credentials, connection probes/dimensions,
  validation, revisions, and rejecting embeddings for specialist/coordinator models.
- 41 RAG integration/migration tests passed with isolated PostgreSQL/pgvector and
  mocked model traffic. Existing credential ciphertext and vector signatures survive
  migration; model updates reuse parsed text, and concurrent model changes cannot
  publish stale vectors. Full-text search works without an embedding connection.
- 18 targeted UI tests, the production UI build, and the golang-migrate schema
  checks passed, including rollback/reapply and adoption of populated databases.
- Built/recreated admin API, admin UI, RAG API, and worker. Backed up the local
  database before applying migration 37; the ledger is clean and services healthy.
- Browser verified creating an Embeddings connection with dimensions/version,
  successful LLM connection testing, RAG selection/save, and a successful RAG
  connection test against a temporary local HTTP fixture. The chat model was
  absent from the RAG selector. Removed the fixture and temporary connection,
  restored the original unconfigured/disabled RAG selection, and left source files
  and indexes unchanged. No external model request was made for this verification.

## Native Bedrock embeddings (2026-09-12)

- Added native Titan Text Embeddings V2 support to LLM Settings connection tests
  and the RAG worker/search runtime. Bedrock uses InvokeModel, the saved region,
  bearer authentication, normalized vectors, and configured dimensions. Other
  native embedding families are rejected until their request formats are supported.
- Applied migration 38 after a private database backup and rebuilt/recreated admin
  API/UI and RAG API/worker. The migration ledger is clean.
- Passed 32 model API tests, 42 RAG workspace/migration tests, 19 targeted UI tests,
  the Docker production UI build, and golang-migrate schema/rollback checks.
- Created `Bedrock Titan Embeddings V2` (`amazon.titan-embed-text-v2:0`) in
  `eu-central-1`, with 1024 dimensions and version `1`. As requested, copied the
  saved `test1` key in backend memory and saved it through the model API under the
  new model's encryption binding. `test1` and its encrypted credential are unchanged.
- Real AWS requests succeeded: direct preflight, saved model connection test, and
  embedding generation using the deployed RAG runtime. Only short connection-test
  text was sent. Browser verified the saved model, Bedrock-specific editing fields,
  and its presence in the RAG model selector. RAG selection/enable state and existing
  indexes remain unchanged; administrators can select this connection when ready.

## RAG file tree (2026-09-13)

- Replaced the flat RAG folder navigation with a reusable Headless Tree component,
  styled with the existing shadcn controls. Folders expand independently of
  selection, files open the source editor, and the parent folder's description and
  processing controls remain available. Expanded branches survive API refreshes;
  deep links reveal their ancestors.
- Passed all 16 focused tree/RAG UI tests, covering keyboard navigation, nested and
  empty folders, refresh/deletion, busy controls, deep links, file editing with the
  current hash, incremental processing, and folder deletion confirmation. TypeScript
  and the Docker production build passed.
- Rebuilt and recreated only admin-web; its container is healthy. Browser verified
  expanding demo/payments, opening operations.md, returning to the selected folder,
  and retaining both expanded branches after Refresh folders. Source documents,
  indexes, and backend configuration were unchanged during this verification.

## Service deletion (2026-09-13)

- Added a Delete action to each service row with a shadcn confirmation dialog.
  Cancellation preserves the service; pending requests disable the dialog actions,
  and failures remain visible for retry or cancellation. Success removes the row
  and closes its detail panel before refreshing the inventory.
- Added an admin-only DELETE endpoint requiring confirmation and the current
  revision. Deletion atomically removes the service, deployment records, repository
  links, and cascading agent selections, and records an audit event. Shared servers,
  repositories, other services, and running applications are preserved. No migration
  is required.
- Passed 42 service/server API tests against disposable PostgreSQL and 11 service UI
  tests, including authorization, project scope, stale revisions, linked-asset
  preservation, confirmation, pending requests, and failure recovery. The production
  UI build and both Docker image builds passed; admin-api and admin-web are healthy.
- Browser verified the deployed Delete action, named confirmation, and Cancel focus
  without deleting the existing service. Successful deletion was verified with
  disposable test records in the integration tests.

## Documentation administration foundation — 2026-09-13

- Applied golang-migrate versions 39–40. The local legacy document sets, old
  collections and generation jobs were empty. Migration 39 retires that unused
  pipeline and refuses populated legacy records. Migration 40 creates managed
  documents/versions, inventory links, analysis prompt versions and generation
  snapshots. The live ledger is version 40, dirty=false.
- Docs navigation exposes Documents, Prompts and Generation history. Create/edit
  Markdown, preview it with the existing renderer, browse/restore historical
  versions, link code projects/services/servers/deployments and filter documents.
  Prompt saves create drafts; activation is explicit; disabling, inactive version
  deletion and whole-prompt deletion are available. Generation execution and RAG
  publishing are intentionally the next stage.
- Validation: 69 API tests (documentation, services and servers); 18 relevant UI
  tests (documents, service CRUD and admin navigation); React Router typegen and
  TypeScript; production Docker builds for admin-api/admin-web. The migration
  suite passed fresh apply, reapply, rollback to 38, coordinator reapply, legacy
  adoption, transactional failure and concurrency checks. Populated legacy data
  rejection and immutable document/prompt content are covered by integration tests.
- Browser verification: created a temporary document, selected a code project and
  service deployment, previewed Markdown, saved v1 and v2, read v1 with its original
  source snapshot, and cancelled deletion. Created a temporary prompt, activated
  v1, saved v2 as draft and verified v1 remained active and undeletable separately.
  Generation history shows its explicit not-yet-enabled state. No browser console
  warnings/errors were recorded. Temporary records were removed using their exact
  IDs and expected revisions; shared inventory was retained.
- admin-api, admin-web and the existing RAG service are healthy. No RAG source
  files, processed chunks or embeddings were changed. Build log:
  `/tmp/knowledge-agent-v3-documentation-build.log`. Migration check log:
  `/tmp/knowledge-agent-v3-document-migrations.log`.

## App-docs prompt fixture — 2026-09-13

- Applied migration 41 locally; golang-migrate reports version 41, dirty=false.
  `Application documentation (Diátaxis)` is enabled for code inputs with v1
  active. The template combines the v2 app-docs skill and its three referenced
  guides, adapting output to managed document proposals and supporting non-Git
  folders. No generation or RAG processing was run.
- All 30 documentation API/fixture tests passed. Coverage includes fresh workspace
  initialization, existing-workspace backfill, same-name template preservation,
  draft edits retaining the active version, no reset of edited templates,
  deletion surviving page reload and rollback refusing customized fixtures.
- The golang-migrate suite passed upgrade/reapply/rollback, fresh-workspace seed,
  legacy adoption, concurrency and transactional-failure checks. Log:
  `/tmp/knowledge-agent-v3-app-docs-fixture-migrations.log`.
- Browser verified the prompt list and editor at
  `/documentation/prompts/391be16e-808d-97c4-ddd4-5da6531a5a69`: full prompt text,
  Code projects input, enabled status, v1 Active, and disabled activation/deletion
  of that already-active version. Verification made no UI edits.

## Service dependency schema — 2026-09-13

- Applied migration 42 locally; ledger version 42, dirty=false. The new
  `ka.service_dependencies` table is empty and ready for inventory relationships.
  It stores directed service pairs, a relationship description, and timestamps.
  This step adds no dependency editor or agent/API dependency projection.
- All 24 service/dependency integration tests passed against disposable PostgreSQL.
  Verified many-to-many direction, reverse lookup, permitted mutual dependencies,
  duplicate/self/missing/cross-workspace rejection, description limits and update
  timestamps. Existing service edits preserve links; service deletion removes
  incoming and outgoing links while preserving peers. Upgrade/rollback preserves
  service records and rollback refuses populated dependency data.
- The golang-migrate suite passed fresh apply, reapply, latest rollback, rollback
  to 38 and coordinator reapply, legacy adoption, concurrency and transactional
  failure checks. Log:
  `/tmp/knowledge-agent-v3-service-dependencies-migrations.log`.

## Service relationship configuration — 2026-09-13

- Applied migration 43 locally (dirty=false). The service context view now returns
  both dependency directions, with service IDs, names and relationship descriptions.
  The view rollback preserves the underlying dependency records.
- Services → Add/Edit has searchable Depends on and Used by lists with optional
  relationship descriptions. Both directions write the same directed rows. The
  current service is excluded, selections survive filtering, and Cancel discards
  form changes. Details and agent service configuration expose both directions.
- Service saves/deletes serialize inventory edits before checking revisions.
  Relationship changes invalidate the other endpoint's revision; stale and
  concurrent opposite edits return 409. Omitted directions preserve existing
  links, empty lists remove that direction, and unrelated edges remain intact.
- Validation: 70 backend tests covering services, dependencies, agent configuration
  and documents; 14 service UI tests; React Router typegen and TypeScript checks;
  complete migration suite and production admin-api/admin-web builds passed.
- Browser verification checked both live form selectors, conditional description
  fields and desktop layout, then cancelled the unsaved form. The existing service
  inventory was not modified; relationship count remains zero. Both admin services
  are healthy. Build and migration logs:
  `/tmp/knowledge-agent-v3-service-dependency-ui-build.log` and
  `/tmp/knowledge-agent-v3-service-dependency-ui-migrations.log`.

## Service dependency graph — 2026-09-13

- Added Service list / Dependency graph tabs, with a direct link at
  `/services?view=graph`. The graph reads existing service relationships; no API
  or database changes. Arrows point to dependencies, isolated services remain
  visible, and the relationship table shows descriptions and opens either end's
  service details.
- Reused the Mermaid renderer with pan/zoom cleanup on unmount, stable SVG markup
  during unrelated React renders, and an initial zoom cap for small diagrams.
  Service names use encoded labels and generated node IDs.
- All 32 focused UI/parser tests passed, including real Mermaid parsing of mutual
  dependencies and punctuation, isolated/deleted targets, empty inventory, direct
  navigation, detail selection, SVG preservation and listener cleanup. TypeScript
  and the production admin-web build passed.
- Deployed admin-web and verified the existing `rg → nginx` relationship, its
  description and nginx details, zoom controls remaining active after selection,
  and list/graph tab switching. No browser warnings or errors were recorded and
  verification did not change inventory records. Build log:
  `/tmp/knowledge-agent-v3-service-graph-build.log`.

## React Flow service graph — 2026-09-13

- Replaced the Services dependency diagram with React Flow 12.11.6 and Dagre
  3.1.1. Dagre arranges services left to right; directed arrows, isolated services,
  relationship descriptions and service-detail links use the existing inventory.
- Nodes can be dragged or moved with the keyboard. Service names open details,
  and Auto layout restores Dagre positions. Native controls zoom and fit the view;
  dragging the background pans. Connection editing and deletion are disabled.
  Moving nodes affects only the current view. Saved inventory and the API are
  unchanged; documentation/chat continue using Mermaid.
- All 24 focused service UI and Dagre layout tests passed, covering direction,
  mutual/longer cycles, isolated/removed services, refreshed inventory, inert HTML
  labels, detail selection, keyboard movement, deletion prevention and layout
  restoration. TypeScript and the production admin-web build passed.
- Deployed admin-web and checked the live `rg → nginx` graph: arrows, node dragging,
  details preserving dragged positions, Auto layout, zoom, fit and canvas panning.
  Browser console had no errors or warnings. Verification did not change service
  records. Build log: `/tmp/knowledge-agent-v3-react-flow-build.log`.

## TALA service graph preview — 2026-09-13

- Added a TALA preview beside the interactive Dagre graph, with a deep link at
  `/services?view=graph&layout=tala`. D2.js 0.1.34 bundles D2 0.9.0 and runs the
  layout and SVG rendering in a browser worker. The SVG is embedded as an image
  in the existing React Flow viewport; it supports pan, zoom and fit, with
  service-detail links and full descriptions in the relationship table.
- The engine is lazy-loaded (approximately 11.5 MB JavaScript before transport
  compression); it is not fetched by the service list or Dagre view. No inventory
  is sent to an external renderer. Cleanup disposes the worker after rendering,
  on unmount or changed source, and on the 60-second timeout. Failures show Retry.
- All 29 focused checks passed: existing CRUD/Dagre behavior, preview deep links,
  comparison switching, image embedding, service-detail selection, retry,
  cancellation/stale results and timeout. A real TALA WASM test compiled and
  rendered escaped labels, a dependency cycle, an isolated service and a stale
  link to a deleted service. TypeScript and both production builds passed.
- Deployed admin-web and visually checked the saved four services and five
  relationships. TALA places the shared `nats` and `nginx` dependencies between
  `tusers` and `rg`, with the `tusers → rg` connection routed above them. Zoom,
  canvas panning, fit, service details and switching back to Dagre work. Browser
  console had no errors or warnings. Verification did not change service records.
  Docker build log: `/tmp/knowledge-agent-v3-tala-build.log`.

## TALA default dependency view — 2026-09-13

- Promoted TALA to the default at `/services?view=graph` and removed preview
  wording. Existing `layout=tala` links remain valid. Interactive (Dagre) stays
  available at `layout=dagre`; switching back to TALA removes that parameter.
- All 30 focused service/graph checks passed, including both default and existing
  TALA URLs, switching both ways, rendering, cleanup and the Dagre alternative.
  The Docker production build (including TypeScript) passed and admin-web was
  deployed. Browser verification confirmed the default diagram, both layout
  switches and their URLs, with no console errors or warnings.
  Build log: `/tmp/knowledge-agent-v3-tala-default-build.log`.
## Service classification — 2026-09-13

- Applied migration 44 through golang-migrate; the live ledger is clean. Added
  independent service type and primary role enums, exposed in service CRUD and
  context. Omitted API fields retain their saved classifications on edit.
- Services can be custom applications, built-in software, or external services.
  Roles include application, proxy, web server, database, cache, message broker,
  worker and other. Existing inventory remains unclassified until edited.
- The form edits both fields; list/details show them and filtering includes their
  labels. TALA and Dagre share role colors, show role labels on nodes and display
  a legend. Environments are deferred to the next change.
- Passed 35 backend tests and 32 UI/graph tests. Tests cover enum validation in
  PostgreSQL and the API, persistence, omitted fields, stale updates, audit values,
  rollback preservation, form selection, filtering, legend and role colors. The
  real D2/TALA WASM test verified colors in the rendered SVG. The golang-migrate
  suite passed upgrade, rollback/reapply, dirty-state and legacy-adoption checks.
- Both production Docker builds passed, including TypeScript. Deployed admin-api
  and admin-web; both are healthy. Browser verification confirmed dropdowns and
  role selection, then cancelled the unsaved form. TALA rendered the four saved
  services and five relationships with the unclassified legend; no browser errors
  or warnings. Existing service values and relationships were not changed.
- Logs: `/tmp/knowledge-agent-v3-service-classification-build.log`,
  `/tmp/knowledge-agent-v3-service-classification-migrations.log` and
  `/tmp/knowledge-agent-v3-service-classification-live-migration.log`.


## Environment details and membership — 2026-09-13

- Added Environments to the administration menu at `/environments`. Operators can
  create, view, edit and delete environments with a name, description and multiple
  selected services/servers. Membership searches preserve selections; the list
  filters by environment details, service names and server names/hostnames.
- Applied migration 45 through golang-migrate; the live ledger is clean. The three
  new tables use explicit many-to-many membership and workspace foreign keys.
  Existing deployment labels remain unchanged; environment-specific dependencies
  and graph configuration are deferred as requested. No environments were seeded.
- Passed 66 backend tests (environments, services, servers and classifications),
  10 UI/navigation tests, React Router typegen, TypeScript and both production
  Docker builds. The migration runner passed upgrades, rollback/reapplication,
  transactional failure, concurrency and legacy-ledger adoption checks.
- Tests covered shared membership, replacement/removal, case-insensitive name
  uniqueness, concurrent stale edits, administrator-only access, cross-workspace
  rejection in API and SQL, deletion confirmation and cleanup, audit events and
  rollback protection. Deleting an environment preserves member assets and
  deployment records; deleting a member cleans up its membership links.
- Deployed admin-api and admin-web; both are healthy. Browser verification confirmed
  the menu, empty state, form layout, service classifications, server hostnames,
  multiple selection and preservation through filtering. Cancelled the unsaved
  verification form; no environment records or asset memberships were added.
  The four services and five existing relationships remain. Browser console had
  no warnings or errors.
- Logs: `/tmp/knowledge-agent-v3-environments-build.log`,
  `/tmp/knowledge-agent-v3-environments-migrations.log` and
  `/tmp/knowledge-agent-v3-environments-live-migration.log`.


## Dependency graph moved to Environments — 2026-09-13

- Removed the graph tab and renderer from Services. Environments now has an
  Environment list / Dependency graph switch, an environment selector, and a Graph
  button on each row. The selected environment and layout are retained in the URL.
- Graph input contains only member services; existing D2 and Dagre generation
  includes edges only when both endpoints are present. Unconnected members remain
  visible. Missing, deleted or empty environments never fall back to all services.
  Shared service dependency records remain the source; no migration or API change.
- Preserved TALA by default, interactive Dagre, role colors/legend, pan/zoom, layout
  reset and service details. Switching environments clears selected details and
  resets the canvas. Refresh uses current membership and removes stale nodes/edges.
- Passed 42 focused service/environment/graph tests, TypeScript and the production
  admin-web build. Coverage includes cross-environment exclusion, deep links,
  empty/deleted environments, switching, refreshed membership, role colors,
  interactions, TALA worker cleanup and real WASM rendering.
- Deployed admin-web and verified the saved dev environment in the browser: four
  member services, five relationships, TALA and Dagre layouts. Services shows the
  inventory without graph tabs. No browser warnings or errors; admin-web is healthy.
  No saved inventory or membership records were changed by verification.
- Build log: `/tmp/knowledge-agent-v3-environment-graph-build.log`.

## 2026-09-13 — Service documentation generation

- Applied migration 46 through golang-migrate; live ledger reports `46`, clean. Disposable database runner passed forward migration, rollback/reapply and guard checks.
- Added environment document associations and four editable active prompt fixtures. Generated work uses scoped existing Agents SDK configurations, immutable job inputs, a dedicated documentation worker and atomic document version saves.
- 56 focused backend tests passed, covering document CRUD, fixtures, environment links, generation scoping, conflicts, failures, export and graph validation. 31 focused frontend tests passed across generation, Documents, Markdown, TALA, environment graph and navigation. Production TypeScript/build checks passed.
- Confirmed real D2/TALA compilation of generated nested graph source, including a quoted/interpolated label. Live configured-model smoke returned valid GeneratedDocument and ComponentGraph outputs using synthetic input with all tools disabled; it saved no application documents and made no SSH connections.
- Rebuilt/deployed admin-api, admin-web and documentation-worker. Both admin services healthy; worker running. Confirmed writable `/work/generated-documents` mount.
- Browser checked service/environment selection, project checkboxes, selected active prompts and environment server choices. Corrected WebKit container-query sizing that collapsed source selectors after selecting a service. D2 preview rendered inside an unsaved Markdown document with pan/zoom controls; discarded it afterward. No browser warnings or errors. Temporary tabs closed; existing inventory/documents unchanged apart from the requested prompt fixtures.

## 2026-09-13 — Built-in documentation workflows

- Replaced generation's AI-agent selectors with fixed workflow steps and one model
  selection from LLM Settings. No documentation profiles were created in `ka.agents`.
  Service generation independently selects diagrams, code analysis and deployment;
  whole-environment generation covers all member components and recorded relations.
- Applied migration 47 through golang-migrate; the live ledger reports 47, clean.
  Added the active Environment architecture fixture and upgraded the untouched D2
  fixture to version 2 for standalone diagrams. Existing customized prompts remain.
  Saved prompt versions can be managed as before; the generation form also shows
  and edits per-run copies, with active-version conflict detection and immutable
  original/overridden prompt snapshots.
- 60 focused backend tests passed across generation, documents, prompt fixtures and
  environments (56 initially, plus four new workflow cases). Covered diagram-only
  output, code reuse, global SSH credential resolution without profile dependency,
  secret-free snapshots, environment filtering/associations, prompt overrides,
  stale submissions, atomic version conflicts, export and rollback guards.
- 24 frontend tests passed for the form, document editing, Markdown and TALA.
  TypeScript and production builds passed. The disposable migration suite passed
  upgrade, rollback/reapply, concurrency, dirty-state and legacy-adoption checks.
- Rebuilt/deployed admin-api, admin-web and documentation-worker. Both admin services
  are healthy; the worker is running. Browser verified both scopes, environment
  membership counts, three independent service choices, selected source folders,
  global SSH server choices, active prompt versions, per-run editing/reset and an
  enabled submit button once required inputs are selected. No browser warnings or
  errors. Closed the temporary verification tab. No live SSH inspection or real
  documentation job was started during browser checks.
- Logs: `/tmp/knowledge-agent-v3-doc-workflow-build.log`,
  `/tmp/ka-doc-workflow-migrations.log`, `/tmp/ka-doc-workflow-live-migration.log`,
  `/tmp/ka-doc-workflow-final-tests.log`, `/tmp/ka-doc-workflow-ui-tests.log`.

## 2026-09-13 — Environment overview and server infrastructure

- Split environment generation into independently selectable **Environment overview
  with graph** and **Server infrastructure architecture**. Both use separate active
  documentation prompts with per-run editing. Infrastructure requires exactly one
  SSH-enabled member server with saved global authentication; it receives no source
  tools and only that server's SSH configuration. Overview stays inventory-based.
- Applied migration 48 through golang-migrate; live ledger is clean. The new active
  infrastructure prompt is seeded for existing/new workspaces, supports read-only
  host/runtime inspection and preserves administrator edits. Guarded rollback and
  the complete disposable migration runner passed.
- 63 backend tests and 10 frontend tests passed, plus TypeScript and production
  builds. Coverage includes environment/server boundaries, one-server validation,
  global credential resolution, distinct overview/infrastructure outputs, correct
  resource links, independent document version histories per server, changing the
  selected environment, and infrastructure-only generation.
- Deployed admin-api, admin-web and documentation-worker. Browser verified both
  selections, eligible server dropdown, separate prompt editors and enabled submit
  state after selecting the required inputs. Layout remains visible in WebKit;
  console had no warnings/errors. Temporary tab closed. No real SSH inspection or
  generation job was started during verification.
- Logs: `/tmp/ka-infrastructure-backend-tests.log`,
  `/tmp/ka-infrastructure-ui-tests.log`, `/tmp/ka-infrastructure-migrations.log`,
  `/tmp/ka-infrastructure-live-migration.log`, `/tmp/ka-infrastructure-build.log`.

## 2026-09-13 — Full environment architecture

- Added the **Full environment architecture** workflow label and migrated the
  untouched Environment architecture fixture to version 2 (migration 49, clean).
  The prompt covers purpose, functional relationships, end-to-end behavior,
  implementation, deployment, networking, reliability, investigation traceability
  and evidence gaps. Customized prompt versions remain intact.
- Architecture jobs pin the full current Markdown and version metadata of scoped
  supporting documents. When infrastructure is also selected, it runs first and
  contributes its findings to synthesis. Tests verify that documents from another
  environment are excluded and later edits do not change queued evidence.
- Outputs include three application-built D2/TALA views: functional dependencies,
  deployment/runtime mapping and recorded network endpoints. Dependency arrows
  are explicitly unverified network routes; missing ports are not guessed. All
  three diagrams compiled with TALA, including unusual quoted/interpolated labels.
- 66 documentation/environment backend cases passed. The 27 SDK/model cases passed
  after correcting two new test assertions about omitted HTTP fields; an initial
  isolated SSH fixture connection failure also passed on recheck and the subsequent
  runtime suite. Ten frontend cases, TypeScript, production builds and the complete
  disposable golang-migrate suite passed. Browser verified the new label, prompt v2,
  independent infrastructure option and stable layout without console errors.
- A real Bedrock smoke using fictional inventory reproduced truncated structured
  output during streaming, including with a larger token budget. Native nonstreaming
  Runner.run returned a valid 37,879-character / 5,518-word document with all nine
  sections (13,503 output tokens). Background documentation now uses complete
  structured responses with a 16,384-token output budget; chat keeps streaming.
  Unit tests cover both runtime modes and propagation of the output budget.
- Rebuilt the administration services and documentation worker. No real server was
  inspected and no application document or generation job was created by the smoke.
- Logs: `/tmp/ka-layered-backend-final.log`, `/tmp/ka-layered-runtime-final.log`,
  `/tmp/ka-layered-structured-recheck.log`, `/tmp/ka-layered-model-smoke.log`,
  `/tmp/ka-layered-ui-tests.log`, `/tmp/ka-layered-migrations.log`,
  `/tmp/ka-layered-runtime-build.log`.

## 2026-09-14 — Storage overview and file browser

- Added Administration → Storage (`/storage`) with configured host/container paths,
  mount availability, purpose and management links for source code, RAG inputs,
  document exports and the original bootstrap docs directory. Relative host paths
  remain explicitly relative to the Compose file directory; they are not resolved
  against the container filesystem. Compose now passes the four host path labels
  alongside the existing mounts.
- Added admin-only read endpoints for the allowlisted mounts. Directory browsing
  loads one level with 200-entry pagination; UTF-8 previews are limited to 512 KB.
  Traversal, symlink traversal and non-file previews are rejected. No write route,
  filesystem reorganization or schema migration was introduced.
- The page explains PostgreSQL's canonical document/version and search-index
  storage, versioned Markdown/D2 exports, manual RAG publication, Jira exports and
  the separate login/tracing/activity volumes. RAG Files/Settings link back to the
  overview. Updated the deployment data/lifecycle guide to match current behavior.
- 13 storage API cases and 25 administration API cases passed; 15 frontend cases
  passed across Storage, navigation and RAG. TypeScript and Docker production
  builds passed. Deployed admin-api and admin-web; all four content folders report
  available. Browser verified the overview layout, URL/breadcrumb navigation,
  public Hello-World README preview and empty document-export folder. Console had
  no errors or warnings. Verification did not create or modify user content.
- Logs: `/tmp/ka-storage-backend.log`, `/tmp/ka-storage-auth.log`,
  `/tmp/ka-storage-ui.log`, `/tmp/ka-storage-build.log`, `/tmp/ka-storage-deploy.log`.

## 2026-09-14 — Private Git authentication

- Code projects now exposes a Private repository switch with masked, write-only
  username and token fields. Existing credentials remain saved when fields are
  blank; either value can be replaced independently. Turning private access off
  and saving removes the pair. The project list shows authentication status.
- Username and token share an AES-GCM encrypted `v2` payload in the existing
  credentials table. The worker also reads legacy `v1` token-only records.
  Credentials reach Git through the subprocess environment and GIT_ASKPASS;
  remote URLs, command arguments, API responses and audit details exclude them.
  Prompt matching uses the prompt prefix so usernames/URLs containing the words
  username or password cannot select the wrong credential.
- 50 backend tests passed against disposable PostgreSQL and local Git fixtures;
  nine UI tests passed. Tests cover encryption, partial updates, removal, damaged
  credential replacement, legacy compatibility and a due 60-minute automatic
  pull. The seven worker tests passed again after the prompt-prefix correction.
- Docker production builds, including TypeScript, passed. Updated admin-api,
  admin-web and repository-worker together. Browser verified private form fields,
  the 60-minute default, scheduling explanation and clean public repository URL;
  no console warnings/errors. No user repository or credentials were changed.
  Private-provider authentication was tested with isolated credentials/fixtures,
  not a real private GitHub repository.
- Documented the existing database scheduler: the worker checks due times every
  two seconds while idle, queues jobs and advances next_sync_at. It runs with the
  browser closed; queued work or worker downtime may delay pulls.
- Logs: `/tmp/ka-git-auth-api-tests.log`, `/tmp/ka-git-auth-ui-tests.log`,
  `/tmp/ka-git-auth-worker-final.log`, `/tmp/ka-git-auth-build.log`,
  `/tmp/ka-git-auth-worker-build.log`, `/tmp/ka-git-auth-deploy.log`,
  `/tmp/ka-git-auth-worker-deploy.log`.

## 2026-09-14 — Service environment selection

- Services can select multiple existing environments using a dropdown. Each
  deployment uses a separate environment dropdown. The service list/details show
  memberships, and filtering includes environment names without requiring a
  deployment. Saved deployments add their service/server to the selected
  environment; removing deployments does not remove shared membership implicitly.
- Migration 50 adds a scoped environment foreign key to deployments and the
  environment summaries to service_assets. Exact existing labels are linked;
  unmatched labels and deployment IDs remain intact. Renames appear in service
  context and document resource names; environment deletion retains deployment
  records and the latest name. Legacy label-only API inputs remain supported.
- Service/environment edits share the existing membership table. Reciprocal
  membership changes advance revisions, foreign/unknown selections are rejected,
  and members used by linked deployments cannot be removed from an environment
  until those deployments are updated.
- 88 backend cases passed across services, environments, their new relationship
  cases, documents and generation. The initial run required updating two existing
  tests for reciprocal revisions and the new rollback order; new tests also had
  two fixture/path corrections. The final 21 environment/relationship cases passed.
  All 35 frontend cases and TypeScript passed. Disposable golang-migrate upgrade,
  rollback/reapply, concurrency and failed-migration checks passed.
- Built and deployed admin-api, admin-web and documentation-worker. Live migration
  is version 50, clean. Browser verified selection of the existing dev environment
  and its default selection in a new deployment, with no console warnings/errors.
  The verification form was discarded; no service or environment was created.
- Logs: `/tmp/ka-service-environments-api.log`,
  `/tmp/ka-service-environments-api-final.log`, `/tmp/ka-service-environments-ui-final.log`,
  `/tmp/ka-service-environments-types.log`, `/tmp/ka-service-environments-migrations.log`,
  `/tmp/ka-service-environments-build.log`, `/tmp/ka-service-environments-live-migration.log`,
  `/tmp/ka-service-environments-deploy.log`.
